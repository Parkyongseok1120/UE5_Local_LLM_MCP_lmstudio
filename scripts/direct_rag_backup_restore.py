#!/usr/bin/env python
"""Restore a refresh backup without consuming its only recoverable copy."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from direct_rag_atomic_replace import atomic_replace


def restore_backup_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_file() and not source.is_symlink():
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.direct-restore-",
            dir=str(destination.parent),
        )
        os.close(handle)
        temporary = Path(temporary_name)
        try:
            shutil.copy2(source, temporary)
            atomic_replace(temporary, destination, replace=os.replace)
        finally:
            temporary.unlink(missing_ok=True)
        return

    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.direct-restore-",
            dir=str(destination.parent),
        )
    )
    shutil.rmtree(temporary)
    try:
        shutil.copytree(source, temporary)
        if destination.exists() or destination.is_symlink():
            if destination.is_dir() and not destination.is_symlink():
                shutil.rmtree(destination)
            else:
                destination.unlink(missing_ok=True)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


__all__ = ["restore_backup_copy"]
