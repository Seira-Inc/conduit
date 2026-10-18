import asyncio
import base64
import os
import subprocess
from pathlib import Path

import pytest

from conduit.desktop import bridge, discover_desktop
from conduit.models import BuildRequest


@pytest.mark.desktop
@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("CONDUIT_TEST_DESKTOP") != "1",
    reason="Set CONDUIT_TEST_DESKTOP=1 on an interactive Windows desktop",
)
async def test_real_windows_controls_create_update_delete():
    fixture = Path(__file__).with_name("windows_fixture.ps1")
    process = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", str(fixture)],
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        target = None
        for _ in range(15):
            await asyncio.sleep(0.4)
            target = await bridge("resolve", args={"target": f"pid:{process.pid}"})
            windows = await bridge("windows", target)
            if windows["windows"]:
                break
        assert windows["windows"]
        launcher = {
            **target,
            "executable": str(Path(target["executable"]).with_name("powershell-launcher.exe")),
        }
        launcher_windows = await bridge("windows", launcher)
        assert {w["handle"] for w in windows["windows"]} <= {w["handle"] for w in launcher_windows["windows"]}
        manifest = await discover_desktop(BuildRequest(kind="desktop", target=f"pid:{process.pid}"))
        assert manifest.desktop["pid"] == process.pid
        assert any(f["type"] == "accessibility" and f["controls"] >= 4 for f in manifest.findings)
        window = windows["windows"][0]["handle"]

        async def ref(name):
            snap = await bridge("snapshot", target, {"window": window})
            return next(c["ref"] for c in snap["controls"] if c["name"] == name)

        await bridge(
            "fill", target, {"window": window, "ref": await ref("Record title"), "text": "Conduit created"}
        )
        await bridge("click", target, {"window": window, "ref": await ref("Create record")})
        snapshot = await bridge("snapshot", target, {"window": window})
        assert any(c["name"] == "Record: Conduit created" for c in snapshot["controls"])
        await bridge(
            "fill", target, {"window": window, "ref": await ref("Record title"), "text": "Conduit changed"}
        )
        await bridge("click", target, {"window": window, "ref": await ref("Update record")})
        snapshot = await bridge("snapshot", target, {"window": window})
        assert any(c["name"] == "Updated: Conduit changed" for c in snapshot["controls"])
        image = await bridge("screenshot", target, {"window": window})
        assert base64.b64decode(image["image_base64"]).startswith(b"\x89PNG")
        await bridge("click", target, {"window": window, "ref": await ref("Delete record")})
        snapshot = await bridge("snapshot", target, {"window": window})
        assert any(c["name"] == "No record" for c in snapshot["controls"])
        with pytest.raises(ValueError, match="choose one window"):
            await bridge("snapshot", target, {"window": 99999999})
    finally:
        process.terminate()
        process.wait(timeout=10)
