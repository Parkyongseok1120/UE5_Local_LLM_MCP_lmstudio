from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from autofix_runtime import AutofixResult, autofix_ubt_allowed  # noqa: E402
from unreal_static_validate import Finding, can_run_autofix_ubt  # noqa: E402


def test_can_run_autofix_ubt_blocks_drift_after_autofix() -> None:
    findings = [
        Finding("warning", "A.h", 1, "CPP_RETURN_TYPE_MISMATCH", "drift"),
    ]
    assert can_run_autofix_ubt(findings, autofix_written=True) is False
    assert can_run_autofix_ubt(findings, autofix_written=False) is True


def test_autofix_ubt_allowed_requires_clean_post_autofix_state() -> None:
    findings = [
        Finding("error", "A.h", 1, "BLUEPRINT_NATIVE_EVENT_IMPL_MISSING", "missing"),
    ]
    result = AutofixResult(written=[Path("A.cpp")], findings=findings)
    assert autofix_ubt_allowed(result) is False
