import asyncio
import json
from datetime import datetime

from mcp.shared.memory import create_connected_server_and_client_session
from test_compiler import family_for
from test_gateway import READ
from test_registry import READY
from test_session_handoff import SIGNED_OUT, FakePage

from conduit.compiler import publish
from conduit.keep import seconds_until, should_degrade
from conduit.programs import ProgramError


def failing_with(*errors):
    queue = iter(errors)

    async def call(self, name, arguments, progress=None):
        raise next(queue)
    return call


def test_only_program_breaking_errors_degrade():
    assert should_degrade(ProgramError("locator_missing", "gone"))
    assert not should_degrade(ProgramError("postcondition_failed", "unsure", uncertain=True))
    assert not should_degrade(ProgramError("object_missing", "no such record"))
    assert not should_degrade(ProgramError("authentication_required", "signed out"))


def test_seconds_until_the_next_daily_check():
    assert seconds_until("07:00", datetime(2026, 10, 18, 6, 59)) == 60
    assert seconds_until("07:00", datetime(2026, 10, 18, 7, 0)) == 24 * 3600


async def test_a_broken_program_withdraws_the_tool_and_a_repair_brings_it_back(app, monkeypatch):
    repairs = []

    async def fake_repair(runtime, resource, *, progress=None, brief=None, **kwargs):
        repairs.append(resource)
        await asyncio.sleep(0.05)
        family = family_for(runtime.manifest.target + "/records").model_copy(update={"name": "records", "label": "records"})
        publish(runtime.manifest, family, {**READY, "resource": "records"})
        return {"status": "ready"}

    monkeypatch.setattr("conduit.runtime.Runtime.call", failing_with(ProgramError("locator_missing", "Save is gone")))
    monkeypatch.setattr("conduit.app.repair_connector", fake_repair)
    monkeypatch.setattr("conduit.app.model_settings", lambda: {"available": True})
    async with create_connected_server_and_client_session(app.state.gateway) as session:
        failed = json.loads((await session.call_tool(READ, {"id": "r1"})).content[0].text)
        withdrawn = {t.name for t in (await session.list_tools()).tools}
        for _ in range(40):
            report = (await session.call_tool("seira_status", {"job_id": failed["repair_job_id"]})).structuredContent
            if report["status"] in ("complete", "error"):
                break
            await asyncio.sleep(0.05)
        back = {t.name for t in (await session.list_tools()).tools}
    assert failed["code"] == "locator_missing" and failed["degraded"] and "repair_job_id" in failed
    assert READ not in withdrawn and READ in back
    assert report["status"] == "complete" and report["kind"] == "repair" and repairs == ["records"]
    capability = app.state.store.get("demo-1").capabilities[0]
    assert capability["status"] == "ready" and "degraded" not in capability


async def test_user_errors_keep_the_tool_and_a_sign_out_says_how_to_sign_in(app, monkeypatch):
    monkeypatch.setattr("conduit.runtime.Runtime.call", failing_with(
        ProgramError("object_missing", "No such record"), ProgramError("authentication_required", "Signed out")))
    async with create_connected_server_and_client_session(app.state.gateway) as session:
        missing = json.loads((await session.call_tool(READ, {"id": "zzz"})).content[0].text)
        signed_out = json.loads((await session.call_tool(READ, {"id": "r1"})).content[0].text)
        listed = {t.name for t in (await session.list_tools()).tools}
    assert missing["code"] == "object_missing" and "degraded" not in missing and READ in listed
    assert "conduit login demo-1" in signed_out["next"] and not app.state.jobs


async def test_without_a_model_the_withdrawn_tool_names_the_manual_repair(app, monkeypatch):
    monkeypatch.setattr("conduit.runtime.Runtime.call", failing_with(ProgramError("program_failed", "broke")))
    monkeypatch.setattr("conduit.app.model_settings", lambda: {"available": False})
    async with create_connected_server_and_client_session(app.state.gateway) as session:
        failed = json.loads((await session.call_tool(READ, {"id": "r1"})).content[0].text)
    assert failed["degraded"] and "conduit repair demo-1 records" in failed["next"] and not app.state.jobs


async def test_daily_check_records_whether_each_site_is_still_signed_in(app, monkeypatch):
    class Browser:
        def __init__(self, manifest, home, *, headed=False):
            self.page, self.context, self.manual_login = FakePage(SIGNED_OUT), None, False

        def session_changed_on_disk(self):
            return False

        async def start(self):
            self.context = object()

        async def save(self):
            return True

        async def close(self):
            self.context = None

    monkeypatch.setattr("conduit.runtime.BrowserSession", Browser)
    store = app.state.store
    before = await app.state.run_daily_check()
    (store.home / "auth").mkdir(exist_ok=True)
    (store.home / "auth" / "demo-1.session").write_bytes(b"JSON\n{}")
    after = await app.state.run_daily_check()
    assert before == [{"connector_id": "demo-1", "name": "Demo", "status": "no_session"}]
    assert after == [{"connector_id": "demo-1", "name": "Demo", "status": "signed_out",
                      "reason": "a password field is visible"}]
    finding = store.get("demo-1").findings[-1]
    assert finding["type"] == "daily_check" and finding["status"] == "signed_out"
    assert app.state.audit.tail()[-1]["tool"] == "daily_check"
