import json

import pytest

from conduit.single_operation import serve


@pytest.mark.asyncio
async def test_unverified_operation_cannot_start(tmp_path):
    (tmp_path / "operation.json").write_text(json.dumps({"verification": {"status": "pending"}}))
    with pytest.raises(ValueError, match="has not passed"):
        await serve(tmp_path, tmp_path)


@pytest.mark.asyncio
async def test_changed_program_cannot_start_even_in_test_mode(tmp_path):
    (tmp_path / "operation.json").write_text(json.dumps({
        "family": {"changed": True}, "fingerprint": "old", "verification": {"status": "passed"}}))
    with pytest.raises(ValueError, match="changed after"):
        await serve(tmp_path, tmp_path, testing=True)
