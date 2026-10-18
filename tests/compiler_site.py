"""A spec-free application with randomized routes, real auth, CSRF, and persistent server state.

Only its rendered UI/JavaScript are supplied to the compiler in live acceptance runs.
"""
import json
import socket
import threading
from contextlib import contextmanager
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4


@contextmanager
def website(variant=False, *, port=0, restored_route=None, canvas_delay_ms=0, client_boot_delay_ms=0):
    base = restored_route or "/" + ("workbench" if variant else "gateway") + "/" + uuid4().hex[:9]
    field = "caption" if variant else "title"
    state = {"items": {}, "sessions": {}, "writes": 0, "false_update": False,
             "drop_update": False, "drop_create": False, "variant": variant, "base": base, "field": field,
             "extra_fields": {}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, body, status=200, content_type="application/json", cookies=None):
            raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            for cookie in cookies or []:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(raw)

        def session(self):
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            token = cookies.get("login")
            return state["sessions"].get(token.value) if token else None

        def body(self):
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")

        def drop(self):
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()

        def do_GET(self):
            if self.path == "/client.js":
                code = r"""
const endpoint=BASE, field=FIELD;
const csrf=()=>document.cookie.split('; ').find(x=>x.startsWith('csrf='))?.split('=')[1];
async function request(method, path='', body){
 const response=await fetch(endpoint+path,{method,credentials:'include',headers:{'content-type':'application/json','x-csrf-token':csrf()},body:body?JSON.stringify(body):undefined});
 return await response.json();
}
async function load(){const data=await request('GET');document.querySelector('#items').replaceChildren();
 for(const item of data.items){const row=document.createElement('article');row.dataset.id=item.id;
 row.textContent=item[field]+' — '+item.note;
 const edit=document.createElement('button');edit.textContent='Edit '+item[field];edit.onclick=async()=>{
 document.querySelector('#entry').value=item[field];document.querySelector('#form').dataset.id=item.id;};
 const remove=document.createElement('button');remove.textContent='Delete '+item[field];remove.onclick=async()=>{await request('DELETE','/'+item.id);await load();};
 row.append(edit,remove);document.querySelector('#items').append(row);}}
document.querySelector('#form').onsubmit=async e=>{e.preventDefault();const id=e.target.dataset.id;
 const body={[field]:document.querySelector('#entry').value,note:document.querySelector('#note').value};
 await request(id?'PATCH':'POST',id?'/'+id:'',body);e.target.reset();delete e.target.dataset.id;await load();};
setTimeout(load,BOOT_DELAY);
""".replace("BASE", json.dumps(state["base"])).replace("FIELD", json.dumps(field)).replace(
    "BOOT_DELAY", str(client_boot_delay_ms))
                return self.send(code, content_type="application/javascript")
            if self.path == "/canvas":
                return self.send("""<!doctype html><title>Document canvas</title>
<h1>Document canvas</h1><button id="export">Export scene</button>
<label>Import scene<input id="import" type="file" accept=".json,.excalidraw"></label>
<button id="open-scene">Open scene</button><button id="confirm-import" hidden>Replace scene</button>
<svg id="canvas" width="800" height="500"></svg><script>
let scene=JSON.parse(localStorage.getItem('drawing')||'{"elements":[]}');
let saveTimer;function persist(){clearTimeout(saveTimer);if(SAVE_DELAY){
saveTimer=setTimeout(()=>localStorage.setItem('drawing',JSON.stringify(scene)),SAVE_DELAY);}
else localStorage.setItem('drawing',JSON.stringify(scene));}
function render(){document.querySelector('#canvas').innerHTML='';for(const e of scene.elements){
const rect=document.createElementNS('http://www.w3.org/2000/svg','rect');
for(const [k,v] of Object.entries({x:e.x,y:e.y,width:e.width,height:e.height,fill:e.fill}))rect.setAttribute(k,v);
rect.dataset.id=e.id;document.querySelector('#canvas').append(rect);}}
document.querySelector('#export').onclick=()=>{const a=document.createElement('a');a.download='scene.json';
a.href=URL.createObjectURL(new Blob([JSON.stringify(scene)],{type:'application/json'}));a.click();};
document.querySelector('#import').onchange=async e=>{scene=JSON.parse(await e.target.files[0].text());
persist();render();e.target.value='';};render();
window.importClicks=[];
document.querySelector('#open-scene').onclick=()=>{window.importClicks.push('open');
if(scene.elements.length)document.querySelector('#confirm-import').hidden=false;
else document.querySelector('#import').click();};
document.querySelector('#confirm-import').onclick=()=>{window.importClicks.push('confirm');
document.querySelector('#confirm-import').hidden=true;document.querySelector('#import').click();};
const canvas=document.querySelector('#canvas');let start;
canvas.onpointerdown=e=>{const box=canvas.getBoundingClientRect();start={x:e.clientX-box.x,y:e.clientY-box.y};};
canvas.onpointerup=e=>{if(!start)return;const box=canvas.getBoundingClientRect();
scene.elements.push({id:crypto.randomUUID(),x:start.x,y:start.y,width:Math.abs(e.clientX-box.x-start.x),
height:Math.abs(e.clientY-box.y-start.y),fill:'blue',label:'Conduit probe'});
start=null;persist();render();};
</script>""".replace("SAVE_DELAY", str(canvas_delay_ms)), content_type="text/html")
            session = self.session()
            if self.path == "/":
                if not session:
                    return self.send("""<!doctype html><title>Sign in to test workspace</title>
<label>Account<input id="account" value="tester"></label><button id="login">Sign in</button>
<script>document.querySelector('#login').onclick=async()=>{await fetch('/login',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({account:document.querySelector('#account').value})});location.reload();};</script>""", content_type="text/html")
                label = "Caption" if variant else "Title"
                return self.send(f"""<!doctype html><title>{'Card desk' if variant else 'Records'}</title>
<h1>My records</h1><form id="form"><label>{label}<input id="entry" required></label>
<label>Note<input id="note"></label><button type="submit">Save record</button></form><section id="items"></section>
<script src="/client.js"></script>""", content_type="text/html")
            if self.path.startswith(state["base"]):
                if not session:
                    return self.send({"error": "login required"}, 401)
                if self.path == state["base"]:
                    return self.send({"items": list(state["items"].get(session["account"], {}).values())})
                key = self.path.rsplit("/", 1)[-1]
                item = state["items"].get(session["account"], {}).get(key)
                return self.send({"item": item}, 200 if item else 404)
            self.send({"error": "not found"}, 404)

        def mutate(self, method):
            session = self.session()
            if not session:
                return self.send({"error": "login required"}, 401)
            if self.headers.get("X-CSRF-Token") != session["csrf"]:
                return self.send({"error": "stale csrf"}, 419)
            if not self.path.startswith(state["base"]):
                return self.send({"error": "wrong route"}, 404)
            state["writes"] += 1
            items = state["items"].setdefault(session["account"], {})
            data = self.body() if method != "DELETE" else {}
            key = self.path.rsplit("/", 1)[-1]
            if method == "POST":
                key = uuid4().hex
                items[key] = {**state["extra_fields"], "id": key, field: data[field], "note": data.get("note", ""), "version": 1}
            elif method == "PATCH":
                if key not in items:
                    return self.send({"error": "not found"}, 404)
                expected = self.headers.get("If-Match")
                if expected and int(expected) != items[key]["version"]:
                    return self.send({"error": "conflict"}, 412)
                if not state["false_update"]:
                    items[key].update({k: v for k, v in data.items() if k in (field, "note")})
                    items[key]["version"] += 1
            else:
                items.pop(key, None)
            if (method == "PATCH" and state["drop_update"]) or (method == "POST" and state["drop_create"]):
                return self.drop()
            # A new CSRF cookie for every successful mutation; captured-token replay must fail.
            session["csrf"] = uuid4().hex
            return self.send({"item": items.get(key)}, 201 if method == "POST" else 200,
                             cookies=[f"csrf={session['csrf']}; Path=/; SameSite=Strict"])

        def do_POST(self):
            if self.path == "/login":
                account = self.body().get("account", "tester")
                token, csrf = uuid4().hex, uuid4().hex
                state["sessions"][token] = {"account": account, "csrf": csrf}
                return self.send({"ok": True}, cookies=[f"login={token}; Path=/; HttpOnly; SameSite=Strict",
                                                       f"csrf={csrf}; Path=/; SameSite=Strict"])
            self.mutate("POST")

        def do_PATCH(self):
            self.mutate("PATCH")

        def do_DELETE(self):
            self.mutate("DELETE")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
