import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import pytest
from playwright.async_api import Error as PlaywrightError

from conduit.browser import BrowserSession, browser_tools
from conduit.login import confirm, fingerprint, looks_signed_in, scripted_login
from conduit.models import Manifest

FORM = (b"<!doctype html><title>Members</title><form method='post' action='/login'>"
        b"<label>Email address<input type='email' name='email'></label>"
        b"<label>Password<input type='password' name='password'></label>"
        b"<button type='submit'>Log in</button></form>")


@pytest.fixture
def form_site():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def page(self, body, status=200, cookie=None):
            self.send_response(status)
            self.send_header("Content-Type", "text/html")
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/home" and "member=ok" in self.headers.get("Cookie", ""):
                return self.page(b"<!doctype html><title>Home</title><h1>Welcome tester</h1><button>Log out</button>")
            if self.path == "/quick":
                return self.page(b"<!doctype html><title>Demo</title><select><option>Administrator</option></select>"
                                 b"<button onclick=\"document.cookie='member=ok;path=/';location.href='/home'\">"
                                 b"Login</button>")
            self.page(FORM)

        def do_POST(self):
            data = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
            if data.get("email") == ["tester@example.com"] and data.get("password") == ["pw"]:
                self.send_response(302)
                self.send_header("Set-Cookie", "member=ok; Path=/")
                self.send_header("Location", "/home")
                return self.end_headers()
            self.page(FORM + b"<p>Wrong password</p>", 401)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


@pytest.mark.integration
async def test_scripted_login_fills_a_plain_form_and_verifies(form_site, tmp_path):
    manifest = Manifest(id="form-login", name="Members", kind="website", target=form_site + "/home",
                        allowed_origins=[form_site], allow_private=True, tools=browser_tools())
    browser = BrowserSession(manifest, tmp_path)
    try:
        wrong = await scripted_login(browser, user="tester@example.com", password="nope")
        assert not wrong["verified"] and wrong["after"]["password_inputs"] == 1
        check = await scripted_login(browser, user="tester@example.com", password="pw")
        assert check["verified"] and check["reason"] == "a sign-out control is visible"
        assert check["mode"] == "scripted" and "pw" not in str(check)
    finally:
        await browser.close()
    demo = BrowserSession(manifest.model_copy(update={"id": "click-login"}), tmp_path)
    try:
        check = await scripted_login(demo, login_url=form_site + "/quick", submit="^login$", click_only=True,
                                     success_text="Welcome tester")
        assert check["verified"] and check["reason"] == "the page shows 'Welcome tester'"
    finally:
        await demo.close()
    restored = BrowserSession(manifest, tmp_path)
    try:
        assert "Welcome tester" in (await restored.snapshot())["text"]
    finally:
        await restored.close()

BLANK = {"password_inputs": 0, "sign_in_controls": 0, "sign_out_controls": 0, "text": ""}


def facts(**overrides):
    return {**BLANK, **overrides}


def test_looks_signed_in_needs_positive_evidence():
    assert looks_signed_in(None, facts(sign_out_controls=1)) == (True, "a sign-out control is visible")
    assert not looks_signed_in(None, facts(password_inputs=1, sign_out_controls=1))[0]
    assert looks_signed_in(facts(password_inputs=1), facts())[0]
    assert not looks_signed_in(facts(sign_in_controls=1), facts())[0]
    verified, reason = looks_signed_in(None, facts())
    assert not verified and "--logged-in-text" in reason
    assert looks_signed_in(None, facts(text="Welcome back, Ada"), text="welcome BACK")[0]
    assert not looks_signed_in(None, facts(sign_out_controls=1, text="Signed out"), text="My forms")[0]
    assert looks_signed_in(None, facts(), selector_found=True)[0]
    assert not looks_signed_in(None, facts(sign_out_controls=1), selector_found=False)[0]


def manifest_for(url, identifier):
    return Manifest(id=identifier, name="Login", kind="website", target=url + "/login",
                    allowed_origins=[url], allow_private=True, tools=browser_tools())


@pytest.mark.integration
async def test_login_check_flips_only_after_a_real_sign_in(local_site, tmp_path):
    url, _ = local_site
    browser = BrowserSession(manifest_for(url, "login-check"), tmp_path)
    try:
        await browser.start()
        before = await fingerprint(browser.page)
        assert before["sign_in_controls"] == 1 and before["password_inputs"] == 0
        unverified = await confirm(browser, before, text="Signed in")
        assert not unverified["verified"] and "text" not in unverified["after"]
        await browser.page.get_by_role("button", name="Sign in").click()
        verified = await confirm(browser, before, text="Signed in")
        assert verified["verified"] and verified["after"]["sign_in_controls"] == 0
        assert (await confirm(browser, before, selector="h1"))["verified"]
        heuristic = await confirm(browser, before)
        assert not heuristic["verified"] and "--logged-in-text" in heuristic["reason"]
    finally:
        await browser.close()


@pytest.mark.integration
async def test_watch_for_sign_in_returns_once_the_page_flips(local_site, tmp_path):
    import asyncio

    from conduit.login import watch_for_sign_in

    url, _ = local_site
    browser = BrowserSession(manifest_for(url, "watch-login"), tmp_path)
    try:
        await browser.start()
        before = await fingerprint(browser.page)
        assert not await watch_for_sign_in(browser, before, 1, text="Signed in")

        async def sign_in_later():
            await asyncio.sleep(1.5)
            await browser.page.get_by_role("button", name="Sign in").click()

        task = asyncio.create_task(sign_in_later())
        assert await watch_for_sign_in(browser, before, 15, text="Signed in")
        await task
    finally:
        await browser.close()


@pytest.mark.integration
async def test_manual_login_records_identity_provider_origins(local_site, tmp_path):
    url, _ = local_site
    alias = url.replace("127.0.0.1", "localhost")
    manifest = manifest_for(url, "idp-origins")
    browser = BrowserSession(manifest, tmp_path)
    browser.manual_login = True
    try:
        await browser.start()
        await browser.page.goto(alias + "/session")
        await browser.page.goto(url + "/login")
    finally:
        await browser.close()
    assert manifest.login_origins == [alias]
    assert manifest.allowed_origins == [url]
    assert {"type": "login_origin", "origin": alias} in manifest.findings
    later = BrowserSession(manifest, tmp_path)
    try:
        await later.start()
        response = await later.page.goto(alias + "/session")
        assert response is not None and response.ok
    finally:
        await later.close()
    blocked = BrowserSession(manifest.model_copy(update={"login_origins": []}), tmp_path)
    try:
        await blocked.start()
        with pytest.raises(PlaywrightError):
            await blocked.page.goto(alias + "/session")
    finally:
        await blocked.close()
