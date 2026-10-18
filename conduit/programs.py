"""Bounded website programs, implemented as steps or isolated JavaScript."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import time
from typing import Any, Literal
from urllib.parse import quote, urlencode, urljoin

from playwright.async_api import Error as BrowserError
from pydantic import Field, model_validator

from .models import StrictModel, origin
from .net import NetworkPolicy

NAVIGATION_KEYS = {"Tab", "Shift+Tab", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"}
TRANSIENT_NAVIGATION = ("ERR_ABORTED", "ERR_NETWORK_CHANGED", "ERR_CONNECTION_RESET", "frame was detached")

GLOBAL_LOOKUP = """path => {
  const value = path.replace(/^window\\./, '').split('.').reduce((o, k) => (o == null ? undefined : o[k]), window);
  return value === undefined || typeof value === 'function' ? null : value;
}"""


class Step(StrictModel):
    kind: Literal["request", "navigate", "click", "fill", "press", "read", "select", "command",
                  "download", "upload", "draw", "set", "assert", "if", "poll"]
    name: str = Field(default="", pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$|^$")
    url: Any = None
    method: Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    transport: Literal["http", "page"] = "http"
    encoding: Literal["json", "form"] = "json"
    effect: Literal["auto", "read", "write"] = "auto"
    read_evidence: list[str] = Field(default_factory=list, max_length=10)
    response_check: dict | None = None
    body: Any = None
    headers: dict[str, Any] = Field(default_factory=dict)
    accept: list[int] = Field(default_factory=lambda: [200, 201, 202, 204])
    locator: dict[str, Any] = Field(default_factory=dict)
    value: Any = None
    attribute: str = "text"
    function: str = ""
    arguments: Any = Field(default_factory=list)
    steps: list["Step"] = Field(default_factory=list)
    otherwise: list["Step"] = Field(default_factory=list)
    attempts: int = Field(default=4, ge=1, le=10)
    interval_ms: int = Field(default=200, ge=0, le=2000)
    expected_document: Any = None
    export_locator: dict[str, Any] = Field(default_factory=dict)
    verify_program: dict[str, Any] | None = None
    chooser_steps: list[dict[str, Any]] = Field(default_factory=list, max_length=4)
    points: Any = Field(default_factory=list)
    modifiers: list[str] = Field(default_factory=list)


class Program(StrictModel):
    steps: list[Step] = Field(default_factory=list, max_length=40)
    result: Any = None
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    javascript: str | None = Field(default=None, min_length=1, max_length=64000)
    request_origins: list[str] = Field(default_factory=list, max_length=10)
    read_evidence: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def one_implementation(self):
        if self.javascript is not None and (self.steps or self.result is not None):
            raise ValueError("Choose JavaScript or steps for a program, not both")
        return self


class Operation(StrictModel):
    input_schema: dict = Field(default_factory=lambda: {"type": "object", "properties": {}})
    program: Program
    description: str = ""


class ManagedField(StrictModel):
    kind: Literal["timestamp_ms", "revision_nonce"]
    evidence: list[str] = Field(min_length=1, max_length=10)


class Family(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,35}$")
    label: str
    mode: Literal["direct", "browser", "document", "application"] = "direct"
    operations: dict[str, Operation]
    create_input: dict
    update_input: dict
    id_path: str = "id"
    item_path: str = ""
    fields: dict[str, str] = Field(default_factory=dict)
    # Optional stable revision returned by read, to detect concurrent changes before mutation.
    revision_path: str | None = None
    managed_fields: dict[str, ManagedField] = Field(default_factory=dict, max_length=12)
    evidence: list[str] = Field(default_factory=list)
    notes: str = ""
    persistence: Program | None = None
    document_inspection: Program | None = None


class ProgramError(ValueError):
    def __init__(self, code, message, *, uncertain=False):
        self.code, self.uncertain = code, uncertain
        super().__init__(message)


def at(value, path, default=...):
    try:
        for part in path.split(".") if path else []:
            value = value[int(part)] if isinstance(value, list) else value[part]
        return value
    except (KeyError, IndexError, TypeError, ValueError):
        if default is not ...:
            return default
        raise ProgramError("binding_missing", f"The operation could not resolve field {path}.") from None


def frame_chain(spec):
    """CSS selectors for the iframes a locator sits inside, outermost first."""
    frame = spec.get("frame")
    if frame in (None, "", []):
        return []
    chain = frame if isinstance(frame, list) else [frame]
    if len(chain) > 3:
        raise ValueError("A frame chain may nest at most three iframes deep")
    for selector in chain:
        if not isinstance(selector, str) or not selector.strip():
            raise ValueError("Each frame entry must be a CSS selector matching an iframe")
    return chain


def walk(steps):
    for step in steps:
        if step.kind == "request" and step.effect == "read" and step.method not in ("GET", "HEAD") and not step.read_evidence:
            raise ValueError("A read-only RPC request needs explicit operation evidence")
        yield step
        yield from walk(step.steps)
        yield from walk(step.otherwise)
        if step.verify_program:
            yield from walk(Program.model_validate(step.verify_program).steps)


SLEEP_CALL = re.compile(r"\bapi\s*\.\s*sleep\s*\(\s*(\d+)\s*\)")
READ_EFFECT = re.compile(r"""effect\s*:\s*['"]read['"]""")
MUTATING_METHOD = re.compile(r"""method\s*:\s*['"](POST|PUT|PATCH|DELETE)['"]""", re.I)


