import pytest

from conduit.browser import browser_tools
from conduit.models import Manifest
from conduit.runtime import Runtime
from conduit.session_lock import SessionLease


def test_session_excludes_concurrent_clients(tmp_path):
    first, second = SessionLease(tmp_path / "test.lock"), SessionLease(tmp_path / "test.lock")
    first.acquire()
    try:
        with pytest.raises(ValueError, match="another client"):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()


@pytest.mark.integration
async def test_runtime_saves_state_and_releases_session(local_site, tmp_path):
    url, _ = local_site
    manifest = Manifest(
        id="persist",
        name="Persist",
        kind="website",
        target=url + "/login",
        allowed_origins=[url],
        allow_private=True,
        tools=browser_tools(),
    )
    runtime = Runtime(manifest, tmp_path)
    other = Runtime(manifest, tmp_path)
    try:
        snap = await runtime.call("browser_snapshot", {})
        with pytest.raises(ValueError, match="another client"):
            await other.call("browser_snapshot", {})
        ref = next(c["ref"] for c in snap["controls"] if c["name"] == "Sign in")
        await runtime.call("browser_click", {"ref": ref})
        await runtime.call("browser_close", {})
        manifest.target = url + "/session"
        snap = await other.call("browser_snapshot", {})
        assert "Signed in" in snap["text"]
    finally:
        await runtime.close()
        await other.close()
