"""A resumed run keeps the writes the application was seen making, and survives refined notes."""

from types import SimpleNamespace

from conduit.learning_state import LearningState
from conduit.models import Manifest
from conduit.recorder import Recorder


def manifest(notes="first wording"):
    return Manifest(id="checkpointed", name="Checkpointed", kind="website", target="https://app.example.com",
                    allowed_origins=["https://app.example.com"], notes=notes)


def collector():
    return SimpleNamespace(sources=[{"url": "https://app.example.com", "kind": "page", "hash": "h", "id": "s1"}],
                           keys=["https://app.example.com"], documents={"https://app.example.com": "<html>"}, seen=set())


def write(recorder, phase, method="POST", url="https://app.example.com/gateway/svc/create"):
    recorder.phase = phase
    recorder._append({"kind": "http", "url": url, "method": method, "status": 200,
                      "initiator": "https://app.example.com/list", "request": {"a": 1}, "response": {"id": 7}})
    return recorder.events[-1]


def test_observed_writes_survive_a_fresh_recorder(tmp_path):
    state = LearningState(tmp_path, manifest())
    recorder = Recorder()
    created = write(recorder, "probe_record")
    write(recorder, "discovery", method="GET")
    write(recorder, "recover_record")
    state.save(collector(), [{"note": 1}], recorder)

    fresh = Recorder()
    fresh._append({"kind": "http", "url": "https://app.example.com/x", "method": "GET", "status": 200})
    feedback = LearningState(tmp_path, manifest()).restore(collector(), fresh)
    assert feedback == [{"note": 1}]
    ids = [e["id"] for e in fresh.events]
    assert created["id"] in ids
    assert fresh.events[0]["phase"] == "probe_record" and fresh.events[0]["url"].endswith("/create")
    # Only pinned writes travel; the original recorder's discovery GET to the same path does not.
    assert not any(e["phase"] == "discovery" and e["url"].endswith("/create") for e in fresh.events)
    assert fresh.sequence >= int(created["id"][1:])
    later = write(fresh, "explore", method="GET")
    assert int(later["id"][1:]) > int(created["id"][1:])


def test_refined_notes_and_tasks_do_not_discard_the_checkpoint(tmp_path):
    state = LearningState(tmp_path, manifest("first wording"))
    state.save(collector(), [{"kept": True}], Recorder())
    restored = LearningState(tmp_path, manifest("the user rewrote the request")).restore(collector())
    assert restored == [{"kept": True}]


def test_a_different_target_still_starts_clean(tmp_path):
    LearningState(tmp_path, manifest()).save(collector(), [{"kept": True}], Recorder())
    other = manifest()
    other.target = "https://other.example.com"
    assert LearningState(tmp_path, other).restore(collector()) == []


def test_restore_without_a_recorder_still_works(tmp_path):
    state = LearningState(tmp_path, manifest())
    recorder = Recorder()
    write(recorder, "probe_record")
    state.save(collector(), [], recorder)
    assert LearningState(tmp_path, manifest()).restore(collector()) == []
