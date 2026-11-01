#!/usr/bin/env python
"""Collect project guideline Markdown files into JSONL for the RAG index."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

from workspace_paths import resolve_index_dir
from architecture_pattern_catalog import pattern_rows

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


def title_from_markdown(path: Path, text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or path.stem
    return path.stem


def collect(args: argparse.Namespace) -> None:
    root = Path(args.root).expanduser().resolve()
    export_arg = str(getattr(args, "pattern_export", "") or "")
    export = Path(export_arg).expanduser().resolve() if export_arg else None
    patterns = pattern_rows(export) if export else []
    if not root.exists() and export is None:
        raise SystemExit(f"guidelines root does not exist: {root}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    temporary = None
    try:
      with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=out_path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        for path in sorted(root.rglob("*.md")):
            if export and path.resolve() == export.with_name("unreal-patterns.md"):
                continue
            if not path.is_file() or path.stat().st_size > args.max_bytes:
                continue

            text = read_text(path)
            if not text or len(text.strip()) < args.min_chars:
                continue

            relative = path.relative_to(root).as_posix()
            item = {
                "id": stable_id(f"{root}:{relative}"),
                "source": "project_guideline",
                "path": str(path),
                "title": title_from_markdown(path, text),
                "text": text,
                "metadata": {
                    "root": str(root),
                    "relative_path": relative,
                    "extension": path.suffix,
                    "guideline": True,
                },
            }
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
            written += 1
        for item in patterns:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
            written += 1
      os.replace(temporary, out_path)
      temporary = None
    finally:
      if temporary is not None:
        temporary.unlink(missing_ok=True)

    print(f"done: wrote {written} guideline files to {out_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect project guideline Markdown files as JSONL.")
    parser.add_argument("--root", default="RAG_Project_Guidelines")
    parser.add_argument("--pattern-export", default="", help="Optional generated architecture pattern catalog, also supports export-only collection.")
    parser.add_argument("--out", default="", help="Output JSONL (default: configured RAG data directory).")
    parser.add_argument("--min-chars", type=int, default=20)
    parser.add_argument("--max-bytes", type=int, default=250_000)
    args = parser.parse_args()
    if not args.out:
        args.out = str(resolve_index_dir() / "raw_guidelines.jsonl")
    return args


if __name__ == "__main__":
    collect(parse_args())
