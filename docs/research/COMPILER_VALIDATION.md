# Website compiler validation

The implementation has passed real model-driven synthesis against disposable local websites and the public Vikunja demo, plus browser and MCP integration tests. An assisted public Excalidraw adapter now passes independent saved-scene checks, including an empty-session test. Unassisted canvas discovery remains incomplete and is deferred at the user's request; the next target is [Microsoft Forms authoring](BUSINESS_TARGET.md).

## Live compiler runs

Only the URL, requested tasks and the website's delivered interface/source/network observations enter these runs. The compiler receives no prewritten adapter or API specification. `scripts/validate_compiler.py` checks the resulting backend state independently of the generated read program, then repeats four CRUD lifecycles with `CONDUIT_LEARNING_DISABLED=1`, including a cold browser start.

| Target | Result | Saved evidence |
| --- | --- | --- |
| Authenticated records website, rotating CSRF cookies | Full CRUD; new inputs; saved login; no model calls during repeated execution | [Report](artifacts/compiler-records-f3068787/report.json) |
| Held-out variant with randomized routes and changed editable field | Freshly synthesized CRUD; no adapter supplied | [Report](artifacts/compiler-changed-eed30507/report.json) |
| JSON document canvas | Create, inspect, edit and erase; actual export/import; save/reopen; unrelated control shapes preserved | [Report](artifacts/compiler-canvas-59a7d8e8/report.json) |
| Route changed after compilation | Old route failure, newly verified replacement at adapter version 2, subsequent model-free CRUD | [Report](artifacts/compiler-repair-bc6f0817/report.json) |
| Official authenticated Vikunja demo | Generated project CRUD called through the actual MCP client, with two process/browser restarts and independent full-record reads | [Independent acceptance](artifacts/vikunja-independent-mcp.json) |

The fixture source is `tests/compiler_site.py`; it is deliberately excluded from the compiler's inputs. The model can inspect JavaScript served by the website, as it can on an ordinary website. Handwritten families in unit tests exercise the executor separately and are not counted as live synthesis evidence.

The public-demo run used the login published by that demo. Earlier discovery gathered official documentation, delivered JavaScript and real browser requests; no adapter was supplied. The successful synthesis pass used `gpt-5.6-sol`. Its original [compilation report](artifacts/public-compiler-52850141/report.json) correctly records failed candidates and an initial MCP acceptance failure. A later acceptance run reused the generated programs unchanged, independently inspected all 17 returned project properties, checked preservation except the server-managed modification timestamp, and verified deletion after restart. Only then was the adapter published. The independent checker is test code; it is not supplied to synthesis or used by generated tools.

Saved model-generated local adapters were also exercised through fresh exported MCP servers with independent state observers: [records](artifacts/mcp-saved-records-3768a7a1/report.json), [changed application](artifacts/mcp-saved-changed-6b3bb124/report.json), and [canvas](artifacts/mcp-saved-canvas-0be89f43/report.json). These are execution regressions, not new discovery runs.

## Generated JavaScript route

The compiler can generate ordinary JavaScript against Conduit's session bridge. The first live trials used `gpt-6-astra`, supplied no adapter or API specification, and required JavaScript implementations for all four CRUD operations.

- The [held-out records run](artifacts/compiler-changed-6cb45da2/report.json) generated a complete family on its first proposal, passing three lifecycle cycles and the staged MCP test. The outer benchmark then failed because it assumed the update would change the caption; the generated tool correctly updated the note instead. The original failed report is retained. A separate [unchanged-adapter acceptance run](artifacts/mcp-saved-changed-338bf7e8/report.json) restored the same disposable website route and passed actual MCP CRUD with independent full-record checks and two restarts. The benchmark now checks whichever record field was requested and verifies preservation of the other field.
- The [live canvas run](artifacts/compiler-canvas-b502080d/report.json) passed generation, varied-input execution with learning disabled, independent persisted-state checks, and exported MCP testing with two restarts. Warm creates took approximately 988 ms; a create after restart took 1,766 ms. The first execution also starts a browser and is excluded from the warm samples even though this older report labels it `cold:false`; new reports inspect the actual browser state when labeling cold starts.

