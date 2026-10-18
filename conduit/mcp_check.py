"""Exercise exported tools through the MCP protocol, with the learning model disabled."""
from __future__ import annotations

import copy
import json
import os
import sys
import time
from datetime import timedelta
from uuid import uuid4

from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .programs import Family, ProgramError
from .verifier import VERBS, matches, vary


def assert_record_matches(native, returned):
    """Compare an acceptance observer's full record without retaining its field values."""
    differences = {"missing_fields": [], "extra_fields": [], "mismatched_fields": []}
    def compare(expected, actual, path=""):
        if sum(map(len, differences.values())) >= 30:
            return
        if isinstance(expected, dict) and isinstance(actual, dict):
            for key in sorted(expected.keys() | actual.keys()):
                child = (path + "." + key).strip(".")
                if key not in actual:
                    differences["missing_fields"].append(child)
                elif key not in expected:
                    differences["extra_fields"].append(child)
                else:
                    compare(expected[key], actual[key], child)
                if sum(map(len, differences.values())) >= 30:
                    break
        elif isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
            for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
                compare(left, right, f"{path}[{index}]")
        elif expected != actual or (isinstance(expected, bool) != isinstance(actual, bool)):
            differences["mismatched_fields"].append(path or "(record)")
    compare(native, returned)
    if any(differences.values()):
        error = ProgramError("independent_state_mismatch", "MCP result differs from independent website state")
        error.postcondition = differences
        raise error


def failure_detail(exc):
    """Unwrap transport task groups without losing the actionable operation error."""
    children = getattr(exc, "exceptions", ())
    if children:
        failures = [failure_detail(child) for child in children]
        return next((f for f in failures if f["error_code"] != "mcp_test_failed"), failures[0])
    result = {"error": str(exc)[:1500], "error_code": getattr(exc, "code", "mcp_test_failed"),
              "outcome_unknown": getattr(exc, "uncertain", False)}
    if hasattr(exc, "postcondition"):
        result["postcondition"] = exc.postcondition
    if getattr(exc, "observations", None):
        result["execution_observations"] = exc.observations
    return result


def remember_failure(manifest, report):
    """Preserve executable-test feedback for repair without replaying the failed call."""
    if report.get("status") != "incomplete":
        return
    for capability in manifest.capabilities:
        if report.get("failed_resource") not in (None, capability.get("resource")):
            continue
        capability["last_error"] = {"code": report.get("error_code"), "message": report.get("error"),
            "uncertain": report.get("outcome_unknown", False), "operation": report.get("failed_operation"),
            "phase": report.get("failed_phase"), "postcondition": report.get("postcondition"),
            "execution_observations": report.get("execution_observations")}
        cleanup = capability.setdefault("cleanup", [])
        for item in report.get("cleanup", []):
            if item.get("status") == "needs_attention" and item.get("resource") in (None, capability.get("resource")) and item not in cleanup:
                cleanup.append(copy.deepcopy(item))


