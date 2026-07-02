#!/usr/bin/env python
"""Tests for Pass@K eval helpers."""

from __future__ import annotations

import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPTS = WORKSPACE / "scripts"
sys.path.insert(0, str(SCRIPTS))

from eval_pass_at_k import count_wrapper_attempts  # noqa: E402


def test_count_wrapper_attempts_handles_missing_dir(tmp_path):
    assert count_wrapper_attempts(tmp_path / "missing") == 0


def test_count_wrapper_attempts_counts_attempt_directories_only(tmp_path):
    run_dir = tmp_path / "wrapper_run"
    run_dir.mkdir()
    (run_dir / "attempt_1").mkdir()
    (run_dir / "attempt_2").mkdir()
    (run_dir / "attempt_notes.txt").write_text("not a directory", encoding="utf-8")
    (run_dir / "other").mkdir()

    assert count_wrapper_attempts(run_dir) == 2
