"""Use the supported Codex CLI as a vision inference backend, with its action tools disabled."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import tempfile
from pathlib import Path

DISABLED = [
    "shell_tool",
    "unified_exec",
    "apps",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "computer_use",
    "plugins",
    "remote_plugin",
    "multi_agent",
    "sleep_tool",
    "goals",
    "image_generation",
    "code_mode_host",
    "view_image",
    "hooks",
]
WIRE_SCHEMA = {
    "type": "object",
    "properties": {
        "tool": {"type": ["string", "null"]},
        "arguments_json": {"type": "string"},
        "done": {"type": "boolean"},
        "blocked": {"type": "boolean"},
        "summary": {"type": "string"},
        "evidence": {"type": "string"},
    },
    "required": ["tool", "arguments_json", "done", "blocked", "summary", "evidence"],
    "additionalProperties": False,
}


async def infer(settings: dict, messages: list, temp_root: Path) -> dict:
    binary = os.environ.get("CONDUIT_CODEX_PATH") or shutil.which("codex")
    if not binary:
        raise ValueError(
            "Codex CLI was not found. Install/sign in to Codex, or configure an HTTP vision model."
        )
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="decision-", dir=temp_root) as scratch:
        directory = Path(scratch).resolve()
        if directory.parent != temp_root.resolve():
            raise ValueError("Invalid model temporary directory")
        screenshot = directory / "screen.png"
        output = directory / "decision.json"
        output_schema = directory / "decision.schema.json"
        output_schema.write_text(json.dumps(WIRE_SCHEMA), encoding="utf-8")
        prompt = []
        for message in messages:
            content = message["content"]
            if isinstance(content, str):
                prompt.append(content)
            else:
                for part in content:
                    if part["type"] == "text":
                        prompt.append(part["text"])
                    elif part["type"] == "image_url":
                        screenshot.write_bytes(base64.b64decode(part["image_url"]["url"].split(",", 1)[1]))
        prompt.append(
            "This is a constrained vision decision, not a coding task. Do not invoke tools or inspect files. "
            "Inspect the attached screenshot and supplied observation only. For the output schema, encode "
            "the arguments object as a JSON string in arguments_json instead of an arguments field. "
            "Return one decision and nothing else."
        )
        command = [
            binary,
            "exec",
            "--ignore-user-config",
            "--skip-git-repo-check",
            "--ephemeral",
            "--sandbox",
            "read-only",
            "-c",
            'approval_policy="never"',
            "-c",
            'web_search="disabled"',
            "-c",
            'model_reasoning_effort="low"',
            "--color",
            "never",
            "--json",
            "--output-schema",
            str(output_schema),
            "--output-last-message",
            str(output),
            "--image",
            str(screenshot),
        ]
        if settings.get("model"):
            command += ["--model", settings["model"]]
        for feature in DISABLED:
            command += ["--disable", feature]
        command.append("-")
        options = {"creationflags": 0x08000000} if os.name == "nt" else {}
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=directory,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **options,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate("\n\n".join(prompt).encode("utf-8")), timeout=180
            )
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        if process.returncode != 0 or not output.exists():
            diagnostic = stderr.decode("utf-8", errors="replace").lower()
            if "auth" in diagnostic or "log in" in diagnostic or "home directory" in diagnostic:
                raise ValueError(
                    "Codex could not use a signed-in account. Run Conduit in your normal Windows session and run codex login if needed."
                )
            if "limit" in diagnostic or "quota" in diagnostic:
                raise ValueError("Codex usage is currently limited. Try later or configure another model.")
            raise ValueError(
                "Codex could not complete the vision decision. Verify the configured model is available and Codex can connect."
            )
        from .telemetry import record_model
        usage = None
        for line in stdout.decode(errors="replace").splitlines():
            try:
                event = json.loads(line)
                if event.get("usage"):
                    usage = event["usage"]
            except ValueError:
                continue
        record_model(usage)
        value = json.loads(output.read_text(encoding="utf-8"))
        value["arguments"] = json.loads(value.pop("arguments_json"))
        return value
