import copy
import json

import pytest
from compiler_site import website as api_website
from interface_site import website
from test_compiler import family_for, login

from conduit.compiler import compile_connector
from conduit.models import Manifest
from conduit.programs import Program, ProgramError, ProgramRunner
from conduit.record_probe import RESOURCE, RecordProbe, creation_steps
from conduit.runtime import Runtime


def steps(label="Title", button="Create record"):
    return [{"kind": "fill", "locator": {"label": label}, "value": {"$ref": "input.marker"}},
            {"kind": "fill", "locator": {"label": "Note"}, "value": "Keep the other content"},
            {"kind": "click", "locator": {"role": "button", "name": button}}]


def inspection(base, variant=False):
    field = "caption" if variant else "title"
    return Program(javascript="""
        const response=await api.request({url:BASE+'/'+encodeURIComponent(input.id),accept:[200,404]});
        if(response.status===404)return null;
        const html=response.body;
        function value(name){
          const pattern=VARIANT ? '<p data-field="'+name+'">(.*?)</p>'
                               : '<dd id="record-'+name+'">(.*?)</dd>';
          const match=html.match(new RegExp(pattern,'s'));
          if(!match)throw new Error('Missing record field '+name);
          return match[1];
        }
        return {id:value('id'),FIELD:value('FIELD'),note:value('note'),version:Number(value('version'))};
    """.replace("BASE", json.dumps(base)).replace("VARIANT", str(variant).lower()).replace("FIELD", field))


def removal(base):
    return Program(steps=[{"kind": "navigate", "url": {"$concat": [base + "/", {"$ref": "input.id"}]}},
                          {"kind": "click", "locator": {"role": "button", "name": "Delete record"}}])


async def open_form(runtime):
    await login(runtime)
    await runtime.browser.page.get_by_role("link", name="New record").click()


@pytest.mark.parametrize("bad", [
    [],
    [{"kind": "click", "locator": {"text": "Create"}}],
    [{"kind": "fill", "locator": {"label": "Title"}, "value": "Conduit literal"},
     {"kind": "click", "locator": {"text": "Create"}}],
    steps() + [{"kind": "click", "locator": {"text": "Create another"}}],
    [{"kind": "request", "url": "/new", "method": "POST"}] + steps(),
])
def test_creation_requires_owned_marker_and_only_one_final_submit(bad):
    with pytest.raises(ValueError):
        creation_steps(bad)


def test_probe_metadata_does_not_remove_an_existing_resource_with_the_action_name(tmp_path):
    capability = {"resource": "record_probe", "status": "ready", "operations": {"read": True}}
    manifest = Manifest(id="resource-name", name="Names", target="https://example.test", kind="website",
                        capabilities=[copy.deepcopy(capability)])
    RecordProbe(tmp_path, manifest).sync_manifest()
    assert manifest.capabilities == [capability]


@pytest.mark.integration
@pytest.mark.parametrize("variant", [False, True])
async def test_html_probe_survives_fresh_runtime_and_verifies_native_cleanup(tmp_path, variant):
    with website(variant) as (url, state):
        manifest = Manifest(id="record-probe", name="Record probe", target=url, kind="website",
                            allowed_origins=[url], allow_private=True)
        runtime = Runtime(manifest, tmp_path)
        try:
            await open_form(runtime)
            probe = RecordProbe(tmp_path, manifest)
            result = await probe.begin(runtime.browser, steps("Caption" if variant else "Title"))
            key = runtime.browser.page.url.rsplit("/", 1)[1]
            assert state["writes"] == 1 and state["items"]["tester"][key][state["field"]] == result["marker"]
            await runtime.close()
            # Simulate the caller losing its manifest update after the durable journal was saved.
            manifest.capabilities = []
            runtime = Runtime(manifest, tmp_path)
            restored = RecordProbe(tmp_path, manifest)
            restored.sync_manifest()
            assert manifest.capabilities[0]["cleanup"][0]["status"] == "needs_attention"
            with pytest.raises(ValueError, match="never repeat"):
                await restored.begin(runtime.browser, steps())
            found = await restored.recover(runtime.browser, inspection(state["base"], variant), object_id=key)
            assert found["record"] == state["items"]["tester"][key] and not found["removed"]
            removed = await restored.recover(runtime.browser, inspection(state["base"], variant), removal(state["base"]))
            assert removed["removed"] and removed["restart_tested"]
            assert not restored.pending() and not manifest.capabilities
            assert not state["items"]["tester"] and state["writes"] == 2
            assert state["content_types"] == {"text/html"}
        finally:
            await runtime.close()


