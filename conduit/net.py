"""Bounded HTTP access; credentials are attached only after checking the destination."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
from urllib.parse import urljoin, urlsplit

import httpx

from .models import normalize_url, origin

MAX_BYTES = 4 * 1024 * 1024


class NetworkPolicy:
    def __init__(self, allowed_origins: list[str], allow_private: bool = False):
        self.allowed = {origin(url) for url in allowed_origins}
        self.allow_private = allow_private

    async def check(self, url: str) -> str:
        url = normalize_url(url)
        if origin(url) not in self.allowed:
            raise ValueError(
                f"Destination {origin(url)} is outside this connector. Add it to allowed origins."
            )
        host = urlsplit(url).hostname
        if not self.allow_private:
            addresses = await asyncio.to_thread(socket.getaddrinfo, host, None, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise ValueError("Private or local addresses require the Allow local network option.")
        return url


async def fetch(
    policy: NetworkPolicy,
    url: str,
    *,
    method: str = "GET",
    params=None,
    headers: dict | None = None,
    json_body=None,
    data=None,
) -> dict:
    url = await policy.check(url)
    request_headers = {"User-Agent": "Conduit/0.1 (local MCP connector)", **(headers or {})}
    async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False) as client:
        for _ in range(6):
            async with client.stream(
                method, url, params=params, headers=request_headers, json=json_body, data=data
            ) as response:
                if response.is_redirect:
                    if method not in ("GET", "HEAD"):
                        raise ValueError("An action returned a redirect; it was not replayed automatically.")
                    destination = await policy.check(urljoin(str(response.url), response.headers["location"]))
                    if origin(destination) != origin(url):
                        # Never forward credentials between origins, even two allowed origins.
                        request_headers = {"User-Agent": request_headers["User-Agent"]}
                    url, params = destination, None
                    continue
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError("Response exceeded the 4 MB limit.")
                    chunks.append(chunk)
                raw = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
                content_type = response.headers.get("content-type", "")
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = raw
                return {
                    "url": str(response.url),
                    "status": response.status_code,
                    "content_type": content_type,
                    "body": body,
                }
    raise ValueError("Too many redirects.")


def auth_values(requirements: list[list[dict]]) -> tuple[dict, dict, dict]:
    """OpenAPI security is OR across alternatives, AND within each alternative."""
    if not requirements:
        return {}, {}, {}
    missing = set()
    for alternative in requirements:
        headers, query, cookies = {}, {}, {}
        complete = True
        for item in alternative:
            value = os.environ.get(item["env"])
            if not value:
                missing.add(item["env"])
                complete = False
                continue
            value = item.get("prefix", "") + value
            destination = {"header": headers, "query": query, "cookie": cookies}[item["in"]]
            destination[item["name"]] = value
        if complete:
            return headers, query, cookies
    raise ValueError(
        "Authentication needed. Set the connector environment variable(s): " + ", ".join(sorted(missing))
    )
