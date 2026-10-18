"""A held-out server-rendered app: HTML forms, no JSON API or client-side source."""
import html
import threading
from contextlib import contextmanager
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs
from uuid import uuid4


@contextmanager
def website(variant=False, *, port=0, restored_route=None):
    base = restored_route or "/desk/" + uuid4().hex[:9]
    field, label = ("caption", "Caption") if variant else ("title", "Title")
    state = {"items": {}, "sessions": {}, "writes": 0, "base": base, "field": field,
             "variant": variant, "content_types": set()}
    def esc(value):
        return html.escape(str(value), quote=True)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def session(self):
            cookie = SimpleCookie(self.headers.get("Cookie", "")).get("login")
            return state["sessions"].get(cookie.value) if cookie else None

        def send(self, content, status=200, *, location=None, cookie=None):
            session = self.session()
            meta = '<meta name="csrf-token" content="' + esc(session["csrf"]) + '">' if session else ""
            raw = ('<!doctype html><html><head><title>Record desk</title>' + meta
                   + '</head><body>' + content + '</body></html>').encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            state["content_types"].add("text/html")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            if location:
                self.send_header("Location", location)
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(raw)

        def csrf(self, session):
            return '<input type="hidden" name="csrf" value="' + esc(session["csrf"]) + '">'

        def edit_form(self, session, item=None):
            path = base + ("/" + item["id"] + "/edit" if item else "/new")
            revision = ('<input type="hidden" name="version" value="' + str(item["version"]) + '">') if item else ""
            return ('<h1>' + ("Edit record" if item else "New record") + '</h1><form method="post" action="'
                    + path + '">' + self.csrf(session) + revision + '<label>' + label
                    + '<input name="' + field + '" required value="' + esc(item[field] if item else "")
                    + '"></label><label>Note<textarea name="note">' + esc(item["note"] if item else "")
                    + '</textarea></label><button type="submit">'
                    + ("Save changes" if item else "Create record") + '</button></form>')

        def do_GET(self):
            session = self.session()
            if not session:
                return self.send('<h1>Sign in</h1><form method="post" action="/login"><label>Account'
                                 '<input name="account" value="tester"></label><button>Sign in</button></form>')
            items = state["items"].setdefault(session["account"], {})
            if self.path in ("/", base):
                links = ''.join('<li><a href="' + base + '/' + key + '">' + esc(item[field]) + '</a></li>'
                                for key, item in items.items())
                return self.send('<h1>My records</h1><a href="' + base + '/new">New record</a><ul>' + links + '</ul>')
            if self.path == base + "/new":
                return self.send(self.edit_form(session))
            parts = self.path.removeprefix(base + "/").split("/")
            key = parts[0]
            item = items.get(key) if self.path.startswith(base + "/") else None
            if item is None:
                return self.send('<h1>Record not found</h1><a href="' + base + '">Back to records</a>', 404)
            if parts[1:] == ["edit"]:
                return self.send(self.edit_form(session, item))
            if len(parts) != 1:
                return self.send('<h1>Not found</h1>', 404)
            properties = [("Record ID", "id", item["id"]), (label, field, item[field]),
                          ("Note", "note", item["note"]), ("Version", "version", item["version"])]
            if variant:
                fields = ''.join('<section><h2>' + name + '</h2><p data-field="' + key + '">'
                                 + esc(value) + '</p></section>' for name, key, value in properties)
            else:
                fields = '<dl>' + ''.join('<dt>' + name + '</dt><dd id="record-' + key + '">' + esc(value)
                                        + '</dd>' for name, key, value in properties) + '</dl>'
            self.send('<h1>Record</h1>' + fields + '<a href="' + base + '/' + key + '/edit">Edit record</a>'
                      '<form method="post" action="' + base + '/' + key + '/remove">'
                      + self.csrf(session) + '<button>Delete record</button></form><a href="'
                      + base + '">Back to records</a>')

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
            data = {key: values[0] for key, values in parse_qs(raw, keep_blank_values=True).items()}
            if self.path == "/login":
                token = uuid4().hex
                state["sessions"][token] = {"account": data.get("account", "tester"), "csrf": uuid4().hex}
                return self.send("Signed in", 303, location=base,
                                 cookie=f"login={token}; Path=/; HttpOnly; SameSite=Strict")
            session = self.session()
            if not session:
                return self.send('<h1>Sign in required</h1>', 401)
            if data.get("csrf") != session["csrf"]:
                return self.send('<h1>Refresh this form</h1>', 403)
            items = state["items"].setdefault(session["account"], {})
            if self.path == base + "/new":
                if not data.get(field):
                    return self.send('<h1>Title required</h1>', 422)
                key = uuid4().hex
                items[key] = {"id": key, field: data[field], "note": data.get("note", ""), "version": 1}
            else:
                parts = self.path.removeprefix(base + "/").split("/")
                if not self.path.startswith(base + "/") or len(parts) != 2 or parts[0] not in items:
                    return self.send('<h1>Record not found</h1>', 404)
                key, action = parts
                if action == "remove":
                    del items[key]
                elif action == "edit":
                    if str(items[key]["version"]) != data.get("version"):
                        return self.send('<h1>Record changed; reload before editing</h1>', 409)
                    items[key].update({field: data[field], "note": data.get("note", ""), "version": items[key]["version"] + 1})
                else:
                    return self.send('<h1>Not found</h1>', 404)
            state["writes"] += 1
            session["csrf"] = uuid4().hex
            self.send("Saved", 303, location=base + ("/" + key if key in items else ""))

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
