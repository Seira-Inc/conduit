import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from conduit import compiler_model
from conduit.agent import Decision, model_settings, request_decision
from conduit.compiler_model import infer
from conduit.telemetry import measure_models


def envelope(payload):
    return json.dumps({"payload_json": json.dumps(payload)})


def fake_message(text, *, stop_reason="end_turn", cache_read=0, model="claude-opus-5"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category="cyber") if stop_reason == "refusal" else None,
        usage=SimpleNamespace(input_tokens=1200, output_tokens=80, cache_read_input_tokens=cache_read,
                              cache_creation_input_tokens=0, model_dump=lambda exclude_none=True: {"input_tokens": 1200}),
        model=model,
        _request_id="req_test",
    )


class FakeClient:
    def __init__(self, message=None, error=None):
        self.calls = []
        self.message = message
        self.error = error
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self.stream, create=self.create))

    @asynccontextmanager
    async def stream(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        calls = self.calls

        class Stream:
            async def get_final_message(self_inner):
                return self.message if not callable(self.message) else self.message(len(calls))

        yield Stream()

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.message


@pytest.fixture
def anthropic_env(monkeypatch):
    monkeypatch.setenv("CONDUIT_MODEL_BACKEND", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    for name in ("CONDUIT_MODEL", "CONDUIT_MODEL_API_KEY", "CONDUIT_MODEL_EFFORT", "CONDUIT_MODEL_STRUCTURED",
                 "CONDUIT_MODEL_MAX_TOKENS", "CONDUIT_MODEL_BASE_URL", "CONDUIT_LEARNING_DISABLED",
                 "ANTHROPIC_WORKSPACE_ID", "CONDUIT_MODEL_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)


def install(monkeypatch, client):
    monkeypatch.setattr(compiler_model, "anthropic_client", lambda settings: client)
    return client


def test_model_settings_prefers_anthropic_key_over_codex_and_http(monkeypatch):
    for name in ("CONDUIT_MODEL_BACKEND", "CONDUIT_MODEL", "CONDUIT_MODEL_BASE_URL", "CONDUIT_MODEL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "abc")
    settings = model_settings()
    assert settings["backend"] == "anthropic"
    assert settings["model"] == "claude-opus-5"
    assert settings["key"] == "abc"
    assert settings["available"]
    monkeypatch.setenv("CONDUIT_MODEL_BACKEND", "http")
    assert not model_settings()["available"]
    monkeypatch.setenv("CONDUIT_MODEL_BASE_URL", "http://localhost:1/v1")
    monkeypatch.setenv("CONDUIT_MODEL", "x")
    assert model_settings()["key"] == ""
    monkeypatch.setenv("CONDUIT_LEARNING_DISABLED", "1")
    assert not model_settings()["available"]


def test_anthropic_client_sends_workspace_header_when_configured(anthropic_env, monkeypatch):
    captured = {}

    class FakeSDKClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeSDKClient)
    compiler_model.anthropic_client(model_settings())
    assert captured["default_headers"] == {} and captured["api_key"] == "test-key"
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_123")
    compiler_model.anthropic_client(model_settings())
    assert captured["default_headers"] == {"anthropic-workspace-id": "wrkspc_123"}


async def test_anthropic_request_splits_contract_into_cached_system(anthropic_env, monkeypatch):
    client = install(monkeypatch, FakeClient(fake_message(envelope({"action": "done"}), cache_read=900)))
    answer, stats = await infer("EVIDENCE: page", Path("."), system="CONTRACT TEXT")
    assert answer == {"action": "done"}
    request = client.calls[0]
    assert request["system"] == [{"type": "text", "text": "CONTRACT TEXT\n" + compiler_model.ENVELOPE,
                                  "cache_control": {"type": "ephemeral"}}]
    assert request["messages"] == [{"role": "user", "content": "EVIDENCE: page"}]
    assert request["output_config"] == {"effort": "high", "format": {"type": "json_schema", "schema": compiler_model.OUTPUT}}
    assert request["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert request["thinking"] == {"type": "adaptive"}
    assert request["fallbacks"] == "default" and request["betas"] == [compiler_model.FALLBACK_BETA]
    assert request["max_tokens"] == 16000 and "tools" not in request
    assert stats["backend"] == "anthropic" and stats["model_calls"] == 1
    assert stats["usage"] == {"input_tokens": 1200, "output_tokens": 80, "cache_read_input_tokens": 900,
                              "cache_creation_input_tokens": 0}
    assert stats["request_id"] == "req_test"


async def test_unsupported_model_omits_fallback_params(anthropic_env, monkeypatch):
    monkeypatch.setenv("CONDUIT_MODEL", "claude-opus-4-8")
    client = install(monkeypatch, FakeClient(fake_message(envelope({"action": "done"}))))
    assert model_settings()["fallbacks"] is False
    answer, stats = await infer("EVIDENCE: page", Path("."), system="CONTRACT")
    assert answer == {"action": "done"} and stats["model_calls"] == 1
    assert "fallbacks" not in client.calls[0] and "betas" not in client.calls[0]


async def test_research_call_adds_web_search_and_effort_env(anthropic_env, monkeypatch):
    monkeypatch.setenv("CONDUIT_MODEL_EFFORT", "xhigh")
    client = install(monkeypatch, FakeClient(fake_message(envelope({"urls": ["https://example.com/docs"]}))))
    answer, _ = await infer("Find docs", Path("."), research=True)
    assert answer["urls"] == ["https://example.com/docs"]
    request = client.calls[0]
    assert request["tools"] == [compiler_model.WEB_SEARCH]
    assert request["output_config"]["effort"] == "xhigh"
    assert request["system"][0]["text"] == compiler_model.ENVELOPE


async def test_structured_mode_tolerates_a_bare_object(anthropic_env, monkeypatch):
    install(monkeypatch, FakeClient(fake_message(json.dumps({"action": "explore"}))))
    answer, _ = await infer("evidence", Path("."), system="CONTRACT")
    assert answer == {"action": "explore"}


async def test_unstructured_mode_strips_fences_and_asks_for_json(anthropic_env, monkeypatch):
    monkeypatch.setenv("CONDUIT_MODEL_STRUCTURED", "0")
    client = install(monkeypatch, FakeClient(fake_message("```json\n{\"action\": \"explore\"}\n```")))
    answer, _ = await infer("evidence", Path("."), system="CONTRACT")
    assert answer == {"action": "explore"}
    assert "format" not in client.calls[0]["output_config"]
    assert client.calls[0]["system"][0]["text"] == "CONTRACT\n" + compiler_model.JSON_ONLY


@pytest.mark.parametrize("stop_reason,phrase", [("max_tokens", "truncated"), ("refusal", "declined")])
async def test_truncation_and_refusal_are_errors(anthropic_env, monkeypatch, stop_reason, phrase):
    install(monkeypatch, FakeClient(fake_message(envelope({}), stop_reason=stop_reason)))
    with pytest.raises(compiler_model.InferenceFailure, match=phrase) as error:
        await infer("evidence", Path("."), system="CONTRACT")
    assert error.value.stats["usage"]["output_tokens"] == 80
    assert error.value.stats["model_calls"] == 1
    assert error.value.stats["stop_reason"] == stop_reason


async def test_no_text_response_still_retains_provider_usage(anthropic_env, monkeypatch):
    install(monkeypatch, FakeClient(fake_message("", cache_read=700)))
    with pytest.raises(compiler_model.InferenceFailure, match="no text") as error:
        await infer("evidence", Path("."))
    assert error.value.stats["usage"]["cache_read_input_tokens"] == 700
    assert error.value.stats["request_id"] == "req_test"


def test_compilation_counts_failed_response_usage_but_keeps_unknown_usage_unknown():
    from conduit.compiler import record_inference_error

    report = {"model_calls": 0, "inference": []}
    stats = {"model_calls": 1, "usage": {"input_tokens": 90, "output_tokens": 40}}
    record_inference_error(report, compiler_model.InferenceFailure("truncated", stats))
    record_inference_error(report, TimeoutError("no provider response"))
    assert report["model_calls"] == 1
    assert report["inference"] == [{**stats, "error": "truncated"}]
    assert report["inference_errors"] == [
        {"message": "truncated", "usage": stats["usage"]},
        {"message": "no provider response", "usage": None},
    ]
    assert "error" not in stats


def status_error(cls, status, message="boom", body=None):
    response = httpx2.Response(status, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    return cls(message, response=response, body=body)


@pytest.mark.parametrize("error,phrase,aborts", [
    (status_error(anthropic.AuthenticationError, 401), "sign-in unavailable", False),
    (status_error(anthropic.RateLimitError, 429), "rate limited", False),
    (status_error(anthropic.APIStatusError, 402, "Your credit balance is too low"), "Usage allowance reached", True),
    (status_error(anthropic.BadRequestError, 400, "billing_error: add credits"), "Usage allowance reached", True),
    (status_error(anthropic.InternalServerError, 500), "overloaded", False),
    (status_error(anthropic.BadRequestError, 400, "schema invalid"), "rejected", False),
    (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")),
     "connection failed", False),
])
async def test_error_mapping_only_billing_uses_abort_phrase(anthropic_env, monkeypatch, error, phrase, aborts):
    install(monkeypatch, FakeClient(error=error))
    with pytest.raises(ValueError, match=phrase) as info:
        await infer("evidence", Path("."), system="CONTRACT")
    text = str(info.value).lower()
    assert ("usage limit" in text or "usage allowance" in text) == aborts
    assert "test-key" not in text


async def test_http_backend_sends_system_max_tokens_and_error_body(monkeypatch):
    monkeypatch.setenv("CONDUIT_MODEL_BACKEND", "http")
    monkeypatch.setenv("CONDUIT_MODEL_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("CONDUIT_MODEL", "m")
    monkeypatch.setenv("CONDUIT_MODEL_MAX_TOKENS", "512")
    monkeypatch.delenv("CONDUIT_LEARNING_DISABLED", raising=False)
    posted = []

    class Response:
        def __init__(self, status, payload):
            self.status_code, self._payload = status, payload
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, json=None):
            posted.append(json)
            if len(posted) == 1:
                return Response(200, {"choices": [{"message": {"content": "```json\n{\"action\": \"done\"}\n```"}}]})
            return Response(400, {"error": "bad schema"})

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    answer, _ = await infer("evidence", Path("."), system="CONTRACT")
    assert answer == {"action": "done"}
    assert posted[0]["messages"][0] == {"role": "system", "content": "CONTRACT"}
    assert posted[0]["max_tokens"] == 512
    with pytest.raises(ValueError, match="bad schema"):
        await infer("evidence", Path("."))


async def test_request_decision_maps_screenshots_and_records_usage(anthropic_env, monkeypatch):
    client = install(monkeypatch, FakeClient(fake_message(json.dumps({"tool": "browser_press", "arguments": {"key": "r"},
                                                                     "done": False, "blocked": False, "summary": "",
                                                                     "evidence": ""}))))
    messages = [{"role": "system", "content": "Operate the app"},
                {"role": "user", "content": [{"type": "text", "text": "observe"},
                                             {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}]
    with measure_models() as meter:
        decision = await request_decision(model_settings(), messages)
    assert decision == Decision(tool="browser_press", arguments={"key": "r"})
    request = client.calls[0]
    assert request["system"] == "Operate the app"
    assert request["messages"][0]["content"][1] == {"type": "image",
                                                    "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}
    assert request["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert meter["model_calls"] == 1


@pytest.mark.live_model
@pytest.mark.skipif(os.environ.get("CONDUIT_TEST_MODEL") != "1", reason="Set CONDUIT_TEST_MODEL=1 to run a real model")
async def test_live_anthropic_round_trip_caches_the_contract(monkeypatch):
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    monkeypatch.setenv("CONDUIT_MODEL_BACKEND", "anthropic")
    monkeypatch.delenv("CONDUIT_LEARNING_DISABLED", raising=False)
    contract = "You are a compiler test. " * 400 + "Reply with a JSON object whose only key is action, value done."
    first, stats1 = await infer("Proceed.", Path("."), system=contract)
    second, stats2 = await infer("Proceed again.", Path("."), system=contract)
    assert first.get("action") == "done" and second.get("action") == "done"
    assert stats1["backend"] == "anthropic"
    assert stats2["usage"]["cache_read_input_tokens"] > 0, stats2
