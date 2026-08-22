#!/usr/bin/env python
"""Collect local Unreal project text files and asset-path metadata into JSONL."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from workspace_paths import canonical_absolute_path_identity, resolve_index_dir


TEXT_EXTENSIONS = {
    ".archive",
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".csv",
    ".cxx",
    ".h",
    ".hh",
    ".hpp",
    ".ini",
    ".inl",
    ".json",
    ".md",
    ".po",
    ".properties",
    ".target",
    ".txt",
    ".uplugin",
    ".uproject",
    ".uprojectdirs",
    ".usf",
    ".ush",
    ".xml",
}
ASSET_EXTENSIONS = {".uasset", ".umap"}
SKIP_DIRS = {
    ".agent",
    ".cline",
    ".codex",
    ".continue",
    ".git",
    ".github",
    ".idea",
    ".openai",
    ".vs",
    "Binaries",
    "Build",
    "coverage",
    "DerivedDataCache",
    "dist",
    "golden",
    "Intermediate",
    "node_modules",
    "Saved",
    "text_snapshot",
    "unreal_projects",
}
_SKIP_DIRS_FOLDED = {name.casefold() for name in SKIP_DIRS}


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


def has_skip_part(path: Path) -> bool:
    return any(
        part.startswith(".")
        or part.casefold() in _SKIP_DIRS_FOLDED
        or (part.lower().startswith("unreal") and part[6:].isdigit())
        for part in path.parts
    )


def find_projects(root: Path) -> list[Path]:
    if root.is_file() and root.suffix == ".uproject":
        return [root]

    direct = sorted(root.glob("*.uproject"))
    if direct:
        return direct

    return sorted(path for path in root.rglob("*.uproject") if not has_skip_part(path))


def _unique_project_files(
    project_files: list[Path],
    *,
    host_platform: str | None = None,
) -> list[Path]:
    """Deduplicate project files using the current host's path semantics."""

    seen: set[str] = set()
    unique_projects: list[Path] = []
    for project_file in project_files:
        resolved = project_file.resolve()
        key = canonical_absolute_path_identity(
            resolved,
            host_platform=host_platform,
        )
        if key in seen:
            continue
        seen.add(key)
        unique_projects.append(resolved)
    return unique_projects


def safe_snapshot_path(copy_root: Path, project_name: str, relative: Path) -> Path:
    return copy_root / project_name / relative


def write_text_snapshot(copy_root: Path | None, project_name: str, relative: Path, source_path: Path) -> None:
    if copy_root is None:
        return
    target = safe_snapshot_path(copy_root, project_name, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target)


def collect_project(
    project_file: Path,
    handle,
    args: argparse.Namespace,
    copy_root: Path | None,
) -> tuple[int, int]:
    project_root = project_file.parent.resolve()
    descriptor = project_file.resolve()
    project_name = project_file.stem
    text_count = 0
    asset_count = 0

    for path in project_root.rglob("*"):
        if not path.is_file() or has_skip_part(path):
            continue

        suffix = path.suffix.lower()
        relative_path = path.relative_to(project_root)
        relative = relative_path.as_posix()

        if suffix in TEXT_EXTENSIONS:
            if path.stat().st_size > args.max_text_bytes:
                continue

            text = read_text(path)
            if not text or len(text.strip()) < args.min_chars:
                continue

            write_text_snapshot(copy_root, project_name, relative_path, path)
            item = {
                "id": stable_id(f"{descriptor}:{relative}"),
                "source": "unreal_project_text",
                "path": str(path),
                "title": f"{project_name}/{relative}",
                "text": text,
                "metadata": {
                    "project": project_name,
                    "project_root": str(project_root),
                    "project_file": str(descriptor),
                    "relative_path": relative,
                    "extension": suffix,
                },
            }
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
            text_count += 1
            if text_count % 250 == 0:
                print(f"[{project_name}] text {text_count}: {relative}")

        elif suffix in ASSET_EXTENSIONS and not args.skip_asset_paths:
            text = "\n".join(
                [
                    f"Unreal project asset path: {relative}",
                    f"Project: {project_name}",
                    f"Asset extension: {suffix}",
                    "Binary Unreal assets are indexed by path only. Open Unreal Editor to inspect Blueprint graphs, defaults, and asset references.",
                ]
            )
            item = {
                "id": stable_id(f"{descriptor}:{relative}"),
                "source": "unreal_project_asset_path",
                "path": str(path),
                "title": f"{project_name}/{relative}",
                "text": text,
                "metadata": {
                    "project": project_name,
                    "project_root": str(project_root),
                    "project_file": str(descriptor),
                    "relative_path": relative,
                    "extension": suffix,
                    "path_only": True,
                },
            }
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
            asset_count += 1
            if asset_count % 1000 == 0:
                print(f"[{project_name}] asset paths {asset_count}: {relative}")

    return text_count, asset_count


def collect(args: argparse.Namespace) -> None:
    roots = [Path(value).expanduser().resolve() for value in args.root]
    project_files: list[Path] = []
    for root in roots:
        if not root.exists():
            print(f"[skip] project root does not exist: {root}")
            continue
        found = find_projects(root)
        if not found:
            print(f"[warn] no .uproject files found under: {root}")
            continue
        project_files.extend(found)

    if not project_files:
        raise SystemExit("no .uproject files found under any --root")

    unique_projects = _unique_project_files(project_files)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    copy_root = Path(args.copy_text_to).resolve() if args.copy_text_to else None
    if copy_root:
        copy_root.mkdir(parents=True, exist_ok=True)

    total_text = 0
    total_assets = 0
    with out_path.open("w", encoding="utf-8") as handle:
        for project_file in unique_projects:
            text_count, asset_count = collect_project(project_file, handle, args, copy_root)
            total_text += text_count
            total_assets += asset_count
            print(f"[done] {project_file.stem}: {text_count} text files, {asset_count} asset paths")

    print(f"done: wrote {total_text} text files and {total_assets} asset paths to {out_path}")
    if copy_root:
        print(f"text snapshot: {copy_root}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Unreal project text and asset-path metadata as JSONL.")
    parser.add_argument("--root", action="append", required=True, help="Search root; repeat for multiple roots.")
    parser.add_argument("--out", default="", help="Output JSONL (default: configured RAG data directory).")
    parser.add_argument("--copy-text-to")
    parser.add_argument("--min-chars", type=int, default=2)
    parser.add_argument("--max-text-bytes", type=int, default=1_000_000)
    parser.add_argument("--skip-asset-paths", action="store_true")
    args = parser.parse_args()
    if not args.out:
        args.out = str(resolve_index_dir() / "raw_projects.jsonl")
    return args


if __name__ == "__main__":
    collect(parse_args())
