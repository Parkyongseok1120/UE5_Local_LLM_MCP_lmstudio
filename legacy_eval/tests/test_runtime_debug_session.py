# Archived workflow runtime-debug controller tests.
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from runtime_debug_session import (  # noqa: E402
    prepare_runtime_session,
    record_patch_candidate_comparison,
    record_runtime_experiment,
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


def _supported(session: dict) -> dict:
    result = record_runtime_experiment(
        session,
        hypothesis_id=session["selectedHypothesisId"],
        reproduction_fingerprint=session["reproductionFingerprint"],
        observer=session["observer"],
        experiment_evidence={
            "kind": "trace",
            "location": "Saved/Profiling/regen.utrace",
            "observation": "timer callback is absent after resume",
            "traceSummary": {"timerCallbacks": 0},
        },
        outcome="supported",
    )
    assert result["ok"] is True
    return result["session"]


def _ready_for_patch(session: dict, changed_files: list[str]) -> dict:
    supported = _supported(session)
    result = record_patch_candidate_comparison(
        supported,
        patch_candidates=[
            {
                "id": "candidate-a",
                "changedFiles": changed_files,
                "diffHash": "diff-a",
                "sandboxEvidence": {
                    "isolatedRoot": "sandbox/a",
                    "staticPassed": True,
                    "staticProof": {"ok": True, "artifactHash": "static-a"},
                    "buildPassed": True,
                    "buildProof": {"ok": True, "artifactHash": "build-a"},
                    "runtimeCompatible": True,
                    "invariantResults": {"same observer": True},
                },
            },
            {
                "id": "candidate-b",
                "changedFiles": changed_files,
                "diffHash": "diff-b",
                "sandboxEvidence": {
                    "isolatedRoot": "sandbox/b",
                    "staticPassed": True,
                    "staticProof": {"ok": True, "artifactHash": "static-b"},
                    "buildPassed": True,
                    "buildProof": {"ok": True, "artifactHash": "build-b"},
                    "runtimeCompatible": True,
                    "invariantResults": {"same observer": True},
                },
            },
        ],
        selected_patch_candidate_id="candidate-a",
        patch_selection_rationale=(
            "candidate-a keeps the supported fix inside the existing owner"
        ),
    )
    assert result["ok"] is True
    return result["session"]


def test_runtime_session_closes_same_observer_loop() -> None:
    files = ["Source/Demo/Private/StaminaComponent.cpp"]
    session = _ready_for_patch(_prepared(), files)
    patched = record_runtime_patch(
        session,
        changed_files=files,
        patch_summary="Restore timer registration on resume",
        selected_patch_candidate_id="candidate-a",
        applied_diff_hash="diff-a",
        build_proof={"ok": True, "proofLevel": "Built", "artifactHash": "build-live-a"},
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
    files = ["Source/Demo/Private/StaminaComponent.cpp"]
    session = _ready_for_patch(_prepared(), files)
    patched = record_runtime_patch(
        session,
        changed_files=files,
        patch_summary="Patch",
        selected_patch_candidate_id="candidate-a",
        applied_diff_hash="diff-a",
        build_proof={"ok": True, "artifactHash": "build-live-b"},
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
    assert verified["session"]["status"] == "awaiting_same_observer_verification"
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


def test_runtime_patch_must_match_selected_diff_and_current_build_proof() -> None:
    files = ["Source/Demo/Private/StaminaComponent.cpp"]
    session = _ready_for_patch(_prepared(), files)
    wrong_diff = record_runtime_patch(
        session,
        changed_files=files,
        patch_summary="Patch",
        selected_patch_candidate_id="candidate-a",
        applied_diff_hash="different",
        build_proof={"ok": True, "artifactHash": "build"},
    )
    assert wrong_diff["ok"] is False
    missing_build = record_runtime_patch(
        session,
        changed_files=files,
        patch_summary="Patch",
        selected_patch_candidate_id="candidate-a",
        applied_diff_hash="diff-a",
        build_proof={"ok": True},
    )
    assert missing_build["ok"] is False


def test_runtime_hypotheses_are_ranked_and_falsification_selects_next() -> None:
    prepared = prepare_runtime_session(
        {
            "symptom": "server reconnect stalls",
            "reproductionSteps": ["disconnect", "reconnect"],
            "observer": {"id": "reconnect", "signal": "latency"},
            "baselineEvidence": {
                "kind": "trace",
                "location": "Saved/Profiling/reconnect.utrace",
                "observation": "stall",
            },
            "hypotheses": [
                {
                    "id": "expensive",
                    "claim": "network stack is corrupt",
                    "falsification": "capture socket events",
                    "priorConfidence": 0.3,
                    "estimatedCost": 5,
                    "blastRadius": 5,
                    "informationGain": 2,
                },
                {
                    "id": "cheap",
                    "claim": "reconnect delegate is registered twice",
                    "falsification": "count delegate registrations",
                    "priorConfidence": 0.8,
                    "estimatedCost": 1,
                    "blastRadius": 1,
                    "informationGain": 5,
                },
            ],
        }
    )["session"]
    assert prepared["selectedHypothesisId"] == "cheap"
    assert prepared["hypotheses"][0]["rank"] == 1
    assert prepared["status"] == "ready_for_experiment"
    assert prepared["writeGate"]["writesAllowed"] is False

    falsified = record_runtime_experiment(
        prepared,
        hypothesis_id="cheap",
        reproduction_fingerprint=prepared["reproductionFingerprint"],
        observer=prepared["observer"],
        experiment_evidence={
            "kind": "automation",
            "location": "Saved/Logs/Reconnect.log",
            "observation": "delegate count is one",
        },
        outcome="falsified",
    )
    assert falsified["ok"] is True
    assert falsified["session"]["selectedHypothesisId"] == "expensive"
    assert falsified["session"]["writeGate"]["writesAllowed"] is False


def test_runtime_metric_and_soak_policy_rejects_unproven_resolved_claim() -> None:
    prepared = prepare_runtime_session(
        {
            "symptom": "frame hitch after map travel",
            "reproductionSteps": ["travel 20 times"],
            "observer": {
                "id": "frame-time",
                "signal": "max frame time",
                "comparison": "decrease",
                "tolerance": 1,
            },
            "baselineEvidence": {
                "kind": "trace",
                "location": "Saved/Profiling/before.utrace",
                "observation": "max frame time 80 ms",
                "signalValue": 80,
                "sampleCount": 20,
                "durationSec": 600,
                "traceSummary": {"maxFrameMs": 80},
            },
            "runtimePolicy": {
                "requireTrace": True,
                "minSamples": 20,
                "minDurationSec": 600,
                "maxErrors": 0,
                "maxCrashes": 0,
                "maxTimeouts": 0,
            },
            "hypotheses": [
                {
                    "claim": "streaming flush blocks the game thread",
                    "falsification": "compare trace scopes around map travel",
                }
            ],
        }
    )["session"]
    patched = record_runtime_patch(
        _ready_for_patch(prepared, ["Source/Demo/Private/Travel.cpp"]),
        changed_files=["Source/Demo/Private/Travel.cpp"],
        patch_summary="defer streaming flush",
        selected_patch_candidate_id="candidate-a",
        applied_diff_hash="diff-a",
        build_proof={"ok": True, "artifactHash": "build-live-travel"},
    )["session"]
    result = verify_runtime_session(
        patched,
        reproduction_fingerprint=prepared["reproductionFingerprint"],
        observer=prepared["observer"],
        after_evidence={
            "kind": "log",
            "location": "Saved/Logs/Demo.log",
            "observation": "looks smoother",
            "signalValue": 60,
            "sampleCount": 2,
            "durationSec": 30,
        },
        outcome="resolved",
    )
    assert result["ok"] is False
    assert "resolved outcome is not supported" in " ".join(result["issues"])


def test_runtime_oracle_compares_named_insights_metric() -> None:
    prepared = prepare_runtime_session(
        {
            "symptom": "game thread stalls",
            "reproductionSteps": ["travel map"],
            "observer": {
                "id": "insights-game-thread",
                "signal": "GameThread max duration",
                "comparison": "decrease",
                "traceMetric": "gameThreadMaxMs",
                "tolerance": 1,
            },
            "baselineEvidence": {
                "kind": "trace",
                "location": "before.utrace",
                "observation": "80 ms maximum",
                "traceSummary": {"gameThreadMaxMs": 80},
            },
            "hypotheses": [
                {
                    "claim": "synchronous streaming blocks travel",
                    "falsification": "compare streaming scopes",
                }
            ],
        }
    )
    assert prepared["ok"] is True
    session = _ready_for_patch(
        prepared["session"],
        ["Source/Demo/Private/Travel.cpp"],
    )
    patched = record_runtime_patch(
        session,
        changed_files=["Source/Demo/Private/Travel.cpp"],
        patch_summary="defer streaming",
        selected_patch_candidate_id="candidate-a",
        applied_diff_hash="diff-a",
        build_proof={"ok": True, "artifactHash": "build-trace"},
    )["session"]
    verified = verify_runtime_session(
        patched,
        reproduction_fingerprint=prepared["session"]["reproductionFingerprint"],
        observer=prepared["session"]["observer"],
        after_evidence={
            "kind": "trace",
            "location": "after.utrace",
            "observation": "45 ms maximum",
            "traceSummary": {"gameThreadMaxMs": 45},
        },
        outcome="resolved",
    )
    assert verified["ok"] is True
    assert verified["session"]["verification"]["oracle"]["delta"] == -35
