#!/usr/bin/env python
"""Single source of truth for known-bad / commonly hallucinated Unreal API patterns.

Both the refactor plan validator and the chat-side code sketch validator consult
these tables so that invented APIs (a frequent small-model failure) are flagged
before they reach code. Entries are intentionally conservative: only add a term
here when it is a well-established hallucination or a wrong lifecycle override,
not merely an API that happens to be missing from the local index.
"""

from __future__ import annotations

import re
from typing import Any

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
KNOWN_BAD_API: dict[str, str] = {
    # Sequencer / MovieScene: bRestoreState is not a public playback flag on
    # ULevelSequencePlayer/ULevelSequenceActor. Restore-on-finish is controlled by
    # the sequence's Restore State option and playback settings, not a bool field.
    "brestorestate": (
        "bRestoreState is not a public flag on the sequence player/actor. "
        "Restore-on-finish is driven by the LevelSequence 'Restore State' setting "
        "and FMovieSceneSequencePlaybackSettings; verify the exact API before use."
    ),
    "setrestorestate": (
        "SetRestoreState is not a standard UE Sequencer API. Configure restore "
        "behavior through the sequence asset / playback settings and verify headers."
    ),
    # Confusing binding APIs that small models invent by analogy.
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
    "giveability": (
        "GiveAbility is not a free function. Grant abilities through "
        "UAbilitySystemComponent::GiveAbility with a valid FGameplayAbilitySpec."
    ),
    "tryactivateability": (
        "TryActivateAbility requires a valid FGameplayAbilitySpecHandle from the "
        "owning AbilitySystemComponent; verify the exact ASC signature."
    ),
    "getplayercontroller": (
        "UGameplayStatics::GetPlayerController requires a world context object. "
        "Do not call a zero-argument GetPlayerController()."
    ),
    "spawnemitteratlocation": (
        "UGameplayStatics::SpawnEmitterAtLocation requires world context and "
        "location/rotation parameters; verify the exact overload."
    ),
    "createwidget": (
        "UUserWidget::CreateWidgetInstance or UWidgetBlueprintLibrary::Create "
        "require a valid owning player/world context; never call CreateWidget() alone."
    ),
    "addtoviewport": (
        "AddToViewport requires a constructed widget and valid player context; "
        "verify UMG widget creation APIs before use."
    ),
    "hasauthority": (
        "HasAuthority() is an AActor member. Subsystems/components need "
        "GetOwner()->HasAuthority() or Role checks on the relevant actor."
    ),
    "isserver": (
        "IsServer() is not a free helper. Use GetNetMode() == NM_DedicatedServer "
        "or actor authority checks with a valid world context."
    ),
    "getnetmode": (
        "GetNetMode() is a UWorld member. Resolve a UWorld* first; do not invent "
        "a global IsServer()/GetNetMode() helper."
    ),
    "geditor": (
        "GEditor and FEditorDelegates are editor-only. Guard with WITH_EDITOR and "
        "keep editor APIs out of runtime game modules."
    ),
    "feditordelegates": (
        "FEditorDelegates are editor-only. Move editor wiring to an Editor module."
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
}

# Context-sensitive patterns cannot live in the token-only table. GetURL is a
# valid member on other types (for example IHttpRequest), so only world
# receivers are rejected here.
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
)


def _scan(text: str, table: dict[str, str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    lowered = str(text or "").lower()
    for token, message in table.items():
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            finding = {"term": token, "message": message}
            replacement = KNOWN_BAD_API_REPLACEMENTS.get(token)
            if replacement:
                finding["replacement"] = replacement
            findings.append(finding)
    return findings


def check_denylist(text: str) -> list[dict[str, Any]]:
    """Return known-bad lifecycle overrides and invented APIs mentioned in text."""
    findings = _scan(text, INVALID_LIFECYCLE_OVERRIDES)
    findings.extend(_scan(text, KNOWN_BAD_API))
    for term, pattern, message in KNOWN_BAD_API_PATTERNS:
        if pattern.search(str(text or "")):
            findings.append(
                {
                    "term": term,
                    "message": message,
                    "replacement": KNOWN_BAD_API_REPLACEMENTS.get(term, ""),
                }
            )
    return findings
