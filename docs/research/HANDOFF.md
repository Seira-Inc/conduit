# Conduit handoff (2026-09-29)

> **Stale as of October 2. Read [`journal.md`](journal.md) first.**
>
> This file is no longer the running history. The project history now lives in two append-only
> living docs, newest first, matching the EUPHRATES convention:
>
> - **[`journal.md`](journal.md)** — what happened, by day, Sept 9 through Oct 4. Includes the
>   standing decisions from Iminabo that are load-bearing in the code, the twenty HubSpot compile
>   attempts with what each died on and cost, and the sixteen compiler fixes they bought.
> - **[`problems_encountered.md`](problems_encountered.md)** — open defects and resolved blockers,
>   with cause and status on each. **17 open items**, including four session-ownership bugs found
>   only when an outside MCP client first drove a connector.
>
> Nothing below this box knows about October 3, which is the day HubSpot produced its first verified
> tools (`hubspot_contacts_create/read/update/delete`) and the day the iframe, write-grounding,
> navigation-settle, locator-feedback, exploration, probe and partial-publish fixes landed. The
> architecture description and the gotchas in this file are still accurate; the state and
> next-steps sections are not.

**Latest continuation, October 2:** read [Crawl4AI handoff](HANDOFF_CRAWL4AI.md) first. The user explicitly reopened one focused Excalidraw operation; Crawl4AI + Claude experiments on Excalidraw and Vikunja both passed. Earlier assessment-only/deferred-canvas notes below are historical.

Written for the next Claude session. After `journal.md`, read this for the architecture description and the gotchas, then `COMPILER_VALIDATION.md`, then `README.md`.

## What Conduit is

Point it at a website URL plus a signed-in browser session and it produces **verified MCP tools** with no API spec: a model proposes a create/read/update/delete "family", `conduit/verifier.py` runs three lifecycle cycles with a control object and a browser restart, and `conduit/mcp_check.py` runs the exported server as a real stdio MCP subprocess across two restarts with learning disabled. Verified tools run with **zero model calls and zero screenshots**. The model (the compiler) is only used while learning.

Built originally by OpenAI Codex ("Astra", gpt-6-astra) Sept 13-28; it stopped on a ChatGPT usage limit mid-Microsoft-Forms-login. This session (Claude) continued from Sept 28. See `~/.claude/.../memory/` for the full backstory (`conduit-astra-history.md`).

## State right now

- Git repo initialized; Fable's baseline is **20 commits under Iminabo's identity**. The September 29 review below adds uncommitted follow-up work. No Claude/Anthropic git attribution (hard rule — see the `git-no-claude-attribution` skill).
- Full test suite green before this stretch: **109 tests** (`-m "not desktop and not live_model"`, ~7 min). Fast suite **67 tests** (`-m "not integration and not desktop and not live_model"`, ~8 s).
- Runs on the **Anthropic API, Claude Opus 5**, official SDK. Config in `conduit/.env` (gitignored; never read/print its values).

### What passes (real third-party sites, learned with no API spec)

