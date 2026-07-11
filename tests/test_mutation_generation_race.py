from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from mutation_generation import finish_validation, read_state, record_mutation, write_state  # noqa: E402


def test_validation_stale_when_generation_changes(tmp_path: Path) -> None:
    project = tmp_path / "Game"
    project.mkdir()
    start = read_state(project)
    start_gen = int(start["mutationGeneration"])
    record_mutation(project, "Source/A.cpp", "hash-a")
    result = finish_validation(project, start_gen)
    assert result["validationStale"] is True
    assert result["validatedGeneration"] is None


def test_validation_succeeds_when_generation_stable(tmp_path: Path) -> None:
    project = tmp_path / "Game"
    project.mkdir()
    state = read_state(project)
    gen = int(state["mutationGeneration"])
    result = finish_validation(project, gen)
    assert result["validationStale"] is False
    assert result["validatedGeneration"] == gen