Records compilation took 65.28 s and two model calls (39,171 input / 1,165 output tokens). Canvas compilation took 89.80 s and three model calls (40,662 input / 1,462 output tokens). These compiler-reported durations include lifecycle verification and the staged exported-MCP gate; the later independent acceptance sequence is separate. New benchmark reports also record wall time around the entire compiler call explicitly. Repeated JavaScript execution made zero internal model calls and took zero screenshots. Process startup adds overhead relative to the original step executor. These samples establish working generation and execution, not a controlled claim that JavaScript improves reliability or cost across arbitrary websites.

## Measured execution

These are local test measurements on this Windows computer. Browser startup, model/provider latency and remote site latency vary. The comparison is the same record-create task; it is not a broad benchmark of all CRUD operations or SaaS sites.

| Measurement | Compiled records | Existing vision loop |
| --- | --- | --- |
| One-time generation and lifecycle verification | 75.14 s; 3 model calls | No generation phase |
| Warm create | 26.40–30.44 ms, three samples | 26.25 s, one sample |
| Create after browser restart | 624.80 ms | 28.26 s |
| Internal model calls per create | 0 | 4 |
| Generation tokens reported by provider | 35,846 input; 1,921 output | N/A |
| Warm create tokens | 0 | 48,233 input; 294 output |

Compiled canvas creation took 775–862 ms warm and 1,468 ms cold. Canvas update/delete took approximately 1.0–1.1 s because each operation exports current state and checks the document before replacement. Canvas compilation took 86.96 s and three model calls. The route repair took 71.58 s; later creates took 22–28 ms warm and 707 ms cold.

Raw provider usage, request counts and operation durations are in each report. Missing provider usage is represented as unavailable, never estimated as zero. Compilation uses model allowance. No price or dollar-cost claim is made.

The public-demo independent MCP acceptance sequence took 14.77 s including three server/browser processes. A preceding protocol-only run measured warm create at 337 ms, update at 471 ms and delete at 456–471 ms, with zero internal model calls or screenshots ([report](artifacts/vikunja-mcp-recheck.json)). The successful resumed synthesis pass alone took 149.79 s and four model calls (193,310 input / 4,819 output tokens); this excludes earlier discovery attempts and is **not** an end-to-end generation-cost claim.

## Integration and failure checks

The latest completed broad regression run passed **72 tests in 369.34 s**, with three live-model/native tests deselected (`artifacts/pytest-javascript-full-01`). It includes both program implementations, form-encoded CRUD, nested account tokens, read-only POST declarations, application errors inside HTTP 200 responses, and document imports whose confirmation dialog depends on whether the canvas is empty. JavaScript checks cover worker cancellation before and after an awaited call, rejection of unobserved read results, and prevention of a retry even when code catches a host-call error. Metadata checks reject identity/content exemptions, backward or implausible timestamps and malformed nonces. Two dependency deprecation warnings remain. This is a local regression result, not a fresh public-site synthesis run.

- Copied standalone export and universal MCP are called through the official MCP client. Typed schemas, structured outputs, CRUD, coverage resources, persisted authentication and structured expired-session errors are checked against independent server state.
- A lost create response produces exactly one server mutation, leaves the resource incomplete, and prevents another learning attempt from creating a duplicate.
- A document changed after export is rejected before import; the intervening shape remains present.

Other tests cover rotating cookies, browser restart, false HTTP success without a state change, a lost update response reconciled without another mutation, stale revisions, preservation of unrelated fields and shapes, GraphQL query/mutation classification, and redacted request/response relationships. Existing discovery, connector migration, session ownership and export tests also pass.

