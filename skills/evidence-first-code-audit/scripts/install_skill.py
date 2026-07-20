#!/usr/bin/env python3
"""Install this skill into a Codex skill directory on any supported platform."""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
import uuid
from pathlib import Path


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


def _default_destination_root() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    return (Path(codex_home) if codex_home else Path.home() / ".codex") / "skills"


def install(destination_root: Path, *, force: bool = False, dry_run: bool = False) -> Path:
    source = Path(__file__).resolve().parents[1]
    skill_name = source.name
    root = destination_root.expanduser().resolve()
    destination = (root / skill_name).resolve()

    if _same_path(destination, source) or source in destination.parents:
        raise ValueError(f"destination must not equal or be nested under the source: {destination}")
    if destination.exists() and not force:
        raise FileExistsError(
            f"destination already exists: {destination}; use --force to replace it"
        )

    print(f"Skill source: {source}")
    print(f"Skill destination: {destination}")
    if dry_run:
        print("Dry run: no files changed.")
        return destination

    root.mkdir(parents=True, exist_ok=True)
    staging_parent = Path(tempfile.mkdtemp(prefix=f".{skill_name}-staging-", dir=root))
    staging = staging_parent / skill_name
    backup = root / f".{skill_name}-backup-{uuid.uuid4().hex}"
    moved_existing = False
    try:
        shutil.copytree(
            source,
            staging,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
        )
        if destination.exists():
            destination.replace(backup)
            moved_existing = True
        staging.replace(destination)
    except Exception:
        if moved_existing and backup.exists() and not destination.exists():
            backup.replace(destination)
        raise
    finally:
        if staging_parent.exists():
            shutil.rmtree(staging_parent)

    if backup.exists():
        if backup.is_dir():
            shutil.rmtree(backup)
        else:
            backup.unlink()
    print("Installed.")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination-root",
        type=Path,
        default=_default_destination_root(),
        help="Parent directory that will contain evidence-first-code-audit.",
    )
    parser.add_argument("--force", action="store_true", help="Replace an existing installation.")
    parser.add_argument("--dry-run", action="store_true", help="Show the destination only.")
    args = parser.parse_args()
    try:
        install(args.destination_root, force=args.force, dry_run=args.dry_run)
    except (FileExistsError, OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
