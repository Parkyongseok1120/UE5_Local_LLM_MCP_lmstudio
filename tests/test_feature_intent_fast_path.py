from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from feature_intent_fast_path import evaluate_bounded_local_fast_path


def test_fast_path_accepts_existing_two_file_local_slice() -> None:
    targets = ["Source/Demo/Thing.h", "Source/Demo/Thing.cpp"]
    decision = evaluate_bounded_local_fast_path(
        "Add a null guard in Source/Demo/Thing.cpp and preserve existing behavior.",
        target_files=targets,
        target_snapshots=[
            {"path": path, "exists": True, "fileHash": f"hash-{index}"}
            for index, path in enumerate(targets)
        ],
    )

    assert decision["eligible"] is True
    assert decision["selectedIntentId"] == "bounded_local"
    assert decision["serverOwnedPhases"] == [
        "SelectIntent",
        "ResolveSlice",
        "CaptureSnapshot",
        "BindIntent",
    ]


def test_fast_path_rejects_new_or_cross_authority_work() -> None:
    decision = evaluate_bounded_local_fast_path(
        "Create a replicated server subsystem for clients.",
        target_files=["Source/Demo/NewService.cpp"],
        target_snapshots=[
            {"path": "Source/Demo/NewService.cpp", "exists": False, "fileHash": ""}
        ],
    )

    assert decision["eligible"] is False
    assert decision["selectedIntentId"] == ""
    assert any("cannot create" in reason for reason in decision["reasons"])
    assert any("authority" in reason for reason in decision["reasons"])
