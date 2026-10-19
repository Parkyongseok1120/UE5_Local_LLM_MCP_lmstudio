"""Single Python reader for engine identity (Unreal Build.version, Unity ProjectVersion.txt).

Every identity has the same shape::

    {status: observed|unknown, engine, version, major, minor, source, reason}

``source`` names how the version was learned:

* ``build_version_file``    strict ``Engine/Build/Build.version`` observation
* ``project_version_file``  Unity ``ProjectSettings/ProjectVersion.txt``
* ``folder_name_inferred``  guessed from a ``UE_5.x`` style folder name; this is
  never ``observed`` and callers must not treat it as verified.

Reading is read-only and bounded. Missing or malformed input yields
``status: "unknown"`` with a reason; no version is ever assumed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

BUILD_VERSION_RELATIVE = "Engine/Build/Build.version"
UNITY_VERSION_RELATIVE = "ProjectSettings/ProjectVersion.txt"
_MAX_IDENTITY_BYTES = 16384
_FOLDER_VERSION = re.compile(r"^UE[_-]?(\d+)[._](\d+)(?:[._](\d+))?$", re.IGNORECASE)
_UNITY_EDITOR_VERSION = re.compile(r"^\s*m_EditorVersion\s*:\s*(\d+)\.(\d+)\.(\d+)([A-Za-z]\d+)?\s*$", re.MULTILINE)


def _unknown(engine: str, source: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "unknown", "engine": engine, "source": source, "reason": reason[:180], **extra}


def _read_stable(path: Path, label: str) -> bytes:
    """Read a small file and fail if it changes while being observed."""
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if before.st_size > _MAX_IDENTITY_BYTES:
            raise ValueError(f"{label} exceeds the identity observation limit")
        raw = stream.read(_MAX_IDENTITY_BYTES)
        after = os.fstat(stream.fileno())
    current = path.stat()
    if any((s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns) !=
           (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) for s in (after, current)):
        raise ValueError(f"{label} changed during observation")
    return raw


def read_unreal_build_identity(engine_root: Path) -> dict[str, Any]:
    """Strictly observe ``Engine/Build/Build.version`` under ``engine_root``."""
    root = Path(engine_root)
    path = root / BUILD_VERSION_RELATIVE
    try:
        raw = _read_stable(path, "Build.version")
        value = json.loads(raw.decode("utf-8-sig"))
        fields = {k: value[k] for k in ("MajorVersion", "MinorVersion", "PatchVersion", "Changelist")
                  if isinstance(value, dict) and type(value.get(k)) is int and value[k] >= 0}
        if len(fields) < 2 or not {"MajorVersion", "MinorVersion"} <= fields.keys():
            raise ValueError("Build.version lacks a verified major/minor")
        version = ".".join(str(fields[k]) for k in ("MajorVersion", "MinorVersion", "PatchVersion") if k in fields)
        return {"status": "observed", "engine": "unreal", "engineRoot": str(root), "version": version,
                "major": fields["MajorVersion"], "minor": fields["MinorVersion"], **fields,
                "source": "build_version_file", "sourcePath": str(path),
                "sha256": hashlib.sha256(raw).hexdigest(), "hashScope": "file"}
    except (OSError, ValueError, TypeError) as exc:
        return _unknown("unreal", "build_version_file", str(exc), engineRoot=str(root), sourcePath=str(path))


def infer_unreal_version_from_folder(engine_root: Path) -> dict[str, Any]:
    """Guess a version from the folder name. Always ``status: unknown``."""
    root = Path(engine_root)
    match = _FOLDER_VERSION.match(root.name)
    if not match:
        return _unknown("unreal", "folder_name_inferred", "folder name carries no version", engineRoot=str(root))
    major, minor = int(match.group(1)), int(match.group(2))
    return _unknown("unreal", "folder_name_inferred", "version guessed from the folder name; Build.version not observed",
                    engineRoot=str(root), version=".".join(match.group(i) for i in (1, 2, 3) if match.group(i)),
                    major=major, minor=minor)


def describe_unreal_engine_identity(engine_root: Path) -> dict[str, Any]:
    """Build.version when observable; otherwise an unknown identity that may
    carry a folder-name guess clearly labelled ``folder_name_inferred``."""
    identity = read_unreal_build_identity(engine_root)
    if identity["status"] == "observed":
        return identity
    guess = infer_unreal_version_from_folder(engine_root)
    if guess.get("version"):
        return {**identity, "inferred": {k: guess[k] for k in ("version", "major", "minor", "source")}}
    return identity


def read_unity_project_version(project_root: Path) -> dict[str, Any]:
    """Observe ``ProjectSettings/ProjectVersion.txt`` ``m_EditorVersion``."""
    root = Path(project_root)
    path = root / UNITY_VERSION_RELATIVE
    try:
        raw = _read_stable(path, "ProjectVersion.txt")
        match = _UNITY_EDITOR_VERSION.search(raw.decode("utf-8-sig", errors="replace"))
        if not match:
            raise ValueError("ProjectVersion.txt lacks a parseable m_EditorVersion")
        major, minor, patch, suffix = int(match.group(1)), int(match.group(2)), int(match.group(3)), match.group(4) or ""
        return {"status": "observed", "engine": "unity", "projectRoot": str(root),
                "version": f"{major}.{minor}.{patch}{suffix}", "major": major, "minor": minor,
                "source": "project_version_file", "sourcePath": str(path),
                "sha256": hashlib.sha256(raw).hexdigest(), "hashScope": "file"}
    except (OSError, ValueError) as exc:
        return _unknown("unity", "project_version_file", str(exc), projectRoot=str(root), sourcePath=str(path))


__all__ = [
    "BUILD_VERSION_RELATIVE",
    "UNITY_VERSION_RELATIVE",
    "describe_unreal_engine_identity",
    "infer_unreal_version_from_folder",
    "read_unity_project_version",
    "read_unreal_build_identity",
]
