"""Bounded network evidence. Secret values are session-local and never exported."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SECRET = re.compile(r"password|passwd|secret|authorization|cookie|token|csrf|xsrf|api[-_]?key", re.I)
NOISE = re.compile(r"/(?:collect|telemetry|analytics|beacon|track)(?:/|$)", re.I)


def redact(value):
    if isinstance(value, dict):
        return {k: "[session secret]" if SECRET.search(k) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value[:60]]
    if isinstance(value, str):
        value = re.sub(r"(?i)(bearer\s+)[\w.\-+/=]+", r"\1[session secret]", value)
        value = re.sub(r"eyJ[\w-]+\.[\w-]+\.[\w-]+", "[session secret]", value)
        return value[:12000]
    return value


def safe_url(url):
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode([
        (k, "[session secret]" if SECRET.search(k) else v) for k, v in parse_qsl(p.query)
    ]), ""))


def shape(value):
    if isinstance(value, dict):
        return {k: shape(v) for k, v in list(value.items())[:60]}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    return type(value).__name__


class Recorder:
    def __init__(self):
        self.events = []
        self.headers = {}  # origin -> current secret-bearing request headers
        self.pending = set()
        self.phase = "discovery"
        self.sequence = 0
        self.enabled = True
        self.header_changed = asyncio.Event()
        self.secret_values = set()  # values resolved from session bindings, masked wherever they appear

    def mask(self, value):
        secrets = [s for s in self.secret_values if len(s) >= 8]
        if not secrets:
            return value
        if isinstance(value, dict):
            return {k: self.mask(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.mask(v) for v in value]
        if isinstance(value, str):
            for secret in secrets:
                value = value.replace(secret, "[session secret]")
        return value

    def attach(self, context):
        # A new browser must reacquire dynamic headers from its own live session.
        self.headers.clear()
        self.header_changed.clear()
        context.on("request", self._request)
        context.on("response", self._response)
        context.on("page", self._page)
        for page in context.pages:
            self._page(page)

    def _page(self, page):
        page.on("websocket", self._websocket)

    def _websocket(self, ws):
        ws.on("framesent", lambda data: self._frame(ws.url, "sent", data))
        ws.on("framereceived", lambda data: self._frame(ws.url, "received", data))

    def _frame(self, url, direction, data):
        if not self.enabled:
            return
        try:
            body = json.loads(data) if len(data) < 12000 else None
        except (ValueError, TypeError):
            body = None
        self._append({"kind": "websocket", "url": safe_url(url), "direction": direction,
                      "body": redact(body), "binary_or_opaque": body is None})

    def _append(self, event):
        self.sequence += 1
        self.events.append({"id": f"e{self.sequence}", "phase": self.phase, **self.mask(event)})
        if len(self.events) > 160:
            # A write the application made while we drove its form is the single most valuable thing
            # recorded, and the chatter of a record page can bury it in a few seconds. Keep those.
            pinned = [e for e in self.events[:-160] if self.pinned(e)][-40:]
            self.events = pinned + self.events[-160:]

    @staticmethod
    def pinned(event):
        return (event.get("kind") == "http" and event.get("method") in ("POST", "PUT", "PATCH", "DELETE")
                and str(event.get("phase", "")).startswith(("probe_record", "verify:")))

    def _request(self, request):
        # Synchronous headers excludes cookies, which remain in the browser's cookie jar.
        p = urlsplit(request.url)
        host = f"{p.scheme}://{p.netloc}"
        secret_headers = {k: v for k, v in request.headers.items() if SECRET.search(k)}
        if secret_headers:
            self.headers.setdefault(host, {}).update(secret_headers)
            self.header_changed.set()

    async def session_header(self, host, key, timeout=8):
        """Wait for SPA startup/refresh without replaying a request or freezing a token."""
        try:
            async with asyncio.timeout(timeout):
                while True:
                    self.header_changed.clear()
                    value = self.headers.get(host, {}).get(key.lower())
                    if value:
                        return value
                    await self.header_changed.wait()
        except TimeoutError:
            return None

    def _response(self, response):
        if not self.enabled or len(self.pending) >= 40:
            return
        phase = self.phase
        task = asyncio.create_task(self._capture(response, phase))
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)

    async def _capture(self, response, phase):
        try:
            request = response.request
            if request.resource_type not in ("xhr", "fetch", "document", "script"):
                return
            if NOISE.search(urlsplit(request.url).path):
                return
            content_type = response.headers.get("content-type", "")
            if "json" not in content_type:
                return
            if int(response.headers.get("content-length", "0")) > 262144:
                return
            body_bytes = await asyncio.wait_for(response.body(), timeout=5)
            if len(body_bytes) > 262144:
                return
            body = json.loads(body_bytes)
            try:
                sent = request.post_data_json
            except Exception:
                sent = request.post_data
            self._append({"kind": "http", "phase": phase, "url": safe_url(request.url),
                          "initiator": safe_url(request.frame.url),
                          "method": request.method, "status": response.status,
                          "request": redact(sent), "response": redact(body),
                          "headers": {k: "[session secret]" if SECRET.search(k) else v
                                      for k, v in request.headers.items()
                                      if k in ("content-type", "accept") or SECRET.search(k)},
                          "hash": hashlib.sha256(body_bytes).hexdigest()})
        except Exception:
            pass  # Evidence loss must not break the application being observed.

    async def flush(self):
        if self.pending:
            pending = list(self.pending)
            _, unfinished = await asyncio.wait(pending, timeout=3)
            for task in unfinished:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    def http(self, url, method, status, request, response, headers):
        """Capture actual direct executions too, which do not emit browser network events."""
        if not self.enabled:
            return
        raw = json.dumps(response, default=str)
        self._append({"kind": "http", "transport": "direct", "url": safe_url(url), "method": method,
                      "status": status, "request": redact(request), "response": redact(response),
                      "headers": {k: "[session secret]" if SECRET.search(k) else v for k, v in headers.items()},
                      "hash": hashlib.sha256(raw.encode()).hexdigest()})

    def relationships(self):
        """Infer value flow without exporting values as adapter constants."""
        def leaves(value, prefix=""):
            if isinstance(value, dict):
                for key, child in value.items():
                    if not SECRET.search(key):
                        yield from leaves(child, (prefix + "." + key).strip("."))
            elif isinstance(value, list):
                for i, child in enumerate(value[:30]):
                    yield from leaves(child, f"{prefix}.{i}")
            elif isinstance(value, str) and 4 <= len(value) <= 200 and value != "[session secret]":
                yield prefix, value
        producers, links = {}, []
        for event in self.events:
            request_values = list(leaves(event.get("request")))
            for value, source in producers.items():
                if value in event["url"]:
                    links.append({"from": source, "to": event["id"] + ".url", "relationship": "response_to_path"})
                for path, sent in request_values:
                    if sent == value:
                        links.append({"from": source, "to": event["id"] + ".request." + path,
                                      "relationship": "response_to_request"})
            for path, value in leaves(event.get("response")):
                producers[value] = event["id"] + ".response." + path
        return links[-100:]

    def summary(self):
        # Export shapes and provenance, never recorded payloads or account identifiers.
        return [{"id": e["id"], "phase": e["phase"], "kind": e["kind"],
                 "method": e.get("method"), "status": e.get("status"),
                 "origin": f"{urlsplit(e['url']).scheme}://{urlsplit(e['url']).netloc}",
                 "response_shape": shape(e.get("response", e.get("body"))), "hash": e.get("hash")}
                for e in self.events]
