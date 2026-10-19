"""The checker behind seira_find: when the deterministic match cannot decide, one small model call
ranks the candidates against the request. It never builds anything."""

from __future__ import annotations

import json

from .agent import model_settings
from .compiler_model import infer, infer_anthropic, infer_http

SYSTEM = ("You match a user's request to one of the listed tools, or to none. Answer with JSON only: "
          '{"tool": <the tool name or null>, "why": <one short sentence>}. Choose null when no tool fits '
          "or when two fit equally; never invent a name.")


async def ask_model(prompt: str, home):
    settings = {**model_settings(), "max_tokens": 300, "effort": "low", "structured": False}
    if settings["backend"] == "anthropic":
        return await infer_anthropic(settings, SYSTEM, prompt, False)
    if settings["backend"] != "codex":
        return await infer_http(settings, SYSTEM, prompt, False)
    return await infer(prompt, home, system=SYSTEM)


async def rerank(request: str, candidates: list, home, *, choose=None) -> dict | None:
    """The candidate the model picks, or None. Candidates are registry entries."""
    listed = [{"tool": e.name, "site": e.site, "resource": e.label or e.resource, "verb": e.verb,
               "description": e.description} for e in candidates]
    prompt = json.dumps({"request": request, "tools": listed}, ensure_ascii=False)
    payload, _stats = await (choose or ask_model)(prompt, home)
    name = payload.get("tool") if isinstance(payload, dict) else None
    entry = next((e for e in candidates if e.name == name), None)
    if entry is None:
        return None
    return {"status": "found", "tool": entry.name, "connector_id": entry.connector_id, "site": entry.site,
            "resource": entry.resource, "verb": entry.verb, "read_only": entry.read_only,
            "has_session": entry.has_session, "why": "checker: " + str(payload.get("why", ""))[:200]}
