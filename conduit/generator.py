from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from .models import Manifest, load_manifest


def atomic_json(path: Path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


class Store:
    def __init__(self, home: Path):
        self.home = home.resolve()
        self.connectors = self.home / "connectors"
        self.connectors.mkdir(parents=True, exist_ok=True)

    def directory(self, connector_id: str) -> Path:
        import re

        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", connector_id):
            raise ValueError("Invalid connector identifier")
        return self.connectors / connector_id

    def get(self, connector_id: str) -> Manifest:
        return load_manifest(self.directory(connector_id) / "connector.json")

    def list(self) -> list[Manifest]:
        results = []
        for path in self.connectors.glob("*/connector.json"):
            try:
                results.append(load_manifest(path))
            except ValueError:
                continue
        return sorted(results, key=lambda m: m.created_at, reverse=True)

    def config(self, connector_id: str):
        directory = self.directory(connector_id)
        return {
            "mcpServers": {
                connector_id: {
                    "command": sys.executable,
                    "args": [str(directory / "server.py")],
                    "env": {"CONDUIT_HOME": str(self.home)},
                }
            }
        }

    def universal_config(self, url="http://127.0.0.1:4455"):
        return {"mcpServers": {"conduit": {"command": sys.executable,
            "args": ["-m", "conduit", "--home", str(self.home), "hub", "--url", url],
            "env": {"PYTHONPATH": str(Path(__file__).resolve().parent.parent)}}}}

    def upgrade(self) -> list[str]:
        """Repackage existing connectors without rediscovery or changing their saved sessions."""
        from .agent import add_task_tools
        from .browser import browser_tools
        from .desktop import desktop_tools

        updated = []
        for manifest in self.list():
            base_tools = browser_tools() if manifest.kind == "website" else desktop_tools()
            names = {tool.name for tool in base_tools}
            manifest.tools = base_tools + [tool for tool in manifest.tools if tool.name not in names]
            add_task_tools(manifest, [])
            self.write(manifest)
            updated.append(manifest.id)
        return updated

    def codex_config(self, connector_id: str) -> str:
        config = self.config(connector_id)["mcpServers"][connector_id]
        return (
            f"[mcp_servers.{connector_id}]\n"
            f"command = {json.dumps(config['command'])}\n"
            f"args = {json.dumps(config['args'])}\n"
            "startup_timeout_sec = 30\n"
            "tool_timeout_sec = 900\n"
            f"[mcp_servers.{connector_id}.env]\n"
            f"CONDUIT_HOME = {json.dumps(str(self.home))}\n"
        )

    def write(self, manifest: Manifest) -> Path:
        directory = self.directory(manifest.id)
        directory.mkdir(parents=True, exist_ok=True)
        atomic_json(directory / "client-config.json", self.config(manifest.id))
        (directory / "codex-config.toml").write_text(self.codex_config(manifest.id), encoding="utf-8")
        runtime = directory / "_runtime" / "conduit"
        runtime.mkdir(parents=True, exist_ok=True)
        package = Path(__file__).parent
        for source in package.iterdir():
            if source.suffix in (".py", ".ps1"):
                shutil.copy2(source, runtime / source.name)
        entry = '''"""Generated Conduit MCP server. Diagnostics belong on stderr; stdout is MCP only."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "_runtime"))
from conduit.mcp_server import run

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    run(Path(__file__).with_name("connector.json"), read_only=args.read_only, headed=args.headed)
'''
        (directory / "server.py").write_text(entry, encoding="utf-8")
        lock = package.parent / "requirements.lock"
        if lock.exists():
            shutil.copy2(lock, directory / "requirements.txt")
        else:
            (directory / "requirements.txt").write_text(
                "mcp>=1.28,<2\nhttpx>=0.28,<1\nplaywright>=1.51,<2\njsonschema>=4.23,<5\npython-dotenv>=1,<2\nmini-racer>=0.14.1,<0.15\npywin32>=310; sys_platform == 'win32'\n",
                encoding="utf-8",
            )
        auth = sorted(
            {a["env"] for t in manifest.tools for group in t.config.get("security", []) for a in group}
        )
        readme = f"""# {manifest.name} · Conduit MCP connector

Target: {manifest.target}

This bundle contains {len(manifest.tools)} real tools, a discovery report in `connector.json`, and its runtime source.
UI actions can change data. The calling AI must act within your request and inspect the result after changes.

## On this computer

For Codex, add `codex-config.toml` to your Codex configuration; it includes a longer timeout for model tasks.
For clients using JSON settings, use `client-config.json`. Both use local stdio servers.
The client starts `server.py`; you do not need to leave a terminal running.
Conduit login sessions stay in the local Conduit state directory and are NOT included in this bundle.
Close the dashboard preview with **Release preview**, **Copy MCP settings**, or `browser_close`
before using this connector in another client. Browser state has one owner at a time.

The calling AI can use interface tools directly. The `task_do` and named `task_*` tools can also
perform a plain-language request themselves using a signed-in Codex CLI (Astra by default),
or a configured OpenAI-compatible vision endpoint. These tasks send screenshots and interface text
to that model and use its allowance. Set `CONDUIT_MODEL_BACKEND=http`, `CONDUIT_MODEL_BASE_URL`,
`CONDUIT_MODEL` and, if required, `CONDUIT_MODEL_API_KEY` to use another provider.
For Codex, `CONDUIT_MODEL` overrides the model and `CONDUIT_CODEX_PATH` overrides the CLI path.
Inspect each task's status and evidence; model-reported completion is not independent verification.

## On another computer

1. Install Python 3.11 or newer.
2. Run `python -m pip install -r requirements.txt` in this folder.
3. Website connectors need Chrome/Edge, or `python -m playwright install chromium`.
4. Change the command and server.py path in your chosen client config to paths on that computer.
5. Remove or update `CONDUIT_HOME` in that config. Sign in again if needed.
6. Windows app connectors require the target app installed on Windows; rebuild if its executable path differs.

Optional server arguments: `--headed` to see the browser; `--read-only` to restrict changes.
API credential environment variables, if any: {", ".join(auth) or "none"}.

## Discovered limitations

{os.linesep.join("- " + warning for warning in manifest.warnings) or "- No discovery warnings. This is not a guarantee that every app feature is automatable."}

Compiled program tools have passed complete CRUD lifecycle tests and execute without model calls.
Their current verification status is recorded per resource in connector.json. Generic browser and
task_* tools are fallbacks and do not imply that full CRUD has been verified.
Login expiry and website changes can require reconnection or recompilation.
"""
        (directory / "README.md").write_text(readme, encoding="utf-8")
        atomic_json(directory / "connector.json", manifest.model_dump())
        return directory
