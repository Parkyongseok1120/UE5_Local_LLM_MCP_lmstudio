#!/usr/bin/env python
"""Deterministic, evidence-bounded Unreal symbol target resolution."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Literal


_CAMEL_BOUNDARY_RE = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])"
)
_IDENTIFIER_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_UNREAL_PREFIXES = frozenset({"a", "u", "f", "i", "s", "t"})
_TYPE_TOKENS = frozenset(
    {
        "c",
        "cpp",
        "cplusplus",
        "class",
        "struct",
        "interface",
        "enum",
        "클래스",
        "구조체",
        "인터페이스",
        "언리얼",
        "unreal",
        "ue",
    }
)
_COMPOUND_TOKEN_ALIASES = {
    "animinstance": ("anim", "instance"),
    "animationinstance": ("animation", "instance"),
}
_LOCATOR_SUFFIX_RE = re.compile(r":\d+(?::\d+)?$")


def _nfkc(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def _compact(value: Any) -> str:
    return re.sub(r"[^\w]+", "", _nfkc(value), flags=re.UNICODE).casefold()


def _locator_file_path(value: Any) -> str:
    """Remove only a trailing source locator, preserving Windows drive colons."""

    return _LOCATOR_SUFFIX_RE.sub("", str(value or "").strip())


def _source_file_identity(value: Any) -> str:
    """Group declaration/definition rows without merging cross-module symbols."""

    normalized = unicodedata.normalize(
        "NFKC", _locator_file_path(value).replace("\\", "/")
    ).casefold()
    return re.sub(r"\.(?:h|hh|hpp|hxx|c|cc|cpp|cxx|inl)$", "", normalized)


def target_tokens(value: Any, *, strip_unreal_prefix: bool = True) -> list[str]:
    """Tokenize separators/CamelCase and bounded Unreal compound aliases."""

    normalized = _nfkc(value).replace("C++", " cpp ").replace("c++", " cpp ")
    raw_words: list[str] = []
    for identifier in _IDENTIFIER_RE.findall(normalized):
        pieces = [piece for piece in re.split(r"[_\-\s]+", identifier) if piece]
        for piece in pieces:
            # Unreal's one-letter type prefix is attached before CamelCase is
            # split.  Acronym-led names such as UHTTPServer otherwise become
            # ``uhttp``/``server`` and cannot share the raw ``HTTP`` token.
            # Require an uppercase second character so ordinary words such as
            # UserSettings are not truncated to serSettings.
            if (
                strip_unreal_prefix
                and len(piece) > 2
                and piece[0].casefold() in _UNREAL_PREFIXES
                and piece[1].isupper()
            ):
                piece = piece[1:]
            raw_words.extend(part for part in _CAMEL_BOUNDARY_RE.split(piece) if part)
    # Preserve non-ASCII type words only to filter the explicit Korean tokens.
    raw_words.extend(re.findall(r"[가-힣]+", normalized))

    tokens: list[str] = []
    for raw in raw_words:
        token = _nfkc(raw).casefold()
        if token in _TYPE_TOKENS:
            continue
        expanded = _COMPOUND_TOKEN_ALIASES.get(token)
        if expanded:
            tokens.extend(expanded)
        elif token:
            tokens.append(token)
    if strip_unreal_prefix and tokens:
        first = tokens[0]
        # A detached one-letter type prefix may remain after separator
        # tokenization ("U HTTPServer"). Two-letter domain terms such as UI
        # and AI are real target evidence and must never be discarded.
        if first in _UNREAL_PREFIXES:
            tokens = tokens[1:]
    return list(dict.fromkeys(token for token in tokens if len(token) > 1))


def _candidate_fields(candidate: dict[str, Any]) -> dict[str, str]:
    symbol = str(
        candidate.get("symbol_name")
        or candidate.get("symbolName")
        or candidate.get("name")
        or ""
    ).strip()
    qualified = str(
        candidate.get("qualified_name")
        or candidate.get("qualifiedName")
        or symbol
    ).strip()
    file_path = str(
        candidate.get("file_path")
        or candidate.get("filePath")
        or candidate.get("relative_path")
        or candidate.get("relativePath")
        or candidate.get("locator")
        or candidate.get("title")
        or ""
    ).strip()
    stem = Path(_locator_file_path(file_path)).stem if file_path else ""
    return {
        "symbol": symbol,
        "qualified": qualified,
        "filePath": file_path,
        "fileStem": stem,
    }


def _existing_candidate_file(file_path: str, project_root: Path | None) -> bool:
    if not file_path:
        return False
    raw = _locator_file_path(file_path)
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute() and project_root is not None:
        candidate = project_root / candidate
    try:
        resolved = candidate.resolve()
        if project_root is not None:
            resolved.relative_to(project_root.resolve())
        return resolved.is_file()
    except (OSError, ValueError):
        return False


def _score_candidate(
    raw_target: str,
    candidate: dict[str, Any],
    *,
    expected_base_type: str,
    directory_domain: str,
) -> dict[str, Any]:
    fields = _candidate_fields(candidate)
    raw_compact = _compact(raw_target)
    query_tokens = target_tokens(raw_target)
    query_set = set(query_tokens)
    candidate_tokens = target_tokens(
        " ".join((fields["qualified"], fields["symbol"], fields["fileStem"]))
    )
    candidate_set = set(candidate_tokens)
    symbol_compact = _compact(fields["symbol"])
    qualified_compact = _compact(fields["qualified"])
    stem_compact = _compact(fields["fileStem"])
    prefixless_symbol = symbol_compact
    symbol_spelling = _nfkc(fields["symbol"])
    if (
        len(symbol_spelling) > 2
        and symbol_spelling[0].casefold() in _UNREAL_PREFIXES
        and symbol_spelling[1].isupper()
    ):
        prefixless_symbol = _compact(symbol_spelling[1:])

    match_kind = "no_match"
    score = 0.0
    if raw_compact and raw_compact == qualified_compact:
        score, match_kind = 1.0, "exact_qualified_symbol"
    elif raw_compact and raw_compact == symbol_compact:
        score, match_kind = 0.99, "exact_symbol"
    elif raw_compact and raw_compact == stem_compact:
        score, match_kind = 0.97, "exact_file_stem"
    elif (
        raw_compact
        and prefixless_symbol != symbol_compact
        and raw_compact == prefixless_symbol
    ):
        score, match_kind = 0.95, "exact_prefix_stripped_symbol"
    elif query_set and query_set.issubset(candidate_set):
        precision = len(query_set) / max(len(candidate_set), 1)
        score = min(0.94, 0.86 + precision * 0.08)
        match_kind = "all_core_tokens"
    elif query_set:
        coverage = len(query_set & candidate_set) / len(query_set)
        precision = len(query_set & candidate_set) / max(len(candidate_set), 1)
        score = coverage * 0.72 + precision * 0.12
        match_kind = "token_coverage" if score else "no_match"

    base_tokens = set(target_tokens(expected_base_type))
    if base_tokens and base_tokens & candidate_set:
        score = min(0.98, score + 0.025)
    if directory_domain and _compact(directory_domain) in _compact(fields["filePath"]):
        score = min(0.98, score + 0.015)
    return {
        "candidate": candidate,
        "symbol": fields["symbol"],
        "qualifiedSymbol": fields["qualified"],
        "filePath": fields["filePath"],
        "score": round(score, 6),
        "matchKind": match_kind,
        "exact": match_kind.startswith("exact_"),
        "queryTokens": query_tokens,
        "candidateTokens": candidate_tokens,
    }


def resolve_symbol_target(
    raw_target: str,
    candidates: list[dict[str, Any]],
    *,
    access: Literal["read", "write"] = "read",
    project_root: Path | None = None,
    expected_base_type: str = "",
    directory_domain: str = "",
    min_confidence: float = 0.82,
    tie_margin: float = 0.04,
) -> dict[str, Any]:
    """Select only a unique high-confidence candidate; never call fuzzy exact."""

    scored = [
        _score_candidate(
            raw_target,
            candidate,
            expected_base_type=expected_base_type,
            directory_domain=directory_domain,
        )
        for candidate in candidates
        if isinstance(candidate, dict)
    ]
    # Multiple chunks for the same symbol are one semantic target, not a tie.
    deduped: dict[str, dict[str, Any]] = {}
    for row in scored:
        symbol_identity = _compact(
            row.get("qualifiedSymbol") or row.get("symbol")
        )
        file_identity = _source_file_identity(row.get("filePath"))
        identity = f"{symbol_identity}\0{file_identity}"
        identity = identity if symbol_identity or file_identity else _compact(row)
        current = deduped.get(identity)
        if current is None or float(row["score"]) > float(current["score"]):
            deduped[identity] = row
    ranked = sorted(
        deduped.values(),
        key=lambda row: (
            -float(row.get("score") or 0.0),
            str(row.get("qualifiedSymbol") or row.get("symbol") or "").casefold(),
            str(row.get("filePath") or "").casefold(),
        ),
    )
    top = ranked[0] if ranked else None
    runner_up = ranked[1] if len(ranked) > 1 else None
    close_score = bool(
        top
        and runner_up
        and float(top["score"]) - float(runner_up["score"]) < tie_margin
    )
    write_evidence_ok = bool(
        top
        and top.get("symbol")
        and _existing_candidate_file(str(top.get("filePath") or ""), project_root)
    )
    resolved = bool(
        top
        and float(top.get("score") or 0.0) >= min_confidence
        and not close_score
        and (access == "read" or write_evidence_ok)
    )
    if resolved:
        status = "resolved"
        error_code = ""
    elif not ranked or not top or float(top.get("score") or 0.0) < min_confidence:
        status = "not_found"
        error_code = "TARGET_NOT_FOUND"
    elif close_score:
        status = "unresolved"
        error_code = "TARGET_AMBIGUOUS"
    else:
        status = "unresolved"
        error_code = "WRITE_TARGET_EVIDENCE_REQUIRED"
    return {
        "version": 1,
        "rawTarget": raw_target,
        "access": access,
        "status": status,
        "errorCode": error_code or None,
        "selected": top if resolved else None,
        "confidence": float(top.get("score") or 0.0) if top else 0.0,
        "exact": bool(top.get("exact")) if resolved and top else False,
        "writeEvidenceVerified": write_evidence_ok if access == "write" else None,
        "candidates": ranked[:8],
    }
