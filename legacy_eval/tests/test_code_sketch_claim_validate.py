"""Archived workflow code-sketch gate tests."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from code_sketch_claim_validate import (  # noqa: E402
    MAX_SKETCH_CHARS,
    _cpp_types_compatible,
    _numeric_conversion_kind,
    _lookup_many_exact,
    _normalized_cpp_type,
    extract_member_call_claims,
    extract_member_calls,
    extract_local_declarations,
    extract_symbols,
    validate_sketch,
)
from unreal_api_denylist import check_denylist  # noqa: E402
import engine_header_evidence  # noqa: E402
from engine_header_evidence import _signature_contracts  # noqa: E402

# A path that does not exist, so validate_sketch takes the deterministic
# "index not found" branch (no dependency on a built RAG index).
NO_INDEX = Path(__file__).resolve().parent / "_no_such_index.sqlite"


@pytest.mark.parametrize(
    ("host_platform", "expected_key"),
    [
        ("win32", "synthetic39.h"),
        ("linux", "Synthetic39.h"),
        ("darwin", "Synthetic39.h"),
    ],
)
def test_engine_header_catalog_python_fallback_avoids_per_file_resolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    host_platform: str,
    expected_key: str,
) -> None:
    header_root = tmp_path / "Engine" / "Source" / "Runtime" / "Core" / "Public"
    header_root.mkdir(parents=True)
    for index in range(40):
        (header_root / f"Synthetic{index}.h").write_text(
            f"struct FSynthetic{index} {{}};\n",
            encoding="utf-8",
        )
    engine_header_evidence.clear_engine_header_catalog_cache()
    monkeypatch.setattr(engine_header_evidence.shutil, "which", lambda _name: None)

    def reject_slow_resolve(*_args, **_kwargs):
        raise AssertionError("trusted os.walk catalog must not resolve each header")

    monkeypatch.setattr(engine_header_evidence, "_contained", reject_slow_resolve)

    catalog = engine_header_evidence._header_catalog(
        tmp_path,
        host_platform=host_platform,
    )

    assert len(catalog) == 40
    assert expected_key in catalog
    engine_header_evidence.clear_engine_header_catalog_cache()


def test_large_engine_tree_without_rg_skips_unbounded_python_content_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "Engine" / "Source" / "Runtime"
    runtime.mkdir(parents=True)
    catalog = {
        f"synthetic{index}.h": [runtime / f"Synthetic{index}.h"]
        for index in range(
            engine_header_evidence._MAX_PYTHON_DECLARATION_SCAN_FILES + 1
        )
    }
    engine_header_evidence.clear_engine_header_catalog_cache()
    monkeypatch.setattr(engine_header_evidence.shutil, "which", lambda _name: None)

    def reject_file_scan(*_args, **_kwargs):
        raise AssertionError("large SDK fallback must escalate instead of reading every header")

    monkeypatch.setattr(engine_header_evidence, "_contained", reject_file_scan)
    resolved, inspected = engine_header_evidence._discover_type_declaration_paths(
        tmp_path,
        catalog,
        ["FTypeDeclaredInAnotherHeader"],
        max_header_chars=1_000_000,
    )

    assert resolved == {"FTypeDeclaredInAnotherHeader": []}
    assert inspected == 0
    engine_header_evidence.clear_engine_header_catalog_cache()


def test_cpp_type_normalization_preserves_multiword_builtin_types():
    assert _normalized_cpp_type("long double") == "long double"
    assert _normalized_cpp_type("unsigned long long") == "unsigned long long"
    assert _normalized_cpp_type("const long double Value") == "long double"
    assert _normalized_cpp_type("unsigned int Count") == "unsigned int"


def test_unreal_real_aliases_are_numeric_compatible_with_builtin_floats():
    assert _cpp_types_compatible("FRealSingle", "float") is True
    assert _cpp_types_compatible("float", "FRealSingle") is True
    assert _cpp_types_compatible("FRealDouble", "double") is True
    assert _cpp_types_compatible("double", "FReal") is True


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        ("int8", "int32"),
        ("uint32", "int64"),
        ("float", "double"),
        ("int32", "double"),
        ("uint16", "float"),
        ("long", "int64"),
    ],
)
def test_numeric_widening_is_compatible_on_all_supported_platforms(actual, expected):
    assert _numeric_conversion_kind(actual, expected) == "widening"
    assert _cpp_types_compatible(actual, expected) is True


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        ("double", "float"),
        ("int64", "int8"),
        ("uint64", "int32"),
        ("int32", "uint32"),
        ("uint64", "double"),
        ("long", "int32"),
    ],
)
def test_numeric_narrowing_is_not_semantically_compatible(actual, expected):
    assert _numeric_conversion_kind(actual, expected) == "narrowing"
    assert _cpp_types_compatible(actual, expected) is False


def test_pointer_types_never_gain_numeric_conversion_compatibility():
    assert _numeric_conversion_kind("int64*", "int64") == "incompatible"
    assert _cpp_types_compatible("int64*", "int64") is False


def test_extract_symbols_finds_unreal_types():
    syms = extract_symbols("ULevelSequencePlayer* P; UMovieSceneSequence* S; FTransform T;")
    assert "ULevelSequencePlayer" in syms
    assert "UMovieSceneSequence" in syms


def test_extract_symbols_does_not_duplicate_prefixed_owned_method_as_type():
    syms = extract_symbols("double Value = FMath::FInterpTo(A, B, Dt, Speed);")

    assert "FMath" in syms
    assert "FInterpTo" not in syms


def test_actor_suffix_member_name_is_not_an_unreal_type_claim():
    syms = extract_symbols(
        "BoardActor = Board; AGomokuBoardActor* TypedBoardActor = nullptr;"
    )

    assert "BoardActor" not in syms
    assert "TypedBoardActor" not in syms
    assert syms == ["AGomokuBoardActor"]


def test_legacy_input_enum_value_is_not_an_unreal_type_claim():
    syms = extract_symbols(
        "InputComponent->BindAction(Name, IE_Pressed, this, &AThing::OnClick); "
        "FObjectInitializer Initializer;"
    )

    assert "IE_Pressed" not in syms
    assert syms == ["AThing", "FObjectInitializer"]


def test_inherited_input_component_bindings_are_known_receiver_calls():
    claims = extract_member_call_claims(
        'InputComponent->BindAction("Click", IE_Pressed, this, &AThing::OnClick);'
    )

    assert claims == []


def test_dynamic_delegate_binding_macros_are_not_index_symbol_claims():
    claims = extract_member_call_claims(
        """
