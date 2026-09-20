#!/usr/bin/env python3
"""Refresh and verify an in-place Unity MCP installation from this source tree.

The MCP configuration already points at source files. Obtain newer source files
before running this command. Bridge or symbol-worker migrations use install.py.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def _read_config(config_path: Path, source_root: Path, *, if_present: bool = False) -> dict | None:
    config_path = config_path.expanduser().absolute()
    if config_path.is_symlink() or not config_path.is_file() or config_path.stat().st_size > 1048576:
        raise ValueError("MCP config must be an existing regular file of at most 1 MiB")
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    servers = config.get("mcpServers") if isinstance(config, dict) else None
    entry = servers.get("unity-tools") if isinstance(servers, dict) else None
    if not isinstance(entry, dict):
        if if_present:
            return None
        raise ValueError("MCP config has no unity-tools entry")
    command, arguments, env = entry.get("command"), entry.get("args"), entry.get("env")
    if not isinstance(command, str) or not command or not isinstance(arguments, list) or not arguments or not isinstance(env, dict):
        raise ValueError("unity-tools command, args or env is invalid")
    server = source_root / "lmstudio-unity-mcp/src/server.js"
    if not server.is_file() or not (source_root / "scripts/check_unity_install.js").is_file():
        raise ValueError("Source tree is missing the Unity MCP runtime or install check")
    if not isinstance(arguments[0], str) or not Path(arguments[0]).is_absolute() or Path(arguments[0]).resolve() != server.resolve():
        raise ValueError("unity-tools uses a different source tree; use install.py to change its binding")
    project_string = env.get("UNITY_PROJECT_ROOT")
    if not isinstance(project_string, str) or not Path(project_string).is_absolute():
        raise ValueError("UNITY_PROJECT_ROOT must be an absolute Unity project path")
    project = Path(project_string).resolve()
    markers = [project / "Assets", project / "Packages/manifest.json", project / "ProjectSettings/ProjectVersion.txt"]
    if not markers[0].is_dir() or any(not marker.is_file() for marker in markers[1:]):
        raise ValueError("UNITY_PROJECT_ROOT is not an available Unity project")
    manifest = json.loads(markers[1].read_text(encoding="utf-8-sig"))
    dependencies = manifest.get("dependencies") if isinstance(manifest, dict) else None
    if not isinstance(dependencies, dict):
        raise ValueError("Unity package dependencies must be an object")
    binding = dependencies.get("com.evidencefirst.unity-bridge")
    expected_binding = "file:" + (source_root / "unity-editor-bridge").resolve().as_posix()
    if binding != expected_binding:
        raise ValueError("Unity Bridge binding differs from this source tree; use install.py for a Bridge migration")
    executable = Path(command)
    if executable.is_absolute():
        if not executable.is_file():
            raise ValueError("Configured Node executable is missing")
        node = str(executable)
    else:
        node = shutil.which(command)
        if not node:
            raise ValueError("Configured Node executable was not found on PATH")
    return {"config": str(config_path), "project": str(project), "sourceRoot": str(source_root), "node": node,
            "server": str(server), "otherServerCount": len(servers) - 1}


def _run(command: list[str], *, cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    shell = os.name == "nt" and Path(command[0]).suffix.lower() in {".cmd", ".bat"}
    native_command: list[str] | str = subprocess.list2cmdline(command) if shell else command
    result = subprocess.run(native_command, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=timeout, check=False, shell=shell,
                            env={**os.environ, "PATH": str(Path(command[0]).parent) + os.pathsep
                                 + os.environ.get("PATH", "")})
    if result.returncode:
        raise RuntimeError(f"Update check failed ({Path(command[0]).name}): {(result.stderr or result.stdout)[-2000:]}")
    return result


def _npm_for(node: str) -> str:
    directory = Path(node).parent
    for name in ("npm.cmd", "npm.exe", "npm") if os.name == "nt" else ("npm",):
        candidate = directory / name
        if candidate.is_file():
            return str(candidate)
    resolved = shutil.which("npm")
    if not resolved:
        raise ValueError("npm is required; use --skip-deps only when current dependencies are valid")
    return resolved


def update(config_path: Path, *, source_root: Path = ROOT, dry_run: bool = False,
           skip_deps: bool = False, if_present: bool = False) -> dict:
    source_root = source_root.expanduser().resolve()
    details = _read_config(config_path, source_root, if_present=if_present)
    if details is None:
        return {"ok": True, "dryRun": dry_run, "scope": "in_place_unity_mcp",
                "installed": False, "skipped": "unity-tools is not installed", "restartRequired": False}
    adapter = source_root / "lmstudio-unity-mcp"
    report = {"ok": True, "dryRun": dry_run, "scope": "in_place_unity_mcp", "config": details["config"],
              "project": details["project"], "sourceRoot": details["sourceRoot"], "configurationChanged": False,
              "projectChanged": False, "otherServerCount": details["otherServerCount"], "restartRequired": not dry_run}
    if dry_run:
        report["verification"] = "not_run"
        report["dependencies"] = "skipped" if skip_deps else "would_refresh"
        return report
    node = details["node"]
    version = _run([node, "--version"], cwd=source_root, timeout=15).stdout.strip()
    if not version.startswith("v") or int(version[1:].split(".")[0]) < 20:
        raise ValueError("Node.js 20+ required")
    if not skip_deps:
        npm = _npm_for(node)
        _run([npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=adapter, timeout=600)
        report["dependencies"] = "npm_ci"
    else:
        report["dependencies"] = "skipped"
    result = _run([node, str(source_root / "scripts/check_unity_install.js"), details["config"]], cwd=source_root, timeout=45)
    verification = json.loads(result.stdout)
    if verification.get("initialized") is not True:
        raise RuntimeError("Unity MCP did not initialize successfully")
    report["verification"] = verification
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp-config", type=Path, required=True, help="Existing MCP JSON containing unity-tools")
    parser.add_argument("--dry-run", action="store_true", help="Inspect bindings without installing dependencies or starting MCP")
    parser.add_argument("--skip-deps", action="store_true", help="Use existing Node dependencies")
    parser.add_argument("--if-present", action="store_true", help="Succeed without changes when unity-tools is not installed")
    args = parser.parse_args()
    try:
        print(json.dumps(update(args.mcp_config, dry_run=args.dry_run, skip_deps=args.skip_deps,
                                if_present=args.if_present), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        print(f"Unity MCP update failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
