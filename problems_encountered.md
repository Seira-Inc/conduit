# Problems Encountered

Only problems / blockers are logged here (date only, no time). General progress goes in
`journal.md`. Each entry: what happened, cause, and status (`[OPEN]` / `[resolved]` / `[note]`).
**Newest entries first.**

Dates are local (America/Chicago).

Items with no single discovery date live under **Carried over from research** at the bottom; the
full account of each is in `docs/research/problems_encountered.md`.

## 2026-10-18 (the front door)

- **[resolved] The connector `notes` field shipped build instructions to MCP clients.** Three serve
  paths carried it: the per-connector server's instructions (`mcp_server.py`), its
  `conduit://discovery` resource, and `GET /api/connectors/{id}`, which the hub reads. All three
  now omit it; the gateway never reads it. A gateway build passes its request to the compiler as a
  `brief` argument instead of writing it to the manifest. `test_tool_results.py`,
  `test_dashboard.py` and `test_gateway.py` assert every served surface is free of a marker note.

- **[resolved] The front door did not exist.** `conduit ui` now also serves a Streamable HTTP MCP
  endpoint at `/mcp` (`conduit/gateway.py`, mounted in `app.py`), listing verified program tools
  across connectors from an mtime-refreshed index (`conduit/registry.py`) and running them in
  process. Claude Code connects with `claude mcp add --transport http seira http://127.0.0.1:4455/mcp`.

- **[resolved] `/api/connectors/{id}/call` dropped `object_id`, `postcondition` and
  `execution_observations` from a failed write**, the fields `mcp_check.verify_export` reads to
  decide whether a create is uncertain; the hub proxies that route, so it lost them too. One
  function now shapes every surface's error (`conduit/tool_results.py`).

- **[note] A cached runtime could keep running a program the CLI had just replaced.** `runtime_for`
  now compares `connector.json`'s mtime and reloads when nothing is running on that connector.
  Covered by `test_runtime_reloads_when_connector_json_changes`.

- **[OPEN] Progress heartbeat not implemented.** UI-driven tools take 12 to 48 s and the client's
  tool-call timeout is unmeasured. The planned 10-second `progress` notification was cut for time;
  measure the timeout on the first UI-driven run before deciding whether it is needed.

- **[OPEN] HubSpot `contacts_read` of a nonexistent id returns a record with null fields instead of
  `object_missing`.** Found on the first live call through the gateway (17.5 s, cold browser): the
  receipt broke the tool's own output schema (`email` must be a string) and the SDK client threw
  the whole result away. Dropping the structured part is no fix either: the client discards a
  non-error result whose tool declares a schema but returns no structured content. The gateway
  now reports such a receipt as an error, code `receipt_schema_mismatch`, carrying the receipt
  (`test_a_receipt_that_breaks_its_schema_is_an_error_that_keeps_the_receipt`); the client only
  validates non-error results. On id `000000000` the name it read back was the title of HubSpot's
  login page, so the program does not check that it landed on a record page. The read program
  needs that check and a not-found result; it is a research-era connector, not gateway code.

- **[note] The research HubSpot connector is named "Loading https://app.hubspot.com/login".**
  Discovery took the page title while the login page was loading, and the gateway now shows that
  name in every HubSpot tool description. Rename the manifest; discovery should ignore
  loading-state titles.

- **[note] `seira_build` refused to see an existing HubSpot tool** because the user said
  `app.hubspot.com` and the connector's target is `app-na2.hubspot.com`. Site matching now falls
  back to the domain label when it names exactly one known site.

- **[note] Browser-based MCP clients are refused by the Origin rule.** The endpoint allows no
  `Origin` (Claude Code, a Node process) or a localhost one; MCP Inspector in a browser gets 403.
  Intended.

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
- **[resolved 2026-10-18] The connector `notes` field shipped build instructions to MCP clients.**
  See the front-door entry above.
- **[resolved 2026-10-18] The front door did not exist.** See the front-door entry above.
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
