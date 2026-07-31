#!/usr/bin/env python
"""Validate Unreal asset rename/move migration evidence before architecture writes."""

from __future__ import annotations

from typing import Any


def _game_path(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/")


def validate_asset_migration(value: Any) -> dict[str, Any]:
    contract = value if isinstance(value, dict) else {}
    issues: list[str] = []
    warnings: list[str] = []
    if not isinstance(value, dict):
        issues.append("assetMigration must be an object")
    moves = contract.get("moves") if isinstance(contract.get("moves"), list) else []
    if not moves:
        issues.append("assetMigration.moves must be a non-empty array")
    normalized_moves: list[dict[str, Any]] = []
    sources: set[str] = set()
    targets: set[str] = set()
    for index, item in enumerate(moves):
        if not isinstance(item, dict):
            issues.append(f"assetMigration.moves[{index}] must be an object")
            continue
        source = _game_path(item.get("from"))
        target = _game_path(item.get("to"))
        referencers = [
            _game_path(path)
            for path in (item.get("referencers") or [])
            if _game_path(path)
        ]
        scan_complete = item.get("referenceScanComplete") is True
        if not source.startswith("/Game/") or not target.startswith("/Game/"):
            issues.append(
                f"assetMigration.moves[{index}] from/to must be full /Game/... paths"
            )
        if source == target:
            issues.append(f"assetMigration.moves[{index}] source and target must differ")
        if source in sources:
            issues.append(f"duplicate asset migration source: {source}")
        if target in targets:
            issues.append(f"duplicate asset migration target: {target}")
        sources.add(source)
        targets.add(target)
        if not referencers and not scan_complete:
            issues.append(
                f"assetMigration.moves[{index}] needs referencers or referenceScanComplete=true"
            )
        normalized_moves.append(
            {
                "from": source,
                "to": target,
                "referencers": referencers,
                "referenceScanComplete": scan_complete,
            }
        )
    if sources & targets:
        warnings.append(
            "migration chain detected; execute moves in dependency order and verify redirectors"
        )
    snapshot_hash = str(contract.get("assetRegistrySnapshotHash") or "").strip()
    if not snapshot_hash:
        issues.append("assetMigration.assetRegistrySnapshotHash is required")
    redirector_policy = str(contract.get("redirectorPolicy") or "").strip()
    if redirector_policy not in {"fixup_then_delete", "retain_compatibility"}:
        issues.append(
            "assetMigration.redirectorPolicy must be fixup_then_delete or retain_compatibility"
        )
    cook_validation = [
        str(item).strip()
        for item in (contract.get("cookValidation") or [])
        if str(item).strip()
    ]
    rollback_plan = [
        str(item).strip()
        for item in (contract.get("rollbackPlan") or [])
        if str(item).strip()
    ]
    if not cook_validation:
        issues.append("assetMigration.cookValidation must be a non-empty array")
    if not rollback_plan:
        issues.append("assetMigration.rollbackPlan must be a non-empty array")
    return {
        "ok": not issues,
        "moves": normalized_moves,
        "assetRegistrySnapshotHash": snapshot_hash,
        "redirectorPolicy": redirector_policy,
        "cookValidation": cook_validation,
        "rollbackPlan": rollback_plan,
        "issues": issues,
        "warnings": warnings,
        "executionOrder": [
            "capture fresh Asset Registry/editor metadata snapshot",
            "move or rename assets without deleting redirectors",
            "resave declared referencers and maps",
            "run redirector fix-up according to policy",
            "cook/package and load affected maps",
            "delete compatibility redirectors only after proof",
        ],
        "implementationGate": {
            "writesAllowed": not issues,
            "reason": (
                "asset migration evidence is complete"
                if not issues
                else "asset migration evidence is incomplete"
            ),
        },
        "proofBoundary": (
            "This validates migration coverage and sequencing. It does not prove that "
            "Editor asset moves, redirector fix-up, cooking, or map loads succeeded."
        ),
    }
