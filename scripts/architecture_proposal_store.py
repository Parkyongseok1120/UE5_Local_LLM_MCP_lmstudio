#!/usr/bin/env python
"""Durable session-scoped architecture proposal drafts and compact revisions."""

from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from atomic_io import atomic_write_text
from state_root import resolve_agent_state_root


def _canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def proposal_revision(value: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:20]


def _draft_path(session_id: str, project_root: str) -> Path:
    identity = json.dumps(
        {"sessionId": session_id or "", "projectRoot": project_root or ""},
        sort_keys=True,
        ensure_ascii=False,
    )
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return resolve_agent_state_root() / "architecture-proposals" / f"{key}.json"


def load_proposal_draft(session_id: str, project_root: str) -> dict[str, Any] | None:
    if not str(session_id or "").strip():
        return None
    path = _draft_path(session_id, project_root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    proposal = payload.get("proposal")
    if not isinstance(proposal, dict):
        return None
    return {
        "proposal": proposal,
        "revision": str(payload.get("revision") or proposal_revision(proposal)),
    }


def save_proposal_draft(
    session_id: str, project_root: str, proposal: dict[str, Any]
) -> str:
    revision = proposal_revision(proposal)
    if not str(session_id or "").strip():
        return revision
    path = _draft_path(session_id, project_root)
    atomic_write_text(
        path,
        json.dumps(
            {
                "version": 1,
                "sessionId": session_id,
                "projectRoot": project_root,
                "revision": revision,
                "proposal": proposal,
                "updatedAt": time.time(),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
    return revision


def merge_proposal_patch(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge objects; arrays/scalars replace their prior values."""
    merged = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_proposal_patch(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def apply_proposal_repairs(
    base: dict[str, Any], repairs: list[dict[str, Any]]
) -> dict[str, Any]:
    """Apply compact dotted-path replacements to a proposal draft.

    Repair paths intentionally address object fields only. Arrays are replaced as
    complete values so a small model never has to reason about unstable indexes in
    a prior draft.
    """
    repaired = deepcopy(base)
    for repair in repairs:
        if not isinstance(repair, dict):
            raise ValueError("each proposal repair must be an object")
        json_path = str(repair.get("jsonPath") or "").strip()
        if not json_path or not all(
            part and part.replace("_", "a").isalnum() and not part[0].isdigit()
            for part in json_path.split(".")
        ):
            raise ValueError(f"invalid proposal repair jsonPath: {json_path or '<empty>'}")
        if "value" not in repair:
            raise ValueError(f"proposal repair is missing value: {json_path}")
        parts = json_path.split(".")
        cursor: dict[str, Any] = repaired
        for part in parts[:-1]:
            child = cursor.get(part)
            if child is None:
                child = {}
                cursor[part] = child
            if not isinstance(child, dict):
                raise ValueError(
                    f"proposal repair path crosses a non-object field: {json_path}"
                )
            cursor = child
        cursor[parts[-1]] = deepcopy(repair["value"])
    return repaired
