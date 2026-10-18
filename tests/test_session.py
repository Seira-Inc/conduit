import asyncio

import pytest

from conduit.browser import BrowserSession, browser_tools
from conduit.models import Manifest
from conduit.secrets import load_session, save_session


@pytest.mark.integration
async def test_login_persists_isolated_session(local_site, tmp_path):
    url, _ = local_site
    manifest = Manifest(
        id="login-test",
        name="Login",
        kind="website",
        target=url + "/login",
        allowed_origins=[url],
        allow_private=True,
        tools=browser_tools(),
    )
    browser = BrowserSession(manifest, tmp_path)
    try:
        snap = await browser.snapshot()
        ref = next(c["ref"] for c in snap["controls"] if c["name"] == "Sign in")
        await browser.execute("click", {"ref": ref})
        await browser.save()
        assert b"present" not in browser.session_file.read_bytes() if __import__("os").name == "nt" else True
    finally:
        await browser.close()
    manifest.target = url + "/session"
    restored = BrowserSession(manifest, tmp_path)
    try:
        assert "Signed in" in (await restored.snapshot())["text"]
    finally:
        await restored.close()
    isolated = BrowserSession(manifest.model_copy(update={"id": "another-connector"}), tmp_path)
    try:
        assert "Signed out" in (await isolated.snapshot())["text"]
    finally:
        await isolated.close()


@pytest.mark.integration
async def test_login_checkpoint_survives_browser_disconnection(local_site, tmp_path):
    url, _ = local_site
    manifest = Manifest(id="checkpoint", name="Login", kind="website", target=url + "/login",
                        allowed_origins=[url], allow_private=True, tools=browser_tools())
    browser = BrowserSession(manifest, tmp_path)
    browser.manual_login = True
    try:
        await browser.start()
        await browser.page.get_by_role("button", name="Sign in").click()
        async with asyncio.timeout(10):
            while not any(cookie["name"] == "test_session"
                          for cookie in (load_session(browser.session_file) or {}).get("cookies", [])):
                await asyncio.sleep(0.05)
        # The user never pressed Finish: the whole browser disappears unexpectedly.
        await browser.browser.close()
        assert await browser.finish_login() == "checkpoint_recovered"
    finally:
        await browser.close()
    restored = BrowserSession(manifest.model_copy(update={"target": url + "/session"}), tmp_path)
    try:
        assert "Signed in" in (await restored.snapshot())["text"]
    finally:
        await restored.close()


@pytest.mark.integration
async def test_closed_login_does_not_claim_an_older_session_was_saved(local_site, tmp_path, monkeypatch):
    url, _ = local_site
    manifest = Manifest(id="checkpoint-old", name="Login", kind="website", target=url + "/login",
                        allowed_origins=[url], allow_private=True, tools=browser_tools())
    browser = BrowserSession(manifest, tmp_path)
    save_session(browser.session_file, {"cookies": [], "origins": []})
    browser.manual_login = True

    async def interrupted_before_first_save():
        await asyncio.Event().wait()

    monkeypatch.setattr(browser, "_checkpoint_login", interrupted_before_first_save)
    try:
        await browser.start()
        await browser.browser.close()
        with pytest.raises(RuntimeError, match="Sign in again"):
            await browser.finish_login()
    finally:
        await browser.close()


async def wait_for_cookie(browser, name="test_session"):
    async with asyncio.timeout(10):
        while not any(cookie["name"] == name
                      for cookie in (load_session(browser.session_file) or {}).get("cookies", [])):
            yield
            await asyncio.sleep(0.05)


@pytest.mark.integration
async def test_login_checkpoints_never_dump_storage_state_or_open_tabs(local_site, tmp_path, monkeypatch):
    url, _ = local_site
    manifest = Manifest(id="cheap-checkpoint", name="Login", kind="website", target=url + "/login",
                        allowed_origins=[url], allow_private=True, tools=browser_tools())
    browser = BrowserSession(manifest, tmp_path)
    browser.manual_login = True
    try:
        await browser.start()

        async def forbidden(**kwargs):
            raise AssertionError("storage_state must not run while the user is signing in")

        monkeypatch.setattr(browser.context, "storage_state", forbidden)
        await browser.page.get_by_role("button", name="Sign in").click()
        async for _ in wait_for_cookie(browser):
            assert len(browser.context.pages) == 1
        assert len(browser.context.pages) == 1
    finally:
        await browser.close()


@pytest.mark.integration
async def test_finish_login_recovers_checkpoint_when_final_save_hangs(local_site, tmp_path, monkeypatch):
    url, _ = local_site
    manifest = Manifest(id="hung-save", name="Login", kind="website", target=url + "/login",
                        allowed_origins=[url], allow_private=True, tools=browser_tools())
    browser = BrowserSession(manifest, tmp_path)
    browser.manual_login = True
    browser.final_save_timeout = 2
    try:
        await browser.start()
        await browser.page.get_by_role("button", name="Sign in").click()
        async for _ in wait_for_cookie(browser):
            pass

        async def hang(**kwargs):
            await asyncio.Event().wait()

        monkeypatch.setattr(browser.context, "storage_state", hang)
        assert await browser.finish_login() == "checkpoint_recovered"
    finally:
        await browser.close()
    restored = BrowserSession(manifest.model_copy(update={"target": url + "/session"}), tmp_path)
    try:
        assert "Signed in" in (await restored.snapshot())["text"]
    finally:
        await restored.close()
