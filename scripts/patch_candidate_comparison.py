#!/usr/bin/env python
"""Evidence-gated comparison for two to four isolated patch candidates."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import PurePosixPath
from typing import Any

MIN_CANDIDATES = 2
MAX_CANDIDATES = 4
MIN_ELIGIBLE_CANDIDATES = 2
WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _non_empty_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _append_issue(issues: list[str], issue: str) -> None:
    if issue not in issues:
        issues.append(issue)


def _normalize_changed_file(value: Any) -> tuple[str, str]:
    if not isinstance(value, str):
        return "", "changedFiles entries must be strings"
    raw = value.strip().replace("\\", "/")
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
    for index, value in enumerate(raw[:MAX_CANDIDATES]):
        row = value if isinstance(value, dict) else {}
        candidate_issues: list[str] = []
        if not isinstance(value, dict):
            candidate_issues.append("candidate must be an object")
        supplied_candidate_id = _non_empty_text(row.get("id"))
        candidate_id = supplied_candidate_id or f"candidate-{index + 1}"
        if not supplied_candidate_id:
            candidate_issues.append("candidate id is required")
        files: list[str] = []
        path_issues: list[str] = []
        changed_files = row.get("changedFiles")
        if not isinstance(changed_files, list):
            changed_files = []
            candidate_issues.append("changedFiles must be an array")
        for item in changed_files:
            normalized_path, path_issue = _normalize_changed_file(item)
            if path_issue:
                path_issues.append(path_issue)
            elif normalized_path and normalized_path not in files:
                files.append(normalized_path)
        diff_hash = _non_empty_text(row.get("diffHash"))
        evidence = (
            row.get("sandboxEvidence")
            if isinstance(row.get("sandboxEvidence"), dict)
            else {}
        )
        if not isinstance(row.get("sandboxEvidence"), dict):
            candidate_issues.append("sandboxEvidence must be an object")
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
        if not files:
            candidate_issues.append("changedFiles is required")
        candidate_issues.extend(path_issues)
        if not diff_hash:
            candidate_issues.append("diffHash is required")
        isolated_root = _non_empty_text(evidence.get("isolatedRoot"))
        if not isolated_root:
            candidate_issues.append("sandboxEvidence.isolatedRoot is required")
        static_passed = (
            evidence.get("staticPassed") is True
            and static_proof.get("ok") is True
            and bool(
                _non_empty_text(static_proof.get("artifactHash"))
                or _non_empty_text(static_proof.get("reportPath"))
            )
        )
        build_passed = (
            evidence.get("buildPassed") is True
            and build_proof.get("ok") is True
            and bool(
                _non_empty_text(build_proof.get("artifactHash"))
                or _non_empty_text(build_proof.get("logPath"))
            )
        )
        runtime_compatible = evidence.get("runtimeCompatible") is True
        invariants_passed = bool(invariant_results) and all(
            isinstance(name, str) and bool(name.strip()) and result is True
            for name, result in invariant_results.items()
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
                "eligible": False,
                "issues": candidate_issues,
                "evidenceScore": score,
            }
        )

    collision_contracts = (
        (
            "id",
            "candidate id must be unique",
            "candidate ids must be unique",
        ),
        (
            "diffHash",
            "diffHash must represent a distinct patch",
            "candidate diff hashes must be unique",
        ),
    )
    for field, candidate_issue, comparison_issue in collision_contracts:
        counts = Counter(
            str(candidate.get(field) or "")
            for candidate in candidates
            if str(candidate.get(field) or "")
        )
        collisions = {value for value, count in counts.items() if count > 1}
        if collisions:
            _append_issue(issues, comparison_issue)
            for candidate in candidates:
                if str(candidate.get(field) or "") in collisions:
                    _append_issue(candidate["issues"], candidate_issue)
    root_counts = Counter(
        _non_empty_text(candidate["sandboxEvidence"].get("isolatedRoot"))
        for candidate in candidates
        if _non_empty_text(candidate["sandboxEvidence"].get("isolatedRoot"))
    )
    root_collisions = {value for value, count in root_counts.items() if count > 1}
    if root_collisions:
        _append_issue(issues, "candidate isolated roots must be unique")
        for candidate in candidates:
            if (
                _non_empty_text(candidate["sandboxEvidence"].get("isolatedRoot"))
                in root_collisions
            ):
                _append_issue(
                    candidate["issues"],
                    "sandboxEvidence.isolatedRoot must identify a distinct sandbox",
                )
    for candidate in candidates:
        candidate["eligible"] = not candidate["issues"]

    ranked = sorted(
        (candidate for candidate in candidates if candidate["eligible"]),
        key=lambda candidate: (
            -int(candidate["evidenceScore"]),
            len(candidate["changedFiles"]),
            candidate["id"],
        ),
    )
    recommended = ranked[0]["id"] if ranked else ""
    explicit_selection = _non_empty_text(selected_candidate_id)
    selected = explicit_selection or recommended
    selected_row = next(
        (candidate for candidate in ranked if candidate["id"] == selected),
        None,
    )
    competition_satisfied = len(ranked) >= MIN_ELIGIBLE_CANDIDATES
    if not competition_satisfied:
        issues.append(
            "at least two fully verified sandbox candidates are required for competition"
        )
    if selected_row is None:
        issues.append("selectedPatchCandidateId must name an eligible candidate")
    rationale = _non_empty_text(selection_rationale)
    if selected and recommended and selected != recommended and not rationale:
        issues.append(
            "patchSelectionRationale is required when overriding the recommended candidate"
        )
    top_score = int(ranked[0]["evidenceScore"]) if ranked else -1
    tied_top_candidates = [
        candidate["id"]
        for candidate in ranked
        if int(candidate["evidenceScore"]) == top_score
    ]
    ambiguous = len(tied_top_candidates) > 1
    if ambiguous and not explicit_selection:
        issues.append(
            "selectedPatchCandidateId is required when top candidates are tied"
        )
    if ambiguous and not rationale:
        issues.append(
            "patchSelectionRationale is required when top candidates are tied"
        )
    return {
        "ok": not issues,
        "mode": "competition",
        "competitionSatisfied": competition_satisfied,
        "minimumEligibleCandidates": MIN_ELIGIBLE_CANDIDATES,
        "candidates": candidates,
        "eligibleCount": len(ranked),
        "ranking": [candidate["id"] for candidate in ranked],
        "recommendedCandidateId": recommended,
        "selectedCandidateId": selected,
        "selectedCandidate": selected_row or {},
        "selectionRationale": rationale,
        "ambiguous": ambiguous,
        "tiedTopCandidateIds": tied_top_candidates,
        "issues": issues,
        "proofBoundary": (
            "Competition requires at least two fully verified candidates from distinct "
            "isolated roots. This comparison checks the evidence contract; it does not "
            "itself create the sandboxes or run Unreal."
        ),
    }
