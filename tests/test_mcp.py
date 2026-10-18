import base64
import json
import re
import shutil
import sys
from datetime import timedelta

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from conduit.discovery import discover
from conduit.generator import Store
from conduit.models import BuildRequest


@pytest.mark.integration
async def test_no_spec_generated_mcp_crud_draw_erase(local_site, tmp_path):
    url, state = local_site
    home = tmp_path / "state"
    manifest = await discover(BuildRequest(target=url + "/playground", allow_private=True, max_pages=1), home)
    assert not any(t.adapter == "http" for t in manifest.tools)
    assert any(t.operation == "form" for t in manifest.tools)
    assert state["writes"] == 0  # Discovery never submits forms or executes writes.
    directory = Store(home).write(manifest)
    portable = tmp_path / "portable"
    shutil.copytree(directory, portable)
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(portable / "server.py")],
        cwd=str(portable),
        env={"CONDUIT_HOME": str(home)},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=90)) as session:
            init = await session.initialize()
            assert init.serverInfo.name.startswith("conduit-")
            listing = await session.list_tools()
            assert {"browser_draw", "browser_click", "browser_fill", "browser_press"} <= {
                t.name for t in listing.tools
            }

            async def call(name, args=None):
                response = await session.call_tool(name, args or {})
                assert not response.isError, str(response.content)
                return json.loads(next(c.text for c in response.content if c.type == "text"))

            async def control(name):
                snapshot = await call("browser_snapshot")
                return next(c["ref"] for c in snapshot["controls"] if c["name"] == name)

            form_name = next(t.name for t in manifest.tools if t.operation == "form")
            created = await call(form_name, {"title": "Conduit original", "text": "Created without an API"})
            assert "Conduit original" in created["text"] and "1 note" in created["text"]
            await call("browser_click", {"ref": await control("Edit Conduit original")})
            await call("browser_fill", {"ref": await control("Note title"), "text": "Conduit edited"})
            edited = await call("browser_click", {"ref": await control("Save changes")})
            assert "Conduit edited" in edited["text"] and "Conduit original" not in edited["text"]
            removed = await call("browser_click", {"ref": await control("Delete Conduit edited")})
            assert "0 notes" in removed["text"] and "Conduit edited" not in removed["text"]

            snapshot = await call("browser_snapshot")
            stale = snapshot["controls"][0]["ref"]
            await call("browser_snapshot")
            invalid = await session.call_tool("browser_click", {"ref": stale})
            assert invalid.isError and "stale" in invalid.content[0].text

            snapshot = await call("browser_snapshot")
            bounds = next(c["bounds"] for c in snapshot["controls"] if c["tag"] == "canvas")
            points = [
                {"x": bounds["x"] + 65, "y": bounds["y"] + 100},
                {"x": bounds["x"] + 180, "y": bounds["y"] + 150},
                {"x": bounds["x"] + 270, "y": bounds["y"] + 90},
            ]
            drawn = await call("browser_draw", {"points": points})
            pixels = int(re.search(r"(\d+) painted pixels", drawn["text"])[1])
            assert pixels > 700
            screenshot = await session.call_tool("browser_screenshot", {})
            image = next(c for c in screenshot.content if c.type == "image")
            assert base64.b64decode(image.data).startswith(b"\x89PNG")
            await call("browser_click", {"ref": await control("Eraser (E)")})
            erased = await call("browser_draw", {"points": points})
            assert "0 painted pixels" in erased["text"]
            report = await session.read_resource("conduit://discovery")
            assert "No API specification" in report.contents[0].text
