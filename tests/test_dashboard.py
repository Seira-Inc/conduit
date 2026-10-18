import asyncio
import io
import time
import tomllib
import zipfile

from fastapi.testclient import TestClient

from conduit.agent import add_task_tools
from conduit.app import create_app
from conduit.browser import browser_tools
from conduit.generator import Store
from conduit.models import Manifest


def test_dashboard_origin_protection_and_export(tmp_path):
    app = create_app(tmp_path)
    manifest = Manifest(
        id="demo",
        name="Demo",
        kind="website",
        target="https://example.com",
        allowed_origins=["https://example.com"],
        tools=browser_tools(),
        notes="SECRET-NOTE: create the contact John Doe",
    )
    directory = app.state.store.write(manifest)
    (directory / ".env").write_text("SECRET=not-for-export")
    (directory / ".state").mkdir()
    (directory / ".state" / "private.json").write_text('{"secret":"not-for-export"}')
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 400
        assert client.post("/api/build", json={"target": "https://example.com"}).status_code == 403
        assert (
            client.post(
                "/api/build",
                headers={"origin": "https://evil.example", "x-conduit-local": "1"},
                json={"target": "https://example.com"},
            ).status_code
            == 403
        )
        response = client.get("/api/connectors/demo/export")
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert "server.py" in archive.namelist()
            assert "_runtime/conduit/runtime.py" in archive.namelist()
            codex = tomllib.loads(archive.read("codex-config.toml").decode("utf-8"))["mcp_servers"]["demo"]
            assert codex["env"]["CONDUIT_HOME"] == str(tmp_path.resolve())
            assert codex["tool_timeout_sec"] >= 300
            assert codex["command"] == app.state.store.config("demo")["mcpServers"]["demo"]["command"]
            assert not any(".env" in name or ".state" in name for name in archive.namelist())
            assert all(b"not-for-export" not in archive.read(name) for name in archive.namelist())
        assert client.get("/api/connectors/does-not-exist").status_code == 404
        detail = client.get("/api/connectors/demo")
        assert detail.status_code == 200 and "notes" not in detail.json()
        assert "SECRET-NOTE" not in detail.text and "SECRET-NOTE" not in client.get("/api/connectors").text


def test_mcp_route_is_reachable_without_the_dashboard_header(tmp_path):
    app = create_app(tmp_path, mcp_json_response=True)
    initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}}
    headers = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
    with TestClient(app) as client:
        assert client.post("/api/build", json={"target": "https://example.com"}).status_code == 403
        ok = client.post("/mcp", json=initialize, headers=headers)
        assert ok.status_code == 200 and ok.headers.get("mcp-session-id")
        assert ok.json()["result"]["capabilities"]["tools"]["listChanged"] is True
        foreign = client.post("/mcp", json=initialize, headers={**headers, "origin": "https://evil.example"})
        assert foreign.status_code == 403
        assert client.post("/mcp", json=initialize, headers={**headers, "host": "evil.example"}).status_code == 400


def test_task_can_be_stopped_without_claiming_success(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    manifest = Manifest(
        id="cancellable",
        name="Cancelable",
        kind="website",
        target="https://example.com",
        tools=browser_tools(),
    )
    add_task_tools(manifest, [])
    app.state.store.write(manifest)

    async def long_task(self, name, arguments, progress=None):
        if progress:
            await progress("Waiting in the test task")
        await asyncio.sleep(30)
        return {"status": "model_reported_complete", "summary": "Should not be reached"}

    monkeypatch.setattr("conduit.runtime.Runtime.call", long_task)
    with TestClient(app) as client:
        headers = {"x-conduit-local": "1"}
        started = client.post(
            "/api/connectors/cancellable/run",
            headers=headers,
            json={"tool": "task_do", "arguments": {"details": "test"}},
        )
        job_id = started.json()["job_id"]
        client.post(f"/api/jobs/{job_id}/cancel", headers=headers, json={})
        for _ in range(20):
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] == "cancelled":
                break
            time.sleep(0.01)
        assert job["status"] == "cancelled"
        assert "result" not in job


def test_upgrade_preserves_requested_tasks_and_login_state(tmp_path):
    store = Store(tmp_path)
    manifest = Manifest(
        id="existing",
        name="Existing connection",
        kind="website",
        target="https://example.com",
        allowed_origins=["https://example.com"],
        notes="Use the workspace named Demo.",
        tools=[t for t in browser_tools() if t.name != "browser_close"],
    )
    add_task_tools(manifest, ["Create my document"])
    manifest.tools = [t for t in manifest.tools if t.name != "task_do"]
    store.write(manifest)
    auth = tmp_path / "auth" / "existing.session"
    auth.parent.mkdir()
    auth.write_bytes(b"opaque user session")
    assert store.upgrade() == [manifest.id]
    updated = store.get(manifest.id)
    assert updated.notes == manifest.notes
    assert updated.target == manifest.target
    assert updated.created_at == manifest.created_at
    assert {"task_do", "task_create_my_document", "browser_close"} <= {t.name for t in updated.tools}
    assert auth.read_bytes() == b"opaque user session"
