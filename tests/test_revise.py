import copy

import pytest
from compiler_site import website
from test_compiler import family_for, login

from conduit.compiler import compile_connector, publish, revise_family
from conduit.mcp_check import assert_record_matches, failure_detail
from conduit.models import Manifest
from conduit.programs import Program, ProgramError
from conduit.runtime import Runtime
from conduit.verifier import verify_family


def test_revision_changes_only_named_programs_and_optional_revision_binding():
    original = family_for("/records").model_dump()
    saved = copy.deepcopy(original)
    program = {**original["operations"]["read"]["program"], "timeout_seconds": 27}
    revised = revise_family(original, {"programs": {"read": program}, "revision_path": "currentVersion"})
    assert original == saved
    assert revised.operations["read"].program.timeout_seconds == 27
    for verb in ("create", "update", "delete"):
        assert revised.operations[verb].model_dump() == saved["operations"][verb]
    assert revised.operations["read"].input_schema == saved["operations"]["read"]["input_schema"]
    assert revised.revision_path == "currentVersion"


@pytest.mark.parametrize("base,programs", [(None, {"read": {}}), ({}, {}), ({}, {"login": {}})])
def test_revision_rejects_missing_base_or_non_crud_targets(base, programs):
    with pytest.raises(ValueError):
        revise_family(base, {"programs": programs})


def test_independent_comparison_reports_missing_nested_fields_without_values():
    native = {"id": "one", "version": 2, "children": [{"name": "Private value", "enabled": True}]}
    returned = {"id": "one", "children": [{"name": "Other private value", "enabled": 1}], "extra": 9}
    with pytest.raises(ProgramError) as error:
        assert_record_matches(native, returned)
    report = failure_detail(error.value)
    assert report["error_code"] == "independent_state_mismatch"
    assert report["postcondition"] == {"missing_fields": ["version"], "extra_fields": ["extra"],
                                      "mismatched_fields": ["children[0].enabled", "children[0].name"]}
    assert "Private value" not in str(report) and "Other private value" not in str(report)
    assert_record_matches(None, None)
    assert_record_matches(native, copy.deepcopy(native))


@pytest.mark.integration
async def test_single_read_revision_passes_whole_family_and_mcp_before_publication(tmp_path):
    with website() as (url, state):
        manifest = Manifest(id="focused-repair", name="Focused repair", target=url, kind="website",
                            allowed_origins=[url], allow_private=True)
        runtime = Runtime(manifest, tmp_path)
        family = family_for(state["base"])
        full_read = family.operations["read"].program.model_dump()
        family.revision_path = None
        family.operations["read"].program = Program(javascript="""
            const r=await api.request({url:BASE+'/'+encodeURIComponent(input.id),accept:[200,404]});
            if(r.status===404)return null;
            const {id,title,note}=r.body.item;return {id,title,note};
        """.replace("BASE", repr(state["base"])))
        try:
            await login(runtime)
            baseline = await verify_family(runtime.browser, manifest, family)
            assert baseline["status"] == "ready"
            publish(manifest, family, baseline)
            created = await runtime.call("records_create", {"title": "Conduit repair check", "note": "Keep"})
            with pytest.raises(ProgramError) as error:
                assert_record_matches(state["items"]["tester"][created["id"]], created["data"])
            manifest.capabilities[0]["last_error"] = failure_detail(error.value)
            await runtime.call("records_delete", {"id": created["id"]})
            old = copy.deepcopy(family.model_dump())

            async def model(prompt, *args, **kwargs):
                assert "missing_fields" in prompt and "previous_family" in prompt
                return {"action": "revise", "programs": {"read": full_read}, "revision_path": "version"}, {"model_calls": 1}

            result = await compile_connector(runtime, resource="records", model=model, research=False)
            assert result["status"] == "ready" and result["mcp_test"]["status"] == "passed"
            assert len(result["attempts"][0]["cases"]) == 3 and result["attempts"][0]["restart_tested"]
            assert result["mcp_test"]["restarts"] == 2 and not state["items"]["tester"]
            changed = next(t for t in manifest.tools if t.name == "records_read")
            assert changed.adapter_version == 2
            for verb in ("create", "update", "delete"):
                assert changed.config["family"]["operations"][verb] == old["operations"][verb]
            made = await runtime.call("records_create", {"title": "Conduit unseen repair", "note": "Keep"})
            assert_record_matches(state["items"]["tester"][made["id"]], made["data"])
            await runtime.call("records_delete", {"id": made["id"]})
        finally:
            await runtime.close()
