import json

import pytest

from conduit.models import Manifest, Tool, normalize_url, origin, schema
from conduit.net import NetworkPolicy, auth_values, fetch
from conduit.openapi import compile_spec
from conduit.runtime import Runtime


def document(paths, **extras):
    return {"openapi": "3.1.0", "info": {"title": "Test", "version": "1"}, "paths": paths, **extras}


def test_url_validation_and_origin():
    assert normalize_url("example.com") == "https://example.com/"
    assert origin("https://EXAMPLE.com:443/path?q=1") == "https://example.com"
    assert origin("http://[::1]:8000/path") == "http://[::1]:8000"
    with pytest.raises(ValueError):
        normalize_url("https://user:secret@example.com")
    with pytest.raises(ValueError):
        normalize_url("file:///C:/test")


def test_openapi_bundled_refs_and_overridden_parameters():
    doc = document(
        {
            "/things/{id}": {
                "parameters": [{"name": "id", "in": "path", "schema": {"type": "string"}}],
                "post": {
                    "operationId": "save",
                    "parameters": [{"name": "id", "in": "path", "schema": {"type": "integer"}}],
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Thing"}}},
                    },
                },
            }
        },
        components={
            "schemas": {
                "Thing": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                }
            }
        },
    )
    tools, warnings = compile_spec(doc, "https://example.com/openapi.json", ["https://example.com"])
    assert not warnings
    assert tools[0].input_schema["properties"]["id"]["type"] == "integer"
    assert set(tools[0].input_schema["required"]) == {"id", "body"}
    assert tools[0].input_schema["properties"]["body"]["required"] == ["title"]
    assert not tools[0].read_only


def test_unsupported_operations_reported_not_invented():
    doc = document(
        {"/upload": {"post": {"requestBody": {"content": {"multipart/form-data": {}}}}}},
        servers=[{"url": "https://other.example"}],
    )
    tools, warnings = compile_spec(doc, "https://example.com/spec", ["https://example.com"])
    assert not tools and "allowed origins" in warnings[0]
    tools, warnings = compile_spec(
        doc, "https://example.com/spec", ["https://example.com", "https://other.example"]
    )
    assert not tools and "JSON request bodies" in warnings[0]


def test_recursive_and_external_refs_skip_with_reason():
    for ref in ("https://evil.example/schema", "#/components/schemas/Node"):
        doc = document(
            {
                "/tree": {
                    "post": {"requestBody": {"content": {"application/json": {"schema": {"$ref": ref}}}}}
                }
            },
            components={
                "schemas": {
                    "Node": {"type": "object", "properties": {"next": {"$ref": "#/components/schemas/Node"}}}
                }
            },
        )
        tools, warnings = compile_spec(doc, "https://example.com/spec", ["https://example.com"])
        assert not tools and warnings


def test_security_alternatives_and_no_secret_errors(monkeypatch):
    monkeypatch.setenv("ONE", "never-print-this-key")
    groups = [
        [{"env": "ONE", "in": "header", "name": "X-Token"}, {"env": "TWO", "in": "query", "name": "key"}]
    ]
    with pytest.raises(ValueError) as error:
        auth_values(groups)
    assert "never-print-this-key" not in str(error.value)
    assert auth_values(groups + [[]]) == ({}, {}, {})


async def test_private_network_and_redirect_scope(local_site):
    url, _ = local_site
    with pytest.raises(ValueError, match="Private or local"):
        await NetworkPolicy([url]).check(url)
    with pytest.raises(ValueError, match="outside this connector"):
        await fetch(NetworkPolicy([url], True), url + "/redirect")


async def test_runtime_validation_and_readonly(tmp_path):
    tool = Tool(
        name="delete",
        description="Delete a test item",
        adapter="http",
        operation="api",
        input_schema=schema({"id": {"type": "integer"}}, ["id"]),
    )
    runtime = Runtime(
        Manifest(id="test", name="test", kind="website", target="https://example.com", tools=[tool]),
        tmp_path,
        read_only=True,
    )
    with pytest.raises(ValueError, match="constraint"):
        await runtime.call("delete", {"id": "wrong"})
    with pytest.raises(ValueError, match="read-only"):
        await runtime.call("delete", {"id": 1})
    with pytest.raises(ValueError, match="Unknown tool"):
        await runtime.call("shell", {"command": "anything"})


async def test_real_optional_api_crud(local_site, tmp_path, monkeypatch):
    url, state = local_site
    doc = (await fetch(NetworkPolicy([url], True), url + "/description.json"))["body"]
    tools, warnings = compile_spec(doc, url + "/description.json", [url])
    assert not warnings
    runtime = Runtime(
        Manifest(
            id="api",
            name="API",
            kind="website",
            target=url,
            allowed_origins=[url],
            allow_private=True,
            tools=tools,
        ),
        tmp_path,
    )
    monkeypatch.setenv("CONDUIT_TOKEN", "test-only-key")
    result = await runtime.call("api_create_item", {"body": {"title": "Made by Conduit"}})
    assert result["status"] == 201 and state["items"] == {"1": "Made by Conduit"}
    result = await runtime.call("api_list_items", {"tag": ["one", "two"]})
    assert result["body"]["tags"] == ["one", "two"]
    assert "test-only-key" not in json.dumps(result)
    await runtime.call("api_delete_item", {"id": "1"})
    assert state["items"] == {} and state["writes"] == 2