RestartButton->OnClicked.AddDynamic(this, &WGomokuHUD::OnRestartClicked);
Source->OnChanged.AddUniqueDynamic(this, &UListener::OnChanged);
Source->OnChanged.Broadcast(Value);
Source->OnChanged.RemoveDynamic(this, &UListener::OnChanged);
Source->OnChanged.IsAlreadyBound(this, &UListener::OnChanged);
"""
    )

    assert not any(
        claim["member"]
        in {"AddDynamic", "AddUniqueDynamic", "Broadcast", "RemoveDynamic", "IsAlreadyBound"}
        for claim in claims
    )


def test_add_lambda_is_not_treated_as_a_dynamic_delegate_primitive():
    claims = extract_member_call_claims(
        "RestartButton->OnClicked.AddLambda([this]() { OnRestartClicked(); });"
    )

    assert any(claim["member"] == "AddLambda" for claim in claims)


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
    } == {("UMyComponent", "SetState", "member")}


def test_qualified_function_definition_is_not_a_static_api_claim():
    claims = extract_member_call_claims(
        """
void AGomokuGameState::AdvanceTurn()
{
    UGameplayStatics::GetPlayerController(World, 0);
}
"""
    )

    assert {
        (claim["receiverType"], claim["member"], claim["callKind"])
        for claim in claims
    } == set()


def test_paired_declaration_context_types_cpp_member_receivers():
    claims = extract_member_call_claims(
        """
void AGomokuGameState::OnStonePlaced()
{
    if (RuleEngineRef.IsValid())
    {
        RuleEngineRef->IsGameWon(WinnerId);
    }
}
""",
        declaration_context=(
            "class AGomokuGameState { "
            "TWeakObjectPtr<UGomokuRuleEngine> RuleEngineRef; };"
        ),
    )

    assert {
        (claim["receiverType"], claim["member"], claim["callKind"])
        for claim in claims
    } == {("UGomokuRuleEngine", "IsGameWon", "member")}


def test_paired_declaration_context_accepts_elaborated_template_pointer_type():
    claims = extract_member_call_claims(
        "GS->HandlePlaceStone(0, FIntPoint(1, 2));",
        declaration_context="TObjectPtr<class AGomokuGameState> GS;",
    )

    assert {
        (claim["receiverType"], claim["member"], claim["callKind"])
        for claim in claims
    } == {("AGomokuGameState", "HandlePlaceStone", "member")}


def test_common_container_and_engine_members_do_not_create_false_blockers():
    claims = extract_member_call_claims(
        """
PlayerRemainingTimes.FindOrAdd(1);
PlayerRemainingTimes.Reset();
StoneInstances->AddInstance(Transform);
StoneInstances->ClearInstances();
StoneInstances->GetInstanceCount();
StoneInstances->GetInstanceTransform(0, Transform, true);
StoneInstances->GetStaticMesh();
StoneInstances->SetCollisionEnabled(ECollisionEnabled::QueryOnly);
StoneInstances->SetVisibility(true);
StoneInstances->IsValid();
FMath::Abs(Value);
FMath::RoundToInt(Value);
Transform.SetScale3D(FVector(1.f));
Transform.SetLocation(FVector::ZeroVector);
""",
        declaration_context="""
TMap<int32, float> PlayerRemainingTimes;
TSubobjectPtr<UInstancedStaticMeshComponent> StoneInstances;
FTransform Transform;
""",
    )

    assert {
        (claim["receiverType"], claim["member"], claim["callKind"])
        for claim in claims
    } == {("UInstancedStaticMeshComponent", "IsValid", "member")}


def test_concise_container_field_sketch_does_not_force_symbol_lookup_loop():
    claims = extract_member_call_claims(
        """
ActivePlayerIndices.Reset();
ActivePlayerIndices.Add(PlayerIndex);
const int32 Count = ActivePlayerIndices.Num();
"""
    )

    assert claims == []


def test_collision_query_params_and_scene_query_stat_are_common_engine_primitives():
    result = validate_sketch(
        "FCollisionQueryParams Params(SCENE_QUERY_STAT(BoardClick), false, this);",
        NO_INDEX,
    )

    assert result["ok"] is True
    assert result["unverifiedCount"] == 0


def test_stable_hit_result_and_gameplay_statics_helpers_are_not_weak_claims():
    claims = extract_member_call_claims(
        "FHitResult Hit; AActor* Actor = Hit.GetActor(); "
        "AGameStateBase* State = UGameplayStatics::GetGameState(this); "
        "UGameplayStatics::DeprojectScreenToWorld(PC, ScreenPosition, WorldPosition, WorldDirection);"
    )

    assert claims == []


def test_invented_deprojection_helper_is_known_bad_with_replacement():
    result = validate_sketch(
        "FVector Start = DeprojectScreenPositionToFVector(ScreenPosition);",
        NO_INDEX,
    )

    assert result["ok"] is False
    bad = next(row for row in result["results"] if row["verdict"] == "known_bad")
    assert bad["symbol"] == "deprojectscreenpositiontofvector"
    assert "DeprojectMousePositionToWorld" in bad["replacement"]


def test_invented_deprojection_to_fov_is_known_bad_with_exact_replacement():
    result = validate_sketch(
        "PC->DeprojectScreenToWorldToFov(ScreenX, ScreenY, Location, Direction);",
        NO_INDEX,
        declaration_context="APlayerController* PC;",
    )

    assert result["ok"] is False
    bad = next(row for row in result["results"] if row["verdict"] == "known_bad")
    assert bad["symbol"] == "deprojectscreentoworldtofov"
    assert "DeprojectScreenPositionToWorld" in bad["replacement"]


def test_invented_scene_delegate_macro_is_known_bad_with_exact_replacement():
    result = validate_sketch(
        'FCollisionQueryParams Params(SCENE_DELEGATE_NAME("BoardClick"), false, this);',
        NO_INDEX,
    )

    assert result["ok"] is False
    bad = next(row for row in result["results"] if row["verdict"] == "known_bad")
    assert bad["symbol"] == "scene_delegate_name"
    assert "SCENE_QUERY_STAT" in bad["replacement"]


def test_click_trace_primitives_are_known_receiver_calls():
    claims = extract_member_call_claims(
        """
