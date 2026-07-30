#!/usr/bin/env python
"""Deterministic causal-session contract for Unreal runtime debugging."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

RUNTIME_EVIDENCE_KINDS = frozenset({"runtime", "log", "trace", "debugger", "automation"})


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _fingerprint(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _clean_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


def _normalize_observer(value: Any) -> dict[str, str]:
    row = value if isinstance(value, dict) else {}
    return {
        "id": str(row.get("id") or "").strip(),
        "signal": str(row.get("signal") or row.get("metric") or "").strip(),
        "expected": str(row.get("expected") or "").strip(),
    }


def _normalize_evidence(value: Any) -> dict[str, str]:
    row = value if isinstance(value, dict) else {}
    return {
        "kind": str(row.get("kind") or "").strip().lower(),
        "location": str(row.get("location") or "").strip(),
        "observation": str(row.get("observation") or "").strip(),
        "artifactHash": str(row.get("artifactHash") or row.get("artifact_hash") or "").strip(),
    }


def prepare_runtime_session(payload: dict[str, Any]) -> dict[str, Any]:
    symptom = str(payload.get("symptom") or "").strip()
    reproduction_steps = _clean_strings(payload.get("reproductionSteps"))
    observer = _normalize_observer(payload.get("observer"))
    baseline = _normalize_evidence(payload.get("baselineEvidence"))
    raw_hypotheses = payload.get("hypotheses") if isinstance(payload.get("hypotheses"), list) else []
    hypotheses: list[dict[str, str]] = []
    for item in raw_hypotheses:
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim") or "").strip()
        falsification = str(item.get("falsification") or item.get("falsificationPlan") or "").strip()
        if claim:
            hypotheses.append(
                {
                    "id": str(item.get("id") or f"h{len(hypotheses) + 1}"),
                    "claim": claim,
                    "falsification": falsification,
                    "status": "open",
                }
            )

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

    reproduction_contract = {
        "steps": reproduction_steps,
        "observer": observer,
        "environment": str(payload.get("environment") or "").strip(),
    }
    session = {
        "version": 1,
        "sessionId": str(payload.get("sessionId") or f"runtime-{uuid.uuid4().hex[:12]}"),
        "status": "ready_for_patch" if not issues else "blocked",
        "symptom": symptom,
        "reproduction": reproduction_contract,
        "reproductionFingerprint": _fingerprint(reproduction_contract),
        "observer": observer,
        "baselineEvidence": baseline,
        "baselineEvidenceHash": _fingerprint(baseline),
        "hypotheses": hypotheses,
        "selectedHypothesisId": str(payload.get("selectedHypothesisId") or "").strip(),
        "patchEvidence": {},
        "verification": {},
        "issues": issues,
        "proofLevel": "RuntimeObserved" if not issues else "Proposed",
        "createdAt": _utc_now(),
        "updatedAt": _utc_now(),
    }
    session["writeGate"] = {
        "writesAllowed": not issues,
        "reason": "causal baseline and falsification contract captured" if not issues else "runtime causal contract incomplete",
    }
    return {"ok": not issues, "session": session, "issues": issues}


def record_runtime_patch(
    session: dict[str, Any],
    *,
    changed_files: list[str],
    patch_summary: str,
    build_proof: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if str(session.get("status") or "") != "ready_for_patch":
        return {"ok": False, "error": "runtime session is not ready_for_patch", "session": session}
    files = _clean_strings(changed_files)
    summary = str(patch_summary or "").strip()
    if not files or not summary:
        return {"ok": False, "error": "changedFiles and patchSummary are required", "session": session}
    updated = dict(session)
    updated["patchEvidence"] = {
        "changedFiles": files,
        "patchSummary": summary,
        "buildProof": dict(build_proof or {}),
        "recordedAt": _utc_now(),
    }
    updated["status"] = "awaiting_same_observer_verification"
    updated["proofLevel"] = "BuildVerified" if (build_proof or {}).get("ok") else "SourceVerified"
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

    updated = dict(session)
    updated["verification"] = {
        "reproductionFingerprint": supplied_fingerprint,
        "observer": normalized_observer,
        "afterEvidence": evidence,
        "afterEvidenceHash": _fingerprint(evidence),
        "outcome": normalized_outcome,
        "sameObserver": not any("same observer" in issue for issue in issues),
        "sameReproduction": supplied_fingerprint == expected_fingerprint,
        "verifiedAt": _utc_now(),
    }
    if issues:
        updated["status"] = "verification_rejected"
        updated["proofLevel"] = "NeedsRuntimeProof"
    elif normalized_outcome == "resolved":
        updated["status"] = "runtime_verified"
        updated["proofLevel"] = "RuntimeVerified"
    else:
        updated["status"] = "runtime_not_fixed"
        updated["proofLevel"] = "RuntimeVerified"
    updated["issues"] = issues
    updated["updatedAt"] = _utc_now()
    return {"ok": not issues, "session": updated, "issues": issues}
