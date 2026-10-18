"""Run the public-website ladder and record every outcome, passed or not, with provider-reported usage."""
# ruff: noqa: E402
import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]

PRICES_PER_MTOK = {
    "claude-opus-5": {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write": 6.25},
    "claude-sonnet-5": {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5},
}
FAILURE_MODES = [
    ("login_unverified", r"sign-in not verified|login_required"),
    ("inference_unavailable", r"usage limit|usage allowance|rate limited|overloaded|connection failed|inference failed|could not finish"),
    ("browser_step_failed", r"browser_step_failed|browser (click|read|download) failed|timeout \d+ms exceeded"),
    ("postcondition_failed", r"postcondition_failed|did not confirm the intended change"),
    ("mcp_gate_failed", r"exported mcp protocol tests failed|mcp_test"),
    ("observer_failed", r"independent (read|record)|unrelated full-record"),
    ("discovery_exhausted", r"no complete compiled crud family|did not pass the full publication gate"),
]


def normalise_usage(entries):
    """Sum provider usage across inference records from either the codex or the anthropic shape."""
    total = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    for entry in entries or []:
        usage = (entry or {}).get("usage") or {}
        total["input"] += int(usage.get("input_tokens") or 0)
        total["output"] += int(usage.get("output_tokens") or 0)
        total["cache_read"] += int(usage.get("cache_read_input_tokens") or usage.get("cached_input_tokens") or 0)
        total["cache_write"] += int(usage.get("cache_creation_input_tokens") or usage.get("cache_write_input_tokens") or 0)
    return total


def estimated_cost(model, tokens):
    prices = PRICES_PER_MTOK.get((model or "").split("/")[-1])
    if not prices:
        return None
    uncached = max(tokens["input"] - tokens["cache_read"] - tokens["cache_write"], 0)
    return round((uncached * prices["input"] + tokens["output"] * prices["output"] + tokens["cache_read"] * prices["cache_read"]
                  + tokens["cache_write"] * prices["cache_write"]) / 1_000_000, 4)


def classify(error_text):
    text = (error_text or "").lower()
    for mode, pattern in FAILURE_MODES:
        if re.search(pattern, text):
            return mode
    return "unknown" if text else None


def status_for(report, observer):
    compilation = (report or {}).get("compilation") or {}
    gates = {
        "compile_ready": compilation.get("status") == "ready",
        "lifecycles_ready": bool(report.get("disabled_model_verification")) and all(
            r.get("status") == "ready" for r in report.get("disabled_model_verification", [])),
        "mcp_passed": (report.get("mcp_protocol") or {}).get("status") == "passed",
        "observer_passed": (observer or {}).get("status") == "passed" if observer is not None else None,
    }
    passed = all(v for v in gates.values() if v is not None) and gates["observer_passed"] is not False
    return ("passed" if passed and observer is not None else "passed_without_observer" if passed else "failed"), gates


def row_for(rung, report, observer, duration_s, error_text=""):
    compilation = (report or {}).get("compilation") or {}
    inference = compilation.get("inference") or []
    tokens = normalise_usage(inference)
    model = next((i.get("model") for i in inference if i.get("model")), None)
    status, gates = status_for(report or {}, observer)
    crawl4ai = ((compilation.get("evidence_processors") or {}).get("crawl4ai") or {})
    errors = " | ".join(filter(None, [error_text, (report or {}).get("error"), compilation.get("message"),
                                        (observer or {}).get("error")]))
    return {
        "target": rung["url"], "rung": rung["id"], "model": model,
        "crawl4ai": {k: crawl4ai.get(k) for k in ("status", "runs", "cache_hits", "duration_ms")},
        "backend": next((i.get("backend") for i in inference if i.get("backend")), None),
        "effort": next((i.get("effort") for i in inference if i.get("effort")), None),
        "rounds_used": len(compilation.get("learning_trace") or []), "model_calls": compilation.get("model_calls"),
        "tokens": tokens, "estimated_cost_usd": estimated_cost(model, tokens),
        "duration_s": round(duration_s, 1), "status": status, "gates": gates,
        "failure_mode": None if status == "passed" else classify(errors), "error": errors[:600] or None,
        "artifacts": {"report": (report or {}).get("_path"), "observer": (observer or {}).get("_path")},
    }


def render_markdown(rows, generated_at):
    lines = ["# Public website ladder", "", f"Generated {generated_at}. Costs are computed from list prices, not billed.", "",
             "| Rung | Target | Model | Rounds | Calls | Input | Output | Cache read | Est. USD | Status | Crawl4AI | Failure mode |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        tokens = row["tokens"]
        lines.append(f"| {row['rung']} | {row['target']} | {row['model'] or '-'} | {row['rounds_used']} | {row['model_calls'] or 0} | "
                     f"{tokens['input']} | {tokens['output']} | {tokens['cache_read']} | "
                     f"{row['estimated_cost_usd'] if row['estimated_cost_usd'] is not None else '-'} | {row['status']} | "
                     f"{(row.get('crawl4ai') or {}).get('status') or '-'} | "
                     f"{row['failure_mode'] or '-'} |")
    for row in rows:
        if row.get("reobserved_from"):
            lines += ["", f"**{row['rung']}**: observer rerun only; compile and MCP results come from {row['reobserved_from']}"]
        if row["error"]:
            lines += ["", f"**{row['rung']}**: {row['error']}"]
    return "\n".join(lines) + "\n"


def load_report(text, marker):
    match = re.search(marker + r" (.+)$", text, re.M)
    if not match:
        return None
    path = Path(match.group(1).strip())
    report = json.loads(path.read_text(encoding="utf-8"))
    report["_path"] = str(path)
    return report


def harness_args(rung):
    args = [rung["url"], "--task", rung["task"], "--rounds", str(rung.get("rounds", 10)),
            "--program-format", rung.get("program_format", "auto"), "--label", rung["id"]]
    if rung.get("notes"):
        args += ["--notes", rung["notes"]]
    for extra in rung.get("allow_origin", []):
        args += ["--allow-origin", extra]
    login = rung.get("login") or {}
    for key, flag in (("user", "--login-user"), ("pass_env", "--login-pass-env"), ("url", "--login-url"),
                      ("user_label", "--user-label"), ("pass_label", "--pass-label"), ("submit", "--submit-name"),
                      ("click", "--login-click"), ("logged_in_text", "--logged-in-text")):
        if login.get(key):
            args += [flag, str(login[key])]
    if login.get("manual"):
        args.append("--manual-login")
    return args


def observer_args(rung, home, output):
    observer = rung["observer"]
    args = [str(home), "--target", rung["url"], "--read-url", observer["read_url"], "--auth", observer.get("auth", "cookie"),
            "--output", str(output)]
    if observer.get("auth_host"):
        args += ["--auth-host", observer["auth_host"]]
    for result_key, api_key in (observer.get("fields") or {}).items():
        args += ["--field", f"{result_key}={api_key}"]
    for field in observer.get("ignore", []):
        args += ["--ignore", field]
    for name, value in (observer.get("headers") or {}).items():
        args += ["--header", f"{name}={value}"]
    if observer.get("record_path"):
        args += ["--record-path", observer["record_path"]]
    if observer.get("id_path"):
        args += ["--id-path", observer["id_path"]]
    if observer.get("deleted_status"):
        args += ["--deleted-status", str(observer["deleted_status"])]
    if observer.get("deleted_flag"):
        args += ["--deleted-flag", observer["deleted_flag"]]
    return args


def run_rung(rung, out_dir, timeout):
    started = time.perf_counter()
    report = observer = None
    error = ""
    try:
        completed = subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_compiled_website.py"), *harness_args(rung)],
                                   cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        (out_dir / f"{rung['id']}-harness.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
        report = load_report(completed.stdout, "Report:")
        if report is None:
            error = "harness produced no report: " + (completed.stderr.strip().splitlines() or ["no output"])[-1]
        elif report.get("status") == "passed" and rung.get("observer"):
            home = Path(report["resume_state"]) if report.get("resume_state") else Path(report["_path"]).parent / "state"
            observer_out = out_dir / f"{rung['id']}-observer.json"
            checked = subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_rest_export.py"),
                                      *observer_args(rung, home, observer_out)], cwd=ROOT, capture_output=True, text=True,
                                     timeout=timeout)
            (out_dir / f"{rung['id']}-observer.log").write_text(checked.stdout + checked.stderr, encoding="utf-8")
            observer = load_report(checked.stdout, "Observer report:")
            if observer is None:
                observer = {"status": "failed", "error": "observer produced no report: "
                            + (checked.stderr.strip().splitlines() or ["no output"])[-1]}
    except subprocess.TimeoutExpired:
        error = "environment_unavailable: rung exceeded its wall-clock timeout"
    return row_for(rung, report, observer, time.perf_counter() - started, error)


def reobserve(rung, report_path, out_dir, timeout):
    """Rerun only the independent observer against an earlier passing harness state; the row says so."""
    started = time.perf_counter()
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    report["_path"] = str(report_path)
    home = Path(report["resume_state"]) if report.get("resume_state") else Path(report_path).parent / "state"
    observer_out = out_dir / f"{rung['id']}-observer.json"
    checked = subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_rest_export.py"),
                              *observer_args(rung, home, observer_out)], cwd=ROOT, capture_output=True, text=True,
                             timeout=timeout)
    (out_dir / f"{rung['id']}-observer.log").write_text(checked.stdout + checked.stderr, encoding="utf-8")
    observer = load_report(checked.stdout, "Observer report:") or {
        "status": "failed", "error": "observer produced no report: " + (checked.stderr.strip().splitlines() or ["no output"])[-1]}
    row = row_for(rung, report, observer, time.perf_counter() - started)
    row["reobserved_from"] = str(report_path)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "scripts" / "ladder.json")
    parser.add_argument("--only", action="append", default=[], help="Run only these rung ids")
    parser.add_argument("--reobserve", action="append", default=[],
                        help="rung=path/to/report.json: rerun only the observer against that earlier harness run")
    parser.add_argument("--timeout", type=int, default=45 * 60, help="Wall-clock seconds per rung step")
    args = parser.parse_args()
    config = {r["id"]: r for r in json.loads(args.config.read_text(encoding="utf-8"))["rungs"]}
    rungs = [r for r in config.values() if r.get("enabled", True) and (not args.only or r["id"] in args.only)]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M") + "-" + uuid4().hex[:4]
    out_dir = ROOT / "artifacts" / "ladder" / stamp
    out_dir.mkdir(parents=True)
    rows = []
    for item in args.reobserve:
        rung_id, _, report_path = item.partition("=")
        print(f"== {rung_id}: observer only, from {report_path}", flush=True)
        rows.append(reobserve(config[rung_id], report_path, out_dir, args.timeout))
        print(json.dumps({k: rows[-1][k] for k in ("status", "failure_mode")}), flush=True)
    for rung in [] if args.reobserve else rungs:
        print(f"== {rung['id']}: {rung['url']}", flush=True)
        rows.append(run_rung(rung, out_dir, args.timeout))
        print(json.dumps({k: rows[-1][k] for k in ("status", "failure_mode", "rounds_used", "estimated_cost_usd")}), flush=True)
        (out_dir / "ladder.json").write_text(json.dumps({"generated_at": stamp, "rows": rows}, indent=2), encoding="utf-8")
        (out_dir / "SUMMARY.md").write_text(render_markdown(rows, stamp), encoding="utf-8")
    print("Ladder: " + str(out_dir / "SUMMARY.md"))


if __name__ == "__main__":
    main()