| Site | Tools | Gates cleared |
|---|---|---|
| Vikunja demo | `vikunja_projects_*` | compile + 3 lifecycles + exported MCP + independent REST observer |
| EspoCRM demo | `espocrm_contact_*` | same four (observer needed soft-delete flag + all-field maps) |
| BookStack demo | `bookstack_books_*` | same four (observer needed derived-field ignores; writes go via HTML forms) |
| Snipe-IT demo | `snipeit_companies_*` | Conduit's 3 gates only; **no observer** (its API refuses the browser session) |
| **Microsoft Forms** (user's real account) | `forms_*` + `form_questions_*` (8 tools) | compile + 3 lifecycles + exported MCP + `conduit verify`; September 29 review adds independent native reads, 20 MCP calls and two restarts |

Evidence: `artifacts/ladder/<stamp>/`, `artifacts/public-compiler-*/report.json`, `artifacts/microsoft-forms-authoring.json`.

## How to run things (Windows, from the conduit dir)

- Live scripts such as `validate_compiler.py` and `repair_interface_export.py` load `.env` internally. Do not print it or try to source it as PowerShell code. Check other entry points for their own configuration loading before running them.
- Tests **must** pass `--basetemp artifacts/pytest-...` — the default temp dir is sandbox-blocked (`PermissionError [WinError 5]`).
- Fast suite: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp artifacts/pytest-x -m "not integration and not desktop and not live_model"`
- Integration suite: same with `-m "not desktop and not live_model"` (launches real browsers; run under the user's interactive account, not a sandbox — DPAPI).
- Live model smoke: `CONDUIT_TEST_MODEL=1 ... -k live_anthropic tests/test_compiler_model.py`.
- Ladder: `.venv/Scripts/python.exe scripts/ladder.py [--only <rung>]`; observer-only rerun: `--reobserve "<rung>=<report.json path>"`.
- Login (no dashboard — user dislikes it): `python -m conduit login <id> --wait 600 --logged-in-text "<words on the signed-in page>" --channel chrome`. `--wait` watches the browser instead of waiting for Enter (good for OTP). Then `python -m conduit compile <id> --rounds N`, `python -m conduit verify <id>`.

## Warnings and gotchas (these will bite you)

1. **API key is org-level.** It needs `ANTHROPIC_WORKSPACE_ID` in `.env` **or** a workspace-scoped key, else every call is `HTTP 400 "not scoped to a workspace"`. A `503 credential validation failed` was an Anthropic incident, not the key — check status.claude.com before assuming a bad key. A genuinely dead key returns 401.
2. **Never read or print `conduit/.env` or the Drive "API Keys" doc.** The Drive key is burned; tell the user to rotate it.
3. **Anthropic structured output rejects open schemas** (`additionalProperties: true` → 400). The compiler payload rides inside a fixed `{payload_json: "<string>"}` envelope (`conduit/compiler_model.py`, `OUTPUT`/`ENVELOPE`). Don't "simplify" that away.
4. **`git add`** — the repo is owned by a different SID; `git config --global --add safe.directory <path>` is already set. Commit with `git -c core.safecrlf=false commit` to silence CRLF noise.
5. **Long JS proposals truncate at 16000 tokens.** `.env` has `CONDUIT_MODEL_MAX_TOKENS=32000` for exactly this. If a compile dies with "Compiler output truncated", raise it.
6. **Login markers are per-site.** The signed-in fingerprint only credits a vanished password form or a sign-out control; SPAs need `--logged-in-text`. Fingerprint is taken *after* the login form renders (single-page apps).
7. **Cleanup that "needs_attention" blocks the next compile.** When a lifecycle leaves a test object (e.g. EspoCRM soft-delete, Forms question-id rule), reconcile it: independently read/delete the object via the site API, then flip the manifest cleanup entry to `status: "removed"` with a `reconciled` note pointing at an `artifacts/*-reconciliation.json`. Never retry the mutation.
8. **The dashboard (`conduit ui`) exists but the user dislikes UI.** Use the CLI. It was Astra's, pre-dating the "no fancy stuff" instruction.

## Open problems / what I'd do next

1. **Snipe-IT has no independent observer.** Its `/api/v1` rejects the browser session. Microsoft Forms now has a separate signed-in context reading `formapi` directly; see the review below. Its observer is independently implemented but uses the same underlying service, not an unrelated storage backend.
2. **The Snipe-IT export gate hit a transient HTTP 405 on a cold create once, then passed on retry.** Worth a look at cold-start header timing in `mcp_check.py` if it recurs (it's the documented "gate catches what lifecycle misses" pattern).
3. **Re-run the ladder periodically** — demo instances drift/reset; it exercises the repair path.
4. **Excalidraw/canvas is deliberately parked** per the user. Don't chase it.
5. Use the Forms connector from a real MCP client (`.conduit/connectors/microsoft-forms-authoring-2b09ac3c/client-config.json`).

## Key code you'll touch

- `conduit/compiler_model.py` — model backends (anthropic/codex/http); `infer(prompt, home, *, research, system)`.
- `conduit/agent.py` — `model_settings()` (env resolution, workspace), the vision `request_decision`.
- `conduit/programs.py` — the step language + `$session`/`$global` bindings + `ProgramRunner.resolve`. `GLOBAL_LOOKUP` reads a page JS global at call time.
- `conduit/login.py` — `fingerprint`, `looks_signed_in`, `confirm`, `scripted_login`, `watch_for_sign_in`.
- `conduit/browser.py` — session, the event-driven login checkpoint (do NOT reintroduce `storage_state()` during manual login — it opens tabs and can close the window), `login_origins`.
- `scripts/verify_compiled_website.py` (harness), `scripts/verify_rest_export.py` (config-driven observer), `scripts/ladder.py` + `scripts/ladder.json`.

## Tips

- Read selected report fields rather than dumping large logs; the pytest basetemp trees under `artifacts/` are huge. Follow the current session's delegation instructions.
- The honest-reporting culture is load-bearing: never mark a rung "passed" without the observer (or state plainly that no observer exists). Keep failed reports.
- After any code edit, run `prune-comments` (standing user rule) and lint (`ruff check conduit scripts tests`) before committing.

## September 29 handoff review

The handoff was checked against the commits, current source, saved manifests and reports. The fast suite passed again (67 tests). The earlier 109-test full-suite result predates the page-global additions and is not a claim of a new full-suite run. The source at review start also had an untracked `artifacts/compiler-records-5c36bb2a/`; it was left alone. The new review changes are not committed.

`scripts/verify_forms_export.py` ran the existing Forms MCP with learning disabled and separate native reads: [passed report](artifacts/forms-independent-604860c0/report.json), 20 calls, two restarts, 47 observer reads, all 47 form fields and 27 normalized question fields checked, no internal models or screenshots. Program fingerprints were unchanged. The initial observer's single-question route returned 404 despite the record existing in the collection; [that failed report](artifacts/forms-independent-52e0e918/report.json) is retained, and [cleanup was independently reconciled](artifacts/forms-observer-reconciliation.json) before the passing run.

The stale `incomplete` status was traced to three prose prerequisites stored as capabilities in `.conduit/checks/c9fee867cdf9/`. Their resource names are not valid program names. The compiler now stores malformed resource entries as discovery notes and conservatively migrates legacy notes. Real incomplete resources and entries with unresolved mutations remain blockers. Six new unit tests cover this distinction and native observer failure cases. The original `microsoft-forms-authoring.json` report is preserved as historical evidence.

The focused compiler/page-global/login/session regression run also passed all 14 tests. Repository-wide Ruff checks passed. [Status reconciliation](artifacts/forms-status-reconciliation.json) records why the current connector can now say `ready`: both real resource families have complete operation coverage and the independent MCP gate passed with unchanged programs. Scope remains title-based form editing and text-question creation/title editing; other question types and advanced authoring settings are not yet verified. No fresh learning-model call was made in this review.

### September 29 follow-up: linked workflow failures

`scripts/verify_forms_workflow.py` exercises two forms, three questions, explicit parent IDs, sibling preservation, stale edits and server/browser restarts. The native question `modifiedDate` did not advance after editing its title, so the original revision guard accepted a stale edit. `conduit/state_guard.py` now hashes the complete observed record and adapter/object identity. Receipts return `state_token`; update/delete accept optional `expected_state_token` and reject a changed record before mutation. Tool schemas upgrade when loaded; generated family programs and their fingerprints remain unchanged. This is not a server-atomic compare-and-write and does not cover children absent from a parent's read result.

The content guard rejected stale edits in three live reruns with unchanged native timestamps. The broader workflow still failed: first an omitted parent `rowCount@odata.type` annotation (now narrowly handled by the observer, with the actual row count still checked), then an independent full-record mismatch during deletion whose exact field is unresolved. See the linked-workflow section in `COMPILER_VALIDATION.md` and `artifacts/forms-workflow-e21b6ce4/report.json`. The checker now reports differing field names and preserves structured errors, nonces and the original failed operation.

Further attempts encountered rate limiting. **Do not report this workflow as passed or immediately repeat the whole write sequence.** `artifacts/forms-workflow-bb47293e/report.json` preserves the 429 failure; `artifacts/forms-workflow-bb47293e-reconciliation.json` confirms removal of its one question and two forms. Earlier uncertain creation was resolved by a complete read in `artifacts/forms-workflow-create-check.json`. All known pending test records are resolved; no original uncertain creation was replayed. Next investigate the remaining mismatch with a minimal focused test once the service is ready. Generic runtime startup readiness also remains open: the independent observer encountered an initial 401 that settled through read-only checks.

Latest validation: **79 fast tests passed**, both compiled-MCP integration variants passed, and the external-edit/stable-native-version integration test passed after correcting a test fixture's field name (`version`, not `revision`). These three integration cases were run separately; no claim of a new full-suite run. Ruff passed. The saved Forms sign-in worked across fresh browser contexts. No learning or vision calls were used, and no new UI was built. Review work remains uncommitted.

### September 30: linked workflow passed

The live `--case delete-focus` run was resumed through its existing process handle, not restarted. `artifacts/forms-workflow-b47cb8cb/report.json` isolated the remaining mismatch to an omitted parent `rowCount@odata.type: "#Int64"`; cleanup confirmed every test record absent. `content_record` in the independent observer now normalizes only that annotation for an actual nonnegative signed-64-bit integer count. All record content and unknown annotation values are still checked. Ten observer tests passed. Generated adapter programs were not edited.

The complete workflow then **passed** in `artifacts/forms-workflow-96936ea9/report.json`: 23 successful MCP calls plus one expected stale-edit rejection, 90 independent native GETs, two MCP/browser restarts, 48.69 seconds including observer work, zero internal model calls/screenshots, and no remaining cleanup. It exercised two forms and three questions with returned parent IDs, Unicode titles, sibling preservation, content guards, and persistence/deletion after restart. The saved connector's `compilation.linked_workflow_test` points to this passing evidence. The earlier failure reports remain intact.

The previous "do not report this workflow as passed" warning applied to the September 29 state and is superseded by this fresh result. Cold-start authentication readiness remains unresolved: earlier read-only startup checks observed a transient 401 before the page refreshed its session. Avoid another full live lifecycle without a new reason; further work should address that generic runtime gap and other outstanding milestone requirements. Canvas and native apps remain deferred under the user's scope instructions.

After the observer changes, the fast regression suite passed **81 tests** (`artifacts/pytest-review-final-02`), with 52 integration/native/live-model cases deselected. Repository-wide lint and whitespace checks passed. This is still not a new full integration-suite run.

### September 30: runtime startup recovery implemented

`BrowserSession.auth_restore_deadline` permits recovery only within five seconds after automatic startup and resets on close. `ProgramRunner.request` retries authentication failures on read-only requests up to four times within that window, using `_request_once` to resolve current cookies/token bindings again. It never retries writes, uncertain outcomes or unrelated failures. Explicit GET mutations also remain single-attempt. The existing program timeout and cancellation remain in force. The compiler prompt now explains observed read-only preflights for asynchronous session restoration.

Validation: **91 fast tests passed** (`artifacts/pytest-startup-auth-fast-01`); **four real-browser startup variants passed** (`artifacts/pytest-startup-auth-browser-01`); **seven focused MCP/header/failure tests passed** (`artifacts/pytest-startup-auth-mcp-01`). The new website fixture forces a 401 with a stale page token, restores its saved HTTP-only session cookie and token, and checks exactly one successful create. Both program languages and both request transports are covered. Lint and whitespace checks passed. No model was invoked.

The Forms export runtime was refreshed without changing its generated programs. `artifacts/forms-startup-runtime-check.json` records three successful fresh MCP starts in `--read-only` mode, each reading an already-deleted test form with one API request and zero mutations/models/screenshots. The transient race did not occur in those three live reads; the local forced-race tests establish recovery. Do not claim automatic recovery for a write-first program or a missing page global accessed before the request. No further Forms lifecycle rerun is needed solely for this change: local exported-MCP regressions passed, and the real linked workflow remains evidenced by `forms-workflow-96936ea9`.

### September 30: requirements audit and initial interface-only benchmark

`MILESTONE_AUDIT.md` maps the original requirements to current source and inspected reports. It records remaining work including explicit verified program minimization/route selection and stronger interface-only synthesis evidence. Canvas remains deferred. Existing Forms success is preserved.

Added `tests/interface_site.py`, an authenticated server-rendered HTML application with randomized routes, ordinary forms, rotating CSRF values, version conflicts, and two interface variants. It serves no JSON API or application JavaScript. Both fixture browser tests passed in `artifacts/pytest-interface-fixture-01`. `scripts/validate_compiler.py` now accepts `interface` and `interface-changed`, retaining independent server-state checks, unseen-input/model-disabled execution, restarts and the actual exported MCP gate. These are new test targets, not a claim that generation already passed.

The first actual learning run **finished with exit 1**: former exec session **42642**, command `scripts/validate_compiler.py --case interface --rounds 8 --program-format javascript`, report `artifacts/compiler-interface-bab68013/report.json`. Do not poll or restart that old handle. Generation produced a complete family and passed three internal lifecycles plus the exported MCP gate, but the independent native-state comparison failed: the read result omitted the visible record `version`. The result is not an acceptance pass. Generation took 666.80 seconds including its MCP gate, with four completed model calls and one additional truncated inference whose usage is unavailable. The initial bare assertion left a blank top-level error; subsequent reproduction supplies structured missing-field diagnostics.

That process loaded its compiler before two diagnostic improvements: future inference retries include the failure cause in progress and model feedback, and the benchmark checkpoints current generation state on every progress event. A focused local test of the retry feedback passed (`artifacts/pytest-inference-feedback-01`), proving zero website writes during the simulated model failure/retry. The failure was output truncation at 32,000 tokens; its usage must not be reported as zero.

### September 30: focused program repair

`action:revise` lets the model replace selected CRUD programs while preserving the other programs and public inputs. It still passes through the complete lifecycle and staged exported-MCP gates. `assert_record_matches` now reports missing/extra/changed field paths from independent state without logging their values. Five focused unit cases and one real browser/MCP integration case passed (`pytest-revise-unit-02`, `pytest-revise-browser-01`); the full fast regression passed **96 tests**, with 60 integration/native/live-model cases deselected (`pytest-focused-repair-fast-01`). Repository-wide lint passed. This is not a new full integration-suite result.

The actual focused repair **passed and finished with exit 0** (former session **22582**): `scripts/repair_interface_export.py artifacts/compiler-interface-bab68013/report.json --rounds 4`, report `artifacts/interface-repair-dc090a73/report.json`. It restored only the local fixture, logged in afresh, reproduced the missing-version failure, collected the real detail page, and removed that evidence record before learning. The model received the original generated family plus fresh UI and independent failure feedback, never fixture source. It chose `revise` and changed all four programs, preserving public inputs and adding `revision_path: version`. The generated read now returns the complete detail-page record. The original failed report and adapter remain intact.

Repair took **one model call**, provider usage 17,578 input / 6,385 cache-creation input / 12,849 output tokens, and 149.16 seconds including staged checks. It passed three lifecycles and the exported MCP gate. A separate independent MCP run then passed **10 calls and two restarts in 9.70 seconds**, with zero internal models/screenshots, exact server-state comparison including the version field, and zero remaining fixture records. The website served only `text/html`. This is successful repair with externally supplied failure feedback, not first-attempt URL-only success.

Fresh URL-only generation against the changed HTML interface **failed and finished with exit 1** (former session **57546**): `scripts/validate_compiler.py --case interface-changed --rounds 6 --program-format javascript`, report `artifacts/compiler-interface-changed-56cfd522/report.json`. It used a newly randomized route, changed field names and markup, fresh authentication and no adapter from the previous run. Two exploration replies were followed by two consecutive 32,000-token truncations; no complete candidate was tested or published. Wall time was 707.30 seconds. The process loaded before the prompt clarification and token-accounting fix below. Do not restart/poll this terminal handle or report the changed variant as passed.

The trace exposes a real learning gap: the empty list revealed only the creation form; the model requested a disposable record to discover the detail/edit/delete interfaces, and the ordinary exploration guard rejected that write. The compiler currently requires the entire CRUD family before allowing ordinary record experiments. Removing contradictory prompt advice improves clarity but does not supply the missing learning phase. **Next work should implement a bounded disposable-record learning/reconciliation path**, with durable ownership/identity tracking, fresh readback and cleanup before another experiment; uncertain creation must not be replayed. Preserve the successful Forms adapter. Do not spend another long identical generation run before changing this mechanism. No process remains active from this review.

### September 30: failed inference accounting

Review found that the Anthropic backend discarded provider-reported usage when a completed response hit the output limit, contained no text or was declined. `InferenceFailure` now carries those statistics into compilation totals, including research failures. Connection errors with no usage remain explicitly unknown. No historical token counts were fabricated or backfilled. The completed changed-interface process loaded before this fix, so both output-limit failures still have unknown usage in that historical report. Its two counted calls exclude those two failed responses.

Validation: **19 model-backend tests passed**, two browser/compiler retry cases passed with zero website writes, and the current fast suite passed **98 tests**, with 61 integration/native/live-model cases deselected (`pytest-handoff-current-fast-01`). Repository-wide lint passed. No additional live model call was made to test accounting.

### October 2: disposable-record learning implemented and accepted

`conduit/record_probe.py` adds an account-local encrypted journal written before any form edit. `probe_record` prepares an already-open creation form with fill/select steps and a runtime-generated marker, then submits once. `recover_record` reads the native record, checks its marker and identity, and optionally deletes it once. It verifies absence after browser restart. A missing search result cannot clear an unidentified creation; a changed or unrelated record cannot be deleted. Interrupted deletes are reconciled through reads, and unresolved experiments block new experiments, candidates and exported-MCP test mutations. A fresh compiler restores pending state even if the caller lost its manifest update. Multi-submit wizards remain outside this probe recipe.

Five recipe unit cases passed. An initial integration run exposed a regex-escaping defect in the test's HTML parser; that helper was corrected. The first rerun was prevented by automatic approval review reaching a usage limit (no test command executed), then the October 2 retry passed **seven integration cases** in `pytest-record-probe-browser-03`. Three further compiler/gate cases passed in `pytest-record-probe-resume-01`, including fresh-runtime learning resumption and blocking MCP tests for an earlier candidate while another experiment is unresolved. One of those repeats the original compiler case, for nine distinct passing integration cases across the runs. Fast regression passed **103 tests** (`pytest-record-probe-fast-01`); repository-wide lint passed.

**Fresh live generation passed and finished with exit 0** (former session **38963**): `scripts/validate_compiler.py --case interface-changed --rounds 8 --program-format javascript`, report `artifacts/compiler-interface-changed-e0bc5d49/report.json`. Do not poll or restart that terminal handle. The model opened the creation form, encountered one harmless read-selector timeout, then used `probe_record` to create a marked record and `recover_record` to remove it with restart verification. Its first candidate's update left the requested fields unchanged; the verifier rejected that and removed both test records. The model corrected its program via `revise`, then passed three lifecycles and the exported MCP gate. No generated source was manually edited.

Acceptance then passed **four additional model-disabled lifecycles** with new inputs and browser restarts, plus an independent actual MCP sequence of **10 calls, two restarts and 20.44 seconds**. All returned records matched the fixture's native state including `version`; there were zero internal model calls/screenshots and zero remaining fixture records. Four `desk_record_*` tools were generated in browser mode, mixing deterministic UI actions and HTTP reads. Generation took **509.69 seconds** and eight model calls, without inference errors. Provider usage: 90,618 input / 7,072 cache-creation input / 49,504 cache-read input / 35,654 output tokens. The earlier failed attempts remain separate evidence and costs. The site delivered only HTML, with no supplied API spec, app JavaScript or fixture source.

A final metadata review reserved `_record_probe` (outside valid generated resource names), preserving a legitimate existing resource named `record_probe` and migrating only an older journal-backed entry. Final validation passed **104 fast tests**, with 70 integration/native/live-model cases deselected (`pytest-record-probe-final-fast-01`), and the fresh-runtime compiler/MCP case passed again (`pytest-record-probe-final-resume-01`). Repository-wide lint passed. These final checks used no learning-model call or real Forms writes. Sessions **52710**, **77303** and all earlier handles are terminal; no work process remains active. Next milestone work is verified program simplification and measured selection between execution routes; canvas/native work remains deferred by the user. All review work remains uncommitted.

### October 2: optimizer and Crawl4AI review

Added `conduit/optimizer.py`, `optimize <connector> <resource> --rounds 2`, and opt-in `compile --optimize`. Candidate programs preserve public schemas and revision semantics, face complete-record reference comparisons during three lifecycles and actual exported-MCP checks, and only replace the published family after measured selection. The reference read tool exists only in the staged test bundle. Pending learning/cleanup blocks optimization. Timings exclude reference reads and startup; selection requires 10% faster median CRUD or fewer steps with nonincreasing requests and at most 5% slowdown. This is bounded model-proposed optimization, not exhaustive route search or statistically robust benchmarking.

Validation passed: one optimizer unit case; three browser/MCP optimizer cases (`pytest-optimizer-browser-01`, 96.11 seconds); 105 fast tests (`pytest-optimizer-fast-01`, 73 deselected); 16 compiler/MCP regression cases (`pytest-optimizer-core-regression-01`, 207.67 seconds); repository-wide lint. Tests include metadata omission and a separate MCP-only drift that blocks publication and further optimization. Former sessions 34563, 79929 and 79859 are terminal.

Live `scripts/optimize_interface_export.py artifacts/compiler-interface-changed-e0bc5d49/report.json --rounds 2` **finished with report status passed**: `artifacts/optimizer-interface-f4bf0a82/report.json`. Its process handle was lost when a tool response exceeded context; authoritative process inspection showed the venv launcher and child (58428/63424), and the final report confirms completion. Do not restart the experiment. The first malformed candidate was rejected without execution; the second passed 30 lifecycle reference comparisons and ten MCP comparisons. The optimizer kept the original because 4,340.54 ms versus 4,602.19 ms was under the 10% threshold and requests increased 11 to 12 despite steps dropping 26 to 19. Optimization used two model calls and 381.96 seconds. Usage: 59,457 input / 7,428 cache-creation / 7,428 cache-read / 24,563 output tokens. Independent native-state acceptance of the retained export passed ten calls, two restarts and 18.20 seconds, with zero remaining records/models/screenshots. No Forms writes were run. The original generation bundle is untouched.

Latest user steering was to check Crawl4AI. Official docs and upstream source were reviewed against `evidence.py`/`recorder.py`. It offers query-based documentation filtering, reusable structured page extraction (including optional model-generated schemas), browser interaction and network capture. Its inspected main branch captures response text as well as metadata; do not mistakenly claim it only records headers. Recommendation: benchmark optional local evidence filtering/page readers, keeping Conduit's session/mutation/verifier pipeline. Details and source links are in `CRAWL4AI_ASSESSMENT.md`. No Crawl4AI dependency was installed, benchmarked or wired into production, and no speed/token saving was claimed. The goal remains active; further canvas/native work remains deferred. All work is uncommitted.
