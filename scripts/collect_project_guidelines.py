#!/usr/bin/env python
"""Collect project guideline Markdown files into JSONL for the RAG index."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


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
    if not root.exists():
        raise SystemExit(f"guidelines root does not exist: {root}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    with out_path.open("w", encoding="utf-8") as handle:
        for path in sorted(root.rglob("*.md")):
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

    print(f"done: wrote {written} guideline files to {out_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect project guideline Markdown files as JSONL.")
    parser.add_argument("--root", default="RAG_Project_Guidelines")
    parser.add_argument("--out", default="data/unreal58/raw_guidelines.jsonl")
    parser.add_argument("--min-chars", type=int, default=20)
    parser.add_argument("--max-bytes", type=int, default=250_000)
    return parser.parse_args()


if __name__ == "__main__":
    collect(parse_args())
