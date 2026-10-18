"""Operation-focused public sources and live frontend evidence."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .browser import frame_selector
from .models import origin
from .net import NetworkPolicy, fetch
from .recorder import SECRET, redact, safe_url, shape


def prompt_evidence(evidence, limit=105000):
    """Keep valid JSON and recent operation evidence; never cut the serialized object in half."""
    value = {**evidence, "page": dict(evidence["page"])}
    page = value["page"]
    page["text"] = page.get("text", "")[:8000]
    page["controls"] = page.get("controls", [])[:100]
    value["local_storage"] = [item if len(json.dumps(item)) <= 12000 else
        {"key": item["key"], "shape": shape(item.get("value")), "value_omitted": "large application state"}
        for item in value.get("local_storage", [])[:10]]
    latest = {}
    for source in evidence["sources"]:
        latest[(source["url"], source["kind"])] = source
    value["sources"] = [{**source, "content": source["content"][:6000]}
                        for source in reversed(list(latest.values()))]
    encoded = json.dumps(redact(value), ensure_ascii=False)
    while len(encoded) > limit:
        # Trim ordinary sources before the filtered Markdown, so its coherent prefix survives; only
        # halve the crawl4ai source when nothing else is left to shrink.
        trimmable = [s for s in value["sources"] if s["kind"] != "crawl4ai"] or value["sources"]
        longest = max(trimmable, key=lambda s: len(s["content"]), default=None)
        if longest and len(longest["content"]) > 1200:
            longest["content"] = longest["content"][:len(longest["content"]) // 2]
        elif len(value.get("network", [])) > 5:
            value["network"] = value["network"][1:]
        elif len(page["controls"]) > 20:
            page["controls"] = page["controls"][:-10]
        else:
            break
        encoded = json.dumps(redact(value), ensure_ascii=False)
    return encoded


def excerpts(source, limit=16000):
    if len(source) <= limit:
        return source
    windows = [source[max(0, m.start() - 400):m.start() + 1600] for m in re.finditer(
        r"fetch\(|axios|XMLHttpRequest|WebSocket|registerTool|requestSubmit|onSubmit|mutation|serialize|/api/", source)]
    return "\n…\n".join(windows)[:limit] or source[:limit]


class EvidenceCollector:
    def __init__(self, session, manifest):
        self.session, self.manifest = session, manifest
        self.sources = []
        self.seen = set()
        self.documents = {}
        self.keys = []
        self.secret_values = set()
        self.crawl4ai_python = os.environ.get("CONDUIT_CRAWL4AI_PYTHON", "")
        self.extractions = OrderedDict()
        self.processors = {"crawl4ai": {"status": "pending" if self.crawl4ai_python else "disabled",
                                      "runs": 0, "cache_hits": 0, "duration_ms": 0, "model_calls": 0}}

    async def enrich_html(self, url, html):
        """Supplement existing evidence; never replace the DOM or runtime auth bindings."""
        if not self.crawl4ai_python:
            return
        from .crawl4ai_evidence import extract

        status = self.processors["crawl4ai"]
        soup = BeautifulSoup(html, "html.parser")
        for node in soup.select("script,style,svg,link,base"):
            node.decompose()
        for node in soup.find_all(True):
            secret_field = SECRET.search(str(node.get("name", "")) + str(node.get("id", "")))
            secret_field = secret_field or node.get("type") == "password"
            for key, value in list(node.attrs.items()):
                if SECRET.search(key) or (secret_field and key in ("value", "content")):
                    node[key] = "[session secret]"
                elif isinstance(value, str):
                    node[key] = self.scrub(safe_url(value) if key in ("href", "action", "src") else value)
            if secret_field and node.name == "textarea":
                node.clear()
                node.append("[session secret]")
        for node in soup.find_all(string=True):
            node.replace_with(self.scrub(str(node)))
        cleaned = self.scrub(str(soup))
        truncated = len(cleaned) > 250000
        cleaned = cleaned[:250000]
        address = self.scrub(safe_url(url))
        digest = hashlib.sha256((address + "\n" + cleaned).encode()).hexdigest()
        started = time.perf_counter()
        try:
            if digest in self.extractions:
                result = self.extractions[digest]
                self.extractions.move_to_end(digest)
                status["cache_hits"] += 1
            else:
                status["runs"] += 1
                result = await extract(Path(self.crawl4ai_python), cleaned, address)
                if (not isinstance(result, dict) or result.get("engine") != "crawl4ai"
                        or result.get("internal_model_calls") != 0):
                    raise ValueError("Unexpected Crawl4AI evidence result")
                self.extractions[digest] = result
                if len(self.extractions) > 32:
                    self.extractions.popitem(last=False)
            status.update(status="used", version=result.get("version"), input_truncated=truncated)
            status.pop("error_type", None)
            text = json.dumps({**result, "input_hash": digest, "input_truncated": truncated})
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            # Retain an explicit failure instead of silently claiming this source was used.
            status.update(status="unavailable", error_type=type(exc).__name__)
            text = json.dumps({"engine": "crawl4ai", "status": "unavailable", "error_type": type(exc).__name__})
        finally:
            status["duration_ms"] = round(status["duration_ms"] + (time.perf_counter() - started) * 1000, 2)
        key = url + "#crawl4ai"
        self.seen.discard(key)
        self.add(key, "crawl4ai", text)

    def scrub(self, text):
        for headers in self.session.recorder.headers.values():
            self.secret_values.update(headers.values())
        self.secret_values.update(self.session.recorder.secret_values)
        for secret in self.secret_values:
            if len(secret) > 8:
                text = text.replace(secret, "[session secret]")
        return text

    def ensure_portable(self, family):
        encoded = json.dumps(family.model_dump())
        if self.scrub(encoded) != encoded:
            raise ValueError("A captured login value appeared in the program; use a runtime session binding")

    def scrubbed(self, html):
        """The parsed document plus a display copy with secrets masked and ornaments removed."""
        soup = BeautifulSoup(html, "html.parser")
        for element in soup.select("input[name],input[id],meta[name]"):
            if SECRET.search(element.get("name", "") + element.get("id", "")):
                for key in ("value", "content"):
                    if element.has_attr(key):
                        element[key] = "[session secret]"
        dom = BeautifulSoup(str(soup), "html.parser")
        for ornament in dom.select("script,style,svg,link,base"):
            ornament.decompose()
        return soup, str(dom)

    def recent_network(self, limit=50):
        """The latest traffic plus every pinned write, which otherwise sits before the window the prompt sees."""
        recorder = self.session.recorder
        recent = recorder.events[-limit:]
        seen = {e["id"] for e in recent}
        pinned = [e for e in recorder.events if recorder.pinned(e) and e["id"] not in seen]
        return pinned + recent

    async def backfill_enrichment(self):
        """Enrich page and frame documents that no pass ever extracted.

        `enrich_html` only runs against the live page, so a resumed run carries forward the very
        documents an operation depends on, a creation form or a record page, unextracted. Their text
        is already on disk, so this needs no navigation, network or model call.
        """
        if not self.crawl4ai_python:
            return
        stale = [key for key, source in zip(self.keys, self.sources, strict=True)
                 if source.get("kind") in ("page", "frame") and key + "#crawl4ai" not in self.keys]
        for key in stale:
            text = self.documents.get(key)
            if text:
                await self.enrich_html(key, text)

    async def collect_frames(self, page):
        """Same-origin iframe documents: page.content() omits them, so a nested form is otherwise invisible."""
        seen = 0
        for index, frame in enumerate(page.frames):
            if frame.parent_frame is None or seen >= 6:
                continue
            try:
                html = await frame.content()
                element = await frame.frame_element()
                attributes = await element.evaluate(
                    "e => ({id: e.id || '', name: e.getAttribute('name') || '', src: e.getAttribute('src') || ''})")
                await element.dispose()
            except Exception:
                # Cross-origin or detached frames cannot be read; omit rather than report an empty document.
                continue
            selector = frame_selector(attributes)
            url = frame.url if frame.url.startswith("http") else f"{page.url}#frame-{index}"
            _, dom = self.scrubbed(html)
            self.add(url, "frame", f"<!-- locator frame selector: {selector} -->\n{dom}")
            await self.enrich_html(url, dom)
            seen += 1

    def add(self, url, kind, text):
        if url in self.seen:
            return
        self.seen.add(url)
        text = self.scrub(text)
        self.documents[url] = text
        location = re.search(r"#inline-\d+$", url)
        # Crawl4AI Markdown is prose; the excerpts() windowing is tuned for JS/network source and would
        # fragment it, so keep it whole (capped) and let prompt_evidence protect the coherent prefix.
        content = text[:12000] if kind == "crawl4ai" else excerpts(text)
        entry = {"url": safe_url(url) + (location[0] if location else ""), "kind": kind,
                 "content": redact(content), "hash": hashlib.sha256(text.encode()).hexdigest()}
        if url in self.keys:
            index = self.keys.index(url)
            self.sources[index] = {"id": self.sources[index]["id"], **entry}
        else:
            self.keys.append(url)
            self.sources.append({"id": f"s{len(self.sources) + 1}", **entry})

    async def collect(self):
        await self.session.start()
        page = self.session.page
        self.secret_values.update(c["value"] for c in await self.session.context.cookies() if len(c["value"]) > 8)
        storage_secrets = await page.evaluate("""pattern => {
          const secret=new RegExp(pattern,'i'), values=[], bindings=[];let visited=0;
          function collect(key,value,path=[],depth=0){
            if(depth>8||++visited>2000||bindings.length>=100)return;
            if(typeof value==='string' && secret.test(path.at(-1)||key)){
              values.push(value);bindings.push({kind:'storage',key,...(path.length?{path}:{})});
            }
            if(value && typeof value==='object')for(const [name,child] of Object.entries(value))
              collect(key,child,[...path,name],depth+1);
          }
          for(const [key,raw] of Object.entries(localStorage).slice(0,100)){
            let value=raw;try{value=JSON.parse(raw)}catch{}
            collect(key,value);
          }
          return {values,bindings};
        }""", SECRET.pattern)
        self.secret_values.update(storage_secrets["values"])
        global_secrets = await page.evaluate("""pattern => {
          const secret=new RegExp(pattern,'i'), values=[], bindings=[];let visited=0;
          const probe=document.createElement('iframe');probe.style.display='none';
          document.documentElement.appendChild(probe);
          const builtin=new Set(Object.getOwnPropertyNames(probe.contentWindow));probe.remove();
          function collect(value,path,depth){
            if(depth>3||++visited>3000||bindings.length>=50||value==null)return;
            if(typeof value==='string'){
              if(value.length>=8&&secret.test(path.at(-1))){values.push(value);bindings.push({kind:'global',key:path.join('.')});}
              return;
            }
            if(typeof value==='object'&&!(value instanceof Node)){
              let entries=[];try{entries=Object.entries(value).slice(0,80)}catch{return}
              for(const [name,child] of entries)collect(child,[...path,name],depth+1);
            }
          }
          for(const name of Object.getOwnPropertyNames(window)){
            if(builtin.has(name))continue;
            let value;try{value=window[name]}catch{continue}
            if(value&&typeof value==='object')collect(value,[name],1);
          }
          return {values,bindings};
        }""", SECRET.pattern)
        self.secret_values.update(global_secrets["values"])
        soup, dom = self.scrubbed(await page.content())
        self.add(page.url, "page", dom)
        await self.enrich_html(page.url, dom)
        await self.collect_frames(page)
        for index, script in enumerate(soup.select("script:not([src])")[:8]):
            self.add(page.url + f"#inline-{index}", "javascript", script.get_text())
        scripts = [urljoin(page.url, s["src"]) for s in soup.select("script[src]")[:8]]
        for url in scripts:
            if url in self.seen or re.search(r"analytics|telemetry|tracking", url, re.I):
                continue
            try:
                # Fetch scripts that the selected website actually references. No credentials on public lookups.
                result = await fetch(NetworkPolicy([origin(url)], self.manifest.allow_private), url)
                if result["status"] == 200 and isinstance(result["body"], str):
                    self.add(url, "javascript", result["body"])
                    match = re.search(r"sourceMappingURL=([^\s]+)", result["body"])
                    if match and not match[1].startswith("data:"):
                        await self.public(urljoin(url, match[1]), "source_map")
            except Exception:
                continue
        # Feature detection, not an assumption that every website has WebMCP.
        try:
            declared = await asyncio.wait_for(page.evaluate("""async () => {
              const t=window.document?.modelContextTesting || navigator.modelContextTesting;
              if(t && typeof t.listTools==='function')return await t.listTools();
              return [];
            }"""), timeout=3)
        except Exception:
            declared = []
        if declared:
            self.add(page.url + "#declared-tools", "webmcp", json.dumps(declared))
        await self.session.recorder.flush()
        storage = await page.evaluate("""pattern => Object.entries(localStorage)
          .filter(([key])=>!new RegExp(pattern,'i').test(key)).slice(0,15)
          .map(([key,value])=>{try{return {key,value:JSON.parse(value)}}catch{return {key,value:value.slice(0,500)}}})""",
          SECRET.pattern)
        storage = json.loads(self.scrub(json.dumps(redact(storage))))
        try:
            accessibility = await page.locator("body").aria_snapshot(timeout=5000)
        except Exception:
            accessibility = "Unavailable for this page"
        await self.backfill_enrichment()
        return {"page": await self.session.snapshot(), "sources": self.sources,
                "evidence_processors": self.processors,
                "accessibility": self.scrub(accessibility[:16000]),
                "session_bindings": storage_secrets["bindings"] + global_secrets["bindings"],
                "network": self.recent_network(),
                "local_storage": storage,
                "relationships": self.session.recorder.relationships(),
                "browser": {"file_access": self.manifest.file_access,
                            "native_file_picker": await page.evaluate("'showOpenFilePicker' in window")}}

    async def public(self, url, kind="documentation", focus=None):
        if url in self.seen:
            source = next((s for s in self.sources if s["url"] == safe_url(url)), None)
            if source and focus:
                raw = self.documents.get(url, "")
                hits = [m.start() for m in re.finditer(re.escape(focus[:100]), raw, re.I)][:8]
                if hits:
                    source["content"] = redact("\n…\n".join(raw[max(0, i-500):i+2000] for i in hits))
                    return {"status": "focused", "source": source["id"], "matches": len(hits),
                            "excerpt": source["content"][:4000]}
                return {"status": "no_matches", "focus": focus,
                        "hint": "focus must be an exact literal substring from the source, not a description or instruction"}
            return {"status": "already_available", "source": source["id"] if source else None}
        if len(self.sources) >= 30:
            return {"status": "source_limit", "hint": "Inspect existing sources with focus, or explore the application"}
        # Remote documentation never receives login state. Private addresses only for the selected site.
        private = self.manifest.allow_private and origin(url) in self.manifest.allowed_origins
        result = await fetch(NetworkPolicy([origin(url)], private), url)
        if result["status"] != 200:
            return {"status": "unavailable", "http_status": result["status"]}
        body = result["body"]
        if isinstance(body, dict):
            if kind == "source_map":
                body = "\n".join(x for x in body.get("sourcesContent", [])[:10] if isinstance(x, str))
            else:
                body = json.dumps(body)
        if isinstance(body, str):
            if "html" in result["content_type"]:
                await self.enrich_html(url, body)
                body = BeautifulSoup(body, "html.parser").get_text(" ", strip=True)
            self.add(url, kind, body)
            return {"status": "added", "source": self.sources[-1]["id"]}
        return {"status": "unsupported_content"}

    def provenance(self):
        return [{k: v for k, v in item.items() if k != "content"} for item in self.sources]
