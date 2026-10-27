from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "analyze_lmstudio_run.py"
FIXTURE = ROOT / "tests" / "fixtures" / "lmstudio_run" / "synthetic.conversation.json"
# Synthetic stand-in for run 1792842246501 (no user code): same-sha re-reads
# around a compaction, archive pages of delivered versions, a cross-evidence
# version mismatch, an invented handle, a changed sha and a suppressed re-read.
REREAD_FIXTURE = ROOT / "tests" / "fixtures" / "lmstudio_run" / "reread_loop.conversation.json"
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_lmstudio_run import analyze_message, analyze_path, load_conversation  # noqa: E402


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


def test_synthetic_run_metrics() -> None:
    report = analyze_path(str(FIXTURE), None)

    assert report["messageIndex"] == 1
    assert report["loadedBuildIds"] == ["build-test-1"]
    assert report["rounds"] == 3
    assert report["toolCalls"] == 7
    assert report["toolCallsByName"] == {"read_symbol": 2, "replace_in_file": 2, "read_file": 1, "read_file_range": 1, "build_unreal_project": 1}
    assert report["thinkingBlocks"] == 1
    assert (report["executionS"], report["prefillS"], report["generationS"]) == (42.0, 12.0, 24.0)
    assert (report["promptTokens"], report["predictedTokens"], report["reasoningTokens"]) == (6000, 5300, 4650)
    assert report["reasoningRecoveries"] == 1
    assert [s["roundIndex"] for s in report["reasoningSpikes"]] == [1]


def test_projection_counts_only_changed_and_uses_round_prefill() -> None:
    report = analyze_path(str(FIXTURE), 1)

    assert report["projectionEvents"] == 2
    assert report["projections"] == 1
    assert report["projectionRounds"][0]["roundIndex"] == 2
    assert report["projectionPrefillS"] == 6.0


def test_source_reads_count_files_lines_and_rereads() -> None:
    report = analyze_path(str(FIXTURE), 1)

    assert (report["projectFiles"], report["engineFiles"]) == (1, 1)
    # 1-40 and 31-40 of Sample.h plus 100-129 of Actor.cpp; 31-40 is returned twice.
    assert report["returnedLines"] == 80
    assert report["uniqueLines"] == 70
    assert report["rereadLines"] == 10
    assert report["readFailures"] == {"SYMBOL_NOT_FOUND": 1}


def test_guidance_changes_and_write_build_outcomes() -> None:
    report = analyze_path(str(FIXTURE), 1)

    changes = report["guidanceChanges"]
    assert [c["roundIndex"] for c in changes] == [0, 2]
    assert changes[1]["added"] == ["unreal/responsibility"]
    assert changes[1]["removed"] == ["design/lifetime"]
    assert changes[1]["omissionReasons"] == ["returned_project_target"]
    assert [g["roundIndex"] for g in report["guidanceByRound"]] == [0, 1, 2]

    write_build = report["writeBuild"]
    assert write_build["replace_in_file"] == {"calls": 2, "outcomes": {"STALE_RECEIPT": 1, "ok": 1}}
    assert write_build["build_unreal_project"] == {"calls": 1, "outcomes": {"BUILD_FAILED": 1}}
    assert write_build["apply_edit_bundle"]["calls"] == 0


def test_rejects_non_assistant_message() -> None:
    with pytest.raises(ValueError):
        analyze_message(load_conversation(FIXTURE), 0)


def test_failure_without_code_and_metadata_without_body_are_not_successes():
    conversation = load_conversation(FIXTURE)
    blocks = [b for step in conversation["messages"][1]["versions"][1]["steps"] for b in step.get("content", [])]
    for block in blocks:
        if block.get("type") != "toolCallResult":
            continue
        outer = json.loads(block["content"])
        payload = json.loads(outer[0]["text"])
        if block["toolCallRequestId"] == "r1":
            payload.pop("content", None)
        if payload.get("errorCode") == "BUILD_FAILED":
            payload.pop("errorCode")
        outer[0]["text"] = json.dumps(payload)
        block["content"] = json.dumps(outer)
    report = analyze_message(conversation, 1)
    assert report["returnedLines"] == 40
    assert report["writeBuild"]["build_unreal_project"]["outcomes"] == {"failed_without_code": 1}


def test_cli_text_json_and_compare() -> None:
    text = _run(str(FIXTURE), "--message-index", "1")
    assert text.returncode == 0, text.stderr
    assert "rounds: 3" in text.stdout
    assert "execution 42.0 s, prefill 12.0 s, generation 24.0 s" in text.stdout

    as_json = _run(str(FIXTURE), "--json", "--compare", str(FIXTURE))
    assert as_json.returncode == 0, as_json.stderr
    payload = json.loads(as_json.stdout)
    assert payload["run"]["rounds"] == payload["compare"]["rounds"] == 3

    compare = _run(str(FIXTURE), "--compare", str(FIXTURE))
    assert compare.returncode == 0, compare.stderr
    assert "re-read lines" in compare.stdout

    missing = _run(str(FIXTURE.with_name("missing.conversation.json")))
    assert missing.returncode == 2


REFERENCE_RUNS = {
    "1792829847868": (39, 52, 1477.0, 451.1, 994.5, 15, 3, 2770),
    "1792815622255": (11, 21, 675.4, 182.6, 486.3, 14, 0, 1421),
}


