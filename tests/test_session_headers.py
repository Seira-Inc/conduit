import asyncio

import pytest
from compiler_site import website
from test_compiler import login

from conduit.mcp_check import failure_detail, remember_failure
from conduit.models import Manifest
from conduit.programs import Program, ProgramError, ProgramRunner
from conduit.recorder import Recorder
from conduit.runtime import Runtime


@pytest.mark.integration
async def test_dynamic_header_waits_for_cold_browser_bootstrap(tmp_path):
    with website(client_boot_delay_ms=500) as (url, _):
        manifest = Manifest(id="cold-headers", name="Cold headers", kind="website", target=url,
                            allowed_origins=[url], allow_private=True)
        runtime = Runtime(manifest, tmp_path)
        try:
            await login(runtime)
            await runtime.browser.close()
            runtime.browser.recorder.headers[url] = {"x-csrf-token": "stale-previous-browser"}
            value, metrics = await ProgramRunner(runtime.browser, manifest).run(Program(
                result={"$session": {"kind": "header", "key": "x-csrf-token", "origin": url}}), {})
            cookies = await runtime.browser.context.cookies(url)
            expected = next(c["value"] for c in cookies if c["name"] == "csrf")
            assert value == expected
            assert metrics["requests"] == 0
            assert metrics["model_calls"] == 0
        finally:
            await runtime.close()


async def test_missing_header_wait_is_bounded_and_origin_specific():
    recorder = Recorder()
    recorder.headers["https://unrelated.example"] = {"authorization": "unrelated"}
    assert await recorder.session_header("https://requested.example", "Authorization", timeout=0.02) is None
    task = asyncio.create_task(recorder.session_header("https://requested.example", "Authorization", timeout=1))
    await asyncio.sleep(0)
    recorder.headers["https://requested.example"] = {"authorization": "current"}
    recorder.header_changed.set()
    assert await task == "current"


def test_mcp_task_group_preserves_authentication_error():
    error = ExceptionGroup("transport", [ExceptionGroup("session", [
        ProgramError("authentication_required", "A fresh login is needed")])])
    assert failure_detail(error) == {"error": "A fresh login is needed",
                                     "error_code": "authentication_required", "outcome_unknown": False}


def test_mcp_failure_reaches_only_its_resource_and_preserves_cleanup_block():
    manifest = Manifest(id="repair-feedback", name="Repair feedback", kind="website", target="https://example.com",
                        capabilities=[{"resource": "records", "status": "ready"}, {"resource": "other", "status": "ready"}])
    error = ProgramError("postcondition_failed", "Readback differs", uncertain=True)
    error.postcondition = {"mismatched_fields": ["title"]}
    error.observations = [{"operation": "storage_read", "key": "drawing", "result_type": "null"}]
    report = {"status": "incomplete", **failure_detail(ExceptionGroup("MCP", [error])),
              "failed_resource": "records", "failed_operation": "create", "failed_phase": "write",
              "cleanup": [{"resource": "records", "status": "needs_attention", "id": "known-test-object"}]}
    remember_failure(manifest, report)
    remember_failure(manifest, report)
    records, other = manifest.capabilities
    assert records["last_error"]["postcondition"] == {"mismatched_fields": ["title"]}
    assert records["last_error"]["operation"] == "create"
    assert records["last_error"]["execution_observations"] == error.observations
    assert records["cleanup"] == report["cleanup"]
    assert "last_error" not in other and "cleanup" not in other
