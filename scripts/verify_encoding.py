#!/usr/bin/env python3
"""Scan repository text trees for UTF-8 BOM and invalid UTF-8 bytes."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def scan_tree(root: Path, *, suffixes: tuple[str, ...]) -> list[str]:
    issues: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in suffixes:
            continue
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            issues.append(f"BOM: {path}")
            continue
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            issues.append(f"INVALID_UTF8: {path}: {exc}")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify UTF-8 encoding hygiene.")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    roots = [
        (args.repo_root / "scripts", (".py", ".ps1", ".md")),
        (args.repo_root / "tests", (".py",)),
        (args.repo_root / "docs", (".md",)),
    ]
    issues: list[str] = []
    for root, suffixes in roots:
        if root.is_dir():
            issues.extend(scan_tree(root, suffixes=suffixes))
    if issues:
        print("\n".join(issues), file=sys.stderr)
        return 1
    print("encoding scan ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
