from types import SimpleNamespace

from conduit.grounding import Ledger, observed, shape, target, write_targets
from conduit.programs import Program, Step

SITE = "https://app.example.com"


def event(url, *, method="GET", initiator=SITE + "/home", phase="discovery", kind="http"):
    return {"id": "e1", "kind": kind, "url": url, "method": method,
            "initiator": initiator, "phase": phase, "status": 200}


def test_shape_collapses_identifier_segments():
    assert shape("/contacts/465480247030/profile") == "/contacts/*/profile"
    assert shape("/items/9f86d081884c7d65") == "/items/*"
    assert shape("/items/7f3e1a9b-2c4d-4e5f-8a1b-0c9d8e7f6a5b") == "/items/*"
    assert shape("/contacts/v1/contact") == "/contacts/v1/contact"
    assert shape("/") == "/"


def test_shape_keeps_long_route_words_that_only_look_opaque():
    """Length alone must not collapse a segment, or an API namespace disappears."""
    assert shape("/chirp-frontend-app/v1/gateway") == "/chirp-frontend-app/v1/gateway"
    assert shape("/g/com.vendor.crm.rpc.ObjectReadRpc/read") == "/g/com.vendor.crm.rpc.ObjectReadRpc/read"
    assert shape("/records/a-very-long-opaque-slug") == "/records/a-very-long-opaque-slug"
    assert shape("/objects/0-1/views") == "/objects/0-1/views"


def test_target_splits_origin_from_shape():
    assert target(SITE + "/api/items/42?page=2") == (SITE, "/api/items/*")


def test_observed_ignores_traffic_we_caused_ourselves():
    origins = [SITE]
    assert observed([event(SITE + "/api/items")], origins) == {(SITE, "/api/items")}
    # Recorder.http sets no initiator for the adapter's own requests.
    assert observed([event(SITE + "/api/items", initiator=None)], origins) == set()
    assert observed([event(SITE + "/api/items", phase="verify:items:0:create")], origins) == set()
    assert observed([event(SITE + "/api/items", phase="runtime")], origins) == set()
    assert observed([event(SITE + "/api/items", initiator="https://elsewhere.example")], origins) == set()
    assert observed([event(SITE + "/api/items", kind="websocket")], origins) == set()


def test_grounded_accepts_observed_and_parent_collections():
    ledger = Ledger([event(SITE + "/api/items/42", method="GET")], [SITE])
    assert ledger.grounded(SITE + "/api/items/99")
    assert ledger.grounded(SITE + "/api/items")
    assert not ledger.grounded(SITE + "/api/contacts")
    assert not ledger.grounded("https://other.example.com/api/items/42")


def test_grounded_accepts_a_path_in_the_sites_own_code():
    ledger = Ledger([], [SITE], text="fetch('/internal/create-contact', {method:'POST'})")
    assert ledger.grounded(SITE + "/internal/create-contact")
    assert not ledger.grounded(SITE + "/internal/delete-everything")


def test_vendor_documentation_does_not_ground_a_path():
    collector = SimpleNamespace(
        sources=[{"url": "https://developers.example.com/docs", "kind": "documentation"},
                 {"url": SITE + "/app.js", "kind": "javascript"}],
        documents={"https://developers.example.com/docs": "POST /contacts/v1/contact creates a contact",
                   SITE + "/app.js": "fetch('/api/real-create')"},
    )
    from conduit.grounding import first_party_text
    text = first_party_text(collector)
    assert "/api/real-create" in text
    assert "/contacts/v1/contact" not in text
    assert not Ledger([], [SITE], text=text).grounded(SITE + "/contacts/v1/contact")


def test_an_rpc_gateway_write_is_grounded_by_a_read_through_the_same_gateway():
    """The real HubSpot shape: the service and method are in the path, so no write matches a read."""
    read = event(SITE + "/chirp-frontend-app/v1/gateway/com.vendor.crm.rpc.ObjectReadRpc/getObjects",
                 method="POST")
    ledger = Ledger([read, event(SITE + "/contacts/245770700/record/0-1/465480247030")], [SITE])

    assert ledger.grounded(SITE + "/chirp-frontend-app/v1/gateway/com.vendor.crm.rpc.ObjectWriteRpc/createObject")
    # A different auth tier is a different namespace, so one shared segment is not enough.
    assert not ledger.grounded(SITE + "/chirp-frontend-internal/v1/gateway/com.vendor.X/y")
    # The guess that actually failed: it shares only "contacts" with anything observed.
    assert not ledger.grounded(SITE + "/contacts/v1/contact")
    assert not ledger.grounded(SITE + "/crm/objects/2026-09/contacts")
    assert ledger.grounded(SITE + "/contacts/245770700/record/0-1/999")


def test_reject_names_the_remedy():
    ledger = Ledger([], [SITE])
    assert ledger.reject("GET", SITE + "/anything") is not None
    message = ledger.reject("POST", SITE + "/contacts/v1/contact")
    assert "probe_record" in message
    assert "not evidence" in message
    assert ledger.reject("POST", SITE + "/ok") is not None


def test_write_targets_lists_only_literal_mutating_urls():
    program = Program(steps=[
        Step(kind="request", method="POST", url=SITE + "/api/items", name="made"),
        Step(kind="request", method="GET", url=SITE + "/api/items/1", name="read"),
        Step(kind="request", method="DELETE", url={"$concat": [SITE + "/api/items/", {"$ref": "input.id"}]},
             name="gone"),
        Step(kind="click", locator={"css": "button"}),
    ])
    assert list(write_targets(program)) == [("POST", SITE + "/api/items")]
