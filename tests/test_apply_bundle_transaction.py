from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from autofix_runtime import AutofixResult, AutofixStep, run_autofix_pipeline  # noqa: E402
from lmstudio_unreal_wrapper import apply_bundle, read_text  # noqa: E402
from unreal_static_validate import Finding  # noqa: E402


def test_apply_bundle_rolls_back_on_patch_failure(tmp_path: Path) -> None:
    source = tmp_path / "Source" / "Game"
    source.mkdir(parents=True)
    target = source / "Holdout.cpp"
    target.write_text("void Foo() {}\n", encoding="utf-8")
    before = {"Source/Game/Holdout.cpp": "void Foo() {}\n"}
    bundle = {
        "files": [],
        "patches": [
            {
                "path": "Source/Game/Holdout.cpp",
                "oldText": "void Foo() {}\n",
                "newText": "void Foo() { return; }\n",
                "expectedOccurrences": 1,
            },
            {
                "path": "Source/Game/Holdout.cpp",
                "oldText": "missing anchor",
                "newText": "void Bar() {}\n",
                "expectedOccurrences": 1,
            },
        ],
    }
    try:
        apply_bundle(tmp_path, bundle, before_apply=before)
        assert False, "expected patch failure"
    except ValueError:
        pass
    assert read_text(target) == "void Foo() {}\n"


def test_stage_bundle_apply_commits_only_after_success(tmp_path: Path) -> None:
    source = tmp_path / "Source" / "Game"
    source.mkdir(parents=True)
    target = source / "Holdout.cpp"
    target.write_text("void Foo() {}\n", encoding="utf-8")
    before = {"Source/Game/Holdout.cpp": "void Foo() {}\n"}
    bundle = {
        "files": [],
        "patches": [
            {
                "path": "Source/Game/Holdout.cpp",
                "oldText": "void Foo() {}\n",
                "newText": "void Foo() { return; }\n",
                "expectedOccurrences": 1,
            }
        ],
    }
    written = apply_bundle(tmp_path, bundle, before_apply=before)
    assert written
    assert read_text(target) == "void Foo() { return; }\n"


def test_autofix_pipeline_rollback_restores_true_pre_step_content(tmp_path: Path) -> None:
    """Regression test for the no-op rollback bug: snapshotting *after* a step
    writes must never be mistaken for a real pre-step snapshot."""
    source = tmp_path / "Source" / "Game"
    source.mkdir(parents=True)
    target = source / "Holdout.cpp"
    original_content = "void Foo() {}\n"
    target.write_text(original_content, encoding="utf-8")

    def failing_apply(root: Path, findings: list[Finding]) -> list[Path]:
        cpp_path = root / "Source" / "Game" / "Holdout.cpp"
        cpp_path.write_text("void Foo() { BROKEN }\n", encoding="utf-8")
        return [cpp_path]

    dummy_finding = Finding("warning", "Source/Game/Holdout.cpp", 1, "SOME_CODE", "dummy")
    step = AutofixStep(
        "always_rejected",
        failing_apply,
        finding_codes=None,
        post_validate=lambda root, findings: False,
    )
    result: AutofixResult = run_autofix_pipeline(tmp_path, [dummy_finding], "compile_fix", [step])
    assert result.written == []
    assert read_text(target) == original_content


def test_autofix_pipeline_rollback_restores_content_on_exception(tmp_path: Path) -> None:
    source = tmp_path / "Source" / "Game"
    source.mkdir(parents=True)
    target = source / "Holdout.cpp"
    original_content = "void Foo() {}\n"
    target.write_text(original_content, encoding="utf-8")

    def raising_apply(root: Path, findings: list[Finding]) -> list[Path]:
        cpp_path = root / "Source" / "Game" / "Holdout.cpp"
        cpp_path.write_text("void Foo() { PARTIAL }\n", encoding="utf-8")
        raise RuntimeError("boom mid-write")

    dummy_finding = Finding("warning", "Source/Game/Holdout.cpp", 1, "SOME_CODE", "dummy")
    step = AutofixStep("boom", raising_apply, finding_codes=None)
    try:
        run_autofix_pipeline(tmp_path, [dummy_finding], "compile_fix", [step])
        assert False, "expected RuntimeError to propagate"
    except RuntimeError:
        pass
    assert read_text(target) == original_content
