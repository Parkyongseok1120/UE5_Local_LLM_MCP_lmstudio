from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.ci import run_ci_suite


ROOT = Path(__file__).resolve().parents[1]


def test_all_declared_suites_validate_as_one_duplicate_free_selection() -> None:
    resolved = run_ci_suite.resolve_test_files(run_ci_suite.PRIMARY_SUITE_NAMES)

    assert len(resolved) == 51
    assert len({path.casefold() for path in resolved}) == 51


def test_missing_test_file_fails_before_pytest(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(run_ci_suite, "SUITES", {"broken": ("tests/missing.py",)})

    try:
        run_ci_suite.resolve_test_files(("broken",), repository_root=tmp_path)
    except run_ci_suite.SuiteValidationError as exc:
        assert "missing test file" in str(exc)
    else:
        raise AssertionError("missing test file did not fail suite validation")


def test_duplicate_suite_member_fails_before_pytest(monkeypatch, tmp_path: Path) -> None:
    test_file = tmp_path / "tests" / "test_example.py"
    test_file.parent.mkdir()
    test_file.write_text("def test_example(): pass\n", encoding="utf-8")
    monkeypatch.setattr(
        run_ci_suite,
        "SUITES",
        {"broken": ("tests/test_example.py", "tests/test_example.py")},
    )

    try:
        run_ci_suite.resolve_test_files(("broken",), repository_root=tmp_path)
    except run_ci_suite.SuiteValidationError as exc:
        assert "duplicate path" in str(exc)
    else:
        raise AssertionError("duplicate suite member did not fail suite validation")


def test_legacy_eval_member_is_rejected(monkeypatch, tmp_path: Path) -> None:
    legacy_file = tmp_path / "legacy_eval" / "tests" / "test_old.py"
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_text("def test_old(): pass\n", encoding="utf-8")
    monkeypatch.setattr(
        run_ci_suite,
        "SUITES",
        {"broken": ("legacy_eval/tests/test_old.py",)},
    )

    try:
        run_ci_suite.resolve_test_files(("broken",), repository_root=tmp_path)
    except run_ci_suite.SuiteValidationError as exc:
        assert "quarantined legacy_eval" in str(exc)
    else:
        raise AssertionError("legacy_eval member did not fail Direct suite validation")


def test_windows_and_case_variant_legacy_eval_members_are_rejected(
    monkeypatch,
    tmp_path: Path,
) -> None:
    for member in (
        r"legacy_eval\tests\test_old.py",
        r"LEGACY_EVAL\tests\test_old.py",
    ):
        monkeypatch.setattr(run_ci_suite, "SUITES", {"broken": (member,)})
        try:
            run_ci_suite.resolve_test_files(("broken",), repository_root=tmp_path)
        except run_ci_suite.SuiteValidationError as exc:
            assert "quarantined legacy_eval" in str(exc)
        else:
            raise AssertionError(f"legacy_eval path was accepted: {member}")


def test_separator_aliases_cannot_evade_duplicate_detection(
    monkeypatch,
    tmp_path: Path,
) -> None:
    test_file = tmp_path / "tests" / "test_example.py"
    test_file.parent.mkdir()
    test_file.write_text("def test_example(): pass\n", encoding="utf-8")
    monkeypatch.setattr(
        run_ci_suite,
        "SUITES",
        {"broken": ("tests/test_example.py", r"tests\test_example.py")},
    )

    try:
        run_ci_suite.resolve_test_files(("broken",), repository_root=tmp_path)
    except run_ci_suite.SuiteValidationError as exc:
        assert "duplicate path" in str(exc)
    else:
        raise AssertionError("separator alias evaded duplicate detection")


def test_drive_unc_and_traversal_members_are_rejected(monkeypatch, tmp_path: Path) -> None:
    for member in (
        r"C:\tests\test_old.py",
        r"\\server\share\tests\test_old.py",
        r"tests\..\outside\test_old.py",
    ):
        monkeypatch.setattr(run_ci_suite, "SUITES", {"broken": (member,)})
        try:
            run_ci_suite.resolve_test_files(("broken",), repository_root=tmp_path)
        except run_ci_suite.SuiteValidationError as exc:
            assert "repository-relative" in str(exc)
        else:
            raise AssertionError(f"unsafe path was accepted: {member}")


def test_pytest_exit_code_is_propagated(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def fake_run(command, *, cwd, check):
        observed.update(command=command, cwd=cwd, check=check)
        return subprocess.CompletedProcess(command, 37)

    monkeypatch.setattr(run_ci_suite.subprocess, "run", fake_run)

    assert run_ci_suite.main(["windows_release"]) == 37
    assert observed["cwd"] == ROOT
    assert observed["check"] is False
    command = observed["command"]
    assert command[:3] == [run_ci_suite.sys.executable, "-m", "pytest"]
    assert "tests/test_ci_release_readiness.py" in command
    assert command[-2:] == ["-q", "--tb=short"]


def test_validate_only_never_spawns_pytest(monkeypatch) -> None:
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("validate-only unexpectedly invoked pytest")

    monkeypatch.setattr(run_ci_suite.subprocess, "run", fail_if_called)

    assert run_ci_suite.main(["portable_direct", "--validate-only"]) == 0
