#!/usr/bin/env python
"""Collect local Unreal Engine C++ source files into JSONL."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


DEFAULT_EXTENSIONS = {
    ".h",
    ".hpp",
    ".hh",
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".inl",
    ".cs",
    ".ush",
    ".usf",
}
SKIP_DIRS = {
    ".git",
    ".vs",
    "Binaries",
    "Build",
    "DerivedDataCache",
    "Intermediate",
    "Saved",
}
OPTIONAL_SKIP_DIRS = {"ThirdParty", "Editor"}


def stable_id(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def read_text(path: Path) -> str | None:
    for encoding in ("utf-8-sig", "utf-8", "cp949", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            print(f"[skip] {path} ({exc})")
            return None
    return None


def should_skip(path: Path, skip_dirs: set[str]) -> bool:
    return any(part in skip_dirs for part in path.parts)


def collect(args: argparse.Namespace) -> None:
    root = Path(args.root).expanduser().resolve()
    if not root.exists():
        raise SystemExit(f"source root does not exist: {root}")

    extensions = {ext if ext.startswith(".") else f".{ext}" for ext in args.extensions}
    skip_dirs = set(SKIP_DIRS)
    if args.exclude_editor:
        skip_dirs.update(OPTIONAL_SKIP_DIRS)
    elif not args.include_third_party:
        skip_dirs.add("ThirdParty")
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    with out_path.open("w", encoding="utf-8") as handle:
        for path in root.rglob("*"):
            if not path.is_file() or should_skip(path, skip_dirs):
                continue
            if path.suffix not in extensions:
                continue
            if path.stat().st_size > args.max_bytes:
                continue

            text = read_text(path)
            if not text or len(text.strip()) < args.min_chars:
                continue

            relative = path.relative_to(root).as_posix()
            item = {
                "id": stable_id(str(path)),
                "source": "unreal_source",
                "path": str(path),
                "title": relative,
                "text": text,
                "metadata": {
                    "root": str(root),
                    "relative_path": relative,
                    "extension": path.suffix,
                },
            }
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
            written += 1
            if written % 250 == 0:
                print(f"[{written}] {relative}")

    print(f"done: wrote {written} files to {out_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect local Unreal source files as JSONL. "
            "By default skips Editor/ and ThirdParty/ directories under the source root."
        ),
    )
    parser.add_argument("--root", required=True)
    parser.add_argument("--out", default="data/unreal58/raw_source.jsonl")
    parser.add_argument("--extensions", nargs="+", default=sorted(DEFAULT_EXTENSIONS))
    parser.add_argument("--min-chars", type=int, default=100)
    parser.add_argument("--max-bytes", type=int, default=1_000_000)
    parser.add_argument(
        "--exclude-editor",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Skip Editor/ and ThirdParty/ directories (default: true). "
            "Use --no-exclude-editor to include Editor trees; ThirdParty still requires --include-third-party."
        ),
    )
    parser.add_argument(
        "--include-third-party",
        action="store_true",
        help="Include ThirdParty/ when --no-exclude-editor is set.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    collect(parse_args())
