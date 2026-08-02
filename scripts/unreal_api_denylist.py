#!/usr/bin/env python
"""Single source of truth for known-bad / commonly hallucinated Unreal API patterns.

Both the refactor plan validator and the chat-side code sketch validator consult
these tables so that invented APIs (a frequent small-model failure) are flagged
before they reach code. Entries are intentionally conservative: only add a term
here when it is a well-established hallucination or a wrong lifecycle override,
not merely an API that happens to be missing from the local index.

Token-only entries (KNOWN_BAD_API) must be names that are themselves invented or
never valid as bare identifiers. Real UE APIs belong in KNOWN_BAD_API_PATTERNS with
receiver/namespace/argument context checks.
"""

from __future__ import annotations

import re
from typing import Any

from cpp_parse_utils import mask_comments_and_strings, offset_in_regions, preprocessor_editor_safe_regions

# Wrong subsystem/actor lifecycle overrides. Key is a lowercase token searched with
# word boundaries; value explains the correct API to verify against.
INVALID_LIFECYCLE_OVERRIDES: dict[str, str] = {
    "onworlddestroyed": (
        "UWorldSubsystem does not expose OnWorldDestroyed in UE 5.8; "
        "use OnWorldEndPlay(UWorld&) or PreDeinitialize()."
    ),
    "worlddestroyed": (
        "WorldDestroyed is not a standard UE subsystem lifecycle override; "
        "verify the direct base API before planning edits."
    ),
}

# Commonly invented API names / members. Key is a lowercase token searched with
# word boundaries; value explains the reality and the correct thing to verify.
# Only include tokens that are never valid UE API names on their own.
KNOWN_BAD_API: dict[str, str] = {
    "brestorestate": (
        "bRestoreState is not a public flag on the sequence player/actor. "
        "Restore-on-finish is driven by the LevelSequence 'Restore State' setting "
        "and FMovieSceneSequencePlaybackSettings; verify the exact API before use."
    ),
    "setrestorestate": (
        "SetRestoreState is not a standard UE Sequencer API. Configure restore "
        "behavior through the sequence asset / playback settings and verify headers."
    ),
    "setbindingtag": (
        "SetBindingTag is not a standard runtime API. Sequencer binding tags are "
        "edited via UMovieSceneSequence binding metadata in the editor; do not "
        "conflate this with AActor::Tags."
    ),
    "addbindingoverride": (
        "AddBindingOverride is not a standard method name. Dynamic binding / "
        "possessable overrides use FMovieSceneObjectBindingID and the player's "
        "binding override APIs; verify the exact signature before use."
    ),
    "disablegravity": (
        "UCharacterMovementComponent has no DisableGravity() member. Use "
        "GravityScale = 0.0f or an intentional movement mode such as MOVE_Flying."
    ),
    "replicatevariable": (
        "ReplicateVariable is not a standard UE replication API. Use "
        "GetLifetimeReplicatedProps with DOREPLIFETIME in the owning class .cpp."
    ),
    "setreplicated": (
        "SetReplicated is not a UPROPERTY replication helper. Register replicated "
        "members with DOREPLIFETIME and bReplicates on the actor."
    ),
    "setgravityenabled": (
        "SetGravityEnabled is not a standard UPrimitiveComponent API. Use "
        "SetEnableGravity or movement/physics APIs on the correct component type."
    ),
    "enablephysicssimulation": (
        "EnablePhysicsSimulation is not a universal actor API. Use "
        "UPrimitiveComponent::SetSimulatePhysics on the colliding component."
    ),
    "deprojectscreenpositiontofvector": (
        "DeprojectScreenPositionToFVector is not an APlayerController API. Use "
        "DeprojectMousePositionToWorld(WorldLocation, WorldDirection) or "
        "DeprojectScreenPositionToWorld(ScreenX, ScreenY, WorldLocation, WorldDirection)."
    ),
    "deprojectscreentoworldtofov": (
        "DeprojectScreenToWorldToFov is not an APlayerController API. Use "
        "DeprojectScreenPositionToWorld(ScreenX, ScreenY, WorldLocation, WorldDirection)."
    ),
    "scene_delegate_name": (
        "SCENE_DELEGATE_NAME is not an Unreal scene-query stat macro. Use "
        "SCENE_QUERY_STAT(QueryName) when constructing FCollisionQueryParams."
    ),
    "tsubobjectptr": (
        "TSubobjectPtr is obsolete and absent from UE 5.8. Store reflected component "
        "fields as TObjectPtr<ComponentType> (or a compatible UPROPERTY pointer) and "
        "assign the result of CreateDefaultSubobject in the constructor."
    ),
    "twidgetptr": (
        "TWidgetPtr is not an Unreal UMG pointer type. A BindWidget field should use "
        "TObjectPtr<UWidgetSubclass> with UPROPERTY(meta=(BindWidget))."
    ),
    "showmousecursor": (
        "APlayerController exposes bShowMouseCursor as a property; it does not have "
        "a ShowMouseCursor(bool) method. Assign bShowMouseCursor = true/false."
    ),
    "getdirectiontolookatfromcamera": (
        "GetDirectionToLookAtFromCamera is not a UKismetMathLibrary API. Use "
        "APlayerController::DeprojectMousePositionToWorld or "
        "DeprojectScreenPositionToWorld to obtain the world ray."
    ),
}

