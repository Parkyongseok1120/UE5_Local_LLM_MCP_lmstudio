#!/usr/bin/env python
"""Collect per-project game design documents into JSONL for the RAG index."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


TEXT_EXTENSIONS = {".md", ".txt", ".json", ".yaml", ".yml"}
SKIP_DIR_PREFIXES = (".", "_")
FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


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


def title_from_text(path: Path, text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or path.stem
    return path.stem.replace("_", " ")


def parse_front_matter(text: str) -> dict[str, str]:
    match = FRONT_MATTER_RE.match(text)
    if not match:
        return {}

    metadata: dict[str, str] = {}
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower().replace("-", "_")
        value = value.strip().strip("\"'")
        if key and value:
            metadata[key] = value
    return metadata


def strip_front_matter(text: str) -> str:
    return FRONT_MATTER_RE.sub("", text, count=1).lstrip()


def infer_design_area(relative: Path, title: str) -> str:
    value = f"{relative.as_posix()} {title}".lower()
    markers = {
        "pillars": ("pillar", "pillars", "vision", "fantasy", "정체성", "목표"),
        "core_loop": ("core_loop", "core loop", "loop", "루프"),
        "combat": ("combat", "battle", "damage", "전투", "공격", "피격"),
        "progression": ("progression", "growth", "reward", "성장", "보상"),
        "enemies": ("enemy", "enemies", "ai", "적", "몬스터"),
        "level_pacing": ("level", "pacing", "map", "레벨", "맵", "동선"),
        "ui_feedback": ("ui", "feedback", "hud", "피드백", "연출"),
        "economy": ("economy", "resource", "shop", "경제", "자원", "상점"),
        "narrative": ("narrative", "story", "quest", "서사", "스토리", "퀘스트"),
    }
    for area, needles in markers.items():
        if any(needle in value for needle in needles):
            return area
    return "general"


def should_skip(path: Path, root: Path, include_templates: bool) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    if include_templates:
        return False
    return any(part.startswith(SKIP_DIR_PREFIXES) for part in relative.parts)


def project_name_from_path(root: Path, path: Path, explicit_project: str | None) -> str:
    if explicit_project:
        return explicit_project
    relative = path.relative_to(root)
    if len(relative.parts) <= 1:
        return "General"
    return relative.parts[0]


def collect(args: argparse.Namespace) -> None:
    root = Path(args.root).expanduser().resolve()
    if not root.exists():
        raise SystemExit(f"game design root does not exist: {root}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    with out_path.open("w", encoding="utf-8") as handle:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in TEXT_EXTENSIONS:
                continue
            if should_skip(path, root, args.include_templates):
                continue
            if path.stat().st_size > args.max_bytes:
                continue

            text = read_text(path)
            if not text or len(text.strip()) < args.min_chars:
                continue

            front_matter = parse_front_matter(text)
            project = project_name_from_path(root, path, args.project)
            relative = path.relative_to(root).as_posix()
            title = front_matter.get("title") or title_from_text(path, text)
            design_area = front_matter.get("design_area") or infer_design_area(Path(relative), title)
            genre = front_matter.get("genre") or ""
            body_text = strip_front_matter(text)
            summary_prefix = "\n".join(
                [
                    f"Game Design Project: {project}",
                    f"Design Area: {design_area}",
                    f"Genre: {genre}" if genre else "Genre: unspecified",
                    "",
                ]
            )

            item = {
                "id": stable_id(f"{root}:{relative}"),
                "source": "game_design_doc",
                "path": str(path),
                "title": f"{project}/{title}",
                "text": summary_prefix + body_text,
                "metadata": {
                    "root": str(root),
                    "project": project,
                    "relative_path": relative,
                    "extension": path.suffix.lower(),
                    "game_design": True,
                    "design_area": design_area,
                    "genre": genre,
                },
            }
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
            written += 1

    print(f"done: wrote {written} game design docs to {out_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect game design docs as JSONL.")
    parser.add_argument("--root", default="Game_Design_Docs")
    parser.add_argument("--out", default="data/unreal58/raw_game_design.jsonl")
    parser.add_argument("--project")
    parser.add_argument("--min-chars", type=int, default=20)
    parser.add_argument("--max-bytes", type=int, default=250_000)
    parser.add_argument("--include-templates", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    collect(parse_args())
