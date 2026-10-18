# Problems Encountered

Only problems / blockers are logged here (date only, no time). General progress goes in
`journal.md`. Each entry: what happened, cause, and status (`[OPEN]` / `[resolved]` / `[note]`).
**Newest entries first.**

Dates are local (America/Chicago); the raw transcripts are stamped UTC, five hours ahead.

Items with no single discovery date live under **Standing** at the bottom.

## 2026-10-04 (using the HubSpot connector from a second MCP client)

The first time a Conduit connector was driven by a real outside client rather than by the harness.
Four defects in two hours, all in the session-ownership layer, none of which any test covers.

- **[OPEN] `browser_close` saves state before releasing, so it can overwrite a valid login.** A
  fresh sign-in was saved to disk; a second Claude Code session was then told to call `browser_close`
  to release the browser; that call wrote the **signed-out cookies it was holding** over the good
  session file. The disk session was genuinely unauthenticated afterwards, and the other session
  diagnosed it correctly before we did — its note on the tool's own description ("Save browser state
  and release this browser") is the giveaway. Cause: newest write wins regardless of quality. A
  context that is signed out must never be allowed to overwrite a stored session that was valid.
  Saving on close is right when the session is healthy; the missing part is the health check.

- **[OPEN] A refreshed session is invisible to a running MCP server.** The server loads the session
  file into memory at startup and never re-reads it. Re-logging in while a client is connected
  changes nothing for that client, and reconnecting the connector in the client reattaches the
  client without restarting the server process, so it keeps serving stale cookies. On a product
  where sessions expire routinely this is exactly the failure a user hits. A session refreshed on
  disk should be picked up, or at minimum invalidate what a running server is holding.

- **[OPEN] One client at a time, with no handoff.** `session_lock.py` gives a connector's browser a
  single owner. When a saved session expires while a client holds it, nothing can open a login
  window until that holder releases, and the only reliable way out was killing the holding process —
  which disconnected the other session's connector twice and looked to the user like the connector
  "dropping". The error message explains the fix, but through the chat front door a person would
  just see it fail. Handing the login back cleanly when a session expires is the real gap.

- **[OPEN] The connector `notes` field leaks build instructions to MCP clients.** The HubSpot
  connector's notes still carry the request written there to steer the compiler — create the contact
  John Doe, read it back by id, put the runtime marker in the last name — and that field is served
  to clients as part of the connector description. A second Claude Code session read it, correctly
  judged it an embedded instruction that did not come from its user, and refused it. The behaviour
  on the client side is exactly right; the bug is ours. Those notes were a build scratchpad and
  should not ship. Not yet stripped.

- **[note] UI-driven tools are seconds, not milliseconds, and that is structural for this target.**
  The HubSpot delete took ~1 minute end to end from a cold client. Roughly 10–15 s is Chromium
  startup (gone on every call after the first if the connector stays connected); the rest is a real
  UI round trip — open the record page, wait for the app to render, open the Actions menu, click
  Delete, wait for the dialog, confirm, re-read to prove absence. The generated programs also poll
  generously, sized for the worst case. `optimizer.py` exists for exactly this and has not been run
  on the contacts family. What will **not** fix it is hoping for an API: see the chirp entry below.

- **[note] Zero write requests were captured during the HubSpot create phases**, so there is nothing
  to replay as HTTP even though the probe did submit the real form. 127 writes were recorded
  overall; none in a create phase. This is why the connector is browser-driven rather than
  request-driven, unlike Vikunja, EspoCRM and Forms.

## 2026-10-03 (HubSpot compiles, runs 1–20)

Twenty attempts, $88.12, one success. Nineteen of the failures were a defect in Conduit rather than
in the model, which is the useful part. The sixteen that were fixed are listed in `journal.md` with
their commits; recorded here are the ones still open and the ones worth not rediscovering.

- **[OPEN] A cleanup matcher deleted a contact that was not ours.** Matching on the substring
  `"john"` caught HubSpot's auto-generated **"Maria Johnson (Sample Contact)"**. The restorable
  option was used, so it is recoverable for 90 days from the Contacts list under
  **Actions → Restore records**. An automatic restore could not locate the restore entry point
  reliably. **Still not restored.** Cause: a hand-written cleanup script, not the product's own
  ownership rule — the probe matches by id *and* runtime marker precisely to avoid this, and the
  script bypassed it.

