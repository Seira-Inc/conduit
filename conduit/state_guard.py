"""Compare observed record content without depending on a vendor's revision field."""
import copy
import hashlib
import json

TOKEN_PATTERN = r"^sha256:[0-9a-f]{64}$"


def state_token(adapter, key, record):
    if record is None:
        return None
    content = json.dumps(["conduit-state-v1", adapter, key, record], sort_keys=True, ensure_ascii=True,
                         separators=(",", ":"))
    return "sha256:" + hashlib.sha256(content.encode()).hexdigest()


def add_state_contract(tool):
    if tool.adapter != "program":
        return tool
    if tool.operation in ("update", "delete"):
        tool.input_schema = copy.deepcopy(tool.input_schema)
        tool.input_schema.setdefault("properties", {})["expected_state_token"] = {
            "type": "string", "pattern": TOKEN_PATTERN,
            "description": "Pass state_token from the last read to reject changed content before writing. "
                           "This readback guard is not an atomic server-side condition.",
        }
    if tool.output_schema:
        tool.output_schema = copy.deepcopy(tool.output_schema)
        tool.output_schema.setdefault("properties", {})["state_token"] = {
            "type": ["string", "null"], "pattern": TOKEN_PATTERN,
            "description": "Opaque fingerprint of the complete observed record; null when absent.",
        }
    return tool