FCollisionQueryParams Params;
Params.AddIgnoredActor(this);
FVector WorldDir;
FVector Normalized = WorldDir.GetSafeNormal();
APlayerController* PC;
PC->DeprojectScreenPositionToWorld(ScreenX, ScreenY, WorldLoc, WorldDir);
"""
    )

    assert claims == []


def test_world_line_trace_primitives_are_known_receiver_calls():
    claims = extract_member_call_claims(
        """
UWorld* World;
FHitResult Hit;
FCollisionQueryParams Params(SCENE_QUERY_STAT(BoardClick), false, this);
if (World->IsGameWorld())
{
    World->LineTraceSingleByChannel(Hit, Start, End, ECC_Visibility, Params);
}
"""
    )

    assert claims == []


def test_gameplay_player_lookup_and_screen_hit_are_known_receiver_calls():
    claims = extract_member_call_claims(
        """
APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0);
FHitResult Hit;
PC->GetHitResultAtScreenPosition(
    FVector2D(ScreenX, ScreenY), ECC_Visibility, false, Hit);
"""
    )

    assert claims == []


def test_deproject_ray_origin_cannot_be_used_as_board_hit_location():
    result = validate_sketch(
        """
APlayerController* PC = UGameplayStatics::GetPlayerController(this, 0);
FVector WorldLoc, WorldDir;
UGameplayStatics::DeprojectScreenToWorld(
    PC, FVector2D(ScreenX, ScreenY), WorldLoc, WorldDir);
int32 GridX = 0, GridY = 0;
WorldToGrid(WorldLoc, GridX, GridY);
""",
        NO_INDEX,
    )

    bad = next(row for row in result["results"] if row["verdict"] == "known_bad")
    assert bad["symbol"] == "deproject_origin_used_as_hit"
    assert "GetHitResultAtScreenPosition" in bad["replacement"]
    assert result["ok"] is False


def test_world_zero_plane_cannot_replace_actual_board_hit():
    result = validate_sketch(
        """
FVector RayOrigin, RayDir;
PC->DeprojectScreenPositionToWorld(ScreenX, ScreenY, RayOrigin, RayDir);
const float BoardZ = 0.f;
const float T = (BoardZ - RayOrigin.Z) / RayDir.Z;
const FVector HitLocation = RayOrigin + RayDir * T;
WorldToGrid(HitLocation, GridX, GridY);
""",
        NO_INDEX,
        declaration_context="APlayerController* PC;",
    )

    bad = next(row for row in result["results"] if row["verdict"] == "known_bad")
    assert bad["symbol"] == "world_zero_plane_used_as_board_hit"
    assert "GetHitResultAtScreenPosition" in bad["replacement"]
    assert result["ok"] is False


def test_round_completion_progress_cannot_be_reset_or_incremented_each_turn():
    result = validate_sketch(
        """
void AGomokuGameState::StartNewTurn()
{
    CurrentRoundIndex++;
    PlayersCompletedThisRound.Reset();
    OnTurnChanged.Broadcast(CurrentPlayerIndex, CurrentRoundIndex);
}

void AGomokuGameState::EndCurrentTurn(bool bForceEnd)
{
    PlayersCompletedThisRound.Add(CurrentPlayerIndex);
    const bool bRoundCompleted =
        PlayersCompletedThisRound.Num() >= LocalPlayerCount;
    StartNewTurn();
}
""",
        NO_INDEX,
    )

    bad_symbols = {
        row["symbol"]
        for row in result["results"]
        if row["verdict"] == "known_bad"
    }
    assert {
        "round_progress_reset_at_turn_start",
        "round_incremented_at_each_turn_start",
        "round_completion_uses_configured_player_count",
    }.issubset(bad_symbols)
    assert result["ok"] is False


def test_round_completion_set_can_reset_after_active_players_finish():
    result = validate_sketch(
        """
void AGomokuGameState::EndCurrentTurn(bool bForceEnd)
{
    PlayersCompletedThisRound.Add(CurrentPlayerIndex);
    if (HaveAllActivePlayersCompletedRound())
    {
        ++CurrentRoundIndex;
        ApplyEndOfRoundRecovery();
        PlayersCompletedThisRound.Reset();
    }
    AdvanceToNextActivePlayer();
}
""",
        NO_INDEX,
    )

    assert not any(
        row["verdict"] == "known_bad"
        and row["symbol"].startswith("round_")
        for row in result["results"]
    )


def test_active_round_membership_and_reverse_turn_order_must_not_use_count_or_clamp():
    result = validate_sketch(
        """
const int32 StartPos = ActivePlayerIndices.IndexOfByKey(CurrentPlayerIndex);
const int32 Pos = FMath::Max<int32>(
    0, StartPos + Step * TurnDirection);
const int32 Candidate = ActivePlayerIndices[Pos];

const bool bAllActed =
    PlayersCompletedThisRound.Num() == ActivePlayerIndices.Num();
