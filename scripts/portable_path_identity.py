#!/usr/bin/env python
"""Host-aware path normalization without lossy Unicode folding."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def is_windows_host_platform(host_platform: str | None = None) -> bool:
    """Return whether *host_platform* uses Windows path matching rules."""

    host = sys.platform if host_platform is None else str(host_platform)
    return host.strip().lower() in {"win32", "windows", "nt"}


def ascii_windows_fold(value: str) -> str:
    """Fold only ASCII A-Z, avoiding Unicode lower/casefold collisions."""

    return str(value).translate(str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"))


def normalize_portable_path(
    value: object,
    *,
    trim_outer_slashes: bool = False,
    strip_project_uri: bool = True,
) -> str:
    """Normalize separators without changing Unicode spelling or case."""

    normalized = str(value or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if strip_project_uri and normalized.lower().startswith("project://"):
        normalized = normalized[len("project://") :]
    normalized = re.sub(r"/{2,}", "/", normalized)
    if trim_outer_slashes:
        normalized = normalized.strip("/")
    elif len(normalized) > 1:
        normalized = normalized.rstrip("/")
    return normalized


def filesystem_path_identity(
    value: object,
    host_platform: str | None = None,
    *,
    trim_outer_slashes: bool = False,
    strip_project_uri: bool = True,
) -> str:
    """Return a portable identity with ASCII case folding only on Windows."""

    normalized = normalize_portable_path(
        value,
        trim_outer_slashes=trim_outer_slashes,
        strip_project_uri=strip_project_uri,
    )
    return ascii_windows_fold(normalized) if is_windows_host_platform(host_platform) else normalized


def resolve_canonical_absolute_path(
    value: object,
    *,
    base_path: Path | str | None = None,
    realpath: bool = True,
) -> str:
    """Resolve an absolute path, using filesystem spelling when it exists."""

    raw = "" if value is None else str(value)
    if not raw:
        return ""
    base = os.getcwd() if base_path is None else str(base_path)
    resolved = os.path.abspath(raw if os.path.isabs(raw) else os.path.join(base, raw))
    if realpath:
        try:
            if os.path.exists(resolved):
                resolved = os.path.realpath(resolved)
        except OSError:
            # Retain lexical identity for inaccessible or concurrently removed paths.
            pass
    return str(resolved)


def canonical_absolute_path_identity(
    value: object,
    host_platform: str | None = None,
    *,
    base_path: Path | str | None = None,
    realpath: bool = True,
) -> str:
    """Return a host-aware absolute path identity without Unicode folding."""

    resolved = resolve_canonical_absolute_path(value, base_path=base_path, realpath=realpath)
    if not resolved or not is_windows_host_platform(host_platform):
        return resolved
    return ascii_windows_fold(resolved.replace("\\", "/"))
