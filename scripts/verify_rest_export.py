"""Independent acceptance checker for a generated public-site MCP, configured per site rather than coded.

This is test code, never compiler input or an execution adapter. It reads full records through the
website's own JSON API, independently of the generated read tool, and publishes only after a pass.
"""
# ruff: noqa: E402
import argparse
import asyncio
import copy
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=False)

from conduit.browser import BrowserSession
from conduit.generator import Store
from conduit.mcp_check import verify_export
from conduit.models import load_manifest, normalize_url
from conduit.programs import fingerprint


def dig(value, path):
    for part in [p for p in (path or "").split(".") if p]:
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def build_observer(config, request, token=None, checks=None):
    """Observer for mcp_check.verify_export. `request(url, headers)` returns (status, json_or_none)."""
    checks = checks if checks is not None else []
    observed = {}
    fields = dict(config.get("fields", {}))
    ignore = set(config.get("ignore", []))
    headers = {"Accept": "application/json", **config.get("headers", {})}
    if token:
        headers[config.get("auth_header", "Authorization")] = token

    async def observe(resource, verb, arguments, result):
        key = result["id"]
        status, payload = await request(config["read_url"].format(id=key), headers)
        if verb == "delete" or result["data"] is None:
            flag = config.get("deleted_flag")
            soft_deleted = bool(flag and status == 200 and isinstance(payload, dict)
                                and dig(dig(payload, config.get("record_path")), flag) is True)
            assert status == config.get("deleted_status", 404) or soft_deleted, \
                f"Independent read still finds the deleted record ({status})"
            checks.append({"verb": verb, "absent": True, **({"soft_deleted": True} if soft_deleted else {})})
            return
        assert status == 200, f"Independent record read failed ({status})"
        actual = dig(payload, config.get("record_path"))
        assert str(dig(actual, config.get("id_path", "id"))) == str(key), "Independent read returned another record"
        for result_key, api_key in fields.items():
            expected = arguments.get(result_key, (result["data"] or {}).get(result_key))
            if expected is not None:
                assert dig(actual, api_key) == expected, f"Independent read disagrees on {api_key}"
        if key in observed:
            before, after = copy.deepcopy(observed[key]), copy.deepcopy(actual)
            for field in ignore:
                before.pop(field, None)
                after.pop(field, None)
            if verb == "update":
                for result_key, api_key in fields.items():
                    if result_key in arguments and "." not in api_key:
                        before[api_key] = arguments[result_key]
            changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
            assert not changed, f"Unrelated full-record properties changed: {changed[:12]}"
        observed[key] = copy.deepcopy(actual)
        checks.append({"verb": verb, "full_record_fields_checked": len(actual)})

    return observe


async def run(args):
    home = args.home.resolve()
    staged = max(home.glob(f"checks/*/connectors/{args.connector}/connector.json"), key=lambda p: p.stat().st_mtime)
    manifest = load_manifest(staged)
    if manifest.target != normalize_url(args.target):
        raise ValueError("This observer only accepts state for the configured target")
    config = {"read_url": args.read_url, "fields": dict(f.split("=", 1) for f in args.field), "ignore": args.ignore,
              "deleted_status": args.deleted_status, "deleted_flag": args.deleted_flag, "record_path": args.record_path,
              "id_path": args.id_path, "headers": dict(h.split("=", 1) for h in args.header)}
    signatures = {t.name: fingerprint(t.config["family"]) for t in manifest.tools if t.adapter == "program"}
    observer = BrowserSession(manifest, home)
    checks = []
    try:
        await observer.start()
        token = None
        if args.auth != "cookie":
            header = "authorization" if args.auth == "bearer" else args.auth.split(":", 1)[1]
            config["auth_header"] = "Authorization" if args.auth == "bearer" else header
            token = await observer.recorder.session_header(args.auth_host or manifest.allowed_origins[0], header)
            if not token:
                raise ValueError(f"The independent observer never saw a {header} header from the signed-in site")

        async def request(url, headers):
            response = await observer.context.request.get(url, headers=headers, max_redirects=0)
            try:
                payload = await response.json()
            except Exception:
                payload = None
            return response.status, payload

        directory = Store(staged.parents[2]).write(manifest)
        report = await verify_export(manifest, directory, home, observe=build_observer(config, request, token, checks))
        report["kind"] = "Unmodified generated public-site programs, independently checked over MCP"
        report["independent_checks"] = checks
        report["observer_config"] = {k: v for k, v in config.items() if k != "auth_header"} | {"auth": args.auth}
        report["fingerprints"] = signatures
        assert signatures == {t.name: fingerprint(t.config["family"]) for t in manifest.tools if t.adapter == "program"}
        args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({k: report.get(k) for k in ("status", "restarts", "independent_observer", "duration_ms")}, indent=2))
        print("Observer report: " + str(args.output.resolve()), flush=True)
        if report["status"] != "passed":
            print(report.get("error"))
            raise SystemExit(1)
        for capability in manifest.capabilities:
            if capability.get("status") == "ready":
                capability["mcp_test"] = report
        manifest.compilation["mcp_test"] = report
        manifest.compilation["status"] = "ready"
        manifest.compilation["acceptance_retest"] = str(args.output.resolve())
        Store(home).write(manifest)
    finally:
        await observer.close()


def parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("home", type=Path, help="The harness state directory holding the staged export")
    parser.add_argument("--target", required=True, help="The website URL the state belongs to")
    parser.add_argument("--read-url", required=True, help="Record read URL template containing {id}")
    parser.add_argument("--auth", default="cookie", help="cookie, bearer, or header:<Name> captured from the signed-in site")
    parser.add_argument("--auth-host", help="Origin whose captured request header supplies the token")
    parser.add_argument("--field", action="append", default=[], help="result_key=api_key pairs that must agree")
    parser.add_argument("--ignore", action="append", default=[], help="Server-managed top-level fields to ignore")
    parser.add_argument("--header", action="append", default=[], help="Extra Name=Value request header, e.g. X-Requested-With=XMLHttpRequest")
    parser.add_argument("--record-path", default="", help="Dotted path to the record inside the JSON response")
    parser.add_argument("--id-path", default="id", help="Dotted path to the identifier inside the record")
    parser.add_argument("--deleted-status", type=int, default=404)
    parser.add_argument("--deleted-flag", help="Boolean record field that marks a soft-deleted record as gone, e.g. deleted")
    parser.add_argument("--connector", default="public-acceptance")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "independent-mcp.json")
    return parser


if __name__ == "__main__":
    asyncio.run(run(parser().parse_args()))