""",
        NO_INDEX,
    )

    bad_symbols = {
        row["symbol"]
        for row in result["results"]
        if row["verdict"] == "known_bad"
    }
    assert "round_completion_compares_set_count_only" in bad_symbols
    assert "turn_direction_clamped_instead_of_wrapped" in bad_symbols
    assert result["ok"] is False


def test_reverse_turn_temporary_cannot_be_clamped_on_later_line():
    result = validate_sketch(
        """
int32 UGomokuRuleEngine::AdvanceTurnIndex(int32 CurrentIndex, int32 Direction) const
{
    const int32 ActiveCount = ActivePlayerIndices.Num();
    int64 Next = static_cast<int64>(CurrentIndex) + static_cast<int64>(Direction);
    Next = FMath::Max(Next, 0LL);
    return static_cast<int32>(Next % ActiveCount);
}
""",
        NO_INDEX,
    )

    assert any(
        row["verdict"] == "known_bad"
        and row["symbol"] == "turn_direction_clamped_instead_of_wrapped"
        for row in result["results"]
    )
    assert result["ok"] is False


def test_active_round_membership_and_reverse_turn_order_accept_wrapping_and_all_of():
    result = validate_sketch(
        """
const int32 Count = ActivePlayerIndices.Num();
const int32 Pos =
    (StartPos + (Step + 1) * TurnDirection + Count * 2) % Count;
const int32 Candidate = ActivePlayerIndices[Pos];
bool bAllActed = !ActivePlayerIndices.IsEmpty();
for (const int32 ActiveId : ActivePlayerIndices)
{
    if (!PlayersCompletedThisRound.Contains(ActiveId))
    {
        bAllActed = false;
        break;
    }
}
""",
        NO_INDEX,
    )

    assert not any(
        row["verdict"] == "known_bad"
        and row["symbol"] in {
            "round_completion_compares_set_count_only",
            "turn_direction_clamped_instead_of_wrapped",
        }
        for row in result["results"]
    )


def test_explicit_screen_coordinates_cannot_be_replaced_by_current_cursor_query():
    result = validate_sketch(
        """
void ABoard::OnScreenClick(int32 ScreenX, int32 ScreenY)
{
    FHitResult Hit;
    PC->GetHitResultUnderCursorByChannel(
        ETraceTypeQuery::TraceTypeQuery1, true, Hit);
}
""",
        NO_INDEX,
    )

    bad = next(row for row in result["results"] if row["verdict"] == "known_bad")
    assert bad["symbol"] == "screen_coordinates_ignored_for_cursor_hit"
    assert "GetHitResultAtScreenPosition" in bad["replacement"]
    assert result["ok"] is False


def test_obsolete_or_invented_unreal_pointer_wrappers_are_known_bad():
    result = validate_sketch(
        "TSubobjectPtr<UStaticMeshComponent> Mesh; TWidgetPtr<UButton> Button;",
        NO_INDEX,
    )

    assert result["ok"] is False
    bad = {
        row["symbol"]: row
        for row in result["results"]
        if row["verdict"] == "known_bad"
    }
    assert {"tsubobjectptr", "twidgetptr"}.issubset(bad)
    assert "TObjectPtr" in bad["tsubobjectptr"]["replacement"]
    assert "BindWidget" in bad["twidgetptr"]["replacement"]


def test_show_mouse_cursor_method_hallucination_is_known_bad():
    result = validate_sketch("ShowMouseCursor(true);", NO_INDEX)

    assert result["ok"] is False
    bad = next(row for row in result["results"] if row["verdict"] == "known_bad")
    assert bad["symbol"] == "showmousecursor"
    assert "bShowMouseCursor" in bad["replacement"]


def test_invented_camera_direction_helper_and_unqualified_statics_are_known_bad():
    result = validate_sketch(
        "FVector Direction = UKismetMathLibrary::GetDirectionToLookAtFromCamera(A, B, C); "
        "APlayerController* PC = GameplayStatics::GetPlayerController(World, 0);",
        NO_INDEX,
    )

    assert result["ok"] is False
    bad = {
        row["symbol"]: row
        for row in result["results"]
        if row["verdict"] == "known_bad"
    }
    assert "getdirectiontolookatfromcamera" in bad
    assert "gameplaystatics_getplayercontroller_unqualified" in bad
    assert "DeprojectScreenPositionToWorld" in bad[
        "getdirectiontolookatfromcamera"
    ]["replacement"]
    assert "UGameplayStatics" in bad[
        "gameplaystatics_getplayercontroller_unqualified"
    ]["replacement"]


def test_nonexistent_ism_transform_methods_remain_api_claims():
    claims = extract_member_call_claims(
        """
StoneInstances->SetNumInstances(225, true);
StoneInstances->SetInstanceTransform(0, Transform, true);
""",
        declaration_context=(
            "TObjectPtr<UInstancedStaticMeshComponent> StoneInstances; "
            "FTransform Transform;"
        ),
    )

    assert {
        (claim["receiverType"], claim["member"])
        for claim in claims
    } == {
        ("UInstancedStaticMeshComponent", "SetNumInstances"),
        ("UInstancedStaticMeshComponent", "SetInstanceTransform"),
    }


def test_comments_and_string_literals_do_not_create_api_claims():
    sketch = '''
// GomokuGameMode.h uses UserWidget after the next slice.
const TCHAR* Label = "UFakeWidget->BogusCall()";
/* UAnotherFakeSubsystem should not be treated as code. */
'''

    assert extract_symbols(sketch) == []
    assert extract_member_call_claims(sketch) == []
    assert validate_sketch(sketch, NO_INDEX)["ok"] is True


def test_build_cs_collection_calls_are_not_unreal_cpp_api_claims():
    sketch = '''
// O_Mock.Build.cs: add UMG/Slate for Gomoku HUD.
PublicDependencyModuleNames.AddRange(new string[] { "Core", "UMG" });
PrivateDependencyModuleNames.AddRange(new string[] { "Slate", "SlateCore" });
// GomokuGameMode.h: prepare UserWidget includes.
'''

    assert extract_symbols(sketch) == []
    assert extract_member_call_claims(sketch) == []
    result = validate_sketch(sketch, NO_INDEX)
    assert result["ok"] is True
    assert result["symbolCount"] == 0


def test_architecture_inheritance_shorthand_declares_greenfield_types():
    sketch = """AGomokuGameMode : AGameModeBase
