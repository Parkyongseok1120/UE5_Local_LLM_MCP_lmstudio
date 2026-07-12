from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from atomic_io import atomic_create_text  # noqa: E402


def test_atomic_create_text_rejects_existing(tmp_path: Path) -> None:
    target = tmp_path / "new.txt"
    atomic_create_text(target, "one\n")
    with pytest.raises(FileExistsError):
        atomic_create_text(target, "two\n")


def test_atomic_create_text_exactly_one_concurrent_winner(tmp_path: Path) -> None:
    target = tmp_path / "race.txt"
    successes = 0
    failures = 0

    def attempt() -> None:
        nonlocal successes, failures
        try:
            atomic_create_text(target, "winner\n")
            successes += 1
        except FileExistsError:
            failures += 1

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(attempt) for _ in range(20)]
        for future in as_completed(futures):
            future.result()

    assert successes == 1
    assert failures == 19
    assert target.read_text(encoding="utf-8") == "winner\n"
