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

_PARTIAL_REPORT_SECTION_ALIASES = [
    {
        "key": "coverage",
        "aliases": ["Coverage: partial", "분석 범위 상태: 부분"],
    },
    {
        "key": "analyzed_scope",
        "aliases": [
            "Analyzed scope: only the evidence cited below",
            "분석한 범위: 아래에 인용된 근거만",
        ],
    },
    {
        "key": "omitted_scope",
        "aliases": [
            "Omitted scope: remaining frontier not analyzed",
            "제외한 범위: 아직 분석하지 않은 남은 조사 범위",
        ],
    },
    {
        "key": "stop_reason",
        "aliases": [
            "Stop reason: bounded inspection left unresolved scope",
            "중단 이유: 제한된 조사 범위에 미확인 영역이 남음",
        ],
    },
    {
        "key": "confidence_limits",
        "aliases": [
            "Confidence limits: findings are limited to cited excerpts",
            "신뢰 한계: 인용한 발췌문이 직접 뒷받침하는 사실로 제한",
        ],
    },
    {
        "key": "next_audit_slice",
        "aliases": [
            "Next audit slice: continue the remaining frontier",
            "다음 감사 범위: 남은 조사 범위를 계속 확인",
        ],
    },
]


def synthesis_report_contract(coverage_incomplete: bool) -> dict[str, Any]:
    """Return the server-owned, machine-validated final-report grammar.

    A claim id binds a report line to an exact evidence record.  It is not a
    semantic-entailment proof, so the model must still omit statements that the
    cited excerpt does not directly support.
    """

    return {
        "version": 2,
        "coverageStatus": "partial" if coverage_incomplete else "complete",
        "lineGrammar": "cited_single_line_bullets_with_partial_metadata",
        "claimBulletFormat": "- <claim> [claim:<claimId>]",
        "claimCitationFormat": "[claim:<claimId>]",
        "claimMarkerSameLine": True,
        "markdownHeadingsAllowed": False,
        "standaloneProseAllowed": False,
        "tablesAllowed": False,
        "citationBinding": "evidence_record_identity_not_semantic_entailment",
        "partialRequiredSections": [
            "Coverage: partial",
            "Analyzed scope: only the evidence cited below",
            "Omitted scope: remaining frontier not analyzed",
            "Stop reason: bounded inspection left unresolved scope",
            "Confidence limits: findings are limited to cited excerpts",
            "Next audit slice: continue the remaining frontier",
        ] if coverage_incomplete else [],
        "partialRequiredSectionAliases": (
            [
                {
                    "key": str(section["key"]),
                    "aliases": [str(alias) for alias in section["aliases"]],
                }
                for section in _PARTIAL_REPORT_SECTION_ALIASES
            ]
            if coverage_incomplete else []
        ),
    }

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


def _coverage_ranges(value: Any) -> list[list[int]]:
    """Return a canonical, merged, bounded line-coverage projection."""

    parsed: list[list[int]] = []
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            continue
        try:
            start, end = int(raw[0]), int(raw[1])
        except (TypeError, ValueError, OverflowError):
            continue
        if start > 0 and end >= start:
            parsed.append([start, end])
    parsed.sort(key=lambda item: (item[0], item[1]))
    merged: list[list[int]] = []
    for start, end in parsed:
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return merged[:32]


def _semantic_anchor_digest(row: dict[str, Any]) -> str:
    anchors = _strings(row.get("semanticAnchors"), 32)
    supplied = str(row.get("semanticAnchorDigest") or "").strip().casefold()
    return supplied if len(supplied) == 64 else _hash(anchors)