AGomokuGameState : AGameStateBase
UGomokuRuleEngine* RuleEngine;
"""

    assert extract_local_declarations(sketch) == {
        "AGomokuGameMode",
        "AGomokuGameState",
    }
    result = validate_sketch(sketch, NO_INDEX)
    assert result["ok"] is False
    assert {row["symbol"] for row in result["results"]} == {"UGomokuRuleEngine"}


def test_greenfield_inheritance_shorthand_does_not_claim_new_types_are_engine_apis():
    result = validate_sketch(
        "AGomokuGameMode : AGameModeBase\nAGomokuGameState : AGameStateBase",
        NO_INDEX,
    )

    assert result["ok"] is True
    assert result["localDeclarationCount"] == 2
    assert result["unverifiedCount"] == 0


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


def test_existing_target_delegate_in_declaration_context_is_project_local():
    result = validate_sketch(
        """
UPROPERTY(BlueprintAssignable)
FOnMatchEndedDelegate OnMatchEnded;
""",
        NO_INDEX,
        declaration_context="""
DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(
    FOnMatchEndedDelegate, const FGomokuWinResult&, WinResult);
""",
    )

    assert result["ok"] is True
    assert result["localDeclarationCount"] == 1
    assert not any(
        item["symbol"] == "FOnMatchEndedDelegate"
        for item in result["results"]
    )


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


def test_project_graph_verifies_static_class_for_reflected_project_class():
    graph = {
        "symbols": [
            {
                "symbol_name": "AGomokuBoardActor",
                "symbol_kind": "class",
                "base_class": "AActor",
                "is_reflected": True,
                "file_path": "Source/Game/GomokuBoardActor.h",
                "line_start": 8,
            }
        ]
    }

    result = validate_sketch(
        "UClass* BoardClass = AGomokuBoardActor::StaticClass();",
        NO_INDEX,
        graph=graph,
    )

    static_class = next(
        item for item in result["results"] if item["symbol"] == "StaticClass"
    )
    assert static_class["verdict"] == "verified"
    assert static_class["receiverType"] == "AGomokuBoardActor"
    assert result["ok"] is True


def test_static_class_stays_unverified_without_reflected_type_proof():
    graph = {
        "symbols": [
            {
                "symbol_name": "FHelperType",
                "symbol_kind": "class",
                "is_reflected": False,
                "file_path": "Source/Game/HelperType.h",
                "line_start": 3,
            }
        ]
    }

    result = validate_sketch(
        "UClass* HelperClass = FHelperType::StaticClass();",
        NO_INDEX,
        graph=graph,
    )

    static_class = next(
        item for item in result["results"] if item["symbol"] == "StaticClass"
    )
    assert static_class["verdict"] == "unverified"
    assert result["ok"] is False


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


def test_project_enum_value_must_exist_in_exact_declaration(tmp_path: Path):
    header = tmp_path / "Source" / "Game" / "GomokuMinigameTypes.h"
    header.parent.mkdir(parents=True)
    header.write_text(
        """
UENUM(BlueprintType)
enum class EMatchPhase : uint8
{
    Waiting,
    Playing UMETA(DisplayName = "Playing"),
    GameOver = 4,
};
""",
        encoding="utf-8",
    )
    graph = {
        "symbols": [
            {
                "symbol_name": "EMatchPhase",
                "symbol_kind": "enum",
                "file_path": str(header),
                "line_start": 3,
            }
        ]
    }

    result = validate_sketch(
        "EMatchPhase Phase = EMatchPhase::Ended;",
        NO_INDEX,
        graph=graph,
        declaration_context=header.read_text(encoding="utf-8"),
    )

    issue = next(
        item
        for item in result["results"]
        if item.get("errorCode") == "PROJECT_ENUM_VALUE_NOT_DECLARED"
    )
    assert result["ok"] is False
    assert issue["symbol"] == "EMatchPhase::Ended"
    assert issue["verdict"] == "known_bad"
    assert issue["evidence"][0]["declaredValues"] == [
        "GameOver",
        "Playing",
        "Waiting",
    ]


def test_project_enum_value_accepts_declared_value(tmp_path: Path):
    header = tmp_path / "Source" / "Game" / "GomokuMinigameTypes.h"
    header.parent.mkdir(parents=True)
    header.write_text(
        "enum class EMatchPhase : uint8 { Waiting, Playing, GameOver };\n",
        encoding="utf-8",
    )
    graph = {
        "symbols": [
            {
                "symbol_name": "EMatchPhase",
                "symbol_kind": "enum",
                "file_path": str(header),
                "line_start": 1,
            }
        ]
    }

    result = validate_sketch(
        "EMatchPhase Phase = EMatchPhase::GameOver;",
        NO_INDEX,
        graph=graph,
        declaration_context=header.read_text(encoding="utf-8"),
    )

    assert not any(
        item.get("errorCode") == "PROJECT_ENUM_VALUE_NOT_DECLARED"
        for item in result["results"]
    )
    assert result["ok"] is True


def test_engine_header_fallback_verifies_exact_type_and_owned_method(tmp_path):
    header = (
        tmp_path
        / "UE_5.8"
        / "Engine"
        / "Plugins"
        / "FX"
        / "Niagara"
        / "Source"
        / "Niagara"
        / "Public"
        / "NiagaraComponent.h"
    )
    header.parent.mkdir(parents=True)
    header.write_text(
        """
