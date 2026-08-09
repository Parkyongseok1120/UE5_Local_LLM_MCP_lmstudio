from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from domain_validators import validate_replication_ownership_conservative  # noqa: E402


def test_replication_unknown_ownership_is_info(tmp_path: Path) -> None:
    root = tmp_path / "Demo"
    header = root / "Net.h"
    header.parent.mkdir(parents=True)
    header.write_text("class ANet : public AActor { UFUNCTION(Server, Reliable) void ServerUse(); };\n", encoding="utf-8")
    findings = validate_replication_ownership_conservative(header, header.read_text(encoding="utf-8"), root)
    assert findings and findings[0].code == "REPLICATION_OWNERSHIP_UNKNOWN"


def test_server_rpc_on_game_state_is_blocking_error(tmp_path: Path) -> None:
    root = tmp_path / "Demo"
    header = root / "GameState.h"
    header.parent.mkdir(parents=True)
    header.write_text(
        "class ABoardState : public AGameStateBase { "
        "UFUNCTION(Server, Reliable) void ServerPlaceStone(); };\n",
        encoding="utf-8",
    )
    findings = validate_replication_ownership_conservative(
        header, header.read_text(encoding="utf-8"), root
    )
    assert findings and findings[0].severity == "error"
    assert findings[0].code == "SERVER_RPC_ON_NON_OWNED_GAMESTATE"