def check_runtime_contract(program: Program):
    """Reject what the worker would reject, before the program is allowed to write anything.

    These rules were only enforced once a call reached the session bridge, so a candidate broke them
    midway through its lifecycle, after the create had already landed. The violation is visible in the
    source, so catching it here turns a run that must be cleaned up by hand into immediate feedback.
    """
    source = program.javascript
    if not source:
        return
    for literal in SLEEP_CALL.findall(source):
        if int(literal) > 2000:
            raise ValueError(f"api.sleep({literal}) exceeds the 2000 ms limit the worker enforces; "
                             "poll in shorter waits and raise Program.timeout_seconds instead")
    if READ_EFFECT.search(source) and MUTATING_METHOD.search(source) and not program.read_evidence:
        raise ValueError("A request declared effect:'read' with a mutating method, so the Program must list the "
                         "read_evidence IDs proving that endpoint only reads")


def validate_program(program: Program, *, read_only=False):
    check_runtime_contract(program)
    steps = list(walk(program.steps))
    if len(steps) > 80:
        raise ValueError("Program exceeds the 80-step limit")
    for step in steps:
        if step.kind == "poll" and any(
            s.kind not in ("request", "read", "download", "set", "assert") or
            (s.kind == "request" and not read_request(s)) for s in walk(step.steps)
        ):
            raise ValueError("Polling may only repeat reads; a mutation is never retried")
        if read_only and (step.kind in ("upload", "fill", "select", "draw") or
                          (step.kind == "request" and not read_request(step))):
            raise ValueError("A read operation contains a mutation")
        if step.kind == "command":
            if not re.fullmatch(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*){0,6}", step.function):
                raise ValueError("Application commands must be callable property paths, not source code")
            if any(x in step.function.split(".") for x in ("constructor", "__proto__", "prototype", "eval", "Function")):
                raise ValueError("Invalid application command")
        for key, value in step.headers.items():
            from .recorder import SECRET
            if SECRET.search(key) and not isinstance(value, dict):
                raise ValueError("Credentials must use session bindings, never literal header values")


def read_request(step):
    if step.effect == "write":
        return False
    if step.effect == "read" and step.read_evidence:
        return True
    if step.method in ("GET", "HEAD"):
        return True
    # GraphQL queries often use HTTP POST. Only a static query can qualify as a read.
    query = step.body.get("query") if isinstance(step.body, dict) else None
    if step.method != "POST" or not isinstance(query, str):
        return False
    tokens = re.sub(r'"""[\s\S]*?"""|"(?:\\.|[^"\\])*"|#[^\n]*', '', query).strip()
    return (tokens.startswith("{") or re.match(r"^query\b", tokens) is not None) and not re.search(
        r"\b(?:mutation|subscription)\b", tokens)


