from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

from bs4 import BeautifulSoup

from .agent import add_task_tools
from .browser import BrowserSession, browser_tools
from .models import BuildRequest, Manifest, Tool, normalize_url, origin, schema, slug, website_origins
from .net import NetworkPolicy, fetch
from .openapi import compile_spec, parse_spec

UNSAFE_LINK = re.compile(
    r"logout|log-out|signout|sign-out|delete|remove|unsubscribe|checkout|purchase|destroy", re.I
)


async def discover(request: BuildRequest, home: Path, progress=None) -> Manifest:
    async def update(message):
        if progress:
            await progress(message)

    if request.kind == "desktop":
        from .desktop import discover_desktop

        await update("Finding the Windows app and its accessible controls")
        manifest = await discover_desktop(request)
        add_task_tools(manifest, request.tasks)
        return manifest
    target = normalize_url(request.target)
    allowed = sorted({*website_origins(target), *(origin(x) for x in request.allowed_origins)})
    manifest = Manifest(
        id=f"{slug(request.name or urlsplit(target).hostname)}-{uuid4().hex[:8]}",
        name=request.name or urlsplit(target).hostname,
        kind="website",
        target=target,
        allowed_origins=allowed,
        allow_private=request.allow_private,
        notes=request.notes,
        tools=browser_tools(),
    )
    await NetworkPolicy(allowed, request.allow_private).check(target)
    pages, spec_candidates = [], []
    if request.browser:
        await update("Opening the website and discovering its interface; no API specification needed")
        session = BrowserSession(manifest, home)
        try:
            await session.start()
            queue, visited = [target], set()
            while queue and len(visited) < request.max_pages:
                url = queue.pop(0)
                if url in visited:
                    continue
                visited.add(url)
                try:
                    response = await session.page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    await session.page.wait_for_timeout(250)
                    page = {
                        "url": session.page.url,
                        "title": await session.page.title(),
                        "status": response.status if response else None,
                        "html": await session.page.content(),
                    }
                    pages.append(page)
                    soup = BeautifulSoup(page["html"], "html.parser")
                    canvas_count = len(soup.select("canvas"))
                    manifest.findings.append(
                        {
                            "type": "page",
                            "url": page["url"],
                            "title": page["title"],
                            "status": page["status"],
                            "canvases": canvas_count,
                            "forms": len(soup.select("form")),
                        }
                    )
                    if len(pages) == 1 and page["title"] and not request.name:
                        manifest.name = page["title"][:100]
                    if soup.select_one('input[type="password"]') or page["status"] in (401, 403):
                        manifest.warnings.append(
                            "This page may require login. Open a login session, sign in, then refresh discovery."
                        )
                    for link in soup.select("a[href],link[href]"):
                        href = urljoin(page["url"], link.get("href", ""))
                        if not href.startswith(("https://", "http://")):
                            continue
                        if origin(href) not in allowed:
                            continue
                        if re.search(r"(?:openapi|swagger).*\.(?:json|yaml|yml)", href, re.I):
                            spec_candidates.append(href)
                        parts = urlsplit(href)
                        if (
                            link.name == "a"
                            and not parts.query
                            and not parts.fragment
                            and not UNSAFE_LINK.search(href)
                        ):
                            if href not in visited and href not in queue and len(queue) < 15:
                                queue.append(href)
                    await update(
                        f"Discovered {len(pages)} page(s), including {canvas_count} canvas surface(s)"
                    )
                except Exception as exc:
                    manifest.warnings.append(
                        f"Could not inspect {url}: {type(exc).__name__}. Browser tools remain available."
                    )
        except Exception as exc:
            manifest.warnings.append(f"Browser discovery unavailable: {str(exc)[:400]}")
        finally:
            await session.close()
    if not pages:
        await update("Checking the page over HTTP")
        try:
            result = await fetch(NetworkPolicy(allowed, request.allow_private), target)
            direct = parse_spec(result["body"])
            if direct:
                spec_candidates.insert(0, target)
            elif isinstance(result["body"], str):
                soup = BeautifulSoup(result["body"], "html.parser")
                pages.append(
                    {
                        "url": result["url"],
                        "html": result["body"],
                        "status": result["status"],
                        "title": soup.title.get_text() if soup.title else manifest.name,
                    }
                )
                manifest.findings.append(
                    {"type": "http_page", "url": result["url"], "status": result["status"]}
                )
        except Exception as exc:
            manifest.warnings.append(
                f"HTTP discovery unavailable: {type(exc).__name__}. Login or browser access may be needed."
            )
    add_forms(manifest, pages)
    await update("Adding browser actions, drawing, erasing, and any optional API tools")
    if request.spec_url:
        spec_candidates.insert(0, normalize_url(request.spec_url))
    # Conventional public descriptions supplement UI discovery; their absence is normal.
    spec_candidates += [urljoin(target, "/openapi.json"), urljoin(target, "/swagger.json")]
    for candidate in list(dict.fromkeys(spec_candidates))[:5]:
        try:
            result = await fetch(NetworkPolicy(allowed, request.allow_private), candidate)
            doc = parse_spec(result["body"]) if result["status"] == 200 else None
            if doc:
                api_tools, warnings = compile_spec(doc, candidate, allowed)
                manifest.tools.extend(api_tools)
                manifest.warnings.extend(warnings)
                manifest.findings.append({"type": "openapi", "url": candidate, "operations": len(api_tools)})
                break
            if candidate == request.spec_url:
                manifest.warnings.append(
                    "The supplied URL did not return a supported OpenAPI 3 JSON/YAML description."
                )
        except Exception as exc:
            if request.spec_url and candidate == normalize_url(request.spec_url):
                manifest.warnings.append(f"Could not use supplied API description: {str(exc)[:300]}")
    if not any(f["type"] == "openapi" for f in manifest.findings):
        manifest.findings.append(
            {
                "type": "strategy",
                "message": "No API specification found. This connector operates the website interface, including canvas pointer and keyboard actions.",
            }
        )
    add_task_tools(manifest, request.tasks)
    return manifest


