import asyncio
import json
import os
import time

import pytest
from mcp import types
from mcp.shared.memory import create_connected_server_and_client_session
from test_compiler import family_for
from test_registry import READY, verified_connector

from conduit.app import create_app
from conduit.browser import browser_tools
from conduit.compiler import publish
from conduit.models import Manifest
from conduit.programs import ProgramError
from conduit.registry import public_name
from conduit.tool_results import error_payload

READ = public_name("demo-1", "records_read")
DELETE = public_name("demo-1", "records_delete")


def receipt(name, arguments):
    verb = name.rsplit("_", 1)[-1]
    return {"status": "verified", "operation": verb, "id": arguments.get("id", "r1"),
            "data": None if verb == "delete" else {"title": "Kept"}, "deleted": verb == "delete",
            "reconciled": True, "adapter_version": "1",
            "state_token": None,
            "metrics": {"requests": 1, "steps": 1, "model_calls": 0, "screenshots": 0, "duration_ms": 1.5}}


@pytest.fixture
def app(tmp_path):
    app = create_app(tmp_path, mcp_json_response=True)
    manifest = verified_connector(app.state.store, "demo-1", "Demo", "https://example.com", "records")
    manifest.notes = "SECRET-NOTE: create the contact John Doe"
    app.state.store.write(manifest)
    return app


async def test_lists_only_verified_tools_with_honest_hints(app):
    async with create_connected_server_and_client_session(app.state.gateway) as session:
        listed = {t.name: t for t in (await session.list_tools()).tools}
    expected = {public_name("demo-1", f"records_{v}") for v in ("create", "read", "update", "delete")}
    assert set(listed) == expected | {"seira_find", "seira_build", "seira_status"}
    read, delete = listed[READ], listed[public_name("demo-1", "records_delete")]
    assert read.annotations.readOnlyHint and not read.annotations.destructiveHint
    assert delete.annotations.destructiveHint and not delete.annotations.readOnlyHint
    assert read.description.startswith("Demo (example.com):")


async def test_instructions_and_resources_never_contain_notes(app):
    assert "SECRET-NOTE" not in app.state.gateway.instructions
    async with create_connected_server_and_client_session(app.state.gateway) as session:
        text = (await session.read_resource("conduit://capabilities")).contents[0].text
    assert "SECRET-NOTE" not in text and json.loads(text)[0]["resource"] == "records"


async def test_call_returns_the_runtime_receipt(app, monkeypatch):
    seen = {}

    async def fake_call(self, name, arguments, progress=None):
        seen.update(connector=self.manifest.id, name=name)
        return receipt(name, arguments)

    monkeypatch.setattr("conduit.runtime.Runtime.call", fake_call)
    async with create_connected_server_and_client_session(app.state.gateway) as session:
        result = await session.call_tool(READ, {"id": "r1"})
    assert not result.isError and seen == {"connector": "demo-1", "name": "records_read"}
    assert result.structuredContent["id"] == "r1" and result.structuredContent["status"] == "verified"


async def test_program_error_matches_the_shared_payload(app, monkeypatch):
    exc = ProgramError("postcondition_failed", "The record did not settle", uncertain=True)
    exc.object_id = "r1"
    exc.postcondition = {"field": "title"}

    async def fail(self, name, arguments, progress=None):
        raise exc

    monkeypatch.setattr("conduit.runtime.Runtime.call", fail)
    async with create_connected_server_and_client_session(app.state.gateway) as session:
        result = await session.call_tool(public_name("demo-1", "records_create"), {"title": "x"})
    assert result.isError and json.loads(result.content[0].text) == error_payload(exc)


async def test_unknown_and_busy_tools_are_refused(app, monkeypatch):
    async def never(self, name, arguments, progress=None):
        raise AssertionError("a refused call must not reach the runtime")

    monkeypatch.setattr("conduit.runtime.Runtime.call", never)
    async with create_connected_server_and_client_session(app.state.gateway) as session:
        unknown = await session.call_tool("deadbeef__records_read", {})
        assert unknown.isError and "Unknown or unverified" in unknown.content[0].text
        app.state.compile_jobs["demo-1"] = "job-1"
        busy = await session.call_tool(READ, {"id": "r1"})
    assert busy.isError and json.loads(busy.content[0].text)["code"] == "connector_busy"


