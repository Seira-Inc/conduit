"""Optional local Crawl4AI processing of already-scrubbed page evidence."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


async def extract(python: Path, html: str, url: str):
    process = await asyncio.create_subprocess_exec(str(python), str(Path(__file__).resolve()),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(
            json.dumps({"html": html, "url": url}).encode()), timeout=90)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise RuntimeError("Crawl4AI extraction failed: " + stderr.decode(errors="replace")[-800:])
    return json.loads(stdout)


def worker():
    import importlib.metadata

    from crawl4ai.extraction_strategy import JsonCssExtractionStrategy
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    payload = json.load(sys.stdin)
    html, url = payload["html"], payload["url"]
    markdown = DefaultMarkdownGenerator().generate_markdown(html, base_url=url).raw_markdown
    schema = {"name": "Interface controls", "baseSelector": "button,input,textarea,select,[role=button]",
        "fields": [{"name": "label", "type": "text"}] + [
            {"name": key, "type": "attribute", "attribute": key}
            for key in ("id", "name", "type", "aria-label", "data-testid", "title")]}
    controls = JsonCssExtractionStrategy(schema).run(url, [html])
    json.dump({"engine": "crawl4ai", "version": importlib.metadata.version("crawl4ai"),
               "markdown": markdown[:14000], "controls": controls[:120], "internal_model_calls": 0}, sys.stdout)


if __name__ == "__main__":
    worker()
