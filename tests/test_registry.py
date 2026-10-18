import os
import shutil

import pytest
from test_compiler import family_for

from conduit.browser import browser_tools
from conduit.compiler import publish
from conduit.generator import Store
from conduit.models import Manifest, Tool
from conduit.registry import Registry, public_name

READY = {"status": "ready", "operations": {"create": True, "read": True, "update": True, "delete": True}}


def verified_connector(store, connector_id, name, target, resource):
    manifest = Manifest(id=connector_id, name=name, kind="website", target=target,
                        allowed_origins=[target], tools=browser_tools())
    family = family_for(target.rstrip("/") + "/" + resource).model_copy(update={"name": resource, "label": resource})
    publish(manifest, family, {**READY, "resource": resource})
    manifest.tools.append(Tool(
        name=resource + "_archive", adapter="program", operation="update", resource=resource,
        execution_mode="direct", description="Not yet proven", input_schema={"type": "object", "properties": {}},
        output_schema=None, read_only=False, evidence="", config={}, verification={"status": "incomplete"},
    ))
    store.write(manifest)
    return manifest


@pytest.fixture
def registry(tmp_path):
    store = Store(tmp_path)
    verified_connector(store, "hubspot-1", "HubSpot", "https://app.hubspot.com", "contacts")
    verified_connector(store, "vikunja-1", "Vikunja", "http://vikunja.example:3456", "tasks")
    registry = Registry(store)
    registry.refresh()
    return registry


def test_index_holds_only_verified_program_tools(registry):
    names = {e.name for e in registry.entries()}
    assert names == {public_name(c, f"{r}_{v}") for c, r in (("hubspot-1", "contacts"), ("vikunja-1", "tasks"))
                     for v in ("create", "read", "update", "delete")}
    entry = registry.lookup(public_name("hubspot-1", "contacts_read"))
    assert entry.site == "https://app.hubspot.com" and entry.read_only and entry.verb == "read"
    assert "hubspot" in entry.tokens and "contact" in entry.resource_tokens
    assert not entry.has_session


def test_refresh_tracks_rewrites_and_removals(registry, tmp_path):
    store = registry.store
    manifest = store.get("hubspot-1")
    manifest.tools = [t for t in manifest.tools if t.name != "contacts_delete"]
    path = store.write(manifest) / "connector.json"
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000_000))
    registry.refresh()
    assert registry.lookup(public_name("hubspot-1", "contacts_delete")) is None
    assert registry.lookup(public_name("hubspot-1", "contacts_read")) is not None
    shutil.rmtree(store.directory("vikunja-1"))
    registry.refresh()
    assert all(e.connector_id == "hubspot-1" for e in registry.entries())
    assert registry.manifest("vikunja-1") is None


def test_session_flag_follows_the_auth_file(registry, tmp_path):
    (tmp_path / "auth").mkdir()
    (tmp_path / "auth" / "hubspot-1.session").write_bytes(b"JSON\n{}")
    registry.refresh()
    assert registry.lookup(public_name("hubspot-1", "contacts_read")).has_session
    assert not registry.lookup(public_name("vikunja-1", "tasks_read")).has_session


@pytest.mark.parametrize("request_text, site, status, detail", [
    ("create a contact in hubspot", None, "found", ("hubspot-1", "create")),
    ("remove a task on vikunja.example", None, "found", ("vikunja-1", "delete")),
    ("edit the contact", "https://app.hubspot.com", "found", ("hubspot-1", "update")),
    ("show me hubspot", None, "found", ("hubspot-1", "read")),
    ("book a flight", None, "none", "no_site"),
    ("update and delete the contact in hubspot", None, "none", "verb_ambiguous"),
    ("delete the contact", "https://nowhere.example", "none", "unknown_site"),
    ("delete the contact", "https://app-na2.hubspot.com", "found", ("hubspot-1", "delete")),
])
def test_find_never_crosses_sites(registry, request_text, site, status, detail):
    result = registry.find(request_text, site)
    assert result["status"] == status
    if status == "found":
        assert (result["connector_id"], result["verb"]) == detail
        assert result["tool"] == public_name(detail[0], f"{registry.lookup(result['tool']).resource}_{detail[1]}")
    else:
        assert result["reason"] == detail


def test_eta_uses_recorded_build_times(registry):
    assert registry.eta_seconds() == (600, "default")
    manifest = registry.store.get("hubspot-1")
    manifest.compilation = {"status": "ready", "duration_ms": 90_000}
    path = registry.store.write(manifest) / "connector.json"
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000_000))
    registry.refresh()
    assert registry.eta_seconds() == (90, "median of 1 builds")
