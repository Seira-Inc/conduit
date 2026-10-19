"""Serve one experimentally verified mutation using Conduit's existing bounded runtime.

This does not certify a new CRUD family. A supplied family provides the existing
read/persistence protocol, with one generated replacement update program.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .mcp_check import failure_detail
from .models import load_manifest
from .programs import fingerprint
from .runtime import Runtime
from .verifier import FamilyExecutor


async def serve(directory: Path, home: Path, *, testing=False):
    definition = json.loads((directory / "operation.json").read_text(encoding="utf-8"))
    if not testing and definition.get("verification", {}).get("status") != "passed":
        raise ValueError("This single operation has not passed its actual MCP acceptance")
    if fingerprint(definition["family"]) != definition["fingerprint"]:
        raise ValueError("The operation changed after its fingerprint was recorded")
    runtime = Runtime(load_manifest(directory / "connector.json"), home)
    server = Server(definition["name"])

    @server.list_tools()
    async def list_tools():
        return [types.Tool(name=definition["name"], description=definition["description"],
            inputSchema=definition["input_schema"],
            annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=True))]

    @server.call_tool()
    async def call_tool(name, arguments):
        try:
            if name != definition["name"]:
                raise ValueError("Unknown operation")
            Draft202012Validator(definition["input_schema"]).validate(arguments)
            async with runtime.lock:
                await runtime.acquire_browser()
                result = await FamilyExecutor(runtime.browser, runtime.manifest, definition["family"]).call(
                    "update", arguments)
            return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result))])
        except Exception as exc:
            return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(failure_detail(exc)))],
                                        isError=True)

    try:
        async with stdio_server() as (reader, writer):
            await server.run(reader, writer, server.create_initialization_options())
    finally:
        await runtime.close()


def run(directory, home, *, testing=False):
    asyncio.run(serve(Path(directory), Path(home), testing=testing))
