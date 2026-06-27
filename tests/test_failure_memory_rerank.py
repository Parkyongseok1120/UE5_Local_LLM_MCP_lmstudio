"""Tests for failure memory rerank helpers."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from failure_memory_rerank import expand_query_with_memory, reject_failure_record  # noqa: E402


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
        updated = json.loads(path.read_text(encoding="utf-8").strip())
        assert updated["status"] == "rejected"