async def test_meta_tools_find_and_status(app):
    async with create_connected_server_and_client_session(app.state.gateway) as session:
        found = (await session.call_tool("seira_find", {"request": "get a record on example.com"})).structuredContent
        none = (await session.call_tool("seira_find", {"request": "book a flight"})).structuredContent
        status = (await session.call_tool("seira_status", {"job_id": "nope"})).structuredContent
    assert found["status"] == "found" and found["tool"] == READ
    assert none["status"] == "none" and none["known_sites"] == ["https://example.com"]
    assert status["status"] == "unknown"


async def test_seira_build_returns_at_once_and_the_job_completes(app, monkeypatch):
    calls = {}

    async def fake_discover(request, home, progress):
        await progress("Discovering")
        return Manifest(id="newsite-1", name="New Site", kind="website", target=request.target,
                        allowed_origins=[request.target], tools=browser_tools())

    async def fake_compile(runtime, *, progress=None, brief=None, **kwargs):
        calls["brief"] = brief
        await asyncio.sleep(0.05)
        family = family_for(runtime.manifest.target + "/tasks").model_copy(update={"name": "tasks", "label": "tasks"})
        publish(runtime.manifest, family, {**READY, "resource": "tasks"})
        return {"status": "ready"}

    monkeypatch.setattr("conduit.app.discover", fake_discover)
    monkeypatch.setattr("conduit.app.compile_connector", fake_compile)
    monkeypatch.setattr("conduit.app.model_settings", lambda: {"available": True})
    async with create_connected_server_and_client_session(app.state.gateway) as session:
        started = time.perf_counter()
        queued = (await session.call_tool("seira_build", {"site": "https://newsite.example",
                                                           "request": "add a task on newsite"})).structuredContent
        assert time.perf_counter() - started < 1.0
        assert queued["status"] == "queued" and queued["login_required"] and queued["eta_seconds"] == 600
        for _ in range(40):
            report = (await session.call_tool("seira_status", {"job_id": queued["job_id"]})).structuredContent
            if report["status"] in ("complete", "error"):
                break
            await asyncio.sleep(0.05)
        listed = {t.name for t in (await session.list_tools()).tools}
    assert report["status"] == "complete", report
    assert report["connector_id"] == "newsite-1" and public_name("newsite-1", "tasks_create") in report["tools"]
    assert public_name("newsite-1", "tasks_delete") in listed
    assert calls["brief"] == "add a task on newsite"
    assert app.state.store.get("newsite-1").notes == ""


async def test_seira_build_refuses_duplicates_and_asks_for_login(app, monkeypatch):
    monkeypatch.setattr("conduit.app.model_settings", lambda: {"available": True})
    store = app.state.store
    store.write(Manifest(id="nosess-1", name="No Session", kind="website", target="https://nosession.example",
                         allowed_origins=["https://nosession.example"], tools=browser_tools()))
    async with create_connected_server_and_client_session(app.state.gateway) as session:
        exists = (await session.call_tool("seira_build", {"site": "https://example.com",
                                                           "request": "get a record"})).structuredContent
        login = (await session.call_tool("seira_build", {"site": "https://nosession.example",
                                                          "request": "add a widget"})).structuredContent
    assert exists["status"] == "exists" and exists["tool"] == READ
    assert login["status"] == "login_required" and "conduit login nosess-1" in login["next"]
    assert not app.state.jobs