KNOWN_BAD_API_REPLACEMENTS: dict[str, str] = {
    "disablegravity": "MoveComp->GravityScale = 0.0f; // or SetMovementMode(MOVE_Flying)",
    "world_geturl": (
        "const FString LevelName = UGameplayStatics::GetCurrentLevelName(World, true);"
    ),
    "gengine_getworld": "UWorld* World = GetWorld(); // from the owning actor/subsystem",
    "giveability_free": (
        "AbilitySystemComponent->GiveAbility(FGameplayAbilitySpec(AbilityClass, 1, INDEX_NONE, this));"
    ),
    "getplayercontroller_zero_arg": (
        "UGameplayStatics::GetPlayerController(WorldContextObject, PlayerIndex);"
    ),
    "geditor_runtime": "Guard with WITH_EDITOR and keep editor APIs in Editor modules.",
    "isserver_free": "World->GetNetMode() == NM_DedicatedServer or Actor->HasAuthority()",
    "getcurrentdeltatime_unqualified": (
        "Use the Tick/TickComponent DeltaTime parameter or GetWorld()->GetDeltaSeconds()."
    ),
    "apawn_getcharactermovement": (
        "ACharacter* Character = Cast<ACharacter>(Pawn); // null-check before Character->GetCharacterMovement()"
    ),
    "deprojectscreenpositiontofvector": (
        "FVector WorldLocation, WorldDirection; "
        "DeprojectMousePositionToWorld(WorldLocation, WorldDirection);"
    ),
    "deprojectscreentoworldtofov": (
        "FVector WorldLocation, WorldDirection; "
        "PlayerController->DeprojectScreenPositionToWorld("
        "ScreenX, ScreenY, WorldLocation, WorldDirection);"
    ),
    "scene_delegate_name": "SCENE_QUERY_STAT(BoardClick)",
    "deproject_origin_used_as_hit": (
        "FHitResult Hit; PlayerController->GetHitResultAtScreenPosition("
        "FVector2D(ScreenX, ScreenY), ECC_Visibility, false, Hit); "
        "WorldToGrid(Hit.Location, GridX, GridY);"
    ),
    "world_zero_plane_used_as_board_hit": (
        "FHitResult Hit; PlayerController->GetHitResultAtScreenPosition("
        "FVector2D(ScreenX, ScreenY), ECC_Visibility, false, Hit); "
        "WorldToGrid(Hit.Location, GridX, GridY);"
    ),
    "round_progress_reset_at_turn_start": (
        "Do not reset PlayersCompletedThisRound in StartNewTurn. Add the player who just "
        "acted in EndCurrentTurn, advance to the next eligible player, and reset the set "
        "only after every active player is represented and the round is incremented."
    ),
    "round_incremented_at_each_turn_start": (
        "Increment CurrentRoundIndex only in the branch that proves every active player "
        "completed the round; StartNewTurn must not increment it on every turn."
    ),
    "round_completion_uses_configured_player_count": (
        "Compare PlayersCompletedThisRound against the current active-player ids/count, "
        "excluding resigned or inactive players, not LocalPlayerCount."
    ),
    "round_completion_compares_set_count_only": (
        "Check every current active player id with PlayersCompletedThisRound.Contains(Id). "
        "Set-count equality is insufficient because a player can resign mid-round, leaving "
        "a stale completed id while a different active id is missing."
    ),
    "turn_direction_clamped_instead_of_wrapped": (
        "Use positive modulo over ActivePlayerIndices.Num() for both +1 and -1 traversal; "
        "do not clamp a negative position to zero."
    ),
    "screen_coordinates_ignored_for_cursor_hit": (
        "FHitResult Hit; PlayerController->GetHitResultAtScreenPosition("
        "FVector2D(ScreenX, ScreenY), ECC_Visibility, false, Hit);"
    ),
    "tsubobjectptr": "TObjectPtr<USceneComponent> Component;",
    "twidgetptr": "UPROPERTY(meta=(BindWidget)) TObjectPtr<UButton> Button;",
    "showmousecursor": "bShowMouseCursor = true;",
    "getdirectiontolookatfromcamera": (
        "FVector WorldLocation, WorldDirection; "
        "DeprojectScreenPositionToWorld(ScreenX, ScreenY, WorldLocation, WorldDirection);"
    ),
    "gameplaystatics_getplayercontroller_unqualified": (
        "UGameplayStatics::GetPlayerController(WorldContextObject, 0);"
    ),
}

