from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

from .browser import POINT
from .models import BuildRequest, Manifest, Tool, schema, slug

BRIDGE = Path(__file__).with_name("windows.ps1")


async def bridge(operation: str, target=None, args=None):
    if os.name != "nt":
        raise ValueError(
            "Desktop connectors currently require Windows. Website connectors work cross-platform."
        )
    request = {"operation": operation, "target": target or {}, "args": args or {}}

    def call():
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(BRIDGE),
            ],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            payload = json.loads(result.stdout.lstrip("\ufeff"))
        except ValueError as exc:
            raise RuntimeError("Windows adapter did not return JSON. " + result.stderr[-500:]) from exc
        if payload.get("error"):
            raise ValueError(payload["error"])
        return payload

    return await asyncio.to_thread(call)


def desktop_tools():
    tools = []

    def add(name, description, properties=None, required=None, read_only=False):
        properties = {
            "window": {
                "type": "integer",
                "description": "Window handle from desktop_windows. Required if multiple windows match.",
            },
            **(properties or {}),
        }
        tools.append(
            Tool(
                name="desktop_" + name,
                description=description,
                adapter="desktop",
                operation=name,
                input_schema=schema(properties, required),
                read_only=read_only,
                evidence="Windows UI Automation and scoped pointer/keyboard adapter; no vendor API required.",
            )
        )

    add("open", "Launch this installed app if it is not running, then list its windows.")
    add("windows", "List windows belonging to this app only.", read_only=True)
    add(
        "snapshot",
        "Read accessible controls and text in the app window. Returns element references.",
        read_only=True,
    )
    add(
        "click",
        "Invoke, select, toggle, or expand a control. May change or delete app data.",
        {"ref": {"type": "string"}},
        ["ref"],
    )
    add(
        "fill",
        "Replace an accessible editable control's text.",
        {"ref": {"type": "string"}, "text": {"type": "string", "maxLength": 50000}},
        ["ref", "text"],
    )
    add(
        "press",
        "Focus the app and press a shortcut, such as Control+s, Control+z, Enter, or Delete.",
        {"key": {"type": "string", "maxLength": 80}},
        ["key"],
    )
    add(
        "type",
        "Type literal text into the focused app control. Use click first to choose a control.",
        {"text": {"type": "string", "maxLength": 50000}},
        ["text"],
    )
    add(
        "screenshot",
        "Capture this app window. Pointer coordinates are pixels relative to this screenshot.",
        read_only=True,
    )
    add(
        "click_at",
        "Click at window screenshot coordinates. The target must be visible and unobscured.",
        POINT["properties"],
        ["x", "y"],
    )
    add(
        "draw",
        "Draw a stroke or drag through window screenshot coordinates using the app's selected tool. Select a drawing/eraser tool first.",
        {"points": {"type": "array", "items": POINT, "minItems": 2, "maxItems": 500}},
        ["points"],
    )
    return tools


async def discover_desktop(request: BuildRequest) -> Manifest:
    resolved = await bridge("resolve", args={"target": request.target})
    manifest = Manifest(
        id=f"{slug(request.name or resolved['name'])}-{uuid4().hex[:8]}",
        name=request.name or resolved["name"],
        kind="desktop",
        target=request.target,
        desktop=resolved,
        tools=desktop_tools(),
        notes=request.notes,
    )
    windows = await bridge("windows", target=resolved)
    manifest.findings.append({"type": "installed_app", **resolved, "open_windows": len(windows["windows"])})
    if windows["windows"]:
        try:
            snap = await bridge("snapshot", target=resolved, args={"window": windows["windows"][0]["handle"]})
            manifest.findings.append({"type": "accessibility", "controls": len(snap["controls"])})
        except Exception as exc:
            manifest.warnings.append(f"App found; accessibility inspection unavailable: {str(exc)[:250]}")
    else:
        manifest.warnings.append("The app is not open. Use desktop_open, then inspect its window.")
    return manifest
