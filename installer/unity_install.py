"""Explicit local Unity installation. Model inference may be remote; Bridge never is.

Uses the integrated installer's journal/rollback for project and config writes.
Dependency installation/worker compilation are reported external actions, not atomic.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path


def checked_path(path: Path) -> Path:
    path = path.expanduser().absolute()
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError(f"Unity install paths must not contain symlinks: {item}")
    return path.resolve()


def run(command: list[str], *, cwd: Path, timeout: int = 300) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    # The installer may have bootstrapped Node outside the caller's PATH.
    if Path(command[0]).name.lower() in {"node", "node.exe"}:
        env["PATH"] = str(Path(command[0]).parent) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"Unity install command failed: {command[0]}: {(result.stderr or result.stdout)[-3000:]}")
    return result


def worker(args, root: Path, project: Path, state: Path, node: str, external: list[str]) -> tuple[Path, Path]:
    supplied = getattr(args, "unity_symbol_worker", None)
    dotnet = getattr(args, "unity_dotnet", None)
    if supplied:
        binary = checked_path(supplied)
        dotnet = Path(dotnet or shutil.which("dotnet") or "")
        if not binary.is_file() or not dotnet.is_file():
            raise ValueError("An existing --unity-symbol-worker requires a matching --unity-dotnet executable")
        return dotnet.resolve(), binary
    output = state / "unity" / "symbol-worker"
    if dotnet or shutil.which("dotnet"):
        executable = Path(dotnet or shutil.which("dotnet")).resolve()
        external.append("dotnet-publish-symbol-worker")
        run([str(executable), "publish", str(root / "unity-symbol-worker/UnitySymbolWorker.csproj"), "-c", "Release", "-o", str(output)], cwd=root)
        return executable, output / "UnitySymbolWorker.dll"
    editor = getattr(args, "unity_editor", None)
    if not editor and platform.system() == "Darwin":
        match = re.search(r"^m_EditorVersion:\s*(\S+)", (project / "ProjectSettings/ProjectVersion.txt").read_text(), re.M)
        if match and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+[abfp][0-9]+", match[1]):
            editor = Path("/Applications/Unity/Hub/Editor") / match[1] / "Unity.app/Contents/MacOS/Unity"
    if editor and Path(editor).is_file():
        editor = Path(editor).resolve()
        bases = [editor.parent.parent / "Resources/Scripting", editor.parent / "Data/Scripting"]
        for base in bases:
            executable = base / "NetCoreRuntime" / ("dotnet.exe" if os.name == "nt" else "dotnet")
            compiler = base / "DotNetSdkRoslyn"
            runtimes = sorted((base / "NetCoreRuntime/shared/Microsoft.NETCore.App").glob("*"))
            runtimes = [p for p in runtimes if p.is_dir() and (p / "System.Runtime.dll").is_file()]
            if executable.is_file() and (compiler / "csc.dll").is_file() and runtimes:
                external.append("explicit-editor-toolchain-worker-build")
                run([node, str(root / "scripts/build_unity_symbol_worker.js"), str(executable), str(compiler), str(runtimes[-1]), str(output)], cwd=root)
                return executable, output / "UnitySymbolWorker.dll"
    raise ValueError("Install a compatible .NET SDK or provide --unity-editor / --unity-dotnet and --unity-symbol-worker; no fixed engine version is assumed")


def install_unity(args, root: Path, transaction_type, lock_type, *, transaction=None, external_actions=None) -> dict:
    selected = getattr(args, "unity_project", None) or args.active_project
    if not selected:
        raise ValueError("--unity-project must name an existing Unity project root")
    project = checked_path(selected)
    if project.parent == project or not (project / "Assets").is_dir():
        raise ValueError("Unity project must have Assets, Packages/manifest.json and ProjectSettings/ProjectVersion.txt")
    manifest_path = checked_path(project / "Packages/manifest.json")
    version_path = checked_path(project / "ProjectSettings/ProjectVersion.txt")
    if not manifest_path.is_file() or not version_path.is_file():
        raise ValueError("Unity project markers are absent")
    original = manifest_path.read_bytes()
    if len(original) > 1048576:
        raise ValueError("Unity package manifest exceeds 1 MiB")
    manifest = json.loads(original)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("dependencies"), dict):
        raise ValueError("Unity dependencies must be a JSON object")
    binding = "file:" + (root / "unity-editor-bridge").resolve().as_posix()
    previous = manifest["dependencies"].get("com.evidencefirst.unity-bridge")
    if previous not in {None, binding} and not getattr(args, "unity_replace_bridge", False):
        raise ValueError("Another Bridge binding exists; use --unity-replace-bridge after reviewing it")
    manifest["dependencies"]["com.evidencefirst.unity-bridge"] = binding
    state = checked_path(args.state_home)
    if state.parent == state:
        raise ValueError("State directory must not be a filesystem root")
    identity = hashlib.sha256(str(project).encode()).hexdigest()[:24]
    config_path = checked_path(args.unity_mcp_config or state / "unity" / identity / "mcp.json")
    node = str(getattr(args, "runtime_node", None) or shutil.which("node") or "")
    report = {"ok": False, "components": ["unity"], "engine": "unity", "dryRun": args.dry_run,
              "project": str(project), "runtimeRoot": str(root), "mcpConfig": str(config_path), "sameHostRequired": True,
              "modelConnection": "not_verified", "ready": False, "externalActions": [],
              "rollbackScope": "project manifest and MCP/runtime configuration only; dependencies/compiled worker are not rolled back"}
    if args.dry_run:
        report.update(ok=True, steps=["verify Node", "install adapter dependencies", "build or validate C# worker", "bind local UPM Bridge", "write isolated MCP config", "initialize/list/status"], verification="not_run")
        return report
    if not node or int(run([node, "--version"], cwd=root).stdout.strip().lstrip("v").split(".")[0]) < 20:
        raise ValueError("Node.js 20+ required")
    tx = transaction if transaction is not None else transaction_type(state, [state, project / "Packages", config_path.parent], dry_run=False)
    lock = lock_type(state, dry_run=False) if transaction is None else None
    if lock is not None:
        lock.acquire()
    try:
        adapter = root / "lmstudio-unity-mcp"
        if not args.skip_deps:
            pnpm = shutil.which("pnpm")
            npm = str(getattr(args, "runtime_npm", None) or shutil.which("npm") or "")
            command = [pnpm, "install", "--frozen-lockfile", "--ignore-scripts"] if pnpm else [npm, "install", "--ignore-scripts", "--no-audit", "--no-fund", "--no-package-lock"]
            if not command[0]:
                raise ValueError("npm or pnpm is required, or use --skip-deps with verified installed dependencies")
            report["externalActions"].append("node-dependencies")
            env = os.environ.copy(); env["PATH"] = str(Path(node).parent) + os.pathsep + env.get("PATH", "")
            result = subprocess.run(command, cwd=adapter, env=env, capture_output=True, text=True, timeout=300)
            if result.returncode:
                raise RuntimeError((result.stderr or result.stdout)[-3000:])
        run([node, "-e", "require('./src/server.js')"], cwd=adapter)
        dotnet, binary = worker(args, root, project, state, node, report["externalActions"])
        config = {"mcpServers": {"unity-tools": {"command": node, "args": [str(adapter / "src/server.js")], "env": {
            "UNITY_PROJECT_ROOT": str(project), "UNITY_DOTNET": str(dotnet), "UNITY_SYMBOL_WORKER": str(binary), "ALLOW_WRITE": "0", "ALLOW_COMMANDS": "0"}}}}
        if config_path.exists():
            if config_path.stat().st_size > 1048576:
                raise ValueError("MCP configuration exceeds 1 MiB")
            current = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(current, dict) or not isinstance(current.get("mcpServers", {}), dict):
                raise ValueError("Existing MCP configuration is invalid")
            current.setdefault("mcpServers", {}).update(config["mcpServers"]); config = current
        if manifest_path.read_bytes() != original:
            raise ValueError("Unity package manifest changed during installation; no project settings overwritten")
        tx.write_file(manifest_path, (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode())
        tx.write_file(config_path, (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode())
        python = str(Path(getattr(args, "runtime_python", None) or sys.executable).resolve())
        tx.write_file(state / "runtime-python.path", (python + "\n").encode())
        check = run([node, str(root / "scripts/check_unity_install.js"), str(config_path)], cwd=root, timeout=45)
        report["verification"] = json.loads(check.stdout)
        report["ready"] = report["verification"].get("connection") == "connected"
        report["worker"] = str(binary); report["ok"] = True
        if transaction is None:
            report["journal"] = str(tx.commit(report))
        return report
    except Exception:
        if transaction is None:
            tx.rollback_actions()
        raise
    finally:
        if external_actions is not None:
            external_actions.extend("unity-" + action for action in report["externalActions"])
        if lock is not None:
            lock.release()