# Context-sensitive patterns: real API names used in invalid call shapes.
KNOWN_BAD_API_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "screen_coordinates_ignored_for_cursor_hit",
        re.compile(
            r"\b[A-Za-z_]\w*\s*\(\s*int32\s+ScreenX\s*,\s*int32\s+ScreenY\s*\)"
            r"[\s\S]{0,2200}?\bGetHitResultUnderCursor(?:ByChannel|ForObjects)?\s*\(",
            re.IGNORECASE,
        ),
        (
            "This function receives explicit ScreenX/ScreenY coordinates but queries the current "
            "cursor instead. Use APlayerController::GetHitResultAtScreenPosition with the supplied "
            "FVector2D(ScreenX, ScreenY) so the clicked coordinates remain authoritative."
        ),
    ),
    (
        "deproject_origin_used_as_hit",
        re.compile(
            r"\bDeprojectScreenToWorld\s*\("
            r"[^,]+,\s*(?:FVector2D\s*\([^)]*\)|[^,]+),"
            r"\s*(?P<origin>[A-Za-z_]\w*)\s*,\s*[A-Za-z_]\w*\s*\)"
            r"[\s\S]{0,1800}?\bWorldToGrid\s*\(\s*(?P=origin)\b",
            re.IGNORECASE,
        ),
        (
            "DeprojectScreenToWorld returns a near-plane ray origin, not the board hit point. "
            "Trace that ray or use APlayerController::GetHitResultAtScreenPosition, then pass "
            "Hit.Location to WorldToGrid."
        ),
    ),
    (
        "world_zero_plane_used_as_board_hit",
        re.compile(
            r"\bDeproject(?:ScreenToWorld|ScreenPositionToWorld)\s*\("
            r"[\s\S]{0,1800}?\b(?:BoardZ|PlaneZ|GroundZ)\s*=\s*0(?:\.0*)?f?\s*;"
            r"[\s\S]{0,1800}?\bWorldToGrid\s*\(",
            re.IGNORECASE,
        ),
        (
            "A hard-coded world Z=0 ray-plane intersection is not the board's actual collision "
            "hit and breaks when the board actor is moved or rotated. Use the supplied screen "
            "coordinates with APlayerController::GetHitResultAtScreenPosition (or a verified "
            "world trace), then pass Hit.Location to WorldToGrid."
        ),
    ),
    (
        "round_progress_reset_at_turn_start",
        re.compile(
            r"\bStartNewTurn\s*\([^)]*\)\s*\{"
            r"[\s\S]{0,1800}?\bPlayersCompletedThisRound\s*\.\s*Reset\s*\(",
            re.IGNORECASE,
        ),
        (
            "Resetting the round-completion set at each turn discards earlier players and "
            "prevents an all-active-players completion condition from accumulating. Reset it "
            "only after the round-complete branch increments the round."
        ),
    ),
    (
        "round_incremented_at_each_turn_start",
        re.compile(
            r"\bStartNewTurn\s*\([^)]*\)\s*\{"
            r"[\s\S]{0,1200}?\bCurrentRoundIndex\s*\+\+"
            r"[\s\S]{0,2600}?\bPlayersCompletedThisRound\b",
            re.IGNORECASE,
        ),
        (
            "A round tracked by PlayersCompletedThisRound cannot increment in every "
            "StartNewTurn call. Increment only after the active-player completion set is full."
        ),
    ),
    (
        "round_completion_uses_configured_player_count",
        re.compile(
            r"\bPlayersCompletedThisRound\s*\.\s*Num\s*\(\s*\)"
            r"\s*>?=\s*LocalPlayerCount\b",
            re.IGNORECASE,
        ),
        (
            "LocalPlayerCount includes players who may have resigned or become inactive. "
            "Round completion must compare the completed-player ids with the current active "
            "players so departure cannot stall the match."
        ),
    ),
    (
        "round_completion_compares_set_count_only",
        re.compile(
            r"\bPlayersCompletedThisRound\s*\.\s*Num\s*\(\s*\)"
            r"\s*==\s*ActivePlayer(?:Indices|Ids|Players)\s*\.\s*Num\s*\(\s*\)",
            re.IGNORECASE,
        ),
        (
            "Equal set sizes do not prove equal membership when active players can change "
            "during a round. Require PlayersCompletedThisRound.Contains(Id) for every current "
            "active player id."
        ),
    ),
    (
        "turn_direction_clamped_instead_of_wrapped",
        re.compile(
            r"\bFMath\s*::\s*Max(?:\s*<[^>]+>)?\s*\(\s*0\s*,"
            r"[^;\n]{0,220}\b(?:TurnDirection|Direction)\b[^;\n]*\)"
            r"[\s\S]{0,800}?\bActivePlayer(?:Indices|Ids|Players)\b",
            re.IGNORECASE,
        ),
        (
            "Clamping a directed circular index to zero repeats/stops at the first player "
            "when traversing in reverse. Normalize with positive modulo by the active-player "
            "count so both directions wrap."
        ),
    ),
    (
        "turn_direction_clamped_instead_of_wrapped",
        re.compile(
            r"\bAdvanceTurn(?:Index)?\s*\([^)]*\bDirection\b[^)]*\)\s*(?:const\s*)?\{"
            r"[\s\S]{0,2200}?\b(?P<index>[A-Za-z_]\w*)\s*=\s*"
            r"FMath\s*::\s*Max(?:\s*<[^>]+>)?\s*\(\s*(?P=index)\s*,\s*0(?:LL|L|f)?\s*\)",
            re.IGNORECASE,
        ),
        (
            "Clamping a temporary directed index against zero still breaks reverse traversal, "
            "even when the Direction expression was assigned on an earlier line. Normalize with "
            "positive modulo over the active-player positions instead."
        ),
    ),
    (
        "gameplaystatics_getplayercontroller_unqualified",
        re.compile(r"(?<![A-Za-z0-9_])GameplayStatics\s*::\s*GetPlayerController\s*\("),
        (
            "The public engine helper is UGameplayStatics::GetPlayerController; "
            "GameplayStatics::GetPlayerController is not a public gameplay-module API."
        ),
    ),
    (
        "world_geturl",
        re.compile(
            r"\b(?:GetWorld\s*\(\s*\)|(?:[A-Za-z_]\w*)?World)\s*->\s*GetURL\s*\(",
            re.IGNORECASE,
        ),
        (
            "UWorld has no GetURL() member. For map identity/restart, use "
            "GetMapName() or UGameplayStatics::GetCurrentLevelName(), then "
            "OpenLevel/ServerTravel as appropriate."
        ),
    ),
    (
        "getcurrentdeltatime_unqualified",
        re.compile(r"(?<![\w>.-])\bGetCurrentDeltaTime\s*\(", re.IGNORECASE),
        (
            "GetCurrentDeltaTime() is not an Unreal Actor/Component helper. Use the "
            "Tick/TickComponent DeltaTime parameter or GetWorld()->GetDeltaSeconds()."
        ),
    ),
    (
        "apawn_getcharactermovement",
        re.compile(
            r"\bAPawn\s*\*\s*(?P<pawn>[A-Za-z_]\w*)[\s\S]{0,2000}?\b(?P=pawn)\s*->\s*GetCharacterMovement\s*\(",
            re.IGNORECASE,
        ),
        (
            "APawn has no GetCharacterMovement(). Cast the pawn to ACharacter and "
            "null-check it, or use the pawn's actual movement component API."
        ),
    ),
    (
        "gengine_getworld",
        re.compile(r"\bGEngine\s*->\s*(?:GetWorld|GetGameInstance)\s*\(", re.IGNORECASE),
        (
            "Do not resolve world context through GEngine. Use the owning "
            "actor/subsystem GetWorld() or pass an explicit world context."
        ),
    ),
    (
        "giveability_free",
        re.compile(r"(?<![\w>-])\bGiveAbility\s*\(\s*\)", re.IGNORECASE),
        (
            "GiveAbility is not a free function. Grant abilities through "
            "UAbilitySystemComponent::GiveAbility with a valid FGameplayAbilitySpec."
        ),
    ),
    (
        "tryactivateability_free",
        re.compile(r"(?<![\w>-])\bTryActivateAbility\s*\(\s*\)", re.IGNORECASE),
        (
            "TryActivateAbility requires a valid FGameplayAbilitySpecHandle from the "
            "owning AbilitySystemComponent; verify the exact ASC signature."
        ),
    ),
    (
        "getplayercontroller_zero_arg",
        re.compile(r"(?<![\w>.-])\bGetPlayerController\s*\(\s*\)", re.IGNORECASE),
        (
            "UGameplayStatics::GetPlayerController requires a world context object. "
            "Do not call a zero-argument GetPlayerController()."
        ),
    ),
    (
        "spawnemitteratlocation_zero_arg",
        re.compile(r"\bSpawnEmitterAtLocation\s*\(\s*\)", re.IGNORECASE),
        (
            "UGameplayStatics::SpawnEmitterAtLocation requires world context and "
            "location/rotation parameters; verify the exact overload."
        ),
    ),
    (
        "createwidget_no_context",
        re.compile(
            r"\bCreateWidget(?:Instance)?\s*(?:<[^>]+>)?\s*\(\s*\)",
            re.IGNORECASE,
        ),
        (
            "UUserWidget::CreateWidgetInstance or UWidgetBlueprintLibrary::Create "
            "require a valid owning player/world context; never call CreateWidget() alone."
        ),
    ),
    (
        "addtoviewport_free",
        re.compile(r"(?<![\w>-])\bAddToViewport\s*\(\s*\)", re.IGNORECASE),
        (
            "AddToViewport requires a constructed widget and valid player context; "
            "verify UMG widget creation APIs before use."
        ),
    ),
    (
        "isserver_free",
        re.compile(r"(?<![\w>-])\bIsServer\s*\(\s*\)", re.IGNORECASE),
        (
            "IsServer() is not a free helper. Use GetNetMode() == NM_DedicatedServer "
            "or actor authority checks with a valid world context."
        ),
    ),
    (
        "getnetmode_free",
        re.compile(r"(?<![\w>-])\bGetNetMode\s*\(\s*\)", re.IGNORECASE),
        (
            "GetNetMode() is a UWorld member. Resolve a UWorld* first; do not invent "
            "a global IsServer()/GetNetMode() helper."
        ),
    ),
)

