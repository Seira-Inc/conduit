"""Lower one observed document recipe into ordinary, verifiable CRUD programs."""
from __future__ import annotations

import copy

from pydantic import Field

from .models import StrictModel, schema
from .programs import Family, ManagedField, Program, validate_program, walk


class DocumentRecipe(StrictModel):
    name: str
    label: str
    evidence: list[str]
    inspection: Program
    replacement: Program
    template: dict
    properties: dict
    fields: dict[str, str]
    create_input: dict
    update_input: dict
    collection_path: str = "elements"
    id_key: str = "id"
    revision_path: str | None = None
    managed_fields: dict[str, ManagedField] = Field(default_factory=dict)
    persistence: Program | None = None
    notes: str = ""
    update_metadata: dict = Field(default_factory=dict)


def bind(value, prefix, contexts=None):
    """Rename local variables and bind recipe context to the caller's private values."""
    if isinstance(value, list):
        return [bind(x, prefix, contexts) for x in value]
    if not isinstance(value, dict):
        return value
    result = {k: bind(v, prefix, contexts) for k, v in value.items()}
    if isinstance(value.get("$ref"), str):
        path = value["$ref"]
        if path.startswith("vars."):
            result["$ref"] = "vars." + prefix + path[5:]
        for source, destination in (contexts or {}).items():
            if path == source or path.startswith(source + "."):
                result["$ref"] = destination + path[len(source):]
    return result


def inline(program, prefix, contexts=None):
    data = bind(program.model_dump(exclude_defaults=True), prefix, contexts)
    def rename(steps):
        for step in steps:
            if step.get("name"):
                step["name"] = prefix + step["name"]
            rename(step.get("steps", []))
            rename(step.get("otherwise", []))
            if step.get("verify_program"):
                rename(step["verify_program"].get("steps", []))
    rename(data.get("steps", []))
    return data


def compile_document(value):
    recipe = DocumentRecipe.model_validate(value)
    validate_program(recipe.inspection, read_only=True)
    if not any(s.kind in ("download", "request", "read", "command") for s in walk(recipe.inspection.steps)):
        raise ValueError("A document recipe needs an actual application inspection")
    if not recipe.fields or set(recipe.fields) != set(recipe.properties):
        raise ValueError("Every editable document property needs an explicit readback field")
    if "." in recipe.id_key or recipe.id_key in recipe.fields.values():
        raise ValueError("The document identity must be a separate top-level property")
    uploads = [s for s in walk(recipe.replacement.steps) if s.kind == "upload"]
    if len(uploads) != 1 or uploads[0].value != {"$ref": "context.after"} or \
            uploads[0].expected_document != {"$ref": "context.before"}:
        raise ValueError("Replacement needs one guarded upload from context.before to context.after")
    if not uploads[0].export_locator and not uploads[0].verify_program:
        raise ValueError("Document replacement must check a fresh application export")
    if any(s.kind not in ("upload", "click", "press", "set", "assert", "read", "download", "if", "poll")
           for s in walk(recipe.replacement.steps)):
        raise ValueError("Document replacement may only navigate controls and upload the prepared document")
    inspect = inline(recipe.inspection, "inspect_")
    start = inspect.get("steps", []) + [{"kind": "set", "name": "document_before", "value": inspect.get("result")}]
    document = {"$ref": "vars.document_before"}
    selected = {"$find": {"items": {"$ref": "vars.document_before." + recipe.collection_path},
                           "path": recipe.id_key, "equals": {"$ref": "input.id"}}}
    id_schema = {"id": {"type": "string"}}
    operations = {"read": {"input_schema": schema(id_schema, ["id"]),
                            "program": {"steps": start, "result": selected}}}
    replacement = inline(recipe.replacement, "replace_", {
        "context.before": "vars.document_before", "context.after": "vars.document_after"})
    for verb in ("create", "update", "delete"):
        item = {"$merge": [recipe.template, {recipe.id_key: {"$uuid": True}}]} if verb == "create" else selected
        item = {"$assign": {"object": item, "values": {"$ref": "input"}, "paths": recipe.fields}}
        if verb == "update" and recipe.update_metadata:
            item = {"$merge": [item, recipe.update_metadata]}
        steps = copy.deepcopy(start)
        if verb != "delete":
            steps.append({"kind": "set", "name": "document_item", "value": item})
        steps.append({"kind": "set", "name": "document_after", "value": {"$patch": {
            "document": document, "path": recipe.collection_path, "operation": verb,
            "id": {"$ref": "input.id", "default": None}, "id_path": recipe.id_key,
            "value": {"$ref": "vars.document_item", "default": None}}}})
        steps.extend(copy.deepcopy(replacement.get("steps", [])))
        props = recipe.properties if verb == "create" else {**id_schema, **recipe.properties}
        required = list(recipe.properties) if verb == "create" else ["id"]
        if verb == "delete":
            props = id_schema
        if recipe.revision_path and verb in ("update", "delete"):
            props = {**props, "expected_revision": {}}
        operations[verb] = {"input_schema": schema(props, required), "program": {
            "steps": steps, "result": {"$ref": "vars.document_item"} if verb == "create" else None,
            "timeout_seconds": min(120, recipe.inspection.timeout_seconds + recipe.replacement.timeout_seconds)}}
    family = Family(name=recipe.name, label=recipe.label, mode="document", evidence=recipe.evidence,
        operations=operations, create_input=recipe.create_input, update_input=recipe.update_input,
        id_path=recipe.id_key, fields=recipe.fields, revision_path=recipe.revision_path, persistence=recipe.persistence,
        document_inspection=recipe.inspection, managed_fields=recipe.managed_fields, notes=recipe.notes)
    from .verifier import validate_family
    validate_family(family)
    return family