@pytest.mark.smoke
@pytest.mark.parametrize("conversation_id", sorted(REFERENCE_RUNS))
def test_reference_conversations_when_present(conversation_id: str) -> None:
    path = Path.home() / ".lmstudio" / "conversations" / f"{conversation_id}.conversation.json"
    if not path.is_file():
        pytest.skip("reference conversation not on this machine")
    report = analyze_path(str(path), 3)
    actual = tuple(report[key] for key in (
        "rounds", "toolCalls", "executionS", "prefillS", "generationS", "projectFiles", "engineFiles", "returnedLines",
    ))
    assert actual == REFERENCE_RUNS[conversation_id]


def test_deliveries_by_path_sha_and_range_across_source_and_archive_reads() -> None:
    report = analyze_path(str(REREAD_FIXTURE), None)
    d = report["deliveries"]
    assert (d["deliveries"], d["sourceDeliveries"], d["archiveDeliveries"]) == (8, 6, 2)
    # c2/c3 same sha whole file, a1/a2 archive pages of delivered versions, c5 inside the new sha's whole read.
    assert d["redundant"] == report["redundantDeliveries"] == 5
    assert d["duplicatedChars"] == 3 * 2450 + 690 + 450
    assert d["duplicatedTokensEstimate"] == report["duplicatedTokensEstimate"] == round(8490 / 3.5)
    assert d["sourceRereads"] == 3
    # Only c3 lands within two rounds after the round-2 compaction.
    assert d["rereadsAfterCompaction"] == report["rereadsAfterCompaction"] == 1
    character, types = d["files"]
    assert character["path"].endswith("SampleCharacter.cpp")
    assert character["shas"] == {"a1a1a1a1": 4, "c3c3c3c3": 2}
    assert character["ranges"]["a1a1a1a1 lines 1-60"] == 4
    assert (character["deliveries"], character["redundant"], character["rereadsAfterCompaction"]) == (6, 4, 1)
    assert (types["deliveries"], types["redundant"], types["suppressed"]) == (2, 1, 1)


def test_cross_evidence_version_mismatch_invented_handles_and_ledger_events() -> None:
    report = analyze_path(str(REREAD_FIXTURE), None)
    d = report["deliveries"]
    assert (d["versionMismatches"], d["crossEvidenceVersionMismatches"]) == (1, 1)
    assert d["versionMismatchDetails"][0]["versionBelongsTo"].endswith("SampleTypes.h")
    assert d["inventedHandles"] == 1
    assert d["inventedHandleDetails"][0]["readHandle"] == "h_" + "9c" * 12
    assert d["suppressedByLedger"] == 1
    assert d["ledgerRedundantRereads"] == 1
    assert report["reportNowEvents"] == [{"roundIndex": 5, "trigger": 1, "finalize": False}]
    text = _run(str(REREAD_FIXTURE))
    assert text.returncode == 0, text.stderr
    assert "body deliveries: 8 (6 source, 2 archive), 5 redundant" in text.stdout
    assert "version mismatches: 1 (1 with another record's version), invented handles: 1" in text.stdout


# Baseline of run 1792842246501, before the read ledger: the measurement the
# fix is compared against (the run itself is not in the repository).
@pytest.mark.smoke
def test_reread_loop_reference_run_when_present() -> None:
    path = Path.home() / ".lmstudio" / "conversations" / "1792842246501.conversation.json"
    if not path.is_file():
        pytest.skip("reference conversation not on this machine")
    d = analyze_path(str(path), 3)["deliveries"]
    assert (d["sourceDeliveries"], d["sourceRereads"], d["rereadsAfterCompaction"]) == (49, 33, 15)
    assert (d["deliveries"], d["redundant"]) == (63, 47)
    assert (d["crossEvidenceVersionMismatches"], d["inventedHandles"]) == (1, 1)
    character = next(f for f in d["files"] if f["path"].endswith("LatencyMultiCombatCharacter.cpp"))
    assert character["shas"] == {"3cddf4aa": 7}
    assert character["redundant"] == 6

def test_v3_contract_telemetry_and_legacy_restoration_are_distinct() -> None:
    events = [
        {"event": "read_ledger", "firstFocusReads": 2, "restoreRequiredReads": 3, "restoredReads": 3, "repeatRestores": 1},
        {"event": "read_ledger", "restoredReads": 7},
        {"event": "working_context_restore", "status": "source_prefix_mismatch", "windowStatus": "rejected", "ledgerStatus": "not_restored", "accessInherited": False},
        {"event": "execution_outcome", "objectiveKind": "mutate", "mutationState": "applied"},
        {"event": "direct_round_observation", "roundIndex": 0, "callPurpose": "mutation_focus"},
        {"event": "system_prefix_fingerprint", "roundIndex": 0, "systemHash": "a", "previousSystemHash": None, "changed": False},
        {"event": "system_prefix_fingerprint", "roundIndex": 1, "systemHash": "b", "previousSystemHash": "a", "changed": True},
        {"event": "report_review_scheduled", "missing": ["ApplyDamage"]},
    ]
    conversation = {"messages": [{"versions": [{"role": "assistant", "steps": [
        {"type": "debugInfoBlock", "debugInfo": json.dumps(e)} for e in events]}]}]}
    report = analyze_message(conversation, 0)
    assert report["readLedger"] == {"measuredEvents": 1, "firstFocusReads": 2, "restoreRequiredReads": 3, "repeatRestores": 1, "legacyAmbiguousRestoredReads": 7}
    assert report["workingContextRestores"][0]["accessInherited"] is False
    assert report["executionOutcomes"][0]["mutationState"] == "applied"
    assert report["callPurposes"] == {"mutation_focus": 1}
    assert report["systemPrefixChanges"] == 1
    assert report["systemPrefixes"][1]["previousSystemHash"] == "a"
    assert report["reportReviewMissing"] == [["ApplyDamage"]]
