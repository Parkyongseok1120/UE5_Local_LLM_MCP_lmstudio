from __future__ import annotations

import concurrent.futures
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
AGENT = ROOT / "lmstudio-unreal-agent-mcp"
sys.path.insert(0, str(SCRIPTS))

from mutation_generation import finish_validation, read_state, record_mutation  # noqa: E402


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


def test_corrupt_mutation_state_raises(tmp_path: Path) -> None:
    from mutation_generation import MutationStateCorruptError

    project = tmp_path / "Game"
    project.mkdir()
    state_file = project / ".agent" / "state" / "mutation.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text("{bad json", encoding="utf-8")
    with pytest.raises(MutationStateCorruptError):
        read_state(project)


def test_concurrent_python_mutations_preserve_paths(tmp_path: Path, monkeypatch) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setenv("AGENT_STATE_ROOT", str(state_root))
    project = tmp_path / "Game"
    project.mkdir()

    def worker(rel: str, digest: str) -> int:
        return record_mutation(project, rel, digest)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        gens = list(pool.map(worker, ["A.cpp", "B.cpp", "C.cpp", "D.cpp"], ["h1", "h2", "h3", "h4"]))

    state = read_state(project)
    assert int(state["mutationGeneration"]) == max(gens)
    assert state["paths"]["A.cpp"] == "h1"
    assert state["paths"]["B.cpp"] == "h2"
    assert state["paths"]["C.cpp"] == "h3"
    assert state["paths"]["D.cpp"] == "h4"


def test_node_and_python_share_mutation_lock(tmp_path: Path, monkeypatch) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setenv("AGENT_STATE_ROOT", str(state_root))
    project = tmp_path / "Game"
    project.mkdir()

    record_mutation(project, "Py.cpp", "py-hash")
    node_script = """
const { recordMutation } = require('./src/mutation-generation');
(async () => {
  await recordMutation(process.argv[1], 'Node.cpp', 'node-content');
  console.log('ok');
})().catch((err) => { console.error(err); process.exit(1); });
"""
    proc = subprocess.run(
        ["node", "-e", node_script, str(project)],
        cwd=str(AGENT),
        env={**os.environ, "AGENT_STATE_ROOT": str(state_root)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    state = read_state(project)
    assert int(state["mutationGeneration"]) >= 2
    assert state["paths"]["Py.cpp"] == "py-hash"
    assert "Node.cpp" in state["paths"]
