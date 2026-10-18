"""Independent lifecycle checks shared by certification and normal write calls."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from uuid import uuid4

from jsonschema import Draft202012Validator

from .programs import Family, ProgramError, ProgramRunner, at, fingerprint, validate_program, walk
from .result_schema import family_schema
from .state_guard import TOKEN_PATTERN, state_token

VERBS = ("create", "read", "update", "delete")
VOLATILE = {"updated_at", "updatedAt", "modified_at", "modifiedAt", "lastModified", "version", "revision"}


def inputs(schema, value):
    Draft202012Validator.check_schema(schema)
    errors = list(Draft202012Validator(schema).iter_errors(value))
    if errors:
        raise ValueError(f"Input does not match the operation schema: {errors[0].validator}")


def validate_family(family):
    if not set(VERBS) <= set(family.operations):
        raise ValueError("A complete family needs create, read, update, and delete programs")
    if not family.fields:
        raise ValueError("At least one editable field must have an independent readback binding")
    if not family.evidence:
        raise ValueError("Programs need references to observed website evidence")
    for path in family.managed_fields:
        if not path or any(not part for part in path.split(".")) or any(
            path == protected or path.startswith(protected + ".") or protected.startswith(path + ".")
            for protected in [family.id_path, *family.fields.values()]
        ):
            raise ValueError("Managed metadata must not cover identity or editable content fields")
    if family.persistence:
        validate_program(family.persistence, read_only=True)
    if family.document_inspection:
        validate_program(family.document_inspection, read_only=True)
    for verb, operation in family.operations.items():
        if "expected_state_token" in operation.input_schema.get("properties", {}):
            raise ValueError("expected_state_token is reserved for the runtime state guard")
        validate_program(operation.program, read_only=verb in ("read", "list"))
        Draft202012Validator.check_schema(operation.input_schema)
        if verb in ("read", "list") and not operation.program.javascript and not any(
            s.kind in ("request", "read", "download", "command") for s in walk(operation.program.steps)
        ):
            raise ValueError("Readback must observe the application, not return cached or supplied arguments")
    inputs(family.operations["create"].input_schema, family.create_input)
    if family.operations["create"].program.result is None and not family.operations["create"].program.javascript:
        raise ValueError("Create must return the observed object and its actual identifier")
    if vary(family.create_input, "trial-a", family.operations["create"].input_schema) == vary(
        family.create_input, "trial-b", family.operations["create"].input_schema
    ):
        raise ValueError("Create needs distinct disposable test values; use Conduit {{nonce}} in an editable string")
    if not any(at(family.create_input, key, None) != at(family.update_input, key, None)
               for key in family.fields if at(family.update_input, key, None) is not None):
        raise ValueError("The update example must actually change an observable field")


def record(family, value):
    return at(value, family.item_path) if value is not None and family.item_path else value


def matches(family, actual, expected):
    if not isinstance(actual, dict):
        return False
    checked = False
    missing = object()
    for key, destination in family.fields.items():
        value = at(expected, key, missing)
        if value is not missing:
            checked = True
            if at(actual, destination, missing) != value:
                return False
    return checked


def preservation_changes(before, after, changed, revision=None, prefix=""):
    if not isinstance(before, dict) or not isinstance(after, dict):
        if before != after:
            yield prefix or "(object)"
        return
    for key, value in before.items():
        path = f"{prefix}.{key}".strip(".")
        if path in changed or path == revision or key in VOLATILE:
            continue
        if key not in after:
            yield path
        elif isinstance(value, dict) and isinstance(after[key], dict):
            yield from preservation_changes(value, after[key], changed, revision, path)
        elif after[key] != value:
            yield path


def preserved(before, after, changed, revision=None, prefix=""):
    return next(preservation_changes(before, after, changed, revision, prefix), None) is None


def managed_changes(family, before, after):
    """Allow only typed metadata changes with the declared meaning, never whole subtrees."""
    allowed = set()
    for path, rule in family.managed_fields.items():
        old, new = at(before, path, None), at(after, path, None)
        if type(old) is not int or type(new) is not int:
            continue
        if rule.kind == "timestamp_ms":
            now = int(time.time() * 1000)
            valid = old <= new and abs(new - now) <= 300000
        else:
            valid = 0 <= old < 2**31 and 0 <= new < 2**31
        if valid:
            allowed.add(path)
    return allowed


class FamilyExecutor:
    def __init__(self, session, manifest, family):
        self.session, self.manifest = session, manifest
        self.adapter_fingerprint = fingerprint(family)
        self.family = Family.model_validate(family)
        self.metrics = {"requests": 0, "steps": 0, "model_calls": 0, "screenshots": 0}
        self.before_document = None

    async def raw(self, verb, args, *, before=None):
        operation = self.family.operations[verb]
        inputs(operation.input_schema, args)
        runner = ProgramRunner(self.session, self.manifest)
        try:
            value, stats = await runner.run(operation.program, args, context={"before": before},
                                             read_only=verb in ("read", "list"))
        except ProgramError:
            if self.family.document_inspection:
                self.before_document = runner.vars.get("document_before")
            raise
        for key in self.metrics:
            self.metrics[key] += stats.get(key, 0)
        return value

    async def read(self, key):
        return record(self.family, await self.raw("read", {"id": key}))

    async def ensure_persisted(self, args, state, key, before=None):
        if self.family.persistence:
            try:
                persisted, stats = await ProgramRunner(self.session, self.manifest).run(self.family.persistence, args,
                    context={"before": before, "after": state, "id": key}, read_only=True)
                if persisted is False:
                    raise ProgramError("persistence_failed", "The application did not confirm that the change was saved")
            except ProgramError as exc:
                exc.uncertain = True  # The mutation already ran, even though this check is read-only.
                exc.object_id = key
                raise
            for metric in self.metrics:
                self.metrics[metric] += stats.get(metric, 0)

    async def save(self, verb, key):
        try:
            await self.session.save()
        except Exception:
            error = ProgramError("session_persistence_failed",
                "The website operation ran, but its local session could not be saved; do not repeat the mutation",
                uncertain=verb != "read")
            error.object_id = key
            raise error from None

    async def settle(self, key, predicate, *, arguments=None, before=None):
        for attempt in range(6):
            try:
                value = await self.read(key)
            except ProgramError as exc:
                exc.uncertain = True
                exc.object_id = key
                raise
            if predicate(value):
                return value
            if attempt < 5:
                await asyncio.sleep(0.2 * (attempt + 1))
        from itertools import islice
        missing = object()
        diagnostics = {"object_present": value is not None,
                       "identity_matches": at(value, self.family.id_path, None) == key}
        if arguments is not None:
            diagnostics["mismatched_fields"] = [dest for src, dest in self.family.fields.items()
                if at(arguments, src, missing) is not missing and at(value, dest, missing) != at(arguments, src)][:20]
        if before is not None:
            diagnostics["unrelated_changed_fields"] = list(islice(preservation_changes(
                before, value, self.changed(arguments or {}) | managed_changes(self.family, before, value),
                self.family.revision_path), 20))
        error = ProgramError("postcondition_failed", "Fresh readback did not confirm the intended change", uncertain=True)
        error.object_id, error.postcondition = key, diagnostics
        raise error

    async def call(self, verb, args):
        start = time.perf_counter()
        self.metrics = {key: 0 for key in self.metrics}
        args = dict(args)
        state_supplied = "expected_state_token" in args
        expected_state = args.pop("expected_state_token", None)
        if state_supplied and (verb not in ("update", "delete") or not isinstance(expected_state, str)
                                           or not re.fullmatch(TOKEN_PATTERN, expected_state)):
            raise ValueError("expected_state_token must be a state_token returned by a previous read")
        inputs(self.family.operations[verb].input_schema, args)
        before = None
        key = args.get("id")
        if verb in ("update", "delete"):
            before = await self.read(key)
            if before is None or at(before, self.family.id_path, None) != key:
                raise ProgramError("object_missing", "The requested object was not found with this login")
            if expected_state is not None and state_token(self.adapter_fingerprint, key, before) != expected_state:
                raise ProgramError("conflict", "The object's content changed since it was read")
            revision = self.family.revision_path
            if revision and "expected_revision" in args and at(before, revision) != args["expected_revision"]:
                raise ProgramError("conflict", "The object was edited since it was read")
        try:
            response = await self.raw(verb, args, before=before)
        except ProgramError as exc:
            # Reconcile a lost response only when identity and the effect are independently knowable.
            if exc.uncertain and verb in ("update", "delete"):
                try:
                    state = await self.settle(key, lambda item: item is None if verb == "delete" else
                                              matches(self.family, item, args) and
                                              at(item, self.family.id_path, None) == key and
                                              self.preserves(before, item, args),
                                              arguments=args, before=before if verb == "update" else None)
                except Exception as check_error:
                    if hasattr(check_error, "postcondition"):
                        exc.postcondition = check_error.postcondition
                    exc.object_id = key
                    raise exc from None
                await self.ensure_persisted(args, state, key, before)
                await self.save(verb, key)
                return self.receipt(verb, state, key, start, reconciled=True)
            raise
        if verb == "create":
            created = record(self.family, response)
            key = at(created, self.family.id_path, None)
            if key is None:
                raise ProgramError("outcome_unknown", "The website did not return the new object's identifier",
                                   uncertain=True)
            state = await self.settle(key, lambda item: matches(self.family, item, args) and
                                      at(item, self.family.id_path, None) == key, arguments=args)
        elif verb == "update":
            state = await self.settle(key, lambda item: matches(self.family, item, args) and
                                      at(item, self.family.id_path, None) == key and
                                      self.preserves(before, item, args),
                                      arguments=args, before=before)
        elif verb == "delete":
            state = await self.settle(key, lambda item: item is None)
        else:
            state = record(self.family, response)
            if state is not None and verb == "read" and at(state, self.family.id_path, None) != key:
                raise ProgramError("wrong_object", "Readback returned a different object")
        if verb != "read":
            await self.ensure_persisted(args, state, key, before)
        await self.save(verb, key)
        return self.receipt(verb, state, key, start)

    def changed(self, args):
        marker = object()
        return {dest for src, dest in self.family.fields.items() if at(args, src, marker) is not marker}

    def preserves(self, before, after, args):
        return preserved(before, after, self.changed(args) | managed_changes(self.family, before, after),
                         self.family.revision_path)

    def receipt(self, verb, state, key, start, reconciled=False):
        return {"status": "verified", "operation": verb, "id": key, "data": state,
                "state_token": state_token(self.adapter_fingerprint, key, state),
                "deleted": verb == "delete", "reconciled": reconciled,
                "adapter_version": self.adapter_fingerprint,
                "metrics": {**self.metrics, "duration_ms": round((time.perf_counter() - start) * 1000, 2)}}


def vary(value, nonce, schema=None):
    schema = schema or {}
    if isinstance(value, dict):
        return {k: vary(v, nonce, schema.get("properties", {}).get(k, {})) for k, v in value.items()}
    if isinstance(value, list):
        return [vary(v, nonce, schema.get("items", {})) for v in value]
    if isinstance(value, str):
        if "{{nonce}}" in value:
            return value.replace("{{nonce}}", nonce)
        # Only vary explicit test strings. Keep selectors, enum values, foreign keys, etc. intact.
        if value.startswith("Conduit ") and not schema.get("enum"):
            return value + " " + nonce
    return value


async def verify_family(session, manifest, family, progress=None, cycles=3, *, reference=None):
    family = Family.model_validate(family)
    validate_family(family)
    report = {"resource": family.name, "status": "testing", "operations": {v: False for v in VERBS},
              "cases": [], "fingerprint": fingerprint(family), "cleanup": [], "restart_tested": False}
    live = []
    unknown_create = False
    previous_ids = set()
    observed_records = []
    start = time.perf_counter()
    reference = Family.model_validate(reference) if reference is not None else None
    if reference:
        report.update(reference_fingerprint=fingerprint(reference), reference_checks=0)

    async def compare_reference(key, value):
        if reference:
            from .mcp_check import assert_record_matches
            observed = await FamilyExecutor(session, manifest, reference).read(key)
            assert_record_matches(observed, value)
            report["reference_checks"] += 1

    async def create_for_learning(executor, arguments, nonce):
        nonlocal unknown_create
        since = session.recorder.sequence
        try:
            raw = await executor.raw("create", arguments)
            key = at(record(family, raw), family.id_path, None)
            if key is None or str(key) in previous_ids:
                from .recorder import shape
                report["create_result_shape"] = shape(raw)
                raise ProgramError("create_identifier_missing", "Create did not extract a fresh identifier from its response",
                                   uncertain=True)
        except ProgramError as exc:
            if exc.uncertain:
                observed_documents = []
                if family.document_inspection and executor.before_document is not None:
                    try:
                        current, _ = await ProgramRunner(session, manifest).run(family.document_inspection, {}, read_only=True)
                        if current == executor.before_document:
                            # Menus can fail before any upload. A fresh full export proves no net edit.
                            report["creation_no_effect_confirmed"] = True
                            exc.uncertain = False
                        else:
                            observed_documents.append(current)
                    except Exception:
                        pass
                if not exc.uncertain:
                    raise
                # A received response with a bad result binding is different from a lost response.
                # Recover identity only for cleanup, after a unique nonce and fresh read prove ownership.
                key = await recover_created_id(executor, arguments, nonce, since, previous_ids, observed_documents)
                unknown_create = key is None
                if key is not None:
                    live.append(key)
                    previous_ids.add(str(key))
                    report["creation_identity_recovered"] = True
            raise
        live.append(key)
        previous_ids.add(str(key))
        return key

    try:
        if family.document_inspection:
            session.recorder.phase = f"verify:{family.name}:inspect"
            original, _ = await ProgramRunner(session, manifest).run(family.document_inspection, {}, read_only=True)
            if not isinstance(original, dict):
                raise ValueError("Document inspection did not return an actual document object")
        for cycle in range(cycles):
            if progress:
                await progress(f"Testing {family.label}: create, read, update, delete ({cycle + 1}/{cycles})")
            if cycle == cycles - 1 and cycle > 0:
                await session.save()
                await session.close()
                await session.start()
                report["restart_tested"] = True
            nonce = uuid4().hex[:10]
            create = vary(family.create_input, nonce, family.operations["create"].input_schema)
            update = vary(family.update_input, nonce, family.operations["update"].input_schema)
            executor = FamilyExecutor(session, manifest, family)
            session.recorder.phase = f"verify:{family.name}:{cycle}:control"
            control_nonce = "control-" + uuid4().hex[:10]
            control_input = vary(family.create_input, control_nonce,
                                 family.operations["create"].input_schema)
            control_id = await create_for_learning(executor, control_input, control_nonce)
            control_state = await executor.settle(control_id, lambda item, control_input=control_input:
                                                  matches(family, item, control_input), arguments=control_input)
            await executor.ensure_persisted(control_input, control_state, control_id)
            await compare_reference(control_id, control_state)
            session.recorder.phase = f"verify:{family.name}:{cycle}:create"
            # Keep the ID even if postcondition validation fails, so cleanup can still run.
            executor = FamilyExecutor(session, manifest, family)
            created_at = time.perf_counter()
            key = await create_for_learning(executor, create, nonce)
            created = await executor.settle(key, lambda item, create=create, key=key: matches(family, item, create) and
                                           at(item, family.id_path, None) == key, arguments=create)
            await executor.ensure_persisted(create, created, key)
            create_metrics = {**executor.metrics, "duration_ms": round((time.perf_counter() - created_at) * 1000, 2)}
            await compare_reference(key, created)
            if await executor.read(control_id) != control_state:
                raise ValueError("Creating another object changed an unrelated control object")
            await compare_reference(control_id, control_state)
            report["operations"]["create"] = True
            session.recorder.phase = f"verify:{family.name}:{cycle}:read"
            readback = await executor.call("read", {"id": key})
            await compare_reference(key, readback["data"])
            report["operations"]["read"] = True
            session.recorder.phase = f"verify:{family.name}:{cycle}:update"
            changed = {"id": key, **update}
            if family.revision_path and "expected_revision" in family.operations["update"].input_schema.get("properties", {}):
                changed["expected_revision"] = at(created, family.revision_path)
            try:
                edited = await executor.call("update", changed)
            except ProgramError as exc:
                # Update is not on the critical path: a site can expose creating and reading a record
                # without a usable edit control. Record why and keep going, so the disposable records
                # are still cleaned up and the operations that do work can publish. An update whose
                # outcome is unknown is a real write and still stops everything.
                if exc.uncertain or report["operations"]["update"]:
                    raise
                report.setdefault("unavailable", {})["update"] = str(exc)[:300]
                edited = None
            if edited is not None:
                await compare_reference(key, edited["data"])
                observed_records.extend((control_state, created, edited["data"]))
                if await executor.read(control_id) != control_state:
                    raise ValueError("Updating an object changed an unrelated control object")
                await compare_reference(control_id, control_state)
                report["operations"]["update"] = True
                # Verify persistence through a freshly opened page as well as the read program.
                await session.page.reload(wait_until="domcontentloaded")
                reopened = await executor.settle(key, lambda item, changed=changed: matches(family, item, changed), arguments=changed)
                await compare_reference(key, reopened)
            else:
                observed_records.extend((control_state, created))
            session.recorder.phase = f"verify:{family.name}:{cycle}:delete"
            deleted = await executor.call("delete", {"id": key})
            await compare_reference(key, None)
            if await executor.read(control_id) != control_state:
                raise ValueError("Deleting an object changed an unrelated control object")
            await compare_reference(control_id, control_state)
            report["operations"]["delete"] = True
            live.remove(key)
            await executor.call("delete", {"id": control_id})
            await compare_reference(control_id, None)
            live.remove(control_id)
            report["cases"].append({"input_hash": hashlib.sha256(json.dumps(create, sort_keys=True).encode()).hexdigest(),
                                     "create": create_metrics, "read": readback["metrics"],
                                     "update": edited["metrics"] if edited else None, "delete": deleted["metrics"],
                                     "model_calls": 0, "screenshots": 0, "reopened": True,
                                     "unrelated_object_preserved": True})
        if len({case["input_hash"] for case in report["cases"]}) < min(2, cycles):
            raise ValueError("Certification needs distinct test inputs; use {{nonce}} in a test string")
        report["result_schema"] = family_schema(family, observed_records)
        report["status"] = "ready"
    except Exception as exc:
        report.update(status="incomplete", error=str(exc) if isinstance(exc, ValueError) else type(exc).__name__,
                      failed_phase=session.recorder.phase, error_code=getattr(exc, "code", "verification_error"))
        if hasattr(exc, "postcondition"):
            report["postcondition"] = exc.postcondition
        if getattr(exc, "observations", None):
            report["execution_observations"] = exc.observations
    finally:
        if unknown_create:
            report["cleanup"].append({"status": "needs_attention", "outcome_unknown": True,
                "reason": "A create response was lost or had no fresh ID. Inspect the test object before further experiments."})
        for key in live:
            try:
                await FamilyExecutor(session, manifest, family).call("delete", {"id": key})
                await compare_reference(key, None)
                report["cleanup"].append({"status": "removed"})
            except Exception:
                report["cleanup"].append({"status": "needs_attention", "id": key})
        report["duration_ms"] = round((time.perf_counter() - start) * 1000, 2)
        report["tested_at"] = datetime.now(timezone.utc).isoformat()
        session.recorder.phase = "runtime"
    return report


async def recover_created_id(executor, arguments, nonce, since, previous_ids, documents=()):
    """Never retry creation. A matching response alone is insufficient to authorize cleanup."""
    family, session = executor.family, executor.session
    await session.recorder.flush()
    candidates = {}
    def inspect(value):
        if isinstance(value, dict):
            key = at(value, family.id_path, None)
            if isinstance(key, (str, int)) and str(key) not in previous_ids and matches(family, value, arguments):
                if nonce in json.dumps(value):
                    candidates[str(key)] = key
            for child in value.values():
                inspect(child)
        elif isinstance(value, list):
            for child in value[:60]:
                inspect(child)
    for event in session.recorder.events:
        if event["id"].startswith("e") and int(event["id"][1:]) > since and event.get("method") in ("POST", "PUT", "PATCH"):
            inspect(event.get("response"))
    for document in documents:
        inspect(document)
    if len(candidates) != 1:
        return None
    key = next(iter(candidates.values()))
    try:
        current = await executor.read(key)
        if matches(family, current, arguments) and at(current, family.id_path, None) == key:
            return key
    except Exception:
        pass
    return None
