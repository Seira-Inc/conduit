"""Compare verified implementations without changing the published adapter until selection."""
from __future__ import annotations

import copy
import json
import statistics
import time
from uuid import uuid4

from .compiler import (
    JAVASCRIPT,
    LANGUAGE,
    adopt_observed_apis,
    check_evidence,
    publish,
    record_inference_error,
    revise_family,
)
from .compiler_model import infer
from .evidence import EvidenceCollector, prompt_evidence
from .generator import Store
from .grounding import Ledger, first_party_text
from .mcp_check import failure_detail, verify_export
from .programs import Family, fingerprint
from .record_probe import RecordProbe
from .verifier import VERBS, validate_family, verify_family

CONTRACT = """
Optimize ONE already verified CRUD family. Its code, fresh observations and measured lifecycle are supplied.
Return action:revise with only replacement Programs, current evidence IDs in evidence, and an optional mode
(direct/browser/document/application),
or action:done with a reason when no supported improvement is available. No exploration or mutation actions.
Prefer an observed direct request or application command over UI work when it preserves the same operation.
Remove redundant navigation, requests or repeated extraction. Do not merely minify code. Never remove a
concurrency guard, persistence check, fresh token binding, pagination handling, field or returned metadata.
All public inputs, field mappings, record identity and other family semantics stay fixed. Preserve full records.
The candidate must pass the same three lifecycle cases, full-record comparisons against the existing reader,
and an actual exported MCP test with the reference reader in the same account session. A false success, dropped
field or unresolved mutation rejects the candidate. Timings exclude reference comparisons; no speed claim is
accepted from your response. Conduit selects using its measurements. Do not repeat a failed write.
"""


def score(report):
    cases = report.get("cases", [])
    if report.get("status") != "ready" or len(cases) < 3:
        raise ValueError("Performance selection requires three successful lifecycle cases")
    elapsed, steps, requests = [], [], []
    for case in cases:
        if any(verb not in case for verb in VERBS):
            raise ValueError("Lifecycle timings must include all four operations")
        elapsed.append(sum(case[verb]["duration_ms"] for verb in VERBS))
        steps.append(sum(case[verb]["steps"] for verb in VERBS))
        requests.append(sum(case[verb]["requests"] for verb in VERBS))
    return {"median_lifecycle_ms": round(statistics.median(elapsed), 2),
            "median_steps": statistics.median(steps), "median_requests": statistics.median(requests),
            "samples_ms": elapsed, "includes_reference_reads": False, "includes_browser_startup": False}


def improvement(baseline, candidate):
    elapsed = candidate["median_lifecycle_ms"]
    if elapsed <= baseline["median_lifecycle_ms"] * 0.90:
        return "at_least_10_percent_faster"
    if (elapsed <= baseline["median_lifecycle_ms"] * 1.05
        and candidate["median_steps"] < baseline["median_steps"]
        and candidate["median_requests"] <= baseline["median_requests"]):
        return "fewer_session_steps_without_material_slowdown"
    return None


def pending_cleanup(manifest):
    return any(item.get("status") == "needs_attention" for capability in manifest.capabilities
               for item in capability.get("cleanup", []))


