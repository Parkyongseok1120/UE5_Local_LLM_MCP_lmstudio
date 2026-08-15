#!/usr/bin/env python3
"""Deterministic identities and fail-closed manifests for control components."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from control_protocol_spec import (
    HASH_SECTIONS,
    control_protocol_identity,
    load_control_protocol_spec,
)


PROTOCOL_VERSION = 2
RAG_COMPONENT_VERSION = "0.3.0"
COMPONENTS = ("agent", "rag", "compactor")
PROTOCOL_IDENTITY_FIELDS = tuple(HASH_SECTIONS)


class ControlRuntimeMismatch(RuntimeError):
    error_code = "CONTROL_RUNTIME_VERSION_MISMATCH"


def _repository_root(value: str | Path | None = None) -> Path:
    return Path(value).expanduser().resolve() if value else Path(__file__).resolve().parents[1]


def _component_files(root: Path, component: str) -> list[Path]:
    if component == "agent":
        base = root / "lmstudio-unreal-agent-mcp"
        files = [base / "package.json", *(base / "src").rglob("*.js")]
    elif component == "compactor":
        base = root / "lmstudio-context-compactor-plugin"
        files = [
            base / "package.json",
            base / "manifest.json",
            *(base / "src").rglob("*.js"),
            *(base / "src").rglob("*.ts"),
        ]
    elif component == "rag":
        base = root / "scripts"
        names = (
            "unreal_rag_mcp.py",
            "task_api.py",
            "task_phase.py",
            "task_gate_history.py",
            "task_continuity.py",
            "phase_tool_router.py",
            "mcp_control_envelope.py",
            "feature_intent_contract.py",
            "feature_intent_fast_path.py",
            "control_runtime_identity.py",
            "control_protocol_spec.py",
        )
        files = [base / name for name in names]
    else:
        raise ValueError(f"unknown control component: {component}")
    return sorted(
        {path.resolve() for path in files if path.is_file()},
        key=lambda path: path.as_posix(),
    )


def _build_hash(root: Path, component: str) -> str:
    digest = hashlib.sha256()
    files = _component_files(root, component)
    if not files:
        raise FileNotFoundError(f"no runtime files found for {component} under {root}")
    component_base = (
        root / "lmstudio-unreal-agent-mcp"
        if component == "agent"
        else root / "lmstudio-context-compactor-plugin"
        if component == "compactor"
        else root / "scripts"
    ).resolve()
    for file_path in sorted(files, key=lambda item: item.relative_to(component_base).as_posix()):
        relative = file_path.relative_to(component_base).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(file_path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _git_commit(root: Path) -> str:
    explicit = os.environ.get("CONTROL_RUNTIME_GIT_COMMIT", "").strip()
    if explicit:
        return explicit[:80]
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip()[:80] if completed.returncode == 0 else ""


def _package_version(path: Path, fallback: str) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return fallback
    return str(payload.get("version") or fallback)


def component_identity(
    component: str,
    *,
    repository_root: str | Path | None = None,
) -> dict[str, Any]:
    root = _repository_root(repository_root)
    versions = {
        "agent": _package_version(root / "lmstudio-unreal-agent-mcp" / "package.json", "unknown"),
        "rag": RAG_COMPONENT_VERSION,
        "compactor": _package_version(
            root / "lmstudio-context-compactor-plugin" / "package.json",
            "unknown",
        ),
    }
    protocol = control_protocol_identity(repository_root=root)
    if int(protocol["protocolVersion"]) != PROTOCOL_VERSION:
        raise ControlRuntimeMismatch(
            "CONTROL_RUNTIME_VERSION_MISMATCH: protocol spec version differs from runtime"
        )
    return {
        "component": component,
        "buildHash": _build_hash(root, component),
        "gitCommit": _git_commit(root),
        "componentVersion": versions[component],
        "protocolVersion": PROTOCOL_VERSION,
        **{field: protocol[field] for field in PROTOCOL_IDENTITY_FIELDS},
    }


def build_runtime_manifest(
    repository_root: str | Path | None = None,
) -> dict[str, Any]:
    root = _repository_root(repository_root)
    return {
        "schemaVersion": 2,
        "protocolVersion": PROTOCOL_VERSION,
        "protocolSpec": load_control_protocol_spec(repository_root=root),
        "components": {
            component: component_identity(component, repository_root=root)
            for component in COMPONENTS
        },
    }


def verify_runtime_component(
    component: str,
    *,
    manifest_path: str | Path | None = None,
    repository_root: str | Path | None = None,
    required: bool | None = None,
) -> dict[str, Any]:
    raw_path = str(manifest_path or os.environ.get("CONTROL_RUNTIME_MANIFEST") or "").strip()
    is_required = (
        required
        if required is not None
        else os.environ.get("CONTROL_RUNTIME_REQUIRED", "").strip().casefold()
        in {"1", "true", "yes", "on"}
    )
    running = component_identity(component, repository_root=repository_root)
    if not raw_path:
        if is_required:
            raise ControlRuntimeMismatch("CONTROL_RUNTIME_VERSION_MISMATCH: manifest is required")
        return {"ok": True, "verified": False, "reason": "manifest_not_configured", "running": running}
    path = Path(raw_path).expanduser().resolve()
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        expected = (manifest.get("components") or {}).get(component)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ControlRuntimeMismatch(
            f"CONTROL_RUNTIME_VERSION_MISMATCH: manifest unavailable ({exc})"
        ) from exc
    if not isinstance(expected, dict):
        raise ControlRuntimeMismatch(
            f"CONTROL_RUNTIME_VERSION_MISMATCH: {component} identity is missing"
        )
    mismatches = [
        key
        for key in (
            "buildHash",
            "componentVersion",
            "protocolVersion",
            "gitCommit",
            *PROTOCOL_IDENTITY_FIELDS,
        )
        if str(expected.get(key) or "") != str(running.get(key) or "")
    ]
    if mismatches:
        raise ControlRuntimeMismatch(
            "CONTROL_RUNTIME_VERSION_MISMATCH: "
            f"{component} differs in {', '.join(mismatches)}"
        )
    return {
        "ok": True,
        "verified": True,
        "manifestPath": str(path),
        "expected": expected,
        "running": running,
    }


if __name__ == "__main__":
    print(json.dumps(build_runtime_manifest(), ensure_ascii=False, indent=2))
