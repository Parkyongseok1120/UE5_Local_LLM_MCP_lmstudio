#!/usr/bin/env python
"""Lightweight local failure memory for compile loop recovery."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


FAILURE_MEMORY_STATUSES = frozenset(
    {"candidate", "verified", "accepted", "expired", "rejected"}
)
TERMINAL_FAILURE_MEMORY_STATUSES = frozenset({"expired", "rejected"})
DEFAULT_ACCEPTANCE_VERIFICATION_THRESHOLD = 2


def signature(error_subkind: str, error_code: str, symbol_name: str) -> str:
    raw = f"{error_subkind}|{error_code}|{symbol_name}".lower()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def append_failure_memory(
    memory_dir: Path,
    project_name: str,
    *,
    error_subkind: str,
    error_code: str,
    symbol_name: str,
    failed_summary: str,
    fix_summary: str,
    changed_files: list[str],
    diff_excerpt: str,
    rag_evidence_ids: list[str],
    original_request: str = "",
    failed_output_summary: str = "",
    bad_chunk_ids: list[str] | None = None,
    good_chunk_ids: list[str] | None = None,
    missing_evidence: str = "",
    final_explanation: str = "",
    retry_count: int = 0,
    model: str = "",
    sampling_profile: str = "",
    status: str = "candidate",
    project_fingerprint: str = "",
    engine_version: str = "",
    verification_count: int = 0,
    expires_days: int | None = None,
) -> Path:
    memory_dir.mkdir(parents=True, exist_ok=True)
    out = memory_dir / f"{project_name}_failures.jsonl"
    error_signature = f"{error_subkind}|{error_code}|{symbol_name}"
    now = datetime.now(timezone.utc)
    requested_status = str(status or "candidate").strip().lower()
    # A new observation has no independent proof yet. Keep the status argument
    # for source compatibility, but never let a caller create a trusted record
    # without passing through update_failure_memory_status().
    normalized_status = "candidate"
    try:
        retention_days = max(
            1,
            min(3650, int(expires_days) if expires_days is not None else 14),
        )
    except (TypeError, ValueError, OverflowError):
        retention_days = 14
    record = {
        "id": signature(error_subkind, error_code, symbol_name),
        "source": "unreal_failure_memory",
        "generatedAt": now.isoformat(),
        "error_subkind": error_subkind,
        "error_code": error_code,
        "symbol_name": symbol_name,
        "error_signature": error_signature,
        "original_request": original_request[:500],
        "failed_summary": failed_summary[:500],
        "failed_output_summary": failed_output_summary[:500],
        "fix_summary": fix_summary[:500],
        "final_explanation": (final_explanation or fix_summary)[:500],
        "changed_files": changed_files,
        "diff_excerpt": diff_excerpt[:2000],
        "rag_evidence_ids": rag_evidence_ids[:10],
        "bad_chunk_ids": (bad_chunk_ids or [])[:10],
        "good_chunk_ids": (good_chunk_ids or rag_evidence_ids or [])[:10],
        "missing_evidence": missing_evidence[:300],
        "retry_count": retry_count,
        "model": model,
        "sampling_profile": sampling_profile,
        "status": normalized_status,
        "verificationCount": 0,
        "lastVerifiedAt": "",
        "expiresAt": (now + timedelta(days=retention_days)).isoformat(),
        "projectFingerprint": str(project_fingerprint or "").strip(),
        "engineVersion": str(engine_version or "").strip(),
        "title": f"Failure memory: {error_subkind} {error_code}",
        "text": f"Prior fix for {error_subkind}: {fix_summary}\nFiles: {', '.join(changed_files)}",
        "metadata": {
            "error_subkind": error_subkind,
            "error_code": error_code,
            "symbol_name": symbol_name,
            "project": project_name,
            "status": normalized_status,
            "projectFingerprint": str(project_fingerprint or "").strip(),
            "engineVersion": str(engine_version or "").strip(),
        },
        "lifecycleHistory": [
            {
                "from": "",
                "to": "candidate",
                "at": now.isoformat(),
                "reason": (
                    "new_observation"
                    if requested_status in {"", "candidate"}
                    else f"untrusted_initial_status_downgraded:{requested_status}"
                ),
            }
        ],
    }
    with out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return out


def update_failure_memory_status(
    memory_dir: Path,
    project_name: str,
    record_id: str,
    *,
    status: str,
    verification_evidence: dict[str, Any] | None = None,
    expires_days: int | None = None,
    acceptance_threshold: int = DEFAULT_ACCEPTANCE_VERIFICATION_THRESHOLD,
) -> bool:
    """Append an auditable, fail-closed lifecycle update.

    Valid transitions are candidate -> verified -> accepted. Additional
    independent verified events may be appended before acceptance. Any active
    state may be rejected or expired; those states are terminal.
    """
    path = memory_dir / f"{project_name}_failures.jsonl"
    if not path.is_file():
        return False
    latest: dict[str, Any] | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if str(row.get("id") or "") == str(record_id or ""):
            latest = row
    if latest is None:
        return False
    latest_metadata = latest.get("metadata")
    if latest_metadata is not None and not isinstance(latest_metadata, dict):
        return False
    latest_record_project = str(
        (latest_metadata or {}).get("project") or ""
    ).strip()
    if latest_record_project and latest_record_project != project_name:
        return False
    now = datetime.now(timezone.utc)
    normalized = str(status or "").strip().lower()
    if normalized not in FAILURE_MEMORY_STATUSES:
        return False
    current = str(latest.get("status") or "candidate").strip().lower()
    if current not in FAILURE_MEMORY_STATUSES:
        return False
    if current in TERMINAL_FAILURE_MEMORY_STATUSES:
        return False

    if verification_evidence is not None and not isinstance(
        verification_evidence,
        dict,
    ):
        return False
    evidence = dict(verification_evidence or {})
    valid_transitions = {
        "candidate": {"verified", "expired", "rejected"},
        "verified": {"verified", "accepted", "expired", "rejected"},
        "accepted": {"expired", "rejected"},
    }
    if normalized not in valid_transitions.get(current, set()):
        return False
    if normalized not in TERMINAL_FAILURE_MEMORY_STATUSES and _record_is_expired(
        latest,
        now,
    ):
        return False

    latest_engine = str(latest.get("engineVersion") or "").strip()
    latest_project = str(latest.get("projectFingerprint") or "").strip()
    if normalized == "verified":
        proof_ok, evidence_engine, evidence_project = _verification_evidence_scope(evidence)
        if not proof_ok:
            return False
        if latest_engine and latest_engine != evidence_engine:
            return False
        if latest_project and latest_project != evidence_project:
            return False
        prior_artifacts: set[tuple[str, str]] = set()
        prior_history = latest.get("verificationHistory")
        for prior in prior_history if isinstance(prior_history, list) else []:
            if isinstance(prior, dict):
                prior_artifacts.update(_verification_artifact_keys(prior))
        if prior_artifacts.intersection(_verification_artifact_keys(evidence)):
            return False
    elif normalized == "accepted":
        try:
            required_count = max(
                DEFAULT_ACCEPTANCE_VERIFICATION_THRESHOLD,
                int(acceptance_threshold),
            )
        except (TypeError, ValueError):
            return False
        verified_count = _trusted_verification_count(latest)
        if current != "verified" or verified_count < required_count:
            return False
        if not latest_engine or not latest_project:
            return False
        if _record_is_expired(latest, now):
            return False
        if not _record_has_valid_verification(latest):
            return False
        supplied_engine = str(evidence.get("engineVersion") or "").strip()
        supplied_project = str(evidence.get("projectFingerprint") or "").strip()
        if supplied_engine and supplied_engine != latest_engine:
            return False
        if supplied_project and supplied_project != latest_project:
            return False

    updated = dict(latest)
    updated["status"] = normalized
    updated["generatedAt"] = now.isoformat()
    if normalized == "verified":
        if current == "verified" and not _record_has_valid_verification(latest):
            return False
        previous_verification_count = (
            _trusted_verification_count(latest)
            if current == "verified"
            else 0
        )
        updated["engineVersion"] = latest_engine or evidence_engine
        updated["projectFingerprint"] = latest_project or evidence_project
        updated["verificationEvidence"] = evidence
        updated["verificationCount"] = previous_verification_count + 1
        updated["lastVerifiedAt"] = now.isoformat()
        existing_verification_history = updated.get("verificationHistory")
        verification_history = (
            list(existing_verification_history)
            if isinstance(existing_verification_history, list)
            else []
        )
        verification_history.append(evidence)
        updated["verificationHistory"] = verification_history[-32:]
    elif evidence:
        updated["lifecycleEvidence"] = evidence
    retention_days = expires_days if expires_days is not None else (
        90 if normalized in {"verified", "accepted"} else 14
    )
    try:
        normalized_retention_days = max(1, min(3650, int(retention_days)))
    except (TypeError, ValueError, OverflowError):
        return False
    if normalized == "expired":
        updated["expiresAt"] = now.isoformat()
    elif normalized == "rejected":
        # Rejection is terminal and should not look temporarily reusable.
        updated["expiresAt"] = latest.get("expiresAt") or now.isoformat()
    else:
        updated["expiresAt"] = (
            now + timedelta(days=normalized_retention_days)
        ).isoformat()
    metadata = dict(latest_metadata or {})
    metadata["project"] = project_name
    metadata["status"] = normalized
    metadata["engineVersion"] = str(updated.get("engineVersion") or "").strip()
    metadata["projectFingerprint"] = str(
        updated.get("projectFingerprint") or ""
    ).strip()
    updated["metadata"] = metadata
    existing_lifecycle_history = updated.get("lifecycleHistory")
    lifecycle_history = (
        list(existing_lifecycle_history)
        if isinstance(existing_lifecycle_history, list)
        else []
    )
    lifecycle_history.append(
        {
            "from": current,
            "to": normalized,
            "at": now.isoformat(),
        }
    )
    updated["lifecycleHistory"] = lifecycle_history[-64:]
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(updated, ensure_ascii=False) + "\n")
    return True


def _verification_evidence_scope(
    evidence: dict[str, Any],
) -> tuple[bool, str, str]:
    engine_version = str(evidence.get("engineVersion") or "").strip()
    project_fingerprint = str(evidence.get("projectFingerprint") or "").strip()
    if not engine_version or not project_fingerprint:
        return False, engine_version, project_fingerprint
    proof_valid = False
    for key in ("buildProof", "runtimeProof"):
        proof = evidence.get(key)
        if not isinstance(proof, dict):
            continue
        artifact_hash = str(proof.get("artifactHash") or "").strip()
        if proof.get("ok") is True and artifact_hash:
            proof_valid = True
            break
    return proof_valid, engine_version, project_fingerprint


def _verification_artifact_keys(
    evidence: dict[str, Any],
) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for proof_name in ("buildProof", "runtimeProof"):
        proof = evidence.get(proof_name)
        if not isinstance(proof, dict) or proof.get("ok") is not True:
            continue
        artifact_hash = str(proof.get("artifactHash") or "").strip()
        if artifact_hash:
            keys.add((proof_name, artifact_hash))
    return keys


def _record_has_valid_verification(record: dict[str, Any]) -> bool:
    try:
        claimed_count = int(record.get("verificationCount") or 0)
        if claimed_count < 1:
            return False
    except (TypeError, ValueError):
        return False
    evidence = record.get("verificationEvidence")
    if not isinstance(evidence, dict):
        return False
    proof_ok, evidence_engine, evidence_project = _verification_evidence_scope(
        evidence
    )
    return (
        proof_ok
        and evidence_engine == str(record.get("engineVersion") or "").strip()
        and evidence_project
        == str(record.get("projectFingerprint") or "").strip()
        and claimed_count == _trusted_verification_count(record)
    )


def _trusted_verification_count(record: dict[str, Any]) -> int:
    history = record.get("verificationHistory")
    evidence_rows = (
        [row for row in history if isinstance(row, dict)]
        if isinstance(history, list)
        else []
    )
    latest = record.get("verificationEvidence")
    if not evidence_rows and isinstance(latest, dict):
        # Backward-compatible only when the legacy evidence itself satisfies
        # the new proof and exact-scope contract.
        evidence_rows = [latest]

    record_engine = str(record.get("engineVersion") or "").strip()
    record_project = str(record.get("projectFingerprint") or "").strip()
    seen_artifacts: set[tuple[str, str]] = set()
    count = 0
    for evidence in evidence_rows:
        proof_ok, evidence_engine, evidence_project = _verification_evidence_scope(
            evidence
        )
        artifact_keys = _verification_artifact_keys(evidence)
        if (
            not proof_ok
            or evidence_engine != record_engine
            or evidence_project != record_project
            or not artifact_keys
            or seen_artifacts.intersection(artifact_keys)
        ):
            continue
        seen_artifacts.update(artifact_keys)
        count += 1
    return count


def _record_is_expired(
    record: dict[str, Any],
    now: datetime | None = None,
) -> bool:
    expires_at = str(record.get("expiresAt") or "").strip()
    if not expires_at:
        return True
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry <= (now or datetime.now(timezone.utc))


def failure_memory_rag_weight() -> float:
    """Low weight — hints only, never override engine evidence."""
    return 0.15


def maybe_auto_reindex_failure_memory(workspace: Path, *, threshold: int = 5) -> None:
    """After accumulating N or more records, run collect + incremental index (best-effort).

    Uses a cumulative >= threshold check so reindex always triggers at or above
    the threshold, avoiding the modulo-zero edge case where certain line counts
    (e.g. 4, 9 when threshold=5) would never trigger.
    """
    import os
    import subprocess
    import sys

    if os.environ.get("UNREAL_FAILURE_MEMORY_AUTO_REINDEX", "1").strip().lower() in {"0", "false", "no"}:
        return
    memory_dir = workspace / "data" / "failure_memory"
    if not memory_dir.is_dir():
        return
    total_lines = sum(
        len([ln for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()])
        for path in memory_dir.glob("*_failures.jsonl")
    )
    # Trigger when total reaches threshold or any multiple of it.
    if total_lines < threshold or total_lines % threshold != 0:
        return
    scripts = workspace / "scripts"
    subprocess.run([sys.executable, str(scripts / "collect_failure_memory.py")], cwd=str(workspace), check=False)
    subprocess.run([sys.executable, str(scripts / "incremental_build.py")], cwd=str(workspace), check=False)
