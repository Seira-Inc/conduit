"""Describe observed result structures without retaining their values or inventing constraints."""
from __future__ import annotations

from collections import defaultdict


def observed_schema(samples, *, depth=0, budget=None):
    budget = [4000] if budget is None else budget
    budget[0] -= 1
    if depth > 12 or budget[0] < 0 or not samples:
        return {}
    groups = defaultdict(list)
    for value in samples:
        kind = ("null" if value is None else "boolean" if isinstance(value, bool) else
                "integer" if isinstance(value, int) else "number" if isinstance(value, float) else
                "string" if isinstance(value, str) else "object" if isinstance(value, dict) else
                "array" if isinstance(value, list) else "unknown")
        groups[kind].append(value)
    if "unknown" in groups:
        return {}
    # An always-null optional server field tells us nothing about its populated type.
    if set(groups) == {"null"}:
        return {}
    if "number" in groups and "integer" in groups:
        groups["number"].extend(groups.pop("integer"))
    variants = []
    for kind, values in sorted(groups.items()):
        variant = {"type": kind}
        if kind == "object":
            keys = sorted(set().union(*(v.keys() for v in values)))
            if len(keys) > 500:
                variants.append(variant)
                continue
            variant["properties"] = {key: observed_schema([v[key] for v in values if key in v],
                depth=depth + 1, budget=budget) for key in keys}
            variant["required"] = sorted(set.intersection(*(set(v) for v in values)))
            # New fields are allowed; learning a few records cannot prove a closed server schema.
        elif kind == "array":
            variant["items"] = observed_schema([item for v in values for item in v[:100]],
                                               depth=depth + 1, budget=budget)
        variants.append(variant)
    return variants[0] if len(variants) == 1 else {"anyOf": variants}


def output_schema(verb, observed=None):
    from .state_guard import TOKEN_PATTERN

    observed = observed or {}
    data = observed.get("data", {})
    if verb == "read" and data:
        data = {"anyOf": [data, {"type": "null"}]}
    elif verb == "delete":
        data = {"type": "null"}
    properties = {
        "status": {"const": "verified"}, "operation": {"const": verb},
        "id": observed.get("id", {}), "data": data,
        "deleted": {"const": verb == "delete"}, "reconciled": {"type": "boolean"},
        "adapter_version": {"type": "string"},
        "state_token": {"type": ["string", "null"], "pattern": TOKEN_PATTERN},
        "metrics": {"type": "object", "properties": {
            key: {"type": "number" if key == "duration_ms" else "integer", "minimum": 0}
            for key in ("requests", "steps", "model_calls", "screenshots", "duration_ms")},
            "required": ["requests", "steps", "model_calls", "screenshots", "duration_ms"]},
    }
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def family_schema(family, records):
    data = observed_schema(records)
    for source, destination in family.fields.items():
        # Inputs mapped to readback fields already have an independently checked equality
        # contract. Keep their declared value range instead of freezing the learning examples
        # (e.g. integer trial coordinates must not rule out later fractional coordinates).
        declarations = []
        for verb in ("create", "update"):
            declaration = family.operations[verb].input_schema
            for part in source.split("."):
                declaration = declaration.get("properties", {}).get(part, {})
            types = declaration.get("type")
            if types:
                declarations.extend(types if isinstance(types, list) else [types])
        node = data
        for part in destination.split("."):
            node = node.get("properties", {}).get(part)
            if node is None:
                break
        if node is not None and declarations:
            types = set(declarations)
            if "number" in types:
                types.discard("integer")
            node.clear()
            node["type"] = next(iter(types)) if len(types) == 1 else sorted(types)
    from .programs import at
    return {"data": data, "id": observed_schema([at(item, family.id_path) for item in records])}
