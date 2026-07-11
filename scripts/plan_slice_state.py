#!/usr/bin/env python
"""Track multi-slice plan execution with file-hash postconditions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SliceProgress:
    slice_id: str
    status: str = "pending"
    file_hashes: dict[str, str] = field(default_factory=dict)
    completed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _file_hash(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return hashlib.sha256(data).hexdigest()[:16]


def init_slice_state(plan_slices: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": 1,
        "activeSliceIndex": 0,
        "slices": [
            {"sliceId": str(item.get("slice_id") or item.get("sliceId") or ""), "status": "pending", "fileHashes": {}}
            for item in plan_slices
        ],
    }


def load_slice_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 1, "activeSliceIndex": 0, "slices": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "activeSliceIndex": 0, "slices": []}
    return data if isinstance(data, dict) else {"version": 1, "activeSliceIndex": 0, "slices": []}


def save_slice_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def mark_slice_complete(
    state: dict[str, Any],
    *,
    project_root: Path,
    written_paths: list[Path | str],
    plan_slices: list[dict[str, Any]],
) -> dict[str, Any]:
    idx = int(state.get("activeSliceIndex") or 0)
    slices = list(state.get("slices") or [])
    if idx >= len(slices):
        return state
    plan = plan_slices[idx] if idx < len(plan_slices) else {}
    hashes: dict[str, str] = {}
    for raw in written_paths:
        path = Path(raw)
        if not path.is_file():
            candidate = project_root / str(raw)
            path = candidate if candidate.is_file() else path
        if path.is_file():
            try:
                rel = str(path.relative_to(project_root)).replace("\\", "/")
            except ValueError:
                rel = str(path).replace("\\", "/")
            hashes[rel] = _file_hash(path)
    slices[idx] = {
        "sliceId": str(plan.get("slice_id") or plan.get("sliceId") or slices[idx].get("sliceId") or ""),
        "status": "complete",
        "fileHashes": hashes,
    }
    state["slices"] = slices
    if idx + 1 < len(plan_slices):
        state["activeSliceIndex"] = idx + 1
        if idx + 1 < len(slices):
            slices[idx + 1]["status"] = "active"
        else:
            slices.append({"sliceId": str(plan_slices[idx + 1].get("slice_id") or ""), "status": "active", "fileHashes": {}})
            state["slices"] = slices
    return state


def next_slice_prompt(state: dict[str, Any], plan_slices: list[dict[str, Any]]) -> str:
    idx = int(state.get("activeSliceIndex") or 0)
    if idx >= len(plan_slices):
        return "All plan slices complete."
    plan = plan_slices[idx]
    files = ", ".join(str(path) for path in (plan.get("files") or [])[:2])
    post = "; ".join(str(item) for item in (plan.get("postconditions") or [])[:3])
    return (
        f"Continue plan slice {plan.get('slice_id') or plan.get('sliceId')}: {plan.get('title')}. "
        f"Edit at most 2 files ({files}). Postconditions: {post}."
    )
