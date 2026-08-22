#!/usr/bin/env python
"""Find a unique engine shard for explicit project-name selectors."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from direct_rag_generation_identity import RagGenerationTransitionError
from direct_rag_named_candidate import match_named_candidate


def _names(value: Any) -> list[str]:
    values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    result: list[str] = []
    for item in values:
        name = str(item or "").strip()
        if not name:
            continue
        candidate = Path(name).expanduser()
        if candidate.exists() or candidate.suffix.casefold() == ".uproject":
            continue
        if name not in result:
            result.append(name)
    return result


def resolve_named_index(
    candidates: list[Path],
    selector: Any,
    workspace: Path,
) -> dict[str, Any] | None:
    names = _names(selector)
    if not names:
        return None
    selected: list[tuple[Path, str, Path, str, str]] = []
    for name in names:
        matches: list[tuple[Path, str, Path, str, str]] = []
        transition: RagGenerationTransitionError | None = None
        for index in candidates:
            if not index.is_file():
                continue
            try:
                matches.extend(match_named_candidate(index, name, workspace))
            except RagGenerationTransitionError as exc:
                transition = exc
                continue
            except Exception:
                continue
        unique = {(str(index), root): row for index, root, *rest in matches for row in [(index, root, *rest)]}
        matches = list(unique.values())
        if not matches:
            if transition is not None:
                return {
                    "ok": False,
                    "errorCode": "RAG_GENERATION_TRANSITION",
                    "error": str(transition),
                    "retryAllowed": True,
                }
            return {
                "ok": False,
                "errorCode": "PROJECT_SELECTOR_NOT_FOUND",
                "error": f"No current engine-compatible project descriptor matches name: {name}",
                "projectNames": names,
            }
        if len(matches) > 1:
            return {
                "ok": False,
                "errorCode": "PROJECT_SELECTOR_AMBIGUOUS",
                "error": "The project name resolves to multiple current engine indexes; use an exact .uproject path.",
                "projectNames": names,
                "candidateIndexes": sorted({str(row[0]) for row in matches}),
                "projectRoots": sorted({row[1] for row in matches}),
            }
        selected.append(matches[0])
    indexes = {str(row[0]) for row in selected}
    if len(indexes) > 1:
        bindings = {(row[3], row[4].casefold()) for row in selected}
        return {
            "ok": False,
            "errorCode": (
                "RAG_MULTI_ENGINE_QUERY_UNSUPPORTED"
                if len(bindings) > 1
                else "RAG_MULTI_INDEX_QUERY_UNSUPPORTED"
            ),
            "error": "One RAG call cannot combine project names owned by different index shards.",
            "projectNames": names,
            "candidateIndexes": sorted(indexes),
        }
    index = selected[0][0]
    return {
        "ok": True,
        "index": str(index),
        "projectNames": names,
        "projectRoots": [row[1] for row in selected],
        "projectDescriptors": [str(row[2]) for row in selected],
        "nameBound": True,
    }


__all__ = ["resolve_named_index"]
