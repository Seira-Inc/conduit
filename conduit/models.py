from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Tool(StrictModel):
    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    description: str
    input_schema: dict[str, Any] = Field(default_factory=lambda: schema())
    adapter: Literal["http", "browser", "desktop", "agent", "program"]
    operation: str
    config: dict[str, Any] = Field(default_factory=dict)
    read_only: bool = False
    evidence: str = ""
    output_schema: dict[str, Any] | None = None
    resource: str = ""
    execution_mode: str = ""
    adapter_version: int = 1
    verification: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def state_contract(self):
        from .state_guard import add_state_contract
        return add_state_contract(self)


class Manifest(StrictModel):
    schema_version: Literal[1, 2] = 2
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    name: str
    kind: Literal["website", "desktop"]
    target: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    allowed_origins: list[str] = Field(default_factory=list)
    login_origins: list[str] = Field(default_factory=list, max_length=12)
    allow_private: bool = False
    tools: list[Tool] = Field(default_factory=list)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    desktop: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""
    capabilities: list[dict[str, Any]] = Field(default_factory=list)
    compilation: dict[str, Any] = Field(default_factory=dict)
    file_access: Literal["native", "downloads"] = "native"

    @field_validator("tools")
    @classmethod
    def unique_tools(cls, value: list[Tool]) -> list[Tool]:
        names = [tool.name for tool in value]
        if len(names) != len(set(names)):
            raise ValueError("Tool names must be unique")
        return value


class BuildRequest(StrictModel):
    target: str = Field(min_length=1, max_length=2048)
    kind: Literal["website", "desktop"] = "website"
    name: str = Field(default="", max_length=100)
    spec_url: str | None = None
    max_pages: int = Field(default=3, ge=1, le=8)
    browser: bool = True
    allow_private: bool = False
    allowed_origins: list[str] = Field(default_factory=list, max_length=12)
    notes: str = Field(default="", max_length=12000)
    tasks: list[str] = Field(default_factory=list, max_length=12)
    compile: bool = True


def schema(properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


def slug(text: str, limit: int = 48) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit] or "connector"


def normalize_url(value: str) -> str:
    value = value.strip()
    if "://" not in value:
        value = "https://" + value
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("Enter an http:// or https:// website URL.")
    if parts.username or parts.password:
        raise ValueError("Use a login session or environment variable instead of credentials in a URL.")
    # Force validation of malformed ports before emitting a connector.
    _ = parts.port
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


def origin(url: str) -> str:
    parts = urlsplit(normalize_url(url))
    port = parts.port
    suffix = f":{port}" if port and port != (443 if parts.scheme == "https" else 80) else ""
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    return f"{parts.scheme}://{host}{suffix}"


def website_origins(url: str) -> set[str]:
    """A URL authorizes its exact host, the conventional www alias, and HTTPS upgrade."""
    parts = urlsplit(normalize_url(url))
    values = {origin(url)}
    if parts.scheme == "http" and not parts.port:
        values.add(origin("https://" + parts.netloc))
    host = parts.hostname
    if host and "." in host and not all(c in "0123456789." for c in host) and not parts.port:
        alias = host[4:] if host.startswith("www.") else "www." + host
        values.add(origin(parts.scheme + "://" + alias))
        values.add(origin("https://" + alias))
    return values


def load_manifest(path: Path) -> Manifest:
    return Manifest.model_validate_json(path.read_text(encoding="utf-8"))