#pragma once
class NIAGARA_API UNiagaraComponent : public UFXSystemComponent
{
public:
    void SetVariableFloat(FName InVariableName, float InValue);
};
""".strip(),
        encoding="utf-8",
    )

    result = validate_sketch(
        "UNiagaraComponent* Comp; Comp->SetVariableFloat(Name, Value);",
        NO_INDEX,
        graph={"symbols": []},
        engine_root=tmp_path / "UE_5.8",
    )

    component = next(
        item for item in result["results"] if item["symbol"] == "UNiagaraComponent"
    )
    method = next(
        item for item in result["results"] if item["symbol"] == "SetVariableFloat"
    )
    assert component["verdict"] == "verified"
    assert component["coverageStatus"] == "engine_header_verified"
    assert method["verdict"] == "verified"
    assert method["receiverType"] == "UNiagaraComponent"
    assert method["coverageStatus"] == "engine_header_verified"
    assert method["evidence"][0]["source"] == "engine_header_exact"
    assert result["engineHeaderLookup"]["status"] == "ready"
    assert result["engineHeaderLookup"]["verifiedClaimCount"] == 2
    assert result["ok"] is True


def test_engine_header_fallback_finds_type_in_differently_named_header(tmp_path):
    header = (
        tmp_path
        / "UE_5.8"
        / "Engine"
        / "Source"
        / "Runtime"
        / "CoreUObject"
        / "Public"
        / "UObject"
        / "CoreNet.h"
    )
    header.parent.mkdir(parents=True)
    header.write_text(
        "class COREUOBJECT_API FLifetimeProperty { public: int32 RepIndex; };",
        encoding="utf-8",
    )

    result = validate_sketch(
        "TArray<FLifetimeProperty> LifetimeProps;",
        NO_INDEX,
        graph={
            "symbols": [
                {
                    "symbol_name": "FLifetimeProperty",
                    "symbol_kind": "usage",
                    "qualified_name": "FLifetimeProperty",
                    "file_path": "Source/Demo/Replicated.cpp",
                    "line_start": 4,
                }
            ]
        },
        engine_root=tmp_path / "UE_5.8",
    )

    lifetime = next(
        item for item in result["results"] if item["symbol"] == "FLifetimeProperty"
    )
    assert lifetime["verdict"] == "verified"
    assert lifetime["coverageStatus"] == "engine_header_verified"
    assert "CoreNet.h" in lifetime["evidence"][0]["locator"]
    assert result["ok"] is True


def test_game_state_server_rpc_is_known_bad_even_when_it_compiles():
    result = validate_sketch(
        "UFUNCTION(Server, Reliable) void ServerPlaceStone(int32 PlayerIndex);",
        NO_INDEX,
        declaration_context="class ABoardState : public AGameStateBase {};",
    )
    bad = next(
        item for item in result["results"]
        if item["symbol"] == "ServerRpcOnGameState"
    )
    assert bad["verdict"] == "known_bad"
    assert "PlayerController" in bad["replacement"]
    assert result["ok"] is False


def test_existing_game_state_server_rpc_does_not_block_a_removal_sketch():
    result = validate_sketch(
        "int32 CurrentPlayerIndex = -1;",
        NO_INDEX,
        declaration_context=(
            "class ABoardState : public AGameStateBase { "
            "UFUNCTION(Server, Reliable) void ServerPlaceStone(int32 PlayerIndex); "
            "};"
        ),
    )

    assert not any(
        item["symbol"] == "ServerRpcOnGameState"
        for item in result["results"]
    )


def test_engine_header_miss_escalates_once_to_compiler_proof_not_absence(tmp_path):
    engine_source = tmp_path / "UE_5.8" / "Engine" / "Source" / "Runtime"
    engine_source.mkdir(parents=True)

    result = validate_sketch(
        "UImaginarySubsystem* System; System->PerformImaginaryAction();",
        NO_INDEX,
        graph={"symbols": []},
        engine_root=tmp_path / "UE_5.8",
    )

    method = next(
        item
        for item in result["results"]
        if item["symbol"] == "PerformImaginaryAction"
    )
    assert method["verdict"] == "compiler_required"
    assert method["sourceLookupVerdict"] == "unverified"
    assert method["coverageStatus"] == "source_lookup_exhausted"
    assert "UHT/UBT compiler proof" in method["note"]
    assert result["engineHeaderLookup"]["status"] == "ready"
    assert result["ok"] is True
    assert result["compilerProofRequired"] is True
    assert result["postMutationRequiredAction"] == "static_validate_project"


def test_engine_header_contract_rejects_wrong_argument_count(tmp_path):
    header = (
        tmp_path
        / "UE_5.8"
        / "Engine"
        / "Plugins"
        / "FX"
        / "Niagara"
        / "Source"
        / "Niagara"
        / "Public"
        / "NiagaraFunctionLibrary.h"
    )
    header.parent.mkdir(parents=True)
    header.write_text(
        """
class NIAGARA_API UNiagaraFunctionLibrary : public UBlueprintFunctionLibrary
{
public:
    static UNiagaraComponent* SpawnSystemAtLocation(
        const UObject* WorldContextObject,
        FVector Location,
        FRotator Rotation = FRotator::ZeroRotator);
};
""".strip(),
        encoding="utf-8",
    )

    result = validate_sketch(
        "UNiagaraFunctionLibrary::SpawnSystemAtLocation();",
        NO_INDEX,
        graph={"symbols": []},
        engine_root=tmp_path / "UE_5.8",
    )

    mismatch = next(
        item
        for item in result["results"]
        if item.get("errorCode") == "ENGINE_ARGUMENT_COUNT_MISMATCH"
    )
    assert mismatch["receiverType"] == "UNiagaraFunctionLibrary"
    assert "called with 0 argument" in mismatch["note"]
    assert result["knownBadCount"] >= 1
    assert result["ok"] is False


def test_engine_header_contract_rejects_narrowing_numeric_argument(tmp_path):
    header = (
        tmp_path
        / "UE_5.8"
        / "Engine"
        / "Source"
        / "Runtime"
        / "Demo"
        / "Public"
        / "DemoMathLibrary.h"
    )
    header.parent.mkdir(parents=True)
    header.write_text(
        """
