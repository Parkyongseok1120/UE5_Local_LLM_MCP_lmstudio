from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from retry_state import make_validation_rejection_record, recommend_retry_action  # noqa: E402
from retry_feedback import retry_feedback_block  # noqa: E402


def test_retry_feedback_block_includes_escalation_hint() -> None:
    previous = make_validation_rejection_record(
        attempt=0,
        rejection_kind="empty_files_without_evidence",
        feedback="empty bundle",
        error_subkind="PRE_APPLY_VALIDATION",
    )
    current = make_validation_rejection_record(
        attempt=1,
        rejection_kind="empty_files_without_evidence",
        feedback="empty bundle again",
        error_subkind="PRE_APPLY_VALIDATION",
    )
    recommendation = recommend_retry_action(
        previous,
        current,
        attempts=[previous, current],
        no_op_guard=True,
        rejection_kind="empty_files_without_evidence",
    )
    block = retry_feedback_block(recommendation)
    assert block
    assert "Do not resubmit" in block or "force" in block.lower() or "evidence" in block.lower()
