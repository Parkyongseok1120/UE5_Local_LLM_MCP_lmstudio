from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from patch_candidate_sandbox import (  # noqa: E402
    materialize_patch_candidate_sandboxes,
)


PATCH_A = """diff --git a/Source/Thing.cpp b/Source/Thing.cpp
--- a/Source/Thing.cpp
+++ b/Source/Thing.cpp
@@ -1 +1 @@
-before
+after-a
"""
PATCH_B = PATCH_A.replace("after-a", "after-b")


def test_materializes_candidates_outside_project_with_argv_runner(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    (project / "Source").mkdir(parents=True)
    (project / "Source" / "Thing.cpp").write_text("before\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_runner(argv, *, cwd, input, **_kwargs):
        calls.append(list(argv))
        if "--check" not in argv:
            (Path(cwd) / "Source" / "Thing.cpp").write_text(
                "applied\n",
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    result = materialize_patch_candidate_sandboxes(
        project_root=project,
        candidates=[
            {"id": "a", "patch": PATCH_A, "runtimeCompatible": True},
            {"id": "b", "patch": PATCH_B, "runtimeCompatible": True},
        ],
        sandbox_root=tmp_path / "sandboxes",
        runner=fake_runner,
    )

    assert result["ok"] is True
    assert len(calls) == 4
    assert result["candidates"][0]["changedFiles"] == ["Source/Thing.cpp"]
    assert Path(
        result["candidates"][0]["sandboxEvidence"]["isolatedRoot"]
    ).is_dir()


def test_rejects_sandbox_root_inside_project(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    project.mkdir()
    result = materialize_patch_candidate_sandboxes(
        project_root=project,
        candidates=[{"patch": PATCH_A}, {"patch": PATCH_B}],
        sandbox_root=project / "sandboxes",
    )
    assert result["ok"] is False
