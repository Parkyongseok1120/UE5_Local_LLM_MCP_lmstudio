"""Validate and safely synchronize compiled LM Studio plugin installations."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any


PRODUCTION_BUNDLE = Path(".lmstudio") / "production.js"
IDENTITY_KEYS = ("owner", "name", "revision")
MAX_MANIFEST_BYTES = 1024 * 1024


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read_manifest(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError(f"LM Studio plugin manifest is too large: {path}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"LM Studio plugin manifest must contain an object: {path}")
    return payload


def source_plugin_identity(source_dir: Path) -> dict[str, Any]:
    manifest_path = source_dir / "manifest.json"
    manifest = _read_manifest(manifest_path)
    identity = {key: manifest.get(key) for key in IDENTITY_KEYS}
    if not identity["owner"] or not identity["name"] or identity["revision"] is None:
        raise ValueError(f"LM Studio plugin source identity is incomplete: {manifest_path}")
    return identity


def plugin_tree_readiness(
    plugin_dir: Path,
    *,
    expected_identity: dict[str, Any],
) -> dict[str, Any]:
    manifest_path = plugin_dir / "manifest.json"
    production_path = plugin_dir / PRODUCTION_BUNDLE
    result: dict[str, Any] = {
        "ready": False,
        "status": "missing-manifest",
        "manifestPath": str(manifest_path),
        "productionBundlePath": str(production_path),
    }
    if not manifest_path.is_file():
        return result
    try:
        manifest = _read_manifest(manifest_path)
    except (OSError, ValueError, UnicodeError) as exc:
        result["status"] = "invalid-manifest"
        result["error"] = str(exc)
        return result
    observed = {key: manifest.get(key) for key in IDENTITY_KEYS}
    result["observedIdentity"] = observed
    if observed != expected_identity:
        result["status"] = "manifest-mismatch"
        return result
    try:
        production_ready = production_path.is_file() and production_path.stat().st_size > 0
    except OSError:
        production_ready = False
    if not production_ready:
        result["status"] = "missing-production-bundle"
        return result
    result["ready"] = True
    result["status"] = "current"
    return result


def _replace_plugin_tree(
    source_dir: Path,
    destination_dir: Path,
    *,
    expected_identity: dict[str, Any],
) -> dict[str, Any]:
    source_dir = source_dir.resolve()
    destination_dir = Path(os.path.abspath(destination_dir.expanduser()))
    if destination_dir.is_symlink():
        raise ValueError(
            f"LM Studio plugin destination must not be a symbolic link: {destination_dir}"
        )
    resolved_destination = destination_dir.resolve(strict=False)
    try:
        resolved_destination.relative_to(source_dir)
    except ValueError:
        pass
    else:
        raise ValueError(
            f"LM Studio plugin destination must not equal or be nested under source: {destination_dir}"
        )
    destination_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_parent = Path(
        tempfile.mkdtemp(prefix=f".{destination_dir.name}-staging-", dir=destination_dir.parent)
    )
    staging = staging_parent / destination_dir.name
    old = destination_dir.parent / f".{destination_dir.name}-old-{uuid.uuid4().hex}"
    moved_old = False
    committed = False
    try:
        shutil.copytree(
            source_dir,
            staging,
            ignore=shutil.ignore_patterns(
                ".git",
                ".pytest_cache",
                "__pycache__",
                "*.pyc",
                ".DS_Store",
            ),
        )
        staged = plugin_tree_readiness(staging, expected_identity=expected_identity)
        if staged["ready"] is not True:
            raise RuntimeError(
                "refusing to install an incomplete LM Studio production plugin: "
                f"{staged['status']}"
            )
        if destination_dir.exists():
            destination_dir.replace(old)
            moved_old = True
        try:
            staging.replace(destination_dir)
            committed = True
        except Exception:
            if moved_old and old.exists():
                old.replace(destination_dir)
                _sync_directory(destination_dir.parent)
            raise
        _sync_directory(destination_dir.parent)
    finally:
        if not committed and staging_parent.exists():
            shutil.rmtree(staging_parent)

    cleanup: dict[str, Any] = {"pending": False}
    if old.exists():
        try:
            shutil.rmtree(old) if old.is_dir() else old.unlink()
            _sync_directory(destination_dir.parent)
        except OSError as exc:
            cleanup = {
                "pending": True,
                "path": str(old),
                "error": str(exc),
            }
    if staging_parent.exists():
        try:
            shutil.rmtree(staging_parent)
        except OSError as exc:
            cleanup.setdefault("stagingPath", str(staging_parent))
            cleanup.setdefault("stagingError", str(exc))
    return {"backupCleanup": cleanup}


def ensure_current_plugin_install(
    *,
    source_dir: Path,
    target_dir: Path,
    installed_candidates: list[tuple[str, Path]],
) -> dict[str, Any]:
    """Reuse only a current production install; otherwise atomically sync one."""
    expected_identity = source_plugin_identity(source_dir)
    target = plugin_tree_readiness(target_dir, expected_identity=expected_identity)
    detail: dict[str, Any] = {
        "target": str(target_dir / "manifest.json"),
        "source": None,
        "copied": False,
        "ready": target["ready"],
        "previousStatus": target["status"],
    }
    if target["ready"] is True:
        detail["source"] = "already-present"
        return detail

    candidate_statuses: dict[str, str] = {}
    for source_name, candidate_dir in installed_candidates:
        candidate = plugin_tree_readiness(candidate_dir, expected_identity=expected_identity)
        candidate_statuses[source_name] = str(candidate["status"])
        if candidate["ready"] is not True:
            continue
        replacement = _replace_plugin_tree(
            candidate_dir,
            target_dir,
            expected_identity=expected_identity,
        )
        installed = plugin_tree_readiness(target_dir, expected_identity=expected_identity)
        detail.update(
            {
                "source": source_name,
                "copied": True,
                "ready": installed["ready"],
                "status": installed["status"],
                **replacement,
            }
        )
        return detail

    detail["source"] = "missing-current-production-install"
    detail["candidateStatuses"] = candidate_statuses
    return detail