class ProgramRunner:
    def __init__(self, session, manifest):
        self.session, self.manifest = session, manifest
        self.vars, self.input = {}, {}
        self.context = {}
        self.mutated = False
        self.requests = 0
        self.steps_run = 0
        self.current_step = "start"
        self.current_locator = {}
        self.secret_values = set()
        self.observations = []

    async def resolve(self, value):
        if isinstance(value, list):
            return [await self.resolve(x) for x in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            return copy.deepcopy(at({"input": self.input, "vars": self.vars, "context": self.context}, value["$ref"],
                                    value.get("default", ...)))
        if "$concat" in value:
            return "".join([str(await self.resolve(x)) for x in value["$concat"]])
        if "$encode" in value:
            return quote(str(await self.resolve(value["$encode"])), safe="")
        if "$json" in value:
            return json.dumps(await self.resolve(value["$json"]), ensure_ascii=False)
        if "$parse" in value:
            return json.loads(await self.resolve(value["$parse"]))
        if "$uuid" in value:
            from uuid import uuid4
            return uuid4().hex
        if "$now" in value:
            return int(time.time() * 1000)
        if "$assign" in value:
            query = value["$assign"]
            result = copy.deepcopy(await self.resolve(query["object"]))
            fields = await self.resolve(query["values"])
            missing = object()
            for source, destination in query["paths"].items():
                field = at(fields, source, missing)
                if field is missing:
                    continue
                parent = result
                parts = destination.split(".")
                for part in parts[:-1]:
                    parent = parent.setdefault(part, {})
                parent[parts[-1]] = copy.deepcopy(field)
            return result
        if "$patch" in value:
            q = value["$patch"]
            document = copy.deepcopy(await self.resolve(q["document"]))
            collection = at(document, q.get("path", "elements"))
            if not isinstance(collection, list):
                raise ValueError("A document patch needs an array of objects")
            key = await self.resolve(q.get("id"))
            id_path = q.get("id_path", "id")
            indices = [i for i, x in enumerate(collection) if at(x, id_path, None) == key]
            if q["operation"] == "create":
                item = await self.resolve(q["value"])
                if any(at(x, id_path) == at(item, id_path) for x in collection):
                    raise ProgramError("conflict", "The document already contains that object")
                collection.append(item)
            else:
                if len(indices) != 1:
                    raise ProgramError("object_missing", "The document object is missing or ambiguous")
                if q["operation"] == "delete":
                    collection.pop(indices[0])
                elif q["operation"] == "update":
                    collection[indices[0]].update(await self.resolve(q["value"]))
                else:
                    raise ValueError("Unknown document patch")
            return document
        if "$find" in value:
            q = value["$find"]
            items, expected = await self.resolve(q["items"]), await self.resolve(q["equals"])
            matches = [x for x in items if at(x, q["path"], None) == expected]
            if len(matches) > 1:
                raise ProgramError("ambiguous_object", "More than one object matched the requested identifier")
            return matches[0] if matches else None
        if "$merge" in value:
            result = {}
            for item in value["$merge"]:
                result.update(await self.resolve(item))
            return result
        if "$equal" in value:
            a, b = await self.resolve(value["$equal"])
            return a == b
        if "$storage" in value:
            query = value["$storage"]
            stored = await self.session.page.evaluate("key => localStorage.getItem(key)", query["key"])
            if stored is None:
                return query.get("default")
            return json.loads(stored) if query.get("parse", True) else stored
        if "$global" in value:
            from .recorder import SECRET

            q = value["$global"]
            if SECRET.search(str(q["key"]).rsplit(".", 1)[-1]):
                raise ValueError("Secret-looking page globals must use a $session global binding")
            found = await self.session.page.evaluate(GLOBAL_LOOKUP, q["key"])
            if found is None:
                if "default" in q:
                    return q["default"]
                raise ProgramError("adapter_call_failed", f"Page global {q['key']} is not present on the current page")
            return found
        if "$session" in value:
            q = value["$session"]
            kind, key = q["kind"], q["key"]
            if kind == "cookie":
                cookies = await self.session.context.cookies(q.get("url", self.manifest.target))
                found = next((c["value"] for c in cookies if c["name"] == key), None)
            elif kind == "header":
                host = q.get("origin", origin(self.manifest.target))
                found = await self.session.recorder.session_header(host, key)
            elif kind == "storage":
                found = await self.session.page.evaluate("key => localStorage.getItem(key)", key)
                if found and q.get("path"):
                    try:
                        found = json.loads(found)
                        path = await self.resolve(q["path"])
                        for part in path.split(".") if isinstance(path, str) else path:
                            found = found[int(part)] if isinstance(found, list) else found[str(part)]
                    except (ValueError, TypeError, KeyError, IndexError):
                        found = None
            elif kind == "meta":
                found = await self.session.page.locator(q.get("selector", f'meta[name="{key}"]')).get_attribute("content")
            elif kind == "global":
                # Anti-forgery tokens that only exist as page JavaScript globals are read at call time,
                # never frozen into the program.
                found = await self.session.page.evaluate(GLOBAL_LOOKUP, key)
                if not isinstance(found, str):
                    found = None
            else:
                raise ValueError("Unsupported session binding")
            if not isinstance(found, str) or not found:
                raise ProgramError("authentication_required", "A fresh login or authentication value is needed")
            self.secret_values.add(found)
            self.session.recorder.secret_values.add(found)
            return q.get("prefix", "") + found
        return {k: await self.resolve(v) for k, v in value.items()}

    async def locator(self, spec):
        q = await self.resolve(spec)
        page = self.session.page
        for selector in frame_chain(q):
            page = page.frame_locator(selector)
        if "role" in q:
            loc = page.get_by_role(q["role"], name=q.get("name"), exact=q.get("exact", True))
        elif "label" in q:
            loc = page.get_by_label(q["label"], exact=True)
        elif "testid" in q:
            loc = page.get_by_test_id(q["testid"])
        elif "text" in q:
            loc = page.get_by_text(q["text"], exact=True)
        elif "css" in q:
            loc = page.locator(q["css"])
        else:
            raise ValueError("A stable locator is required")
        if "within" in q:
            loc = (await self.locator(q["within"])).locator(loc)
        if "index" in q:
            loc = loc.nth(q["index"])
        return loc

    async def navigate(self, url, attempts=3):
        """Opening a page is a read, so a transient abort is retried; a write is never repeated here.

        A single-page app redirecting or replacing the document while a navigation is in flight raises
        ERR_ABORTED. That ended a lifecycle cycle mid-run even though nothing had gone wrong with the
        operation under test.
        """
        for attempt in range(attempts):
            try:
                await self.session.page.goto(url, wait_until="domcontentloaded", timeout=25000)
                return
            except BrowserError as exc:
                transient = any(code in str(exc) for code in TRANSIENT_NAVIGATION)
                if not transient or attempt == attempts - 1:
                    raise
                self.observations.append({"operation": "navigation_retry", "attempt": attempt + 1})
                self.observations = self.observations[-8:]
                await asyncio.sleep(0.4 * (attempt + 1))

    async def settle(self, timeout_ms=8000):
        """Give a single-page app time to hydrate after navigation; a plain page passes through at once.

        domcontentloaded fires long before a heavy frontend has rendered its controls, so the very next
        click used to wait out the whole action timeout on a button that did not exist yet.
        """
        try:
            await self.session.page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except BrowserError:
            # Pages with polling or sockets never go idle; the bounded wait is all they get.
            pass

    async def settle_after_action(self, frames_before, timeout_ms=4000):
        """Let a click finish what it started before the next step looks at the page.

        A create panel on a heavy frontend is often an iframe injected a moment after the click; a
        count read or snapshot that runs at once sees the old page and reports that no form appeared.
        Only exploration pays this wait. Verified programs keep Playwright's per-locator auto-wait.
        """
        page = self.session.page
        deadline = time.monotonic() + timeout_ms / 1000
        await self.settle(timeout_ms=min(timeout_ms, 2500))
        while time.monotonic() < deadline:
            if len(page.frames) > frames_before:
                for frame in page.frames[frames_before:]:
                    try:
                        await frame.wait_for_load_state("domcontentloaded", timeout=max(1, int((deadline - time.monotonic()) * 1000)))
                    except BrowserError:
                        pass
                return
            await asyncio.sleep(0.25)

    async def ensure_present(self, loc, spec):
        """Fail fast when nothing matches, and say what does, instead of waiting out the action timeout.

        Playwright's auto-wait cannot tell "no such element" from "not ready yet", so a wrong role or a
        control living inside an iframe the locator never named looked identical to a slow page.
        """
        try:
            await loc.first.wait_for(state="attached", timeout=3000)
            return
        except BrowserError:
            pass
        if await loc.count():
            return
        from .recorder import redact
        hints = await self.locator_hints(spec)
        raise ProgramError("locator_missing",
                           f"No element matches {json.dumps(redact(spec))} in the current document"
                           + (f"; {hints}" if hints else ""))

    async def locator_hints(self, spec):
        """Where the named text actually is, so the next locator can be corrected rather than guessed."""
        from .browser import frame_selector
        needle = next((str(spec[k]) for k in ("name", "text", "label", "testid") if spec.get(k)), None)
        hints = []
        for frame in self.session.page.frames:
            try:
                if frame.parent_frame is not None:
                    element = await frame.frame_element()
                    attributes = await element.evaluate(
                        "e => ({id: e.id || '', name: e.getAttribute('name') || '', src: e.getAttribute('src') || ''})")
                    await element.dispose()
                    where = f'inside iframe {frame_selector(attributes)} (add "frame" to the locator)'
                else:
                    where = "in the main document"
                if not needle:
                    # Only frames that hold form controls are worth naming; chat widgets and
                    # notification banners are iframes too and would drown the useful one.
                    if frame.parent_frame is not None and await frame.locator("input,textarea,select").count():
                        hints.append(f"iframe with form controls present: {frame_selector(attributes)}")
                    continue
                found = frame.get_by_text(needle, exact=False)
                if not await found.count():
                    found = frame.get_by_label(needle, exact=False)
                count = await found.count()
                if not count:
                    continue
                tags = await found.evaluate_all(
                    "els => els.slice(0, 3).map(e => e.tagName.toLowerCase()"
                    " + (e.getAttribute('role') ? '[role=' + e.getAttribute('role') + ']' : ''))")
                hints.append(f"{count} element(s) matching {needle!r} {where}: {', '.join(tags)}")
            except BrowserError:
                continue
        return "; ".join(hints[:4])

    async def request(self, step):
        for attempt in range(5):
            try:
                return await self._request_once(step)
            except ProgramError as exc:
                deadline = getattr(self.session, "auth_restore_deadline", 0.0)
                delay = min(0.25 * 2**attempt, 1.0)
                if (exc.code != "authentication_required" or exc.uncertain or self.mutated
                        or not read_request(step) or attempt == 4 or time.monotonic() + delay >= deadline):
                    raise
                self.observations.append({"operation": "startup_auth_read", "attempt": attempt + 1})
                await asyncio.sleep(delay)

    async def _request_once(self, step):
        url = urljoin(self.session.page.url, str(await self.resolve(step.url)))
        await NetworkPolicy(self.manifest.allowed_origins, self.manifest.allow_private).check(url)
        headers, body = await self.resolve(step.headers), await self.resolve(step.body)
        headers = {k.lower(): str(v) for k, v in headers.items() if v is not None}
        payload = body
        if step.encoding == "form" and body is not None:
            if not isinstance(body, dict) or any(isinstance(v, (dict, list)) for v in body.values()):
                raise ValueError("Form fields must be scalar; encode structured fields explicitly with $json")
            payload = urlencode({k: str(v).lower() if isinstance(v, bool) else str(v)
                                 for k, v in body.items() if v is not None})
            content_type = next((v for k, v in headers.items() if k.lower() == "content-type"), None)
            if content_type and not content_type.startswith("application/x-www-form-urlencoded"):
                raise ValueError("Form request has a conflicting Content-Type")
            headers.setdefault("content-type", "application/x-www-form-urlencoded")
        writing = not read_request(step)
        # A computed or JavaScript-built URL is only visible here, so the grounding rule is enforced
        # before the request leaves: an invented write endpoint must never reach a real account.
        ledger = getattr(self.session, "grounding", None)
        if writing and ledger is not None:
            problem = ledger.reject(step.method, url)
            if problem:
                raise ProgramError("ungrounded_write", problem)
        self.mutated |= writing
        self.requests += 1
        try:
            if step.transport == "page":
                result = await self.session.page.evaluate("""async q => {
                  const r=await fetch(q.url,{method:q.method,credentials:'include',redirect:'error',
                    headers:{...(q.body!==null?{'content-type':'application/json'}:{}),...q.headers},
                    body:q.body===null?undefined:(q.encoding==='form'?q.payload:JSON.stringify(q.body)),signal:AbortSignal.timeout(20000)});
                  const t=await r.text(); let body;try{body=JSON.parse(t)}catch{body=t.slice(0,50000)}
                  return {status:r.status,body};
                }""", {"url": url, "method": step.method, "headers": headers, "body": body,
                        "encoding": step.encoding, "payload": payload})
            else:
                response = await self.session.context.request.fetch(
                    url, method=step.method, headers=headers, data=payload if body is not None else None,
                    timeout=20000, max_redirects=0, fail_on_status_code=False,
                )
                try:
                    raw = await response.body()
                    if len(raw) > 4 * 1024 * 1024:
                        raise ProgramError("response_limit", "The response was too large", uncertain=writing)
                    try:
                        data = json.loads(raw) if raw else None
                    except ValueError:
                        data = raw.decode(errors="replace")[:50000]
                    result = {"status": response.status, "body": data}
                finally:
                    await response.dispose()
                self.session.recorder.http(url, step.method, result["status"], body, result["body"], headers)
        except ProgramError:
            raise
        except Exception:
            raise ProgramError("outcome_unknown" if writing else "transport_failed",
                               "The operation's response was lost; no mutation was retried.", uncertain=writing) from None
        if result["status"] in (401, 403, 419):
            raise ProgramError("authentication_required", "The website requires login or does not permit this operation")
        if result["status"] in (409, 412):
            raise ProgramError("conflict", "The object changed. Read its current state before editing it")
        if result["status"] not in step.accept:
            raise ProgramError("unexpected_response", f"Website returned HTTP {result['status']}", uncertain=writing)
        if step.response_check:
            check = step.response_check
            if at(result["body"], check["path"], None) != check.get("equals", True):
                code = at(result["body"], check.get("error_path", "error"), None)
                authentication = isinstance(code, str) and code in check.get("authentication_errors", [])
                label = code if isinstance(code, str) and re.fullmatch(r"[a-zA-Z0-9_]{1,80}", code) else "operation_failed"
                raise ProgramError("authentication_required" if authentication else "application_error",
                    "The website rejected the operation: " + label, uncertain=writing and not authentication)
        return result

    async def choose_file(self, step, payload):
        """Click an observed dialog sequence once, stopping as soon as a picker opens."""
        page = self.session.page
        chosen = asyncio.get_running_loop().create_future()
        def on_chooser(value):
            if not chosen.done():
                chosen.set_result(value)
        page.on("filechooser", on_chooser)
        waiting = None
        try:
            for spec in [step.locator, *step.chooser_steps]:
                if chosen.done():
                    break
                loc = await self.locator(spec)
                waiting = asyncio.create_task(loc.wait_for(state="visible", timeout=8000))
                done, _ = await asyncio.wait((chosen, waiting), return_when=asyncio.FIRST_COMPLETED)
                if chosen in done:
                    break
                await waiting  # Propagate a missing control without clicking a different one.
                waiting = None
                self.current_locator = spec
                await loc.click()
            picker = await asyncio.wait_for(asyncio.shield(chosen), timeout=8)
            self.mutated = True
            await picker.set_files(payload)
        finally:
            if waiting is not None and not waiting.done():
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
            page.remove_listener("filechooser", on_chooser)
            if not chosen.done():
                chosen.cancel()

    async def execute_steps(self, steps):
        for step in steps:
            self.current_step = step.kind
            self.current_locator = step.locator
            self.steps_run += 1
            if self.steps_run > 160:
                raise ProgramError("step_limit", "Program exceeded its execution limit", uncertain=self.mutated)
            result = None
            if step.kind == "request":
                result = await self.request(step)
            elif step.kind == "navigate":
                url = urljoin(self.session.page.url, await self.resolve(step.url))
                await NetworkPolicy(self.manifest.allowed_origins, self.manifest.allow_private).check(url)
                await self.navigate(url)
                await self.settle()
            elif step.kind in ("click", "fill", "press", "select", "read", "download", "upload"):
                loc = await self.locator(step.locator)
                if not (step.kind == "read" and step.attribute == "count"):
                    await self.ensure_present(loc, step.locator)
                if step.kind == "read":
                    if step.attribute == "count":
                        result = await loc.count()
                    elif step.attribute == "json":
                        result = json.loads(await loc.text_content())
                    elif step.attribute == "value":
                        result = await loc.input_value()
                    elif step.attribute == "text":
                        result = await loc.inner_text()
                    else:
                        result = await loc.get_attribute(step.attribute)
                elif step.kind == "download":
                    # Chromium limits bursts of renderer-initiated downloads. Each export is a fresh
                    # application read, so pace requests instead of reusing stale document contents.
                    gap = 0.2 - (time.monotonic() - self.session.last_download)
                    if gap > 0:
                        await asyncio.sleep(gap)
                    self.session.last_download = time.monotonic()
                    async with self.session.page.expect_download() as download:
                        await loc.click()
                    item = await download.value
                    from pathlib import Path
                    path = Path(await item.path())
                    if path.stat().st_size > 4 * 1024 * 1024:
                        raise ValueError("Document exceeds the 4 MB limit")
                    result = json.loads(path.read_text(encoding="utf-8"))
                    await item.delete()
                elif step.kind == "upload":
                    if step.expected_document is not None:
                        if not step.export_locator and not step.verify_program:
                            raise ValueError("Document replacement requires a fresh export for conflict detection")
                        expected = await self.resolve(step.expected_document)
                        if step.verify_program:
                            guard = Program.model_validate(step.verify_program)
                            validate_program(guard, read_only=True)
                            # A guard may use the same local names as the mutation recipe.
                            # Keep its locals private while sharing the bounded execution budget.
                            outer = self.vars
                            self.vars = copy.deepcopy(outer)
                            try:
                                observed = await self.evaluate(guard, read_only=True)
                            finally:
                                self.vars = outer
                        else:
                            check = Step(kind="download", locator=step.export_locator, name="_guard_document")
                            await self.execute_steps([check])
                            observed = self.vars["_guard_document"]
                        if observed != expected:
                            raise ProgramError("conflict", "The document changed while the edit was being prepared")
                    self.mutated = True
                    data = await self.resolve(step.value)
                    self.current_step = "upload"
                    payload = {"name": "conduit-document.json", "mimeType": "application/json",
                               "buffer": json.dumps(data).encode()}
                    if step.attribute == "filechooser":
                        await self.choose_file(step, payload)
                    else:
                        await loc.set_input_files(payload)
                else:
                    # Typing into a form field changes the page, not the account: nothing is committed
                    # until a submit. Treating a fill as a possible mutation made a create that timed
                    # out mid-form look like a write whose outcome was unknown, which blocks every later
                    # compile. A key press can commit (Enter), so it still counts.
                    # Tab, Escape and arrows move focus; only a committing key can write.
                    navigation = step.kind == "press" and str(step.value) in NAVIGATION_KEYS
                    self.mutated = self.mutated or (step.kind in ("click", "press") and not navigation)
                    if step.kind == "click":
                        await loc.click()
                    elif step.kind == "fill":
                        await loc.fill(str(await self.resolve(step.value)))
                    elif step.kind == "select":
                        await loc.select_option(str(await self.resolve(step.value)))
                    else:
                        await loc.press(str(await self.resolve(step.value)))
            elif step.kind == "draw":
                self.mutated = True
                result = await self.session.execute("draw", {"points": await self.resolve(step.points),
                                                           "modifiers": step.modifiers}, {})
            elif step.kind == "command":
                self.mutated = True
                # Only an actually exposed callable can be used. No invented window APIs or eval strings.
                result = await self.session.page.evaluate("""async q => {
                  const keys=q.name.split('.');let obj=window;
                  for(const key of keys.slice(0,-1))obj=obj?.[key];
                  const fn=obj?.[keys.at(-1)];if(typeof fn!=='function')throw Error('Command unavailable');
                  return await fn.apply(obj,q.args);
                }""", {"name": step.function, "args": await self.resolve(step.arguments)})
            elif step.kind == "set":
                result = await self.resolve(step.value)
            elif step.kind == "assert":
                if not await self.resolve(step.value):
                    raise ProgramError("postcondition_failed", "The expected website state was not observed",
                                       uncertain=self.mutated)
            elif step.kind == "if":
                await self.execute_steps(step.steps if await self.resolve(step.value) else step.otherwise)
            elif step.kind == "poll":
                for attempt in range(step.attempts):
                    await self.execute_steps(step.steps)
                    if await self.resolve(step.value):
                        break
                    if attempt + 1 == step.attempts:
                        raise ProgramError("postcondition_failed", "The website did not reach the expected state",
                                           uncertain=self.mutated)
                    await asyncio.sleep(step.interval_ms / 1000)
            if step.name:
                self.vars[step.name] = result

    def describe_failure(self, exc):
        """One line the model can act on, in the same words run() uses."""
        from .recorder import redact
        if isinstance(exc, ProgramError):
            return exc.code, str(exc)
        if isinstance(exc, BrowserError):
            lines = [line.strip() for line in str(exc).splitlines() if line.strip()]
            detail = redact(" | ".join(lines[:7]))[:900]
            locator = " " + json.dumps(redact(self.current_locator)) if self.current_locator else ""
            return "browser_step_failed", f"Browser {self.current_step}{locator} failed: {detail}"
        return "program_failed", f"The {self.current_step} step could not process the website result"

    async def explore(self, program):
        """Run read-only steps best-effort, reporting every outcome instead of stopping at the first miss.

        Each learning pass is one model call, so an exploration that aborts on its first failed step
        teaches one fact per call. Continuing past a miss lets a single pass show the model which
        candidates were ambiguous, what the next step found, and the page state it all left behind.
        """
        program = Program.model_validate(program)
        validate_program(program, read_only=True)
        self.input, self.context = {}, {}
        outcomes = []
        await self.session.start()
        try:
            async with asyncio.timeout(program.timeout_seconds):
                for step in program.steps:
                    entry = {"step": step.model_dump(exclude_defaults=True)}
                    try:
                        frames_before = len(self.session.page.frames)
                        await self.execute_steps([step])
                        if step.kind in ("click", "press"):
                            await self.settle_after_action(frames_before)
                        if step.name:
                            value = await self.resolve({"$ref": f"vars.{step.name}"})
                            # Feedback rides into later prompts verbatim, so a body-text read must not
                            # carry a whole page with it.
                            entry["result"] = value[:2000] + "…" if isinstance(value, str) and len(value) > 2000 else value
                        entry["status"] = "ok"
                    except (ProgramError, BrowserError, ValueError, TypeError, KeyError) as exc:
                        entry["status"], entry["error"] = self.describe_failure(exc)
                    outcomes.append(entry)
        except TimeoutError:
            outcomes.append({"status": "timeout", "error": "Exploration exceeded its time limit"})
        return outcomes

    async def evaluate(self, program, *, read_only=False):
        if program.javascript is not None:
            from .javascript import execute_javascript
            return await execute_javascript(self, program, read_only=read_only)
        await self.execute_steps(program.steps)
        return await self.resolve(program.result)

    async def run(self, program, arguments, *, context=None, read_only=False):
        program = Program.model_validate(program)
        validate_program(program, read_only=read_only)
        self.input = arguments
        self.context = context or {}
        start = time.perf_counter()
        try:
            async with asyncio.timeout(program.timeout_seconds):
                await self.session.start()
                result = await self.evaluate(program, read_only=read_only)
        except TimeoutError:
            raise ProgramError("outcome_unknown" if self.mutated else "timeout",
                               f"The {self.current_step} step exceeded its time limit; no mutation was retried",
                               uncertain=self.mutated) from None
        except BrowserError as exc:
            from .recorder import redact
            # Playwright puts the useful part on the following lines: for a strict-mode violation,
            # the candidate elements; for a timeout, which actionability check stalled. Keep them.
            lines = [line.strip() for line in str(exc).splitlines() if line.strip()]
            detail = redact(" | ".join(lines[:7]))[:900]
            locator = " " + json.dumps(redact(self.current_locator)) if self.current_locator else ""
            raise ProgramError("browser_step_failed", f"Browser {self.current_step}{locator} failed: {detail}",
                               uncertain=self.mutated) from None
        except ProgramError as exc:
            if self.observations and not getattr(exc, "observations", None):
                exc.observations = self.observations[-8:]
            if self.mutated and exc.code == "binding_missing":
                exc.uncertain = True
            raise
        except (ValueError, TypeError, KeyError):
            raise ProgramError("program_failed", f"The {self.current_step} step could not process the website result",
                               uncertain=self.mutated) from None
        return result, {"duration_ms": round((time.perf_counter() - start) * 1000, 2),
                        "requests": self.requests, "steps": self.steps_run, "model_calls": 0, "screenshots": 0}


def fingerprint(family):
    value = family.model_dump() if isinstance(family, Family) else family
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]