- **[OPEN] Leaked browser processes.** 82 browser processes were alive mid-session; a single
  Chromium normally spawns about a dozen subprocesses, so most were from runs that did not close
  cleanly. Not a cause of any failure, but sessions are not always released and it wastes memory on
  the machine.

- **[OPEN] The form parser discards the richest free signal.** It skips any form containing a
  checkbox, radio, file or password input — which is the declarative, zero-cost evidence avenue, and
  it is being discarded on precisely the forms that matter.

- **[OPEN] The recorder is purely passive, so interception is a missing capability.** It listens for
  requests and responses, so it can only learn a write *after* that write has landed. Route
  interception plus abort — letting the app compose the request and killing it inside the browser
  before a byte reaches a socket — is strictly safer than a disposable write and would have made
  several of the twenty runs free. Not a tuning improvement; genuinely absent. Caveats to respect
  when building it: captured single-use tokens generalise as a template but not as values; you get
  one sample, not the accepted schema; you never see the response, so the identifier field stays
  unknown; and the app sees a network failure, so the browser context should be discarded afterwards.

- **[note] HubSpot's create form has three quirks that cost one paid run each, and all three were
  readable off the page for free.** The fields `[data-test-id='firstname-input']`,
  `lastname-input` and `jobtitle-input` are **`textarea`** elements, not inputs (only
  `email-input` is an `<input type=email>`), so any locator asking for an input matched nothing;
  the bare `[data-test-id='...']` form works for both. **Job title stays disabled until the email
  field has a value**, so field order matters. And **the form commits a field only on blur**, so the
  Create button (`button[data-test-id="create-button"]`) stays visibly present but disabled until
  focus leaves the last field — fill-then-click-immediately hits a dead button, fill-then-Tab
  enables it within about two seconds. These are site-specific and worth nothing elsewhere; the
  transferable lesson is the process one below.

- **[note] The process failure that produced those three.** Facts were written into the compiler's
  connector notes from inference rather than observation, and the compiler trusted them. Each wrong
  fact cost a full run plus a manual cleanup. Iminabo called this out directly, and he was right
  about the shape of it even though the cause was carelessness rather than anything deliberate. The
  rule now: open the page, read the actual tags, write down only what was seen.