class InterruptedProcess(BaseException):
    pass


@pytest.mark.integration
async def test_a_miss_before_the_submit_closes_the_probe_instead_of_blocking(tmp_path):
    """Nothing leaves the browser until the submit, so a wrong fill locator is not an uncertain write."""
    with website() as (url, state):
        manifest = Manifest(id="aborted-probe", name="Aborted probe", target=url, kind="website",
                            allowed_origins=[url], allow_private=True)
        runtime = Runtime(manifest, tmp_path)
        try:
            await open_form(runtime)
            probe = RecordProbe(tmp_path, manifest)
            with pytest.raises(ValueError, match="before its submit") as failure:
                await probe.begin(runtime.browser, steps(label="No such field"))
            assert "No such field" in str(failure.value)
            assert state["writes"] == 0
            assert not probe.pending()
            assert not any(c.get("resource") == RESOURCE for c in manifest.capabilities)
            # The next probe may proceed at once; there is nothing to reconcile.
            result = await probe.begin(runtime.browser, steps())
            assert result["status"] == "needs_inspection"
            assert state["writes"] == 1
            key = next(iter(state["items"]["tester"]))
            removed = await probe.recover(runtime.browser, inspection(state["base"]), removal(state["base"]), object_id=key)
            assert removed["removed"] and not probe.pending()
        finally:
            await runtime.close()


@pytest.mark.integration
async def test_drift_in_other_fields_does_not_block_deleting_the_owned_probe(tmp_path):
    """Live sites move timestamps between reads; ownership is the id plus the marker, not every byte."""
    with website() as (url, state):
        manifest = Manifest(id="drifting-probe", name="Drifting probe", target=url, kind="website",
                            allowed_origins=[url], allow_private=True)
        runtime = Runtime(manifest, tmp_path)
        try:
            await open_form(runtime)
            probe = RecordProbe(tmp_path, manifest)
            await probe.begin(runtime.browser, steps())
            key = next(iter(state["items"]["tester"]))
            seen = await probe.recover(runtime.browser, inspection(state["base"]), object_id=key)
            assert not seen["removed"] and probe.pending()["status"] == "observed"
            # The application changes a field behind our back, as a CRM does with its timestamps.
            state["items"]["tester"][key]["version"] += 7
            result = await probe.recover(runtime.browser, inspection(state["base"]), removal(state["base"]), object_id=key)
            assert result["removed"] and not probe.pending()
            assert not state["items"]["tester"]
        finally:
            await runtime.close()


@pytest.mark.integration
async def test_a_removal_rejected_by_validation_leaves_nothing_unconfirmed(tmp_path):
    """A program the runtime refuses to execute cannot have deleted anything."""
    with website() as (url, state):
        manifest = Manifest(id="invalid-removal", name="Invalid removal", target=url, kind="website",
                            allowed_origins=[url], allow_private=True)
        runtime = Runtime(manifest, tmp_path)
        try:
            await open_form(runtime)
            probe = RecordProbe(tmp_path, manifest)
            await probe.begin(runtime.browser, steps())
            key = next(iter(state["items"]["tester"]))
            invalid = Program(javascript="await api.sleep(5000); await api.navigate('/'); return null;")
            # The worker's limits are checked in the source, so the removal is refused before the probe
            # is ever marked as having started a deletion.
            with pytest.raises(ValueError, match="2000 ms limit"):
                await probe.recover(runtime.browser, inspection(state["base"]), invalid, object_id=key)
            pending = probe.pending()
            assert not pending["delete_started"]
            assert len(state["items"]["tester"]) == 1
            result = await probe.recover(runtime.browser, inspection(state["base"]), removal(state["base"]), object_id=key)
            assert result["removed"] and not probe.pending() and not state["items"]["tester"]
        finally:
            await runtime.close()


