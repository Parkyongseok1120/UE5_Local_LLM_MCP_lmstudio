"""Tests for the code sketch claim validator (anti-hallucination for chat sketches)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from code_sketch_claim_validate import (  # noqa: E402
    MAX_SKETCH_CHARS,
    _lookup_many_exact,
    extract_member_call_claims,
    extract_member_calls,
    extract_symbols,
    validate_sketch,
)
from unreal_api_denylist import check_denylist  # noqa: E402

# A path that does not exist, so validate_sketch takes the deterministic
# "index not found" branch (no dependency on a built RAG index).
NO_INDEX = Path(__file__).resolve().parent / "_no_such_index.sqlite"


def test_extract_symbols_finds_unreal_types():
    syms = extract_symbols("ULevelSequencePlayer* P; UMovieSceneSequence* S; FTransform T;")
    assert "ULevelSequencePlayer" in syms
    assert "UMovieSceneSequence" in syms


def test_extract_member_calls():
    calls = extract_member_calls("P->SetRestoreState(true); Actor->GetActorTransform();")
    assert "SetRestoreState" in calls
    assert "GetActorTransform" in calls


def test_extract_member_call_claims_infers_receiver_types():
    claims = extract_member_call_claims(
        "UMyComponent* Comp; Comp->SetState(true); UGameplayStatics::GetPlayerController(World, 0);"
    )
    assert {
        (claim["receiverType"], claim["member"], claim["callKind"])
        for claim in claims
    } == {
        ("UMyComponent", "SetState", "member"),
        ("UGameplayStatics", "GetPlayerController", "static"),
    }


def test_denylist_flags_known_hallucinations():
    terms = {hit["term"] for hit in check_denylist("set bRestoreState and override OnWorldDestroyed")}
    assert "brestorestate" in terms
    assert "onworlddestroyed" in terms


def test_validate_sketch_flags_known_bad():
    result = validate_sketch("player->SetRestoreState(true); // keep end position", NO_INDEX)
    assert result["ok"] is False
    assert result["knownBadCount"] >= 1
    verdicts = {r["symbol"]: r["verdict"] for r in result["results"]}
    assert verdicts.get("setrestorestate") == "known_bad"
    assert "known_bad" in result["verdictSummary"]


def test_validate_sketch_marks_unknown_symbols_unverified_without_index():
    result = validate_sketch("UFrobnicatorWidgetXYZ* w = NewObject<UFrobnicatorWidgetXYZ>();", NO_INDEX)
    assert result["indexExists"] is False
    assert result["ok"] is False
    assert result["unverifiedCount"] >= 1


def test_validate_sketch_ok_when_only_common_safe_symbols():
    result = validate_sketch("AActor* a; UWorld* w; FString name;", NO_INDEX)
    assert result["ok"] is True
    assert result["symbolCount"] == 0


def test_oversized_sketch_fails_before_index_or_graph_work(monkeypatch):
    monkeypatch.setattr(
        "code_sketch_claim_validate._resolve_index",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("index resolution must not run")),
    )
    result = validate_sketch("UTooLargeType " + ("x" * MAX_SKETCH_CHARS), NO_INDEX)
    assert result["ok"] is False
    assert result["errorCode"] == "SKETCH_TOO_LARGE"
    assert result["indexLookupMode"] == "not_started"
    assert result["results"] == []


def test_exact_batch_lookup_uses_one_indexed_query_for_many_symbols(tmp_path: Path):
    index = tmp_path / "rag.sqlite"
    connection = sqlite3.connect(index)
    connection.execute(
        "create table chunks (symbol_name text, symbol_kind text, title text, locator text, "
        "source text, project text, module_name text, metadata_json text)"
    )
    connection.execute("create index chunks_symbol_name_idx on chunks(symbol_name)")
    connection.execute(
        "insert into chunks values (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "UKnownType",
            "class",
            "UKnownType class",
            "/Engine/KnownType.h",
            "unreal_symbol",
            "Engine",
            "Runtime",
            "{}",
        ),
    )
    connection.commit()
    connection.close()

    rows, error, query_count = _lookup_many_exact(
        index,
        ["UKnownType", *(f"UUnknown{value:03d}" for value in range(60))],
    )
    assert error == ""
    assert query_count == 1
    assert list(rows) == ["uknowntype"]
    assert rows["uknowntype"][0]["evidence_source"] == "rag_index_exact"


def test_validate_sketch_batches_all_index_candidates_once(monkeypatch, tmp_path: Path):
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"placeholder")
    calls: list[list[str]] = []

    def fake_exact(_index, symbols, *, top_k=5):
        calls.append(list(symbols))
        return {}, "", 1

    monkeypatch.setattr("code_sketch_claim_validate._lookup_many_exact", fake_exact)
    sketch = "\n".join(f"UType{value:03d}* Value{value};" for value in range(60))
    result = validate_sketch(sketch, index)
    assert len(calls) == 1
    assert len(calls[0]) == 60
    assert result["indexLookupQueryCount"] == 1
    assert result["unverifiedCount"] == 60


def test_live_project_hallucinations_return_replacements():
    result = validate_sketch(
        "MoveComp->DisableGravity(); FString Name = World->GetURL();",
        NO_INDEX,
    )
    known_bad = {
        item["symbol"]: item
        for item in result["results"]
        if item["verdict"] == "known_bad"
    }
    assert "disablegravity" in known_bad
    assert "world_geturl" in known_bad
    assert "GravityScale" in known_bad["disablegravity"]["replacement"]
    assert "GetCurrentLevelName" in known_bad["world_geturl"]["replacement"]
    assert result["results"][0]["verdict"] == "known_bad"


def test_denylist_flags_replicatevariable_token():
    terms = {hit["term"] for hit in check_denylist("ReplicateVariable();")}
    assert "replicatevariable" in terms


def test_denylist_flags_giveability_free_call():
    terms = {hit["term"] for hit in check_denylist("GiveAbility();")}
    assert "giveability_free" in terms


def test_denylist_flags_geditor_without_with_editor():
    terms = {hit["term"] for hit in check_denylist("GEditor->GetEditorWorldContext();")}
    assert "geditor_runtime" in terms


def test_denylist_allows_valid_gas_usage():
    code = "AbilitySystemComponent->GiveAbility(FGameplayAbilitySpec(AbilityClass, 1, INDEX_NONE, this));"
    terms = {hit["term"] for hit in check_denylist(code)}
    assert "giveability_free" not in terms
    assert "giveability" not in terms


def test_denylist_allows_valid_try_activate_ability():
    code = "AbilitySystemComponent->TryActivateAbility(Handle);"
    terms = {hit["term"] for hit in check_denylist(code)}
    assert "tryactivateability_free" not in terms


def test_denylist_allows_gameplay_statics_get_player_controller():
    code = "APlayerController* PC = UGameplayStatics::GetPlayerController(World, 0);"
    terms = {hit["term"] for hit in check_denylist(code)}
    assert "getplayercontroller_zero_arg" not in terms


def test_denylist_allows_spawn_emitter_at_location():
    code = "UGameplayStatics::SpawnEmitterAtLocation(World, Template, Location);"
    terms = {hit["term"] for hit in check_denylist(code)}
    assert "spawnemitteratlocation_zero_arg" not in terms


def test_denylist_allows_create_widget_with_context():
    code = "UUserWidget* Widget = CreateWidget<UMyWidget>(PlayerController, WidgetClass);"
    terms = {hit["term"] for hit in check_denylist(code)}
    assert "createwidget_no_context" not in terms


def test_denylist_allows_add_to_viewport_member_call():
    code = "Widget->AddToViewport();"
    terms = {hit["term"] for hit in check_denylist(code)}
    assert "addtoviewport_free" not in terms


def test_denylist_allows_has_authority_in_actor_method():
    code = "void AMyActor::Tick(float DeltaTime) { if (HasAuthority()) { } }"
    terms = {hit["term"] for hit in check_denylist(code)}
    assert not terms


def test_denylist_allows_geditor_with_with_editor():
    code = "#if WITH_EDITOR\nGEditor->GetEditorWorldContext();\n#endif"
    terms = {hit["term"] for hit in check_denylist(code)}
    assert "geditor_runtime" not in terms


def test_denylist_warns_zero_arg_get_player_controller():
    terms = {hit["term"] for hit in check_denylist("GetPlayerController();")}
    assert "getplayercontroller_zero_arg" in terms


def test_denylist_allows_member_get_player_controller():
    code = "Controller = PlayerState->GetPlayerController();"
    terms = {hit["term"] for hit in check_denylist(code)}
    assert "getplayercontroller_zero_arg" not in terms


def test_denylist_allows_dot_get_player_controller():
    code = "Controller = PlayerState.GetPlayerController();"
    terms = {hit["term"] for hit in check_denylist(code)}
    assert "getplayercontroller_zero_arg" not in terms


def test_denylist_warns_gameplay_statics_zero_arg_get_player_controller():
    terms = {hit["term"] for hit in check_denylist("UGameplayStatics::GetPlayerController();")}
    assert "getplayercontroller_zero_arg" in terms


def test_denylist_allows_spaced_arrow_get_player_controller():
    code = "Controller = PlayerState -> GetPlayerController();"
    terms = {hit["term"] for hit in check_denylist(code)}
    assert "getplayercontroller_zero_arg" not in terms


def test_denylist_ignores_get_player_controller_in_comment():
    code = "// Never call GetPlayerController() here"
    terms = {hit["term"] for hit in check_denylist(code)}
    assert "getplayercontroller_zero_arg" not in terms


def test_denylist_ignores_get_player_controller_in_text_macro():
    code = 'FString S = TEXT("GetPlayerController()");'
    terms = {hit["term"] for hit in check_denylist(code)}
    assert "getplayercontroller_zero_arg" not in terms


def test_denylist_warns_geditor_when_with_editor_only_in_comment():
    code = "// WITH_EDITOR\nvoid Run() { GEditor->SelectActor(Actor, true); }"
    terms = {hit["term"] for hit in check_denylist(code)}
    assert "geditor_runtime" in terms


def test_denylist_allows_geditor_after_guard_ends_only_inside_block():
    code = (
        "#if WITH_EDITOR\n"
        "void Edit() { GEditor->SelectActor(Actor, true); }\n"
        "#endif\n"
        "void Run() { GEditor->SelectActor(Actor, true); }"
    )
    terms = {hit["term"] for hit in check_denylist(code)}
    assert "geditor_runtime" in terms


def test_denylist_allows_spaced_member_add_to_viewport():
    code = "Widget -> AddToViewport();"
    terms = {hit["term"] for hit in check_denylist(code)}
    assert "addtoviewport_free" not in terms


def test_denylist_allows_spaced_member_is_server():
    code = "if (World -> IsServer()) {}"
    terms = {hit["term"] for hit in check_denylist(code)}
    assert "isserver_free" not in terms


def test_denylist_allows_spaced_member_get_net_mode():
    code = "Mode = World -> GetNetMode();"
    terms = {hit["term"] for hit in check_denylist(code)}
    assert "getnetmode_free" not in terms


def test_denylist_flags_unqualified_current_delta_time():
    terms = {hit["term"] for hit in check_denylist("Value += GetCurrentDeltaTime();")}
    assert "getcurrentdeltatime_unqualified" in terms


def test_denylist_flags_get_character_movement_on_apawn_receiver():
    code = "void Update(APawn* Pawn) { Pawn->GetCharacterMovement()->StopMovementImmediately(); }"
    terms = {hit["term"] for hit in check_denylist(code)}
    assert "apawn_getcharactermovement" in terms


def test_validate_sketch_skips_locally_declared_types_and_delegate(tmp_path: Path):
    sketch = """
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FOnStaminaChangedSignature, float, Value);
UCLASS()
class UStaminaComponent : public UActorComponent
{
    GENERATED_BODY()
public:
    UPROPERTY(BlueprintAssignable)
    FOnStaminaChangedSignature OnStaminaChanged;
};
"""

    result = validate_sketch(sketch, NO_INDEX)
    result_symbols = {item["symbol"] for item in result["results"]}

    assert result["localDeclarationCount"] == 2
    assert "UStaminaComponent" not in result_symbols
    assert "FOnStaminaChangedSignature" not in result_symbols
    assert "UCLASS" not in result_symbols
    assert "UPROPERTY" not in result_symbols


def test_member_method_on_different_receiver_is_not_verified(monkeypatch, tmp_path: Path):
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"index")

    def fake_lookup(_index, symbols, *, top_k=5):
        return {
            "setstate": [
                {
                    "symbol_name": "SetState",
                    "symbol_kind": "function",
                    "qualified_name": "UDifferentComponent::SetState",
                }
            ]
        }, "", 1

    monkeypatch.setattr("code_sketch_claim_validate._lookup_many_exact", fake_lookup)
    result = validate_sketch(
        "class UMyComponent {}; UMyComponent* Comp; Comp->SetState(true);",
        index,
    )
    method = next(item for item in result["results"] if item["symbol"] == "SetState")
    assert method["verdict"] == "unverified"
    assert method["receiverType"] == "UMyComponent"
    assert result["ok"] is False


def test_member_method_requires_exact_receiver_owner(monkeypatch, tmp_path: Path):
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"index")

    def fake_lookup(_index, symbols, *, top_k=5):
        return {
            "setstate": [
                {
                    "symbol_name": "SetState",
                    "symbol_kind": "function",
                    "qualified_name": "UMyComponent::SetState",
                }
            ]
        }, "", 1

    monkeypatch.setattr("code_sketch_claim_validate._lookup_many_exact", fake_lookup)
    result = validate_sketch(
        "class UMyComponent {}; UMyComponent* Comp; Comp->SetState(true);",
        index,
    )
    method = next(item for item in result["results"] if item["symbol"] == "SetState")
    assert method["verdict"] == "verified"
    assert method["receiverType"] == "UMyComponent"
    assert result["ok"] is True


def test_ownerless_exact_member_evidence_is_weak_and_fail_closed(monkeypatch, tmp_path: Path):
    index = tmp_path / "rag.sqlite"
    index.write_bytes(b"index")

    monkeypatch.setattr(
        "code_sketch_claim_validate._lookup_many_exact",
        lambda *_args, **_kwargs: (
            {"setstate": [{"symbol_name": "SetState", "symbol_kind": "function"}]},
            "",
            1,
        ),
    )
    result = validate_sketch(
        "class UMyComponent {}; UMyComponent* Comp; Comp->SetState(true);",
        index,
    )
    method = next(item for item in result["results"] if item["symbol"] == "SetState")
    assert method["verdict"] == "weak"
    assert result["weakCount"] == 1
    assert result["ok"] is False


def test_project_graph_verifies_project_member_without_rag_index():
    graph = {
        "symbols": [
            {
                "symbol_name": "UMyComponent",
                "symbol_kind": "class",
                "base_class": "UActorComponent",
                "file_path": "Source/Game/MyComponent.h",
                "line_start": 8,
            },
            {
                "symbol_name": "SetState",
                "symbol_kind": "function",
                "qualified_name": "UMyComponent::SetState",
                "file_path": "Source/Game/MyComponent.cpp",
                "line_start": 14,
            },
        ]
    }
    result = validate_sketch(
        "UMyComponent* Comp; Comp->SetState(true);",
        NO_INDEX,
        graph=graph,
    )
    method = next(item for item in result["results"] if item["symbol"] == "SetState")
    assert method["verdict"] == "verified"
    assert result["projectGraphAvailable"] is True
    assert result["ok"] is True


def test_project_graph_accepts_member_declared_on_local_base_class():
    graph = {
        "symbols": [
            {
                "symbol_name": "UMyComponent",
                "symbol_kind": "class",
                "base_class": "UMyBaseComponent",
            },
            {
                "symbol_name": "UMyBaseComponent",
                "symbol_kind": "class",
                "base_class": "UActorComponent",
            },
            {
                "symbol_name": "SetState",
                "symbol_kind": "function",
                "qualified_name": "UMyBaseComponent::SetState",
                "file_path": "Source/Game/MyBaseComponent.cpp",
                "line_start": 14,
            },
        ]
    }
    result = validate_sketch(
        "UMyComponent* Comp; Comp->SetState(true);",
        NO_INDEX,
        graph=graph,
    )
    method = next(item for item in result["results"] if item["symbol"] == "SetState")
    assert method["verdict"] == "verified"
    assert result["ok"] is True