- **[note] HubSpot's in-app write path is reachable in principle and not in practice.** Its in-app
  writes do not use the documented API. They use **chirp**, an internal Avro RPC, at
  `/chirp-frontend-{app|internal|external}/v1/gateway/{serviceName}/{rpcName}?clienttimeout={ms}`
  with a bare JSON body and an `x-hs-fingerprint` header — a shape fully reconstructed from
  HubSpot's own unminified TypeScript, which ships publicly in source maps with `sourcesContent`.
  What is **not** recoverable is the operation's identity: a fully-qualified Java service name, a
  method name and an opaque Avro fingerprint, which exist only in generated client code inside the
  authenticated CRM bundle. That bundle is absent from both public version manifests (login's 377
  projects, signup's 248) and referenced by no unauthenticated page. Documented writes live on a
  different host and need a bearer token. **No HubSpot documentation sanctions a write to the app
  host at all**, which is why a model that "knows HubSpot" reliably produces a plausible hybrid of
  neither — run 3's HTTP 405 took the host from the session and the path, payload shape and id field
  from the sunset v1 API.

- **[resolved] No iframe support anywhere in the browser layer** (`4b0bb4f`). Every locator resolved
  against the top-level page; there was not a single frame lookup in the codebase. HubSpot's create
  form lives in an `object-builder-ui` iframe, so the first two runs' field reads were
  *structurally impossible*, not slow or unlucky. Fixed with a frame key on locators (CSS selector
  or list, nesting up to three), frame-aware snapshots, per-frame evidence, and a contract note so
  the model knows the key exists.

- **[resolved] A model could invent a write endpoint from pretrained memory and nothing stopped it**
  (`4b0bb4f`, `grounding.py`). `check_evidence` only verified that cited evidence ids existed;
  nothing required a write step to cite an observed request at all. Run 3 recalled HubSpot's legacy
  v1 path with **empty evidence citations on all four programs**. Now a write URL must be backed by
  first-party evidence, vendor documentation explicitly excluded, enforced both at validation and
  inside the single request choke point — the latter matters because every family that currently
  works uses JavaScript programs, so a rule inspecting only declarative steps would have been
  bypassed by writing the same guess in JS.

- **[resolved] A probe that failed before its submit locked the connector** (`02cc3af`). The probe
  journals ownership before its first edit, which is correct for recovery, but it treated a locator
  miss *before any field was touched* as an uncertain creation. That blocks all future compiles
  until a human resolves it, and seven passes plus about $6 went on reconciliation programs each
  rejected by a different safety rule. Nothing can have been created when nothing left the browser.
  Same principle later applied to removals (`71a3958`), to fills (`1e931e0`) and to focus keys
  (`7c5f015`).

- **[resolved] Conduit's own contract rules fired after the write had already landed** (`cb9e0e1`).
  An oversized sleep killed runs 9, 10 and 18; a POST used as a read without its evidence
  declaration killed run 17. Both are plainly visible in the program source, but were only enforced
  deep inside the worker, so a candidate violated them halfway through its lifecycle *after* HubSpot
  had created the control contact — a ~$13 failure plus a manual cleanup each time. Checked at
  proposal time now, so the model gets ordinary feedback and revises.

- **[resolved] An exploration that aborted on its first failed step made each model call worth one
  fact** (`7e85f84`). Ten passes bought about ten facts, which is why runs 4 and 5 burned out on the
  menu dance. Exploration now runs every step best-effort and reports all outcomes plus the frames
  present on the resulting page.

- **[resolved] "Resolved to 2 elements" reached the model with the candidates cut off** (`dd69636`).
  Playwright's strict-mode error carries the matching candidates on later lines, and only the first
  line was being kept. That truncation ended run 5 on its last pass.

- **[resolved] Navigation returned before a heavy SPA had rendered, and a locator miss was
  indistinguishable from a slow page** (`b39d492`). The first click after a navigation always burned
  its full timeout, and a wrong role produced the same error text as an unhydrated page, so the model
  had nothing to correct against. Now: bounded network-idle settle capped at 8 s, and a miss fails in
  ~3 s naming what does match the text, by tag and role, and the frame selector to add.

- **[resolved] Reads ran before a click's injected iframe existed** (`0cc5115`), and the frame list
  was buried under chat, notification and onboarding widgets. Run 6 clicked correctly and then read
  too early. Exploration now settles after a click and filters the reported frames to those holding
  controls.

- **[resolved] "Resumed" runs resumed almost nothing** (`4b22594`). The learning checkpoint never
  saved a single network observation, so every resumed run started with an empty recorder and had to
  re-observe the create request from scratch. Worse, the checkpoint's scope included the notes and
  tasks, both of which had been edited before runs 8 and 9 — so neither of those actually resumed at
  all, which invalidated the reasoning about what each was starting from.

- **[resolved] The recorder's rolling 160-event window evicted the one request we were paying to
  observe** (`de3376a`). Recovery traffic pushed the probe-phase create request out before the report
  was written. Probe and verify-phase writes are pinned now.

- **[resolved] A transient browser error could end a thirty-round creation** (`9d734de`, `d21da5d`).
  Evidence collection at the top of a pass sat outside the per-action error handler, so one aborted
  navigation threw away 17 passes. And an `ERR_ABORTED` during a lifecycle killed run 19 after all
  four operations had already passed once. Collection failures now cost one pass; aborted navigations
  retry up to three times with backoff, only for genuinely transient conditions, and only because
  opening a page is a read so no write can be repeated.

- **[resolved] `--rounds` was clamped to 16 in the CLI layer** (`71a3958`), so a run launched with a
  30-round ceiling silently ran 16 and the reasoning about why it ran out of passes was wrong.

- **[resolved] A resource had to prove all four verbs to publish anything** (`242b896`). Iminabo asked
  for two tools and the contract was building four, making his request hostage to an update operation
  he never asked for and which was the hardest of the four on this site. Publishing now emits the
  operations that passed, marks the capability partial with the gap named, and the export gate
  exercises only those — while the lifecycle still creates, reads and deletes a disposable record
  three times with an untouched control record, so cleanup stays guaranteed. An update whose outcome
  is **unknown** still stops everything.

## 2026-10-02 (Crawl4AI measurement)

- **[OPEN] Crawl4AI supplements the evidence instead of replacing it, so it enlarges the prompt it
  was supposed to shrink.** Measured on two real signed-in sites, off then on. EspoCRM (REST SPA):
  −2.1% input tokens, a wash. BookStack (server-rendered HTML, where it should have helped most):
  **+30.1% input tokens, one extra learning round, 6.0× the generation wall time**, still passing.
  Extraction itself was trivial (9 s), so the cost was the model thinking longer on a bigger prompt.
  Verdict: **off by default**, reachable only via `--crawl4ai-python` / `CONDUIT_CRAWL4AI_PYTHON`.
  To pay off it would have to use the filtered Markdown **in place of** the raw DOM when confident,
  and drop or shrink the DOM source accordingly — a larger change to `evidence.py` budgeting and
  `prompt_evidence`, to be re-measured on the same two sites. Full numbers in
  `CRAWL4AI_MEASUREMENT.md`.

- **[note] The ladder recorded a passing run as a timeout.** BookStack-on ran ~57 minutes, past the
  ladder's 45-minute per-rung wall-clock limit, while the underlying harness finished and wrote a
  passing report afterwards. Worth knowing before trusting a ladder timeout.

- **[note] Crawl4AI only ever enriched whatever page was live during a collection pass.** On the
  first HubSpot run with it enabled it processed the contacts landing page and six chrome iframes
  (chat, notifications, onboarding tours) and **skipped the create form, the record detail page and
  the object-builder embed** — every page that mattered for writing a contact. Resumed runs made it
  worse, because inherited sources were never enriched. Fixed by backfilling every page and frame
  document that lacks an extraction from text already on disk (no navigation, no model calls), and
  by raising the extraction cache above 8 entries, which was thrashing across a multi-page run.

## 2026-09-30 (Microsoft Forms, linked workflow)

- **[resolved] A stale edit was accepted using an earlier revision value.** Microsoft's question
  timestamp sometimes does not change after an edit, so a revision guard keyed on it is not a guard.
  Fixed with `state_guard.py`: a read returns a hash of the full record, and update/delete can
  require that token back and refuse if the content changed. **Explicitly not a server-atomic
  write** — it is a read-then-compare, and the code says so. Rejected a stale edit in two live runs.
  Noted at the time: *"I won't mark stale-edit protection as verified on this evidence"* — the right
  instinct, and the reason the guard exists rather than a reassurance.

- **[resolved] HTTP 401 on a cold start, before any write.** Forms refreshes its authentication
  *after* the page loads, so the read-only sign-in check ran too early. Fixed with a pre-mutation
  read probe that waits for one successful read before any write, and bounded startup recovery where
  read-only auth failures may retry with fresh cookies and tokens. **Writes stay single-attempt,
  including when authentication fails.** The forced race only ever reproduced in local tests, never
  in the live checks.

- **[resolved] An observer bug looked like a tool bug, twice.** A question mismatch was traced to the
  checker, not the generated tool: the single-question URL returns 404 while the question
  *collection* holds the record. Then a parent-form "change" during question deletion turned out to
  be an optional annotation (`rowCount@odata.type`) disappearing while every stored value stayed
  identical. Both were corrected in the checker without touching the generated tools. The line worth
  keeping from this: *"A probe is evidence, never CRUD certification."*

- **[note] Microsoft rate-limited the live tests (429)** once enough disposable forms had been
  created, which stopped test-data creation mid-workflow. Two stray forms and one question were
  reconciled through the API and confirmed deleted. Rate limits are a real constraint on live-account
  targets, not a flake.

- **[note] An empty website hides the interfaces a compiler needs.** On a site with no records, the
  edit and delete controls only appear after one exists, while exploration permits only reads. This
  was the blocker that defined the following three days, and the fix was `record_probe.py` (a
  journalled disposable record with a unique ownership marker, deleted exactly once, absence verified
  after a restart). Recorded here because clearer prompt instructions alone would not have
  established that it was fixed.

## 2026-09-29 (Anthropic backend, ladder)

- **[resolved] `503 credential validation failed` is not a bad key.** Every call failed, including a
  bare minimal request with no Conduit features. A genuinely dead key returns **401** (which the old
  EUPHRATES key did). Anthropic had an active incident open. **Check status.claude.com before
  rotating anything** — the near-miss here was telling the user to recreate a working key.

- **[resolved] An organisation-level key is rejected on every call.** *"This API key is not scoped to
  a workspace, so this request must include the anthropic-workspace-id header."* Fixed in `cf72ede`
  by supporting `ANTHROPIC_WORKSPACE_ID`; a workspace-scoped key needs no header. Symptom is a
  blanket HTTP 400, which reads like a code bug.

- **[resolved] Anthropic structured output rejects open schemas** (`additionalProperties: true` →
  400), which is why the compiler payload rides inside a fixed `{payload_json: "<string>"}` envelope
  in `compiler_model.py`. Do not "simplify" that away.

- **[resolved] Long JavaScript proposals truncate at 16,000 tokens** and the run dies with "Compiler
  output truncated". `.env` carries `CONDUIT_MODEL_MAX_TOKENS=32000` for exactly this.

- **[resolved] A login fingerprint taken too early credits the wrong page**, and a sign-in-looking
  control still present after login reads as a failure. Fingerprint after the login form renders
  (single-page apps), and for SPAs pass `--logged-in-text`. The marker is per-site: Forms' first-run
  page says **"Choose a scenario"**, not "My forms", and guessing it wrong cost a nine-minute watcher
  timeout. Use Chrome, not Edge — a signed-in Edge profile interferes with Microsoft sign-ins.

- **[resolved] Snipe-IT's login page hides a decoy password field ahead of the real one**, so the
  harness must pick the first **visible** password input, not the first in the DOM (`881e1e1`).

- **[resolved] EspoCRM soft-deletes** (`deleted: true`), so a read must treat a soft-deleted record
  as absent or the lifecycle's deletion check fails; it also ignores Contact `title`, so a family
  that tries to update it fails on a field the demo silently drops. **BookStack** derives
  `slug`/`description_html` and routes writes through HTML forms rather than a JSON API, which is
  why its learning cost was 5× the others'. All recorded as ladder rung notes rather than worked
  around.

- **[OPEN] Snipe-IT has no independent observer.** Its `/api/v1` refuses the browser session and the
  page sends no capturable token, so it passes three of the four gates. **Microsoft Forms also has
  no observer** beyond the exported-MCP gate; a real one would need a second signed-in context
  reading `formapi` directly. Possible, not done. Recorded so neither is ever reported as four gates.

- **[note] A transient HTTP 405 on a cold create** hit the Snipe-IT export gate once and passed on
  retry. Worth a look at cold-start header timing in `mcp_check.py` if it recurs — it is the
  documented "gate catches what the lifecycle misses" pattern working, not a bug.

## 2026-09-28 (Astra's last stretch)

- **[resolved] The sign-in browser closed itself before the session was saved**, losing the login,
  with the cause never found. Mitigated by background login checkpointing while `manual_login` is
  set. The checkpoint that did this was a 1 Hz `storage_state` dump that **opened a tab each time**;
  it was replaced the next day (`e500419`) by cheap event-driven cookies-first checkpoints. Do not
  reintroduce a polling `storage_state` checkpoint.

- **[resolved] A connector reported `incomplete` while both its resources were `ready`.** Three prose
  prerequisites had been stored as capabilities whose "resource" names were not valid program
  identifiers, so they counted as unfinished resources forever. `discovery_resources()` and
  `migrate_discovery_notes()` turn malformed entries into discovery *notes* instead, preserving the
  original report as history.

- **[note] Astra's usage limit stopped the project mid-login**, and had already cut it off on Sept
  16, 20, 21, 22 and 24. The Codex auto-review guardian hits the same limit, so the final command
  was rejected before it ran. This is an operational fact about the setup, not a Conduit defect, but
  it is why the Forms login was left half-finished and why Claude took over.

- **[note] Every context compaction in Astra's thread carried an empty summary**, so it repeatedly
  lost its own working state and kept only Iminabo's instructions. That is the mechanical reason the
  same ground gets re-covered across the archive, and the reason `HANDOFF.md` and now these two
  living docs exist.

## 2026-09-20 to 2026-09-24 (canvas)

- **[OPEN] Unassisted canvas discovery has never completed.** Excalidraw's full-CRUD pass reused an
  earlier learning session, and the later `excalidraw_move_element` tool was given the existing
  adapter's protocol as explicit assistance. Every report stamps that assistance rather than claiming
  discovery. Deliberately parked by Iminabo — *"drawing canvas seem like the hardest variant"* — and
  the Oct 3 research explains why it is not merely hard: an accessibility node can express a role, a
  label, bounds and an action verb, but there is no attribute that can mean "this control issues a
  POST to this path with this body". For canvas and WebGL the endpoint is **absent, not hidden**.
  Across nine canvas vendors, zero products write through rendered pixels; the route is always a side
  door (official API, plugin sandbox, document format, or desktop COM). Observing one real write is
  the only general option, which is why the disposable-record probe should stay as a last resort
  rather than be removed.

- **[resolved] Canvas discovery kept revisiting the same menus.** Each learning pass received the
  previous results but **not the steps that produced them**, so a working export sequence was
  rediscovered every pass. Preserving `executed_steps` as reusable evidence fixed it. This is the
  single highest-value diagnosis in the canvas work.

- **[resolved] Excalidraw's import dialog differs between empty and non-empty documents.** Once a
  drawing exists, "Open" shows a replacement dialog ("Loading from a file will replace your existing
  content") before the file picker, and `showOpenFilePicker` is absent. Also: autosave can lag a
  visible edit, and reopening **normalises** some fields — so a verification that compares
  immediately, or compares everything, fails on a correct edit. Fixed with durable-save checks,
  restoration re-verified after a browser restart, and narrow metadata allowances for `versionNonce`
  and `updated`, which Excalidraw's own `mutateElement.ts` changes on every edit. Metadata rules
  cannot exempt identity or editable fields — there is a test for that.

- **[resolved] Chromium throttles bursts of renderer-initiated downloads**
  (`DownloadRequestLimiter`), which looked like a canvas persistence failure. Fixed by pacing
  downloads 200 ms apart.

- **[resolved] The model repeatedly emitted malformed, repetitive canvas programs.** Rather than
  fight it, `document_compiler.py` lowers **one** observed export/patch/import recipe into four
  ordinary verifiable CRUD programs. Worked immediately, including nested-field edits. The general
  lesson, which Astra drew from the Holy review the same week: *"Conduit may also be making
  generation harder by forcing every operation into a small custom language."*

- **[resolved] A fresh model run created a record and extracted its identifier incorrectly.** Conduit
  stopped rather than creating another — the correct behaviour — then recovered the id from the
  observed response plus a fresh read, cleaned up, and repaired the adapter. A test asserts that a
  genuinely lost response still causes exactly **one** create attempt.

## Standing (structural, not tied to one day)

- **[OPEN] The front door does not exist.** The hub serves saved verified tools just in time and the
  single-operation path stores and exposes one verified operation, but there is no single entry that
  takes a sentence, resolves it against saved tools, and only falls through to creation when nothing
  matches. Today those are separate CLI commands a person strings together. This is the piece
  Iminabo's mockup is actually asking for, it is free of model spend to build, and the three sites
  that already pass every gate (Vikunja, EspoCRM, BookStack) are where it should be demonstrated.

- **[OPEN] The prompt is the architecture.** About 220 lines of natural-language contract in
  `compiler.py` drive behaviour as much as the Python does, and nothing versions it, diffs it, or
  tests it. Several of the twenty HubSpot runs turned on single sentences in it.

- **[OPEN] Every connector carries a full copy of the package** under `_runtime/`. Repo-wide greps
  keep hitting those stale forks, and a fix to the package does not reach an exported connector until
  `conduit upgrade` repackages it.

- **[OPEN] Truncated model output gets its braces repaired.** `compiler_model.py` appends up to eight
  closing delimiters silently, so malformed inference can still parse and execute. It is a pragmatic
  hack that works; it is also a path by which a half-written program runs.

- **[OPEN] The single-operation server hardcodes one verb.** `single_operation.py` can only serve an
  `update`, and only a script outside the package can produce such a bundle. Both imported
  single-operation connectors are updates, which is why this has not bitten yet.

- **[OPEN] The old Anthropic key in the team's shared Drive "API Keys" doc is burned and still needs
  rotating.** It is in plaintext and shared with the whole team. Anthropic already rejects it. Never
  read or print `conduit/.env`.

- **[note] Tests must pass `--basetemp artifacts/pytest-...`** — the default temp dir is
  sandbox-blocked (`PermissionError [WinError 5]`). Integration tests need the user's interactive
  account, not a sandbox, because of DPAPI. Source `.env` before any live run; the scripts read the
  process environment.

- **[note] Keep `mcp` pinned `<2`.** Generated servers import `mcp.server.fastmcp`, which mcp 2.0
  removed.

- **[note] Cleanup marked `needs_attention` blocks the next compile, by design.** Reconcile it by
  independently reading or deleting the object through the site's own API, then flip the manifest
  cleanup entry to `status: "removed"` with a `reconciled` note pointing at an
  `artifacts/*-reconciliation.json`. **Never retry the mutation.**
