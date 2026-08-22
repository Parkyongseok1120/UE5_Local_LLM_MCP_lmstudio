#!/usr/bin/env python
"""Enumerate RAG shards and strongly verify only identity-matching candidates."""

from __future__ import annotations

import json
from pathlib import Path

from direct_rag_generation_identity import RagGenerationTransitionError
from direct_rag_manifest_binding import engine_bindings_match, read_manifest_engine_binding
from direct_rag_project_engine import normalize_engine_version
from direct_rag_unbuilt_shard import is_shard_namespace

MAX_LIGHT_MANIFEST_BYTES = 1024 * 1024


def candidate_indexes(base_index: Path) -> list[Path]:
    base = base_index.expanduser().resolve()
    result = [base]
    if (
        base.name.casefold() != "rag.sqlite"
        or not is_shard_namespace(base.parent.name)
    ):
        return result
    data_root = base.parent.parent.resolve()
    if not data_root.is_dir():
        return result
    for child in sorted(data_root.iterdir(), key=lambda path: path.name.casefold()):
        folded = child.name.casefold()
        if (
            child.name.startswith(".")
            or ".direct-refresh-" in folded
            or not is_shard_namespace(child.name)
            or child.is_symlink()
            or not child.is_dir()
        ):
            continue
        candidate = child / "rag.sqlite"
        if candidate.is_symlink() or not candidate.is_file():
            continue
        resolved = candidate.resolve()
        if (
            resolved.parent.parent != data_root
            or resolved in result
            or light_manifest_engine_binding(resolved) is None
        ):
            continue
        result.append(resolved)
    return result


def light_manifest_engine_binding(index: Path) -> tuple[str, str] | None:
    """Read only routing identity; this deliberately does not certify a generation."""

    manifest = index.expanduser().resolve().parent / "build_manifest.json"
    try:
        if manifest.stat().st_size > MAX_LIGHT_MANIFEST_BYTES:
            return None
        payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    version = normalize_engine_version(payload.get("engineVersion"))
    if not version:
        return None
    return version, str(payload.get("engineAssociation") or "").strip()


def validated_candidate_binding(
    index: Path,
    requested_version: str,
    requested_association: str,
) -> tuple[str, str] | None:
    light = light_manifest_engine_binding(index)
    if light is None or not engine_bindings_match(
        requested_version,
        requested_association,
        light[0],
        light[1],
    ):
        return None
    validated = read_manifest_engine_binding(index.expanduser().resolve().parent)
    if not engine_bindings_match(
        requested_version,
        requested_association,
        validated[0],
        validated[1],
    ):
        return None
    return validated


def matching_engine_indexes(
    candidates: list[Path],
    requested_version: str,
    requested_association: str,
) -> list[Path]:
    matches: list[Path] = []
    transition: RagGenerationTransitionError | None = None
    for index in candidates:
        try:
            binding = validated_candidate_binding(
                index,
                requested_version,
                requested_association,
            )
        except RagGenerationTransitionError as exc:
            transition = exc
            continue
        if binding is not None:
            matches.append(index.resolve())
    if not matches and transition is not None:
        raise transition
    return matches


def select_existing_index(
    base: Path,
    matches: list[Path],
    *,
    owned_index: Path | None,
    named_index: Path | None,
    requested_version: str,
    reported_association: object,
) -> dict[str, object]:
    if owned_index is not None:
        return {"ok": True, "index": owned_index if owned_index in matches else None}
    if named_index is not None:
        return {"ok": True, "index": named_index if named_index in matches else None}
    if base in matches:
        return {"ok": True, "index": base}
    if len(matches) == 1:
        return {"ok": True, "index": matches[0]}
    if not matches:
        return {"ok": True, "index": None}
    return {
        "ok": False,
        "errorCode": "RAG_ENGINE_INDEX_AMBIGUOUS",
        "error": (
            f"Multiple RAG indexes declare Unreal {requested_version}; "
            "configure one exact index path."
        ),
        "projectEngineVersion": requested_version,
        "projectEngineAssociation": reported_association,
        "candidateIndexes": [str(path) for path in matches],
    }


__all__ = [
    "candidate_indexes",
    "light_manifest_engine_binding",
    "matching_engine_indexes",
    "select_existing_index",
    "validated_candidate_binding",
]
