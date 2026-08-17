"""Canonical synthesis-readiness semantics shared by Python task control."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

POLICY = json.loads(
    (Path(__file__).resolve().parents[1] / "config" / "synthesis_readiness_policy.json")
    .read_text(encoding="utf-8")
)

SOURCE_EVIDENCE_TASK_KINDS = frozenset(
    {
        *(str(item).casefold() for item in POLICY.get("directEvidenceTaskKinds", [])),
        "source_analysis",
    }
)


def is_source_evidence_task(state: dict[str, Any] | None) -> bool:
    """Return whether source-evidence liveness controls apply to this task."""

    value = state if isinstance(state, dict) else {}
    task_kind = str(value.get("taskKind") or "").strip().casefold()
    if task_kind in SOURCE_EVIDENCE_TASK_KINDS:
        return True
    contract = value.get("inspectionContract") if isinstance(value.get("inspectionContract"), dict) else {}
    if str(contract.get("intent") or "").strip().casefold() in SOURCE_EVIDENCE_TASK_KINDS:
        return True
    repository_audit = value.get("repoAuditLedger") if isinstance(value.get("repoAuditLedger"), dict) else {}
    return repository_audit.get("required") is True


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _strings(value: Any, limit: int = 32) -> list[str]:
    rows = {
        str(item or "").replace("\\", "/").strip()
        for item in (value if isinstance(value, list) else [])
    }
    return sorted(item for item in rows if item)[:limit]


def _pairing_key(value: Any) -> str:
    name = str(value or "").replace("\\", "/").casefold()
    for suffix in (".hpp", ".cpp", ".cxx", ".inl", ".h", ".cc", ".c"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    parts = [part for part in name.split("/") if part]
    source_index = max((index for index, part in enumerate(parts) if part == "source"), default=-1)
    if source_index >= 0 and len(parts) > source_index + 2:
        relative = parts[source_index + 2 :]
        if relative and relative[0] in {"public", "private", "classes"}:
            relative = relative[1:]
        return f"{'/'.join(parts[: source_index + 2])}:{'/'.join(relative)}"
    return name


def _nonnegative_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, parsed)


def derive_synthesis_readiness(state: dict[str, Any] | None) -> dict[str, Any]:
    state = state if isinstance(state, dict) else {}
    plan_revision = str(state.get("planRevision") or "")
    ledger = state.get("sourceEvidence") if isinstance(state.get("sourceEvidence"), dict) else {}
    files_obj = ledger.get("files") if isinstance(ledger.get("files"), dict) else {}
    files = list(files_obj.values()) if str(ledger.get("planRevision") or "") == plan_revision else []
    files = [row for row in files if isinstance(row, dict)]
    accepted_ids = _strings([
        row.get("evidenceId") or f"{row.get('path') or ''}:{row.get('contentHash') or ''}"
        for row in files
    ])
    declarations = [row for row in files if str(row.get("sourceKind") or "") == "declaration"]
    implementations = [row for row in files if str(row.get("sourceKind") or "") == "implementation"]
    decl_keys = {_pairing_key(row.get("path")) for row in declarations}
    impl_keys = {_pairing_key(row.get("path")) for row in implementations}
    pair_count = len({key for key in decl_keys if key and key in impl_keys})
    contract = state.get("inspectionContract") if isinstance(state.get("inspectionContract"), dict) else {}
    budget = contract.get("evidenceBudget") if isinstance(contract.get("evidenceBudget"), dict) else {}
    progress = state.get("inspectionProgress") if isinstance(state.get("inspectionProgress"), dict) else {}
    frontier = _strings(
        progress.get("remainingFrontier") or state.get("remainingFrontier"),
        int(POLICY.get("maximumFrontierEntries") or 32),
    )
    required_pairs = max(1, int(budget.get("representativePairs") or POLICY.get("defaultRepresentativePairs") or 1))
    max_reads = max(1, int(budget.get("maxDirectSourceReadsPerPhase") or 2**31 - 1))
    max_chars = max(1, int(budget.get("maxEvidenceCharsPerPhase") or 2**31 - 1))
    bound_reached = int(progress.get("directSourceReads") or 0) >= max_reads or int(progress.get("evidenceCharacters") or 0) >= max_chars
    task_kind = str(state.get("taskKind") or "").casefold()
    direct_required = task_kind not in POLICY.get("evidenceFreeTaskKinds", [])
    direct_satisfied = not direct_required or (
        len(accepted_ids) >= int(POLICY.get("minimumAcceptedDirectEvidence") or 2)
        and len(declarations) >= int(POLICY.get("minimumDeclarationEvidence") or 1)
        and len(implementations) >= int(POLICY.get("minimumImplementationEvidence") or 1)
    )
    representative_satisfied = not direct_required or pair_count >= required_pairs
    repo = state.get("repoAuditLedger") if isinstance(state.get("repoAuditLedger"), dict) else {}
    repo_open = repo.get("required") is True and (
        int(repo.get("remainingCount") or 0) > 0
        or repo.get("overflow") is True
        or str(repo.get("status") or "") != "complete"
    )
    partial_allowed = direct_satisfied and pair_count > 0 and bound_reached and bool(frontier)
    coverage_satisfied = not repo_open and (representative_satisfied or partial_allowed)
    coverage_incomplete = repo_open or not representative_satisfied
    recovery = state.get("recoveryObligation") if isinstance(state.get("recoveryObligation"), dict) else {}
    pending = str(recovery.get("status") or "").casefold() in {
        "evidence_required", "repair_planning_required", "revalidate_required"
    }
    mode_eligible = (
        str(state.get("mode") or "").casefold() == "read_only"
        and state.get("writesAllowed") is not True
        and not (state.get("writeGate") or {}).get("writesAllowed")
    )
    ready = mode_eligible and direct_satisfied and coverage_satisfied and not pending
    reason = "ready"
    if not mode_eligible:
        reason = "task_not_read_only"
    elif not direct_satisfied:
        reason = "direct_source_evidence_missing" if not accepted_ids else "direct_source_evidence_insufficient"
    elif repo_open:
        reason = "repository_frontier_open"
    elif not representative_satisfied and not partial_allowed:
        reason = "representative_coverage_insufficient"
    elif pending:
        reason = "pending_evidence_obligation"
    accepted_hash = _hash(accepted_ids)
    frontier_hash = _hash(frontier)
    return {
        "version": 1, "ready": ready, "reason": reason,
        "acceptedDirectEvidenceCount": len(accepted_ids), "acceptedEvidenceIds": accepted_ids,
        "acceptedEvidenceHash": accepted_hash, "declarationCount": len(declarations),
        "implementationCount": len(implementations), "representativePairCount": pair_count,
        "requiredRepresentativePairs": required_pairs, "coverageMode": str(contract.get("coverageMode") or ""),
        "coverageSatisfied": coverage_satisfied, "coverageIncomplete": coverage_incomplete,
        "remainingFrontier": frontier, "remainingFrontierHash": frontier_hash,
        "pendingEvidenceObligation": pending, "sourceEvidencePlanRevision": str(ledger.get("planRevision") or ""),
        "planRevision": plan_revision, "controlEpoch": _nonnegative_int(state.get("controlEpoch")),
        "commitEligible": ready, "boundReached": bound_reached,
    }


def synthesis_latch_matches(state: dict[str, Any], readiness: dict[str, Any] | None = None) -> bool:
    readiness = readiness or derive_synthesis_readiness(state)
    action = state.get("postBudgetAction") if isinstance(state.get("postBudgetAction"), dict) else {}
    try:
        action_epoch = int(action.get("controlEpoch"))
        state_epoch = int(state.get("controlEpoch"))
    except (TypeError, ValueError, OverflowError):
        return False
    return (
        str(action.get("name") or "") == "synthesize_current_evidence"
        and action_epoch >= 0
        and state_epoch >= 0
        and action_epoch == state_epoch
        and str(action.get("planRevision") or "") == str(state.get("planRevision") or "")
        and str(action.get("acceptedEvidenceHash") or "") == readiness["acceptedEvidenceHash"]
        and str(action.get("remainingFrontierHash") or "") == readiness["remainingFrontierHash"]
        and readiness["ready"] is True
    )