@pytest.mark.integration
async def test_a_removal_that_never_reaches_its_control_can_be_corrected(tmp_path):
    """A missed Delete locator with the record unchanged is not an unconfirmed deletion."""
    with website() as (url, state):
        manifest = Manifest(id="missed-delete", name="Missed delete", target=url, kind="website",
                            allowed_origins=[url], allow_private=True)
        runtime = Runtime(manifest, tmp_path)
        try:
            await open_form(runtime)
            probe = RecordProbe(tmp_path, manifest)
            await probe.begin(runtime.browser, steps())
            key = next(iter(state["items"]["tester"]))
            wrong = Program(steps=[{"kind": "navigate", "url": {"$concat": [state["base"] + "/", {"$ref": "input.id"}]}},
                                   {"kind": "click", "locator": {"role": "menuitem", "name": "Delete"}}])
            with pytest.raises(ValueError, match="nothing was deleted") as failure:
                await probe.recover(runtime.browser, inspection(state["base"]), wrong, object_id=key)
            assert "Delete" in str(failure.value)
            pending = probe.pending()
            assert pending and pending["status"] == "observed" and not pending["delete_started"]
            assert len(state["items"]["tester"]) == 1
            # The corrected removal is allowed, runs once, and the probe closes normally.
            result = await probe.recover(runtime.browser, inspection(state["base"]), removal(state["base"]), object_id=key)
            assert result["removed"] and not probe.pending()
            assert not state["items"]["tester"]
        finally:
            await runtime.close()


@pytest.mark.integration
@pytest.mark.parametrize("interrupted_submit", ["Create record", "Delete record"])
async def test_interrupted_mutation_is_reconciled_without_replaying_it(tmp_path, monkeypatch, interrupted_submit):
    with website() as (url, state):
        manifest = Manifest(id="interrupted-record", name="Interrupted record", target=url, kind="website",
                            allowed_origins=[url], allow_private=True)
        runtime = Runtime(manifest, tmp_path)
        original = ProgramRunner.execute_steps

        async def interrupt_after_commit(runner, program_steps):
            await original(runner, program_steps)
            if any(s.kind == "click" and s.locator.get("name") == interrupted_submit for s in program_steps):
                raise InterruptedProcess()

        try:
            await open_form(runtime)
            probe = RecordProbe(tmp_path, manifest)
            monkeypatch.setattr(ProgramRunner, "execute_steps", interrupt_after_commit)
            if interrupted_submit == "Create record":
                with pytest.raises(InterruptedProcess):
                    await probe.begin(runtime.browser, steps())
                key = next(iter(state["items"]["tester"]))
            else:
                await probe.begin(runtime.browser, steps())
                key = next(iter(state["items"]["tester"]))
                with pytest.raises(InterruptedProcess):
                    await probe.recover(runtime.browser, inspection(state["base"]), removal(state["base"]), object_id=key)
            await runtime.close()
            monkeypatch.setattr(ProgramRunner, "execute_steps", original)
            runtime = Runtime(manifest, tmp_path)
            probe = RecordProbe(tmp_path, manifest)
            with pytest.raises(ValueError, match="never repeat"):
                await probe.begin(runtime.browser, steps())
            result = await probe.recover(runtime.browser, inspection(state["base"]), removal(state["base"]), object_id=key)
            assert result["removed"] and not probe.pending()
            assert state["writes"] == 2 and not state["items"]["tester"]
        finally:
            await runtime.close()


@pytest.mark.integration
async def test_foreign_records_empty_searches_and_changed_records_do_not_authorize_cleanup(tmp_path):
    with website() as (url, state):
        manifest = Manifest(id="owned-record", name="Owned record", target=url, kind="website",
                            allowed_origins=[url], allow_private=True)
        runtime = Runtime(manifest, tmp_path)
        try:
            await open_form(runtime)
            probe = RecordProbe(tmp_path, manifest)
            await probe.begin(runtime.browser, steps())
            key = next(iter(state["items"]["tester"]))
            read = inspection(state["base"])
            state["items"]["tester"]["foreign"] = {"id": "foreign", "title": "User record", "note": "Keep", "version": 1}
            with pytest.raises(ProgramError, match="ownership"):
                await probe.recover(runtime.browser, read, removal(state["base"]), object_id="foreign")
            with pytest.raises(ProgramError, match="no confirmed identity"):
                await probe.recover(runtime.browser, read, object_id="missing")
            assert state["writes"] == 1 and probe.pending()
            await probe.recover(runtime.browser, read, object_id=key)
            # Drift in other fields is logged, not a veto: ownership is the id plus the marker, which a
            # live application's own timestamp churn never touches.
            state["items"]["tester"][key]["note"] = "Changed elsewhere"
            result = await probe.recover(runtime.browser, read, removal(state["base"]))
            assert result["removed"]
            assert list(state["items"]["tester"]) == ["foreign"]
        finally:
            await runtime.close()


