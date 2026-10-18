"""Publish a tested single-operation bundle without claiming full CRUD coverage."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .models import Tool, load_manifest, slug
from .programs import Family, fingerprint, validate_program
from .result_schema import output_schema
from .secrets import load_session, save_session


def verified_program(tool):
    if tool.adapter != "program":
        return False
    proof = tool.verification
    if proof.get("status") == "ready":
        return proof.get("scope") != "single_operation"
    return (proof.get("status") == "operation_ready" and proof.get("scope") == "single_operation"
            and proof.get("operations") == [tool.operation])


def operation_tool(definition):
    proof = definition.get("verification", {})
    if (proof.get("status") != "passed" or proof.get("scope") != "single_operation"
            or proof.get("mcp_calls", 0) < 2 or not all(proof.get(key) is True for key in
                ("restarted_execution", "native_state_observer", "learning_disabled"))):
        raise ValueError("Import requires a single operation with completed MCP, restart and native-state checks")
    family = Family.model_validate(definition["family"])
    digest = fingerprint(family)
    if digest != definition.get("fingerprint"):
        raise ValueError("The generated operation changed after its recorded verification")
    # Current standalone experiments certify update only. Do not expose their scaffolding CRUD operations.
    operation = family.operations["update"]
    if definition["input_schema"] != operation.input_schema:
        raise ValueError("The exposed input schema differs from the tested operation")
    for verb, candidate in family.operations.items():
        validate_program(candidate.program, read_only=verb in ("read", "list"))
    return Tool(name=definition["name"], description=definition["description"], adapter="program",
        operation="update", input_schema=operation.input_schema, output_schema=output_schema("update"),
        resource=family.name, execution_mode=family.mode, read_only=False,
        config={"family": family.model_dump(), "fingerprint": digest},
        verification={**copy.deepcopy(proof), "status": "operation_ready", "operations": ["update"]},
        evidence="Previously tested single-operation bundle; " + "; ".join(family.evidence))


def import_operation(directory: Path, store, *, session_home: Path | None = None):
    definition = json.loads((directory / "operation.json").read_text(encoding="utf-8"))
    tool = operation_tool(definition)
    source = load_manifest(directory / "connector.json")
    manifest = source.model_copy(deep=True)
    manifest.id = slug(tool.name) + "-" + tool.config["fingerprint"][:8]
    manifest.name = definition["name"]
    target_auth = store.home / "auth" / (manifest.id + ".session")
    if store.directory(manifest.id).exists() or target_auth.exists():
        raise ValueError("This operation is already imported; existing connectors and sessions were preserved")
    session = None
    if session_home is not None:
        session = load_session(session_home / "auth" / (source.id + ".session"))
        if session is None:
            raise ValueError("The explicitly selected source session does not exist")
    manifest.tools = [tool]
    manifest.capabilities = [{"resource": tool.resource, "label": tool.description, "status": "partial",
        "mode": tool.execution_mode, "verification_scope": "single_operation", "verified_tools": [tool.name],
        "operations": {verb: verb == "update" for verb in ("create", "read", "update", "delete")}}]
    manifest.compilation = {"status": "operation_ready", "scope": "single_operation",
                            "verification": copy.deepcopy(tool.verification)}
    exported = store.write(manifest)
    if session is not None:
        save_session(target_auth, session)
    return manifest, exported