def _coverage_level(row: dict[str, Any]) -> str:
    explicit = str(row.get("coverageLevel") or "").strip().upper()
    if explicit in {
        "DISCOVERED",
        "RANGE_PARTIAL",
        "SYMBOL_COMPLETE",
        "FILE_COMPLETE",
        "CLAIM_VALIDATED",
    }:
        return explicit
    line_count = _nonnegative_int(row.get("lineCount"))
    ranges = _coverage_ranges(row.get("coveredRanges"))
    complete = row.get("wholeFileComplete") is True or (
        line_count > 0
        and any(start <= 1 and end >= line_count for start, end in ranges)
    )
    if complete and row.get("truncated") is not True:
        return "FILE_COMPLETE"
    if ranges:
        return "RANGE_PARTIAL"
    return "DISCOVERED"


def _evidence_identity(row: dict[str, Any]) -> dict[str, Any]:
    """Identity of evidence *state*, including material coverage."""

    return {
        "path": str(row.get("path") or "").replace("\\", "/"),
        "contentHash": str(row.get("contentHash") or "").casefold(),
        "evidenceSnapshotGeneration": _nonnegative_int(
            row.get("evidenceSnapshotGeneration", row.get("mutationGeneration"))
        ),
        "coveredRanges": _coverage_ranges(row.get("coveredRanges")),
        "wholeFileComplete": row.get("wholeFileComplete") is True,
        "truncated": row.get("truncated") is True,
        "lineCount": _nonnegative_int(row.get("lineCount")),
        "sourceKind": str(row.get("sourceKind") or "").casefold(),
        "coverageLevel": _coverage_level(row),
        "semanticAnchorDigest": _semantic_anchor_digest(row),
    }


