"""Tests for fail-closed failure-memory retrieval and rerank helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from failure_memory_rerank import (  # noqa: E402
    chunk_boost_for_memory,
    expand_query_with_memory,
    load_failure_records,
    reject_failure_record,
)


def _trusted_row(
    record_id: str,
    *,
    project: str = "Test",
    status: str = "verified",
    engine_version: str = "5.8",
    project_fingerprint: str = "project-a",
    artifact_hash: str = "build-a",
    verification_count: int = 1,
    expires_at: str = "2099-01-01T00:00:00+00:00",
) -> dict:
    verification_history = [
        {
            "engineVersion": engine_version,
            "projectFingerprint": project_fingerprint,
            "buildProof": {
                "ok": True,
                "artifactHash": (
                    artifact_hash
                    if index == verification_count - 1
                    else f"{artifact_hash}-{index}"
                ),
            },
        }
        for index in range(max(1, verification_count))
    ]
    evidence = verification_history[-1]
    return {
        "id": record_id,
        "status": status,
        "fix_summary": "added include",
        "error_signature": record_id,
        "good_chunk_ids": ["engine:good"],
        "bad_chunk_ids": ["memory:bad"],
        "expiresAt": expires_at,
        "engineVersion": engine_version,
        "projectFingerprint": project_fingerprint,
        "verificationCount": verification_count,
        "verificationEvidence": evidence,
        "verificationHistory": verification_history,
        "metadata": {
            "project": project,
            "engineVersion": engine_version,
            "projectFingerprint": project_fingerprint,
            "status": status,
        },
    }


def _write_rows(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_expand_query_no_memory(tmp_path: Path) -> None:
    q = expand_query_with_memory("C1083 missing include", tmp_path)
    assert q == "C1083 missing include"


def test_reject_failure_record_appends_terminal_update(tmp_path: Path) -> None:
    path = tmp_path / "Test_failures.jsonl"
    _write_rows(path, [_trusted_row("abc123", status="accepted", verification_count=2)])

    assert reject_failure_record(tmp_path, "Test", "abc123") is True
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 2
    assert rows[-1]["status"] == "rejected"
    assert load_failure_records(tmp_path) == []


def test_only_latest_verified_unexpired_memory_is_loaded(
    tmp_path: Path,
) -> None:
    path = tmp_path / "Test_failures.jsonl"
    _write_rows(
        path,
        [
            {"id": "candidate", "status": "candidate", "fix_summary": "guess"},
            {"id": "promoted", "status": "candidate", "fix_summary": "old"},
            _trusted_row("promoted"),
            _trusted_row(
                "expired",
                expires_at="2020-01-01T00:00:00+00:00",
            ),
        ],
    )

    loaded = load_failure_records(
        tmp_path,
        project="Test",
        engine_version="5.8",
        project_fingerprint="project-a",
    )
    assert [row["id"] for row in loaded] == ["promoted"]
    assert "added include" in expand_query_with_memory("promoted", tmp_path)


def test_rejection_append_supersedes_previous_verified_record(
    tmp_path: Path,
) -> None:
    path = tmp_path / "Test_failures.jsonl"
    _write_rows(path, [_trusted_row("abc123")])

    assert reject_failure_record(tmp_path, "Test", "abc123") is True
    assert load_failure_records(tmp_path) == []


def test_same_signature_is_kept_per_project(tmp_path: Path) -> None:
    for project, fingerprint in (("Alpha", "alpha-fp"), ("Beta", "beta-fp")):
        _write_rows(
            tmp_path / f"{project}_failures.jsonl",
            [
                _trusted_row(
                    "same",
                    project=project,
                    project_fingerprint=fingerprint,
                )
            ],
        )

    assert len(load_failure_records(tmp_path)) == 2
    assert [
        row["metadata"]["project"]
        for row in load_failure_records(tmp_path, project="Alpha")
    ] == ["Alpha"]


def test_project_query_scope_is_exact_not_substring(tmp_path: Path) -> None:
    _write_rows(
        tmp_path / "TestGame_failures.jsonl",
        [_trusted_row("same", project="TestGame")],
    )

    assert load_failure_records(tmp_path, project="Test") == []
    assert len(load_failure_records(tmp_path, project="TestGame")) == 1


def test_missing_or_mismatched_record_project_scope_is_rejected(
    tmp_path: Path,
) -> None:
    valid = _trusted_row("valid")
    missing = _trusted_row("missing")
    missing["metadata"].pop("project")
    mismatch = _trusted_row("mismatch", project="Other")
    _write_rows(
        tmp_path / "Test_failures.jsonl",
        [valid, missing, mismatch],
    )

    loaded = load_failure_records(tmp_path, project="Test")
    assert [row["id"] for row in loaded] == ["valid"]


def test_engine_and_fingerprint_query_scope_fail_closed(tmp_path: Path) -> None:
    _write_rows(
        tmp_path / "Test_failures.jsonl",
        [_trusted_row("valid")],
    )

    assert load_failure_records(tmp_path, engine_version="5.7") == []
    assert load_failure_records(tmp_path, project_fingerprint="project-b") == []
    assert len(
        load_failure_records(
            tmp_path,
            engine_version="5.8",
            project_fingerprint="project-a",
        )
    ) == 1


def test_promoted_record_missing_scope_or_proof_is_never_loaded(
    tmp_path: Path,
) -> None:
    missing_engine = _trusted_row("missing-engine")
    missing_engine["engineVersion"] = ""
    missing_engine["verificationEvidence"]["engineVersion"] = ""
    missing_fingerprint = _trusted_row("missing-fingerprint")
    missing_fingerprint["projectFingerprint"] = ""
    missing_fingerprint["verificationEvidence"]["projectFingerprint"] = ""
    missing_proof = _trusted_row("missing-proof")
    missing_proof["verificationEvidence"].pop("buildProof")
    false_proof = _trusted_row("false-proof")
    false_proof["verificationEvidence"]["buildProof"]["ok"] = False
    no_artifact = _trusted_row("no-artifact")
    no_artifact["verificationEvidence"]["buildProof"]["artifactHash"] = ""
    _write_rows(
        tmp_path / "Test_failures.jsonl",
        [
            missing_engine,
            missing_fingerprint,
            missing_proof,
            false_proof,
            no_artifact,
        ],
    )

    assert load_failure_records(tmp_path) == []


def test_accepted_record_requires_verification_threshold(tmp_path: Path) -> None:
    _write_rows(
        tmp_path / "Test_failures.jsonl",
        [
            _trusted_row(
                "under-threshold",
                status="accepted",
                verification_count=1,
            ),
            _trusted_row(
                "accepted",
                status="accepted",
                verification_count=2,
                artifact_hash="build-b",
            ),
        ],
    )

    assert [row["id"] for row in load_failure_records(tmp_path)] == ["accepted"]


def test_claimed_verification_count_must_match_distinct_proof_history(
    tmp_path: Path,
) -> None:
    inflated = _trusted_row(
        "inflated",
        status="accepted",
        verification_count=2,
    )
    inflated["verificationHistory"] = [inflated["verificationEvidence"]]
    duplicate = _trusted_row(
        "duplicate",
        status="accepted",
        verification_count=2,
    )
    duplicate["verificationHistory"] = [
        duplicate["verificationEvidence"],
        dict(duplicate["verificationEvidence"]),
    ]
    _write_rows(
        tmp_path / "Test_failures.jsonl",
        [inflated, duplicate],
    )

    assert load_failure_records(tmp_path) == []


def test_non_object_json_lines_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "Test_failures.jsonl"
    path.write_text(
        "null\n[]\n"
        + json.dumps(_trusted_row("valid"))
        + "\n\"scalar\"\n",
        encoding="utf-8",
    )

    assert [row["id"] for row in load_failure_records(tmp_path)] == ["valid"]


def test_chunk_boost_requires_trusted_record_and_exact_scope(
    tmp_path: Path,
) -> None:
    _write_rows(
        tmp_path / "Test_failures.jsonl",
        [_trusted_row("C1083")],
    )

    assert chunk_boost_for_memory(
        "engine:good",
        {},
        tmp_path,
        project="Test",
        engine_version="5.8",
        project_fingerprint="project-a",
    ) == 0.15
    assert chunk_boost_for_memory(
        "memory:bad",
        {},
        tmp_path,
        project="Test",
        engine_version="5.8",
        project_fingerprint="project-a",
    ) == -0.15
    assert chunk_boost_for_memory(
        "engine:good",
        {},
        tmp_path,
        project="Test",
        engine_version="5.7",
        project_fingerprint="project-a",
    ) == 0.0
    assert chunk_boost_for_memory(
        "unrelated",
        {"source": "unreal_failure_memory"},
        tmp_path,
        project="Test",
    ) == 0.0


def test_expand_query_uses_only_scope_matching_trusted_records(
    tmp_path: Path,
) -> None:
    row = _trusted_row("C1083")
    row["error_signature"] = "C1083"
    row["fix_summary"] = "include the owning module header"
    _write_rows(tmp_path / "Test_failures.jsonl", [row])

    expanded = expand_query_with_memory(
        "C1083 missing include",
        tmp_path,
        project="Test",
        engine_version="5.8",
        project_fingerprint="project-a",
    )
    assert "include the owning module header" in expanded
    assert (
        expand_query_with_memory(
            "C1083 missing include",
            tmp_path,
            project="Test",
            engine_version="5.7",
            project_fingerprint="project-a",
        )
        == "C1083 missing include"
    )
