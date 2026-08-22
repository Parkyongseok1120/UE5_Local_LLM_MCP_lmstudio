"""Validate named pytest suites and propagate pytest's process exit code."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath

try:
    from .ci_suites import PRIMARY_SUITE_NAMES, QUARANTINED_TEST_ROOTS, SUITES
except ImportError:  # Direct execution: python scripts/ci/run_ci_suite.py ...
    from ci_suites import PRIMARY_SUITE_NAMES, QUARANTINED_TEST_ROOTS, SUITES


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class SuiteValidationError(ValueError):
    """A named suite is missing, ambiguous, stale, or outside the Direct tree."""


def resolve_test_files(
    suite_names: Sequence[str],
    *,
    repository_root: Path = REPOSITORY_ROOT,
) -> tuple[str, ...]:
    if not suite_names:
        raise SuiteValidationError("at least one CI suite is required")

    duplicate_names = sorted({name for name in suite_names if suite_names.count(name) > 1})
    if duplicate_names:
        raise SuiteValidationError(f"duplicate suite selection: {', '.join(duplicate_names)}")

    unknown = sorted(set(suite_names) - set(SUITES))
    if unknown:
        raise SuiteValidationError(
            f"unknown CI suite(s): {', '.join(unknown)}; available: {', '.join(sorted(SUITES))}"
        )

    repository_root = repository_root.resolve()
    quarantined_roots = tuple(
        tuple(part.casefold() for part in PurePosixPath(root.replace("\\", "/")).parts)
        for root in QUARANTINED_TEST_ROOTS
    )
    selected: list[str] = []
    seen_paths: set[str] = set()
    for suite_name in suite_names:
        members = SUITES[suite_name]
        if not members:
            raise SuiteValidationError(f"CI suite is empty: {suite_name}")
        suite_seen: set[str] = set()
        for raw_path in members:
            raw_text = str(raw_path)
            windows_path = PureWindowsPath(raw_text)
            path = PurePosixPath(raw_text.replace("\\", "/"))
            normalized = path.as_posix().casefold()
            if path.is_absolute() or windows_path.drive or ".." in path.parts:
                raise SuiteValidationError(f"CI suite path must be repository-relative: {raw_path}")
            if path.suffix != ".py":
                raise SuiteValidationError(f"CI suite member must be a Python test file: {raw_path}")
            folded_parts = tuple(part.casefold() for part in path.parts)
            if any(folded_parts[: len(root)] == root for root in quarantined_roots):
                raise SuiteValidationError(f"Direct CI suite references quarantined legacy_eval: {raw_path}")
            if normalized in suite_seen:
                raise SuiteValidationError(f"duplicate path in CI suite {suite_name}: {raw_path}")
            if normalized in seen_paths:
                raise SuiteValidationError(f"duplicate path across selected CI suites: {raw_path}")
            candidate = repository_root.joinpath(*path.parts).resolve()
            if not candidate.is_relative_to(repository_root):
                raise SuiteValidationError(f"CI suite path escapes the repository: {raw_path}")
            if not candidate.is_file():
                raise SuiteValidationError(f"CI suite references a missing test file: {raw_path}")
            suite_seen.add(normalized)
            seen_paths.add(normalized)
            selected.append(path.as_posix())
    return tuple(selected)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one or more validated named pytest suites from scripts/ci/ci_suites.py."
    )
    parser.add_argument("suites", nargs="+", help="Named suites to run")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate membership and test-file existence without invoking pytest",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        test_files = resolve_test_files(args.suites)
    except SuiteValidationError as exc:
        print(f"CI suite validation failed: {exc}", file=sys.stderr)
        return 2
    if args.validate_only:
        print(f"validated {len(test_files)} test files across {len(args.suites)} suite(s)")
        return 0

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", *test_files, "-q", "--tb=short"],
        cwd=REPOSITORY_ROOT,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
