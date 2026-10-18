"""Learn an empty website's record forms without losing ownership of the experiment."""
from __future__ import annotations

import asyncio
from uuid import uuid4

from .programs import Program, ProgramError, ProgramRunner, Step, at, validate_program
from .recorder import redact
from .secrets import load_session, save_session

RESOURCE = "_record_probe"


class ProbeAborted(ValueError):
    """The probe failed while preparing the form, before anything could reach the website."""


def creation_steps(values):
    steps = [Step.model_validate(value) for value in values]
    if not 2 <= len(steps) <= 12 or any(step.steps or step.otherwise for step in steps):
        raise ValueError("A record probe needs 2–12 flat UI steps")
    if any(step.kind not in ("fill", "select") for step in steps[:-1]):
        raise ValueError("Open the new-record form through exploration first, which can navigate and "
                         "click but not submit; then prepare it here using fill/select only")
    if steps[-1].kind != "click" and not (steps[-1].kind == "press" and steps[-1].value == "Enter"):
        raise ValueError("A record probe ends with one submit click or Enter")
    if not any(step.kind == "fill" and step.value == {"$ref": "input.marker"} for step in steps[:-1]):
        raise ValueError("Fill one record field with the runtime-owned input.marker")
    return steps


def contains_marker(value, marker):
    if isinstance(value, str):
        return marker in value
    if isinstance(value, dict):
        return any(contains_marker(child, marker) for child in value.values())
    if isinstance(value, list):
        return any(contains_marker(child, marker) for child in value)
    return False


class ObservationRunner(ProgramRunner):
    observed = False

    async def execute_steps(self, steps):
        for step in steps:
            await super().execute_steps([step])
            if step.kind in ("request", "read", "download"):
                self.observed = True


