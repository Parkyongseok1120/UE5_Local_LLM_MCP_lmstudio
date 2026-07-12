#!/usr/bin/env python
"""Cross-process project mutation generation counter."""

from __future__ import annotations

import json
from pathlib import Path

from atomic_io import atomic_write_text


def _state_path(project_root: Path) -> Path:
    return (project_root / ".agent" / "state" / "mutation.json").resolve()


def default_state() -> dict:
    return {"mutationGeneration": 0, "paths": {}, "validatedGeneration": 0}


def read_state(project_root: Path) -> dict:
    path = _state_path(project_root)
    if not path.is_file():
        return default_state()
    try:
        return {**default_state(), **json.loads(path.read_text(encoding="utf-8"))}
    except Exception:
        return default_state()


def write_state(project_root: Path, state: dict) -> None:
    path = _state_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2))


def _state_path(project_root: Path) -> Path:
    return (project_root / ".agent" / "state" / "mutation.json").resolve()


def _with_mutation_lock(project_root: Path, fn):
    from time import sleep

    from write_locks import release_cross_process_lock, try_acquire_cross_process_lock

    state_path = _state_path(project_root)
    for attempt in range(40):
        acquired = try_acquire_cross_process_lock(state_path, "mutation_generation")
        if acquired.get("ok"):
            try:
                return fn()
            finally:
                release_cross_process_lock(state_path)
        sleep(min(0.05 * (attempt + 1), 0.5))
    raise RuntimeError("mutation generation lock busy")


def record_mutation(project_root: Path, rel_path: str, content_hash: str) -> int:
    def action() -> int:
        state = read_state(project_root)
        state["mutationGeneration"] = int(state.get("mutationGeneration") or 0) + 1
        state.setdefault("paths", {})[rel_path.replace("\\", "/")] = content_hash
        write_state(project_root, state)
        return int(state["mutationGeneration"])

    return _with_mutation_lock(project_root, action)


def finish_validation(project_root: Path, start_generation: int) -> dict:
    def action() -> dict:
        state = read_state(project_root)
        current = int(state.get("mutationGeneration") or 0)
        if current != int(start_generation):
            return {"validationStale": True, "validatedGeneration": None, "mutationGeneration": current}
        state["validatedGeneration"] = current
        write_state(project_root, state)
        return {"validationStale": False, "validatedGeneration": current, "mutationGeneration": current}

    return _with_mutation_lock(project_root, action)
