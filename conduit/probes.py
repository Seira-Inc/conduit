"""Learn real document objects through a reversible UI experiment."""
from pathlib import Path
from uuid import uuid4

from .programs import Program, ProgramError, ProgramRunner, Step, validate_program, walk
from .recorder import redact
from .secrets import load_session, save_session


def document_programs(inspection, restore):
    inspection, restore = Program.model_validate(inspection), Program.model_validate(restore)
    validate_program(inspection, read_only=True)
    if not inspection.javascript and not any(s.kind in ("download", "read", "request", "command") for s in walk(inspection.steps)):
        raise ValueError("A document probe needs an actual read/export program")
    uploads = [s for s in walk(restore.steps) if s.kind == "upload"]
    if len(uploads) != 1 or uploads[0].value != {"$ref": "context.before"} or uploads[0].expected_document != {"$ref": "context.after"}:
        raise ValueError("Probe restoration must upload context.before and guard against context.after")
    if any(s.kind not in ("click", "press", "read", "set", "if", "poll", "assert", "download", "upload") for s in walk(restore.steps)):
        raise ValueError("Restoration may only navigate controls and import the original document")
    validate_program(restore)
    return inspection, restore


async def confirm_saved(session, manifest, program, before, after):
    if program:
        value, _ = await ProgramRunner(session, manifest).run(program, {},
            context={"before": before, "after": after}, read_only=True)
        if value is False:
            raise ProgramError("persistence_failed", "The probe's save check did not confirm durable state", uncertain=True)


async def probe_document(session, manifest, inspection, steps, restore, persistence=None):
    inspection, restore = document_programs(inspection, restore)
    persistence = Program.model_validate(persistence) if persistence else None
    if persistence:
        validate_program(persistence, read_only=True)
    steps = [Step.model_validate(s) for s in steps]
    if len(steps) > 12 or any(s.kind not in ("click", "fill", "press", "draw") for s in walk(steps)):
        raise ValueError("Document probes use at most 12 observed UI steps")
    before, _ = await ProgramRunner(session, manifest).run(inspection, {}, read_only=True)
    if not isinstance(before, dict):
        raise ValueError("Document inspection must return a JSON document object")
    recovery = session.home / "recovery" / (manifest.id + "-" + uuid4().hex[:8] + ".session")
    # Persist before the first UI mutation, including if the process is interrupted.
    recovery_record = {"target": manifest.target, "document": before,
                       "persistence": persistence.model_dump() if persistence else None}
    save_session(recovery, recovery_record)
    after, failure = None, None
    try:
        await ProgramRunner(session, manifest).run(Program(steps=steps), {})
        after, _ = await ProgramRunner(session, manifest).run(inspection, {}, read_only=True)
        await confirm_saved(session, manifest, persistence, before, after)
        # Establish the conflict baseline from the saved, normalized representation.
        await session.save()
        await session.close()
        await session.start()
        after, _ = await ProgramRunner(session, manifest).run(inspection, {}, read_only=True)
    except Exception as exc:
        failure = exc
    finally:
        try:
            if after is None:
                after, _ = await ProgramRunner(session, manifest).run(inspection, {}, read_only=True)
            save_session(recovery, {**recovery_record, "after": after})
            if after != before:
                await ProgramRunner(session, manifest).run(restore, {}, context={"before": before, "after": after})
            restored, _ = await ProgramRunner(session, manifest).run(inspection, {}, read_only=True)
            if restored != before:
                raise ValueError("The original document was not restored exactly")
            await confirm_saved(session, manifest, persistence, after, before)
            await session.save()
            await session.close()
            await session.start()
            reopened, _ = await ProgramRunner(session, manifest).run(inspection, {}, read_only=True)
            if reopened != before:
                raise ValueError("Restoration did not survive browser restart; verify the application's durable save state")
            recovery.unlink()
        except Exception as exc:
            cause = redact(str(exc).splitlines()[0])[:350]
            original = (redact(str(failure).splitlines()[0])[:200] + "; ") if failure else ""
            error = ProgramError("probe_needs_attention",
                f"The experiment could not confirm restoration: {original}{cause}. Original document saved locally at {recovery}.",
                uncertain=True)
            error.recovery = str(recovery) if after is not None else None
            raise error from None
    if failure:
        raise failure
    if after == before:
        raise ValueError("The UI experiment did not create an observable document change")
    return {"before": before, "after": after, "restored": True}


async def recover_probe(session, manifest, recovery, inspection, restore, persistence=None):
    """Repair the import path without drawing again or overwriting intervening changes."""
    path = Path(recovery).resolve()
    if not path.is_relative_to((session.home / "recovery").resolve()):
        raise ValueError("Recovery record must belong to this local execution session")
    saved = load_session(path)
    if not saved or saved.get("target") != manifest.target or not isinstance(saved.get("after"), dict):
        raise ValueError("Recovery needs the original document and its observed post-probe state")
    inspection, restore = document_programs(inspection, restore)
    persistence = persistence or saved.get("persistence")
    persistence = Program.model_validate(persistence) if persistence else None
    if persistence:
        validate_program(persistence, read_only=True)
    before, after = saved["document"], saved["after"]
    current, _ = await ProgramRunner(session, manifest).run(inspection, {}, read_only=True)
    if current != before:
        if current != after:
            raise ProgramError("conflict", "The document changed after the probe; recovery did not overwrite it")
        await ProgramRunner(session, manifest).run(restore, {}, context={"before": before, "after": after})
    observed, _ = await ProgramRunner(session, manifest).run(inspection, {}, read_only=True)
    if observed != before:
        raise ProgramError("probe_needs_attention", "Fresh export did not confirm the original document was restored", uncertain=True)
    await confirm_saved(session, manifest, persistence, after, before)
    await session.save()
    await session.close()
    await session.start()
    reopened, _ = await ProgramRunner(session, manifest).run(inspection, {}, read_only=True)
    if reopened != before:
        raise ProgramError("probe_needs_attention", "Restoration did not survive browser restart; verify durable save state", uncertain=True)
    save_session(path, {**saved, "status": "restored"})
    return {"before": before, "after": after, "restored": True}
