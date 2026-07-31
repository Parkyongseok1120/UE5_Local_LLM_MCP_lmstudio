#!/usr/bin/env python
"""Deterministic architecture candidate generation and comparison.

The portfolio is a planning aid, not a design proof. Generated candidates stay
blocked for implementation until a source-backed proposal supplies ownership,
invariants, slices, migration, and validation evidence.
"""

from __future__ import annotations

from typing import Any

SCORE_FIELDS = ("complexity", "maintainability", "performance", "risk")
SCORE_WEIGHTS = {
    "complexity": 0.20,
    "maintainability": 0.35,
    "performance": 0.15,
    "risk": 0.30,
}
MINIMUM_SELECTION_MARGIN = 4.0


def _clean_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(
        dict.fromkeys(
            str(item).strip()
            for item in value
            if isinstance(item, str) and str(item).strip()
        )
    )


def _owner_candidates(analysis: dict[str, Any]) -> list[str]:
    owners: list[str] = []
    for row in (analysis.get("stateTransitions") or {}).get(
        "stateOwnershipCandidates", []
    ):
        if isinstance(row, dict):
            owners.append(str(row.get("ownerCandidate") or "").strip())
    for row in (analysis.get("lifecycle") or {}).get("callbacks", []):
        if isinstance(row, dict):
            owners.append(str(row.get("ownerCandidate") or "").strip())
    for row in (analysis.get("topology") or {}).get("owners", []):
        if isinstance(row, dict):
            owners.append(str(row.get("id") or "").strip())
    return list(dict.fromkeys(owner for owner in owners if owner))


def _risk_signals(analysis: dict[str, Any]) -> list[str]:
    signals: list[str] = []
    topology = analysis.get("topology") or {}
    state = analysis.get("stateTransitions") or {}
    lifecycle = analysis.get("lifecycle") or {}
    graph = analysis.get("graphEvidence") or {}
    if topology.get("sourceDependencyCycles"):
        signals.append("source_dependency_cycle")
    if int(state.get("multipleWriterCandidateCount") or 0) > 0:
        signals.append("multiple_state_writers")
    if lifecycle.get("pairingGaps"):
        signals.append("lifecycle_pairing_gap")
    if graph.get("complete") is False:
        signals.append("incomplete_source_graph")
    if (analysis.get("focus") or {}).get("unmatchedSymbols"):
        signals.append("unmatched_focus_symbol")
    return signals


def _candidate(
    *,
    name: str,
    strategy: str,
    owner: str,
    rationale: str,
    scores: dict[str, int],
    risks: list[str],
    required_evidence: list[str],
    migration_shape: list[str],
) -> dict[str, Any]:
    return {
        "name": name,
        "strategy": strategy,
        "ownerCandidate": owner,
        "rationale": rationale,
        "scores": scores,
        "riskSignals": risks,
        "requiredEvidence": required_evidence,
        "migrationShape": migration_shape,
        "implementationReady": False,
        "proofLevel": "Proposed",
    }


def generate_architecture_portfolio(
    analysis: dict[str, Any],
    *,
    objective: str = "",
    constraints: list[str] | None = None,
) -> dict[str, Any]:
    """Generate bounded strategy candidates from current source inventory."""
    owners = _owner_candidates(analysis)
    primary_owner = owners[0] if owners else "owner_requires_source_evidence"
    risks = _risk_signals(analysis)
    constraints_clean = _clean_strings(constraints or [])
    base_evidence = [
        "read state/data/lifecycle owner definitions",
        "verify callers and consumers",
        "verify module dependency direction",
        "define failure and recovery behavior",
        "build and targeted regression evidence",
    ]
    candidates = [
        _candidate(
            name="extend-existing-owner",
            strategy="extend_existing_owner",
            owner=primary_owner,
            rationale=(
                "Add the capability to the strongest existing owner candidate and "
                "preserve current boundaries."
            ),
            scores={
                "complexity": 2,
                "maintainability": 4,
                "performance": 4,
                "risk": 2 + int("multiple_state_writers" in risks),
            },
            risks=risks,
            required_evidence=base_evidence,
            migration_shape=[
                "add compatible API to the existing owner",
                "move callsites in bounded slices",
                "remove compatibility surface after verification",
            ],
        ),
        _candidate(
            name="boundary-adapter",
            strategy="introduce_boundary_adapter",
            owner=primary_owner,
            rationale=(
                "Keep current owners and place an adapter at the dependency boundary "
                "to stage migration without reversing module direction."
            ),
            scores={
                "complexity": 3,
                "maintainability": 5,
                "performance": 3,
                "risk": 2 + int("source_dependency_cycle" in risks),
            },
            risks=risks,
            required_evidence=[
                *base_evidence,
                "prove adapter ownership and removal condition",
            ],
            migration_shape=[
                "introduce a compatibility adapter",
                "migrate consumers behind the adapter",
                "delete the adapter only after asset/runtime validation",
            ],
        ),
        _candidate(
            name="extract-dedicated-owner",
            strategy="extract_dedicated_owner",
            owner="new_owner_requires_approval",
            rationale=(
                "Create a dedicated owner only when existing ownership would violate "
                "lifecycle, dependency, or state invariants."
            ),
            scores={
                "complexity": 5,
                "maintainability": 4,
                "performance": 3,
                "risk": 4 + int("source_dependency_cycle" in risks),
            },
            risks=risks,
            required_evidence=[
                *base_evidence,
                "prove no existing owner can accept the capability",
                "approve new lifecycle and serialization ownership",
            ],
            migration_shape=[
                "add the new owner without switching consumers",
                "dual-read or adapt old ownership during migration",
                "switch bounded consumers and validate rollback",
                "remove the old ownership path",
            ],
        ),
    ]
    for candidate in candidates:
        candidate["scores"]["risk"] = min(5, candidate["scores"]["risk"])
    return {
        "version": 1,
        "objective": str(objective or "").strip(),
        "constraints": constraints_clean,
        "existingOwnerCandidates": owners,
        "riskSignals": risks,
        "candidates": candidates,
        "candidateCount": len(candidates),
        "implementationReady": False,
        "nextAction": "score_source_backed_alternatives_and_select",
        "proofBoundary": (
            "Generated candidates are bounded strategy templates derived from source "
            "inventory. They do not prove semantic fitness or authorize writes."
        ),
    }


