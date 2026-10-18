import asyncio
import json
import os
import shutil
import socket
import sys
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
import uvicorn
from compiler_site import website
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_compiler import family_for, login

from conduit.app import create_app
from conduit.compiler import publish
from conduit.generator import Store
from conduit.models import Manifest
from conduit.runtime import Runtime
from conduit.verifier import verify_family


@asynccontextmanager
async def dashboard(home):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(create_app(home), log_level="error"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.02)
        yield "http://127.0.0.1:" + str(listener.getsockname()[1])
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 15)
        listener.close()


@pytest.mark.integration
@pytest.mark.parametrize("javascript", [False, True])
async def test_compiled_tools_over_portable_and_universal_mcp(tmp_path, javascript):
    with website() as (url, state):
        home = tmp_path / "state"
        manifest = Manifest(id="compiled", name="Compiled", target=url, kind="website",
                            allowed_origins=[url], allow_private=True)
        runtime = Runtime(manifest, home)
        try:
            await login(runtime)
            family = family_for(state["base"])
            if javascript:
                from javascript_fixture import javascript_family
                family = javascript_family(state["base"])
            report = await verify_family(runtime.browser, manifest, family)
            assert report["status"] == "ready", report
            publish(manifest, family, report)
        finally:
            await runtime.close()
        portable = tmp_path / "export"
        shutil.copytree(Store(home).write(manifest), portable)
        env = {**os.environ, "CONDUIT_HOME": str(home), "CONDUIT_LEARNING_DISABLED": "1"}

        async def exercise(params, prefix=""):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=45)) as client:
                    await client.initialize()
                    listing = await client.list_tools()
                    tool = next(t for t in listing.tools if t.name.endswith("records_create"))
                    assert tool.outputSchema
                    data_schema = tool.outputSchema["properties"]["data"]
                    assert data_schema["properties"]["title"] == {"type": "string"}
                    assert tool.outputSchema["properties"]["id"]["type"] in ("integer", "string")
                    name = tool.name.removesuffix("create")
                    response = await client.call_tool(name + "create", {"title": "Protocol " + prefix, "note": "Preserve"})
                    assert not response.isError, response.content
                    result = response.structuredContent
                    key = result["id"]
                    assert state["items"]["tester"][key]["title"] == "Protocol " + prefix
                    assert result["metrics"]["model_calls"] == 0
                    assert result["state_token"].startswith("sha256:")
                    edited = await client.call_tool(name + "update", {"id": key, "title": "Edited " + prefix,
                                                                     "expected_state_token": result["state_token"]})
                    assert not edited.isError, edited.content
                    stale = await client.call_tool(name + "delete", {"id": key, "expected_state_token": result["state_token"]})
                    assert stale.isError and key in state["items"]["tester"]
                    assert state["items"]["tester"][key]["note"] == "Preserve"
                    coverage = await client.read_resource("conduit://capabilities")
                    assert '"ready"' in coverage.contents[0].text
                    removed = await client.call_tool(name + "delete", {"id": key,
                                                                      "expected_state_token": edited.structuredContent["state_token"]})
                    assert not removed.isError and key not in state["items"]["tester"]
                    state["sessions"].clear()
                    expired = await client.call_tool(name + "read", {"id": key})
                    assert expired.isError
                    failure = json.loads(expired.content[0].text)
                    assert failure["code"] == "authentication_required"
                    assert failure.get("mutation_retried") is False

        await exercise(StdioServerParameters(command=sys.executable, args=[str(portable / "server.py")],
                                            cwd=str(portable), env=env), "portable")
        runtime = Runtime(manifest, home)
        try:
            await login(runtime)
        finally:
            await runtime.close()
        async with dashboard(home) as dashboard_url:
            await exercise(StdioServerParameters(command=sys.executable,
                args=["-m", "conduit", "--home", str(home), "hub", "--url", dashboard_url], env=env), "universal")
