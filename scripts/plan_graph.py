#!/usr/bin/env python
"""Mutable plan graph, plan deltas, and resumable working state."""

from __future__ import annotations

import copy
from typing import Any


NODE_STATUSES = frozenset(
    {"pending", "active", "completed", "blocked", "invalidated", "skipped_with_evidence", "failed"}
)


def apply_plan_delta(state: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    """Apply invalidate/add/reorder atomically; increment planRevision."""
    next_state = copy.deepcopy(state)
    nodes = list(next_state.get("nodes") or next_state.get("slices") or [])
    node_map = {str(node.get("sliceId") or node.get("id") or ""): node for node in nodes}

    for node_id in delta.get("invalidate") or []:
        node = node_map.get(str(node_id))
        if node:
            node["status"] = "invalidated"
            node["completionReason"] = ""

    for spec in delta.get("add") or []:
        if isinstance(spec, str):
            node_id = spec.strip()
        else:
            node_id = str(spec.get("id") or spec.get("sliceId") or "")
        if node_id and node_id not in node_map:
            node_map[node_id] = {
                "sliceId": node_id,
                "status": "pending",
                "attemptCount": 0,
                "fileHashes": {},
                "proofLevel": "",
            }

    order = [str(item) for item in (delta.get("reorder") or []) if str(item) in node_map]
    if order:
        remaining = [node_id for node_id in node_map if node_id not in order]
        ordered_ids = order + remaining
        nodes = [node_map[node_id] for node_id in ordered_ids]
    else:
        nodes = list(node_map.values())

    next_state["nodes"] = nodes
    next_state["slices"] = nodes
    next_state["planRevision"] = int(next_state.get("planRevision") or 1) + 1
    next_state["completed"] = False
    if delta.get("writeGate"):
        next_state["writeGate"] = delta["writeGate"]
    next_state["lastDeltaReason"] = str(delta.get("reason") or "")
    return next_state


def merge_evidence_branch(branches: list[dict[str, Any]]) -> dict[str, Any]:
    claim = ""
    evidence: list[Any] = []
    missing: list[str] = []
    conflicts: list[str] = []
    for branch in branches:
        claim = claim or str(branch.get("claim") or "")
        evidence.extend(branch.get("evidence") or [])
        missing.extend(branch.get("missingEvidence") or [])
        conflicts.extend(branch.get("conflicts") or [])
    unresolved = bool(missing or conflicts)
    return {
        "claim": claim,
        "verdict": "unsupported" if unresolved else "supported",
        "evidence": evidence,
        "missingEvidence": sorted(set(missing)),
        "conflicts": sorted(set(conflicts)),
        "writesAllowed": not unresolved,
    }
