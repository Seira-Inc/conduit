"""Check the saved Forms MCP against fresh native reads in a separate browser context.

This acceptance observer is never supplied to the compiler or used as an adapter.
Only records created by the MCP lifecycle are changed; the observer itself only reads.
"""
# ruff: noqa: E402
import argparse
import asyncio
import copy
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from conduit.browser import BrowserSession
from conduit.generator import Store
from conduit.mcp_check import verify_export
from conduit.programs import fingerprint
from conduit.recorder import SECRET


class FormsReadError(ValueError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"Independent Forms read failed (HTTP {status})")


def public_record(value):
    if isinstance(value, dict):
        return {k: "[session secret]" if SECRET.search(k) else public_record(v) for k, v in value.items()}
    if isinstance(value, list):
        return [public_record(v) for v in value]
    return value


def changed_fields(expected, actual):
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        return "record presence or type"
    return ", ".join(sorted(key for key in expected.keys() | actual.keys()
                            if key not in expected or key not in actual or expected[key] != actual[key]))


def content_record(resource, value):
    value = copy.deepcopy(value)
    if (resource == "forms" and isinstance(value, dict) and value.get("rowCount@odata.type") == "#Int64"
            and type(value.get("rowCount")) is int and 0 <= value["rowCount"] < 2**63):
        value.pop("rowCount@odata.type")
    return value


class FormsComparisonError(AssertionError):
    code = "independent_record_mismatch"

    def __init__(self, message, resource, key, expected, actual):
        super().__init__(message + ": " + changed_fields(expected, actual))
        self.postcondition = {"resource": resource, "object_id": key, "check": message, "differences": {}}
        if isinstance(expected, dict) and isinstance(actual, dict):
            for field in sorted(expected.keys() | actual.keys()):
                if field in expected and field in actual and expected[field] == actual[field]:
                    continue
                detail = {"before_present": field in expected, "after_present": field in actual,
                          "before_type": type(expected.get(field)).__name__,
                          "after_type": type(actual.get(field)).__name__}
                if field in {"rowCount@odata.type", "rowCount", "modifiedDate", "order", "index", "status"}:
                    detail.update(before=expected.get(field), after=actual.get(field))
                self.postcondition["differences"][field] = detail


def check_record(resource, verb, arguments, result, native, previous=None):
    actual = copy.deepcopy(native)
    if actual is not None and resource == "form_questions":
        parent, question = result["id"].split("::", 1)
        assert actual["id"] == question, "Independent read returned a different question"
        actual.update(id=result["id"], question_id=question, form_id=parent)
    actual = public_record(actual)
    comparable = content_record(resource, actual)
    claimed = content_record(resource, result["data"])
    if comparable != claimed:
        raise FormsComparisonError("MCP result differs from the independently read full native record",
                                   resource, result["id"], claimed, comparable)
    if verb == "delete" or result["data"] is None:
        assert actual is None, "The native record still exists after deletion"
        return None, {"resource": resource, "verb": verb, "absent": True}
    assert actual["id"] == result["id"], "Independent read returned another record"
    if "title" in arguments:
        assert actual["title"] == arguments["title"], "The requested title was not saved"
    if previous is not None:
        expected, current = content_record(resource, previous), copy.deepcopy(comparable)
        if verb == "update":
            if "title" in arguments:
                expected["title"] = arguments["title"]
            expected.pop("modifiedDate", None)
            current.pop("modifiedDate", None)
        if expected != current:
            raise FormsComparisonError("An unrelated native record property changed", resource,
                                       result["id"], expected, current)
    check = {"resource": resource, "verb": verb, "full_record_fields_checked": len(comparable),
             "native_record_matches": True, "unrelated_properties_preserved": previous is not None}
    if actual != comparable or claimed != result["data"]:
        check["optional_type_annotations"] = ["rowCount@odata.type"]
    return actual, check