New checks cover delayed authentication-header acquisition, document recipe compilation, nested-field preservation, asynchronous autosave, guarded probe recovery, and post-write session-save failure without duplicate creation. Verification now restarts once after editing and again after deletion. The publication gate retains an earlier working adapter if a new exported MCP fails. Malformed inner JSON may receive bounded closing-delimiter completion; this cannot insert values or complete truncated output, and all normal schema, evidence and execution checks still apply.

## Anthropic backend

The compiler was switched to the Anthropic API (Claude Opus 5 through the official SDK) on 2026-09-28 after the Codex allowance ran out. The stable contract is sent as a cached system prompt and the per-round evidence as the user message; replies are schema-enforced JSON inside a fixed envelope. The first gate was the local records website with `scripts/validate_compiler.py --case records --rounds 6`: [report](artifacts/compiler-records-3970438f/report.json).

| Measurement | Value |
| --- | --- |
| Learning passes | 2 (propose, done) |
| Model calls | 2 |
| Provider-reported tokens | 19,001 input (5,805 read from cache on the second call), 3,034 output, 5,805 cache write |
| Compilation including lifecycle verification and the staged MCP gate | 52.6 s |
| Model-free execution trials and exported-MCP protocol test | passed |

Token counts are what the provider reported; no dollar cost is claimed. This gate establishes that the new backend drives the existing loop end to end, not that it generalizes.

## Business website ladder

`scripts/ladder.json` and `scripts/ladder.py` run public demo instances of business applications through the full harness: scripted sign-in through the site's own form, compilation with no supplied adapter, model-free lifecycles, the exported-MCP test, and an independent read of every record through the site's JSON API. Every run, passed or not, is kept under `artifacts/ladder/`. Results are recorded here as they are produced.

| Rung | Resource | Sign-in | Learning passes / model calls | Provider-reported tokens | Result | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| Vikunja official demo | projects | scripted form login, verified by the vanished password form | 3 / 4 | 142,823 input (23,184 cache reads), 6,758 output | Passed all four gates: compile ready, three model-free lifecycles, exported MCP with two restarts, independent REST observer over 10 calls checking all 17 record fields | [Compilation](artifacts/public-compiler-vikunja-8ac0380d/report.json) · [Ladder row](artifacts/ladder/20260929-0425-5200/ladder.json) · [Observer](artifacts/ladder/20260929-0425-5200/vikunja-observer.json) |
| EspoCRM demo | contacts (firstName, lastName, description) | click-only demo sign-in, verified by the word Homepage | 3 / 4 | 145,916 input (41,655 cache reads), 14,682 output | Compile ready, lifecycles and exported MCP passed in the run; the observer passed on a rerun once it mapped every edited field and accepted the demo's soft-delete flag (`deleted: true`) as absence. 10 calls, 55 fields each | [Compilation](artifacts/public-compiler-espocrm-f8fc2f1f/report.json) · [Run row](artifacts/ladder/20260929-0449-3aa2/ladder.json) · [Observer rerun](artifacts/ladder/20260929-0509-eba8/espocrm-observer.json) |
| BookStack demo | books (name, description) | scripted form login, verified by the word Books | 7 / 8 | 870,309 input (64,660 cache reads), 35,170 output | Compile ready, lifecycles and exported MCP passed in the run; the observer passed on a rerun once `slug` and `description_html`, which the site derives from the edited fields, were listed as derived. 4 checks of 18 fields | [Compilation](artifacts/public-compiler-bookstack-266f4873/report.json) · [Run row](artifacts/ladder/20260929-0449-3aa2/ladder.json) · [Observer rerun](artifacts/ladder/20260929-0508-6cb3/bookstack-observer.json) |
| Snipe-IT demo | companies (name, phone, email, notes) | scripted form login (first visible password field), verified by the word Companies | 3 / 4 on the passing retry | 3 runs; the passing retry used about 0.93 USD at list prices | Passed Conduit's three gates on the retry (compile ready, model-free lifecycles, exported MCP with two restarts). No independent observer result: the demo's `/api/v1` rejects the browser session (302, then 401 with an XHR header) and the page never sends a bearer token the observer could capture. An earlier attempt was withheld by the export gate after HTTP 405 on a cold create that did not recur | [Passing retry](artifacts/public-compiler-snipeit-2a720534/report.json) · [Withheld attempt](artifacts/public-compiler-snipeit-2c8f4d84/report.json) · [Finding](artifacts/snipeit-gate-finding.json) · [Observer attempts](artifacts/ladder/20260929-0519-e176/snipeit-observer.log) |

