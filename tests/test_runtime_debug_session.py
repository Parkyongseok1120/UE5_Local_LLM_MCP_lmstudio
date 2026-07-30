from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from runtime_debug_session import (  # noqa: E402
    prepare_runtime_session,
    record_runtime_patch,
    verify_runtime_session,
)


def _prepared() -> dict:
    result = prepare_runtime_session(
        {
            "symptom": "Stamina does not regenerate after PIE resume",
            "reproductionSteps": ["Start PIE", "Drain stamina", "Wait five seconds"],
            "observer": {
                "id": "stamina-log",
                "signal": "LogStamina current value",
                "expected": "value increases",
            },
            "baselineEvidence": {
                "kind": "log",
                "location": "Saved/Logs/Demo.log:120",
                "observation": "value remains at zero",
            },
            "hypotheses": [
                {
                    "id": "h1",
                    "claim": "regen timer is cleared on resume",
                    "falsification": "trace timer registration and callback after resume",
                }
            ],
        }
    )
    assert result["ok"] is True
    return result["session"]


def test_runtime_session_closes_same_observer_loop() -> None:
    session = _prepared()
    patched = record_runtime_patch(
        session,
        changed_files=["Source/Demo/Private/StaminaComponent.cpp"],
        patch_summary="Restore timer registration on resume",
        build_proof={"ok": True, "proofLevel": "Built"},
    )
    assert patched["ok"] is True

    verified = verify_runtime_session(
        patched["session"],
        reproduction_fingerprint=session["reproductionFingerprint"],
        observer=session["observer"],
        after_evidence={
            "kind": "log",
            "location": "Saved/Logs/Demo.log:180",
            "observation": "value increases after resume",
        },
        outcome="resolved",
    )

    assert verified["ok"] is True
    assert verified["session"]["status"] == "runtime_verified"
    assert verified["session"]["proofLevel"] == "RuntimeVerified"


def test_runtime_session_rejects_different_observer_or_reproduction() -> None:
    session = _prepared()
    patched = record_runtime_patch(
        session,
        changed_files=["Source/Demo/Private/StaminaComponent.cpp"],
        patch_summary="Patch",
    )["session"]

    verified = verify_runtime_session(
        patched,
        reproduction_fingerprint="different",
        observer={"id": "screen", "signal": "UI text"},
        after_evidence={
            "kind": "log",
            "location": "Saved/Logs/Demo.log:200",
            "observation": "looks fixed",
        },
        outcome="resolved",
    )

    assert verified["ok"] is False
    assert verified["session"]["status"] == "verification_rejected"
    assert verified["session"]["proofLevel"] == "NeedsRuntimeProof"


def test_runtime_session_requires_falsifiable_baseline() -> None:
    result = prepare_runtime_session(
        {
            "symptom": "broken",
            "reproductionSteps": ["PIE"],
            "observer": {"id": "log", "signal": "value"},
            "baselineEvidence": {"kind": "guess"},
            "hypotheses": [{"claim": "maybe timer"}],
        }
    )
    assert result["ok"] is False
    assert result["session"]["writeGate"]["writesAllowed"] is False
