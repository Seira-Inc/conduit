import time

import pytest

from conduit.browser import BrowserSession, browser_tools
from conduit.login import looks_signed_out
from conduit.models import Manifest
from conduit.runtime import Runtime
from conduit.secrets import load_session, save_session
from conduit.session_lock import SessionBusy, SessionLease

SIGNED_IN = {"password_inputs": 0, "sign_in_controls": 0, "sign_out_controls": 1, "text": ""}
SIGNED_OUT = {"password_inputs": 1, "sign_in_controls": 1, "sign_out_controls": 0, "text": ""}


def manifest():
    return Manifest(id="demo", name="Demo", kind="website", target="https://example.com",
                    allowed_origins=["https://example.com"], tools=browser_tools())


class FakePage:
    def __init__(self, facts):
        self.facts, self.url = facts, "https://example.com/app"

    def is_closed(self):
        return False

    async def evaluate(self, script):
        return self.facts

    async def title(self):
        return "App"


class FakeContext:
    def __init__(self, state):
        self.state = state

    async def storage_state(self, indexed_db=True):
        return self.state


class FakeBrowser:
    def __init__(self, changed=False):
        self.context, self.manual_login, self.changed, self.closed = object(), False, changed, 0

    def session_changed_on_disk(self):
        return self.changed and self.context is not None

    async def close(self):
        self.closed += 1
        self.context = None


def test_an_idle_holder_loses_the_lease_and_learns_it_on_its_next_call(tmp_path):
    path = tmp_path / "locks" / "demo.lock"
    first = SessionLease(path, ttl=0.2)
    first.acquire()
    second = SessionLease(path, ttl=0.2)
    with pytest.raises(SessionBusy, match="held by another client"):
        second.acquire()
    assert first.renew()
    time.sleep(0.25)
    second.acquire()
    assert not first.renew()
    first.release()
    assert path.exists()
    second.release()
    assert not path.exists()


def test_signed_out_is_judged_only_on_evidence():
    assert looks_signed_out(SIGNED_OUT) == "a password field is visible"
    assert looks_signed_out({"sign_in_controls": 1}) is not None
    assert looks_signed_out(SIGNED_IN) is None and looks_signed_out({}) is None


async def test_save_never_overwrites_a_stored_login_with_a_signed_out_state(tmp_path):
    session = BrowserSession(manifest(), tmp_path)
    good = {"cookies": [{"name": "sid", "value": "good"}], "origins": []}
    save_session(session.session_file, good)
    session.context = FakeContext({"cookies": [], "origins": []})
    session.page = FakePage(SIGNED_OUT)
    assert await session.save() is False
    assert load_session(session.session_file) == good and "password" in session.last_save_refusal
    session.page = FakePage(SIGNED_IN)
    session.context = FakeContext({"cookies": [{"name": "sid", "value": "newer"}], "origins": []})
    assert await session.save() is True
    assert load_session(session.session_file)["cookies"][0]["value"] == "newer"
    assert session.last_save_refusal is None and not session.session_changed_on_disk()


async def test_a_session_refreshed_on_disk_restarts_the_context_before_the_next_call(tmp_path):
    runtime = Runtime(manifest(), tmp_path)
    runtime.browser = FakeBrowser(changed=True)
    await runtime.acquire_browser()
    assert runtime.browser.closed == 1 and runtime.session_lock.held
    await runtime.close(save=False)
    assert runtime.session_lock is None


async def test_a_holder_that_lost_its_lease_hands_the_browser_over(tmp_path):
    runtime = Runtime(manifest(), tmp_path)
    runtime.browser = FakeBrowser()
    await runtime.acquire_browser()
    other = SessionLease(runtime.session_lock.path)
    runtime.session_lock.path.unlink()
    other.acquire()
    with pytest.raises(SessionBusy):
        await runtime.acquire_browser()
    assert runtime.session_lock is None and runtime.browser.closed == 1
    other.release()
    await runtime.acquire_browser()
    assert runtime.session_lock.held
    await runtime.close()
    assert not runtime.session_lock
