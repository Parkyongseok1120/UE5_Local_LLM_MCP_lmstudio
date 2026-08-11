"""Strict server-owned policy for bounded local feature-intent selection."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any


_DISALLOWED_MARKERS = (
    "subsystem",
    "module",
    "plugin",
    "server",
    "client",
    "network",
    "multiplayer",
    "replicat",
    "rpc",
    "authority",
    "savegame",
    "save game",
    "persistence",
    "persistent",
    "migration",
    "schema",
    "config",
    "delete",
    "rename",
    "move file",
    "public api",
    "서브시스템",
    "모듈",
    "플러그인",
    "서버",
    "클라이언트",
    "네트워크",
    "멀티플레이",
    "복제",
    "권한",
    "저장",
    "로드",
    "영속",
    "마이그레이션",
    "스키마",
    "삭제",
    "이름 변경",
)

_ALLOWED_SUFFIXES = (".h", ".hpp", ".cpp", ".cc", ".cxx", ".inl")


def _scope_owner(path: str) -> str:
    parts = PurePosixPath(path.replace("\\", "/")).parts
    if not parts:
        return ""
    if parts[0].casefold() == "source" and len(parts) >= 2:
        return f"source/{parts[1].casefold()}"
    if parts[0].casefold() == "plugins" and len(parts) >= 4:
        try:
            source_index = next(
                index for index, part in enumerate(parts) if part.casefold() == "source"
            )
        except StopIteration:
            return ""
        if source_index + 1 < len(parts):
            return "/".join(part.casefold() for part in parts[: source_index + 2])
    return ""


def evaluate_bounded_local_fast_path(
    request: str,
    *,
    target_files: list[str],
    target_snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return an auditable decision; defaults to ineligible on uncertainty."""

    reasons: list[str] = []
    normalized_targets = list(
        dict.fromkeys(str(path or "").strip().replace("\\", "/") for path in target_files)
    )
    if not 1 <= len(normalized_targets) <= 2:
        reasons.append("selected slice must contain one or two exact files")
    snapshot_by_path = {
        str(item.get("path") or "").replace("\\", "/"): item
        for item in target_snapshots
        if isinstance(item, dict)
    }
    for path in normalized_targets:
        lowered = path.casefold()
        snapshot = snapshot_by_path.get(path)
        if not lowered.endswith(_ALLOWED_SUFFIXES):
            reasons.append(f"{path}: only existing C++ source files are eligible")
        if lowered.endswith((".build.cs", ".target.cs")):
            reasons.append(f"{path}: build/module policy files require explicit intent")
        if not snapshot or snapshot.get("exists") is not True:
            reasons.append(f"{path}: fast path cannot create a new target")
    owners = {_scope_owner(path) for path in normalized_targets}
    if "" in owners or len(owners) != 1:
        reasons.append("targets must stay inside one existing Source module owner")

    text = re.sub(r"\s+", " ", str(request or "")).strip().casefold()
    marker_hits = [marker for marker in _DISALLOWED_MARKERS if marker in text]
    if marker_hits:
        reasons.append(
            "request changes architecture/authority/persistence scope: "
            + ", ".join(marker_hits[:4])
        )
    if re.search(r"\b(?:create|new)\s+(?:class|file|component|actor)\b", text):
        reasons.append("new type/file ownership requires explicit intent selection")
    if any(marker in text for marker in ("whole project", "entire project", "across modules", "프로젝트 전체")):
        reasons.append("project-wide scope is not bounded local work")

    eligible = not reasons
    return {
        "eligible": eligible,
        "policy": "bounded_local_existing_owner_v1",
        "selectedIntentId": "bounded_local" if eligible else "",
        "reasons": reasons,
        "targetFiles": normalized_targets,
        "owner": next(iter(owners), "") if len(owners) == 1 else "",
        "serverOwnedPhases": [
            "SelectIntent",
            "ResolveSlice",
            "CaptureSnapshot",
            "BindIntent",
        ],
    }


def bounded_local_question_answers() -> dict[str, str]:
    """Explicit defaults guaranteed by the strict fast-path eligibility policy."""

    return {
        "ownershipLifetime": "Preserve the existing selected source owner and its lifetime.",
        "authorityReplication": "Preserve current authority; introduce no replication, RPC, or network policy.",
        "persistence": "Introduce no save, config, schema, or migration behavior.",
        "failureSemantics": "Fail closed and preserve the prior observable behavior outside the requested case.",
        "userVisibleBehavior": "Change only the behavior explicitly named in the bounded request.",
        "nonGoals": "No new subsystem, module, plugin, global state, persistence, or replication.",
    }