async def optimize_connector(runtime, resource, *, progress=None, model=None, max_rounds=2):
    if not runtime.browser:
        raise ValueError("Optimization currently targets websites")
    if not 1 <= max_rounds <= 4:
        raise ValueError("Optimization uses one to four candidate rounds")
    manifest = runtime.manifest
    originals = [t for t in manifest.tools if t.resource == resource and t.adapter == "program"]
    if {t.operation for t in originals} != set(VERBS) or any(t.verification.get("status") != "ready" for t in originals):
        raise ValueError("Optimize requires an existing verified CRUD family")
    if pending_cleanup(manifest) or RecordProbe(runtime.home, manifest).pending():
        raise ValueError("Resolve outstanding learning mutations before optimization")
    original = Family.model_validate(originals[0].config["family"])
    original_read = next(t for t in originals if t.operation == "read")
    capability = next(c for c in manifest.capabilities if c["resource"] == resource)
    report = {"status": "testing", "resource": resource, "baseline_fingerprint": fingerprint(original),
              "model_calls": 0, "inference": [], "candidates": [],
              "selection_policy": {"faster_ratio": 0.90, "simpler_max_latency_ratio": 1.05,
                                   "timing_scope": "three warm CRUD lifecycles; reference reads excluded"}}
    manifest.compilation.setdefault("optimizations", {})[resource] = report
    started = time.perf_counter()
    model = model or infer

    async def update(message):
        if progress:
            await progress(message)

    def retain_cleanup(result):
        items = [item for item in result.get("cleanup", []) if item.get("status") == "needs_attention"]
        if items:
            capability.setdefault("cleanup", []).extend(copy.deepcopy(items))
            report["status"] = "needs_attention"
        return bool(items)

    async with runtime.lock:
        session = runtime.browser
        collector = EvidenceCollector(session, manifest)
        try:
            runtime.acquire_browser()
            await session.start()
            await update("Measuring the existing adapter's complete CRUD lifecycle")
            baseline = await verify_family(session, manifest, original, update)
            report["baseline"] = baseline
            if baseline["status"] != "ready":
                report["status"] = "baseline_failed"
                retain_cleanup(baseline)
                return report
            best, best_result, best_score, best_stage, best_wire = original, baseline, score(baseline), manifest, None
            report["baseline_score"] = best_score
            feedback = []
            for turn in range(max_rounds):
                runtime.acquire_browser()
                await session.start()
                await update(f"Proposing a simpler or faster verified route ({turn + 1}/{max_rounds})")
                evidence = await collector.collect()
                evidence["sources"] = collector.sources
                prompt = ("EVIDENCE:\n" + prompt_evidence(evidence) + "\nCURRENT FAMILY:\n"
                          + json.dumps(best.model_dump(exclude_defaults=True), ensure_ascii=False)
                          + "\nMEASUREMENTS AND FEEDBACK:\n" + json.dumps({"current": best_score, "feedback": feedback}))
                try:
                    answer, stats = await model(prompt, runtime.home, system=LANGUAGE + JAVASCRIPT + CONTRACT)
                    report["model_calls"] += stats["model_calls"]
                    report["inference"].append(stats)
                except (ValueError, TimeoutError) as exc:
                    record_inference_error(report, exc)
                    report.update(status="inference_failed", error=str(exc))
                    break
                if answer.get("action") == "done":
                    report["reason"] = answer.get("reason", "No supported improvement proposed")
                    break
                candidate_report = {"reason": answer.get("reason", ""), "status": "rejected"}
                report["candidates"].append(candidate_report)
                try:
                    if answer.get("action") != "revise":
                        raise ValueError("Optimization accepts revise or done only")
                    if "revision_path" in answer and answer["revision_path"] != original.revision_path:
                        raise ValueError("Optimization cannot change the record's revision semantics")
                    candidate = revise_family(best.model_dump(), answer)
                    candidate = Family.model_validate({**candidate.model_dump(), "mode": answer.get("mode", best.mode)})
                    if fingerprint(candidate) == fingerprint(best):
                        raise ValueError("The candidate contains no implementation change")
                    validate_family(candidate)
                    collector.ensure_portable(candidate)
                    ledger = Ledger(session.recorder.events, manifest.allowed_origins,
                                    first_party_text(collector))
                    check_evidence(candidate, {item["id"] for item in collector.sources + session.recorder.events},
                                   ledger)
                    stage = manifest.model_copy(deep=True)
                    await adopt_observed_apis(stage, candidate, session.recorder.events)
                    candidate_report.update(fingerprint=fingerprint(candidate), mode=candidate.mode,
                                            family=candidate.model_dump(exclude_defaults=True))
                    previous_manifest = session.manifest
                    session.manifest = stage
                    session.grounding = ledger
                    try:
                        checked = await verify_family(session, stage, candidate, update, reference=original)
                    finally:
                        session.manifest = previous_manifest
                        session.grounding = None
                    candidate_report["verification"] = checked
                    if retain_cleanup(checked):
                        break
                    if checked["status"] != "ready":
                        feedback.append({"candidate": candidate_report["fingerprint"], "verification": checked})
                        continue
                    candidate_score = score(checked)
                    candidate_report["score"] = candidate_score
                    publish(stage, candidate, checked)
                    alias = "__reference_" + resource + "_read"
                    stage.tools.append(original_read.model_copy(update={"name": alias}, deep=True))
                    await runtime.close()
                    directory = Store(runtime.home / "checks" / uuid4().hex[:12]).write(stage)
                    wire = await verify_export(stage, directory, runtime.home, resources={resource}, progress=update,
                                               reference_tools={resource: alias})
                    candidate_report["mcp_test"] = wire
                    if retain_cleanup(wire):
                        break
                    if wire["status"] != "passed":
                        feedback.append({"candidate": candidate_report["fingerprint"], "mcp_test": wire})
                        continue
                    reason = improvement(best_score, candidate_score)
                    candidate_report.update(status="verified", selection_reason=reason)
                    feedback.append({"fingerprint": candidate_report["fingerprint"], "score": candidate_score,
                                     "selection_reason": reason})
                    if reason:
                        best, best_result, best_score, best_stage, best_wire = candidate, checked, candidate_score, stage, wire
                except Exception as exc:
                    candidate_report.update(**failure_detail(exc))
                    feedback.append(failure_detail(exc))
            if report["status"] == "needs_attention":
                return report
            if fingerprint(best) != fingerprint(original):
                publish(manifest, best, best_result)
                manifest.allowed_origins = best_stage.allowed_origins
                selected = next(c for c in manifest.capabilities if c["resource"] == resource)
                selected["mcp_test"] = best_wire
                report["status"] = "selected"
            elif report["status"] == "testing":
                report["status"] = "unchanged"
            report.update(selected_fingerprint=fingerprint(best), selected_mode=best.mode, selected_score=best_score)
        except Exception as exc:
            report.update(status="failed", **failure_detail(exc))
        finally:
            report["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
            report["sources"] = collector.provenance()
            report["evidence_processors"] = collector.processors
            await runtime.close()
    return report
