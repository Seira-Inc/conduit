"""An app whose create form lives in a nested iframe, like HubSpot's object builder."""

import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conduit.browser import BrowserSession, frame_selector
from conduit.evidence import EvidenceCollector
from conduit.models import Manifest
from conduit.programs import Program, ProgramError, ProgramRunner, Step, frame_chain

PARENT = """<!doctype html><title>Records</title><h1>All records</h1>
<iframe id="builder" name="builderFrame" src="/embed" width="600" height="400"></iframe>"""

CHROME = """<iframe id="chat-widget" src="/chrome" width="300" height="200"></iframe>"""

EMBED = """<!doctype html><title>Create record</title>
<form id="create"><label for="title">Title</label><input id="title" name="title">
<button type="submit" id="save">Create record</button></form>
<p id="saved">none</p>"""

WIDGET = """<!doctype html><title>Chat</title><p>Need help? Chat with us.</p>"""

LAZY = """<!doctype html><title>Records</title><h1>All records</h1>
<button id="open">Create new</button>
<script>
document.getElementById('open').addEventListener('click', () => {
  setTimeout(() => {
    const f = document.createElement('iframe');
    f.id = 'builder'; f.src = '/embed'; f.width = 600; f.height = 400;
    document.body.appendChild(f);
  }, 900);
});
</script>"""


@contextmanager
def iframe_site(chrome=False):
    """A record app whose create form is an iframe; `chrome` adds a controls-free widget iframe too."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path.startswith("/embed"):
                body = EMBED
            elif self.path.startswith("/chrome"):
                body = WIDGET
            elif self.path.startswith("/lazy"):
                body = LAZY
            else:
                body = PARENT + (CHROME if chrome else "")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def opened(base, home):
    manifest = Manifest(id="iframe-fixture", name="Iframe fixture", kind="website",
                        target=base, allowed_origins=[base], allow_private=True)
    return BrowserSession(manifest, home), manifest


def test_frame_chain_normalizes_and_bounds():
    assert frame_chain({}) == []
    assert frame_chain({"frame": None}) == []
    assert frame_chain({"frame": "iframe#a"}) == ["iframe#a"]
    assert frame_chain({"frame": ["iframe#a", "iframe#b"]}) == ["iframe#a", "iframe#b"]
    with pytest.raises(ValueError):
        frame_chain({"frame": ["a", "b", "c", "d"]})
    with pytest.raises(ValueError):
        frame_chain({"frame": [""]})
    with pytest.raises(ValueError):
        frame_chain({"frame": [7]})


def test_frame_selector_prefers_stable_attributes():
    assert frame_selector({"id": "builder", "name": "n", "src": "/embed"}) == 'iframe[id="builder"]'
    assert frame_selector({"id": "", "name": "n", "src": "/embed"}) == 'iframe[name="n"]'
    assert frame_selector({"id": "", "name": "", "src": "/embed"}) == 'iframe[src="/embed"]'
    assert frame_selector({"id": "", "name": "", "src": ""}) == "iframe"


@pytest.mark.integration
async def test_locator_reaches_a_control_inside_an_iframe(tmp_path):
    with iframe_site() as base:
        session, manifest = opened(base, tmp_path)
        try:
            await session.start()
            await session.execute("navigate", {"url": base})
            runner = ProgramRunner(session, manifest)

            # Without a frame the control is invisible to the top-level document.
            bare = Program(steps=[Step(kind="read", name="t", locator={"label": "Title"}, attribute="value")],
                           result={"$ref": "vars.t"}, timeout_seconds=8)
            with pytest.raises(ProgramError):
                await runner.run(bare, {})

            inside = Program(steps=[
                Step(kind="fill", locator={"label": "Title", "frame": 'iframe[id="builder"]'}, value="Conduit one"),
                Step(kind="read", name="t", locator={"css": "#title", "frame": 'iframe[id="builder"]'},
                     attribute="value"),
            ], result={"$ref": "vars.t"})
            value, _ = await runner.run(inside, {})
            assert value == "Conduit one"
        finally:
            await session.close()


@pytest.mark.integration
async def test_snapshot_lists_frames_and_their_controls(tmp_path):
    with iframe_site() as base:
        session, _ = opened(base, tmp_path)
        try:
            await session.start()
            await session.execute("navigate", {"url": base})
            snapshot = await session.snapshot()
            assert snapshot["frames"] == [
                {"selector": 'iframe[id="builder"]', "url": base + "/embed", "controls": 2}
            ]
            inner = [c for c in snapshot["controls"] if c.get("frame")]
            assert {c["frame"] for c in inner} == {'iframe[id="builder"]'}
            assert any(c["name"] == "Create record" for c in inner)
            assert len({c["ref"] for c in snapshot["controls"]}) == len(snapshot["controls"])
        finally:
            await session.close()


@pytest.mark.integration
async def test_evidence_includes_the_iframe_document(tmp_path):
    with iframe_site() as base:
        session, manifest = opened(base, tmp_path)
        try:
            await session.start()
            await session.execute("navigate", {"url": base})
            collector = EvidenceCollector(session, manifest)
            await collector.collect()
            frames = [s for s in collector.sources if s["kind"] == "frame"]
            assert len(frames) == 1
            assert 'locator frame selector: iframe[id="builder"]' in frames[0]["content"]
            assert 'id="title"' in frames[0]["content"]
            top = next(s for s in collector.sources if s["kind"] == "page")
            assert 'id="title"' not in top["content"]
        finally:
            await session.close()
