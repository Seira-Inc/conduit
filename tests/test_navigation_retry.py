"""A transient navigation abort is retried; a real navigation failure still fails."""

import pytest
from playwright.async_api import Error as BrowserError

from conduit.programs import ProgramRunner


class FakePage:
    def __init__(self, failures, error="net::ERR_ABORTED at https://app.example.com/x"):
        self.failures, self.error, self.calls = failures, error, 0

    async def goto(self, url, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise BrowserError(self.error)
        return None


class FakeSession:
    def __init__(self, page):
        self.page = page


def runner_for(page):
    runner = ProgramRunner.__new__(ProgramRunner)
    runner.session = FakeSession(page)
    runner.observations = []
    return runner


async def test_an_aborted_navigation_is_retried_until_it_lands():
    page = FakePage(failures=2)
    runner = runner_for(page)
    await runner.navigate("https://app.example.com/x")
    assert page.calls == 3
    assert [o["operation"] for o in runner.observations] == ["navigation_retry", "navigation_retry"]


async def test_a_persistent_abort_still_raises():
    page = FakePage(failures=5)
    runner = runner_for(page)
    with pytest.raises(BrowserError):
        await runner.navigate("https://app.example.com/x")
    assert page.calls == 3


async def test_a_real_failure_is_not_retried():
    page = FakePage(failures=1, error="net::ERR_NAME_NOT_RESOLVED at https://nope.example")
    runner = runner_for(page)
    with pytest.raises(BrowserError):
        await runner.navigate("https://nope.example")
    assert page.calls == 1


async def test_a_detached_frame_is_treated_as_transient():
    page = FakePage(failures=1, error="Navigating frame was detached")
    runner = runner_for(page)
    await runner.navigate("https://app.example.com/x")
    assert page.calls == 2
