import pytest

from conduit.evidence import EvidenceCollector
from conduit.learning_state import LearningState
from conduit.models import Manifest
from conduit.runtime import Runtime


@pytest.mark.integration
def test_learning_checkpoint_preserves_feedback_and_refreshes_live_page(tmp_path):
    manifest = Manifest(id="checkpoint", name="Checkpoint", target="https://example.com", kind="website")
    collector = EvidenceCollector(Runtime(manifest, tmp_path).browser, manifest)
    collector.add(manifest.target, "page", "Old page")
    collector.add(manifest.target + "/docs", "documentation", "Documented operation")
    checkpoint = LearningState(tmp_path, manifest)
    checkpoint.save(collector, [{"exploration": {"document": {"elements": []}}}])
    other = EvidenceCollector(Runtime(manifest, tmp_path).browser, manifest)
    assert checkpoint.restore(other)[0]["exploration"]["document"] == {"elements": []}
    other.add(manifest.target, "page", "Current page")
    assert len(other.sources) == 2
    assert other.sources[0]["id"] == "s1" and other.sources[0]["content"] == "Current page"
    assert manifest.target + "/docs" in other.seen
    # Refining the request keeps what was learned about the site; only another target starts clean.
    manifest.notes = "A different requested operation"
    assert LearningState(tmp_path, manifest).restore(other)[0]["exploration"]["document"] == {"elements": []}
    manifest.target = "https://elsewhere.example.com"
    assert LearningState(tmp_path, manifest).restore(other) == []
    checkpoint.clear()
    assert not checkpoint.path.exists()
