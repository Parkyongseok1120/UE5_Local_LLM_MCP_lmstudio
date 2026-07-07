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
}


def _scan(text: str, table: dict[str, str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    lowered = str(text or "").lower()
    for token, message in table.items():
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            findings.append({"term": token, "message": message})
    return findings


def check_denylist(text: str) -> list[dict[str, Any]]:
    """Return known-bad lifecycle overrides and invented APIs mentioned in text."""
    findings = _scan(text, INVALID_LIFECYCLE_OVERRIDES)
    findings.extend(_scan(text, KNOWN_BAD_API))
    return findings
