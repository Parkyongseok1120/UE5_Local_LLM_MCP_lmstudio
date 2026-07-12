from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from plan_slice_state import (  # noqa: E402
    init_slice_state,
    mark_slice_complete,
    terminal_status_for_plan,
)


def _plan() -> list[dict]:
    return [
        {"slice_id": "a", "slice_kind": "compile", "files": ["Source/Demo/Private/Demo.cpp"]},
        {"slice_id": "b", "slice_kind": "compile", "files": ["Source/Demo/Public/Demo.h"]},
    ]


def test_terminal_index_reaches_len_on_final_slice(tmp_path: Path) -> None:
    root = tmp_path / "Demo"
    cpp = root / "Source" / "Demo" / "Private" / "Demo.cpp"
    cpp.parent.mkdir(parents=True)
    cpp.write_text("void A() {}\n", encoding="utf-8")
    state = init_slice_state(_plan())
    state = mark_slice_complete(
        state,
        project_root=root,
        written_paths=[cpp],
        plan_slices=_plan(),
        proof_level="Built",
    )
    assert state["activeSliceIndex"] == 1
    assert state["completed"] is False
    header = root / "Source" / "Demo" / "Public" / "Demo.h"
    header.parent.mkdir(parents=True)
    header.write_text("class X {};\n", encoding="utf-8")
    state = mark_slice_complete(
        state,
        project_root=root,
        written_paths=[header],
        plan_slices=_plan(),
        proof_level="Built",
    )
    assert state["activeSliceIndex"] == 2
    assert state["completed"] is True


def test_wrong_file_does_not_complete_slice(tmp_path: Path) -> None:
    root = tmp_path / "Demo"
    wrong = root / "Source" / "Demo" / "Private" / "Other.cpp"
    wrong.parent.mkdir(parents=True)
    wrong.write_text("void B() {}\n", encoding="utf-8")
    state = init_slice_state(_plan())
    state = mark_slice_complete(
        state,
        project_root=root,
        written_paths=[wrong],
        plan_slices=_plan(),
        proof_level="Built",
    )
    assert state["slices"][0]["status"] == "failed"
    assert state["failed"] is True


def test_compile_fix_terminal_status() -> None:
    state = {"completed": True}
    assert (
        terminal_status_for_plan(
            task_kind="compile_fix",
            mode="compile_fix",
            executable_slice_count=0,
            slice_state=state,
        )
        == "COMPILE_FIX_COMPLETE"
    )
