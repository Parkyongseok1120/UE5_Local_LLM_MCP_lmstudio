from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from unreal_static_validate import (  # noqa: E402
    Finding,
    has_actionable_static_findings,
    should_block_llm_apply_static_gate,
)


def test_multifile_drift_warning_blocks_static_gate() -> None:
    findings = [
        Finding("warning", "A.h", 1, "CPP_RETURN_TYPE_MISMATCH", "drift"),
    ]
    assert has_actionable_static_findings(findings, mode="multifile_refactor") is True
    assert should_block_llm_apply_static_gate(findings, mode="multifile_refactor") is True
    assert has_actionable_static_findings(findings, mode="compile_fix") is False


def test_compile_fix_ignores_drift_warning_for_gate() -> None:
    findings = [
        Finding("warning", "A.h", 1, "CPP_RETURN_TYPE_MISMATCH", "drift"),
    ]
    assert should_block_llm_apply_static_gate(findings, mode="compile_fix") is False