class FormsObserver:
    def __init__(self, browser):
        self.browser = browser
        self.base = None
        self.records = {}
        self.checks = []
        self.requests = 0

    async def start(self):
        await self.browser.start()
        page = self.browser.page
        await page.wait_for_function(
            "() => window.OfficeFormServerInfo?.userInfo?.UserId && window.OfficeFormServerInfo?.userInfo?.TenantId",
            timeout=20000,
        )
        ids = await page.evaluate("() => ({user:OfficeFormServerInfo.userInfo.UserId, tenant:OfficeFormServerInfo.userInfo.TenantId})")
        self.base = "https://forms.cloud.microsoft/formapi/api/" + quote(ids["tenant"], safe="")
        self.base += "/users/" + quote(ids["user"], safe="") + "/forms"
        deadline = time.monotonic() + 15
        while True:
            try:
                await self.get(self.base)
                break
            except FormsReadError as exc:
                if exc.status not in (401, 403) or time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(0.5)

    async def get(self, url):
        assert url.startswith(self.base), "Observer request left the observed Forms collection"
        token = await self.browser.page.evaluate("() => OfficeFormServerInfo.antiForgeryToken")
        response = await self.browser.context.request.get(url, max_redirects=0,
            headers={"Accept": "application/json", "OData-Version": "4.0", "__requestverificationtoken": token})
        self.requests += 1
        if response.status == 404:
            return None
        if response.status != 200:
            raise FormsReadError(response.status)
        payload = await response.json()
        assert isinstance(payload, dict) and "error" not in payload, "Forms returned an application error"
        return payload

    async def record(self, resource, key):
        if resource == "forms":
            native = await self.get(self.base + "('" + quote(key, safe="") + "')")
            return None if native is None or native.get("softDeleted") else native
        if resource != "form_questions":
            raise ValueError("The Forms observer only accepts forms and form_questions")
        parent, question = key.split("::", 1)
        parent_url = self.base + "('" + quote(parent, safe="") + "')"
        collection = await self.get(parent_url + "/questions")
        if collection is None:
            assert await self.get(parent_url) is None, "Question collection disappeared but its parent still exists"
            return None
        assert isinstance(collection.get("value"), list), "Question collection has no record array"
        assert not collection.get("@odata.nextLink"), "Question collection pagination needs observation"
        native = next((item for item in collection["value"] if item["id"] == question), None)
        return None if native is None or native.get("status") == "Deleted" else native

    async def observe(self, resource, verb, arguments, result):
        key = result["id"]
        native = await self.record(resource, key)
        current, check = check_record(resource, verb, arguments, result, native, self.records.get((resource, key)))
        self.records[(resource, key)] = current
        # Re-read every other test object, including sibling questions, after each operation.
        for (other_resource, other_key), before in self.records.items():
            if (other_resource, other_key) == (resource, key) or before is None:
                continue
            other = await self.record(other_resource, other_key)
            check_record(other_resource, "read", {}, {"id": other_key, "data": before}, other, before)
        check["other_test_objects_preserved"] = True
        self.checks.append(check)


async def run(args):
    store = Store(args.home)
    manifest = store.get(args.connector)
    if urlsplit(manifest.target).hostname != "forms.cloud.microsoft":
        raise ValueError("This observer is scoped to the saved Microsoft Forms connector")
    output = ROOT / "artifacts" / ("forms-independent-" + uuid4().hex[:8])
    output.mkdir(parents=True)
    signatures = {t.name: fingerprint(t.config["family"]) for t in manifest.tools if t.adapter == "program"}
    browser = BrowserSession(manifest, store.home)
    observer = FormsObserver(browser)
    report = {"status": "incomplete", "kind": "Independent native Forms reads of unchanged generated MCP tools",
              "fingerprints": signatures, "learning_disabled": True}
    try:
        await observer.start()
        print("Saved Forms sign-in works in a separate browser context.", flush=True)
        async def progress(message):
            print(message, flush=True)
        directory = store.directory(manifest.id)
        result = await verify_export(manifest, directory, store.home, observe=observer.observe, progress=progress,
                                     resources={"forms", "form_questions"})
        report.update(result)
        assert signatures == {t.name: fingerprint(t.config["family"]) for t in manifest.tools if t.adapter == "program"}
    except Exception as exc:
        report.update(status="incomplete", error=type(exc).__name__ + ": " + str(exc)[:500])
    finally:
        report["independent_checks"] = observer.checks
        report["observer_read_requests"] = observer.requests
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        await browser.close()
        print("Observer report: " + str(output / "report.json"), flush=True)
    print(json.dumps({k: report.get(k) for k in ("status", "error", "restarts", "independent_observer", "duration_ms")}))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=ROOT / ".conduit")
    parser.add_argument("--connector", default="microsoft-forms-authoring-2b09ac3c")
    asyncio.run(run(parser.parse_args()))
