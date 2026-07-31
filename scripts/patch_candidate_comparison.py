#!/usr/bin/env python
"""Evidence-gated comparison for two to four isolated patch candidates."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

MIN_CANDIDATES = 2
MAX_CANDIDATES = 4
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _normalize_changed_file(value: Any) -> tuple[str, str]:
    raw = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if (
        not raw
        or raw.startswith("/")
        or WINDOWS_DRIVE_RE.match(raw)
        or ".." in path.parts
    ):
        return "", "changedFiles must use project-relative paths without traversal"
    normalized = path.as_posix()
    if not normalized.startswith(("Source/", "Plugins/", "Config/")):
        return "", "changedFiles must stay under Source, Plugins, or Config"
    return normalized, ""


def compare_patch_candidates(
    values: Any,
    *,
    selected_candidate_id: str = "",
    selection_rationale: str = "",
) -> dict[str, Any]:
    raw = values if isinstance(values, list) else []
    issues: list[str] = []
    if not MIN_CANDIDATES <= len(raw) <= MAX_CANDIDATES:
        issues.append("two to four patch candidates are required")
    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for index, value in enumerate(raw[:MAX_CANDIDATES]):
        row = value if isinstance(value, dict) else {}
        candidate_id = str(row.get("id") or f"candidate-{index + 1}").strip()
        files: list[str] = []
        path_issues: list[str] = []
        for item in row.get("changedFiles") or []:
            normalized_path, path_issue = _normalize_changed_file(item)
            if path_issue:
                path_issues.append(path_issue)
            elif normalized_path and normalized_path not in files:
                files.append(normalized_path)
        diff_hash = str(row.get("diffHash") or "").strip()
        evidence = (
            row.get("sandboxEvidence")
            if isinstance(row.get("sandboxEvidence"), dict)
            else {}
        )
        invariant_results = (
            evidence.get("invariantResults")
            if isinstance(evidence.get("invariantResults"), dict)
            else {}
        )
        static_proof = (
            evidence.get("staticProof")
            if isinstance(evidence.get("staticProof"), dict)
            else {}
        )
        build_proof = (
            evidence.get("buildProof")
            if isinstance(evidence.get("buildProof"), dict)
            else {}
        )
        candidate_issues: list[str] = []
        if candidate_id in seen_ids:
            candidate_issues.append("candidate id must be unique")
            issues.append("candidate ids must be unique")
        if not files:
            candidate_issues.append("changedFiles is required")
        candidate_issues.extend(path_issues)
        if not diff_hash:
            candidate_issues.append("diffHash is required")
        elif diff_hash in seen_hashes:
            candidate_issues.append("diffHash must represent a distinct patch")
            issues.append("candidate diff hashes must be unique")
        if not str(evidence.get("isolatedRoot") or "").strip():
            candidate_issues.append("sandboxEvidence.isolatedRoot is required")
        static_passed = (
            evidence.get("staticPassed") is True
            and static_proof.get("ok") is True
            and bool(
                static_proof.get("artifactHash")
                or static_proof.get("reportPath")
            )
        )
        build_passed = (
            evidence.get("buildPassed") is True
            and build_proof.get("ok") is True
            and bool(build_proof.get("artifactHash") or build_proof.get("logPath"))
        )
        runtime_compatible = evidence.get("runtimeCompatible") is True
        invariants_passed = bool(invariant_results) and all(
            value is True for value in invariant_results.values()
        )
        if not static_passed:
            candidate_issues.append(
                "static sandbox validation needs passing proof and an artifact hash/report path"
            )
        if not build_passed:
            candidate_issues.append(
                "sandbox build needs passing proof and an artifact hash/log path"
            )
        if not invariants_passed:
            candidate_issues.append("sandbox invariant checks did not all pass")
        if not runtime_compatible:
            candidate_issues.append("candidate is not tied to the supported runtime hypothesis")
        score = (
            int(static_passed) * 30
            + int(build_passed) * 30
            + int(invariants_passed) * 25
            + int(runtime_compatible) * 10
            + max(0, 5 - min(5, len(files) - 1))
        )
        candidates.append(
            {
                "id": candidate_id,
                "changedFiles": files,
                "diffHash": diff_hash,
                "sandboxEvidence": dict(evidence),
                "eligible": not candidate_issues,
                "issues": candidate_issues,
                "evidenceScore": score,
            }
        )
        seen_ids.add(candidate_id)
        if diff_hash:
            seen_hashes.add(diff_hash)

    ranked = sorted(
        (candidate for candidate in candidates if candidate["eligible"]),
        key=lambda candidate: (
            -int(candidate["evidenceScore"]),
            len(candidate["changedFiles"]),
            candidate["id"],
        ),
    )
    recommended = ranked[0]["id"] if ranked else ""
    selected = str(selected_candidate_id or recommended).strip()
    selected_row = next(
        (candidate for candidate in ranked if candidate["id"] == selected),
        None,
    )
    if not ranked:
        issues.append("at least one fully verified sandbox candidate is required")
    if selected_row is None:
        issues.append("selectedPatchCandidateId must name an eligible candidate")
    if selected and recommended and selected != recommended and not selection_rationale.strip():
        issues.append(
            "patchSelectionRationale is required when overriding the recommended candidate"
        )
    return {
        "ok": not issues,
        "candidates": candidates,
        "eligibleCount": len(ranked),
        "ranking": [candidate["id"] for candidate in ranked],
        "recommendedCandidateId": recommended,
        "selectedCandidateId": selected,
        "selectedCandidate": selected_row or {},
        "selectionRationale": str(selection_rationale or "").strip(),
        "issues": issues,
        "proofBoundary": (
            "Candidate evidence must come from isolated roots. This comparison checks "
            "the evidence contract; it does not itself create the sandbox or run Unreal."
        ),
    }
