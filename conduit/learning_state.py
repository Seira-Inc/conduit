"""Account-local, encrypted learning checkpoints; never part of an exported adapter."""
from __future__ import annotations

from .secrets import load_session, save_session


class LearningState:
    def __init__(self, home, manifest, resource=None):
        self.path = home / "learning" / (manifest.id + ".session")
        # Notes and requested tasks are refined between runs; what was learned about the site is not
        # invalidated by that, so only the target and the resource under repair scope a checkpoint.
        self.scope = {"target": manifest.target, "resource": resource}

    def restore(self, collector, recorder=None):
        value = load_session(self.path)
        if not value or value.get("scope") != self.scope or value.get("version") != 2:
            return []
        collector.sources = value["sources"]
        collector.keys = value["keys"]
        collector.documents = value["documents"]
        # Refresh pages and delivered scripts. Cached public documents remain available for focused reads.
        collector.seen = {key for key, source in zip(collector.keys, collector.sources, strict=True)
                          if source["kind"] in ("documentation", "source_map")}
        if recorder is not None and value.get("observed_writes"):
            # The writes the application was seen making are the evidence a proposal replays and the
            # grounding rule checks; a fresh browser must not forget them.
            recorder.events = value["observed_writes"] + recorder.events
            recorder.sequence = max(recorder.sequence, max(int(e["id"][1:]) for e in value["observed_writes"]))
        return value["feedback"]

    def save(self, collector, feedback, recorder=None):
        observed = [e for e in recorder.events if recorder.pinned(e)][-40:] if recorder is not None else []
        save_session(self.path, {"version": 2, "scope": self.scope, "sources": collector.sources,
                                "keys": collector.keys, "documents": collector.documents, "feedback": feedback[-8:],
                                "observed_writes": observed})

    def clear(self):
        self.path.unlink(missing_ok=True)
