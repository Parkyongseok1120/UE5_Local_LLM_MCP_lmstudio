from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from workspace_paths import filesystem_path_identity


FRONTIER_CLAIM_TYPES = frozenset(
    {
        "missing_definition",
        "missing_call_edge",
        "missing_branch",
        "missing_file",
        "missing_required_behavior",
    }
)


def is_completion_audit_request(request: str) -> bool:
    text = " ".join(str(request or "").split()).casefold()
    if not text:
        return False
    english = re.search(
        r"\b(?:completion\s+audit|find\s+(?:all\s+)?missing\s+"
        r"(?:features?|implementations?|branches?|steps?)|"
        r"(?:first|earliest|next)\s+incomplete\s+(?:feature|implementation|step)|"
        r"what\s+remains\s+(?:unimplemented|incomplete)|"
        r"current\s+implementation\s+(?:status|frontier))\b",
        text,
    )
    korean_gap = any(
        token in text
        for token in (
            "미완성 기능",
            "미완성 단계",
            "누락 구현",
            "누락된 기능",
            "빠진 기능",
            "완료되지 않은 기능",
            "남은 구현",
        )
    )
    korean_frontier = bool(
        re.search(r"(?:가장\s*)?(?:앞선|이른|다음)\s*미완성", text)
        or re.search(
            r"현재\s*(?:구현\s*)?(?:상태|프런티어).*(?:미완성|누락|남은)",
            text,
        )
    )
    return bool(english or korean_gap or korean_frontier)


def _normalized_project_path(value: Any) -> str:
    path = str(value or "").replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    if not path or path.startswith("/") or ".." in path.split("/"):
        return ""
    return path.strip("/")


def _ledger_entries(ledger: dict[str, Any], *, absent: bool) -> dict[str, dict[str, Any]]:
    selected = (
        ledger.get("absentEvidence")
        if absent and isinstance(ledger.get("absentEvidence"), dict)
        else ledger
    )
    raw_files = selected.get("files") if isinstance(selected.get("files"), dict) else {}
    result: dict[str, dict[str, Any]] = {}
    for key, value in raw_files.items():
        if not isinstance(value, dict):
            continue
        evidence_id = str(value.get("evidenceId") or "").strip()
        if evidence_id:
            result[evidence_id] = {**value, "_key": str(key)}
    return result


def _source_entry_current(project_root: Path, entry: dict[str, Any]) -> bool:
    relative = _normalized_project_path(entry.get("path") or entry.get("_key"))
    expected_hash = str(entry.get("contentHash") or "").strip().casefold()
    if not relative or not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        return False
    candidate = (project_root / relative).resolve()
    try:
        candidate.relative_to(project_root)
        current_hash = hashlib.sha256(candidate.read_bytes()).hexdigest()
    except (OSError, ValueError):
        return False
    ranges = entry.get("coveredRanges")
    ranges_valid = bool(
        isinstance(ranges, list)
        and ranges
        and all(
            isinstance(item, list)
            and len(item) >= 2
            and isinstance(item[0], int)
            and isinstance(item[1], int)
            and item[0] > 0
            and item[1] >= item[0]
            for item in ranges
        )
    )
    return current_hash == expected_hash and ranges_valid


def validate_feature_frontier(
    claims: Any,
    *,
    project_root: str | Path,
    evidence_ledger: dict[str, Any],
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    issues: list[str] = []
    if not isinstance(claims, list) or not claims:
        return {
            "ok": False,
            "errorCode": "FEATURE_FRONTIER_TYPED_CLAIMS_REQUIRED",
            "issues": ["completion-audit requests require at least one typed frontier claim"],
            "claims": [],
        }
    if len(claims) > 32:
        issues.append("frontierClaims exceeds the 32-claim bound")
    source_by_id = _ledger_entries(evidence_ledger, absent=False)
    absent_by_id = _ledger_entries(evidence_ledger, absent=True)
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(claims[:32]):
        prefix = f"frontierClaims[{index}]"
        if not isinstance(raw, dict):
            issues.append(f"{prefix} must be an object")
            continue
        claim_type = str(raw.get("claimType") or "").strip()
        subject_symbol = str(raw.get("subjectSymbol") or "").strip()[:500]
        object_symbol = str(raw.get("objectSymbol") or "").strip()[:500]
        target_path = _normalized_project_path(raw.get("path") or raw.get("targetPath"))
        refs = list(
            dict.fromkeys(
                str(item or "").strip()
                for item in (raw.get("evidenceRefs") or [])
                if str(item or "").strip()
            )
        )[:16]
        if claim_type not in FRONTIER_CLAIM_TYPES:
            issues.append(f"{prefix}.claimType is unsupported")
        if not refs:
            issues.append(f"{prefix}.evidenceRefs must contain server evidence ids")
        if claim_type in {
            "missing_definition",
            "missing_call_edge",
            "missing_branch",
            "missing_required_behavior",
        } and not subject_symbol:
            issues.append(f"{prefix}.subjectSymbol is required for {claim_type}")
        if claim_type == "missing_call_edge" and not object_symbol:
            issues.append(f"{prefix}.objectSymbol is required for missing_call_edge")
        if claim_type == "missing_file" and not target_path:
            issues.append(f"{prefix}.path is required for missing_file")

        resolved_source = [source_by_id[ref] for ref in refs if ref in source_by_id]
        resolved_absent = [absent_by_id[ref] for ref in refs if ref in absent_by_id]
        unresolved = [ref for ref in refs if ref not in source_by_id and ref not in absent_by_id]
        if unresolved:
            issues.append(f"{prefix} has unresolved evidenceRefs: {', '.join(unresolved)}")
        if claim_type == "missing_file":
            matching_absent = [
                entry
                for entry in resolved_absent
                if entry.get("searchComplete") is True
                and filesystem_path_identity(
                    _normalized_project_path(entry.get("path") or entry.get("_key")),
                    trim_outer_slashes=True,
                )
                == filesystem_path_identity(target_path, trim_outer_slashes=True)
            ]
            if not matching_absent:
                issues.append(
                    f"{prefix} requires matching absent evidence with searchComplete=true"
                )
        else:
            if not resolved_source:
                issues.append(f"{prefix} requires current direct-source evidence")
            stale_refs = [
                ref
                for ref in refs
                if ref in source_by_id and not _source_entry_current(root, source_by_id[ref])
            ]
            if stale_refs:
                issues.append(f"{prefix} has stale or uncovered evidenceRefs: {', '.join(stale_refs)}")
        normalized.append(
            {
                "claimType": claim_type,
                **({"subjectSymbol": subject_symbol} if subject_symbol else {}),
                **({"objectSymbol": object_symbol} if object_symbol else {}),
                **({"path": target_path} if target_path else {}),
                "evidenceRefs": refs,
            }
        )

    fingerprint = hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return {
        "ok": not issues,
        "errorCode": "" if not issues else "FEATURE_FRONTIER_TYPED_CLAIMS_INVALID",
        "issues": issues,
        "claims": normalized,
        "fingerprint": fingerprint,
        "authority": "typed_server_evidence",
    }
