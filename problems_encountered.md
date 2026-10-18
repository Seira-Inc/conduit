# Problems Encountered

Only problems / blockers are logged here (date only, no time). General progress goes in
`journal.md`. Each entry: what happened, cause, and status (`[OPEN]` / `[resolved]` / `[note]`).
**Newest entries first.**

Dates are local (America/Chicago).

Items with no single discovery date live under **Carried over from research** at the bottom; the
full account of each is in `docs/research/problems_encountered.md`.

## 2026-10-18 (seeding the production repo)

- **[resolved] The fast suite errored on a fresh clone: 13 fixture setup errors.** Cause: pytest's
  `--basetemp artifacts/pytest-run` needs `artifacts/` to exist, and the research repo had it only
  because of its run reports. Fixed by tracking `artifacts/.gitkeep` with a `.gitignore` exception;
  146 passed, 1 skipped afterwards.

- **[resolved] Two test modules import from `scripts/`, which was not copied.** `test_ladder.py`
  loads `ladder.py` and `verify_rest_export.py` by path; `test_forms_observer.py` imports
  `verify_forms_export` and `verify_forms_workflow`. The five files (plus `ladder.json`) were
  copied; the other thirteen research scripts were not.

- **[resolved] No venv of its own at first.** The first test run used the research venv's Python
  3.14.6. Fixed the same day: `uv venv` (uv 0.12.0, Python 3.12.13, pinned in `.python-version`),
  `uv pip install -e ".[dev]"`, Playwright's Chromium. The fast suite passes under it: 146 passed,
  1 skipped. The `requirements.lock` still comes from the research repo and has not been
  regenerated here.

- **[note] The design doc's drawings do not survive export.** `docs/SYSTEM_DESIGN.md` carries text
  versions of the loop and the Tier 0 layout; the drawn versions live only in the Claude Doc.

## Carried over from research (still open)

Each of these is described in full, with its date, in `docs/research/problems_encountered.md`. The
first five block every tier in `docs/SYSTEM_DESIGN.md`.

- **[OPEN] `browser_close` saves state before releasing, so signed-out cookies can overwrite a valid
  login.** Fix: save only after `looks_signed_in` passes (`conduit/login.py`).
- **[OPEN] A refreshed session is invisible to a running MCP server.** Fix: reload the session file
  before each call.
- **[OPEN] One client at a time, with no handoff.** `session_lock.py` is an exclusive file lock;
  fix: a lease with expiry.
- **[OPEN] The connector `notes` field ships build instructions to MCP clients**
  (`conduit/mcp_server.py:36`). Fix: strip it from the served instructions.
- **[OPEN] The front door does not exist.** Everything is stdio on localhost (`mcp_server.py`,
  `hub.py`); the dashboard is a private JSON API.
- **[OPEN] `"model_calls": 0` is a literal, not a measurement** (`conduit/programs.py:812`). The
  guarantee is structural today; make it measured.
- **[OPEN] The compiler prompt (~220 lines, `conduit/compiler.py`) is unversioned and untested.**
- **[OPEN] Truncated model output gets up to 8 closing braces added silently.**
- **[OPEN] The single-operation server hardcodes `update`** (`conduit/single_operation.py`).
- **[OPEN] Leaked browser processes** (82 logged on Oct 3); no reaper.
- **[OPEN] The form parser discards checkbox, radio, file and password forms**
  (`conduit/discovery.py`).
- **[OPEN] Every connector bundle carries a full copy of the package under `_runtime`**
  (`conduit/generator.py`).
- **[OPEN] No independent observer for Snipe-IT or Microsoft Forms** beyond the MCP gate.
- **[OPEN] A burned API key is still in the shared Drive doc.** Rotate before anything is hosted.
- **[OPEN] Unassisted canvas discovery has never completed.** Parked by design (website-first).