async def test_runtime_reloads_when_connector_json_changes(app, monkeypatch):
    async def fake_call(self, name, arguments, progress=None):
        return receipt(name, arguments) | {"data": {"tools": sorted(t.name for t in self.manifest.tools)}}

    monkeypatch.setattr("conduit.runtime.Runtime.call", fake_call)
    store = app.state.store
    async with create_connected_server_and_client_session(app.state.gateway) as session:
        first = (await session.call_tool(READ, {"id": "r1"})).structuredContent["data"]["tools"]
        before = app.state.runtimes["demo-1"]
        manifest = store.get("demo-1")
        manifest.tools = [t for t in manifest.tools if t.name != "records_delete"]
        path = store.write(manifest) / "connector.json"
        os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000_000))
        second = (await session.call_tool(READ, {"id": "r1"})).structuredContent["data"]["tools"]
    assert "records_delete" in first and "records_delete" not in second
    assert app.state.runtimes["demo-1"] is not before


def answering(confirm):
    async def callback(context, params):
        assert params.mode == "form" and "cannot be undone" in params.message
        return types.ElicitResult(action="accept" if confirm is not None else "decline",
                                  content={"confirm": confirm} if confirm is not None else None)
    return callback


async def test_delete_asks_the_user_through_elicitation_and_logs_the_answer(app, monkeypatch):
    calls = []

    async def fake_call(self, name, arguments, progress=None):
        calls.append((name, arguments))
        return receipt(name, arguments)

    monkeypatch.setattr("conduit.runtime.Runtime.call", fake_call)
    async with create_connected_server_and_client_session(app.state.gateway,
                                                           elicitation_callback=answering(True)) as session:
        done = await session.call_tool(DELETE, {"id": "r1"})
    async with create_connected_server_and_client_session(app.state.gateway,
                                                           elicitation_callback=answering(None)) as session:
        declined = await session.call_tool(DELETE, {"id": "r2"})
    assert not done.isError and calls == [("records_delete", {"id": "r1"})]
    assert declined.isError and json.loads(declined.content[0].text)["code"] == "not_confirmed"
    rows = app.state.audit.tail()
    assert [(r["verb"], r["approval"], r["outcome"]) for r in rows] == [
        ("delete", "elicitation", "ok"), ("delete", "elicitation", "not_confirmed")]
    assert "r1" not in json.dumps(rows) and rows[0]["user"] == "local"


async def test_delete_without_elicitation_needs_a_one_use_confirm_token(app, monkeypatch):
    calls = []

    async def fake_call(self, name, arguments, progress=None):
        calls.append(arguments)
        return receipt(name, arguments)

    monkeypatch.setattr("conduit.runtime.Runtime.call", fake_call)
    async with create_connected_server_and_client_session(app.state.gateway) as session:
        listed = {t.name: t for t in (await session.list_tools()).tools}
        first = json.loads((await session.call_tool(DELETE, {"id": "r1"})).content[0].text)
        assert first["code"] == "confirmation_required" and first["preview"]["arguments"] == {"id": "r1"}
        wrong = json.loads((await session.call_tool(DELETE, {"id": "r9", "confirm_token": first["confirm_token"]}))
                           .content[0].text)
        done = await session.call_tool(DELETE, {"id": "r1", "confirm_token": first["confirm_token"]})
        reused = json.loads((await session.call_tool(DELETE, {"id": "r1", "confirm_token": first["confirm_token"]}))
                            .content[0].text)
    assert "confirm_token" in listed[DELETE].inputSchema["properties"]
    assert "confirm_token" not in listed[READ].inputSchema["properties"]
    assert wrong["code"] == "confirmation_expired" and reused["code"] == "confirmation_expired"
    assert not done.isError and calls == [{"id": "r1"}]
    assert app.state.audit.tail()[-1]["approval"] == "confirm_token"


async def test_seira_build_needs_a_model(app, monkeypatch):
    monkeypatch.setattr("conduit.app.model_settings", lambda: {"available": False})
    async with create_connected_server_and_client_session(app.state.gateway) as session:
        result = await session.call_tool("seira_build", {"site": "https://other.example", "request": "add a thing"})
    assert result.isError and "No model is configured" in result.content[0].text
