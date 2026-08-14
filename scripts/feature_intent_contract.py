#!/usr/bin/env python
"""Deterministic feature-intent candidates and fail-closed selection contracts."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

FEATURE_INTENT_GATE = "unreal_feature_intent_resolve"
MIN_CANDIDATES = 3
MAX_CANDIDATES = 5
DIMENSIONS = (
    "ownershipLifetime",
    "authorityReplication",
    "persistence",
    "failureSemantics",
    "userVisibleBehavior",
    "nonGoals",
)
RISK_CLASS_DIMENSIONS = {
    "bounded_local": (
        "ownershipLifetime",
        "failureSemantics",
        "userVisibleBehavior",
        "nonGoals",
    ),
    "networked_runtime": (
        "ownershipLifetime",
        "authorityReplication",
        "failureSemantics",
        "userVisibleBehavior",
        "nonGoals",
    ),
    "persistent": (
        "ownershipLifetime",
        "persistence",
        "failureSemantics",
        "userVisibleBehavior",
        "nonGoals",
    ),
    "async_runtime": (
        "ownershipLifetime",
        "failureSemantics",
        "userVisibleBehavior",
        "nonGoals",
    ),
    "modular": (
        "ownershipLifetime",
        "failureSemantics",
        "nonGoals",
    ),
    "general": DIMENSIONS,
}


def classify_feature_risk(value: Any, request: str = "") -> str:
    explicit = _clean(value).casefold()
    aliases = {
        "bounded": "bounded_local",
        "local": "bounded_local",
        "networked": "networked_runtime",
        "network": "networked_runtime",
        "replicated": "networked_runtime",
        "persistence": "persistent",
        "async": "async_runtime",
        "module": "modular",
    }
    if explicit in RISK_CLASS_DIMENSIONS:
        return explicit
    if explicit in aliases:
        return aliases[explicit]
    text = _clean(request, limit=4000).casefold()
    if any(marker in text for marker in _DIMENSION_SIGNALS["persistence"]):
        return "persistent"
    if any(
        marker in text
        for marker in (
            "single player",
            "single-player",
            "local hotseat",
            "local 2-player",
            "local two-player",
            "no replication",
            "without replication",
        )
    ):
        return "bounded_local"
    if any(marker in text for marker in _DIMENSION_SIGNALS["authorityReplication"]):
        return "networked_runtime"
    if any(marker in text for marker in ("async", "timeout", "cancel", "비동기", "취소")):
        return "async_runtime"
    if any(marker in text for marker in ("module", "plugin", "interface", "모듈", "플러그인")):
        return "modular"
    return "bounded_local"


def required_dimensions_for_risk(risk_class: str, request: str = "") -> tuple[str, ...]:
    required = list(RISK_CLASS_DIMENSIONS.get(risk_class, DIMENSIONS))
    return tuple(dict.fromkeys(required))

_DIMENSION_SIGNALS = {
    "ownershipLifetime": (
        "owner", "ownership", "lifetime", "world", "gameinstance", "game instance",
        "localplayer", "local player", "engine", "subsystem", "actor", "component",
        "소유", "수명", "월드", "게임 인스턴스",
    ),
    "authorityReplication": (
        "authority", "server", "client", "replicate", "replication", "rpc", "onrep",
        "single player", "multiplayer", "권한", "서버", "클라이언트", "복제",
    ),
    "persistence": (
        "persist", "persistence", "save", "load", "config", "session", "transient",
        "저장", "로드", "영속", "세션",
    ),
    "failureSemantics": (
        "fail", "fallback", "retry", "timeout", "error", "rollback", "best effort",
        "실패", "오류", "재시도", "롤백",
    ),
    "userVisibleBehavior": (
        "ui", "hud", "widget", "message", "toast", "visible", "player", "input",
        "사용자", "플레이어", "표시", "화면",
    ),
    "nonGoals": (
        "non-goal", "non goal", "out of scope", "do not", "don't", "without",
        "제외", "하지 않", "범위 밖",
    ),
}

_VAGUE_SIGNALS = (
    "maybe", "somehow", "appropriate", "best", "proper", "etc", "or ",
    "알아서", "적당", "대충", "등등", "뭐", "아무거나",
)
_BROAD_SIGNALS = (
    "whole project", "entire project", "across modules", "multiple modules",
    "system-wide", "프로젝트 전체", "전체 구조", "여러 모듈",
)
_WRITE_SIGNALS = (
    "implement", "create", "add", "change", "modify", "fix", "refactor", "generate",
    "구현", "생성", "추가", "수정", "개선", "고쳐", "리팩터",
)

_COMPLETION_AUDIT_SIGNALS = (
    r"\b(?:current\s+implementation|implementation\s+status|earliest\s+incomplete|"
    r"first\s+incomplete|what\s+remains|first\s+unfinished)\b",
    r"현재\s*(?:구현\s*)?상태",
    r"구현\s*상태",
    r"(?:가장\s*)?(?:앞|이른|먼저)[^\n]{0,40}(?:미완성|미완료|완료되지\s*않)",
    r"아직\s*완료되지\s*않",
    r"미완(?:성|료)[^\n]{0,20}(?:기능|단계)",
)


def requires_feature_completion_audit(request: str) -> bool:
    """Return whether writes must be selected from a proven completion frontier.

    This is server-side policy, not a prompt hint.  A request to inspect the
    current implementation and complete the earliest missing behavior cannot
    safely bind Feature Intent until direct source evidence proves both the
    completed predecessors and one concrete functional gap.
    """

    source = _clean(request, limit=8000)
    return any(re.search(pattern, source, re.IGNORECASE) for pattern in _COMPLETION_AUDIT_SIGNALS)


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _clean(value: Any, *, limit: int = 600) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _intent_id(value: Any, fallback: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_-]+", "_", _clean(value).lower()).strip("_")
    return (cleaned or fallback)[:64]


def _criterion(
    criterion_id: str,
    statement: str,
    observer: str,
    oracle: str,
) -> dict[str, str]:
    return {
        "criterionId": criterion_id,
        "statement": statement,
        "observer": observer,
        "oracle": oracle,
    }


def _candidate_templates(request: str, count: int) -> list[dict[str, Any]]:
    request_summary = _clean(request, limit=180) or "the requested feature"
    templates = [
        {
            "intentId": "bounded_local",
            "riskClass": "bounded_local",
            "title": "Bounded local behavior",
            "summary": f"Implement {request_summary} in the nearest existing owner with no new global state.",
            "dimensions": {
                "ownershipLifetime": "Nearest existing Actor/Component owns state for its current lifetime.",
                "authorityReplication": "Existing authority policy is preserved; no new replication is introduced.",
                "persistence": "Transient only; no save/config migration.",
                "failureSemantics": "Fail closed and preserve the prior observable behavior.",
                "userVisibleBehavior": "Only the explicitly requested local behavior changes.",
                "nonGoals": "No new subsystem, cross-module API, persistence, or replication.",
            },
            "acceptanceCriteria": [
                _criterion(
                    "local_observer",
                    "The requested local behavior changes without altering unrelated instances.",
                    "focused automated test or deterministic local trace",
                    "requested case passes and the unchanged-control case remains identical",
                ),
                _criterion(
                    "build_observer",
                    "The bounded target remains build-valid.",
                    "UnrealBuildTool target build",
                    "build exits successfully with no new diagnostics in the touched target",
                ),
            ],
            "reversible": True,
            "boundedScope": True,
            "riskWeight": 1,
        },
        {
            "intentId": "authoritative_runtime",
            "riskClass": "networked_runtime",
            "title": "Authoritative runtime service",
            "summary": f"Implement {request_summary} behind one explicit runtime owner and authority boundary.",
            "dimensions": {
                "ownershipLifetime": "A World or GameInstance scoped service is the single runtime owner.",
                "authorityReplication": "Server authority owns mutations; clients receive explicit replicated observations only when required.",
                "persistence": "Runtime state is session-scoped unless a separate persistence decision is approved.",
                "failureSemantics": "Reject unauthorized mutation and emit a stable diagnostic/metric.",
                "userVisibleBehavior": "Players observe one consistent authoritative result.",
                "nonGoals": "No implicit client authority and no hidden process-global singleton.",
            },
            "acceptanceCriteria": [
                _criterion(
                    "authority_observer",
                    "Only the selected authority can commit the state transition.",
                    "server/client functional test with authority trace",
                    "server transition occurs once and unauthorized client mutation count is zero",
                ),
                _criterion(
                    "lifetime_observer",
                    "State is created and released at the selected runtime lifetime.",
                    "lifecycle automation test or deterministic init/deinit log",
                    "one initialization and one cleanup are observed per selected owner lifetime",
                ),
            ],
            "reversible": True,
            "boundedScope": False,
            "riskWeight": 2,
        },
        {
            "intentId": "persistent_contract",
            "riskClass": "persistent",
            "title": "Persistent user-facing contract",
            "summary": f"Implement {request_summary} as versioned persistent state with explicit migration and fallback.",
            "dimensions": {
                "ownershipLifetime": "A GameInstance/profile service owns the in-memory state and storage adapter.",
                "authorityReplication": "Persistence authority is local/profile scoped; network authority remains separate.",
                "persistence": "Versioned save/config schema with an explicit backward-compatible migration.",
                "failureSemantics": "Invalid or old data falls back safely without overwriting recoverable input.",
                "userVisibleBehavior": "The selected value survives restart and failure is visible but non-destructive.",
                "nonGoals": "No replication of storage records and no silent destructive migration.",
            },
            "acceptanceCriteria": [
                _criterion(
                    "restart_observer",
                    "The selected state survives a save/load boundary.",
                    "round-trip persistence integration test",
                    "loaded semantic value equals the saved value after process/session restart",
                ),
                _criterion(
                    "migration_observer",
                    "Legacy or invalid input follows the declared migration/fallback path.",
                    "versioned fixture test",
                    "legacy fixture migrates and invalid fixture preserves its source while returning the safe default",
                ),
            ],
            "reversible": False,
            "boundedScope": False,
            "riskWeight": 3,
        },
        {
            "intentId": "modular_extension",
            "riskClass": "modular",
            "title": "Modular extension boundary",
            "summary": f"Implement {request_summary} behind a narrow interface suitable for multiple modules.",
            "dimensions": {
                "ownershipLifetime": "A declared module service owns the implementation; consumers depend on an interface.",
                "authorityReplication": "Authority is expressed in the interface contract and implemented by one provider.",
                "persistence": "Storage is an optional adapter, not an implicit responsibility of consumers.",
                "failureSemantics": "Missing providers return an explicit unavailable result with no partial mutation.",
                "userVisibleBehavior": "Consumers observe the same contract regardless of provider implementation.",
                "nonGoals": "No reverse dependency, service locator, or undocumented global ownership.",
            },
            "acceptanceCriteria": [
                _criterion(
                    "dependency_observer",
                    "The dependency graph follows the declared interface direction.",
                    "module/include graph check",
                    "no reverse edge or dependency cycle is introduced",
                ),
                _criterion(
                    "provider_observer",
                    "Missing and present providers produce the declared outcomes.",
                    "provider contract test",
                    "present provider passes the behavior oracle and missing provider returns unavailable without mutation",
                ),
            ],
            "reversible": True,
            "boundedScope": False,
            "riskWeight": 2,
        },
        {
            "intentId": "async_resilient",
            "riskClass": "async_runtime",
            "title": "Asynchronous resilient workflow",
            "summary": f"Implement {request_summary} as a cancellable async operation with explicit timeout semantics.",
            "dimensions": {
                "ownershipLifetime": "The initiating owner holds a cancellable operation handle no longer than its lifetime.",
                "authorityReplication": "Only the authoritative initiator commits completion; late results are ignored.",
                "persistence": "No persistence unless completion is explicitly committed by the selected owner.",
                "failureSemantics": "Timeout, cancellation, retry exhaustion, and partial failure are distinct terminal outcomes.",
                "userVisibleBehavior": "Users see deterministic pending, success, failure, and cancellation states.",
                "nonGoals": "No unbounded retry, detached callback, or mutation after owner destruction.",
            },
            "acceptanceCriteria": [
                _criterion(
                    "terminal_observer",
                    "Every operation reaches exactly one declared terminal state.",
                    "async state-machine test with callback counter",
                    "terminal callback count equals one for success, timeout, cancellation, and failure fixtures",
                ),
                _criterion(
                    "lifetime_observer",
                    "Destroyed owners cannot receive or commit late results.",
                    "owner-destruction race test",
                    "post-destruction mutation and callback counts are zero",
                ),
            ],
            "reversible": True,
            "boundedScope": False,
            "riskWeight": 2,
        },
    ]
    return templates[: max(MIN_CANDIDATES, min(MAX_CANDIDATES, count))]


def analyze_feature_intent_ambiguity(
    request: str,
    *,
    write_intent: bool | None = None,
    reversible: bool | None = None,
    bounded_scope: bool | None = None,
) -> dict[str, Any]:
    """Classify intent ambiguity using explicit, stable arithmetic."""

    text = _clean(request, limit=4000).lower()
    if write_intent is None:
        write_intent = any(marker in text for marker in _WRITE_SIGNALS)
    present = {
        name: any(marker in text for marker in markers)
        for name, markers in _DIMENSION_SIGNALS.items()
    }
    risk_class = classify_feature_risk("", text)
    required_dimensions = required_dimensions_for_risk(risk_class, text)
    missing = [name for name in required_dimensions if not present[name]]
    explicit_exclusion_or = bool(
        re.search(r"\b(?:without|no)\b[^.;]{0,96}\bor\b", text)
    )
    vague_hits = sum(
        1
        for marker in _VAGUE_SIGNALS
        if (
            bool(re.search(r"\bor\b", text)) and not explicit_exclusion_or
            if marker.strip() == "or"
            else marker in text
        )
    )
    # A concrete implementation choice such as "WFeatureHUD or simple UI widget"
    # is not the same as an open-ended "maybe/best/proper" request. Keep the
    # choice in the ambiguity score, but do not let that one token make an
    # otherwise named, reversible target unbounded.
    hard_vague_hits = sum(
        1
        for marker in _VAGUE_SIGNALS
        if marker.strip() != "or" and marker in text
    )
    broad_hits = sum(1 for marker in _BROAD_SIGNALS if marker in text)
    has_concrete_target = bool(
        re.search(r"(?:Source|Plugins|Config)[/\\][^\s]+", request, re.IGNORECASE)
        or re.search(r"\b[UAFS][A-Z][A-Za-z0-9_]{2,}\b", request)
        or re.search(r"\b[A-Za-z0-9_]+\.(?:h|hpp|cpp|cc|cs)\b", request, re.IGNORECASE)
    )
    has_explicit_existing_owner = bool(
        re.search(
            r"\bexisting\s+[a-z0-9_ ]{0,48}\b(?:component|actor|subsystem|class|module)\b",
            text,
        )
        or re.search(
            r"(?:기존|현재)\s*[가-힣a-z0-9_ ]{0,32}(?:컴포넌트|액터|서브시스템|클래스|모듈)",
            text,
        )
    )
    # A feature can be bounded by an explicit local runtime boundary and
    # concrete observable behavior even when a small model summarizes away the
    # original C++ class/file names. This is common for detailed game-feature
    # prompts (local hotseat, exact board/timer values, win/restart/timeout
    # behavior). Do not turn those into an unrelated replication/persistence
    # architecture choice merely because the tool-call summary lacks a path.
    has_explicit_local_boundary = any(
        marker in text
        for marker in (
            "local hotseat",
            "local 2-player",
            "local two-player",
            "single player",
            "single-player",
            "one pc",
            "한 pc",
            "로컬 2인",
            "로컬 2명",
        )
    )
    observable_detail_hits = sum(
        1
        for marker in (
            "mouse",
            "input",
            "click",
            "win",
            "restart",
            "timeout",
            "auto-place",
            "turn",
            "board",
            "timer",
            "초",
            "클릭",
            "승리",
            "재시작",
            "시간 초과",
            "턴",
            "보드",
        )
        if marker in text
    )
    has_bounded_local_behavior = bool(
        has_explicit_local_boundary and observable_detail_hits >= 3
    )
    points = 10
    points += min(20, vague_hits * 10)
    points += min(20, broad_hits * 10)
    points += min(30, len(missing) * 5)
    if write_intent and not has_concrete_target:
        points += 15
    if len(text.split()) < 8:
        points += 10
    points -= sum(3 for value in present.values() if value)
    score = round(max(0, min(100, points)) / 100.0, 2)
    if write_intent and broad_hits and vague_hits and not has_concrete_target:
        score = max(score, 0.75)

    inferred_reversible = bool(reversible) if reversible is not None else not any(
        marker in text for marker in ("migration", "delete", "rename", "schema", "마이그레이션", "삭제")
    )
    inferred_bounded = bool(bounded_scope) if bounded_scope is not None else (
        (
            has_concrete_target
            or has_explicit_existing_owner
            or has_bounded_local_behavior
        )
        and broad_hits == 0
        and hard_vague_hits == 0
    )
    if score < 0.45 and inferred_reversible and inferred_bounded:
        action = "bounded_assumption"
    elif score >= 0.70:
        action = "user_approval"
    else:
        action = "resolve_before_write"

    questions: list[str] = []
    question_by_dimension = {
        "ownershipLifetime": "Which object/module owns the state, and for what lifetime?",
        "authorityReplication": "Which side is authoritative, and what must replicate?",
        "persistence": "Must the state survive map/session/process restart?",
        "failureSemantics": "What observable result is required on failure, timeout, or partial completion?",
        "userVisibleBehavior": "Which exact user-visible behavior distinguishes success from failure?",
        "nonGoals": "Which adjacent behavior is explicitly out of scope?",
    }
    if action != "bounded_assumption":
        questions = [question_by_dimension[name] for name in missing[:3]]

    return {
        "ambiguityScore": score,
        "scorePoints": int(score * 100),
        "writeIntent": bool(write_intent),
        "reversible": inferred_reversible,
        "boundedScope": inferred_bounded,
        "riskClass": risk_class,
        "requiredDimensions": list(required_dimensions),
        "missingDimensions": missing,
        "recommendedAction": action,
        "requiresResolution": bool(write_intent and action != "bounded_assumption"),
        "blockingQuestions": questions,
    }


def _normalize_criteria(raw: Any, candidate_id: str) -> tuple[list[dict[str, str]], list[str]]:
    criteria: list[dict[str, str]] = []
    issues: list[str] = []
    if not isinstance(raw, list):
        return [], [f"{candidate_id}: acceptanceCriteria must be an array"]
    for index, item in enumerate(raw[:12]):
        if not isinstance(item, dict):
            issues.append(f"{candidate_id}: acceptance criterion {index + 1} must be an object")
            continue
        normalized = {
            "criterionId": _intent_id(
                item.get("criterionId") or item.get("id"),
                f"criterion_{index + 1}",
            ),
            "statement": _clean(item.get("statement") or item.get("criterion")),
            "observer": _clean(item.get("observer")),
            "oracle": _clean(item.get("oracle")),
        }
        missing = [key for key in ("statement", "observer", "oracle") if not normalized[key]]
        if missing:
            issues.append(
                f"{candidate_id}: criterion {index + 1} missing {', '.join(missing)}"
            )
        else:
            criteria.append(normalized)
    if not criteria:
        issues.append(f"{candidate_id}: at least one complete acceptance criterion is required")
    return criteria, issues


def _normalize_candidate(raw: Any, index: int, request: str) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    candidate_id = _intent_id(
        source.get("intentId") or source.get("id"),
        f"candidate_{index + 1}",
    )
    dimensions_source = source.get("dimensions")
    dimensions_source = dimensions_source if isinstance(dimensions_source, dict) else {}
    dimensions = {
        name: _clean(dimensions_source.get(name) or source.get(name))
        for name in DIMENSIONS
    }
    risk_class = classify_feature_risk(
        source.get("riskClass"),
        " ".join(
            (
                request,
                str(source.get("intentId") or ""),
                str(source.get("title") or ""),
                str(source.get("summary") or ""),
            )
        ),
    )
    required_dimensions = required_dimensions_for_risk(risk_class, request)
    criteria, issues = _normalize_criteria(source.get("acceptanceCriteria"), candidate_id)
    missing_dimensions = [name for name in required_dimensions if not dimensions.get(name)]
    if missing_dimensions:
        issues.append(f"{candidate_id}: missing dimensions {', '.join(missing_dimensions)}")

    request_terms = {
        token for token in re.findall(r"[a-z0-9_]{4,}", request.lower())
        if token not in {"this", "that", "with", "from", "into", "feature"}
    }
    candidate_text = " ".join(
        [_clean(source.get("title")), _clean(source.get("summary")), *dimensions.values()]
    ).lower()
    alignment_hits = len({token for token in request_terms if token in candidate_text})
    completeness = (
        sum(8 for name in required_dimensions if dimensions.get(name))
        + min(12, len(criteria) * 6)
    )
    alignment = min(20, alignment_hits * 4)
    request_risk = classify_feature_risk("", request)
    risk_alignment = 12 if risk_class == request_risk else 0
    reversible = source.get("reversible") is True
    bounded = source.get("boundedScope") is True
    safety = (6 if reversible else 0) + (4 if bounded else 0)
    risk_weight = max(0, min(5, int(source.get("riskWeight") or 0)))
    specificity = min(10, max(0, len(candidate_text.split()) // 12))
    score = (
        completeness
        + alignment
        + risk_alignment
        + safety
        + specificity
        - risk_weight
    )
    normalized = {
        "intentId": candidate_id,
        "title": _clean(source.get("title")) or f"Candidate {index + 1}",
        "summary": _clean(source.get("summary")) or _clean(request, limit=180),
        "dimensions": dimensions,
        "riskClass": risk_class,
        "requiredDimensions": list(required_dimensions),
        "acceptanceCriteria": criteria,
        "reversible": reversible,
        "boundedScope": bounded,
        "score": int(score),
        "scoreBreakdown": {
            "completeness": completeness,
            "requestAlignment": alignment,
            "riskClassAlignment": risk_alignment,
            "safety": safety,
            "specificity": specificity,
            "riskPenalty": risk_weight,
        },
        "eligible": not issues,
        "issues": issues,
    }
    return normalized


def _compact_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "intentId": item["intentId"],
            "title": item["title"],
            "summary": item["summary"],
            "score": item["score"],
            "eligible": item["eligible"],
            "acceptanceCriterionCount": len(item["acceptanceCriteria"]),
            "issues": list(item["issues"])[:3],
            "riskClass": item["riskClass"],
            "requiredDimensions": list(item["requiredDimensions"]),
        }
        for item in candidates
    ]


def resolve_feature_intent(
    request: str,
    *,
    candidates: list[dict[str, Any]] | None = None,
    selected_intent_id: str = "",
    selection_rationale: str = "",
    blocking_question_answers: dict[str, Any] | None = None,
    user_approved: bool = False,
    write_intent: bool | None = None,
    reversible: bool | None = None,
    bounded_scope: bool | None = None,
    candidate_count: int = 3,
    include_full: bool = False,
) -> dict[str, Any]:
    """Normalize, rank, and select one feature intent without model-memory trust."""

    raw_candidates = candidates
    if raw_candidates is None:
        raw_candidates = _candidate_templates(request, candidate_count)
    if not MIN_CANDIDATES <= len(raw_candidates) <= MAX_CANDIDATES:
        return {
            "ok": False,
            "errorCode": "FEATURE_INTENT_CANDIDATE_COUNT",
            "error": f"Feature intent requires {MIN_CANDIDATES}-{MAX_CANDIDATES} candidates.",
        }

    normalized = [
        _normalize_candidate(item, index, request)
        for index, item in enumerate(raw_candidates)
    ]
    ids = [item["intentId"] for item in normalized]
    if len(ids) != len(set(ids)):
        return {
            "ok": False,
            "errorCode": "FEATURE_INTENT_DUPLICATE_ID",
            "error": "Feature intent candidate IDs must be unique.",
        }
    normalized.sort(key=lambda item: (-int(item["score"]), str(item["intentId"])))
    eligible = [item for item in normalized if item["eligible"]]
    if len(eligible) < 2:
        return {
            "ok": False,
            "errorCode": "FEATURE_INTENT_INSUFFICIENT_ELIGIBLE",
            "error": "At least two complete feature intent candidates are required.",
            "candidateCount": len(normalized),
            "eligibleCandidateCount": len(eligible),
            "candidates": _compact_candidates(normalized),
        }

    ambiguity = analyze_feature_intent_ambiguity(
        request,
        write_intent=write_intent,
        reversible=reversible,
        bounded_scope=bounded_scope,
    )
    answers = {
        _clean(key, limit=300): _clean(value, limit=800)
        for key, value in dict(blocking_question_answers or {}).items()
        if _clean(key) and _clean(value)
    }
    missing_for_questions = list(ambiguity.get("missingDimensions") or [])[:3]
    unresolved_questions = [
        question
        for index, question in enumerate(ambiguity["blockingQuestions"])
        if not answers.get(question)
        and not answers.get(
            str(missing_for_questions[index])
            if index < len(missing_for_questions)
            else ""
        )
    ]
    top_score = int(eligible[0]["score"])
    top_ids = [item["intentId"] for item in eligible if int(item["score"]) == top_score]
    explicit_id = _intent_id(selected_intent_id, "") if _clean(selected_intent_id) else ""
    selected = next(
        (item for item in eligible if item["intentId"] == explicit_id),
        None,
    )

    error_code = ""
    error = ""
    requires_explicit = bool(ambiguity["requiresResolution"])
    if explicit_id and selected is None:
        error_code = "FEATURE_INTENT_SELECTION_INVALID"
        error = "selectedIntentId must identify an eligible candidate."
    elif len(top_ids) > 1 and (selected is None or not _clean(selection_rationale)):
        error_code = "FEATURE_INTENT_TIE_REQUIRES_SELECTION"
        error = "Top-scoring candidates are tied; explicit selection and rationale are required."
    elif requires_explicit and (selected is None or not _clean(selection_rationale)):
        error_code = "FEATURE_INTENT_SELECTION_REQUIRED"
        error = "Ambiguous writes require explicit selectedIntentId and selectionRationale."
    elif unresolved_questions:
        error_code = "FEATURE_INTENT_BLOCKING_QUESTIONS"
        error = "Blocking feature-intent questions require explicit answers."
    elif ambiguity["recommendedAction"] == "user_approval" and not user_approved:
        error_code = "FEATURE_INTENT_USER_APPROVAL_REQUIRED"
        error = "High-ambiguity intent remains plan-only until explicit user approval."
    elif selected is None:
        selected = eligible[0]

    contract_body = {
        "version": 1,
        "requestHash": _canonical_hash(_clean(request, limit=8000)),
        "ambiguity": ambiguity,
        "candidateCount": len(normalized),
        "candidates": [
            {
                key: item[key]
                for key in (
                    "intentId",
                    "title",
                    "summary",
                    "dimensions",
                    "acceptanceCriteria",
                    "reversible",
                    "boundedScope",
                    "score",
                    "scoreBreakdown",
                    "eligible",
                    "issues",
                )
            }
            for item in normalized
        ],
        "selectedIntentId": selected["intentId"] if selected else "",
        "selectionRationale": _clean(selection_rationale, limit=1200),
        "blockingQuestionAnswers": answers,
    }
    contract_hash = _canonical_hash(contract_body)
    selected_oracle_hash = _canonical_hash(
        selected["acceptanceCriteria"] if selected else []
    )
    result = {
        "ok": not error_code,
        "status": "resolved" if not error_code else "blocked",
        "errorCode": error_code,
        "error": error,
        "ambiguity": ambiguity,
        "candidateCount": len(normalized),
        "eligibleCandidateCount": len(eligible),
        "candidates": _compact_candidates(normalized),
        "topCandidateIds": top_ids,
        "selectedIntentId": selected["intentId"] if selected else "",
        "selectedIntentSummary": (
            {
                "intentId": selected["intentId"],
                "title": selected["title"],
                "summary": selected["summary"],
                "acceptanceCriterionCount": len(selected["acceptanceCriteria"]),
            }
            if selected
            else {}
        ),
        "intentContractHash": contract_hash,
        "acceptanceOracleHash": selected_oracle_hash,
        "blockingQuestions": unresolved_questions,
        "requiresUserApproval": ambiguity["recommendedAction"] == "user_approval",
        "writeGate": {
            "writesAllowed": not error_code,
            "requiredGate": FEATURE_INTENT_GATE if ambiguity["requiresResolution"] else "",
            "reason": error or "feature intent is resolved and acceptance oracles are explicit",
        },
    }
    if include_full:
        result["contract"] = contract_body
        result["selectedCandidate"] = selected or {}
    return result


def resolve_architecture_bound_feature_intent(
    request: str,
    *,
    architecture_contract: dict[str, Any],
    target_files: list[str],
    include_full: bool = False,
) -> dict[str, Any]:
    """Resolve one local feature from a server-validated architecture contract.

    The architecture gate has already selected ownership, scope, invariants,
    files, and validation obligations. Reasking the model to select among the
    generic feature templates loses that evidence and adds a redundant round
    trip. This adapter preserves the normal 3-candidate audit shape while
    making the validated contract the explicit selected candidate.
    """

    contract = architecture_contract if isinstance(architecture_contract, dict) else {}
    scope = contract.get("scope") if isinstance(contract.get("scope"), dict) else {}
    ownership = (
        contract.get("ownership")
        if isinstance(contract.get("ownership"), dict)
        else {}
    )
    decision = _clean(contract.get("decision") or request, limit=1200)
    non_goals_value = scope.get("nonGoals")
    non_goals = (
        "; ".join(_clean(item, limit=300) for item in non_goals_value if _clean(item))
        if isinstance(non_goals_value, list)
        else _clean(non_goals_value, limit=800)
    )
    dimensions = {
        "ownershipLifetime": _clean(
            "; ".join(
                value
                for value in (
                    str(ownership.get("stateOwner") or ""),
                    str(ownership.get("dataOwner") or ""),
                    str(ownership.get("lifecycleOwner") or ""),
                )
                if value.strip()
            )
            or "Use the owner and lifetime selected by the validated architecture.",
            limit=1000,
        ),
        "authorityReplication": (
            f"Validated local scope: networked={scope.get('networked')!s}; "
            f"runtime={_clean(scope.get('runtime'), limit=120) or 'standalone'}."
        ),
        "persistence": (
            "No persistence or migration is introduced by this validated local contract."
        ),
        "failureSemantics": _clean(
            "; ".join(
                value
                for value in (
                    str(ownership.get("failurePolicy") or ""),
                    str(ownership.get("recoveryPolicy") or ""),
                )
                if value.strip()
            )
            or "Fail closed and preserve the prior observable state.",
            limit=1000,
        ),
        "userVisibleBehavior": decision,
        "nonGoals": non_goals or "Preserve behavior outside the validated architecture scope.",
    }
    validation_plan = [
        _clean(item, limit=500)
        for item in (contract.get("validationPlan") or [])
        if _clean(item)
    ]
    if not validation_plan:
        validation_plan = ["targeted build and regression validation"]
    criteria = [
        _criterion(
            f"architecture_check_{index + 1}",
            f"The validated architecture decision remains true for {decision}.",
            check,
            (
                f"{check} completes successfully for the exact selected slice "
                "with no regression outside its declared files"
            ),
        )
        for index, check in enumerate(validation_plan[:4])
    ]
    selected = {
        "intentId": "architecture_bound_local",
        "riskClass": "bounded_local",
        "title": "Validated local architecture contract",
        "summary": decision,
        "dimensions": dimensions,
        "acceptanceCriteria": criteria,
        "reversible": True,
        "boundedScope": True,
        "riskWeight": 1,
    }
    generic_alternatives = _candidate_templates(request, 3)
    candidates = [selected, *generic_alternatives[:2]]
    answers = {name: value for name, value in dimensions.items()}
    result = resolve_feature_intent(
        request,
        candidates=candidates,
        selected_intent_id="architecture_bound_local",
        selection_rationale=(
            "Server selected the already-validated local architecture contract; "
            "the model is not asked to recreate its ownership or slice decision."
        ),
        blocking_question_answers=answers,
        user_approved=True,
        write_intent=True,
        reversible=True,
        bounded_scope=bool(1 <= len(target_files) <= 2),
        candidate_count=3,
        include_full=include_full,
    )
    result["architectureBound"] = {
        "serverOwned": True,
        "targetFiles": list(target_files),
        "validationLevel": str(scope.get("validationLevel") or ""),
    }
    return result


def can_auto_bind_architecture_feature_intent(
    *,
    slice_provenance: dict[str, Any],
    target_files: list[str],
    snapshot_issues: list[str],
    explicit_semantic_input: bool,
) -> bool:
    """Return whether validated architecture evidence may replace model selection.

    Only a reversible, bounded, non-networked Draft/Bound decision is eligible.
    Strict, high-risk, persistence/migration, and multiplayer decisions must keep
    the normal explicit Feature Intent resolution path.
    """

    provenance = slice_provenance if isinstance(slice_provenance, dict) else {}
    contract = (
        provenance.get("featureIntentContract")
        if isinstance(provenance.get("featureIntentContract"), dict)
        else {}
    )
    scope = contract.get("scope") if isinstance(contract.get("scope"), dict) else {}
    return bool(
        provenance.get("source") == "validated_architecture"
        and contract
        and scope.get("networked") is False
        and str(scope.get("runtime") or "").strip().casefold()
        in {"local_hotseat", "standalone", "editor"}
        and str(scope.get("validationLevel") or "Draft").strip().casefold()
        in {"draft", "bound"}
        and str(scope.get("risk") or "low").strip().casefold() != "high"
        and contract.get("hasMigrationPlan") is not True
        and 1 <= len(target_files) <= 2
        and not snapshot_issues
        and not explicit_semantic_input
    )


def target_snapshot_hash(target_snapshots: list[dict[str, Any]]) -> str:
    normalized = [
        {
            "path": _clean(item.get("path"), limit=1200),
            "absolutePath": _clean(item.get("absolutePath"), limit=2000),
            "exists": bool(item.get("exists")),
            "fileHash": _clean(item.get("fileHash"), limit=128),
        }
        for item in target_snapshots
        if isinstance(item, dict)
    ]
    normalized.sort(key=lambda item: (item["absolutePath"], item["path"]))
    return _canonical_hash(normalized)