def _normalized_scores(value: Any) -> tuple[dict[str, float], list[str]]:
    scores = value if isinstance(value, dict) else {}
    normalized: dict[str, float] = {}
    issues: list[str] = []
    for field in SCORE_FIELDS:
        score = scores.get(field)
        if (
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not 1 <= float(score) <= 5
        ):
            issues.append(f"scores.{field} must be a number from 1 to 5")
            continue
        normalized[field] = float(score)
    return normalized, issues


def _utility(scores: dict[str, float]) -> float:
    normalized = {
        "complexity": (6.0 - scores["complexity"]) / 5.0,
        "maintainability": scores["maintainability"] / 5.0,
        "performance": scores["performance"] / 5.0,
        "risk": (6.0 - scores["risk"]) / 5.0,
    }
    return round(
        sum(normalized[field] * SCORE_WEIGHTS[field] for field in SCORE_FIELDS)
        * 100,
        2,
    )


def compare_architecture_alternatives(
    alternatives: Any,
    *,
    selected_alternative: str = "",
    selection_rationale: str = "",
) -> dict[str, Any]:
    """Compare scored alternatives without treating the score as correctness proof."""
    rows: list[dict[str, Any]] = []
    raw = alternatives if isinstance(alternatives, list) else []
    for index, item in enumerate(raw):
        if isinstance(item, str):
            rows.append(
                {
                    "index": index,
                    "name": item.strip(),
                    "eligible": False,
                    "issues": ["structured scores are required for comparison"],
                    "utilityScore": None,
                }
            )
            continue
        if not isinstance(item, dict):
            rows.append(
                {
                    "index": index,
                    "name": "",
                    "eligible": False,
                    "issues": ["alternative must be a string or object"],
                    "utilityScore": None,
                }
            )
            continue
        name = str(item.get("name") or "").strip()
        scores, issues = _normalized_scores(item.get("scores"))
        if not name:
            issues.append("name is required")
        rows.append(
            {
                "index": index,
                "name": name,
                "eligible": not issues,
                "issues": issues,
                "scores": scores,
                "utilityScore": _utility(scores) if not issues else None,
                "rationale": str(item.get("rationale") or "").strip(),
            }
        )

    ranked = sorted(
        (row for row in rows if row["eligible"]),
        key=lambda row: (-float(row["utilityScore"]), str(row["name"]).lower()),
    )
    recommended = str(ranked[0]["name"]) if ranked else ""
    margin = (
        round(float(ranked[0]["utilityScore"]) - float(ranked[1]["utilityScore"]), 2)
        if len(ranked) >= 2
        else 0.0
    )
    ambiguous = len(ranked) >= 2 and margin < MINIMUM_SELECTION_MARGIN
    selected = str(selected_alternative or "").strip()
    selected_row = next((row for row in ranked if row["name"] == selected), None)
    selection_issues: list[str] = []
    if len(ranked) < 2:
        selection_issues.append("at least two fully scored alternatives are required")
    if not selected:
        selection_issues.append("selectedAlternative is required")
    elif selected_row is None:
        selection_issues.append("selectedAlternative must name an eligible alternative")
    if selected and recommended and selected != recommended and not str(
        selection_rationale or ""
    ).strip():
        selection_issues.append(
            "selectionRationale is required when overriding the recommended alternative"
        )
    if ambiguous and not str(selection_rationale or "").strip():
        selection_issues.append(
            "selectionRationale is required when candidate scores are ambiguous"
        )
    return {
        "version": 1,
        "alternatives": rows,
        "eligibleCount": len(ranked),
        "ranking": [row["name"] for row in ranked],
        "recommendedAlternative": recommended,
        "selectedAlternative": selected,
        "selectionMargin": margin,
        "minimumSelectionMargin": MINIMUM_SELECTION_MARGIN,
        "ambiguous": ambiguous,
        "selectionValid": not selection_issues,
        "selectionIssues": selection_issues,
        "proofBoundary": (
            "The utility score compares declared tradeoffs only. Source, build, test, "
            "runtime, migration, and asset evidence remain mandatory."
        ),
    }