def add_forms(manifest: Manifest, pages: list[dict]):
    existing = {t.name for t in manifest.tools}
    for page in pages:
        soup = BeautifulSoup(page["html"], "html.parser")
        for index, form in enumerate(soup.select("form")[:12]):
            action = urljoin(page["url"], form.get("action") or page["url"])
            if origin(action) not in manifest.allowed_origins:
                continue
            controls = form.select("input,textarea,select,button")
            fields, properties, required = [], {}, []
            names = [c.get("name") for c in controls if c.get("name")]
            # Password/login and complex multi-value forms stay accessible through generic UI tools.
            if form.select_one(
                'input[type="password"],input[type="file"],input[type="checkbox"],input[type="radio"]'
            ):
                continue
            for element in controls:
                name = element.get("name")
                if (
                    not name
                    or names.count(name) != 1
                    or element.name == "button"
                    or element.get("type") in ("hidden", "submit", "reset", "button")
                ):
                    continue
                label = element.get("aria-label") or element.get("placeholder") or name
                properties[name] = {"type": "string", "description": label, "maxLength": 20000}
                if element.name == "select":
                    properties[name]["enum"] = [
                        o.get("value", o.get_text()) for o in element.select("option")
                    ]
                if element.has_attr("required"):
                    required.append(name)
                fields.append({"key": name, "name": name, "tag": element.name})
            if not fields:
                continue
            label = form.get("aria-label") or form.get("id") or f"form {index + 1} on {page['title']}"
            name = "form_" + slug(label, 44).replace("-", "_")
            if name in existing:
                name += f"_{len(existing)}"
            existing.add(name)
            manifest.tools.append(
                Tool(
                    name=name,
                    description=f"Fill and submit {label} through the website UI. This may change data.",
                    input_schema=schema(properties, required),
                    adapter="browser",
                    operation="form",
                    evidence=f"Observed form {index + 1} at {page['url']}",
                    config={
                        "page": page["url"],
                        "index": index,
                        "action": action,
                        "method": form.get("method", "get").lower(),
                        "field_names": names,
                        "fields": fields,
                    },
                )
            )


async def refresh_forms(manifest: Manifest, session: BrowserSession):
    await session.start()
    manifest.tools = [t for t in manifest.tools if t.operation != "form"]
    page = {
        "url": session.page.url,
        "title": await session.page.title(),
        "html": await session.page.content(),
    }
    add_forms(manifest, [page])
    manifest.findings.append({"type": "session_refresh", "url": page["url"], "title": page["title"]})
    manifest.warnings = [w for w in manifest.warnings if "may require login" not in w]
