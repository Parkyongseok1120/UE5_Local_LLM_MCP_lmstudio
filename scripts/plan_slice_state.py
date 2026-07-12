#!/usr/bin/env python
"""Track multi-slice plan execution with file-hash postconditions and proof chain."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
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

INFORMATIONAL_SLICE_KINDS = frozenset({"architecture", "analysis", "investigation"})
DEFAULT_MAX_RETRIES_PER_SLICE = 5
DEFAULT_MAX_TOTAL_MODEL_CALLS = 40


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


def _normalize_rel(path: Path, project_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _slice_kind(plan: dict[str, Any]) -> str:
    return str(plan.get("slice_kind") or plan.get("sliceKind") or "compile")


def _expected_files(plan: dict[str, Any]) -> list[str]:
    raw = plan.get("files") or []
    return [str(item).replace("\\", "/") for item in raw if str(item).strip()]


def _is_placeholder_path(path: str) -> bool:
    return "<" in path or ">" in path or path.startswith("...")


def _written_intersects_expected(
    written_paths: list[Path | str],
    expected: list[str],
    project_root: Path,
) -> bool:
    if not expected or all(_is_placeholder_path(item) for item in expected):
        return bool(written_paths)
    written_rels = {_normalize_rel(Path(raw), project_root) for raw in written_paths}
    for item in expected:
        if _is_placeholder_path(item):
            continue
        normalized = item.replace("\\", "/").lstrip("./")
        if normalized in written_rels:
            return True
        if any(rel.endswith("/" + normalized) or rel == normalized for rel in written_rels):
            return True
    return False


def _has_effective_writes(hashes: dict[str, str], pre_hashes: dict[str, str]) -> bool:
    if not hashes:
        return False
    for rel, digest in hashes.items():
        if pre_hashes.get(rel) != digest:
            return True
    return bool(hashes) and not pre_hashes


def _reject_slice(
    state: dict[str, Any],
    idx: int,
    slices: list[dict[str, Any]],
    *,
    reason: str,
    status: str = "failed",
) -> dict[str, Any]:
    if 0 <= idx < len(slices):
        slices[idx]["status"] = status
        slices[idx]["failureReason"] = reason
    state["lastError"] = reason
    state["failed"] = True
    state["slices"] = slices
    return state


def _fingerprint_entry(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "sliceId": str(item.get("slice_id") or item.get("sliceId") or ""),
        "sliceKind": _slice_kind(item),
        "files": _expected_files(item),
        "postconditions": [str(v) for v in (item.get("postconditions") or [])],
        "requiredIncludes": [
            str(v)
            for v in (item.get("required_includes") or item.get("requiredIncludes") or [])
        ],
        "requiredValidators": [
            str(v)
            for v in (item.get("required_validators") or item.get("requiredValidators") or [])
        ],
        "dependsOn": [str(v) for v in (item.get("depends_on") or item.get("dependsOn") or [])],
        "domain": str(item.get("domain") or "generic"),
        "approvalScope": str(item.get("approval_scope") or item.get("approvalScope") or ""),
    }


def plan_fingerprint(plan_slices: list[dict[str, Any]]) -> str:
    payload = [_fingerprint_entry(item) for item in plan_slices]
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def normalize_slice_state(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("completed"):
        state["failed"] = False
        state["activeNodeId"] = ""
    if state.get("failed"):
        state["completed"] = False
    return state


def migrate_slice_state_on_fingerprint_change(
    state: dict[str, Any],
    plan_slices: list[dict[str, Any]],
    *,
    expected_fingerprint: str,
) -> dict[str, Any]:
    if state.get("corrupt"):
        return state
    stored = str(state.get("planFingerprint") or "")
    if not expected_fingerprint or not stored or stored == expected_fingerprint:
        return normalize_slice_state(state)
    fresh = init_slice_state(
        plan_slices,
        plan_id=str(state.get("planId") or ""),
        plan_revision=int(state.get("planRevision") or 1) + 1,
    )
    fresh["planMigrationReason"] = f"plan fingerprint changed ({stored} -> {expected_fingerprint})"
    fresh["previousPlanFingerprint"] = stored
    fresh["planFingerprint"] = expected_fingerprint
    return normalize_slice_state(fresh)


def _bundle_mutation_paths(bundle: dict[str, Any] | None) -> list[str]:
    if not isinstance(bundle, dict):
        return []
    paths: list[str] = []
    for item in bundle.get("patches") or []:
        rel = str(item.get("path") or "").replace("\\", "/").strip()
        if rel:
            paths.append(rel)
    for item in bundle.get("files") or []:
        rel = str(item.get("path") or "").replace("\\", "/").strip()
        if rel:
            paths.append(rel)
    return list(dict.fromkeys(paths))


def capture_pre_change_hashes(
    state: dict[str, Any],
    *,
    project_root: Path,
    plan_slices: list[dict[str, Any]],
    bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    idx = int(state.get("activeSliceIndex") or 0)
    slices = list(state.get("slices") or [])
    if idx >= len(slices) or idx >= len(plan_slices):
        return state
    plan = plan_slices[idx]
    pre: dict[str, str] = dict(slices[idx].get("preChangeHashes") or {})
    rel_paths = list(_bundle_mutation_paths(bundle))
    if not rel_paths:
        rel_paths = [rel for rel in _expected_files(plan) if not _is_placeholder_path(rel)]
    for rel in rel_paths:
        if _is_placeholder_path(rel):
            continue
        candidate = project_root / rel.replace("\\", "/")
        if candidate.is_file():
            pre[rel.replace("\\", "/")] = _file_hash(candidate)
    for raw in plan.get("files") or []:
        text = str(raw)
        if _is_placeholder_path(text):
            continue
        candidate = project_root / text.replace("\\", "/")
        if candidate.is_file():
            pre[_normalize_rel(candidate, project_root)] = _file_hash(candidate)
    slices[idx]["preChangeHashes"] = pre
    state["slices"] = slices
    return state


def validate_loaded_state(
    state: dict[str, Any],
    plan_slices: list[dict[str, Any]],
    *,
    expected_fingerprint: str = "",
) -> dict[str, Any]:
    if state.get("corrupt"):
        return state
    if expected_fingerprint and str(state.get("planFingerprint") or "") not in {"", expected_fingerprint}:
        state = migrate_slice_state_on_fingerprint_change(
            state,
            plan_slices,
            expected_fingerprint=expected_fingerprint,
        )
    idx = int(state.get("activeSliceIndex") or 0)
    if plan_slices and idx > len(plan_slices):
        return {
            **init_slice_state(plan_slices),
            "failed": True,
            "lastError": "plan slice index out of range",
            "planFingerprint": expected_fingerprint,
        }
    return normalize_slice_state(state)


def active_slice_status(state: dict[str, Any]) -> str:
    idx = int(state.get("activeSliceIndex") or 0)
    slices = state.get("slices") or []
    if idx >= len(slices):
        return "complete"
    return str(slices[idx].get("status") or "")


def slice_completion_accepted(state: dict[str, Any], idx: int) -> bool:
    slices = state.get("slices") or []
    if idx >= len(slices):
        return False
    return str(slices[idx].get("status") or "") == "complete"


def retry_or_total_cap_exceeded(
    state: dict[str, Any],
    *,
    max_retries_per_slice: int = DEFAULT_MAX_RETRIES_PER_SLICE,
    max_total_model_calls: int = DEFAULT_MAX_TOTAL_MODEL_CALLS,
) -> bool:
    retries = int(state.get("retryWithinSlice") or 0)
    total = int(state.get("totalModelCalls") or 0)
    return retries >= max_retries_per_slice or total >= max_total_model_calls


def init_slice_state(
    plan_slices: list[dict[str, Any]],
    *,
    plan_id: str = "",
    plan_revision: int = 1,
) -> dict[str, Any]:
    nodes = [
        {
            "sliceId": str(item.get("slice_id") or item.get("sliceId") or ""),
            "status": "active" if idx == 0 else "pending",
            "attemptCount": 0,
            "fileHashes": {},
            "preChangeHashes": {},
            "proofLevel": "",
            "completionReason": "",
            "failureReason": "",
            "sliceKind": _slice_kind(item),
        }
        for idx, item in enumerate(plan_slices)
    ]
    return {
        "version": 3,
        "planId": plan_id or uuid.uuid4().hex[:16],
        "planRevision": int(plan_revision),
        "activeNodeId": nodes[0]["sliceId"] if nodes else "",
        "activeSliceIndex": 0,
        "retryWithinSlice": 0,
        "totalModelCalls": 0,
        "completed": not plan_slices,
        "failed": False,
        "planFingerprint": plan_fingerprint(plan_slices),
        "slices": nodes,
    }


def load_slice_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return init_slice_state([])
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {
            "version": 3,
            "planId": "",
            "planRevision": 0,
            "activeNodeId": "",
            "activeSliceIndex": 0,
            "retryWithinSlice": 0,
            "totalModelCalls": 0,
            "completed": False,
            "failed": True,
            "corrupt": True,
            "lastError": "plan slice state corrupt or unreadable",
            "slices": [],
        }
    if not isinstance(data, dict):
        return {
            "version": 3,
            "failed": True,
            "corrupt": True,
            "lastError": "plan slice state is not an object",
            "slices": [],
        }
    if int(data.get("version") or 0) < 3:
        return normalize_slice_state(_migrate_v2_to_v3(data))
    return normalize_slice_state(data)


def _migrate_v2_to_v3(data: dict[str, Any]) -> dict[str, Any]:
    slices = list(data.get("slices") or [])
    nodes = [
        {
            "sliceId": str(item.get("sliceId") or ""),
            "status": str(item.get("status") or "pending"),
            "attemptCount": 0,
            "fileHashes": dict(item.get("fileHashes") or {}),
            "preChangeHashes": {},
            "proofLevel": str(item.get("proofLevel") or ""),
            "completionReason": "",
            "failureReason": "",
            "sliceKind": "compile",
        }
        for item in slices
    ]
    active = int(data.get("activeSliceIndex") or 0)
    return {
        "version": 3,
        "planId": str(data.get("planId") or uuid.uuid4().hex[:16]),
        "planRevision": int(data.get("planRevision") or 1),
        "activeNodeId": nodes[active]["sliceId"] if active < len(nodes) else "",
        "activeSliceIndex": active,
        "retryWithinSlice": int(data.get("retryWithinSlice") or 0),
        "totalModelCalls": int(data.get("totalModelCalls") or 0),
        "completed": bool(data.get("completed")),
        "failed": bool(data.get("failed")),
        "slices": nodes,
        "lastError": data.get("lastError", ""),
    }


def save_slice_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def increment_model_call(state: dict[str, Any]) -> dict[str, Any]:
    state["totalModelCalls"] = int(state.get("totalModelCalls") or 0) + 1
    return state


def increment_retry_within_slice(state: dict[str, Any]) -> dict[str, Any]:
    state["retryWithinSlice"] = int(state.get("retryWithinSlice") or 0) + 1
    return state


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
    if idx >= len(plan_slices) or idx >= len(slices):
        if plan_slices and idx >= len(plan_slices):
            state["activeSliceIndex"] = len(plan_slices)
            state["completed"] = True
            state["activeNodeId"] = ""
        return state

    plan = plan_slices[idx]
    slice_kind = _slice_kind(plan)
    slices[idx]["attemptCount"] = int(slices[idx].get("attemptCount") or 0) + 1
    slices[idx]["sliceKind"] = slice_kind

    if slice_kind in INFORMATIONAL_SLICE_KINDS:
        if not required_evidence_satisfied:
            return _reject_slice(
                state,
                idx,
                slices,
                reason="slice completion rejected: required evidence not satisfied",
            )
    elif slice_kind == "runtime":
        if not runtime_verified or proof_level not in {"PIEVerified", "RuntimeVerified"}:
            return _reject_slice(
                state,
                idx,
                slices,
                reason="slice completion rejected: runtime evidence required",
            )
    elif proof_level != "Built":
        return _reject_slice(
            state,
            idx,
            slices,
            reason=f"slice completion rejected: proof_level={proof_level or 'missing'}",
        )

    expected = _expected_files(plan)
    if slice_kind not in INFORMATIONAL_SLICE_KINDS:
        if not written_paths:
            return _reject_slice(
                state,
                idx,
                slices,
                reason="slice completion rejected: empty written_paths",
            )
        if expected and not _written_intersects_expected(written_paths, expected, project_root):
            return _reject_slice(
                state,
                idx,
                slices,
                reason="slice completion rejected: wrong-file or unrelated edit",
            )

    pre_hashes = dict(slices[idx].get("preChangeHashes") or {})
    hashes: dict[str, str] = {}
    for raw in written_paths:
        path = Path(raw)
        if not path.is_file():
            candidate = project_root / str(raw)
            path = candidate if candidate.is_file() else path
        if path.is_file():
            hashes[_normalize_rel(path, project_root)] = _file_hash(path)

    if slice_kind not in INFORMATIONAL_SLICE_KINDS:
        if not hashes:
            return _reject_slice(
                state,
                idx,
                slices,
                reason="slice completion rejected: no file hashes captured",
            )
        if not _has_effective_writes(hashes, pre_hashes):
            return _reject_slice(
                state,
                idx,
                slices,
                reason="slice completion rejected: byte-identical or no-op changes",
            )

    slices[idx] = {
        **slices[idx],
        "sliceId": str(plan.get("slice_id") or plan.get("sliceId") or slices[idx].get("sliceId") or ""),
        "status": "complete",
        "fileHashes": hashes,
        "proofLevel": proof_level or ("EvidenceVerified" if slice_kind in INFORMATIONAL_SLICE_KINDS else proof_level),
        "completionReason": "evidence_satisfied" if slice_kind in INFORMATIONAL_SLICE_KINDS else "built",
        "failureReason": "",
    }
    state["slices"] = slices
    state["retryWithinSlice"] = 0
    state["lastError"] = ""
    state["failed"] = False

    if idx + 1 >= len(plan_slices):
        state["activeSliceIndex"] = len(plan_slices)
        state["activeNodeId"] = ""
        state["completed"] = True
    else:
        state["activeSliceIndex"] = idx + 1
        next_id = str(plan_slices[idx + 1].get("slice_id") or plan_slices[idx + 1].get("sliceId") or "")
        state["activeNodeId"] = next_id
        if idx + 1 < len(slices):
            slices[idx + 1]["status"] = "active"
        else:
            slices.append(
                {
                    "sliceId": next_id,
                    "status": "active",
                    "attemptCount": 0,
                    "fileHashes": {},
                    "preChangeHashes": {},
                    "proofLevel": "",
                    "completionReason": "",
                    "failureReason": "",
                    "sliceKind": _slice_kind(plan_slices[idx + 1]),
                }
            )
        state["slices"] = slices
    return state


def proof_level_from_build_output(ok: bool, output: str) -> str:
    from build_proof import proof_level_from_build_output as _canonical_proof_level

    return _canonical_proof_level(ok, output)


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
    total = len(plan_slices)
    user_message = f"Editing slice {idx + 1}/{total}: {plan.get('title') or plan.get('slice_id')}"
    return (
        f"Continue plan slice {plan.get('slice_id') or plan.get('sliceId')}: {plan.get('title')}. "
        f"Status={slice_status or 'active'}. Edit at most 2 files ({files}). Postconditions: {post}. "
        f"phase=editing userMessage={user_message!r} cancellable=true resumeAction=unreal_task_cancel"
    )


def terminal_status_for_plan(
    *,
    task_kind: str,
    mode: str,
    executable_slice_count: int,
    slice_state: dict[str, Any],
) -> str:
    if task_kind == "compile_fix" or mode in {"compile_fix", "module_fix", "reflection_fix"}:
        if executable_slice_count == 0 or slice_state.get("completed"):
            return "COMPILE_FIX_COMPLETE"
    if slice_state.get("completed"):
        return "PLAN_COMPLETE"
    return "SLICE_BUILD_OK"
