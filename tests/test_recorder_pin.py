"""Writes the application made while a form was driven survive the recorder's rolling window."""

from conduit.recorder import Recorder


def event(method, phase, i):
    return {"kind": "http", "url": f"https://app.example.com/rpc/{i}", "method": method, "status": 200}


def test_probe_phase_writes_outlive_the_window():
    recorder = Recorder()
    recorder.phase = "probe_record"
    recorder._append(event("POST", "probe_record", 0))
    created = recorder.events[-1]["id"]
    recorder.phase = "recover_record"
    for i in range(1, 400):
        recorder._append(event("POST" if i % 3 else "GET", "recover_record", i))
    ids = [e["id"] for e in recorder.events]
    assert created in ids
    assert len(recorder.events) <= 200
    assert ids == sorted(ids, key=lambda x: int(x[1:]))


def test_reads_and_discovery_traffic_are_not_pinned():
    recorder = Recorder()
    recorder.phase = "discovery"
    recorder._append(event("POST", "discovery", 0))
    first = recorder.events[-1]["id"]
    recorder.phase = "probe_record"
    recorder._append(event("GET", "probe_record", 1))
    read = recorder.events[-1]["id"]
    recorder.phase = "explore"
    for i in range(2, 400):
        recorder._append(event("GET", "explore", i))
    ids = {e["id"] for e in recorder.events}
    assert first not in ids and read not in ids
    assert len(recorder.events) == 160


def test_pinned_set_is_bounded():
    recorder = Recorder()
    recorder.phase = "verify:items:0:create"
    for i in range(500):
        recorder._append(event("POST", recorder.phase, i))
    assert len(recorder.events) == 200
