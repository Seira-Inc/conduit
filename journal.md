# Conduit Journal

Running log of the production Conduit. **Newest entries first.** Append at the top.
Problems and blockers are logged separately in `problems_encountered.md`.

**Dates and times are local (America/Chicago).**

This repo started clean on 2026-10-18 from the research repo, which stays at
`startup/conduit` with no remote and 50 commits. Its journal, with the thirteen standing decisions
and the full history of who built what, is kept here as `docs/research/journal.md`; its open
problems as `docs/research/problems_encountered.md`. The design this repo builds toward is
`docs/SYSTEM_DESIGN.md` (exported from the live Claude Doc, which also holds the two drawings and
the comment threads).

## Standing decisions

The thirteen research decisions carry over (`docs/research/journal.md`, "Standing decisions from
Iminabo"). Iminabo amended and extended them on 2026-10-18, in `docs/SYSTEM_DESIGN.md` §10:

1. **Approval.** The client's own card (ChatGPT, Claude) plus Seira elicitation before delete and
   irreversible actions. Decision #4, "connected means approved", stays as the permission rule; the
   card is a safety step, not a permission.
2. **Sessions.** v1 keeps sessions on the user's PC (Tier 0). Cloud sessions are a stretch opt-in in
   v1 and the default from v2 onward.
3. **Atomic vs shortest.** Atomic families by default; the site's own composite action when its
   semantics differ, when composing would take more than five calls (a working number, still to be
   tested), or when no family can express it.
4. **Codebase engine.** Out of scope for v1. The product is the website engine only.
5. **Access.** The site's own permissions decide what a person can reach; a resource they cannot
   open gets no tool and no operation. An admin blocks or revokes one person's access to one item,
   never a whole site. A site whose terms forbid automation is still built for; the legal exposure
   is an open question for experts.
6. **Tier 0 runs local-only first.** No tunnel, no VPS: gateway and runtime on one machine until
   the loop works end to end. Customer zero is Iminabo until the others are free.

## 2026-10-18 — the production repo is seeded and the design is written

**The repo.** `Seira-Inc/conduit` on GitHub, private, branch `imi`; `main` holds only the initial
commit. Local folder `startup/prod/conduit`. Iminabo's call: start clean, do not carry the research
commits, copy what is necessary.

**What was copied.** The `conduit/` package (47 files, including the dashboard's `static/`), all 49
test files, `pyproject.toml`, `requirements.lock`, `.gitignore`, `.env.example`, and five scripts
the tests load (`ladder.py`, `ladder.json`, `verify_rest_export.py`, `verify_forms_export.py`,
`verify_forms_workflow.py`). Under `docs/research/`: the research `journal.md`,
`problems_encountered.md`, `HANDOFF.md` and `COMPILER_VALIDATION.md`, because the design cites
their numbers and open items.

**What was left behind.** The other thirteen research scripts, `artifacts/` (171 tracked reports
and ladder runs), the dashboard launchers (`start.ps1`, `Launch Conduit.cmd`), and the research
write-ups (`BUSINESS_TARGET.md`, `MILESTONE_AUDIT.md`, `HOLY_SLACK_REVIEW.md`, `VALIDATION.md`,
the four `CRAWL4AI_*` and `HANDOFF_CRAWL4AI.md` files). All still readable in the research repo.

**The design.** Written today as a Claude Doc, after reading every Notion page, the whole Drive
folder, and the Oct 5 and Oct 12 meeting transcripts. Spine: the just-in-time loop, tag → find →
build → verify → ask → run → confirm → keep, each step grounded in the file that does it today.
Three budget tiers: Tier 0 (free, hackathon: the existing runtime as a local agent on the user's PC,
a tiny gateway holding the front door and the registry), Tier 1 (about $50, per-company container,
cloud sessions over noVNC), Tier 2 (about $200, sandbox per call, signed fingerprints, audit).
Iminabo rejected a first draft that was architecture-first; the doc had to start from what Conduit
does and keep the loop as its spine.

**Decisions made today** are the six under Standing decisions above, plus one comment thread on the
doc: the composition threshold moved from three calls to five, to be tested.

**Tests.** The fast suite (`-m "not integration and not desktop and not live_model"`) passes in the
new folder: 146 passed, 1 skipped. First under the research venv, then under this repo's own venv,
made with `uv venv` (uv 0.12.0, Python 3.12.13, pinned in `.python-version`) and
`uv pip install -e ".[dev]"`; the team does not use pip directly. Two things had to change for the
suite to run here: `artifacts/` must exist for pytest's `--basetemp`, so `artifacts/.gitkeep` is
tracked; and the five scripts above had to come along.

**Next.** The Tier 0 front door: a Streamable HTTP MCP server on localhost that lists verified tools
from a registry and runs them through the existing runtime, plus the three meta tools
(`seira_find`, `seira_build`, `seira_status`). Nothing of it exists yet; `hub.py` over stdio is the
nearest thing.
