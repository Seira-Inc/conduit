import json
from types import SimpleNamespace

import pytest

from conduit.compiler import adopt_observed_apis
from conduit.evidence import EvidenceCollector, prompt_evidence
from conduit.models import Manifest
from conduit.programs import Family, Program, Step, read_request, validate_program
from conduit.recorder import Recorder


def test_graphql_queries_are_reads_and_mutations_cannot_be_polled():
    query = Step(kind="request", method="POST", body={"query": "query Get($id:ID!){item(id:$id){id title}}"})
    assert read_request(query)
    validate_program(Program(steps=[query]), read_only=True)
    mutation = Step(kind="request", method="POST", body={"query": "mutation {deleteItem(id:1)}"})
    with pytest.raises(ValueError, match="mutation"):
        validate_program(Program(steps=[mutation]), read_only=True)
    with pytest.raises(ValueError, match="never retried"):
        validate_program(Program(steps=[Step(kind="poll", steps=[mutation])]))
    assert not read_request(Step(kind="request", method="POST", body={"query": {"$ref": "input.query"}}))


def test_recorder_links_new_server_ids_without_exporting_their_values():
    recorder = Recorder()
    recorder.http("https://example.com/contacts", "POST", 201, {"name": "Conduit test"},
                  {"id": "unique-created-id", "access_token": "secret-token"}, {"Authorization": "secret"})
    recorder.http("https://example.com/contacts/unique-created-id", "PATCH", 200,
                  {"parent": "unique-created-id"}, {"id": "unique-created-id"}, {})
    links = recorder.relationships()
    assert {"from": "e1.response.id", "to": "e2.url", "relationship": "response_to_path"} in links
    assert "unique-created-id" not in str(links)
    assert "secret-token" not in str(recorder.events)


def test_prompt_keeps_recent_sources_and_valid_json_under_budget():
    evidence = {"page": {"text": "P" * 20000, "controls": [{"name": "N" * 150}] * 200},
                "sources": [{"id": f"s{i}", "url": f"https://example.com/{i}", "kind": "javascript",
                             "content": "SOURCE" * 4000} for i in range(30)], "network": []}
    evidence["sources"][-1]["content"] = "latest actual request handler"
    packed = prompt_evidence(evidence)
    result = json.loads(packed)
    assert len(packed) <= 105000
    assert result["sources"][0]["content"] == "latest actual request handler"
    assert len(evidence["page"]["controls"]) == 200


def test_captured_cookie_is_removed_from_evidence_and_rejected_as_program_constant():
    collector = EvidenceCollector(SimpleNamespace(recorder=Recorder()), None)
    collector.secret_values.add("session-value-must-stay-local")
    collector.add("https://example.com/app.js", "javascript", 'const csrf="session-value-must-stay-local";')
    assert "session-value-must-stay-local" not in collector.sources[0]["content"]
    assert "session-value-must-stay-local" not in collector.documents["https://example.com/app.js"]
    with pytest.raises(ValueError, match="runtime session binding"):
        collector.ensure_portable(SimpleNamespace(model_dump=lambda: {"body": "session-value-must-stay-local"}))


async def test_only_api_origins_observed_from_connected_page_are_adopted():
    manifest = Manifest(id="origins", name="Origins", target="http://127.0.0.1:11001", kind="website",
                        allowed_origins=["http://127.0.0.1:11001"], allow_private=True)
    family = Family(name="things", label="things", create_input={}, update_input={}, operations={
        "create": {"program": {"steps": [{"kind": "request", "url": "http://127.0.0.1:11002/items", "method": "POST"}]}},
        "read": {"program": {"steps": [{"kind": "request", "url": "http://127.0.0.1:11003/items"}]}}})
    await adopt_observed_apis(manifest, family, [
        {"id": "e1", "kind": "http", "initiator": manifest.target + "/workspace", "url": "http://127.0.0.1:11002/items"},
        {"id": "e2", "kind": "http", "initiator": "https://unrelated.example/frame", "url": "http://127.0.0.1:11003/items"}])
    assert manifest.allowed_origins == ["http://127.0.0.1:11001", "http://127.0.0.1:11002"]