## Microsoft Forms authoring

The first real commercial SaaS target, learned on the user's own Microsoft account after a manual sign-in verified by `conduit login --wait`. Three compile runs on 2026-09-29 ([run 1](artifacts/forms-compile.log), [run 2](artifacts/forms-compile2.log), [run 3](artifacts/forms-compile3.log)); evidence summary in [microsoft-forms-authoring.json](artifacts/microsoft-forms-authoring.json).

| Run | Passes / calls | Outcome |
| --- | --- | --- |
| 1 | 9 / 10 | Incomplete. Reads on the Forms data API worked; every write was refused with 403 "AntiForgery token validation error" because the token exists only as a page JavaScript global, which no session binding could express. The verifier refused a program that pasted the token in as a literal. |
| 2 | 5 / 5 | `forms` family ready and published (create, read, update, delete) using the new `global` session binding; exported MCP passed with two restarts. `form_questions` failed on the service rule that question ids start with `r`; one test form was left behind, found through the API and deleted ([reconciliation](artifacts/forms-questions-reconciliation.json)). |
| 3 | 2 / 2 | `form_questions` family ready and published; exported MCP passed again. `conduit verify` then re-ran the whole export independently: passed, two restarts, 25.4 s. |

Published tools: `forms_create/read/update/delete` and `form_questions_create/read/update/delete`. They call the Forms OData API with the anti-forgery token bound from `OfficeFormServerInfo.antiForgeryToken` and the tenant and user ids from `OfficeFormServerInfo.userInfo` at execution time; nothing is frozen into the programs. Provider-reported tokens across the three runs: 1,258,944 input, 95,651 output. These compilation runs initially had no separate native observer; the September 29 review below adds that check.

Notes on these runs. The Vikunja rung is a regression of the earlier Codex-era result under the new backend; it took 321 s wall clock from sign-in to observer pass, including one earlier attempt that failed only the sign-in check ([report](artifacts/public-compiler-vikunja-e41e6d0c/report.json)) before the pre-login fingerprint was taken after the form rendered. EspoCRM needed two compile attempts: the first stalled because the demo does not store the Contact `title` field and its API keeps soft-deleted records readable ([first run](artifacts/public-compiler-espocrm-4d578342/report.json), [reconciliation](artifacts/espocrm-4d578342-reconciliation.json)); those two facts were added to the rung notes. BookStack's first run died on two consecutive 16,000-token output truncations, not on the site ([first run](artifacts/public-compiler-bookstack-72e5ca5b/report.json)); the output cap was raised to 32,000 for the ladder. The EspoCRM run's export gate failed once with an authentication error that did not reproduce when the same family was re-exported by hand ([repro](artifacts/espocrm-manual-gate-repro.json)). Observer reruns reuse the earlier run's compiled programs unchanged; their rows say so.

## Reproduce

