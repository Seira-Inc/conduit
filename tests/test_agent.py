from pathlib import Path

import pytest

from conduit.agent import Decision, add_task_tools, run_task
from conduit.models import Manifest


class FakeRuntime:
    def __init__(self):
        from conduit.browser import browser_tools

        self.manifest = Manifest(
            id="fake", name="Fake", kind="website", target="https://example.com", tools=browser_tools()
        )
        self.browser = True
        self.home = Path("unused-test-model-home")
        self.calls = []

    async def _call(self, name, arguments):
        self.calls.append(name)
        if name == "browser_screenshot":
            return {"image_base64": "test-fixture"}
        return {"text": "Current state"}


async def test_named_tasks_require_model_and_are_not_fake_success(monkeypatch):
    monkeypatch.setenv("CONDUIT_MODEL_BACKEND", "http")
    monkeypatch.delenv("CONDUIT_MODEL", raising=False)
    monkeypatch.delenv("CONDUIT_MODEL_BASE_URL", raising=False)
    with pytest.raises(ValueError, match="Named tasks need"):
        await run_task(FakeRuntime(), "Create note", "Test")


async def test_task_reobserves_after_action_and_stops_at_limit(monkeypatch):
    monkeypatch.setenv("CONDUIT_MODEL", "test-model")
    monkeypatch.setenv("CONDUIT_MODEL_BASE_URL", "http://localhost:9999/v1")

    async def decide(*args):
        return Decision(tool="browser_press", arguments={"key": "r"})

    monkeypatch.setattr("conduit.agent.request_decision", decide)
    runtime = FakeRuntime()
    result = await run_task(runtime, "Draw rectangle", "Test", max_steps=2)
    assert result["status"] == "step_limit"
    assert runtime.calls == [
        "browser_snapshot",
        "browser_screenshot",
        "browser_press",
        "browser_snapshot",
        "browser_screenshot",
        "browser_press",
        "browser_snapshot",
        "browser_screenshot",
    ]


async def test_model_cannot_run_arbitrary_tools(monkeypatch):
    monkeypatch.setenv("CONDUIT_MODEL", "test")
    monkeypatch.setenv("CONDUIT_MODEL_BASE_URL", "http://localhost:9999/v1")

    async def decide(*args):
        return Decision(tool="execute_shell", arguments={"command": "untrusted content"})

    monkeypatch.setattr("conduit.agent.request_decision", decide)
    with pytest.raises(ValueError, match="outside this connector"):
        await run_task(FakeRuntime(), "Test", "Test")


def test_specific_requested_task_generation():
    runtime = FakeRuntime()
    add_task_tools(runtime.manifest, ["Create a Trello card", "Draw a rectangle"])
    named = [t for t in runtime.manifest.tools if t.adapter == "agent"]
    assert [t.name for t in named] == ["task_do", "task_create_a_trello_card", "task_draw_a_rectangle"]
    assert all(t.input_schema["required"] == ["details"] for t in named)
