import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ladder = load("ladder")
rest = load("verify_rest_export")


def test_usage_normalises_codex_and_anthropic_shapes():
    tokens = ladder.normalise_usage([
        {"usage": {"input_tokens": 1000, "output_tokens": 50, "cached_input_tokens": 400, "cache_write_input_tokens": 100}},
        {"usage": {"input_tokens": 2000, "output_tokens": 70, "cache_read_input_tokens": 1500, "cache_creation_input_tokens": 0}},
        {"usage": None},
    ])
    assert tokens == {"input": 3000, "output": 120, "cache_read": 1900, "cache_write": 100}
    assert ladder.estimated_cost("claude-opus-5", tokens) == round(
        (1000 * 5 + 120 * 25 + 1900 * 0.5 + 100 * 6.25) / 1_000_000, 4)
    assert ladder.estimated_cost("gpt-6-astra", tokens) is None


def test_status_requires_all_four_gates():
    report = {"compilation": {"status": "ready"}, "disabled_model_verification": [{"status": "ready"}],
              "mcp_protocol": {"status": "passed"}}
    assert ladder.status_for(report, {"status": "passed"})[0] == "passed"
    assert ladder.status_for(report, None)[0] == "passed_without_observer"
    assert ladder.status_for(report, {"status": "failed"})[0] == "failed"
    assert ladder.status_for({**report, "mcp_protocol": {"status": "incomplete"}}, {"status": "passed"})[0] == "failed"
    assert ladder.status_for({"compilation": {"status": "incomplete"}}, None)[0] == "failed"


def test_failure_modes_and_markdown():
    assert ladder.classify("Compiler inference failed: You've hit your usage limit") == "inference_unavailable"
    assert ladder.classify("Sign-in NOT verified: no sign-out control") == "login_unverified"
    assert ladder.classify("Locator.click: Timeout 8000ms exceeded") == "browser_step_failed"
    assert ladder.classify("Exported MCP protocol tests failed") == "mcp_gate_failed"
    assert ladder.classify("No complete compiled CRUD family was verified") == "discovery_exhausted"
    assert ladder.classify("") is None
    rung = {"id": "demo", "url": "https://demo.example"}
    report = {"compilation": {"status": "incomplete", "inference": [{"model": "claude-opus-5", "backend": "anthropic",
              "effort": "high", "usage": {"input_tokens": 10, "output_tokens": 2}}], "learning_trace": [{}, {}],
              "model_calls": 2}, "error": "Compilation or repair did not pass the full publication gate", "_path": "r.json"}
    row = ladder.row_for(rung, report, None, 12.34)
    assert row["status"] == "failed" and row["failure_mode"] == "discovery_exhausted"
    assert row["rounds_used"] == 2 and row["model"] == "claude-opus-5" and row["backend"] == "anthropic"
    markdown = ladder.render_markdown([row], "now")
    assert "| demo | https://demo.example | claude-opus-5 | 2 | 2 |" in markdown
    assert "publication gate" in markdown


async def test_rest_observer_checks_records_independently():
    records = {"7": {"id": 7, "title": "Conduit one", "note": "n", "updated": "t1"}}
    calls = []

    async def request(url, headers):
        calls.append((url, headers))
        key = url.rsplit("/", 1)[-1]
        return (200, {"data": records[key]}) if key in records else (404, None)

    config = {"read_url": "https://x/api/things/{id}", "fields": {"title": "title"}, "ignore": ["updated"],
              "record_path": "data", "auth_header": "Authorization"}
    checks = []
    observe = rest.build_observer(config, request, token="Bearer abc", checks=checks)
    await observe("things", "create", {"title": "Conduit one"}, {"id": "7", "data": {"title": "Conduit one"}})
    records["7"] = {**records["7"], "title": "Conduit two", "updated": "t2"}
    await observe("things", "update", {"id": "7", "title": "Conduit two"}, {"id": "7", "data": {"title": "Conduit two"}})
    records["7"]["note"] = "changed"
    import pytest
    with pytest.raises(AssertionError, match=r"nrelated .*\['note'\]"):
        await observe("things", "read", {"id": "7"}, {"id": "7", "data": {"title": "Conduit two"}})
    records["7"]["note"] = "n"
    records["7"]["deleted"] = True
    with pytest.raises(AssertionError, match="still finds"):
        await observe("things", "delete", {"id": "7"}, {"id": "7", "data": None})
    soft = rest.build_observer({**config, "deleted_flag": "deleted"}, request, token="Bearer abc", checks=checks)
    await soft("things", "delete", {"id": "7"}, {"id": "7", "data": None})
    assert checks[-1] == {"verb": "delete", "absent": True, "soft_deleted": True}
    del records["7"]
    await observe("things", "delete", {"id": "7"}, {"id": "7", "data": None})
    assert calls[0][1]["Authorization"] == "Bearer abc" and calls[0][1]["Accept"] == "application/json"
    assert [c["verb"] for c in checks] == ["create", "update", "delete", "delete"]