def _bounded_excerpts(row: dict[str, Any]) -> list[dict[str, Any]]:
    excerpts: list[dict[str, Any]] = []
    raw_values = row.get("supportingExcerpts")
    if not isinstance(raw_values, list):
        raw_values = []
    for raw in raw_values[:8]:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "")
        maximum = max(1, int(POLICY.get("maximumExactExcerptCharacters") or 4000))
        # An excerpt is an exact claim binding. Silently slicing it would make
        # the declared line range/digest describe text that the model did not
        # actually receive, so oversized excerpts are rejected and a narrower
        # source range must be collected instead.
        if not text or len(text) > maximum:
            continue
        ranges = _coverage_ranges([raw.get("range") or [raw.get("startLine"), raw.get("endLine")]])
        if not ranges:
            continue
        start, end = ranges[0]
        excerpts.append(
            {
                "startLine": start,
                "endLine": end,
                "text": text,
                "excerptDigest": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        )
    return excerpts


def materialize_synthesis_evidence_bundle(
    state: dict[str, Any] | None,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Select and serialize the exact source material sent to final synthesis.

    The durable source ledger may be much larger than the final prompt.  This
    function therefore selects a bounded claim-supporting subset and hashes the
    exact canonical JSON string that the Compactor must inject byte-for-byte.
    Unselected accepted files remain coverage evidence; they are not falsely
    presented as materialized final-claim evidence.
    """

    value = state if isinstance(state, dict) else {}
    plan_revision = str(value.get("planRevision") or "")
    if rows is None:
        ledger = value.get("sourceEvidence") if isinstance(value.get("sourceEvidence"), dict) else {}
        files = ledger.get("files") if isinstance(ledger.get("files"), dict) else {}
        rows = list(files.values()) if str(ledger.get("planRevision") or "") == plan_revision else []
    maximum_claims = max(1, int(POLICY.get("maximumSelectedSynthesisClaims") or 16))
    maximum_characters = max(1024, int(POLICY.get("maximumPromptEvidenceCharacters") or 12000))
    candidates = sorted(
        (item for item in (rows or []) if isinstance(item, dict)),
        key=lambda item: (
            0 if _coverage_level(item) == "CLAIM_VALIDATED" else 1,
            0 if str(item.get("sourceKind") or "") == "declaration" else 1,
            str(item.get("path") or "").casefold(),
        ),
    )
    candidate_declarations = {
        _pairing_key(item.get("path")): item
        for item in candidates
        if Path(str(item.get("path") or "")).suffix.casefold() in {".h", ".hpp", ".hh", ".inl"}
        and _bounded_excerpts(item)
    }
    candidate_implementations = {
        _pairing_key(item.get("path")): item
        for item in candidates
        if Path(str(item.get("path") or "")).suffix.casefold() in {".cpp", ".c", ".cc", ".cxx"}
        and _bounded_excerpts(item)
    }
    paired_keys = sorted(
        key for key in candidate_declarations if key and key in candidate_implementations
    )
    paired_rows = [
        (candidate_declarations[key], candidate_implementations[key])
        for key in paired_keys[: max(1, maximum_claims // 2)]
    ]
    paired_ids = {id(item) for pair in paired_rows for item in pair}
    unpaired_candidates = [item for item in candidates if id(item) not in paired_ids]
    pair_record_target = max(1, len(paired_rows) * 2)
    # Reserve deterministic JSON metadata space, then distribute the remaining
    # exact-source budget fairly across every representative pair.  Selecting
    # one 4K declaration first used to starve later implementation records and
    # let a four-pair readiness claim bind only one pair to the final prompt.
    per_record_excerpt_limit = min(
        max(1, int(POLICY.get("maximumExactExcerptCharacters") or 4000)),
        max(64, (maximum_characters - min(4000, maximum_characters // 3)) // pair_record_target),
    )
    records: list[dict[str, Any]] = []

    def record_for(row: dict[str, Any], excerpt_limit: int) -> dict[str, Any] | None:
        excerpts = _bounded_excerpts(row)
        if not excerpts:
            return None
        identity = _evidence_identity(row)
        excerpt = dict(excerpts[0])
        if len(excerpt["text"]) > excerpt_limit:
            exact_text = excerpt["text"][:excerpt_limit]
            excerpt["text"] = exact_text
            excerpt["endLine"] = min(
                int(excerpt["endLine"]),
                int(excerpt["startLine"]) + exact_text.count("\n"),
            )
            excerpt["excerptDigest"] = hashlib.sha256(
                exact_text.encode("utf-8")
            ).hexdigest()
        claim_id = str(row.get("claimId") or row.get("evidenceId") or _hash(identity)[:24])[:80]
        existing_claim_ids = {str(item.get("claimId") or "") for item in records}
        if claim_id in existing_claim_ids:
            suffix = _hash({"identity": identity, "excerpt": excerpt})[:12]
            claim_id = f"{claim_id[:67]}-{suffix}"
        return {
            "claimId": claim_id,
            "sourcePath": identity["path"],
            "contentHash": identity["contentHash"],
            "startLine": excerpt["startLine"],
            "endLine": excerpt["endLine"],
            "exactExcerpt": excerpt["text"],
            "excerptDigest": excerpt["excerptDigest"],
            "coverageLevel": identity["coverageLevel"],
            "classification": str(row.get("classification") or "direct"),
        }

    def serialized_with(proposed: list[dict[str, Any]]) -> str:
        proposed_binding = {
            "version": 2,
            "taskSessionId": str(value.get("taskSessionId") or ""),
            "objectiveHash": str(value.get("objectiveHash") or ""),
            "planRevision": plan_revision,
            "mutationGeneration": _nonnegative_int(value.get("mutationGeneration")),
            "records": proposed,
        }
        return json.dumps(
            proposed_binding,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    for declaration, implementation in paired_rows:
        pair_records = [
            record
            for row in (declaration, implementation)
            if (record := record_for(row, per_record_excerpt_limit)) is not None
        ]
        if len(pair_records) != 2:
            continue
        proposed = [*records, *pair_records]
        if len(proposed) <= maximum_claims and len(serialized_with(proposed)) <= maximum_characters:
            records = proposed

    for row in unpaired_candidates:
        record = record_for(row, per_record_excerpt_limit)
        if record is None:
            continue
        proposed = [*records, record]
        if len(proposed) > maximum_claims or len(serialized_with(proposed)) > maximum_characters:
            continue
        records = proposed
    binding = {
        "version": 2,
        "taskSessionId": str(value.get("taskSessionId") or ""),
        "objectiveHash": str(value.get("objectiveHash") or ""),
        "planRevision": plan_revision,
        "mutationGeneration": _nonnegative_int(value.get("mutationGeneration")),
        "records": records,
    }
    serialized_evidence = json.dumps(
        binding,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        **binding,
        "serializedEvidence": serialized_evidence,
        "serializedCharacterCount": len(serialized_evidence),
        "bundleHash": hashlib.sha256(serialized_evidence.encode("utf-8")).hexdigest(),
    }


def derive_synthesis_readiness(state: dict[str, Any] | None) -> dict[str, Any]:
    state = state if isinstance(state, dict) else {}
    plan_revision = str(state.get("planRevision") or "")
    ledger = state.get("sourceEvidence") if isinstance(state.get("sourceEvidence"), dict) else {}
    files_obj = ledger.get("files") if isinstance(ledger.get("files"), dict) else {}
    files = list(files_obj.values()) if str(ledger.get("planRevision") or "") == plan_revision else []
    files = [row for row in files if isinstance(row, dict)]
    evidence_identities = [_evidence_identity(row) for row in files]
    evidence_state_hash = _hash(evidence_identities)
    accepted_rows = [
        row
        for row in files
        if _coverage_level(row) in {"FILE_COMPLETE", "CLAIM_VALIDATED"}
        and row.get("truncated") is not True
        and bool(_coverage_ranges(row.get("coveredRanges")))
    ]
    accepted_ids = _strings([
        _hash(_evidence_identity(row))
        for row in accepted_rows
    ])
    declarations = [row for row in accepted_rows if str(row.get("sourceKind") or "") == "declaration"]
    implementations = [row for row in accepted_rows if str(row.get("sourceKind") or "") == "implementation"]
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
    policy_required_pairs = max(1, int(budget.get("representativePairs") or POLICY.get("defaultRepresentativePairs") or 1))
    discovered_paths = _strings([*(row.get("path") for row in files), *frontier], 64)
    discovered_decl = {
        _pairing_key(value)
        for value in discovered_paths
        if Path(value).suffix.casefold() in {".h", ".hpp", ".hh", ".inl"}
    }
    discovered_impl = {
        _pairing_key(value)
        for value in discovered_paths
        if Path(value).suffix.casefold() in {".cpp", ".c", ".cc", ".cxx"}
    }
    discovered_relevant_pairs = max(
        _nonnegative_int(progress.get("discoveredRelevantPairs")),
        len({key for key in discovered_decl if key and key in discovered_impl}),
    )
    required_pairs = (
        max(1, min(policy_required_pairs, max(pair_count, discovered_relevant_pairs)))
        if progress.get("discoveryStarted") is True
        else 1
    )
    max_reads = max(1, int(budget.get("maxDirectSourceReadsPerPhase") or 2**31 - 1))
    max_chars = max(1, int(budget.get("maxEvidenceCharsPerPhase") or 2**31 - 1))
    phase_read_calls = int(
        progress.get(
            "phaseDirectSourceReadCalls",
            progress.get("directSourceReadCalls", progress.get("directSourceReads") or 0),
        )
    )
    phase_evidence_characters = int(progress.get("phaseEvidenceCharacters", progress.get("evidenceCharacters") or 0))
    bound_reached = phase_read_calls >= max_reads or phase_evidence_characters >= max_chars
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
    partial_allowed = direct_satisfied and pair_count > 0 and bound_reached
    coverage_satisfied = not repo_open and (representative_satisfied or partial_allowed)
    coverage_incomplete = repo_open or not representative_satisfied
    recovery = state.get("recoveryObligation") if isinstance(state.get("recoveryObligation"), dict) else {}
    pending = str(recovery.get("status") or "").casefold() not in {
        "", "evidence_complete", "not_required"
    }
    mode_eligible = (
        str(state.get("mode") or "").casefold() == "read_only"
        and state.get("writesAllowed") is not True
        and not (state.get("writeGate") or {}).get("writesAllowed")
    )
    bundle = materialize_synthesis_evidence_bundle(state, accepted_rows)
    selected_paths = {
        str(row.get("sourcePath") or "").replace("\\", "/").casefold()
        for row in bundle["records"]
    }
    selected_declarations = {
        _pairing_key(row.get("sourcePath"))
        for row in bundle["records"]
        if Path(str(row.get("sourcePath") or "")).suffix.casefold() in {".h", ".hpp", ".hh", ".inl"}
    }
    selected_implementations = {
        _pairing_key(row.get("sourcePath"))
        for row in bundle["records"]
        if Path(str(row.get("sourcePath") or "")).suffix.casefold() in {".cpp", ".c", ".cc", ".cxx"}
    }
    selected_pair_count = len(
        {key for key in selected_declarations if key and key in selected_implementations}
    )
    materialized_required_pairs = (
        required_pairs if representative_satisfied else min(1, pair_count)
    )
    bundle_materialized = not direct_required or bool(
        selected_paths
        and len(bundle["records"]) >= int(POLICY.get("minimumAcceptedDirectEvidence") or 2)
        and selected_pair_count >= materialized_required_pairs
        and hashlib.sha256(bundle["serializedEvidence"].encode("utf-8")).hexdigest()
        == bundle["bundleHash"]
    )
    ready = mode_eligible and direct_satisfied and coverage_satisfied and not pending and bundle_materialized
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
    elif not bundle_materialized:
        reason = "synthesis_evidence_not_materialized"
    accepted_hash = evidence_state_hash
    frontier_hash = _hash(frontier)
    return {
        "version": 1, "ready": ready, "reason": reason,
        "acceptedDirectEvidenceCount": len(accepted_rows), "acceptedEvidenceIds": accepted_ids,
        "acceptedEvidenceIdsTruncated": len(accepted_rows) > len(accepted_ids),
        "acceptedEvidenceHash": accepted_hash, "evidenceStateHash": evidence_state_hash,
        "declarationCount": len(declarations),
        "implementationCount": len(implementations), "representativePairCount": pair_count,
        "requiredRepresentativePairs": required_pairs, "policyRepresentativePairs": policy_required_pairs,
        "discoveredRelevantPairs": discovered_relevant_pairs, "coverageMode": str(contract.get("coverageMode") or ""),
        "coverageSatisfied": coverage_satisfied, "coverageIncomplete": coverage_incomplete,
        "remainingFrontier": frontier, "remainingFrontierHash": frontier_hash,
        "pendingEvidenceObligation": pending, "sourceEvidencePlanRevision": str(ledger.get("planRevision") or ""),
        "planRevision": plan_revision, "controlEpoch": _nonnegative_int(state.get("controlEpoch")),
        "commitEligible": ready, "boundReached": bound_reached,
        "synthesisEvidenceMaterialized": bundle_materialized,
        "selectedSynthesisEvidenceCount": len(bundle["records"]),
        "selectedSynthesisRepresentativePairCount": selected_pair_count,
        "selectedSynthesisEvidencePaths": sorted(selected_paths),
        "claimLedger": {
            "version": 1,
            "claims": [
                {
                    key: row[key]
                    for key in (
                        "claimId", "sourcePath", "contentHash", "startLine", "endLine",
                        "exactExcerpt", "excerptDigest", "coverageLevel", "classification",
                    )
                }
                for row in bundle["records"]
            ],
        },
        "reportContract": synthesis_report_contract(coverage_incomplete),
        "synthesisEvidenceBundle": bundle,
        "synthesisEvidenceBundleHash": bundle["bundleHash"],
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
        and str(action.get("synthesisEvidenceBundleHash") or "")
        == str(readiness.get("synthesisEvidenceBundleHash") or "")
        and readiness["ready"] is True
    )