Run browser tests under the normal signed-in Windows account so DPAPI session protection can be exercised. Live compiler commands consume model allowance.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/validate_compiler.py --case records --baseline
.\.venv\Scripts\python.exe scripts/validate_compiler.py --case changed
.\.venv\Scripts\python.exe scripts/validate_compiler.py --case canvas
.\.venv\Scripts\python.exe scripts/validate_compiler.py --case repair
.\.venv\Scripts\python.exe scripts/verify_compiled_website.py https://excalidraw.com
```

## Outstanding milestone evidence

The September 29 handoff review independently exercised the saved Microsoft Forms adapter through the actual MCP protocol. [The passing report](artifacts/forms-independent-604860c0/report.json) contains 20 calls, two server/browser restarts, 47 separate native read requests, and zero internal model calls or screenshots. A separate signed-in browser context checked all 47 form fields and all 27 normalized question fields, compared the requested titles, checked preservation of the other test objects, and confirmed deletion. The generated programs were unchanged; this is stronger execution evidence, not a new discovery run. The complete acceptance sequence, including the observer, took 36.57 seconds.

The [first observer attempt](artifacts/forms-independent-52e0e918/report.json) failed because the individual question URL returned 404 while the same record remained in the question collection. The checker now uses the observed collection route and rejects incomplete pagination. This was an observer error, not an established adapter failure. Its one pending test question was freshly read, removed through the generated MCP, and confirmed absent in [the reconciliation](artifacts/forms-observer-reconciliation.json) before another lifecycle ran. No survey distribution or response submission was performed.

The handoff's `incomplete` compilation status also came from three earlier prose prerequisites stored as resource names. Staged manifests preserve this evidence. The compiler now separates such notes from valid resource identifiers and migrates old notes while retaining genuine incomplete resources and unresolved mutation records. Historical failed reports remain unchanged.

### Linked Forms workflow and content guards

The stronger `scripts/verify_forms_workflow.py` test uses returned form identifiers to create questions in two different forms, preserves siblings, edits parent and child titles, tries a stale edit, and checks persistence after restarting the MCP. It initially failed for the reasons below, then **passed the complete workflow on September 30**. This extends the passing per-resource CRUD result above.

The [first linked run](artifacts/forms-workflow-86c98d3f/report.json) exposed a real defect: a question's `modifiedDate` stayed unchanged after an edit, so the native revision guard accepted an older read. Known test records were [independently confirmed absent](artifacts/forms-workflow-reconciliation.json). The runtime now returns a content-based `state_token` and accepts `expected_state_token` for updates and deletes. It compares the full observed record before sending the mutation. This is optional for compatibility and is not atomic with the server-side write. It protects only the record returned by the learned read program; it does not implicitly cover its child collections.

The new guard rejected the stale edit without changing the question in [a live rerun](artifacts/forms-workflow-fc8a1502/report.json), even though the native timestamp again stayed unchanged. That run subsequently failed the strict parent-preservation check. [A diagnostic rerun](artifacts/forms-workflow-bdc34766/report.json) isolated an omitted `rowCount@odata.type` annotation. The observer permits only that annotation's presence/absence to differ while still comparing `rowCount` and all other content. The [next run](artifacts/forms-workflow-e21b6ce4/report.json) reached a separate full-record mismatch during question deletion; its exact changed field remains unresolved. All known records from these runs were confirmed removed in their cleanup sections. No claim of a passing linked workflow is made.

Subsequent tests stopped on a create error, then [HTTP 429](artifacts/forms-workflow-bb47293e/report.json). A [complete collection read](artifacts/forms-workflow-create-check.json) resolved the first uncertain creation as absent. The rate-limited run's known question and two forms were freshly checked, removed, and [confirmed absent](artifacts/forms-workflow-bb47293e-reconciliation.json); the original creation was not retried. Further learning/lifecycle writes stopped. The checker now preserves structured MCP errors, test nonces and failed-operation names. A separate startup read also observed a transient 401 before authentication settled; the observer retries only these startup reads, not mutations. General runtime startup readiness remains an open issue.

Validation after these changes: 79 fast tests passed; both standalone/universal MCP integration variants (step programs and JavaScript) passed with stale-delete checks; a separate browser integration test passed with an external content change that did not advance the native version. Those three integration cases ran separately from the fast suite, not as a new full-suite run. Repository-wide Ruff checks passed. No learning-model call was made during this review.

### September 30: complete linked Forms workflow passed

The [focused deletion diagnostic](artifacts/forms-workflow-b47cb8cb/report.json) identified the remaining mismatch: `rowCount@odata.type` changed from `"#Int64"` to absent when reading the parent again after deleting a question. All other content matched, and its test records were removed. The observer now handles this representation difference in every form comparison. It removes only that exact annotation when the associated `rowCount` is a nonnegative integer within the signed 64-bit range. The actual count, titles, question content and all other properties remain checked; unknown annotation values and changed content still fail. This is a correction to the independent checker, with no change to the generated programs.

The [full linked-workflow report](artifacts/forms-workflow-96936ea9/report.json) **passed**: 23 successful real MCP calls, one deliberately rejected stale edit, two server/browser restarts, and 90 independent native reads in 48.69 seconds. Two newly created forms received three questions through their returned identifiers; parent/child edits, sibling preservation, Unicode input, persistence and deletion were checked. The native question timestamp again stayed unchanged, while the content token correctly rejected the stale edit. The successful calls used zero internal models and screenshots, and learning was disabled for all server processes. All disposable objects were confirmed absent after the final restart; no cleanup remained. This duration includes the independent observer and process startup and is not a warm-call latency claim.

Ten focused observer tests passed, including narrow annotation handling and structured mismatch evidence without copying arbitrary record contents. Repository-wide lint passed. General cold-start authentication readiness remains an open issue from the earlier transient 401; this passing run does not establish that the timing issue is fixed.

### September 30: bounded startup authentication recovery

The runtime now opens a five-second recovery window after automatic page startup. A read-only request that fails authentication can be retried at most four times during that window, with cookies and token bindings resolved anew for each attempt. Writes, uncertain outcomes, transport failures, rate limits and other server errors are not retried. Recovery stops after a mutation in the same program, and existing program timeouts/cancellation still apply. The compiler contract explains when an observed read-only preflight is needed; the runtime does not invent an endpoint or replay a rejected create.

`tests/test_startup_auth.py` provides a real local website that deliberately rejects the first read, then restores an HTTP-only cookie and a page-global token using its saved session. All four browser variants passed: steps and JavaScript over both HTTP and in-page requests. Every case independently checked a failed read with the old binding, a successful read with the new binding, and exactly one successful mutation. These are runtime regressions, not new synthesis evidence.

The fast suite passed **91 tests** with 56 browser/native/live-model cases deselected. A separate seven-test regression passed both portable/universal MCP variants, delayed header acquisition, structured expired-session errors and refusal to replay a caught write failure. The four startup browser tests ran separately and passed in 9.87 seconds. Ruff and whitespace checks passed. No learning-model call was made.

The [live read-only Forms check](artifacts/forms-startup-runtime-check.json) started the updated exported server three times and read an already-deleted test form. All three calls confirmed absence with zero model calls, screenshots or mutations. Cold wall time, including MCP startup, was 3.37–4.63 seconds. Each used one API request, so these live runs did **not** reproduce the transient 401; the forced local tests establish the recovery behavior. This change covers read/preflight authentication races. A program that begins with a rejected write still stops after that one attempt, and page-global access before a request can require its own readiness condition.

The local fixtures and public-demo workflow do not establish support for arbitrary SaaS applications. The [assisted Excalidraw repair](artifacts/public-compiler-c1f8e6e0/report.json) passed three learning lifecycles, three further model-disabled lifecycles and two exported-MCP checks. Its ordinary JavaScript uses the real JSON export/import path, checks intervening document changes and waits for saved state. Repair took one Astra call (32,175 input / 2,260 output tokens), 158.88 seconds including staged verification; this excludes earlier attempts and is not a full generation-cost claim.

A separate [independent acceptance](artifacts/excalidraw-independent-4280d2a1/report.json) checked every returned native object against saved scene data, preserved unrelated objects and restored the starting scene. It passed 10 calls and two restarts in 29.43 seconds. The same generated programs also passed in a [fresh empty session](artifacts/excalidraw-independent-2180155e/report.json), with no copied scene/login state, in 30.61 seconds. Both tests ran with learning disabled, zero internal model calls and zero screenshots. These prove execution of the assisted adapter, not unassisted discovery.

A fresh URL-only attempt initially [hit a usage limit](artifacts/public-compiler-47a0029c/report.json), then [reached a guarded document experiment](artifacts/public-compiler-344c01fc/report.json) whose import restoration failed. A later run recovered the original document and was stopped when the user deferred canvas work. It did not publish a new canvas adapter. The runtime now permits JavaScript probe inspection and waits for durable restoration; nine focused probe/save tests passed, including delayed autosave and conflicting edits. Further public canvas experiments are deferred.

Result schemas now describe observed record fields and identifiers instead of only the generic receipt. Editable fields retain their declared input types, so integer test values do not prohibit later fractional values. Four schema unit tests and both standalone/universal MCP integration variants passed (the integration run had five tests including three earlier schema tests). A failed boolean save check now returns an uncertain outcome with the known object identity and does not retry the mutation.

### September 30: HTML-only generation and independent repair

The local interface fixture serves only server-rendered HTML: no JSON API, delivered application JavaScript, API specification or supplied source. Routes are randomized; authentication, rotating CSRF values and object versions are real server behavior. [Initial generation](artifacts/compiler-interface-bab68013/report.json) passed its internal lifecycle/MCP gates but failed the separate native-state comparison because the read omitted the visible `version`. Generation took 666.80 seconds including the gate. Four completed model calls were recorded, plus a truncated 32,000-token inference whose usage is unavailable. This initial result is a failure, not a complete acceptance pass.

The compiler now supports replacing selected programs through `revise`, preserving public input schemas and still rerunning all lifecycle and MCP gates. Independent mismatch feedback reports field paths without their contents. [Actual repair](artifacts/interface-repair-dc090a73/report.json) reproduced the missing version, collected the real detail page, removed that disposable object, and supplied the generated family plus fresh HTML and failure feedback. One model call revised all four programs, including full detail-page reads and the native version binding. No generated source was manually edited.

Repair passed three lifecycles and its staged MCP gate in 149.16 seconds, then passed a separate independent check: **10 MCP calls, two restarts, 9.70 seconds**, zero internal model calls/screenshots and no remaining records. Every returned record matched the website's actual stored record, including version. Provider-reported repair usage was 17,578 input tokens, 6,385 cache-creation input tokens and 12,849 output tokens. These counts exclude the initial failed generation. This establishes HTML-only execution after repair feedback, not unassisted first-attempt success. Five focused unit cases, one real-browser/MCP repair case and the 96-test fast suite passed; no new full integration-suite claim is made.

The [fresh changed-interface attempt](artifacts/compiler-interface-changed-56cfd522/report.json) **failed** after 707.30 seconds, before testing a candidate. It inspected the empty list and creation form, then tried to create a disposable record to discover the remaining interfaces; the read-only exploration guard rejected that step. Two subsequent replies truncated at 32,000 tokens. Their usage is unknown because that process loaded before the accounting fix. The new backend retains provider-reported usage even when a completed reply fails; 19 backend tests, two browser retry cases and the 98-test fast suite passed.

That failure exposed a missing controlled learning phase for ordinary disposable records. The October 2 implementation and acceptance below address it. Automatic choice/minimization across multiple verified routes and broader WebSocket/WebMCP execution still need further work. Native applications and binary inspection remain outside this website milestone.

### October 2: fresh HTML-only generation passed

The compiler can now create one marked disposable record through an already-open form, inspect its actual detail/edit/delete interfaces, and remove it before proposing further mutations. An encrypted journal is written before editing and survives compiler/browser restarts. Ownership and full readback precede cleanup; deletion is issued once and absence is checked after reopening. Missing results cannot clear an unidentified creation. Unresolved experiments prevent later lifecycle/MCP writes. The experiment currently supports fill/select preparation and one final submit, with multi-submit wizards outside this recipe.

[Fresh changed-interface generation](artifacts/compiler-interface-changed-e0bc5d49/report.json) **passed** from URL plus login, without an API specification, fixture source or delivered application JavaScript. The model used the new experiment successfully. Its first update candidate changed the native version but not the requested fields; verification rejected it and removed both test records. A model-generated revision then passed three lifecycle cases and the exported-MCP gate. Generated source was not edited manually.

Four additional model-disabled lifecycles passed with new inputs and browser restarts. The separate independent MCP acceptance passed **10 calls and two restarts in 20.44 seconds**, checking complete native records including version and leaving zero records behind. Execution used zero internal model calls or screenshots. The four `desk_record_*` tools use deterministic browser actions plus HTTP reads; the manifest labels this browser execution. Warm create calls in the additional lifecycles took 1.09–1.46 seconds; cold create calls took 2.32–2.61 seconds. These are local fixture measurements, not commercial-site performance guarantees.

Generation took **509.69 seconds and eight model calls**, with no inference errors. Provider-reported usage was 90,618 input tokens, 7,072 cache-creation input tokens, 49,504 cache-read input tokens and 35,654 output tokens. This includes the rejected candidate and its correction but excludes earlier separate benchmark attempts. Nine distinct local browser/MCP cases passed across focused runs for the record-learning mechanism, including interrupted mutations, ownership conflicts, misleading deletion results, learning resumption and blocking later test writes while cleanup is pending.

### October 2: verified route optimization

The opt-in optimizer keeps public inputs, identity and revision semantics fixed while asking the configured model for simpler programs. Before selection, each candidate passes three lifecycles with complete-record comparisons against the previous reader, then an actual exported-MCP gate using that reference reader in the same account session. Unresolved mutations block further candidates and preserve the prior published implementation. Reference comparisons corroborate equivalence to the existing adapter; the acceptance harness separately compares results with native fixture storage.

Three focused real-browser/MCP cases passed in 96.11 seconds: selecting a direct implementation, rejecting a reader that drops metadata, and withholding publication when metadata drift appears only in the MCP gate. All 16 compiler/MCP regression cases passed in 207.67 seconds. The fast suite passed 105 cases with 73 integration/native/live-model cases deselected; repository-wide lint passed.

[Live HTML-only optimization](artifacts/optimizer-interface-f4bf0a82/report.json) completed in **381.96 seconds with two model calls**. The first response used the wrong program shape and was rejected before writes; feedback produced a valid second candidate. It passed three lifecycles, 30 complete-record reference checks and ten exported-MCP reference checks with two restarts. It reduced median warm CRUD time from **4,602.19 to 4,340.54 ms** and session steps from **26 to 19**, but increased requests from **11 to 12**. Conduit correctly retained the original adapter: this was below the 10% speed threshold and did not meet the alternative requirement of fewer steps without additional requests. This is a verified candidate comparison, not a claimed speed improvement of the delivered adapter.

The retained export then passed a separate native-state acceptance: **ten MCP calls, two restarts, 18.20 seconds**, zero internal model calls/screenshots and zero remaining records. The experiment made 40 fixture mutations. Provider-reported optimization usage totaled 59,457 input, 7,428 cache-creation input, 7,428 cache-read input and 24,563 output tokens. These counts are additional to original generation costs. No generated program was manually edited, and the original generation bundle was preserved.

Selection uses three sequential baseline and candidate samples, excluding reference reads and browser startup. These local measurements are not randomized or statistically conclusive. Automatic optimization is optional; `compile --optimize` runs one candidate round per resource, while `optimize <connector-id> <resource>` defaults to two.
