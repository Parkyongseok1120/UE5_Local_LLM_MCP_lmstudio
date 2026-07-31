#!/usr/bin/env python
"""Deterministic causal-session contract for Unreal runtime debugging."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from patch_candidate_comparison import compare_patch_candidates
from runtime_oracle import (
    RUNTIME_EVIDENCE_KINDS,
    evaluate_runtime_oracle,
    normalize_runtime_evidence,
    normalize_runtime_policy,
    rank_runtime_hypotheses,
)


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _fingerprint(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _clean_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


def _normalize_observer(value: Any) -> dict[str, Any]:
    row = value if isinstance(value, dict) else {}
    return {
        "id": str(row.get("id") or "").strip(),
        "signal": str(row.get("signal") or row.get("metric") or "").strip(),
        "expected": str(row.get("expected") or "").strip(),
        "comparison": str(row.get("comparison") or "").strip().lower(),
        "traceMetric": str(row.get("traceMetric") or "").strip(),
        "targetValue": row.get("targetValue"),
        "tolerance": row.get("tolerance", 0),
    }


def _normalize_evidence(value: Any) -> dict[str, Any]:
    return normalize_runtime_evidence(value)


def prepare_runtime_session(payload: dict[str, Any]) -> dict[str, Any]:
    symptom = str(payload.get("symptom") or "").strip()
    reproduction_steps = _clean_strings(payload.get("reproductionSteps"))
    observer = _normalize_observer(payload.get("observer"))
    baseline = _normalize_evidence(payload.get("baselineEvidence"))
    hypotheses = rank_runtime_hypotheses(payload.get("hypotheses"))

    issues: list[str] = []
    if not symptom:
        issues.append("symptom is required")
    if not reproduction_steps:
        issues.append("at least one reproductionSteps entry is required")
    if not observer["id"] or not observer["signal"]:
        issues.append("observer.id and observer.signal are required")
    if baseline["kind"] not in RUNTIME_EVIDENCE_KINDS:
        issues.append("baselineEvidence.kind must be runtime, log, trace, debugger, or automation")
    if not baseline["location"] or not baseline["observation"]:
        issues.append("baselineEvidence.location and observation are required")
    if not hypotheses:
        issues.append("at least one causal hypothesis is required")
    if any(not item["falsification"] for item in hypotheses):
        issues.append("every hypothesis requires a falsification plan")
    requested_hypothesis = str(payload.get("selectedHypothesisId") or "").strip()
    hypothesis_ids = {str(item.get("id") or "") for item in hypotheses}
    if requested_hypothesis and requested_hypothesis not in hypothesis_ids:
        issues.append("selectedHypothesisId must reference a supplied hypothesis")
    selected_hypothesis = requested_hypothesis or (
        str(hypotheses[0].get("id") or "") if hypotheses else ""
    )

    reproduction_contract = {
        "steps": reproduction_steps,
        "observer": observer,
        "environment": str(payload.get("environment") or "").strip(),
    }
    session = {
        "version": 2,
        "sessionId": str(payload.get("sessionId") or f"runtime-{uuid.uuid4().hex[:12]}"),
        "status": "ready_for_experiment" if not issues else "blocked",
        "symptom": symptom,
        "reproduction": reproduction_contract,
        "reproductionFingerprint": _fingerprint(reproduction_contract),
        "observer": observer,
        "baselineEvidence": baseline,
        "baselineEvidenceHash": _fingerprint(baseline),
        "hypotheses": hypotheses,
        "selectedHypothesisId": selected_hypothesis,
        "experiments": [],
        "patchCandidateComparison": {},
        "runtimePolicy": normalize_runtime_policy(payload.get("runtimePolicy")),
        "patchEvidence": {},
        "verification": {},
        "issues": issues,
        "proofLevel": "RuntimeObserved" if not issues else "Proposed",
        "createdAt": _utc_now(),
        "updatedAt": _utc_now(),
    }
    session["writeGate"] = {
        "writesAllowed": False,
        "reason": (
            "selected hypothesis requires a supporting runtime experiment"
            if not issues
            else "runtime causal contract incomplete"
        ),
    }
    return {"ok": not issues, "session": session, "issues": issues}


def record_runtime_experiment(
    session: dict[str, Any],
    *,
    hypothesis_id: str,
    reproduction_fingerprint: str,
    observer: dict[str, Any],
    experiment_evidence: dict[str, Any],
    outcome: str,
) -> dict[str, Any]:
    if str(session.get("status") or "") != "ready_for_experiment":
        return {
            "ok": False,
            "error": "runtime session is not ready_for_experiment",
            "session": session,
        }
    selected_id = str(session.get("selectedHypothesisId") or "")
    supplied_id = str(hypothesis_id or "").strip()
    normalized_observer = _normalize_observer(observer)
    expected_observer = _normalize_observer(session.get("observer"))
    evidence = _normalize_evidence(experiment_evidence)
    normalized_outcome = str(outcome or "").strip().lower()
    issues: list[str] = []
    if supplied_id != selected_id:
        issues.append("experiment must target the selected hypothesis")
    if str(reproduction_fingerprint or "").strip() != str(
        session.get("reproductionFingerprint") or ""
    ):
        issues.append("experiment must use the same reproduction fingerprint")
    if normalized_observer["id"] != expected_observer["id"]:
        issues.append("experiment must use the same observer.id")
    if normalized_observer["signal"] != expected_observer["signal"]:
        issues.append("experiment must use the same observer.signal")
    if evidence["kind"] not in RUNTIME_EVIDENCE_KINDS:
        issues.append("experimentEvidence.kind must be runtime-verifiable")
    if not evidence["location"] or not evidence["observation"]:
        issues.append("experimentEvidence.location and observation are required")
    if normalized_outcome not in {"supported", "falsified", "inconclusive"}:
        issues.append("experimentOutcome must be supported, falsified, or inconclusive")

    updated = dict(session)
    hypotheses = [dict(item) for item in session.get("hypotheses") or []]
    experiments = [
        dict(item) for item in session.get("experiments") or [] if isinstance(item, dict)
    ]
    experiment = {
        "hypothesisId": supplied_id,
        "reproductionFingerprint": str(reproduction_fingerprint or "").strip(),
        "observer": normalized_observer,
        "evidence": evidence,
        "evidenceHash": _fingerprint(evidence),
        "outcome": normalized_outcome,
        "recordedAt": _utc_now(),
    }
    experiments.append(experiment)
    updated["experiments"] = experiments[-64:]
    if not issues:
        for item in hypotheses:
            if str(item.get("id") or "") == supplied_id:
                item["status"] = normalized_outcome
        updated["hypotheses"] = hypotheses
        if normalized_outcome == "supported":
            updated["status"] = "ready_for_patch_candidates"
            updated["writeGate"] = {
                "writesAllowed": False,
                "reason": "compare two to four isolated patch candidates",
            }
        else:
            open_hypotheses = [
                item for item in hypotheses if item.get("status") == "open"
            ]
            if normalized_outcome == "falsified" and open_hypotheses:
                open_hypotheses.sort(
                    key=lambda item: (
                        -float(item.get("priorityScore") or 0),
                        str(item.get("id") or ""),
                    )
                )
                updated["selectedHypothesisId"] = str(
                    open_hypotheses[0].get("id") or ""
                )
            updated["status"] = (
                "ready_for_experiment"
                if open_hypotheses or normalized_outcome == "inconclusive"
                else "needs_new_hypothesis"
            )
            updated["writeGate"] = {
                "writesAllowed": False,
                "reason": "no supported causal hypothesis",
            }
    else:
        updated["status"] = "ready_for_experiment"
        updated["lastRejectedExperiment"] = experiment
        updated["writeGate"] = {
            "writesAllowed": False,
            "reason": "experiment contract mismatch",
        }
    updated["issues"] = issues
    updated["updatedAt"] = _utc_now()
    return {"ok": not issues, "session": updated, "issues": issues}


def record_patch_candidate_comparison(
    session: dict[str, Any],
    *,
    patch_candidates: list[dict[str, Any]],
    selected_patch_candidate_id: str = "",
    patch_selection_rationale: str = "",
) -> dict[str, Any]:
    if str(session.get("status") or "") != "ready_for_patch_candidates":
        return {
            "ok": False,
            "error": "runtime session is not ready_for_patch_candidates",
            "session": session,
        }
    comparison = compare_patch_candidates(
        patch_candidates,
        selected_candidate_id=selected_patch_candidate_id,
        selection_rationale=patch_selection_rationale,
    )
    updated = dict(session)
    updated["patchCandidateComparison"] = comparison
    updated["issues"] = list(comparison["issues"])
    updated["status"] = (
        "ready_for_patch" if comparison["ok"] else "ready_for_patch_candidates"
    )
    updated["writeGate"] = {
        "writesAllowed": comparison["ok"],
        "reason": (
            "an isolated patch candidate was selected from verified alternatives"
            if comparison["ok"]
            else "patch candidate evidence is incomplete"
        ),
    }
    updated["updatedAt"] = _utc_now()
    return {"ok": comparison["ok"], "session": updated, "comparison": comparison}


def record_runtime_patch(
    session: dict[str, Any],
    *,
    changed_files: list[str],
    patch_summary: str,
    selected_patch_candidate_id: str = "",
    applied_diff_hash: str = "",
    build_proof: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if str(session.get("status") or "") != "ready_for_patch":
        return {"ok": False, "error": "runtime session is not ready_for_patch", "session": session}
    files = _clean_strings(changed_files)
    summary = str(patch_summary or "").strip()
    if not files or not summary:
        return {"ok": False, "error": "changedFiles and patchSummary are required", "session": session}
    comparison = dict(session.get("patchCandidateComparison") or {})
    expected_candidate = str(comparison.get("selectedCandidateId") or "")
    supplied_candidate = str(selected_patch_candidate_id or "").strip()
    if supplied_candidate != expected_candidate:
        return {
            "ok": False,
            "error": "selectedPatchCandidateId must match the verified candidate comparison",
            "session": session,
        }
    expected_files = set(
        str(item)
        for item in (comparison.get("selectedCandidate") or {}).get("changedFiles") or []
    )
    if set(files) != expected_files:
        return {
            "ok": False,
            "error": "changedFiles must match the selected patch candidate",
            "session": session,
        }
    expected_diff_hash = str(
        (comparison.get("selectedCandidate") or {}).get("diffHash") or ""
    )
    if str(applied_diff_hash or "").strip() != expected_diff_hash:
        return {
            "ok": False,
            "error": "appliedDiffHash must match the selected patch candidate",
            "session": session,
        }
    normalized_build_proof = dict(build_proof or {})
    if normalized_build_proof.get("ok") is not True or not (
        normalized_build_proof.get("artifactHash")
        or normalized_build_proof.get("logPath")
    ):
        return {
            "ok": False,
            "error": "buildProof requires ok=true and an artifactHash or logPath",
            "session": session,
        }
    updated = dict(session)
    updated["patchEvidence"] = {
        "changedFiles": files,
        "patchSummary": summary,
        "selectedPatchCandidateId": supplied_candidate,
        "appliedDiffHash": expected_diff_hash,
        "buildProof": normalized_build_proof,
        "recordedAt": _utc_now(),
    }
    updated["status"] = "awaiting_same_observer_verification"
    updated["proofLevel"] = "BuildVerified"
    updated["updatedAt"] = _utc_now()
    return {"ok": True, "session": updated}


def verify_runtime_session(
    session: dict[str, Any],
    *,
    reproduction_fingerprint: str,
    observer: dict[str, Any],
    after_evidence: dict[str, Any],
    outcome: str,
) -> dict[str, Any]:
    if str(session.get("status") or "") != "awaiting_same_observer_verification":
        return {
            "ok": False,
            "error": "record the patch before runtime verification",
            "session": session,
        }
    issues: list[str] = []
    expected_fingerprint = str(session.get("reproductionFingerprint") or "")
    supplied_fingerprint = str(reproduction_fingerprint or "").strip()
    normalized_observer = _normalize_observer(observer)
    expected_observer = _normalize_observer(session.get("observer"))
    evidence = _normalize_evidence(after_evidence)
    normalized_outcome = str(outcome or "").strip().lower()
    if supplied_fingerprint != expected_fingerprint:
        issues.append("verification must use the same reproduction fingerprint")
    if normalized_observer["id"] != expected_observer["id"]:
        issues.append("verification must use the same observer.id")
    if normalized_observer["signal"] != expected_observer["signal"]:
        issues.append("verification must use the same observer.signal")
    if evidence["kind"] not in RUNTIME_EVIDENCE_KINDS:
        issues.append("afterEvidence.kind must be runtime, log, trace, debugger, or automation")
    if not evidence["location"] or not evidence["observation"]:
        issues.append("afterEvidence.location and observation are required")
    if normalized_outcome not in {"resolved", "not_resolved", "regressed"}:
        issues.append("outcome must be resolved, not_resolved, or regressed")
    oracle = evaluate_runtime_oracle(
        observer=expected_observer,
        baseline_evidence=dict(session.get("baselineEvidence") or {}),
        after_evidence=evidence,
        runtime_policy=dict(session.get("runtimePolicy") or {}),
    )
    if normalized_outcome == "resolved" and not oracle["resolved"]:
        issues.append("resolved outcome is not supported by the runtime oracle")
    if normalized_outcome in {"not_resolved", "regressed"} and oracle["resolved"]:
        issues.append("negative outcome conflicts with the runtime oracle")

    updated = dict(session)
    updated["verification"] = {
        "reproductionFingerprint": supplied_fingerprint,
        "observer": normalized_observer,
        "afterEvidence": evidence,
        "afterEvidenceHash": _fingerprint(evidence),
        "outcome": normalized_outcome,
        "oracle": oracle,
        "sameObserver": not any("same observer" in issue for issue in issues),
        "sameReproduction": supplied_fingerprint == expected_fingerprint,
        "verifiedAt": _utc_now(),
    }
    if issues:
        updated["status"] = "awaiting_same_observer_verification"
        updated["lastRejectedVerification"] = dict(updated["verification"])
        updated["proofLevel"] = "NeedsRuntimeProof"
    elif normalized_outcome == "resolved":
        updated["status"] = "runtime_verified"
        updated["proofLevel"] = "RuntimeVerified"
    else:
        updated["status"] = "runtime_not_fixed"
        updated["proofLevel"] = "RuntimeObserved"
    updated["issues"] = issues
    updated["updatedAt"] = _utc_now()
    return {"ok": not issues, "session": updated, "issues": issues}
