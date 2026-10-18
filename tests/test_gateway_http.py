"""The gateway over a real socket, the way Claude Code reaches it: Streamable HTTP with SSE, so
server-initiated notifications travel too."""

import asyncio

from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from test_compiled_mcp import dashboard
from test_compiler import family_for
from test_gateway import receipt
from test_registry import READY, verified_connector

from conduit.browser import browser_tools
from conduit.compiler import publish
from conduit.generator import Store
from conduit.models import Manifest
from conduit.registry import public_name


async def test_gateway_over_streamable_http(tmp_path, monkeypatch):
    verified_connector(Store(tmp_path), "demo-1", "Demo", "https://example.com", "records")

    async def fake_call(self, name, arguments, progress=None):
        return receipt(name, arguments)

    async def fake_discover(request, home, progress):
        return Manifest(id="newsite-1", name="New Site", kind="website", target=request.target,
                        allowed_origins=[request.target], tools=browser_tools())

    async def fake_compile(runtime, *, progress=None, brief=None, **kwargs):
        family = family_for(runtime.manifest.target + "/tasks").model_copy(update={"name": "tasks", "label": "tasks"})
        publish(runtime.manifest, family, {**READY, "resource": "tasks"})
        return {"status": "ready"}

    monkeypatch.setattr("conduit.runtime.Runtime.call", fake_call)
    monkeypatch.setattr("conduit.app.discover", fake_discover)
    monkeypatch.setattr("conduit.app.compile_connector", fake_compile)
    monkeypatch.setattr("conduit.app.model_settings", lambda: {"available": True})
    changed = asyncio.Event()

    async def handler(message):
        if isinstance(message, types.ServerNotification) and isinstance(message.root, types.ToolListChangedNotification):
            changed.set()

    async with dashboard(tmp_path) as url:
        async with streamable_http_client(url + "/mcp") as (read, write, _):
            async with ClientSession(read, write, message_handler=handler) as session:
                init = await session.initialize()
                assert init.capabilities.tools.listChanged
                names = {t.name for t in (await session.list_tools()).tools}
                assert public_name("demo-1", "records_read") in names and "seira_build" in names
                result = await session.call_tool(public_name("demo-1", "records_read"), {"id": "r1"})
                assert not result.isError and result.structuredContent["id"] == "r1"
                queued = (await session.call_tool("seira_build", {"site": "https://newsite.example",
                                                                   "request": "add a task"})).structuredContent
                assert queued["status"] == "queued"
                async with asyncio.timeout(10):
                    await changed.wait()
                names = {t.name for t in (await session.list_tools()).tools}
    assert public_name("newsite-1", "tasks_create") in names
