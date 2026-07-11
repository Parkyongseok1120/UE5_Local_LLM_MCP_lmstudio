from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from plan_slice_state import (  # noqa: E402
    capture_pre_change_hashes,
    init_slice_state,
    load_slice_state,
    mark_slice_complete,
    plan_fingerprint,
    slice_completion_accepted,
    validate_loaded_state,
)


def _plan() -> list[dict]:
    return [{"slice_id": "a", "slice_kind": "compile", "files": ["Source/Demo/Private/Demo.cpp"]}]


def test_built_stale_rejects_slice_and_sets_failed(tmp_path: Path) -> None:
    root = tmp_path / "Demo"
    cpp = root / "Source" / "Demo" / "Private" / "Demo.cpp"
    cpp.parent.mkdir(parents=True)
    cpp.write_text("void A() {}\n", encoding="utf-8")
    state = init_slice_state(_plan())
    state = capture_pre_change_hashes(state, project_root=root, plan_slices=_plan())
    state = mark_slice_complete(
        state,
        project_root=root,
        written_paths=[cpp],
        plan_slices=_plan(),
        proof_level="BuiltStale",
    )
    assert state["slices"][0]["status"] == "failed"
    assert state["failed"] is True
    assert not slice_completion_accepted(state, 0)


def test_no_op_edit_rejected_with_pre_change_hashes(tmp_path: Path) -> None:
    root = tmp_path / "Demo"
    cpp = root / "Source" / "Demo" / "Private" / "Demo.cpp"
    cpp.parent.mkdir(parents=True)
    cpp.write_text("void A() {}\n", encoding="utf-8")
    state = init_slice_state(_plan())
    state = capture_pre_change_hashes(state, project_root=root, plan_slices=_plan())
    state = mark_slice_complete(
        state,
        project_root=root,
        written_paths=[cpp],
        plan_slices=_plan(),
        proof_level="Built",
    )
    assert state["slices"][0]["status"] == "failed"
    assert "byte-identical" in str(state.get("lastError") or "")


def test_corrupt_slice_state_is_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "plan_slice_state.json"
    path.write_text("{not json", encoding="utf-8")
    state = load_slice_state(path)
    assert state.get("corrupt") is True
    assert state.get("failed") is True


def test_fingerprint_mismatch_resets_state() -> None:
    plan = _plan()
    state = init_slice_state(plan)
    state["planFingerprint"] = "deadbeefdeadbeef"
    validated = validate_loaded_state(state, plan, expected_fingerprint=plan_fingerprint(plan))
    assert validated.get("failed") is True
    assert "fingerprint mismatch" in str(validated.get("lastError") or "")


def test_architecture_slice_requires_evidence(tmp_path: Path) -> None:
    root = tmp_path / "Demo"
    plan = [{"slice_id": "arch", "slice_kind": "architecture", "files": []}]
    state = init_slice_state(plan)
    state = mark_slice_complete(
        state,
        project_root=root,
        written_paths=[],
        plan_slices=plan,
        proof_level="Built",
        required_evidence_satisfied=False,
    )
    assert state["slices"][0]["status"] == "failed"
    assert not slice_completion_accepted(state, 0)
