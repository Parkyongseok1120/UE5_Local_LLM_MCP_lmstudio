from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from plan_graph import apply_plan_delta, merge_evidence_branch  # noqa: E402
from plan_slice_state import init_slice_state, load_slice_state  # noqa: E402


def test_plan_delta_invalidates_node() -> None:
    state = init_slice_state([{"slice_id": "a"}, {"slice_id": "b"}])
    updated = apply_plan_delta(
        state,
        {"reason": "scope changed", "invalidate": ["a"], "add": ["c"], "writeGate": "plan_only"},
    )
    statuses = {node["sliceId"]: node["status"] for node in updated["slices"]}
    assert statuses["a"] == "invalidated"
    assert updated["planRevision"] == 2


def test_corrupt_state_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    loaded = load_slice_state(path)
    assert loaded.get("corrupt") is True
    assert loaded.get("failed") is True
    assert loaded.get("completed") is False


def test_unresolved_evidence_branch_blocks_writes() -> None:
    merged = merge_evidence_branch(
        [{"claim": "Client can call ServerEquip", "missingEvidence": ["owning connection"]}]
    )
    assert merged["writesAllowed"] is False
