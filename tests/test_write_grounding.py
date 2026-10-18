"""A write may only target a path first-party evidence actually shows."""

import copy
import json

import pytest
from compiler_site import website
from test_compiler import family_for, login

from conduit.compiler import compile_connector
from conduit.models import Manifest
from conduit.programs import Family
from conduit.runtime import Runtime

INVENTED = "/invented/v1/thing"


def steps_family_with_invented_create(base, field):
    family = family_for(base, field).model_dump()
    family["operations"]["create"]["program"]["steps"][0]["url"] = base + INVENTED
    return Family.model_validate(family)


def javascript_family_with_invented_create(base, field):
    """The same guess expressed as JavaScript, which no static rule on request steps would catch."""
    family = copy.deepcopy(family_for(base, field).model_dump())
    create = family["operations"]["create"]["program"]
    create["steps"] = []
    create["result"] = None
    create["javascript"] = (
        "const csrf = await api.session({kind:'cookie', key:'csrf'});"
        f"const r = await api.request({{url: {json.dumps(base + INVENTED)}, method:'POST',"
        "  headers:{'x-csrf-token': csrf}, body: input});"
        "return r.body.item;"
    )
    return Family.model_validate(family)


async def run_with(tmp_path, state, url, family, name):
    manifest = Manifest(id=name, name=name, target=url, kind="website",
                        allowed_origins=[url], allow_private=True)
    runtime = Runtime(manifest, tmp_path)
    calls = []

    async def model(prompt, home, **kwargs):
        calls.append(prompt)
        if len(calls) == 1:
            return {"action": "propose", "family": family.model_dump()}, {"model_calls": 1}
        return {"action": "done"}, {"model_calls": 1}

    try:
        await login(runtime)
        report = await compile_connector(runtime, model=model, research=False)
        return report, manifest, calls
    finally:
        await runtime.close()


@pytest.mark.integration
async def test_steps_create_to_an_unobserved_path_is_rejected_before_it_is_sent(tmp_path):
    with website() as (url, state):
        family = steps_family_with_invented_create(state["base"], state["field"])
        report, manifest, calls = await run_with(tmp_path, state, url, family, "ungrounded-steps")

        assert report["status"] == "incomplete"
        # The decisive assertion: the fixture never saw a write at all.
        assert state["writes"] == 0
        assert not any(t.adapter == "program" for t in manifest.tools)
        complaint = json.dumps(report.get("last_feedback") or {}) + json.dumps(report.get("attempts") or [])
        assert "never observed" in complaint or "probe_record" in complaint
        assert INVENTED in complaint
        assert "probe_record" in calls[-1]


@pytest.mark.integration
async def test_javascript_create_to_an_unobserved_path_is_blocked_by_the_runtime_backstop(tmp_path):
    with website() as (url, state):
        family = javascript_family_with_invented_create(state["base"], state["field"])
        report, manifest, _ = await run_with(tmp_path, state, url, family, "ungrounded-js")

        assert report["status"] == "incomplete"
        assert state["writes"] == 0
        assert not any(t.adapter == "program" for t in manifest.tools)
        trace = json.dumps(report.get("attempts") or []) + json.dumps(report.get("last_feedback") or {})
        assert "ungrounded_write" in trace or "never observed" in trace


@pytest.mark.integration
async def test_a_family_targeting_the_observed_path_still_passes(tmp_path):
    """The guard must not break the ordinary case: the site's own route is grounded by its own code."""
    with website() as (url, state):
        family = family_for(state["base"], state["field"])
        report, manifest, _ = await run_with(tmp_path, state, url, family, "grounded-steps")

        assert report["status"] == "ready", json.dumps(report, indent=2)
        assert {t.name for t in manifest.tools if t.adapter == "program"} == {
            "records_" + verb for verb in ("create", "read", "update", "delete")}
        assert state["writes"] > 0
        assert state["items"]["tester"] == {}
