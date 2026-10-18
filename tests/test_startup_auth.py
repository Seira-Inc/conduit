import asyncio
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from conduit.models import Manifest
from conduit.programs import Program, ProgramError, ProgramRunner, Step
from conduit.runtime import Runtime


@contextmanager
def restoring_website():
    state = {"restore_requested": False, "restored": False, "reads": [], "writes": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, body, status=200, *, cookie=None):
            raw = (body if isinstance(body, str) else json.dumps(body)).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html" if isinstance(body, str) else "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(raw)

        def ready(self):
            return ("access=current" in self.headers.get("Cookie", "")
                    and self.headers.get("X-CSRF-Token") == "current-binding")

        def do_GET(self):
            if self.path == "/":
                return self.send("""<!doctype html><title>Restoring session</title><script>
                window.SessionConfig={csrfToken:'stale-binding'};
                async function restore(){
                  const r=await fetch('/restore');const v=await r.json();
                  if(v.ready){window.SessionConfig.csrfToken=v.token;return;}
                  setTimeout(restore,30);
                } restore();</script>""")
            if self.path == "/restore":
                if state["restore_requested"] and "refresh=saved" in self.headers.get("Cookie", ""):
                    state["restored"] = True
                    return self.send({"ready": True, "token": "current-binding"},
                                     cookie="access=current; Path=/; HttpOnly; SameSite=Strict")
                return self.send({"ready": False})
            if self.path == "/items":
                state["reads"].append({"ready": self.ready(), "token": self.headers.get("X-CSRF-Token")})
                if not self.ready():
                    state["restore_requested"] = True
                    return self.send({"error": "session restoring"}, 401)
                return self.send({"items": []})
            self.send({}, 404)

        def do_POST(self):
            if self.path == "/login":
                return self.send({"ok": True}, cookie="refresh=saved; Path=/; HttpOnly; SameSite=Strict")
            if self.path == "/items":
                data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                state["writes"].append({"ready": self.ready(), "data": data})
                if not self.ready():
                    return self.send({"error": "login required"}, 401)
                return self.send({"id": "created-once", **data}, 201)
            self.send({}, 404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.integration
@pytest.mark.parametrize("javascript,transport", [(False, "http"), (False, "page"), (True, "http"), (True, "page")])
async def test_cold_saved_session_recovers_read_with_fresh_cookie_and_token_before_one_write(
        tmp_path, javascript, transport):
    with restoring_website() as (url, state):
        manifest = Manifest(id="startup-auth", name="Startup auth", target=url, kind="website",
                            allowed_origins=[url], allow_private=True)
        runtime = Runtime(manifest, tmp_path)
        try:
            await runtime.browser.start()
            await runtime.browser.context.request.post(url + "/login")
            await runtime.browser.save()
            await runtime.browser.close()
            assert runtime.browser.auth_restore_deadline == 0
            binding = {"kind": "global", "key": "SessionConfig.csrfToken"}
            headers = {"x-csrf-token": {"$session": binding}}
            if javascript:
                program = Program(javascript="""
                  const headers={'x-csrf-token':api.session({kind:'global',key:'SessionConfig.csrfToken'})};
                  await api.request({url:'/items',headers,transport:input.transport});
                  return (await api.request({url:'/items',method:'POST',headers,body:{title:input.title},
                                             transport:input.transport})).body;
                """)
            else:
                program = Program(steps=[
                    Step(kind="request", url="/items", headers=headers, transport=transport),
                    Step(kind="request", name="created", url="/items", method="POST", headers=headers,
                         body={"title": {"$ref": "input.title"}}, transport=transport),
                ], result={"$ref": "vars.created.body"})
            runner = ProgramRunner(runtime.browser, manifest)
            result, metrics = await runner.run(program, {"title": "Conduit saved-session test", "transport": transport})
            assert result == {"id": "created-once", "title": "Conduit saved-session test"}
            assert state["restored"] and state["reads"][0] == {"ready": False, "token": "stale-binding"}
            assert state["reads"][-1] == {"ready": True, "token": "current-binding"}
            assert state["writes"] == [{"ready": True, "data": {"title": "Conduit saved-session test"}}]
            assert 2 <= len(state["reads"]) <= 5
            assert metrics["requests"] == len(state["reads"]) + 1
            assert metrics["model_calls"] == metrics["screenshots"] == 0
            assert any(o["operation"] == "startup_auth_read" for o in runner.observations)
        finally:
            await runtime.close()


@pytest.mark.parametrize("method,code,warm,mutated,uncertain", [
    ("POST", "authentication_required", False, False, False),
    ("GET", "authentication_required", True, False, False),
    ("GET", "authentication_required", False, True, False),
    ("GET", "authentication_required", False, False, True),
    ("GET", "unexpected_response", False, False, False),
    ("GET", "transport_failed", False, False, False),
    ("GET", "conflict", False, False, False),
])
async def test_startup_recovery_does_not_repeat_writes_or_unrelated_failures(method, code, warm, mutated, uncertain):
    session = SimpleNamespace(auth_restore_deadline=0 if warm else time.monotonic() + 5)
    runner = ProgramRunner(session, None)
    runner.mutated = mutated
    runner._request_once = AsyncMock(side_effect=ProgramError(code, "Failure", uncertain=uncertain))
    with pytest.raises(ProgramError) as error:
        await runner.request(Step(kind="request", method=method))
    assert error.value.code == code
    runner._request_once.assert_awaited_once()


async def test_persistent_auth_failure_is_bounded_and_cancellation_propagates(monkeypatch):
    session = SimpleNamespace(auth_restore_deadline=time.monotonic() + 5)
    runner = ProgramRunner(session, None)
    runner._request_once = AsyncMock(side_effect=ProgramError("authentication_required", "Sign in"))
    monkeypatch.setattr("conduit.programs.asyncio.sleep", AsyncMock())
    with pytest.raises(ProgramError, match="Sign in"):
        await runner.request(Step(kind="request"))
    assert runner._request_once.await_count == 5
    runner._request_once.reset_mock()
    monkeypatch.setattr("conduit.programs.asyncio.sleep", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await runner.request(Step(kind="request"))
    runner._request_once.assert_awaited_once()


async def test_declared_get_mutation_is_not_retried_during_startup():
    runner = ProgramRunner(SimpleNamespace(auth_restore_deadline=time.monotonic() + 5), None)
    runner._request_once = AsyncMock(side_effect=ProgramError("authentication_required", "Sign in"))
    with pytest.raises(ProgramError):
        await runner.request(Step(kind="request", method="GET", effect="write"))
    runner._request_once.assert_awaited_once()


async def test_retry_is_not_scheduled_past_the_startup_deadline(monkeypatch):
    runner = ProgramRunner(SimpleNamespace(auth_restore_deadline=100.1), None)
    runner._request_once = AsyncMock(side_effect=ProgramError("authentication_required", "Sign in"))
    sleep = AsyncMock()
    monkeypatch.setattr("conduit.programs.time.monotonic", lambda: 100)
    monkeypatch.setattr("conduit.programs.asyncio.sleep", sleep)
    with pytest.raises(ProgramError):
        await runner.request(Step(kind="request"))
    runner._request_once.assert_awaited_once()
    sleep.assert_not_awaited()
