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
RAG_COMPONENT_VERSION = "0.3.1"
COMPONENTS = ("agent", "rag", "compactor")
PROTOCOL_IDENTITY_FIELDS = tuple(HASH_SECTIONS)


class ControlRuntimeMismatch(RuntimeError):
    error_code = "CONTROL_RUNTIME_VERSION_MISMATCH"


class ControlRuntimeSourceHeadMismatch(ControlRuntimeMismatch):
    error_code = "CONTROL_RUNTIME_SOURCE_HEAD_MISMATCH"


def _repository_root(value: str | Path | None = None) -> Path:
    return Path(value).expanduser().resolve() if value else Path(__file__).resolve().parents[1]


def _component_files(root: Path, component: str) -> list[Path]:
    if component == "agent":
        base = root / "lmstudio-unreal-agent-mcp"
        files = [
            base / "package.json",
            root / "config" / "synthesis_readiness_policy.json",
            root / "config" / "control_state_machine.json",
            root / "scripts" / "control_transition_bridge.py",
            root / "scripts" / "control_state_registry.py",
            root / "scripts" / "phase_tool_router.py",
            root / "scripts" / "synthesis_readiness.py",
            root / "scripts" / "task_api.py",
            root / "scripts" / "task_gate_history.py",
            root / "scripts" / "workspace_paths.py",
            *(base / "src").rglob("*.js"),
        ]
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
            "control_state_registry.py",
            "control_protocol_spec.py",
            "synthesis_readiness.py",
        )
        files = [base / name for name in names]
        files.append(root / "config" / "synthesis_readiness_policy.json")
        files.append(root / "config" / "control_state_machine.json")
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
    for file_path in sorted(
        files,
        key=lambda item: os.path.relpath(item, component_base).replace("\\", "/"),
    ):
        relative = os.path.relpath(file_path, component_base).replace("\\", "/").encode("utf-8")
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
        git_root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        git_root = None
    if (
        git_root is not None
        and git_root.returncode == 0
        and Path(git_root.stdout.strip()).resolve() == root.resolve()
    ):
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
        if completed.returncode == 0:
            return completed.stdout.strip()[:80]
    # Relocatable production bundles intentionally exclude .git. The package
    # builder seals the source commit into its deterministic inventory manifest
    # so every installed component retains the release identity.
    try:
        packaged = json.loads(
            (root / "package-manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return ""
    return str(packaged.get("sourceGitCommit") or "").strip()[:80]


def assert_source_tree_matches_head(
    repository_root: str | Path | None = None,
) -> str:
    """Return the sealed source commit, rejecting runtime-source drift.

    Untracked build products outside production code/config roots remain
    outside this gate. Untracked files under a packaged runtime root are
    rejected because they would otherwise be shipped under the identity of a
    commit that never contained them.
    A relocatable package without .git inherits the commit sealed by its
    package manifest.
    """
    root = _repository_root(repository_root)
    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        inside = None
    if (
        inside is not None
        and inside.returncode == 0
        and Path(inside.stdout.strip()).resolve() == root.resolve()
    ):
        try:
            diff = subprocess.run(
                ["git", "diff", "--quiet", "HEAD", "--"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ControlRuntimeSourceHeadMismatch(
                f"CONTROL_RUNTIME_SOURCE_HEAD_MISMATCH: unable to verify tracked source tree ({exc})"
            ) from exc
        if diff.returncode == 1:
            raise ControlRuntimeSourceHeadMismatch(
                "CONTROL_RUNTIME_SOURCE_HEAD_MISMATCH: tracked source tree differs from HEAD"
            )
        if diff.returncode != 0:
            detail = (diff.stderr or diff.stdout or "git diff failed").strip()
            raise ControlRuntimeSourceHeadMismatch(
                f"CONTROL_RUNTIME_SOURCE_HEAD_MISMATCH: unable to verify tracked source tree ({detail})"
            )
        try:
            untracked = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ControlRuntimeSourceHeadMismatch(
                f"CONTROL_RUNTIME_SOURCE_HEAD_MISMATCH: unable to verify untracked runtime sources ({exc})"
            ) from exc
        if untracked.returncode != 0:
            detail = (untracked.stderr or untracked.stdout or "git ls-files failed").strip()
            raise ControlRuntimeSourceHeadMismatch(
                f"CONTROL_RUNTIME_SOURCE_HEAD_MISMATCH: unable to verify untracked runtime sources ({detail})"
            )
        runtime_prefixes = (
            "config/",
            "scripts/",
            "lmstudio-unreal-agent-mcp/src/",
            "lmstudio-context-compactor-plugin/src/",
        )
        runtime_root_files = {
            "lmstudio-unreal-agent-mcp/package.json",
            "lmstudio-context-compactor-plugin/package.json",
            "lmstudio-context-compactor-plugin/manifest.json",
        }
        untracked_runtime = sorted(
            relative.replace("\\", "/")
            for relative in untracked.stdout.splitlines()
            if relative.replace("\\", "/") in runtime_root_files
            or relative.replace("\\", "/").startswith(runtime_prefixes)
        )
        if untracked_runtime:
            raise ControlRuntimeSourceHeadMismatch(
                "CONTROL_RUNTIME_SOURCE_HEAD_MISMATCH: untracked runtime source files exist: "
                + ", ".join(untracked_runtime[:8])
            )
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
        commit = completed.stdout.strip()[:80] if completed.returncode == 0 else ""
        if commit:
            return commit
        raise ControlRuntimeSourceHeadMismatch(
            "CONTROL_RUNTIME_SOURCE_HEAD_MISMATCH: clean checkout HEAD is unavailable"
        )
    try:
        packaged = json.loads(
            (root / "package-manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        packaged = {}
    commit = str(packaged.get("sourceGitCommit") or "").strip()[:80]
    if commit:
        return commit
    raise ControlRuntimeSourceHeadMismatch(
        "CONTROL_RUNTIME_SOURCE_HEAD_MISMATCH: source commit is not sealed in this package"
    )


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
    git_commit: str | None = None,
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
        "gitCommit": str(git_commit or _git_commit(root)).strip()[:80],
        "componentVersion": versions[component],
        "protocolVersion": PROTOCOL_VERSION,
        **{field: protocol[field] for field in PROTOCOL_IDENTITY_FIELDS},
    }


def build_runtime_manifest(
    repository_root: str | Path | None = None,
    *,
    require_clean_source: bool = False,
) -> dict[str, Any]:
    root = _repository_root(repository_root)
    expected_source_commit = (
        assert_source_tree_matches_head(root)
        if require_clean_source
        else _git_commit(root)
    )
    return {
        "schemaVersion": 2,
        "protocolVersion": PROTOCOL_VERSION,
        # One package-level source/evaluation identity owns the expected HEAD.
        # Component gitCommit/buildHash fields below remain the independent
        # relocatable self-integrity evidence for each installed runtime.
        "expectedSourceGitCommit": expected_source_commit,
        "protocolSpec": load_control_protocol_spec(repository_root=root),
        "components": {
            component: component_identity(
                component,
                repository_root=root,
                git_commit=expected_source_commit,
            )
            for component in COMPONENTS
        },
    }


def verify_runtime_component(
    component: str,
    *,
    manifest_path: str | Path | None = None,
    repository_root: str | Path | None = None,
    required: bool | None = None,
    expected_git_commit: str | None = None,
) -> dict[str, Any]:
    raw_path = str(manifest_path or os.environ.get("CONTROL_RUNTIME_MANIFEST") or "").strip()
    is_required = (
        required
        if required is not None
        else os.environ.get("CONTROL_RUNTIME_REQUIRED", "").strip().casefold()
        in {"1", "true", "yes", "on"}
    )
    running = component_identity(component, repository_root=repository_root)
    expected_source_commit = str(
        expected_git_commit
        or os.environ.get("CONTROL_RUNTIME_EXPECTED_GIT_COMMIT")
        or ""
    ).strip()[:80]
    def provenance(verified: bool, expected_identity: dict[str, Any] | None = None) -> dict[str, Any]:
        installed_commit = str(
            (expected_identity or {}).get("gitCommit") or running.get("gitCommit") or ""
        )
        source_matched: bool | None = (
            installed_commit == expected_source_commit
            if expected_source_commit
            else None
        )
        return {
            "bundleIntegrityVerified": bool(verified),
            "installedGitCommit": installed_commit,
            "expectedGitCommit": expected_source_commit,
            "sourceHeadMatched": source_matched,
            "runtimeStale": source_matched is False,
            "runtimeVerified": bool(verified and source_matched is not False),
        }
    if not raw_path:
        if is_required:
            raise ControlRuntimeMismatch("CONTROL_RUNTIME_VERSION_MISMATCH: manifest is required")
        return {
            "ok": True,
            "verified": False,
            "reason": "manifest_not_configured",
            "running": running,
            **provenance(False),
        }
    path = Path(raw_path).expanduser().resolve()
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if not expected_source_commit:
            expected_source_commit = str(
                manifest.get("expectedSourceGitCommit") or ""
            ).strip()[:80]
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
    status = provenance(True, expected)
    if status["runtimeStale"]:
        raise ControlRuntimeSourceHeadMismatch(
            "CONTROL_RUNTIME_SOURCE_HEAD_MISMATCH: "
            f"installed {status['installedGitCommit'] or 'unknown'} does not match "
            f"expected {status['expectedGitCommit']}"
        )
    return {
        "ok": True,
        "verified": True,
        "manifestPath": str(path),
        "expected": expected,
        "running": running,
        **status,
    }


if __name__ == "__main__":
    print(json.dumps(build_runtime_manifest(), ensure_ascii=False, indent=2))
