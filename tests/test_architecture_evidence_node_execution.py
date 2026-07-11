from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from plan_slice_state import init_slice_state, mark_slice_complete, slice_completion_accepted  # noqa: E402


def test_architecture_slice_accepts_valid_evidence(tmp_path: Path) -> None:
    root = tmp_path / "Demo"
    plan = [{"slice_id": "arch", "slice_kind": "architecture", "files": []}]
    state = init_slice_state(plan)
    state = mark_slice_complete(
        state,
        project_root=root,
        written_paths=[],
        plan_slices=plan,
        proof_level="",
        required_evidence_satisfied=True,
    )
    assert state["slices"][0]["status"] == "complete"
    assert slice_completion_accepted(state, 0)
    assert state["completed"] is True


def test_analysis_slice_does_not_complete_on_build_proof_only(tmp_path: Path) -> None:
    root = tmp_path / "Demo"
    cpp = root / "Source" / "Demo" / "Private" / "Demo.cpp"
    cpp.parent.mkdir(parents=True)
    cpp.write_text("void A() {}\n", encoding="utf-8")
    plan = [{"slice_id": "analysis", "slice_kind": "analysis", "files": ["Source/Demo/Private/Demo.cpp"]}]
    state = init_slice_state(plan)
    state = mark_slice_complete(
        state,
        project_root=root,
        written_paths=[cpp],
        plan_slices=plan,
        proof_level="Built",
        required_evidence_satisfied=False,
    )
    assert state["slices"][0]["status"] == "failed"
    assert not slice_completion_accepted(state, 0)
