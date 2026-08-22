from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_proof import parse_build_proof, proof_level_from_build_output  # noqa: E402


def test_executor_setup_with_compile_yields_built() -> None:
    output = (
        "Executing up to 16 processes, one per physical core\n"
        "Building 4 actions with 4 processes\n"
        "[1/4] Compile Demo.cpp"
    )
    proof = parse_build_proof(True, output)
    assert proof["proofLevel"] == "Built"
    assert proof["compileLineCount"] == 1
    assert proof["executorOnly"] is False


def test_executor_setup_lines_do_not_yield_built() -> None:
    output = "Executing up to 16 processes, one per physical core\nBuilding 4 actions with 4 processes"
    proof = parse_build_proof(True, output)
    assert proof["proofLevel"] != "Built"
    assert proof["proofLevel"] == "BuiltUnverified"
    assert proof["executorOnly"] is True


def test_large_action_denominator_does_not_inflate_compile_count() -> None:
    output = "[1/100] Compile Demo.cpp"
    proof = parse_build_proof(True, output)
    assert proof["compileLineCount"] == 1
    assert proof["declaredTotalActions"] == 100
    assert proof["actionCount"] == 100
    assert proof["proofLevel"] == "Built"


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
