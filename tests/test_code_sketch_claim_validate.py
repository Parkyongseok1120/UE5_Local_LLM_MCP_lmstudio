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


def test_denylist_flags_replication_and_gas_hallucinations():
    terms = {hit["term"] for hit in check_denylist("ReplicateVariable(); GiveAbility(); GEditor->")}
    assert "replicatevariable" in terms
    assert "giveability" in terms
    assert "geditor" in terms