async def verify_export(manifest, directory, home, *, observe=None, progress=None, resources=None, reference_tools=None):
    """Caller must release its browser session first. Only disposable objects are changed.

    An optional independent observer checks real application state in acceptance tests.
    Without one, results explicitly describe checks through the generated read tool.
    """
    families = {t.resource: Family.model_validate(t.config["family"]) for t in manifest.tools
                if t.adapter == "program" and t.operation == "create" and t.verification.get("status") == "ready"
                and (resources is None or t.resource in resources)}
    # Only the verbs this connector actually published are required or exercised; a resource may
    # legitimately offer creating, reading and removing without an edit control.
    published = {name: [t.operation for t in manifest.tools
                        if t.adapter == "program" and t.resource == name and t.operation in VERBS]
                 for name in families}
    report = {"status": "testing", "transport": "stdio", "learning_disabled": True,
              "independent_observer": observe is not None, "calls": [], "cleanup": [], "restarts": 0}
    if reference_tools:
        report["reference_checks"] = 0
    if not families:
        return {**report, "status": "incomplete", "error": "No verified CRUD tools to test"}
    params = StdioServerParameters(command=sys.executable, args=[str(directory / "server.py")],
        cwd=str(directory), env={**os.environ, "CONDUIT_HOME": str(home), "CONDUIT_LEARNING_DISABLED": "1"})
    live, schemas = {}, {}
    uncertain_create = False
    active_call = {}
    started = time.perf_counter()

    async def call(client, resource, verb, arguments):
        nonlocal uncertain_create
        name = resource + "_" + verb
        active_call.update(failed_resource=resource, failed_operation=verb)
        start = time.perf_counter()
        try:
            response = await client.call_tool(name, arguments)
        except Exception:
            if verb == "create":
                uncertain_create = True
            raise
        if response.isError:
            detail = next((c.text for c in response.content if c.type == "text"), "MCP call failed")
            try:
                error = json.loads(detail)
            except ValueError:
                error = {"message": detail, "code": "mcp_error"}
            if verb == "create":
                if error.get("object_id") is not None:
                    live[(resource, str(error["object_id"]))] = error["object_id"]
                elif error.get("outcome_unknown", True):
                    uncertain_create = True
            failure = ProgramError(error.get("code", "mcp_error"), error.get("message", detail),
                                   uncertain=error.get("outcome_unknown", False))
            if error.get("postcondition"):
                failure.postcondition = error["postcondition"]
            if error.get("execution_observations"):
                failure.observations = error["execution_observations"]
            raise failure
        result = response.structuredContent
        if not isinstance(result, dict):
            if verb == "create":
                uncertain_create = True
            raise ValueError("MCP tool did not return its declared structured result")
        # Remember identity before other checks, so a later validation failure can still be cleaned up.
        if verb == "create":
            if result.get("id") is None or (resource, str(result["id"])) in live:
                uncertain_create = True
                raise ValueError("MCP create did not return a fresh identifier")
            live[(resource, str(result["id"]))] = result["id"]
        Draft202012Validator(schemas[name]).validate(result)
        if result["metrics"]["model_calls"] != 0 or result["metrics"]["screenshots"] != 0:
            raise ValueError("Compiled execution invoked a model or screenshot")
        if reference_tools and resource in reference_tools:
            reference = await client.call_tool(reference_tools[resource], {"id": result["id"]})
            if reference.isError or not isinstance(reference.structuredContent, dict):
                raise ValueError("The existing adapter's reference read failed during MCP verification")
            native = reference.structuredContent
            if native["metrics"]["model_calls"] != 0 or native["metrics"]["screenshots"] != 0:
                raise ValueError("Reference execution invoked a model or screenshot")
            assert_record_matches(native["data"], result["data"])
            report["reference_checks"] += 1
        if observe:
            await observe(resource, verb, arguments, result)
        report["calls"].append({"tool": name, "wall_ms": round((time.perf_counter() - start) * 1000, 2),
                                "metrics": copy.deepcopy(result["metrics"])})
        if verb == "delete":
            live.pop((resource, str(result["id"])), None)
        return result

    async def connect_checks(client):
        await client.initialize()
        catalog = {t.name: t for t in (await client.list_tools()).tools}
        for resource in families:
            for verb in published[resource]:
                name = resource + "_" + verb
                tool = catalog.get(name)
                if tool is None or not tool.outputSchema:
                    raise ValueError("The MCP catalog lacks a typed tool: " + name)
                Draft202012Validator.check_schema(tool.inputSchema)
                Draft202012Validator.check_schema(tool.outputSchema)
                schemas[name] = tool.outputSchema
        coverage = await client.read_resource("conduit://capabilities")
        if not coverage.contents:
            raise ValueError("The MCP did not expose its verification resource")

    saved = {}
    try:
        for phase in ("write", "restart_read_delete", "restart_verify_absence"):
            active_call["failed_phase"] = phase
            if progress:
                await progress("Testing the exported MCP: " + phase.replace("_", " "))
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=90)) as client:
                    await connect_checks(client)
                    if phase != "write":
                        report["restarts"] += 1
                    for resource, family in families.items():
                        if phase == "write":
                            inputs = vary(family.create_input, "mcp-control-" + uuid4().hex[:10],
                                          family.operations["create"].input_schema)
                            control = await call(client, resource, "create", inputs)
                            inputs = vary(family.create_input, "mcp-new-" + uuid4().hex[:10],
                                          family.operations["create"].input_schema)
                            created = await call(client, resource, "create", inputs)
                            if not matches(family, created["data"], inputs):
                                raise ValueError("MCP create returned different data")
                            if "update" in published[resource]:
                                changes = {"id": created["id"], **vary(family.update_input, "mcp-edit-" + uuid4().hex[:10],
                                                                     family.operations["update"].input_schema)}
                                edited = await call(client, resource, "update", changes)
                                if not matches(family, edited["data"], changes):
                                    raise ValueError("MCP update returned different data")
                            else:
                                edited = created
                            saved[resource] = (control, edited)
                        elif phase == "restart_read_delete":
                            control, edited = saved[resource]
                            current = await call(client, resource, "read", {"id": edited["id"]})
                            if current["data"] != edited["data"]:
                                raise ValueError("The edited object did not survive the MCP/browser restart")
                            await call(client, resource, "delete", {"id": edited["id"]})
                            absent = await call(client, resource, "read", {"id": edited["id"]})
                            if absent["data"] is not None:
                                raise ValueError("A fresh MCP read still found the deleted object")
                            unchanged = await call(client, resource, "read", {"id": control["id"]})
                            if unchanged["data"] != control["data"]:
                                raise ValueError("The MCP changed an unrelated control object")
                            await call(client, resource, "delete", {"id": control["id"]})
                        else:
                            for removed in saved[resource]:
                                absent = await call(client, resource, "read", {"id": removed["id"]})
                                if absent["data"] is not None:
                                    live[(resource, str(removed["id"]))] = removed["id"]
                                    raise ValueError("A deleted object reappeared after restarting the MCP/browser")
        report["status"] = "passed"
    except Exception as exc:
        report.update(status="incomplete", **failure_detail(exc), **active_call)
    finally:
        if live:
            # Reconnect and read first; never repeat a create or replay an uncertain mutation.
            try:
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=90)) as client:
                        await connect_checks(client)
                        for (resource, _), key in list(live.items()):
                            try:
                                found = await call(client, resource, "read", {"id": key})
                                if found["data"] is not None:
                                    await call(client, resource, "delete", {"id": key})
                                live.pop((resource, str(key)), None)
                                report["cleanup"].append({"resource": resource, "status": "removed"})
                            except Exception:
                                report["cleanup"].append({"resource": resource, "id": key, "status": "needs_attention"})
            except Exception:
                report["cleanup"].append({"status": "needs_attention", "reason": "Could not reconnect for cleanup"})
        if uncertain_create:
            report["cleanup"].append({"status": "needs_attention", "outcome_unknown": True})
        report["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        remember_failure(manifest, report)
    return report
