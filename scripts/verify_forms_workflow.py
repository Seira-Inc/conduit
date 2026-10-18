"""Exercise linked form/question tools with new parent IDs through real MCP sessions."""
# ruff: noqa: E402
import argparse
import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from conduit.browser import BrowserSession
from conduit.generator import Store
from conduit.mcp_check import failure_detail
from conduit.programs import ProgramError, fingerprint
from scripts.verify_forms_export import FormsObserver, check_record, content_record, public_record


def check_parent(before, after):
    assert after is not None, "A question operation removed its parent form"
    expected, current = content_record("forms", before), content_record("forms", after)
    expected.pop("modifiedDate", None)
    current.pop("modifiedDate", None)
    annotations = {}
    annotation = "rowCount@odata.type"
    if (annotation in before) != (annotation in after):
        annotations[annotation] = {"before": before.get(annotation), "after": after.get(annotation)}
    changed = sorted(key for key in expected.keys() | current.keys() if expected.get(key) != current.get(key))
    assert expected == current, "A question operation changed unrelated parent form properties: " + ", ".join(changed)
    return annotations


async def run(args):
    store = Store(args.home)
    manifest = store.get(args.connector)
    assert manifest.target == "https://forms.cloud.microsoft/", "Only the selected Forms connector is accepted"
    directory = store.directory(manifest.id)
    output = ROOT / "artifacts" / ("forms-workflow-" + uuid4().hex[:8])
    output.mkdir(parents=True)
    signatures = {t.name: fingerprint(t.config["family"]) for t in manifest.tools if t.adapter == "program"}
    params = StdioServerParameters(command=sys.executable, args=[str(directory / "server.py")], cwd=str(directory),
        env={**os.environ, "CONDUIT_HOME": str(store.home), "CONDUIT_LEARNING_DISABLED": "1"})
    browser = BrowserSession(manifest, store.home)
    observer = FormsObserver(browser)
    report = {"status": "incomplete", "learning_disabled": True, "independent_observer": True,
              "case": args.case, "fingerprints": signatures, "calls": [], "checks": [], "restarts": 0, "cleanup": []}
    full_workflow = args.case == "workflow"
    live, records, schemas = {}, {}, {}
    unknown_create = False
    started = time.perf_counter()

    @asynccontextmanager
    async def connection():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=90)) as client:
                await client.initialize()
                for tool in (await client.list_tools()).tools:
                    if tool.name in signatures:
                        assert tool.outputSchema, "Generated tool lacks its output contract"
                        schemas[tool.name] = tool.outputSchema
                yield client

    async def call(client, resource, verb, arguments):
        nonlocal unknown_create
        name = resource + "_" + verb
        report["active_operation"] = name
        start = time.perf_counter()
        try:
            response = await client.call_tool(name, arguments)
        except Exception:
            unknown_create = verb == "create" or unknown_create
            raise
        if response.isError:
            detail = next((c.text for c in response.content if c.type == "text"), "MCP call failed")
            try:
                error = json.loads(detail)
            except ValueError:
                error = {"code": "mcp_error", "message": detail}
            if verb == "create":
                if error.get("object_id") is not None:
                    live[(resource, error["object_id"])] = True
                elif error.get("outcome_unknown", True):
                    unknown_create = True
            failure = ProgramError(error.get("code", "mcp_error"), error.get("message", "MCP call failed"),
                                   uncertain=error.get("outcome_unknown", False))
            if error.get("postcondition"):
                failure.postcondition = error["postcondition"]
            if error.get("execution_observations"):
                failure.observations = error["execution_observations"]
            raise failure
        result = response.structuredContent
        if verb == "create":
            if not isinstance(result, dict) or not result.get("id"):
                unknown_create = True
                raise ValueError("Create returned no usable identifier")
            live[(resource, result["id"])] = True
        Draft202012Validator(schemas[name]).validate(result)
        assert result["metrics"]["model_calls"] == result["metrics"]["screenshots"] == 0
        native = await observer.record(resource, result["id"])
        key = (resource, result["id"])
        current, check = check_record(resource, verb, arguments, result, native, records.get(key))
        if "form_id" in arguments:
            assert current["form_id"] == arguments["form_id"], "Question was created in a different form"
            check["requested_parent_matches"] = True
        records[key] = current
        if resource == "form_questions" and verb != "read":
            parent_id = result["id"].split("::", 1)[0]
            parent_key = ("forms", parent_id)
            parent = public_record(await observer.record("forms", parent_id))
            annotations = check_parent(records[parent_key], parent)
            if annotations:
                check["parent_type_annotation_changes"] = annotations
            records[parent_key] = parent
            check["parent_content_preserved"] = True
        for (other_resource, other_id), previous in records.items():
            if (other_resource, other_id) == key or previous is None:
                continue
            other = await observer.record(other_resource, other_id)
            check_record(other_resource, "read", {}, {"id": other_id, "data": previous}, other, previous)
        check["other_test_objects_preserved"] = True
        report["checks"].append(check)
        report["calls"].append({"tool": name, "wall_ms": round((time.perf_counter() - start) * 1000, 2),
                                "metrics": result["metrics"]})
        if verb == "delete":
            live.pop(key, None)
        return result

    async def remove(client, resource, key):
        if resource == "forms":
            collection = await observer.get(observer.base + "('" + quote(key, safe="") + "')/questions")
            assert collection is None or (not collection.get("@odata.nextLink") and not collection.get("value")), \
                "Refusing to delete a form that still contains questions"
        return await call(client, resource, "delete", {"id": key})

    nonce = uuid4().hex[:10]
    report["disposable_nonce"] = nonce
    try:
        await observer.start()
        print("Testing " + args.case + " with independent native reads.", flush=True)
        async with connection() as client:
            first = await call(client, "forms", "create", {"title": "Conduit linked A " + nonce})
            forms = [first]
            if full_workflow:
                second = await call(client, "forms", "create", {"title": "Conduit linked B " + nonce})
                forms.append(second)
            q1 = await call(client, "form_questions", "create", {"form_id": first["id"], "title": 'Conduit "R&D" café ' + nonce})
            if full_workflow:
                q2 = await call(client, "form_questions", "create", {"form_id": second["id"], "title": "Conduit other parent " + nonce})
            sibling = await call(client, "form_questions", "create", {"form_id": first["id"], "title": "Conduit sibling " + nonce})
            questions = [q1, sibling] + ([q2] if full_workflow else [])
            if full_workflow:
                before = await call(client, "form_questions", "read", {"id": q1["id"]})
                updated = await call(client, "form_questions", "update", {"id": q1["id"], "title": "Conduit revised € " + nonce,
                                                                         "expected_state_token": before["state_token"]})
                report["native_question_revision_advanced"] = updated["data"]["modifiedDate"] != before["data"]["modifiedDate"]
                unchanged = await observer.record("form_questions", q1["id"])
                stale = await client.call_tool("form_questions_update", {"id": q1["id"], "title": "Conduit stale " + nonce,
                                                                         "expected_state_token": before["state_token"]})
                assert stale.isError, "A stale question edit was accepted"
                error = json.loads(next(c.text for c in stale.content if c.type == "text"))
                assert error["code"] == "conflict", "Stale edit did not return a structured conflict"
                assert await observer.record("form_questions", q1["id"]) == unchanged, "Rejected edit still changed the question"
                report["stale_state_token_rejected_without_change"] = True
                await call(client, "forms", "update", {"id": first["id"], "title": "Conduit revised parent " + nonce})
        report["restarts"] += 1
        print("Reopening the MCP to verify parents and their questions.", flush=True)
        async with connection() as client:
            for resource, key in list(live):
                await call(client, resource, "read", {"id": key})
            for question in questions:
                await remove(client, "form_questions", question["id"])
            for form in forms:
                await remove(client, "forms", form["id"])
        report["restarts"] += 1
        async with connection() as client:
            for resource, key in records:
                assert (await call(client, resource, "read", {"id": key}))["data"] is None
        assert signatures == {t.name: fingerprint(t.config["family"]) for t in manifest.tools if t.adapter == "program"}
        report["status"] = "passed"
    except Exception as exc:
        report.update(status="incomplete", failed_operation=report.get("active_operation"), **failure_detail(exc))
    finally:
        if live:
            try:
                async with connection() as client:
                    for resource, key in sorted(live.copy(), key=lambda item: item[0] == "forms"):
                        try:
                            native = await observer.record(resource, key)
                            if native is not None:
                                assert native.get("title", "").startswith("Conduit ")
                                await remove(client, resource, key)
                            report["cleanup"].append({"resource": resource, "status": "removed"})
                        except Exception:
                            try:
                                absent = await observer.record(resource, key) is None
                            except Exception:
                                absent = False
                            report["cleanup"].append({"resource": resource, "id": key,
                                                      "status": "removed" if absent else "needs_attention"})
            except Exception:
                report["cleanup"].append({"status": "needs_attention", "reason": "Cannot reconnect for cleanup"})
        if unknown_create:
            report["cleanup"].append({"status": "needs_attention", "outcome_unknown": True})
        report["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        report["observer_read_requests"] = observer.requests
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        await browser.close()
        print("Workflow report: " + str(output / "report.json"), flush=True)
    print(json.dumps({k: report.get(k) for k in ("status", "error", "restarts", "duration_ms")}))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=ROOT / ".conduit")
    parser.add_argument("--connector", default="microsoft-forms-authoring-2b09ac3c")
    parser.add_argument("--case", choices=("workflow", "delete-focus"), default="workflow")
    asyncio.run(run(parser.parse_args()))
