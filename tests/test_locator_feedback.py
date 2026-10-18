"""A missing locator fails fast and says where the named control actually is."""

import pytest
from test_iframe import iframe_site, opened

from conduit.programs import Program, ProgramError, ProgramRunner, Step


async def run(session, manifest, *steps, result=None, timeout=20):
    program = Program(steps=list(steps), result=result, timeout_seconds=timeout)
    return await ProgramRunner(session, manifest).run(program, {})


@pytest.mark.integration
async def test_control_inside_an_unnamed_iframe_is_pointed_at(tmp_path):
    with iframe_site() as base:
        session, manifest = opened(base, tmp_path)
        try:
            await session.start()
            await session.execute("navigate", {"url": base})
            with pytest.raises(ProgramError) as failure:
                await run(session, manifest,
                          Step(kind="read", name="t", locator={"label": "Title"}, attribute="value"))
            message = str(failure.value)
            assert failure.value.code == "browser_step_failed" or failure.value.code == "locator_missing"
            assert "No element matches" in message
            assert 'iframe[id="builder"]' in message
            assert '"frame"' in message
        finally:
            await session.close()


@pytest.mark.integration
async def test_wrong_role_names_the_element_that_does_carry_the_text(tmp_path):
    with iframe_site() as base:
        session, manifest = opened(base, tmp_path)
        try:
            await session.start()
            await session.execute("navigate", {"url": base})
            with pytest.raises(ProgramError) as failure:
                await run(session, manifest,
                          Step(kind="click", locator={"role": "menuitem", "name": "All records"}))
            message = str(failure.value)
            assert "No element matches" in message
            assert "All records" in message
            assert "h1" in message
            assert "main document" in message
        finally:
            await session.close()


@pytest.mark.integration
async def test_truly_absent_text_fails_quickly_without_a_false_hint(tmp_path):
    with iframe_site() as base:
        session, manifest = opened(base, tmp_path)
        try:
            await session.start()
            await session.execute("navigate", {"url": base})
            import time
            started = time.perf_counter()
            with pytest.raises(ProgramError) as failure:
                await run(session, manifest, Step(kind="click", locator={"text": "Launch the rockets"}))
            elapsed = time.perf_counter() - started
            message = str(failure.value)
            assert "No element matches" in message
            # Nothing on the page carries that text, so no "matching" hint may be invented.
            assert "matching" not in message
            # Well under the 8 s action timeout the old path always burned.
            assert elapsed < 7
        finally:
            await session.close()


@pytest.mark.integration
async def test_ambiguous_locator_lists_the_candidates(tmp_path):
    """A strict-mode violation must carry the candidate elements, not just the count."""
    with iframe_site() as base:
        session, manifest = opened(base, tmp_path)
        try:
            await session.start()
            await session.execute("navigate", {"url": base})
            with pytest.raises(ProgramError) as failure:
                await run(session, manifest,
                          Step(kind="click", locator={"css": "input, button", "frame": 'iframe[id="builder"]'}))
            message = str(failure.value)
            assert "resolved to 2 elements" in message
            assert "1)" in message and "2)" in message
            assert "title" in message and "save" in message
        finally:
            await session.close()


@pytest.mark.integration
async def test_css_miss_names_only_iframes_that_hold_form_controls(tmp_path):
    with iframe_site(chrome=True) as base:
        session, manifest = opened(base, tmp_path)
        try:
            await session.start()
            await session.execute("navigate", {"url": base})
            with pytest.raises(ProgramError) as failure:
                await run(session, manifest, Step(kind="click", locator={"css": "#no-such-control"}))
            message = str(failure.value)
            assert 'iframe[id="builder"]' in message
            assert "chat-widget" not in message
        finally:
            await session.close()


@pytest.mark.integration
async def test_exploration_reports_every_step_and_keeps_going(tmp_path):
    """One pass yields the miss, the candidates, and what the later steps found, not just the first error."""
    with iframe_site() as base:
        session, manifest = opened(base, tmp_path)
        try:
            await session.start()
            await session.execute("navigate", {"url": base})
            program = Program(steps=[
                Step(kind="click", locator={"role": "menuitem", "name": "All records"}),
                Step(kind="click", locator={"css": "input, button", "frame": 'iframe[id="builder"]'}),
                Step(kind="read", name="heading", locator={"css": "h1"}, attribute="text"),
                Step(kind="read", name="title", locator={"css": "#title", "frame": 'iframe[id="builder"]'},
                     attribute="value"),
            ], timeout_seconds=30)
            outcomes = await ProgramRunner(session, manifest).explore(program)
            assert [o["status"] for o in outcomes] == ["locator_missing", "browser_step_failed", "ok", "ok"]
            assert "h1" in outcomes[0]["error"]
            assert "resolved to 2 elements" in outcomes[1]["error"]
            assert outcomes[2]["result"] == "All records"
            assert outcomes[3]["result"] == ""
        finally:
            await session.close()


@pytest.mark.integration
async def test_exploration_waits_for_an_iframe_a_click_injects_later(tmp_path):
    """The create panel on a heavy frontend arrives after the click; the next step must see it."""
    with iframe_site() as base:
        session, manifest = opened(base, tmp_path)
        try:
            await session.start()
            await session.execute("navigate", {"url": base + "/lazy"})
            assert len(session.page.frames) == 1
            outcomes = await ProgramRunner(session, manifest).explore(Program(steps=[
                Step(kind="click", locator={"text": "Create new"}),
                Step(kind="read", name="fields", locator={"css": "input", "frame": 'iframe[id="builder"]'},
                     attribute="count"),
                Step(kind="read", name="label", locator={"css": "label", "frame": 'iframe[id="builder"]'},
                     attribute="text"),
            ], timeout_seconds=30))
            assert [o["status"] for o in outcomes] == ["ok", "ok", "ok"]
            assert outcomes[1]["result"] == 1
            assert outcomes[2]["result"] == "Title"
            snapshot = await session.snapshot()
            assert [f["selector"] for f in snapshot["frames"]] == ['iframe[id="builder"]']
            assert snapshot["frames"][0]["controls"] == 2
        finally:
            await session.close()


@pytest.mark.integration
async def test_exploration_refuses_writes(tmp_path):
    with iframe_site() as base:
        session, manifest = opened(base, tmp_path)
        try:
            await session.start()
            await session.execute("navigate", {"url": base})
            with pytest.raises(ValueError):
                await ProgramRunner(session, manifest).explore(Program(steps=[
                    Step(kind="fill", locator={"css": "#title", "frame": 'iframe[id="builder"]'}, value="x")]))
        finally:
            await session.close()


@pytest.mark.integration
async def test_count_read_of_nothing_is_zero_not_an_error(tmp_path):
    with iframe_site() as base:
        session, manifest = opened(base, tmp_path)
        try:
            await session.start()
            await session.execute("navigate", {"url": base})
            value, _ = await run(session, manifest,
                                 Step(kind="read", name="n", locator={"css": "table"}, attribute="count"),
                                 result={"$ref": "vars.n"})
            assert value == 0
        finally:
            await session.close()


@pytest.mark.integration
async def test_navigate_then_act_works_on_the_first_try(tmp_path):
    """The settle after navigation means a program can navigate and immediately use the page."""
    with iframe_site() as base:
        session, manifest = opened(base, tmp_path)
        try:
            await session.start()
            value, _ = await run(session, manifest,
                                 Step(kind="navigate", url=base),
                                 Step(kind="read", name="h", locator={"css": "h1"}, attribute="text"),
                                 result={"$ref": "vars.h"})
            assert value == "All records"
        finally:
            await session.close()
