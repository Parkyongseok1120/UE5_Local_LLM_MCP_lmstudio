"""Tests for failure memory rerank helpers."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from failure_memory_rerank import (  # noqa: E402
    expand_query_with_memory,
    load_failure_records,
    reject_failure_record,
)


def test_expand_query_no_memory():
    with tempfile.TemporaryDirectory() as tmp:
        q = expand_query_with_memory("C1083 missing include", Path(tmp))
        assert q == "C1083 missing include"


def test_reject_failure_record():
    with tempfile.TemporaryDirectory() as tmp:
        mem = Path(tmp)
        path = mem / "Test_failures.jsonl"
        row = {"id": "abc123", "status": "accepted", "fix_summary": "added include"}
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        assert reject_failure_record(mem, "Test", "abc123") is True
        updated = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
        assert updated["status"] == "rejected"


def test_only_latest_verified_unexpired_memory_is_loaded():
    with tempfile.TemporaryDirectory() as tmp:
        mem = Path(tmp)
        path = mem / "Test_failures.jsonl"
        rows = [
            {"id": "candidate", "status": "candidate", "fix_summary": "guess"},
            {"id": "promoted", "status": "candidate", "fix_summary": "old"},
            {
                "id": "promoted",
                "status": "verified",
                "fix_summary": "proven",
                "expiresAt": "2099-01-01T00:00:00+00:00",
                "engineVersion": "5.6",
            },
            {
                "id": "expired",
                "status": "verified",
                "fix_summary": "stale",
                "expiresAt": "2020-01-01T00:00:00+00:00",
            },
        ]
        path.write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n",
            encoding="utf-8",
        )

        loaded = load_failure_records(mem, engine_version="5.6")
        assert [row["id"] for row in loaded] == ["promoted"]
        assert expand_query_with_memory("promoted", mem) == "promoted"


def test_rejection_append_supersedes_previous_verified_record():
    with tempfile.TemporaryDirectory() as tmp:
        mem = Path(tmp)
        path = mem / "Test_failures.jsonl"
        path.write_text(
            json.dumps(
                {
                    "id": "abc123",
                    "status": "verified",
                    "fix_summary": "added include",
                    "expiresAt": "2099-01-01T00:00:00+00:00",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        assert reject_failure_record(mem, "Test", "abc123") is True
        assert load_failure_records(mem) == []


def test_same_signature_is_kept_per_project():
    with tempfile.TemporaryDirectory() as tmp:
        mem = Path(tmp)
        row = {
            "id": "same",
            "status": "verified",
            "expiresAt": "2099-01-01T00:00:00+00:00",
        }
        for project in ("Alpha", "Beta"):
            (mem / f"{project}_failures.jsonl").write_text(
                json.dumps({**row, "project": project}) + "\n",
                encoding="utf-8",
            )

        assert len(load_failure_records(mem)) == 2
