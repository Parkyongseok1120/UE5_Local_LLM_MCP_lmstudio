from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_proof import parse_build_proof, proof_level_from_build_output  # noqa: E402
from plan_slice_state import (  # noqa: E402
    init_slice_state,
    mark_slice_complete,
    migrate_slice_state_on_fingerprint_change,
    normalize_slice_state,
    plan_fingerprint,
    validate_loaded_state,
)


def test_executor_setup_lines_do_not_yield_built() -> None:
    output = "Executing up to 16 processes, one per physical core\nBuilding 4 actions with 4 processes"
    proof = parse_build_proof(True, output)
    assert proof["proofLevel"] != "Built"
    assert proof["proofLevel"] == "BuiltUnverified"


def test_zero_actions_yields_built_stale() -> None:
    output = "Target is up to date\n0 actions executed"
    proof = parse_build_proof(True, output)
    assert proof["proofLevel"] == "BuiltStale"
    assert proof["targetUpToDate"] is True


def test_compile_actions_yield_built() -> None:
    output = "[1/3] Compile Demo.cpp\n[2/3] Link DemoEditor-Win64-Development.exe"
    proof = parse_build_proof(True, output)
    assert proof["proofLevel"] == "Built"
    assert proof["compileActionCount"] == 3
    assert proof["linkActionCount"] == 3


def test_failed_build() -> None:
    assert proof_level_from_build_output(False, "error C2065") == "Failed"


def test_fingerprint_mismatch_migrates_without_failed() -> None:
    plan = [{"slice_id": "a", "slice_kind": "compile", "files": ["Source/Demo.cpp"]}]
    state = init_slice_state(plan)
    state["planFingerprint"] = "deadbeefdeadbeef"
    validated = validate_loaded_state(state, plan, expected_fingerprint=plan_fingerprint(plan))
    assert validated.get("failed") is False
    assert validated.get("planMigrationReason")
    assert validated.get("previousPlanFingerprint") == "deadbeefdeadbeef"


def test_normalize_completed_clears_failed_and_active_node() -> None:
    state = init_slice_state([{"slice_id": "a"}])
    state["completed"] = True
    state["failed"] = True
    state["activeNodeId"] = "a"
    normalized = normalize_slice_state(state)
    assert normalized["failed"] is False
    assert normalized["activeNodeId"] == ""


def test_successful_slice_clears_last_error(tmp_path: Path) -> None:
    target = tmp_path / "Thing.cpp"
    target.write_text("void X() {}\n", encoding="utf-8")
    plan = [{"slice_id": "s1", "slice_kind": "compile", "files": ["Thing.cpp"]}]
    state = init_slice_state(plan)
    state["lastError"] = "previous failure"
    state["retryWithinSlice"] = 2
    state = mark_slice_complete(
        state,
        project_root=tmp_path,
        written_paths=[target],
        plan_slices=plan,
        proof_level="Built",
    )
    assert state["lastError"] == ""
    assert state["retryWithinSlice"] == 0
    assert state["failed"] is False
