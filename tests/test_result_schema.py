import json

import pytest
from jsonschema import Draft202012Validator, ValidationError

from conduit.programs import Family
from conduit.result_schema import family_schema, observed_schema, output_schema


def test_result_types_merge_observed_states_without_record_values():
    records = [{"id": "first-private-id", "title": "private title", "size": 1, "active": True,
                "owner": None, "tags": [], "optional": {"label": "private label"}},
               {"id": "second-private-id", "title": "new title", "size": 1.5, "active": False,
                "owner": {"name": "private name"}, "tags": ["private tag"]}]
    schema = observed_schema(records)
    checker = Draft202012Validator(schema)
    for record in records:
        checker.validate(record)
    assert schema["properties"]["size"] == {"type": "number"}
    assert "optional" not in schema["required"]
    assert schema["properties"]["tags"]["items"] == {"type": "string"}
    checker.validate({**records[1], "new_server_field": 1})
    with pytest.raises(ValidationError):
        checker.validate({**records[1], "title": 123})
    assert "private" not in json.dumps(schema)


def test_null_only_and_empty_collection_do_not_guess_populated_types():
    schema = observed_schema([{"optional": None, "values": []}])
    Draft202012Validator(schema).validate({"optional": {"later": True}, "values": [1, "two"]})


def test_tool_contract_describes_full_record_and_absence_separately():
    observed = {"data": observed_schema([{"id": 12, "text": "hello"}]), "id": {"type": "integer"}}
    result = {"status": "verified", "operation": "create", "id": 99, "data": {"id": 99, "text": "new"},
              "deleted": False, "reconciled": False, "adapter_version": "hash", "state_token": "sha256:" + "a" * 64, "metrics": {
                  "requests": 2, "steps": 2, "model_calls": 0, "screenshots": 0, "duration_ms": 12.5}}
    Draft202012Validator(output_schema("create", observed)).validate(result)
    with pytest.raises(ValidationError):
        Draft202012Validator(output_schema("create", observed)).validate({**result, "data": None})
    Draft202012Validator(output_schema("read", observed)).validate({**result, "operation": "read", "data": None})
    Draft202012Validator(output_schema("delete", observed)).validate({**result, "operation": "delete", "deleted": True, "data": None})


def test_editable_fields_keep_input_types_beyond_the_learning_values():
    family = Family(name="records", label="Records", fields={"position": "nested.x", "title": "title"},
        create_input={}, update_input={}, operations={verb: {"program": {}, "input_schema": {
            "type": "object", "properties": {"position": {"type": "number"}, "title": {"type": ["string", "null"]}}}}
            for verb in ("create", "update")})
    result = family_schema(family, [{"id": "a", "nested": {"x": 100, "fixed": True}, "title": "Test"}])
    Draft202012Validator(result["data"]).validate({"id": "new", "nested": {"x": 100.5, "fixed": True}, "title": None})
    with pytest.raises(ValidationError):
        Draft202012Validator(result["data"]).validate({"id": "new", "nested": {"x": "wrong", "fixed": True}, "title": None})
