"""Archived tests for the removed failure-memory lifecycle."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from failure_memory import (  # noqa: E402
    append_failure_memory,
    signature,
    update_failure_memory_status,
)


def _append_candidate(
    tmp_path: Path,
    *,
    status: str = "candidate",
    engine_version: str = "5.8",
    project_fingerprint: str = "project-a",
) -> tuple[Path, str]:
    path = append_failure_memory(
        tmp_path,
        "Test",
        error_subkind="COMPILE_INCLUDE",
        error_code="C1083",
        symbol_name="Widget.h",
        failed_summary="missing include",
        fix_summary="add include",
        changed_files=["Source/Test/Widget.cpp"],
        diff_excerpt="+#include \"Widget.h\"",
        rag_evidence_ids=["engine:widget"],
        status=status,
        engine_version=engine_version,
        project_fingerprint=project_fingerprint,
    )
    return path, signature("COMPILE_INCLUDE", "C1083", "Widget.h")


def _proof(
    artifact_hash: str,
    *,
    engine_version: str = "5.8",
    project_fingerprint: str = "project-a",
    runtime: bool = False,
) -> dict:
    proof_name = "runtimeProof" if runtime else "buildProof"
    return {
        "engineVersion": engine_version,
        "projectFingerprint": project_fingerprint,
        proof_name: {
            "ok": True,
            "artifactHash": artifact_hash,
        },
    }


def _rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_untrusted_initial_verified_status_is_downgraded_to_candidate(
    tmp_path: Path,
) -> None:
    path, _ = _append_candidate(tmp_path, status="verified")

    row = _rows(path)[-1]
    assert row["status"] == "candidate"
    assert row["verificationCount"] == 0
    assert "untrusted_initial_status_downgraded:verified" in (
        row["lifecycleHistory"][-1]["reason"]
    )


def test_verified_requires_proof_artifact_and_complete_scope(tmp_path: Path) -> None:
    path, record_id = _append_candidate(
        tmp_path,
        engine_version="",
        project_fingerprint="",
    )
    rejected_evidence = [
        {},
        {"engineVersion": "5.8", "projectFingerprint": "project-a"},
        {
            "engineVersion": "5.8",
            "projectFingerprint": "project-a",
            "buildProof": {"ok": True},
        },
        {
            "engineVersion": "5.8",
            "projectFingerprint": "project-a",
            "buildProof": {"ok": False, "artifactHash": "build-a"},
        },
        {
            "engineVersion": "",
            "projectFingerprint": "project-a",
            "buildProof": {"ok": True, "artifactHash": "build-a"},
        },
        {
            "engineVersion": "5.8",
            "projectFingerprint": "",
            "runtimeProof": {"ok": True, "artifactHash": "trace-a"},
        },
    ]

    for evidence in rejected_evidence:
        before = len(_rows(path))
        assert (
            update_failure_memory_status(
                tmp_path,
                "Test",
                record_id,
                status="verified",
                verification_evidence=evidence,
            )
            is False
        )
        assert len(_rows(path)) == before

    assert update_failure_memory_status(
        tmp_path,
        "Test",
        record_id,
        status="verified",
        verification_evidence=_proof("build-a"),
    )
    row = _rows(path)[-1]
    assert row["engineVersion"] == "5.8"
    assert row["projectFingerprint"] == "project-a"
    assert row["verificationCount"] == 1


def test_verification_scope_must_match_candidate_scope(tmp_path: Path) -> None:
    path, record_id = _append_candidate(tmp_path)

    assert not update_failure_memory_status(
        tmp_path,
        "Test",
        record_id,
        status="verified",
        verification_evidence=_proof("build-a", engine_version="5.7"),
    )
    assert not update_failure_memory_status(
        tmp_path,
        "Test",
        record_id,
        status="verified",
        verification_evidence=_proof(
            "build-a",
            project_fingerprint="different-project",
        ),
    )
    assert len(_rows(path)) == 1


def test_acceptance_requires_two_distinct_verifications(tmp_path: Path) -> None:
    path, record_id = _append_candidate(tmp_path)

    assert update_failure_memory_status(
        tmp_path,
        "Test",
        record_id,
        status="verified",
        verification_evidence=_proof("build-a"),
    )
    assert not update_failure_memory_status(
        tmp_path,
        "Test",
        record_id,
        status="accepted",
        acceptance_threshold=1,
    )
    assert not update_failure_memory_status(
        tmp_path,
        "Test",
        record_id,
        status="verified",
        verification_evidence=_proof("build-a"),
    )
    assert update_failure_memory_status(
        tmp_path,
        "Test",
        record_id,
        status="verified",
        verification_evidence=_proof("trace-a", runtime=True),
    )
    assert update_failure_memory_status(
        tmp_path,
        "Test",
        record_id,
        status="accepted",
        verification_evidence={
            "engineVersion": "5.8",
            "projectFingerprint": "project-a",
        },
    )

    row = _rows(path)[-1]
    assert row["status"] == "accepted"
    assert row["verificationCount"] == 2
    assert len(row["verificationHistory"]) == 2
    assert [event["to"] for event in row["lifecycleHistory"]] == [
        "candidate",
        "verified",
        "verified",
        "accepted",
    ]


def test_candidate_cannot_inherit_a_forged_verification_count(
    tmp_path: Path,
) -> None:
    path, record_id = _append_candidate(tmp_path)
    rows = _rows(path)
    rows[-1]["verificationCount"] = 99
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    assert update_failure_memory_status(
        tmp_path,
        "Test",
        record_id,
        status="verified",
        verification_evidence=_proof("build-a"),
    )
    assert _rows(path)[-1]["verificationCount"] == 1
    assert not update_failure_memory_status(
        tmp_path,
        "Test",
        record_id,
        status="accepted",
    )


def test_malformed_json_values_are_ignored_during_update(tmp_path: Path) -> None:
    path, record_id = _append_candidate(tmp_path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("null\n")
        handle.write("[]\n")

    assert update_failure_memory_status(
        tmp_path,
        "Test",
        record_id,
        status="verified",
        verification_evidence=_proof("build-a"),
    )
    assert _rows(path)[-1]["status"] == "verified"


def test_malformed_evidence_and_mismatched_embedded_project_fail_closed(
    tmp_path: Path,
) -> None:
    path, record_id = _append_candidate(tmp_path)
    assert not update_failure_memory_status(
        tmp_path,
        "Test",
        record_id,
        status="verified",
        verification_evidence=["not", "an", "object"],  # type: ignore[arg-type]
    )

    rows = _rows(path)
    rows[-1]["metadata"]["project"] = "Other"
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    assert not update_failure_memory_status(
        tmp_path,
        "Test",
        record_id,
        status="verified",
        verification_evidence=_proof("build-a"),
    )


def test_acceptance_rejects_mismatched_explicit_scope(tmp_path: Path) -> None:
    _, record_id = _append_candidate(tmp_path)
    for artifact in ("build-a", "build-b"):
        assert update_failure_memory_status(
            tmp_path,
            "Test",
            record_id,
            status="verified",
            verification_evidence=_proof(artifact),
        )

    assert not update_failure_memory_status(
        tmp_path,
        "Test",
        record_id,
        status="accepted",
        verification_evidence={
            "engineVersion": "5.7",
            "projectFingerprint": "project-a",
        },
    )
    assert not update_failure_memory_status(
        tmp_path,
        "Test",
        record_id,
        status="accepted",
        verification_evidence={
            "engineVersion": "5.8",
            "projectFingerprint": "project-b",
        },
    )


def test_expired_and_rejected_are_terminal(tmp_path: Path) -> None:
    path, record_id = _append_candidate(tmp_path)
    assert update_failure_memory_status(
        tmp_path,
        "Test",
        record_id,
        status="expired",
    )
    expired_count = len(_rows(path))
    for status in ("candidate", "verified", "accepted", "rejected"):
        assert not update_failure_memory_status(
            tmp_path,
            "Test",
            record_id,
            status=status,
            verification_evidence=_proof("build-a"),
        )
    assert len(_rows(path)) == expired_count

    other_path = append_failure_memory(
        tmp_path,
        "Other",
        error_subkind="LINK",
        error_code="LNK2019",
        symbol_name="Missing",
        failed_summary="link",
        fix_summary="module",
        changed_files=["Source/Other/Other.Build.cs"],
        diff_excerpt="+Module",
        rag_evidence_ids=[],
    )
    other_id = signature("LINK", "LNK2019", "Missing")
    assert update_failure_memory_status(
        tmp_path,
        "Other",
        other_id,
        status="rejected",
    )
    assert not update_failure_memory_status(
        tmp_path,
        "Other",
        other_id,
        status="verified",
        verification_evidence=_proof("build-b"),
    )
    assert _rows(other_path)[-1]["status"] == "rejected"


def test_elapsed_candidate_cannot_be_promoted(tmp_path: Path) -> None:
    path, record_id = _append_candidate(tmp_path)
    rows = _rows(path)
    rows[-1]["expiresAt"] = "2020-01-01T00:00:00+00:00"
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    assert not update_failure_memory_status(
        tmp_path,
        "Test",
        record_id,
        status="verified",
        verification_evidence=_proof("build-a"),
    )


def test_reject_remains_available_after_acceptance(tmp_path: Path) -> None:
    path, record_id = _append_candidate(tmp_path)
    for artifact in ("build-a", "build-b"):
        assert update_failure_memory_status(
            tmp_path,
            "Test",
            record_id,
            status="verified",
            verification_evidence=_proof(artifact),
        )
    assert update_failure_memory_status(
        tmp_path,
        "Test",
        record_id,
        status="accepted",
    )
    assert update_failure_memory_status(
        tmp_path,
        "Test",
        record_id,
        status="rejected",
        verification_evidence={"reason": "regression reproduced"},
    )
    assert _rows(path)[-1]["status"] == "rejected"
    assert not update_failure_memory_status(
        tmp_path,
        "Test",
        record_id,
        status="verified",
        verification_evidence=_proof("build-c"),
    )
