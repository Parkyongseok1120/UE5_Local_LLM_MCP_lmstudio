from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import eval_pass_at_k  # noqa: E402


def test_calculate_kpi_metrics_extended_fields():
    metrics = eval_pass_at_k.calculate_kpi_metrics(
        [
            {"id": "a", "pass": True, "passAt1": True, "attempts": 1},
            {"id": "b", "pass": True, "passAt1": False, "attempts": 3, "sameErrorRepeated": True},
            {"id": "c", "pass": False, "passAt1": False, "attempts": 2, "noOpEdit": True},
        ]
    )

    assert metrics["passAt1Count"] == 1
    assert metrics["passAt1Rate"] == 0.333
    assert metrics["averageAttempts"] == 2.0
    assert metrics["failedCaseIds"] == ["c"]
    assert metrics["attemptHistogram"] == {"1": 1, "2": 1, "3": 1}
    assert metrics["sameErrorRepeatedCount"] == 1
    assert metrics["noOpEditCount"] == 1
    assert metrics["repeatedErrorCaseIds"] == ["b"]
    assert metrics["noOpCaseIds"] == ["c"]
