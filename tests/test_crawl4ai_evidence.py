import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import conduit.crawl4ai_evidence as crawl4ai_evidence
from conduit.evidence import EvidenceCollector, prompt_evidence

ROOT = Path(__file__).resolve().parents[1]
MARKER = "Create a disposable record here"
PAGE = (f"<html><body><h1>{MARKER}</h1>"
        "<form><input name='password' type='password' value='hunter2-should-not-leak'>"
        "<input name='title'><button>Save</button></form>"
        "<script>fetch('/api/x')</script></body></html>")


def collector(monkeypatch, *, python="py"):
    if python:
        monkeypatch.setenv("CONDUIT_CRAWL4AI_PYTHON", python)
    else:
        monkeypatch.delenv("CONDUIT_CRAWL4AI_PYTHON", raising=False)
    session = SimpleNamespace(recorder=SimpleNamespace(headers={}, secret_values=set()))
    return EvidenceCollector(session, SimpleNamespace(target="https://site.test/"))


def fake_result(markdown="# Heading\n\nAn ordinary sentence of prose that must survive intact."):
    return {"engine": "crawl4ai", "version": "stub", "markdown": markdown,
            "controls": [{"label": "Save", "type": "submit"}], "internal_model_calls": 0}


def install_extract(monkeypatch, result=None, error=None):
    calls = {"count": 0, "html": None}

    async def fake_extract(python, html, url):
        calls["count"] += 1
        calls["html"] = html
        if error:
            raise error
        return result if result is not None else fake_result()

    monkeypatch.setattr(crawl4ai_evidence, "extract", fake_extract)
    return calls


def crawl4ai_source(evidence_collector):
    return next((s for s in evidence_collector.sources if s["kind"] == "crawl4ai"), None)


async def test_enrich_adds_coherent_source_and_scrubs_secrets(monkeypatch):
    coll = collector(monkeypatch)
    calls = install_extract(monkeypatch, fake_result("# Records\n\nUse the Save button to create a record."))
    await coll.enrich_html("https://site.test/records", PAGE)
    assert calls["count"] == 1
    assert "hunter2-should-not-leak" not in calls["html"]
    status = coll.processors["crawl4ai"]
    assert status["status"] == "used" and status["model_calls"] == 0 and status["runs"] == 1
    source = crawl4ai_source(coll)
    assert source and "Use the Save button to create a record." in source["content"]
    evidence = {"page": {"text": "", "controls": []}, "sources": coll.sources, "network": []}
    rendered = prompt_evidence(evidence)
    assert "Use the Save button to create a record." in rendered


async def test_enrich_soft_fails_without_breaking_collection(monkeypatch):
    coll = collector(monkeypatch)
    install_extract(monkeypatch, error=RuntimeError("crawl4ai boom"))
    await coll.enrich_html("https://site.test/records", PAGE)
    status = coll.processors["crawl4ai"]
    assert status["status"] == "unavailable" and status["error_type"] == "RuntimeError"
    source = crawl4ai_source(coll)
    assert json.loads(coll.documents[source["url"].split("#")[0] + "#crawl4ai"])["status"] == "unavailable"


async def test_identical_page_is_cached(monkeypatch):
    coll = collector(monkeypatch)
    calls = install_extract(monkeypatch)
    await coll.enrich_html("https://site.test/records", PAGE)
    await coll.enrich_html("https://site.test/records", PAGE)
    assert calls["count"] == 1
    assert coll.processors["crawl4ai"]["cache_hits"] == 1 and coll.processors["crawl4ai"]["runs"] == 1


async def test_rejects_a_result_claiming_internal_model_calls(monkeypatch):
    coll = collector(monkeypatch)
    install_extract(monkeypatch, result={**fake_result(), "internal_model_calls": 1})
    await coll.enrich_html("https://site.test/records", PAGE)
    assert coll.processors["crawl4ai"]["status"] == "unavailable"


async def test_disabled_when_env_unset(monkeypatch):
    coll = collector(monkeypatch, python="")
    calls = install_extract(monkeypatch)
    await coll.enrich_html("https://site.test/records", PAGE)
    assert calls["count"] == 0
    assert coll.processors["crawl4ai"]["status"] == "disabled"
    assert crawl4ai_source(coll) is None


@pytest.mark.skipif(not (ROOT / ".crawl4ai-venv/Scripts/python.exe").exists(),
                    reason="Crawl4AI venv not installed")
async def test_real_subprocess_extraction():
    python = ROOT / ".crawl4ai-venv/Scripts/python.exe"
    result = await crawl4ai_evidence.extract(python, "<html><body><h1>Title</h1><p>Body text.</p></body></html>",
                                             "https://site.test/")
    assert result["engine"] == "crawl4ai" and result["internal_model_calls"] == 0
    assert "Title" in result["markdown"]
