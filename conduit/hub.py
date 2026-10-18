"""Universal MCP facade over the dashboard's single account/session owner."""
import hashlib
import json
from urllib.parse import urlsplit

import httpx
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .models import Tool
from .operation_import import verified_program


async def serve_hub(url):
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname not in ("localhost", "127.0.0.1", "::1") or parts.username:
        raise ValueError("The universal MCP connects only to a local Conduit dashboard")
    server = Server("conduit", version="0.2.0", instructions=
        "Use the typed verified tools directly. Their runtime verifies writes without model calls or screenshots. "
        "Some connectors verify one operation only; do not infer full CRUD support from their presence. "
        "Read conduit://capabilities for tested coverage. The dashboard owns login sessions. "
        "Pass the last read's state_token as expected_state_token when updating or deleting. "
        "An uncertain mutation outcome must not be blindly retried.")
    mapping = {}
    async with httpx.AsyncClient(base_url=url.rstrip("/"), timeout=150, trust_env=False,
                                 headers={"X-Conduit-Local": "1"}) as client:
        async def catalog():
            response = await client.get("/api/connectors")
            response.raise_for_status()
            entries = []
            for connector in response.json():
                r = await client.get("/api/connectors/" + connector["id"])
                r.raise_for_status()
                entries.append(r.json())
            return entries

        @server.list_tools()
        async def listing():
            tools = []
            mapping.clear()
            for connector in await catalog():
                prefix = hashlib.sha256(connector["id"].encode()).hexdigest()[:8]
                for tool in connector["tools"]:
                    if not verified_program(Tool.model_validate(tool)):
                        continue
                    name = prefix + "__" + tool["name"]
                    mapping[name] = (connector["id"], tool["name"])
                    tools.append(types.Tool(name=name, description=connector["name"] + ": " + tool["description"],
                        inputSchema=tool["input_schema"], outputSchema=tool.get("output_schema"),
                        annotations=types.ToolAnnotations(readOnlyHint=tool["read_only"],
                                                          destructiveHint=not tool["read_only"])))
            return sorted(tools, key=lambda t: t.name)

        @server.call_tool()
        async def call(name, arguments):
            if name not in mapping:
                await listing()
            if name not in mapping:
                raise ValueError("Unknown or unverified compiled tool")
            connector_id, tool_name = mapping[name]
            try:
                response = await client.post(f"/api/connectors/{connector_id}/call",
                                             json={"tool": tool_name, "arguments": arguments})
                data = response.json()
                if response.is_error:
                    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(data))], isError=True)
                result = data["result"]
                return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result))],
                                            structuredContent=result, isError=False)
            except httpx.HTTPError:
                return types.CallToolResult(content=[types.TextContent(type="text", text=
                    "The dashboard connection was interrupted. Check the operation's outcome before retrying a write.")], isError=True)

        @server.list_resources()
        async def resources():
            return [types.Resource(uri="conduit://capabilities", name="CRUD coverage", mimeType="application/json")]

        @server.read_resource()
        async def read(uri):
            if str(uri) != "conduit://capabilities":
                raise ValueError("Unknown resource")
            return json.dumps([{k: c[k] for k in ("id", "name", "capabilities", "compilation")} for c in await catalog()])

        async with stdio_server() as (stream_in, stream_out):
            await server.run(stream_in, stream_out, server.create_initialization_options())
