"""Rules the worker enforces are caught in the source, before a candidate can write anything."""

import pytest

from conduit.programs import Program, validate_program


def js(body, **kw):
    return Program(javascript=body, **kw)


def test_an_oversized_sleep_is_rejected_from_the_source():
    with pytest.raises(ValueError, match="2000 ms limit"):
        validate_program(js("await api.sleep(5000); return null;"))
    with pytest.raises(ValueError, match="api.sleep\\(2500\\)"):
        validate_program(js("for(let i=0;i<3;i++){ await api . sleep( 2500 ); } return null;"))


def test_sleeps_within_the_limit_pass():
    validate_program(js("await api.sleep(2000); await api.sleep(800); return null;"))
    validate_program(js("const ms = 5000; return ms;"))  # not a sleep call


def test_a_read_declared_on_a_mutating_method_needs_its_evidence():
    body = "const r = await api.request({url:'/s', method:'POST', effect:'read'}); return r.body;"
    with pytest.raises(ValueError, match="read_evidence"):
        validate_program(js(body))
    validate_program(js(body, read_evidence=["e12"]))


def test_an_ordinary_get_read_needs_nothing_extra():
    validate_program(js("const r = await api.request({url:'/s', method:'GET', effect:'read'}); return r.body;"))


def test_step_programs_are_unaffected():
    validate_program(Program(steps=[{"kind": "read", "name": "t", "locator": {"css": "h1"}}],
                             result={"$ref": "vars.t"}))
