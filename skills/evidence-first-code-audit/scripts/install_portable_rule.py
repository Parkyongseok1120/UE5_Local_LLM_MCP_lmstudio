#!/usr/bin/env python3
"""Export the tool-neutral evidence-first rule to an explicit agent-rule path."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


def install(output_path: Path, *, force: bool = False, dry_run: bool = False) -> Path:
    source = Path(__file__).resolve().parents[1] / "references" / "portable-rule.md"
    destination = output_path.expanduser().resolve()
    if _same_path(destination, source):
        raise ValueError(f"output must not overwrite the portable rule source: {destination}")
    if destination.exists() and not force:
        raise FileExistsError(
            f"output already exists: {destination}; use --force to replace it"
        )

    print(f"Rule source: {source}")
    print(f"Rule destination: {destination}")
    if dry_run:
        print("Dry run: no files changed.")
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(source.read_text(encoding="utf-8"))
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    print("Installed.")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_path", type=Path, help="Explicit output file for the agent rule.")
    parser.add_argument("--force", action="store_true", help="Replace an existing output file.")
    parser.add_argument("--dry-run", action="store_true", help="Show the destination only.")
    args = parser.parse_args()
    try:
        install(args.output_path, force=args.force, dry_run=args.dry_run)
    except (FileExistsError, OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
