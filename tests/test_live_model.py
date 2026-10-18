import asyncio
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from conduit.desktop import bridge
from conduit.discovery import discover
from conduit.generator import Store
from conduit.models import BuildRequest


@pytest.mark.live_model
@pytest.mark.skipif(
    os.environ.get("CONDUIT_TEST_MODEL") != "1", reason="Set CONDUIT_TEST_MODEL=1 to run a real model"
)
async def test_model_creates_note_without_api_or_recorded_workflow(local_site, tmp_path):
    url, _ = local_site
    manifest = await discover(
        BuildRequest(target=url + "/playground", max_pages=1, allow_private=True, tasks=["Create a note"]),
        tmp_path,
    )
    directory = Store(tmp_path).write(manifest)
    env = {k: v for k, v in os.environ.items() if k.startswith("CONDUIT_")}
    env["CONDUIT_HOME"] = str(tmp_path)
    params = StdioServerParameters(command=sys.executable, args=[str(directory / "server.py")], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(minutes=4)) as client:
            await client.initialize()
            response = await client.call_tool(
                "task_create_a_note",
                {
                    "details": "Create exactly one note titled Conduit live model test with the text Built from the interface alone. Do not make any other changes.",
                    "max_steps": 8,
                },
            )
            assert not response.isError, response.content
            result = json.loads(response.content[0].text)
            (tmp_path / "model-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            assert result["status"] == "model_reported_complete", result
            observed = await client.call_tool("browser_snapshot", {})
            assert not observed.isError
            snapshot = json.loads(observed.content[0].text)
            assert "Conduit live model test" in snapshot["text"]
            assert "Built from the interface alone" in snapshot["text"]
            assert "1 note" in snapshot["text"]


@pytest.mark.live_model
@pytest.mark.desktop
@pytest.mark.skipif(
    os.name != "nt"
    or os.environ.get("CONDUIT_TEST_MODEL") != "1"
    or os.environ.get("CONDUIT_TEST_DESKTOP") != "1",
    reason="Requires an interactive Windows desktop and explicit live-model test opt-in",
)
async def test_model_operates_native_app_through_generated_mcp(tmp_path):
    process = subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-STA",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(Path(__file__).with_name("windows_fixture.ps1")),
        ],
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(15):
            await asyncio.sleep(0.4)
            target = await bridge("resolve", args={"target": f"pid:{process.pid}"})
            if (await bridge("windows", target))["windows"]:
                break
        manifest = await discover(
            BuildRequest(kind="desktop", target=f"pid:{process.pid}", tasks=["Create a record"]),
            tmp_path,
        )
        directory = Store(tmp_path).write(manifest)
        env = {k: v for k, v in os.environ.items() if k.startswith("CONDUIT_")}
        env["CONDUIT_HOME"] = str(tmp_path)
        params = StdioServerParameters(command=sys.executable, args=[str(directory / "server.py")], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(minutes=4)) as client:
                await client.initialize()
                response = await client.call_tool(
                    "task_create_a_record",
                    {
                        "details": "Create one record with the title Conduit native model test.",
                        "max_steps": 6,
                    },
                )
                assert not response.isError, response.content
                result = json.loads(response.content[0].text)
                (tmp_path / "native-model-result.json").write_text(
                    json.dumps(result, indent=2), encoding="utf-8"
                )
                assert result["status"] == "model_reported_complete", result
                observed = await client.call_tool("desktop_snapshot", {})
                assert not observed.isError, observed.content
                controls = json.loads(observed.content[0].text)["controls"]
                assert any(c["name"] == "Record: Conduit native model test" for c in controls)
    finally:
        process.terminate()
        process.wait(timeout=10)