class UDemoMathLibrary : public UBlueprintFunctionLibrary
{
public:
    static void SetSmallValue(float Value);
};
""".strip(),
        encoding="utf-8",
    )

    result = validate_sketch(
        "UDemoMathLibrary::SetSmallValue(1.0);",
        NO_INDEX,
        graph={"symbols": []},
        engine_root=tmp_path / "UE_5.8",
    )

    mismatch = next(
        item
        for item in result["results"]
        if item.get("errorCode") == "ENGINE_PARAMETER_TYPE_MISMATCH"
    )
    assert "double" in mismatch["note"]
    assert "float" in mismatch["note"]
    assert result["ok"] is False


def test_engine_header_contract_rejects_unrelated_unreal_pointer_assignment(tmp_path):
    header = (
        tmp_path
        / "UE_5.8"
        / "Engine"
        / "Plugins"
        / "FX"
        / "Niagara"
        / "Source"
        / "Niagara"
        / "Public"
        / "NiagaraFunctionLibrary.h"
    )
    header.parent.mkdir(parents=True)
    header.write_text(
        """
class NIAGARA_API UNiagaraFunctionLibrary : public UBlueprintFunctionLibrary
{
public:
    static UNiagaraComponent* SpawnSystemAtLocation(
        const UObject* WorldContextObject,
        FVector Location);
};
""".strip(),
        encoding="utf-8",
    )

    result = validate_sketch(
        "AActor* Spawned = UNiagaraFunctionLibrary::SpawnSystemAtLocation(World, Location);",
        NO_INDEX,
        graph={"symbols": []},
        engine_root=tmp_path / "UE_5.8",
    )

    mismatch = next(
        item
        for item in result["results"]
        if item.get("errorCode") == "ENGINE_RETURN_TYPE_MISMATCH"
    )
    assert mismatch["receiverType"] == "UNiagaraFunctionLibrary"
    assert "AActor*" in mismatch["note"]
    assert result["knownBadCount"] >= 1
    assert result["ok"] is False


@pytest.mark.parametrize("engine_version", ["5.4", "5.5", "5.6", "5.7", "5.8"])
def test_version_local_engine_headers_accept_real_template_signature_shapes(
    tmp_path: Path,
    engine_version: str,
):
    engine = tmp_path / f"UE {engine_version} Cross Platform"
    core_math = engine / "Engine" / "Source" / "Runtime" / "Core" / "Public" / "Math"
    core_math.mkdir(parents=True)
    (core_math / "MathFwd.h").write_text(
        "using FVector = UE::Math::TVector<double>;\n",
        encoding="utf-8",
    )
    (core_math / "Vector.h").write_text(
        """
namespace UE::Math
{
template <typename T>
struct TVector
{
    [[nodiscard]] UE_FORCEINLINE_HINT static T DotProduct(
        const TVector<T>& V1, const TVector<T>& V2);
    [[nodiscard]] T Size() const;
};
}
""".strip(),
        encoding="utf-8",
    )
    (core_math / "UnrealMathUtility.h").write_text(
        """
struct FMath
{
    template <typename T1, typename T2, typename T3, typename T4>
    [[nodiscard]] static auto FInterpTo(
        T1 Current, T2 Target, T3 DeltaTime, T4 InterpSpeed)
    {
        return Current;
    }
};
""".strip(),
        encoding="utf-8",
    )
    anim = (
        engine
        / "Engine"
        / "Source"
        / "Runtime"
        / "AnimGraphRuntime"
        / "Public"
        / "KismetAnimationLibrary.h"
    )
    anim.parent.mkdir(parents=True)
    anim.write_text(
        """
class ANIMGRAPHRUNTIME_API UKismetAnimationLibrary
{
public:
    static float CalculateDirection(const FVector& Velocity, const FRotator& BaseRotation);
};
""".strip(),
        encoding="utf-8",
    )
    unrelated = (
        engine
        / "Engine"
        / "Source"
        / "ThirdParty"
        / "Noise"
        / "Public"
        / "Vector.h"
    )
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text(
        """
struct FVector
{
    static bool DotProduct(int32 Wrong);
    bool Size(int32 Wrong) const;
};
""".strip(),
        encoding="utf-8",
    )

    result = validate_sketch(
        """
