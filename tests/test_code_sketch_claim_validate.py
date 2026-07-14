"""Tests for the code sketch claim validator (anti-hallucination for chat sketches)."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from code_sketch_claim_validate import (  # noqa: E402
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
