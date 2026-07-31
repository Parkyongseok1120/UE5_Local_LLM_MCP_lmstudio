from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from asset_migration_contract import validate_asset_migration  # noqa: E402


def test_asset_migration_requires_referencer_cook_and_rollback_evidence() -> None:
    result = validate_asset_migration(
        {
            "moves": [
                {
                    "from": "/Game/UI/WBP_Old",
                    "to": "/Game/UI/WBP_New",
                    "referencers": ["/Game/Maps/Lobby"],
                }
            ],
            "assetRegistrySnapshotHash": "abc123",
            "redirectorPolicy": "fixup_then_delete",
            "cookValidation": ["cook Windows target", "load Lobby"],
            "rollbackPlan": ["restore WBP_Old and resave Lobby"],
        }
    )

    assert result["ok"] is True
    assert result["implementationGate"]["writesAllowed"] is True
    assert "delete compatibility redirectors only after proof" in result["executionOrder"]


def test_asset_migration_fails_closed_without_reference_scan() -> None:
    result = validate_asset_migration(
        {
            "moves": [{"from": "/Game/A", "to": "/Game/B"}],
            "redirectorPolicy": "fixup_then_delete",
        }
    )

    assert result["ok"] is False
    assert result["implementationGate"]["writesAllowed"] is False
    assert any("referencers" in issue for issue in result["issues"])