_MEMBER_GET_PLAYER_CONTROLLER = re.compile(
    r"(?:->|\.)\s*GetPlayerController\s*\(\s*\)",
    re.IGNORECASE,
)
_MEMBER_ADD_TO_VIEWPORT = re.compile(
    r"(?:->|\.)\s*AddToViewport\s*\(\s*\)",
    re.IGNORECASE,
)
_MEMBER_IS_SERVER = re.compile(
    r"(?:->|\.)\s*IsServer\s*\(\s*\)",
    re.IGNORECASE,
)
_MEMBER_GET_NET_MODE = re.compile(
    r"(?:->|\.)\s*GetNetMode\s*\(\s*\)",
    re.IGNORECASE,
)


def _is_safe_member_call(masked: str, match: re.Match[str], member_pattern: re.Pattern[str]) -> bool:
    start = match.start()
    prefix = masked[max(0, start - 48) : start]
    return bool(member_pattern.search(prefix + match.group(0)))


def _scan(text: str, table: dict[str, str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    lowered = str(text or "").lower()
    for token, message in table.items():
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            finding: dict[str, Any] = {
                "term": token,
                "message": message,
                "matchKind": "token",
            }
            replacement = KNOWN_BAD_API_REPLACEMENTS.get(token)
            if replacement:
                finding["replacement"] = replacement
            findings.append(finding)
    return findings


def _is_safe_get_player_controller_call(masked: str, match: re.Match[str]) -> bool:
    start = match.start()
    prefix = masked[max(0, start - 48) : start]
    return bool(_MEMBER_GET_PLAYER_CONTROLLER.search(prefix + match.group(0)))


def _scan_editor_runtime(masked: str, original: str) -> list[dict[str, Any]]:
    """Flag GEditor/FEditorDelegates when not inside an active #if WITH_EDITOR block."""
    findings: list[dict[str, Any]] = []
    regions = preprocessor_editor_safe_regions(original)
    for term, pattern, message in (
        (
            "geditor_runtime",
            re.compile(r"\bGEditor\b"),
            (
                "GEditor and FEditorDelegates are editor-only. Guard with WITH_EDITOR and "
                "keep editor APIs out of runtime game modules."
            ),
        ),
        (
            "feditordelegates_runtime",
            re.compile(r"\bFEditorDelegates\b"),
            "FEditorDelegates are editor-only. Move editor wiring to an Editor module.",
        ),
    ):
        for match in pattern.finditer(masked):
            if offset_in_regions(match.start(), regions):
                continue
            findings.append(
                {
                    "term": term,
                    "message": message,
                    "replacement": KNOWN_BAD_API_REPLACEMENTS.get(term, ""),
                    "matchKind": "pattern",
                }
            )
            break
    return findings


def check_denylist(text: str) -> list[dict[str, Any]]:
    """Return known-bad lifecycle overrides and invented APIs mentioned in text."""
    original = str(text or "")
    masked = mask_comments_and_strings(original)
    findings = _scan(masked, INVALID_LIFECYCLE_OVERRIDES)
    findings.extend(_scan(masked, KNOWN_BAD_API))
    for term, pattern, message in KNOWN_BAD_API_PATTERNS:
        matched = False
        for match in pattern.finditer(masked):
            if term == "getplayercontroller_zero_arg" and _is_safe_get_player_controller_call(masked, match):
                continue
            if term == "addtoviewport_free" and _is_safe_member_call(masked, match, _MEMBER_ADD_TO_VIEWPORT):
                continue
            if term == "isserver_free" and _is_safe_member_call(masked, match, _MEMBER_IS_SERVER):
                continue
            if term == "getnetmode_free" and _is_safe_member_call(masked, match, _MEMBER_GET_NET_MODE):
                continue
            matched = True
            break
        if matched:
            findings.append(
                {
                    "term": term,
                    "message": message,
                    "replacement": KNOWN_BAD_API_REPLACEMENTS.get(term, ""),
                    "matchKind": "pattern",
                }
            )
    findings.extend(_scan_editor_runtime(masked, original))
    return findings
