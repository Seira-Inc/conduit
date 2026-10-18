"""Structured compiler inference, separate from the per-action vision runner."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path

from .agent import model_settings
from .codex_model import DISABLED
from .recorder import redact

OUTPUT = {"type": "object", "properties": {"payload_json": {"type": "string"}},
          "required": ["payload_json"], "additionalProperties": False}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
WEB_SEARCH = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}
JSON_ONLY = "Return exactly one JSON object and nothing else."
ENVELOPE = "Return the requested JSON object serialized as a string in the payload_json field."


class InferenceFailure(ValueError):
    """A completed provider response can consume tokens without producing usable output."""
    def __init__(self, message, stats):
        super().__init__(message)
        self.stats = stats


def strip_fences(content):
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text


def parse_payload(content):
    try:
        value = json.loads(content)
        if not isinstance(value, dict):
            raise ValueError("The compiler needs a JSON object")
        return value
    except ValueError as exc:
        # The outer structured-output envelope is valid JSON, but a model can omit a
        # closing object brace inside its JSON string. Complete only mismatched closing
        # delimiters; never invent keys, values, quotes, commas or truncated tail content.
        repaired, count = complete_closers(content)
        if count:
            try:
                value = json.loads(repaired)
                if isinstance(value, dict):
                    value["syntax_recovery"] = {"inserted_closers": count}
                    return value
            except ValueError:
                pass
        return {"action": "invalid", "error": str(exc), "invalid_payload": content[:40000]}


def complete_closers(content):
    pairs = {"{": "}", "[": "]"}
    stack, output = [], []
    quoted = escaped = False
    count = 0
    for char in content:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in pairs:
            stack.append(pairs[char])
        elif char in ("}", "]"):
            if char not in stack:
                return content, 0
            while stack[-1] != char:
                output.append(stack.pop())
                count += 1
                if count > 8:
                    return content, 0
            stack.pop()
        output.append(char)
    if stack or quoted:
        return content, 0
    return "".join(output), count


async def infer(prompt: str, home: Path, *, research=False, system=""):
    settings = model_settings()
    if not settings["available"]:
        raise ValueError("Learning new operations needs the configured model. Existing compiled tools work without it.")
    if settings["backend"] == "anthropic":
        return await infer_anthropic(settings, system, prompt, research)
    if settings["backend"] != "codex":
        return await infer_http(settings, system, prompt, research)
    return await infer_codex(settings, (system + "\n" + prompt) if system else prompt, home, research)


def anthropic_client(settings):
    import anthropic

    # Organization-level keys carry no workspace; Anthropic then requires the workspace id on every request.
    headers = {"anthropic-workspace-id": settings["workspace"]} if settings.get("workspace") else {}
    return anthropic.AsyncAnthropic(api_key=settings["key"], timeout=600, max_retries=3, default_headers=headers)


def _short(exc):
    return redact(str(exc))[:300]


def _billing(exc):
    text = str(exc).lower()
    return getattr(exc, "status_code", None) == 402 or "billing" in text or "credit balance" in text


async def infer_anthropic(settings, system, prompt, research):
    import anthropic

    # Anthropic structured output rejects open schemas, so the open compiler payload travels as a
    # string inside a fixed envelope, exactly as the codex path does.
    instructions = (system + "\n" if system else "") + (ENVELOPE if settings["structured"] else JSON_ONLY)
    request = {
        "model": settings["model"],
        "max_tokens": settings["max_tokens"],
        "system": [{"type": "text", "text": instructions, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": prompt}],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": settings["effort"]},
    }
    if settings["fallbacks"]:
        request["betas"] = [FALLBACK_BETA]
        request["fallbacks"] = "default"
    if settings["structured"]:
        request["output_config"]["format"] = {"type": "json_schema", "schema": OUTPUT}
    if research:
        request["tools"] = [dict(WEB_SEARCH)]
    try:
        async with anthropic_client(settings).beta.messages.stream(**request) as stream:
            message = await stream.get_final_message()
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        raise ValueError("Compiler model sign-in unavailable: " + _short(exc)) from exc
    except anthropic.RateLimitError as exc:
        raise ValueError("Compiler model rate limited (HTTP 429); retry shortly") from exc
    except anthropic.APIStatusError as exc:
        if _billing(exc):
            raise ValueError("Usage allowance reached: " + _short(exc)) from exc
        if exc.status_code >= 500:
            raise ValueError(f"Compiler model overloaded (HTTP {exc.status_code})") from exc
        raise ValueError("Compiler request rejected: " + _short(exc)) from exc
    except anthropic.APIConnectionError as exc:
        raise ValueError("Compiler model connection failed: " + _short(exc)) from exc
    usage = message.usage
    stats = {
        "model_calls": 1,
        "usage": {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_input_tokens": usage.cache_read_input_tokens or 0,
            "cache_creation_input_tokens": usage.cache_creation_input_tokens or 0,
        },
        "research": research,
        "model": message.model,
        "backend": "anthropic",
        "effort": settings["effort"],
        "stop_reason": message.stop_reason,
        "request_id": getattr(message, "_request_id", None),
    }
    if message.stop_reason == "refusal":
        category = getattr(message.stop_details, "category", None)
        raise InferenceFailure(f"Compiler model declined the request ({category or 'unspecified'})", stats)
    if message.stop_reason == "max_tokens":
        raise InferenceFailure(f"Compiler output truncated at {settings['max_tokens']} tokens", stats)
    text = "".join(block.text for block in message.content if getattr(block, "type", "") == "text")
    if not text.strip():
        raise InferenceFailure("Compiler model returned no text", stats)
    if settings["structured"]:
        try:
            text = json.loads(text)["payload_json"]
        except (ValueError, KeyError, TypeError):
            pass
    return parse_payload(strip_fences(text)), stats


async def infer_http(settings, system, prompt, research):
    import httpx

    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(settings["base_url"].rstrip("/") + "/chat/completions",
            headers={"Authorization": "Bearer " + settings.get("key", "")},
            json={"model": settings["model"], "messages": messages, "max_tokens": settings["max_tokens"],
                  "response_format": {"type": "json_object"}})
        if response.status_code >= 400:
            raise ValueError(f"Compiler model returned HTTP {response.status_code}: {redact(response.text[:300])}")
        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("Compiler model returned no message content") from exc
        return parse_payload(strip_fences(content)), {"model_calls": 1, "usage": data.get("usage"),
                                                       "research": research, "model": settings["model"],
                                                       "backend": settings["backend"]}


async def infer_codex(settings, prompt, home, research):
    binary = os.environ.get("CONDUIT_CODEX_PATH") or shutil.which("codex")
    root = home / "model-temp"
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="compile-", dir=root) as scratch:
        directory = Path(scratch)
        schema_path, output_path = directory / "schema.json", directory / "answer.json"
        schema_path.write_text(json.dumps(OUTPUT), encoding="utf-8")
        cmd = [binary, "exec", "--ignore-user-config", "--skip-git-repo-check", "--ephemeral",
               "--sandbox", "read-only", "-c", 'approval_policy="never"',
               "-c", 'web_search="live"' if research else 'web_search="disabled"',
               "-c", 'model_reasoning_effort="high"', "--color", "never", "--json",
               "--output-schema", str(schema_path.resolve()), "--output-last-message", str(output_path.resolve()),
               "--model", settings["model"]]
        for feature in DISABLED:
            cmd += ["--disable", feature]
        cmd.append("-")
        process = await asyncio.create_subprocess_exec(*cmd, cwd=directory, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            **({"creationflags": 0x08000000} if os.name == "nt" else {}))
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(
                (prompt + "\nReturn the requested object serialized in payload_json. Do not use shell or inspect local files.").encode()), 240)
        except BaseException:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        if process.returncode or not output_path.exists():
            diagnostic = stderr.decode(errors="replace")
            errors = []
            for line in stdout.decode(errors="replace").splitlines():
                try:
                    event = json.loads(line)
                    if event.get("type") in ("error", "turn.failed"):
                        errors.append(str(event.get("message", event.get("error", ""))))
                except ValueError:
                    continue
            summary = redact("; ".join(errors))[:500]
            if not summary:
                lower = diagnostic.lower()
                summary = ("Usage allowance reached" if "quota" in lower or "usage limit" in lower else
                           "Sign-in unavailable" if "auth" in lower or "log in" in lower else
                           f"Inference process exited with code {process.returncode}")
            raise ValueError("Compiler inference failed: " + summary)
        usage = None
        for line in stdout.decode(errors="replace").splitlines():
            try:
                event = json.loads(line)
                if event.get("usage"):
                    usage = event["usage"]
            except ValueError:
                pass
        return parse_payload(json.loads(output_path.read_text(encoding="utf-8"))["payload_json"]), {
            "model_calls": 1, "usage": usage, "research": research,
            "model": settings["model"], "backend": settings["backend"]}
