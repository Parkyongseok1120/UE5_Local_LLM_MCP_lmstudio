#!/usr/bin/env python3
"""Scan tracked repository text files for UTF-8 BOM and invalid UTF-8 bytes."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

TEXT_SUFFIXES = (
    ".js",
    ".json",
    ".md",
    ".py",
    ".ps1",
    ".sh",
    ".txt",
    ".yml",
    ".yaml",
)


def _tracked_text_files(repo_root: Path) -> list[Path]:
    """Prefer git ls-files so only tracked sources are gated."""
    try:
        raw = subprocess.check_output(
            ["git", "-C", str(repo_root), "ls-files", "-z"],
            text=False,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    selected: list[Path] = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        try:
            relative = Path(item.decode("utf-8"))
        except UnicodeDecodeError:
            continue
        if relative.suffix.lower() not in TEXT_SUFFIXES:
            continue
        path = repo_root / relative
        if path.is_file():
            selected.append(path)
    return selected


def scan_paths(paths: list[Path]) -> list[str]:
    issues: list[str] = []
    for path in sorted(paths, key=lambda item: item.as_posix().lower()):
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            issues.append(f"BOM: {path}")
            continue
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            issues.append(f"INVALID_UTF8: {path}: {exc}")
    return issues


def scan_tree(root: Path, *, suffixes: tuple[str, ...]) -> list[str]:
    """Legacy directory walk used when git is unavailable."""
    paths = [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix.lower() in suffixes
    ]
    return scan_paths(paths)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify UTF-8 encoding hygiene.")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    tracked = _tracked_text_files(args.repo_root)
    if tracked:
        issues = scan_paths(tracked)
    else:
        # Fallback mirrors the historical scripts/tests/docs scope when git is missing.
        issues = []
        for root, suffixes in (
            (args.repo_root / "scripts", TEXT_SUFFIXES),
            (args.repo_root / "tests", TEXT_SUFFIXES),
            (args.repo_root / "docs", TEXT_SUFFIXES),
            (args.repo_root / "lmstudio-unreal-agent-mcp", TEXT_SUFFIXES),
            (args.repo_root / "RAG_Project_Guidelines", TEXT_SUFFIXES),
            (args.repo_root / "installer", TEXT_SUFFIXES),
            (args.repo_root / "config", TEXT_SUFFIXES),
            (args.repo_root / "skills", TEXT_SUFFIXES),
        ):
            if root.is_dir():
                issues.extend(scan_tree(root, suffixes=suffixes))
        # Also cover root-level launchers/docs when present.
        for name in (
            "install.py",
            "install.sh",
            "INSTALL.bat",
            "README.md",
            "README.ko.md",
            "CONTRIBUTING.md",
            "SECURITY.md",
            "EPIC_NOTICE.md",
            "requirements.txt",
            ".github/workflows/ci.yml",
        ):
            path = args.repo_root / name
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
                issues.extend(scan_paths([path]))
    if issues:
        print("\n".join(issues), file=sys.stderr)
        return 1
    print(f"encoding scan ok ({len(tracked) if tracked else 'fallback'} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