@pytest.mark.integration
async def test_fake_delete_success_stays_pending_and_is_not_repeated(tmp_path):
    with website() as (url, state):
        manifest = Manifest(id="false-delete", name="False delete", target=url, kind="website",
                            allowed_origins=[url], allow_private=True)
        runtime = Runtime(manifest, tmp_path)
        try:
            await open_form(runtime)
            probe = RecordProbe(tmp_path, manifest)
            await probe.begin(runtime.browser, steps())
            key = next(iter(state["items"]["tester"]))
            read = inspection(state["base"])
            with pytest.raises(ProgramError, match="still found"):
                await probe.recover(runtime.browser, read, Program(javascript="return true;"), object_id=key)
            with pytest.raises(ProgramError, match="No deletion was repeated"):
                await probe.recover(runtime.browser, read, removal(state["base"]))
            assert state["writes"] == 1 and probe.pending()
            del state["items"]["tester"][key]  # Simulate later independent reconciliation, not a retry.
            result = await probe.recover(runtime.browser, read)
            assert result["removed"] and not probe.pending()
        finally:
            await runtime.close()


@pytest.mark.integration
@pytest.mark.parametrize("restart_learning", [False, True])
async def test_compiler_requires_probe_cleanup_then_still_runs_complete_lifecycle_and_mcp(tmp_path, restart_learning):
    with api_website() as (url, state):
        manifest = Manifest(id="probe-compiler", name="Probe compiler", target=url, kind="website",
                            allowed_origins=[url], allow_private=True)
        runtime = Runtime(manifest, tmp_path)
        family = family_for(state["base"])
        calls = []

        async def model(prompt, *_args, **_kwargs):
            calls.append(prompt)
            turn = len(calls)
            if turn == 1:
                answer = {"action": "probe_record", "steps": steps(button="Save record")}
            elif turn == 2:
                assert len(state["items"]["tester"]) == 1
                answer = {"action": "propose", "family": family.model_dump()}
            elif turn == 3:
                assert "pending record" in prompt and state["writes"] == 1
                key = next(iter(state["items"]["tester"]))
                answer = {"action": "recover_record", "id": key,
                          "inspection": family.operations["read"].program.model_dump(),
                          "remove": family.operations["delete"].program.model_dump()}
            elif turn == 4:
                assert not state["items"]["tester"] and state["writes"] == 2
                answer = {"action": "propose", "family": family.model_dump()}
            else:
                answer = {"action": "done"}
            return answer, {"model_calls": 1}

        try:
            await login(runtime)
            if restart_learning:
                paused = await compile_connector(runtime, model=model, research=False, max_rounds=1)
                assert paused["status"] == "incomplete" and state["writes"] == 1
                await runtime.close()
                manifest.capabilities = []
                runtime = Runtime(manifest, tmp_path)
            result = await compile_connector(runtime, model=model, research=False, max_rounds=5)
            assert result["status"] == "ready", result
            assert result["mcp_test"]["status"] == "passed" and result["mcp_test"]["restarts"] == 2
            assert len(result["attempts"][0]["cases"]) == 3 and result["attempts"][0]["restart_tested"]
            assert not state["items"]["tester"] and not RecordProbe(tmp_path, manifest).pending()
            assert all(c["resource"] != RESOURCE for c in manifest.capabilities)
            assert len(calls) == 5
            unchanged = copy.deepcopy(family.model_dump())
            assert next(t for t in manifest.tools if t.operation == "create").config["family"] == unchanged
        finally:
            await runtime.close()


@pytest.mark.integration
async def test_unresolved_probe_blocks_exported_mcp_write_gate_for_earlier_candidate(tmp_path):
    with api_website() as (url, state):
        manifest = Manifest(id="probe-gate", name="Probe gate", target=url, kind="website",
                            allowed_origins=[url], allow_private=True)
        runtime = Runtime(manifest, tmp_path)
        family = family_for(state["base"])
        answers = iter([{"action": "propose", "family": family.model_dump()},
                        {"action": "probe_record", "steps": steps(button="Save record")}, {"action": "done"}])

        async def model(*_args, **_kwargs):
            return next(answers), {"model_calls": 1}

        try:
            await login(runtime)
            result = await compile_connector(runtime, model=model, research=False, max_rounds=3)
            assert result["status"] == "incomplete" and result["mcp_test"]["status"] == "incomplete"
            assert "experiment" in result["mcp_test"]["error"]
            assert not any(t.adapter == "program" for t in manifest.tools)
            assert len(state["items"]["tester"]) == 1 and state["writes"] == 16
            key = next(iter(state["items"]["tester"]))
            await RecordProbe(tmp_path, manifest).recover(runtime.browser, family.operations["read"].program,
                                                         family.operations["delete"].program, object_id=key)
            assert not state["items"]["tester"] and state["writes"] == 17
        finally:
            await runtime.close()
