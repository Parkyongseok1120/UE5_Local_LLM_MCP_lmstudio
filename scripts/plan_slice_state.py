#!/usr/bin/env python
"""Track multi-slice plan execution with file-hash postconditions and proof chain."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SLICE_STATUSES = (
    "pending",
    "active",
    "files_read",
    "patched",
    "static_validated",
    "built",
    "complete",
    "failed",
)


@dataclass
class SliceProgress:
    slice_id: str
    status: str = "pending"
    file_hashes: dict[str, str] = field(default_factory=dict)
    proof_level: str = ""
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
        "version": 2,
        "activeSliceIndex": 0,
        "slices": [
            {
                "sliceId": str(item.get("slice_id") or item.get("sliceId") or ""),
                "status": "active" if idx == 0 else "pending",
                "fileHashes": {},
                "proofLevel": "",
            }
            for idx, item in enumerate(plan_slices)
        ],
    }


def load_slice_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 2, "activeSliceIndex": 0, "slices": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"version": 2, "activeSliceIndex": 0, "slices": []}
    return data if isinstance(data, dict) else {"version": 2, "activeSliceIndex": 0, "slices": []}


def save_slice_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def advance_slice_status(
    state: dict[str, Any],
    *,
    status: str,
    proof_level: str = "",
) -> dict[str, Any]:
    idx = int(state.get("activeSliceIndex") or 0)
    slices = list(state.get("slices") or [])
    if idx >= len(slices):
        return state
    if status not in SLICE_STATUSES:
        return state
    slices[idx]["status"] = status
    if proof_level:
        slices[idx]["proofLevel"] = proof_level
    state["slices"] = slices
    return state


def mark_slice_complete(
    state: dict[str, Any],
    *,
    project_root: Path,
    written_paths: list[Path | str],
    plan_slices: list[dict[str, Any]],
    proof_level: str = "",
    required_evidence_satisfied: bool = False,
    runtime_verified: bool = False,
) -> dict[str, Any]:
    idx = int(state.get("activeSliceIndex") or 0)
    slices = list(state.get("slices") or [])
    if idx >= len(slices):
        return state
    plan = plan_slices[idx] if idx < len(plan_slices) else {}
    slice_kind = str(plan.get("slice_kind") or plan.get("sliceKind") or "compile")
    if slice_kind in {"analysis", "architecture"}:
        if not required_evidence_satisfied:
            slices[idx]["status"] = "failed"
            state["lastError"] = "slice completion rejected: required evidence not satisfied"
            state["slices"] = slices
            return state
    elif slice_kind == "runtime":
        if not runtime_verified or proof_level not in {"PIEVerified", "RuntimeVerified"}:
            slices[idx]["status"] = "built" if proof_level == "Built" else "failed"
            state["lastError"] = "slice completion rejected: runtime evidence required"
            state["slices"] = slices
            return state
    elif proof_level != "Built":
        slices[idx]["status"] = "static_validated" if proof_level in {"BuiltStale", "BuiltUnverified"} else "failed"
        state["lastError"] = f"slice completion rejected: proof_level={proof_level or 'missing'}"
        state["slices"] = slices
        return state

    if slice_kind not in {"analysis", "architecture"} and not written_paths:
        slices[idx]["status"] = "failed"
        state["lastError"] = "slice completion rejected: empty written_paths"
        state["slices"] = slices
        return state

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
    if not hashes and slice_kind not in {"analysis", "architecture"}:
        slices[idx]["status"] = "failed"
        state["lastError"] = "slice completion rejected: no file hashes captured"
        state["slices"] = slices
        return state

    slices[idx] = {
        "sliceId": str(plan.get("slice_id") or plan.get("sliceId") or slices[idx].get("sliceId") or ""),
        "status": "complete",
        "fileHashes": hashes,
        "proofLevel": proof_level,
    }
    state["slices"] = slices
    if idx + 1 < len(plan_slices):
        state["activeSliceIndex"] = idx + 1
        if idx + 1 < len(slices):
            slices[idx + 1]["status"] = "active"
        else:
            slices.append(
                {
                    "sliceId": str(plan_slices[idx + 1].get("slice_id") or ""),
                    "status": "active",
                    "fileHashes": {},
                    "proofLevel": "",
                }
            )
            state["slices"] = slices
    return state


def proof_level_from_build_output(ok: bool, output: str) -> str:
    if not ok:
        return "Failed"
    text = str(output or "")
    action_patterns = (
        r"Executing up to \d+ processes, one per physical core",
        r"Building \d+ action(?:s)? with \d+ process(?:es)?",
        r"\[(\d+)/(\d+)\] Compile",
    )
    if re.search(action_patterns[0], text, re.IGNORECASE) or re.search(action_patterns[1], text, re.IGNORECASE):
        return "Built"
    action_matches = list(re.finditer(action_patterns[2], text, re.IGNORECASE))
    if action_matches and any(int(match.group(2)) > 0 for match in action_matches):
        return "Built"
    if re.search(r"Target is up to date|0 action(?:s)?", text, re.IGNORECASE):
        return "BuiltStale"
    return "BuiltUnverified"


def next_slice_prompt(state: dict[str, Any], plan_slices: list[dict[str, Any]]) -> str:
    idx = int(state.get("activeSliceIndex") or 0)
    if idx >= len(plan_slices):
        return "All plan slices complete."
    plan = plan_slices[idx]
    files = ", ".join(str(path) for path in (plan.get("files") or [])[:2])
    post = "; ".join(str(item) for item in (plan.get("postconditions") or [])[:3])
    slice_status = ""
    slices = state.get("slices") or []
    if idx < len(slices):
        slice_status = str(slices[idx].get("status") or "")
    return (
        f"Continue plan slice {plan.get('slice_id') or plan.get('sliceId')}: {plan.get('title')}. "
        f"Status={slice_status or 'active'}. Edit at most 2 files ({files}). Postconditions: {post}."
    )