class RecordProbe:
    def __init__(self, home, manifest):
        self.manifest = manifest
        self.path = home / "recovery" / (manifest.id + "-record.session")

    def pending(self):
        value = load_session(self.path)
        if value and value.get("status") != "removed":
            if value.get("target") != self.manifest.target:
                raise ValueError("An unresolved record probe belongs to this connector's earlier target")
            return value
        return None

    def save(self, record):
        save_session(self.path, record)
        self.sync_manifest()

    def sync_manifest(self):
        self.manifest.capabilities = [c for c in self.manifest.capabilities if c.get("resource") != RESOURCE
            and not (c.get("resource") == "record_probe" and c.get("recovery") == str(self.path))]
        record = self.pending()
        if record:
            self.manifest.capabilities.append({"resource": RESOURCE, "status": "incomplete",
                "operations": {verb: False for verb in ("create", "read", "update", "delete")},
                "recovery": str(self.path), "cleanup": [{"status": "needs_attention", "id": record.get("id")}]})

    def summary(self):
        record = self.pending()
        return {key: record.get(key) for key in ("marker", "id", "status", "page_url", "error")} if record else None

    async def begin(self, session, steps):
        if self.pending():
            raise ValueError("Resolve the existing record probe; never repeat its creation")
        steps = creation_steps(steps)
        await session.start()
        await session.save()
        record = {"target": self.manifest.target, "marker": "Conduit learning " + uuid4().hex,
                  "status": "creation_started", "page_url": session.page.url, "delete_started": False}
        self.save(record)
        runner = ProgramRunner(session, self.manifest)
        arguments = {"marker": record["marker"]}
        try:
            # Preparing the form cannot create anything: nothing leaves the browser until the final
            # submit. A miss here is a wrong locator, not an uncertain write, so the probe is closed
            # outright instead of demanding a reconciliation nothing could need.
            try:
                await runner.run(Program(steps=steps[:-1]), arguments)
            except Exception as exc:
                record.update(status="removed", aborted_before_submit=True,
                              error=redact(str(exc).splitlines()[0])[:350])
                raise ProbeAborted("The record probe stopped before its submit, so nothing was created; fix "
                                   "the failing step and probe again: " + record["error"]) from exc
            await runner.run(Program(steps=steps[-1:]), arguments)
            record["status"] = "needs_inspection"
        except ProbeAborted:
            raise
        except Exception as exc:
            record["error"] = redact(str(exc).splitlines()[0])[:350]
            raise
        finally:
            record["page_url"] = session.page.url
            self.save(record)
        return self.summary()

    async def recover(self, session, inspection, remove=None, *, object_id=None, id_path="id"):
        record = self.pending()
        if not record:
            raise ValueError("No ordinary record probe needs recovery")
        inspection = Program.model_validate(inspection)
        validate_program(inspection, read_only=True)
        remove = Program.model_validate(remove) if remove else None
        if remove:
            validate_program(remove)
        if record.get("id") is not None and object_id not in (None, record["id"]):
            raise ValueError("Recovery cannot switch to a different record")
        arguments = {"marker": record["marker"], "id": record.get("id", object_id)}

        async def read():
            runner = ObservationRunner(session, self.manifest)
            value, _ = await runner.run(inspection, arguments, read_only=True)
            if not runner.observed:
                raise ValueError("Record inspection must perform an actual website read")
            if value is not None and not isinstance(value, dict):
                raise ValueError("Record inspection returns the full record object, or null if absent")
            return value

        current = await read()
        if current is None and "observed_record" not in record:
            raise ProgramError("probe_needs_attention",
                "Creation has no confirmed identity. An absent search result does not authorize another create.", uncertain=True)
        if current is not None:
            key = at(current, id_path, None)
            if isinstance(key, bool) or not isinstance(key, (str, int)) or key == "":
                raise ValueError("Inspection must return the actual native record identifier")
            if arguments["id"] is not None and key != arguments["id"]:
                raise ValueError("Inspection returned another record identifier")
            if not contains_marker(current, record["marker"]):
                raise ProgramError("conflict", "This record does not contain the probe's unique ownership marker")
            if "observed_record" in record and current != record["observed_record"]:
                # A live application moves timestamps and activity counters between any two reads, so
                # whole-record equality would forbid deleting the probe's own record on every real site.
                # Ownership is the id plus the unique marker, both just confirmed; the drift is logged.
                changed = sorted(k for k in set(current) | set(record["observed_record"])
                                 if current.get(k) != record["observed_record"].get(k))
                record["drift_since_inspection"] = changed[:20]
            if record["delete_started"]:
                raise ProgramError("probe_needs_attention",
                    "The earlier deletion is still unconfirmed. No deletion was repeated.", uncertain=True)
            record.update(id=key, observed_record=current, status="observed")
            arguments["id"] = key
            self.save(record)
            if remove is None:
                return {"record": current, "id": key, "removed": False}
            record.update(status="delete_started", delete_started=True)
            self.save(record)
            failure = None
            try:
                await ProgramRunner(session, self.manifest).run(remove, arguments, context={"before": current})
            except Exception as exc:
                failure = exc
                record["error"] = redact(str(exc).splitlines()[0])[:350]
                self.save(record)
            for attempt in range(5):
                if await read() is None:
                    break
                if attempt == 4:
                    if failure is not None:
                        # The removal failed and the owned record is still there, so the deletion did not
                        # take effect. A removal that reaches the website leaves no record to read back;
                        # one that stops at a bad step, an invalid program or an unconfirmed dialog leaves
                        # it exactly where it was. Hand the step back to be corrected rather than locking
                        # the probe behind a reconciliation nothing could need.
                        record.update(status="observed", delete_started=False)
                        self.save(record)
                        raise ProbeAborted("The removal failed and the record is still present, so nothing was "
                                           "deleted; fix the failing step and call recover_record again: "
                                           + record["error"]) from failure
                    raise ProgramError("probe_needs_attention", "Fresh reads still found the probe after deletion", uncertain=True)
                await asyncio.sleep(0.2 * (attempt + 1))
        # Absence must survive reopening the authenticated session, including after a lost delete response.
        await session.save()
        await session.close()
        await session.start()
        if await read() is not None:
            raise ProgramError("probe_needs_attention", "The probe reappeared after browser restart", uncertain=True)
        record["status"] = "removed"
        self.save(record)
        return {"record": record["observed_record"], "id": record["id"], "removed": True, "restart_tested": True}
