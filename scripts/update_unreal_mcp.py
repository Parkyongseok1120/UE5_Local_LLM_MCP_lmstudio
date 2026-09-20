#!/usr/bin/env python3
"""Refresh and verify in-place Unreal Agent and RAG MCP runtimes.

The existing mcp.json remains authoritative for project, engine, index and
permission settings. This updater accepts only entries bound to this source
tree, refreshes locked Node dependencies, and performs read-only MCP catalog
smokes without rewriting the configuration.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_TOOLS = {
    "workspace_status", "git_status", "git_log", "git_changed_files",
    "git_diff_file", "git_read_file",
}
UNSAFE_PROCESS_ENV = {
    "NODE_OPTIONS", "NODE_PATH", "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP",
    "LD_PRELOAD", "DYLD_INSERT_LIBRARIES",
}


def _executable(command: str, label: str) -> str:
    candidate = Path(command)
    if candidate.is_absolute():
        if not candidate.is_file():
            raise ValueError(f"Configured {label} executable is missing")
        return str(candidate)
    resolved = shutil.which(command)
    if not resolved:
        raise ValueError(f"Configured {label} executable was not found on PATH")
    return resolved


def _entry(servers: dict[str, Any], name: str) -> tuple[dict[str, Any], list[str], dict[str, str]]:
    value = servers.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"MCP config has no {name} entry")
    arguments, env = value.get("args"), value.get("env")
    if (not isinstance(value.get("command"), str) or not value["command"]
            or not isinstance(arguments, list) or not arguments
            or not all(isinstance(item, str) for item in arguments)
            or not isinstance(env, dict)
            or not all(isinstance(key, str) and isinstance(item, str) for key, item in env.items())):
        raise ValueError(f"{name} command, args or env is invalid")
    return value, arguments, env


def _read_config(config_path: Path, source_root: Path, *, if_present: bool = False) -> dict | None:
    config_path = config_path.expanduser().absolute()
    if config_path.is_symlink() or not config_path.is_file() or config_path.stat().st_size > 1048576:
        raise ValueError("MCP config must be an existing regular file of at most 1 MiB")
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    servers = config.get("mcpServers") if isinstance(config, dict) else None
    if servers is None and if_present:
        return None
    if not isinstance(servers, dict):
        raise ValueError("MCP config must contain an mcpServers object")
    present = {name for name in ("unreal-agent", "unreal-rag") if isinstance(servers.get(name), dict)}
    if not present and if_present:
        return None
    if present != {"unreal-agent", "unreal-rag"}:
        raise ValueError("Unreal MCP installation is incomplete; unreal-agent and unreal-rag must both be present")

    agent, agent_args, agent_env = _entry(servers, "unreal-agent")
    rag, rag_args, rag_env = _entry(servers, "unreal-rag")
    agent_source = source_root / "lmstudio-unreal-agent-mcp/src/direct-server.js"
    rag_source = source_root / "scripts/unreal_rag_direct.py"
    manifest = source_root / "config/stable_tool_manifest.json"
    for required in (agent_source, rag_source, manifest, source_root / "lmstudio-unreal-agent-mcp/package-lock.json"):
        if not required.is_file():
            raise ValueError(f"Source tree is missing required Unreal runtime file: {required.relative_to(source_root)}")
    if not Path(agent_args[0]).is_absolute() or Path(agent_args[0]).resolve() != agent_source.resolve():
        raise ValueError("unreal-agent uses a different source tree; use install.py to change its binding")
    if not Path(rag_args[0]).is_absolute() or Path(rag_args[0]).resolve() != rag_source.resolve():
        raise ValueError("unreal-rag uses a different source tree; use install.py to change its binding")

    node = _executable(agent["command"], "Node")
    python = _executable(rag["command"], "Python")
    expected = json.loads(manifest.read_text(encoding="utf-8-sig"))
    return {
        "config": str(config_path), "sourceRoot": str(source_root), "node": node, "python": python,
        "agentArgs": agent_args, "agentEnv": agent_env, "ragArgs": rag_args, "ragEnv": rag_env,
        "agentTools": list(expected["agentEssential"]), "ragTools": list(expected["ragEssential"]),
        "otherServerCount": len(servers) - 2,
        "permissions": {key: agent_env.get(key, "0") for key in
                        ("ALLOW_WRITE", "ALLOW_COMMANDS", "ALLOW_UNREAL_BUILD")},
    }


def _process_env(configured: dict[str, str]) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key.upper() not in UNSAFE_PROCESS_ENV}
    env.update({key: value for key, value in configured.items() if key.upper() not in UNSAFE_PROCESS_ENV})
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run(command: list[str], *, cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    shell = os.name == "nt" and Path(command[0]).suffix.lower() in {".cmd", ".bat"}
    native_command: list[str] | str = subprocess.list2cmdline(command) if shell else command
    result = subprocess.run(
        native_command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, check=False, shell=shell,
        env={**os.environ, "PATH": str(Path(command[0]).parent) + os.pathsep + os.environ.get("PATH", "")},
    )
    if result.returncode:
        detail = (result.stderr or result.stdout)[-2000:]
        raise RuntimeError(f"Update command failed ({Path(command[0]).name}): {detail}")
    return result


def _mcp_tools(command: str, arguments: list[str], configured_env: dict[str, str], *, cwd: Path) -> set[str]:
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "integrated-update-check", "version": "1"},
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    result = subprocess.run(
        [command, *arguments], cwd=cwd,
        input="\n".join(json.dumps(item, separators=(",", ":")) for item in messages) + "\n",
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45, check=False,
        env=_process_env(configured_env),
    )
    if result.returncode:
        raise RuntimeError(f"MCP smoke failed ({Path(arguments[0]).name}): {(result.stderr or result.stdout)[-2000:]}")
    responses = []
    for line in result.stdout.splitlines():
        try:
            responses.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    listed = next((item for item in responses if item.get("id") == 2), None)
    tools = listed.get("result", {}).get("tools") if isinstance(listed, dict) else None
    if not isinstance(tools, list) or not all(isinstance(item, dict) and isinstance(item.get("name"), str) for item in tools):
        raise RuntimeError(f"MCP tools/list response missing ({Path(arguments[0]).name})")
    return {item["name"] for item in tools}


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
        return {"ok": True, "dryRun": dry_run, "scope": "in_place_unreal_mcp",
                "installed": False, "skipped": "unreal-agent and unreal-rag are not installed",
                "restartRequired": False}
    report: dict[str, Any] = {
        "ok": True, "dryRun": dry_run, "scope": "in_place_unreal_mcp",
        "installed": True, "config": details["config"], "sourceRoot": details["sourceRoot"],
        "configurationChanged": False, "permissionsChanged": False,
        "permissions": details["permissions"], "otherServerCount": details["otherServerCount"],
        "restartRequired": not dry_run,
    }
    if dry_run:
        report["dependencies"] = "skipped" if skip_deps else "would_refresh_locked_node_dependencies"
        report["verification"] = "not_run"
        return report

    node_version = _run([details["node"], "--version"], cwd=source_root, timeout=15).stdout.strip()
    if not node_version.startswith("v") or int(node_version[1:].split(".")[0]) < 20:
        raise ValueError("Node.js 20+ required")
    _run([details["python"], "--version"], cwd=source_root, timeout=15)
    agent_root = source_root / "lmstudio-unreal-agent-mcp"
    if skip_deps:
        report["dependencies"] = "skipped"
    else:
        npm = _npm_for(details["node"])
        _run([npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"], cwd=agent_root, timeout=600)
        report["dependencies"] = "npm_ci"

    agent_tools = _mcp_tools(details["node"], details["agentArgs"], details["agentEnv"], cwd=source_root)
    expected_agent = set(details["agentTools"])
    if details["agentEnv"].get("WORKSPACE_CAPABILITIES") == "0":
        expected_agent -= WORKSPACE_TOOLS
    rag_tools = _mcp_tools(details["python"], details["ragArgs"], details["ragEnv"], cwd=source_root)
    if agent_tools != expected_agent:
        raise RuntimeError(f"unreal-agent catalog mismatch: expected {len(expected_agent)}, got {len(agent_tools)}")
    if rag_tools != set(details["ragTools"]):
        raise RuntimeError(f"unreal-rag catalog mismatch: expected {len(details['ragTools'])}, got {len(rag_tools)}")
    report["verification"] = {
        "unrealAgentInitialized": True, "unrealAgentTools": len(agent_tools),
        "unrealRagInitialized": True, "unrealRagTools": len(rag_tools),
        "modelConnection": "not_verified", "engineExecution": "not_requested",
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp-config", type=Path, required=True, help="Existing MCP JSON containing Unreal entries")
    parser.add_argument("--dry-run", action="store_true", help="Inspect bindings without dependencies or MCP startup")
    parser.add_argument("--skip-deps", action="store_true", help="Use existing locked Node dependencies")
    parser.add_argument("--if-present", action="store_true", help="Succeed when Unreal MCP is not installed")
    args = parser.parse_args()
    try:
        print(json.dumps(update(args.mcp_config, dry_run=args.dry_run, skip_deps=args.skip_deps,
                                if_present=args.if_present), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        print(f"Unreal MCP update failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
