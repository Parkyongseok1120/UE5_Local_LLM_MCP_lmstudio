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
}

# Context-sensitive patterns: real API names used in invalid call shapes.
KNOWN_BAD_API_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
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
