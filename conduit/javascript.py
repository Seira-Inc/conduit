"""Generated code uses a separate V8 process and a bounded session-only RPC surface."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from .programs import Program, ProgramError, Step, read_request, validate_program
from .recorder import SECRET

LIMIT = 4 * 1024 * 1024
METHODS = {"request", "navigate", "click", "fill", "press", "select", "read", "download", "upload", "command", "draw"}


def scrub(runner, value):
    def visit(item):
        if isinstance(item, str):
            for secret in runner.secret_values:
                if len(secret) >= 4:
                    item = item.replace(secret, "[session secret]")
            return item
        if isinstance(item, dict):
            return {k: "[session secret]" if SECRET.search(k) else visit(v) for k, v in item.items()}
        if isinstance(item, list):
            return [visit(v) for v in item]
        return item
    return visit(value)


class SessionBridge:
    def __init__(self, runner, program, read_only):
        self.runner, self.program, self.read_only = runner, program, read_only
        self.calls, self.observations = 0, 0
        self.failure = None

    async def call(self, data):
        if self.failure:
            raise self.failure  # Catching a JS error must never permit a mutation retry.
        self.calls += 1
        if self.calls > 80:
            raise ProgramError("step_limit", "Adapter exceeded 80 session calls", uncertain=self.runner.mutated)
        method, params = data.get("method"), data.get("params", {})
        if not isinstance(params, dict):
            raise ValueError("Session call parameters must be an object")
        if method == "uuid":
            return str(uuid4())
        if method == "sleep":
            ms = params.get("ms")
            if not isinstance(ms, (float, int)) or not 0 <= ms <= 2000:
                raise ValueError("Sleep must be between 0 and 2000 milliseconds")
            await asyncio.sleep(ms / 1000)
            return None
        if method == "storage":
            key = params.get("key")
            if not isinstance(key, str) or SECRET.search(key):
                raise ValueError("Authentication storage must use an opaque api.session binding")
            result = await self.runner.resolve({"$storage": {"key": key, "default": None}})
            kind = "null" if result is None else "array" if isinstance(result, list) else type(result).__name__
            self.runner.observations.append({"operation": "storage_read", "key": key, "result_type": kind})
            self.runner.observations = self.runner.observations[-8:]
            self.observations += 1
            return result
        if method == "global":
            key = params.get("key")
            if not isinstance(key, str):
                raise ValueError("Page global lookups need a dotted key")
            return await self.runner.resolve({"$global": {"key": key, "default": None}})
        if method not in METHODS:
            raise ValueError("Unknown session operation")
        step = Step.model_validate({**params, "kind": method, "name": "_javascript_result"})
        validate_program(Program(steps=[step]), read_only=self.read_only)
        if step.read_evidence and not set(step.read_evidence) <= set(self.program.read_evidence):
            raise ValueError("Read-only RPC evidence must be declared by the adapter")
        if method == "upload":
            if step.expected_document is None:
                raise ValueError("Document upload needs expected_document and a fresh export guard")
            self.runner.vars["document_before"] = step.expected_document
        if method in ("read", "download") or (method == "request" and read_request(step)):
            self.observations += 1
        await self.runner.execute_steps([step])
        return self.runner.vars.pop("_javascript_result", None)


async def execute_javascript(runner, program, *, read_only=False):
    bridge = SessionBridge(runner, program, read_only)
    environment = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}}
    process = await asyncio.create_subprocess_exec(sys.executable, "-I", str(Path(__file__).with_name("js_worker.py")),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env=environment, limit=LIMIT + 1, **({"creationflags": 0x08000000} if os.name == "nt" else {}))

    async def send(value):
        raw = json.dumps(value, ensure_ascii=True).encode() + b"\n"
        if len(raw) > LIMIT:
            raise ProgramError("message_limit", "Adapter message exceeds the limit", uncertain=runner.mutated)
        process.stdin.write(raw)
        await process.stdin.drain()

    try:
        await send({"javascript": program.javascript, "input": scrub(runner, runner.input),
                    "context": scrub(runner, runner.context)})
        while True:
            line = await process.stdout.readline()
            if not line:
                raise ProgramError("javascript_failed", "The adapter worker stopped", uncertain=runner.mutated)
            message = json.loads(line)
            if message.get("type") == "call":
                try:
                    value = await bridge.call(message["call"])
                    reply = {"value": scrub(runner, value)}
                except Exception as exc:
                    error = exc if isinstance(exc, ProgramError) else ProgramError("adapter_call_failed",
                        str(scrub(runner, str(exc)))[:500], uncertain=runner.mutated)
                    bridge.failure = error
                    reply = {"error": {"code": error.code, "message": str(error)}}
                await send({"id": message["id"], **reply})
            elif message.get("type") in ("result", "error"):
                if bridge.failure:
                    raise bridge.failure
                if message["type"] == "error":
                    raise ProgramError("javascript_failed", str(scrub(runner, message.get("message", "")))[:700],
                                       uncertain=runner.mutated)
                if read_only and not bridge.observations:
                    raise ProgramError("readback_missing", "A read adapter must observe actual website state")
                return scrub(runner, message.get("value"))
            else:
                raise ValueError("Unknown adapter worker message")
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.wait()