FVector A;
FVector B;
double Dot = FVector::DotProduct(A, B);
double Length = A.Size();
double Smoothed = FMath::FInterpTo(Current, Target, DeltaTime, Speed);
float Direction = UKismetAnimationLibrary::CalculateDirection(Velocity, Rotation);
""",
        NO_INDEX,
        graph={"symbols": []},
        engine_root=engine,
    )

    assert result["ok"] is True, result
    assert not any(
        str(item.get("errorCode") or "").startswith("ENGINE_")
        for item in result["results"]
    )
    for symbol in ("DotProduct", "Size", "FInterpTo", "CalculateDirection"):
        row = next(item for item in result["results"] if item["symbol"] == symbol)
        assert row["verdict"] == "verified", row
        assert all("ThirdParty" not in evidence["locator"] for evidence in row["evidence"])


def test_chained_call_uses_header_return_type_as_terminal_receiver(tmp_path: Path):
    engine = tmp_path / "UE_5.8"
    actor_header = (
        engine
        / "Engine"
        / "Source"
        / "Runtime"
        / "Engine"
        / "Classes"
        / "GameFramework"
        / "Actor.h"
    )
    vector_header = (
        engine
        / "Engine"
        / "Source"
        / "Runtime"
        / "Core"
        / "Public"
        / "Math"
        / "Vector.h"
    )
    actor_header.parent.mkdir(parents=True)
    vector_header.parent.mkdir(parents=True)
    actor_header.write_text(
        "class AActor {\npublic:\n    FVector GetVelocity() const;\n};\n",
        encoding="utf-8",
    )
    vector_header.write_text(
        "struct FVector {\n    double Size() const;\n};\n",
        encoding="utf-8",
    )

    result = validate_sketch(
        "void AActor::Tick() { double Speed = GetVelocity().Size(); }",
        NO_INDEX,
        graph={"symbols": []},
        engine_root=engine,
    )

    assert result["ok"] is True, result
    size = next(item for item in result["results"] if item["symbol"] == "Size")
    assert size["receiverType"] == "FVector"
    assert size["verdict"] == "verified"
    assert result["engineHeaderLookup"]["inspectedFileCount"] >= 2


def test_chained_call_rejects_member_on_scalar_return(tmp_path: Path):
    engine = tmp_path / "UE_5.8"
    actor_header = (
        engine
        / "Engine"
        / "Source"
        / "Runtime"
        / "Engine"
        / "Classes"
        / "GameFramework"
        / "Actor.h"
    )
    actor_header.parent.mkdir(parents=True)
    actor_header.write_text(
        "class AActor {\npublic:\n    double GetVelocity() const;\n};\n",
        encoding="utf-8",
    )

    result = validate_sketch(
        "void AActor::Tick() { double Speed = GetVelocity().Size(); }",
        NO_INDEX,
        graph={"symbols": []},
        engine_root=engine,
    )

    issue = next(
        item
        for item in result["results"]
        if item.get("errorCode") == "CHAIN_RECEIVER_NOT_OBJECT"
    )
    assert issue["receiverType"] == "double"
    assert result["knownBadCount"] >= 1
    assert result["ok"] is False


def test_header_signature_parser_rejects_calls_assignments_and_comments():
    text = """
// static bool DotProduct(int32 Wrong);
return Size();
double Value = Size();
OutLength = Size();
[[nodiscard]] double Size() const;
"""

    contracts = _signature_contracts(text, "Size")

    assert len(contracts) == 1
    assert contracts[0]["returnType"] == "double"
    assert contracts[0]["requiredArgumentCount"] == 0


def test_project_source_contract_rejects_observed_qwen_api_mixing(tmp_path):
    source = tmp_path / "Source" / "O_Mock"
    source.mkdir(parents=True)
    header = source / "Contracts.h"
    header.write_text(
        """
class O_MOCK_API AGomokuPlayerController : public APlayerController
{
};

class O_MOCK_API AGomokuBoardActor : public AActor
{
public:
    bool WorldToGrid(const FVector& WorldLocation, int32& OutX, int32& OutY) const;
};

class O_MOCK_API AGomokuGameState : public AGameStateBase
{
public:
    void HandlePlaceStone(int32 PlayerIndex, const FIntPoint& Cell);
};
""".strip(),
        encoding="utf-8",
    )
    from build_symbol_graph import build_symbol_graph

    graph = build_symbol_graph(tmp_path / "Source")
    result = validate_sketch(
        """
class GOMOKU_API AGomokuPlayerController : public APlayerController {};
AGomokuBoardActor* BoardActor;
FIntPoint GridPos = BoardActor->WorldToGrid(HitLocation);
UGameInstance* GI = GetWorld()->GetGameState<AGomokuGameState>();
AGomokuGameState* GS;
GS->HandlePlaceStone(GridPos, this);
""",
        NO_INDEX,
        graph=graph,
    )

    error_codes = {
        item.get("errorCode")
        for item in result["results"]
        if item.get("errorCode")
    }
    assert {
        "PROJECT_API_MACRO_MISMATCH",
        "PROJECT_ARGUMENT_COUNT_MISMATCH",
        "PROJECT_PARAMETER_TYPE_MISMATCH",
        "PROJECT_RETURN_TYPE_MISMATCH",
        "TEMPLATE_RETURN_TYPE_MISMATCH",
    }.issubset(error_codes)
    assert result["knownBadCount"] >= 5
    assert result["ok"] is False


def test_project_source_contract_accepts_exact_arity_return_and_template_type(tmp_path):
    source = tmp_path / "Source" / "O_Mock"
    source.mkdir(parents=True)
    (source / "Contracts.h").write_text(
        """
class O_MOCK_API AGomokuBoardActor : public AActor
{
public:
    bool WorldToGrid(const FVector& WorldLocation, int32& OutX, int32& OutY) const;
};

class O_MOCK_API AGomokuGameState : public AGameStateBase
{
public:
    void HandlePlaceStone(int32 PlayerIndex, const FIntPoint& Cell);
};
""".strip(),
        encoding="utf-8",
    )
    from build_symbol_graph import build_symbol_graph

    graph = build_symbol_graph(tmp_path / "Source")
    result = validate_sketch(
        """
AGomokuBoardActor* BoardActor;
int32 X, Y;
bool bMapped = BoardActor->WorldToGrid(HitLocation, X, Y);
AGomokuGameState* GS = GetWorld()->GetGameState<AGomokuGameState>();
GS->HandlePlaceStone(0, FIntPoint(X, Y));
""",
        NO_INDEX,
        graph=graph,
        declaration_context="""
UFUNCTION()
void OnMouseMoveY(float Value);
UPROPERTY(VisibleAnywhere)
TObjectPtr<AGomokuBoardActor> BoardActor;
void AGomokuPlayerController::OnMouseMoveX(float Value) {}
""",
    )

    contract_errors = {
        item.get("errorCode")
        for item in result["results"]
        if item.get("errorCode")
    }
    assert not {
        "PROJECT_ARGUMENT_COUNT_MISMATCH",
        "PROJECT_PARAMETER_TYPE_MISMATCH",
        "PROJECT_RETURN_TYPE_MISMATCH",
        "TEMPLATE_RETURN_TYPE_MISMATCH",
    } & contract_errors
