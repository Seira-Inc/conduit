import time

import pytest
from test_compiler import family_for

from conduit.programs import Family
from conduit.verifier import FamilyExecutor, validate_family


def executor():
    family = family_for("/records").model_dump()
    family["managed_fields"] = {"meta.time": {"kind": "timestamp_ms", "evidence": ["s1"]},
                                "meta.nonce": {"kind": "revision_nonce", "evidence": ["s1"]}}
    return FamilyExecutor(None, None, family)


def test_metadata_accepts_real_revision_changes_but_preserves_content_and_clock():
    now = int(time.time() * 1000)
    before = {"id": "one", "title": "Before", "note": "Keep", "version": 1,
              "meta": {"time": now - 2000, "nonce": 17, "content": "Also keep"}}
    after = {**before, "title": "After", "version": 2, "meta": {**before["meta"], "time": now, "nonce": 982}}
    check = executor()
    validate_family(check.family)
    assert check.preserves(before, after, {"title": "After"})
    assert not check.preserves(before, {**after, "note": "Corrupted"}, {"title": "After"})
    assert not check.preserves(before, {**after, "meta": {**after["meta"], "content": "Lost"}}, {"title": "After"})
    for bad in ({"time": now - 5000}, {"time": now + 600000}, {"nonce": -1}, {"nonce": "changed"}, {"nonce": 2**35}):
        assert not check.preserves(before, {**after, "meta": {**after["meta"], **bad}}, {"title": "After"})


@pytest.mark.parametrize("path", ["", "id", "title", "title.child", "meta"])
def test_metadata_cannot_exempt_identity_editable_fields_or_ancestors(path):
    family = executor().family.model_dump()
    family["fields"]["note"] = "meta.content"
    family["managed_fields"] = {path: {"kind": "revision_nonce", "evidence": ["s1"]}}
    with pytest.raises(ValueError, match="identity or editable"):
        validate_family(Family.model_validate(family))
