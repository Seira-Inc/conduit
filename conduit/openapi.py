"""Compile supported OpenAPI operations into data, never downloaded executable code."""

from __future__ import annotations

import copy
import re
from urllib.parse import urljoin

import yaml

from .models import Tool, origin, schema, slug


def parse_spec(body) -> dict | None:
    if isinstance(body, str):
        try:
            body = yaml.safe_load(body)
        except yaml.YAMLError:
            return None
    if (
        isinstance(body, dict)
        and str(body.get("openapi", "")).startswith("3.")
        and isinstance(body.get("paths"), dict)
    ):
        return body
    return None


def resolve(value, doc: dict, seen: tuple = (), depth: int = 0):
    if depth > 35:
        raise ValueError("Schema nesting is too deep")
    if isinstance(value, list):
        return [resolve(x, doc, seen, depth + 1) for x in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        ref = value["$ref"]
        if not ref.startswith("#/"):
            raise ValueError("External schema references need a bundled OpenAPI document")
        if ref in seen:
            raise ValueError("Recursive schemas are not supported by this first version")
        target = doc
        for part in ref[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        return resolve(
            {**target, **{k: v for k, v in value.items() if k != "$ref"}}, doc, seen + (ref,), depth + 1
        )
    result = {k: resolve(v, doc, seen, depth + 1) for k, v in value.items()}
    if result.pop("nullable", False):
        result = {"anyOf": [result, {"type": "null"}]}
    return result


def security(doc: dict, operation: dict) -> list[list[dict]]:
    result = []
    schemes = doc.get("components", {}).get("securitySchemes", {})
    for alternative in operation.get("security", doc.get("security", [])):
        group = []
        for name in alternative:
            item = resolve(schemes.get(name, {}), doc)
            env = "CONDUIT_" + re.sub(r"[^A-Z0-9]", "_", name.upper())
            if item.get("type") in ("oauth2", "openIdConnect") or (
                item.get("type") == "http" and item.get("scheme", "").lower() == "bearer"
            ):
                group.append({"env": env, "in": "header", "name": "Authorization", "prefix": "Bearer "})
            elif item.get("type") == "apiKey" and item.get("in") in ("header", "query", "cookie"):
                group.append({"env": env, "in": item["in"], "name": item["name"]})
            else:
                raise ValueError(f"Unsupported security scheme: {name}")
        result.append(group)
    return result


def compile_spec(doc: dict, spec_url: str, allowed: list[str]) -> tuple[list[Tool], list[str]]:
    tools, warnings, used = [], [], set()
    for path, raw_path_item in list(doc["paths"].items())[:250]:
        if not path.startswith("/"):
            continue
        try:
            path_item = resolve(raw_path_item, doc)
        except (KeyError, TypeError, ValueError) as exc:
            warnings.append(f"Skipped {path}: {exc}")
            continue
        for method, operation in path_item.items():
            if method not in ("get", "post", "put", "patch", "delete", "head"):
                continue
            try:
                servers = operation.get(
                    "servers", path_item.get("servers", doc.get("servers", [{"url": "/"}]))
                )
                server = copy.deepcopy(servers[0])
                base = server["url"]
                for key, value in server.get("variables", {}).items():
                    base = base.replace("{" + key + "}", str(value["default"]))
                if "{" in base:
                    raise ValueError("Unresolved server variables")
                base = urljoin(spec_url, base).rstrip("/")
                if origin(base) not in {origin(u) for u in allowed}:
                    raise ValueError(f"API origin {origin(base)} must be added to allowed origins")
                properties, required, bindings = {}, [], []
                parameters = {(p["in"], p["name"]): p for p in path_item.get("parameters", [])}
                parameters.update({(p["in"], p["name"]): p for p in operation.get("parameters", [])})
                for (location, name), p in parameters.items():
                    if location not in ("path", "query", "header", "cookie"):
                        raise ValueError(f"Unsupported parameter location: {location}")
                    if p.get("style", "form" if location in ("query", "cookie") else "simple") not in (
                        "form",
                        "simple",
                    ):
                        raise ValueError(f"Unsupported serialization for {name}")
                    param_schema = p.get("schema", {"type": "string"})
                    if param_schema.get("type") == "object":
                        raise ValueError(f"Object parameter {name} needs explicit serialization support")
                    if location == "header" and name.lower() in (
                        "host",
                        "authorization",
                        "cookie",
                        "content-length",
                    ):
                        raise ValueError(f"Reserved header parameter {name}")
                    key = name if name not in properties else f"{location}_{name}"
                    properties[key] = {**param_schema, "description": p.get("description", name)}
                    if p.get("required") or location == "path":
                        required.append(key)
                    bindings.append(
                        {
                            "key": key,
                            "name": name,
                            "in": location,
                            "explode": p.get("explode", location in ("query", "cookie")),
                        }
                    )
                body = operation.get("requestBody")
                body_key = None
                if body:
                    if "application/json" not in body.get("content", {}):
                        raise ValueError("Only JSON request bodies are currently supported")
                    body_key = "body" if "body" not in properties else "request_body"
                    properties[body_key] = body["content"]["application/json"].get("schema", {})
                    if body.get("required"):
                        required.append(body_key)
                raw_name = slug(operation.get("operationId") or method + "-" + path, 51).replace("-", "_")
                name, suffix = "api_" + raw_name, 2
                while name in used:
                    name = f"api_{raw_name}_{suffix}"
                    suffix += 1
                used.add(name)
                auth = security(doc, operation)
                auth_note = sorted({i["env"] for g in auth for i in g})
                description = operation.get("summary") or f"{method.upper()} {path}"
                if auth_note:
                    description += ". Credentials: " + ", ".join(auth_note)
                tools.append(
                    Tool(
                        name=name,
                        description=description[:1600],
                        input_schema=schema(properties, required),
                        adapter="http",
                        operation="api",
                        read_only=method in ("get", "head"),
                        evidence=f"OpenAPI operation at {spec_url}: {method.upper()} {path}",
                        config={
                            "base": base,
                            "path": path,
                            "method": method.upper(),
                            "bindings": bindings,
                            "body_key": body_key,
                            "security": auth,
                        },
                    )
                )
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                warnings.append(f"Skipped {method.upper()} {path}: {exc}")
    return tools, warnings
