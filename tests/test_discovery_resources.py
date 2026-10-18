from conduit.compiler import discovery_resources, migrate_discovery_notes
from conduit.models import Manifest


def manifest():
    return Manifest(id="notes", name="Notes", kind="website", target="https://example.com/")


def test_prerequisite_prose_is_preserved_without_becoming_a_fake_resource():
    m = manifest()
    discovery_resources(m, {"reason": "Need account access", "resources": ["contacts", "A supported token binding"]})
    assert [c["resource"] for c in m.capabilities] == ["contacts"]
    assert m.capabilities[0]["status"] == "incomplete"
    assert not any(m.capabilities[0]["operations"].values())
    assert m.findings[0]["detail"] == "A supported token binding"


def test_legacy_note_migration_preserves_real_resources_and_unresolved_mutations():
    m = manifest()
    base = {"status": "incomplete", "operations": {v: False for v in ("create", "read", "update", "delete")}}
    m.capabilities = [{**base, "resource": "A supported token binding"},
                      {**base, "resource": "contacts"},
                      {**base, "resource": "A lost mutation", "cleanup": [{"status": "needs_attention"}]}]
    migrate_discovery_notes(m)
    assert [c["resource"] for c in m.capabilities] == ["contacts", "A lost mutation"]
    assert m.findings[0]["previous_capability"]["resource"] == "A supported token binding"
    migrate_discovery_notes(m)
    assert len(m.findings) == 1
