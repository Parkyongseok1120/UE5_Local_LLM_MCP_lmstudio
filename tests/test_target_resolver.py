from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from rag_semantic import split_identifier  # noqa: E402
from target_resolver import resolve_symbol_target, target_tokens  # noqa: E402


def test_split_identifier_uses_camel_segments_instead_of_zero_width_matches():
    parts = split_identifier("UCPlayerCharacterAnimInstance")
    assert {"Player", "Character", "Anim", "Instance"}.issubset(parts)


def test_player_animinstance_phrase_resolves_unique_unreal_class():
    result = resolve_symbol_target(
        "Player Animinstance C++ 클래스",
        [
            {
                "symbol_name": "UCPlayerCharacterAnimInstance",
                "qualified_name": "Game::UCPlayerCharacterAnimInstance",
                "file_path": "Source/Game/Animation/UCPlayerCharacterAnimInstance.h",
            },
            {
                "symbol_name": "UCEnemyCharacterAnimInstance",
                "file_path": "Source/Game/Animation/UCEnemyCharacterAnimInstance.h",
            },
        ],
    )
    assert target_tokens("Player Animinstance C++ 클래스") == ["player", "anim", "instance"]
    assert result["status"] == "resolved"
    assert result["selected"]["symbol"] == "UCPlayerCharacterAnimInstance"
    assert result["selected"]["matchKind"] == "all_core_tokens"
    assert result["exact"] is False


def test_unreal_prefix_is_removed_before_acronym_camel_tokenization():
    assert target_tokens("UHTTPServer") == ["http", "server"]
    assert target_tokens("UserSettings") == ["user", "settings"]

    result = resolve_symbol_target(
        "HTTP Server",
        [
            {
                "symbol_name": "UHTTPServer",
                "file_path": "Source/Networking/UHTTPServer.h",
            },
            {
                "symbol_name": "UHTTPClient",
                "file_path": "Source/Networking/UHTTPClient.h",
            },
        ],
    )
    assert result["status"] == "resolved"
    assert result["selected"]["symbol"] == "UHTTPServer"


def test_two_letter_domain_token_is_not_mistaken_for_unreal_prefix():
    assert target_tokens("UI Manager") == ["ui", "manager"]
    assert target_tokens("AI Controller") == ["ai", "controller"]

    wrong_domain = resolve_symbol_target(
        "UI Manager",
        [
            {
                "symbol_name": "UAudioManager",
                "file_path": "Source/Audio/UAudioManager.h",
            }
        ],
    )
    assert wrong_domain["status"] == "not_found"
    assert wrong_domain["errorCode"] == "TARGET_NOT_FOUND"
    assert wrong_domain["selected"] is None


def test_prefix_stripped_exact_match_requires_real_unreal_type_spelling():
    unreal_type = resolve_symbol_target(
        "HTTP Server",
        [{"symbol_name": "UHTTPServer", "file_path": "Source/UHTTPServer.h"}],
    )
    assert unreal_type["status"] == "resolved"
    assert unreal_type["selected"]["matchKind"] == "exact_prefix_stripped_symbol"

    for query, ordinary_symbol in (
        ("ser Settings", "UserSettings"),
        ("pdate State", "UpdateState"),
    ):
        result = resolve_symbol_target(
            query,
            [
                {
                    "symbol_name": ordinary_symbol,
                    "file_path": f"Source/{ordinary_symbol}.h",
                }
            ],
        )
        assert result["status"] == "not_found"
        assert result["errorCode"] == "TARGET_NOT_FOUND"
        assert result["selected"] is None


def test_close_symbol_scores_fail_closed():
    result = resolve_symbol_target(
        "Player AnimInstance",
        [
            {"symbol_name": "UPlayerOneAnimInstance", "file_path": "One.h"},
            {"symbol_name": "UPlayerTwoAnimInstance", "file_path": "Two.h"},
        ],
    )
    assert result["status"] == "unresolved"
    assert result["errorCode"] == "TARGET_AMBIGUOUS"
    assert result["selected"] is None


def test_same_unqualified_symbol_in_two_modules_is_not_deduplicated():
    result = resolve_symbol_target(
        "Shared AnimInstance",
        [
            {
                "symbol_name": "USharedAnimInstance",
                "file_path": "Plugins/First/Source/First/USharedAnimInstance.h:12",
            },
            {
                "symbol_name": "USharedAnimInstance",
                "file_path": "Plugins/Second/Source/Second/USharedAnimInstance.cpp:44",
            },
        ],
    )

    assert result["status"] == "unresolved"
    assert result["errorCode"] == "TARGET_AMBIGUOUS"
    assert result["selected"] is None
    assert len(result["candidates"]) == 2


def test_write_target_requires_existing_project_file(tmp_path: Path):
    row = {
        "symbol_name": "UPlayerAnimInstance",
        "file_path": "Source/Game/UPlayerAnimInstance.h",
    }
    missing = resolve_symbol_target(
        "Player AnimInstance",
        [row],
        access="write",
        project_root=tmp_path,
    )
    assert missing["errorCode"] == "WRITE_TARGET_EVIDENCE_REQUIRED"

    target = tmp_path / "Source" / "Game" / "UPlayerAnimInstance.h"
    target.parent.mkdir(parents=True)
    target.write_text("class UPlayerAnimInstance;", encoding="utf-8")
    resolved = resolve_symbol_target(
        "Player AnimInstance",
        [row],
        access="write",
        project_root=tmp_path,
    )
    assert resolved["status"] == "resolved"
    assert resolved["writeEvidenceVerified"] is True


def test_write_target_preserves_windows_drive_colon_and_removes_source_locator(
    tmp_path: Path, monkeypatch
):
    source = tmp_path / "Source" / "Game" / "UPlayerAnimInstance.h"
    source.parent.mkdir(parents=True)
    source.write_text("class UPlayerAnimInstance;", encoding="utf-8")
    windows_locator = r"C:\Game Root\Source\Game\UPlayerAnimInstance.h:42:7"

    original_resolve = Path.resolve

    def fake_resolve(path: Path, *args, **kwargs):
        if str(path).endswith(windows_locator.removesuffix(":42:7")):
            return source
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fake_resolve)
    result = resolve_symbol_target(
        "Player AnimInstance",
        [{"symbol_name": "UPlayerAnimInstance", "file_path": windows_locator}],
        access="write",
        project_root=tmp_path,
    )

    assert result["status"] == "resolved"
    assert result["selected"]["filePath"] == windows_locator
    assert result["writeEvidenceVerified"] is True
