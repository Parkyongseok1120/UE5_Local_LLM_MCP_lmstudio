#!/usr/bin/env python
"""Patch LM Studio mcp.json entries for unreal-rag and unreal-agent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from workspace_paths import find_workspace_root as resolve_workspace_root, resolve_index_path

DEFAULT_LMSTUDIO_ROOT = Path.home() / ".lmstudio"
SHARED_CONFIG = DEFAULT_LMSTUDIO_ROOT / "config" / "unreal-workspace.json"
NODE_CANDIDATES = (
    Path(r"C:\Program Files\nodejs\node.exe"),
    Path(r"C:\Program Files (x86)\nodejs\node.exe"),
    Path.home() / "AppData/Local/Programs/nodejs/node.exe",
)


def resolve_node_exe() -> Path:
    for candidate in NODE_CANDIDATES:
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError("node.exe not found; install Node.js or pass --node")


def resolve_npx_cli(node_exe: Path | None = None) -> Path:
    node_exe = node_exe or resolve_node_exe()
    cli = node_exe.parent / "node_modules" / "npm" / "bin" / "npx-cli.js"
    if not cli.exists():
        raise FileNotFoundError(f"npx-cli.js not found next to {node_exe}")
    return cli.resolve()


def resolve_mcp_remote_proxy() -> Path:
    proxy = DEFAULT_LMSTUDIO_ROOT / "mcp-tools" / "node_modules" / "mcp-remote" / "dist" / "proxy.js"
    if not proxy.exists():
        raise FileNotFoundError(
            f"mcp-remote not installed at {proxy}. Run: npm install mcp-remote --prefix {proxy.parents[3]}"
        )
    return proxy.resolve()


def patch_node_commands(entry: dict[str, Any], node_exe: Path, mcp_remote_proxy: Path) -> dict[str, Any]:
    command = str(entry.get("command") or "")
    args = list(entry.get("args") or [])
    if command in {"node", "npx"} or command.lower().endswith(("node.exe", "npx.cmd")):
        if any(str(arg) == "mcp-remote" for arg in args):
            remote_url = next((str(arg) for arg in args if str(arg).startswith("http")), "")
            if remote_url:
                entry["command"] = str(node_exe)
                entry["args"] = [str(mcp_remote_proxy), remote_url]
                entry.pop("env", None)
                return entry
        if command == "npx":
            npx_cli = resolve_npx_cli(node_exe)
            entry["command"] = str(node_exe)
            entry["args"] = [str(npx_cli), *args]
        else:
            entry["command"] = str(node_exe)
    return entry


def find_workspace_root() -> Path:
    return resolve_workspace_root(DEFAULT_LMSTUDIO_ROOT)


def patch_server(entry: dict[str, Any], workspace: Path, shared_config: Path) -> dict[str, Any]:
    env = dict(entry.get("env") or {})
    env["SHARED_UNREAL_CONFIG"] = str(shared_config)
    entry["env"] = env
    return entry


def patch_unreal_rag(entry: dict[str, Any], workspace: Path, python_exe: Path) -> dict[str, Any]:
    index = resolve_index_path(workspace)
    entry["command"] = str(python_exe)
    entry["args"] = [
        str(workspace / "scripts" / "unreal_rag_mcp.py"),
        "--index",
        str(index),
    ]
    entry = patch_server(entry, workspace, SHARED_CONFIG)
    env = dict(entry.get("env") or {})
    env["UNREAL58_ROOT"] = str(workspace)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    entry["env"] = env
    return entry


def resolve_agent_root(workspace: Path) -> Path:
    bundled = workspace / "lmstudio-unreal-agent-mcp"
    if (bundled / "src" / "server.js").is_file():
        return bundled.resolve()
    fallback = DEFAULT_LMSTUDIO_ROOT / "lmstudio-unreal-agent-mcp"
    if (fallback / "src" / "server.js").is_file():
        return fallback.resolve()
    raise FileNotFoundError(
        "lmstudio-unreal-agent-mcp not found beside workspace or under ~/.lmstudio"
    )


def patch_unreal_agent(entry: dict[str, Any], workspace: Path, node_exe: Path) -> dict[str, Any]:
    agent_root = resolve_agent_root(workspace)
    entry["command"] = str(node_exe)
    entry["args"] = [str(agent_root / "src" / "server.js")]
    env = dict(entry.get("env") or {})
    env.setdefault("WORKSPACE_ROOT", str(Path.home() / "Documents"))
    env["AGENT_MCP_CONFIG"] = str(agent_root / "config" / "agent-mcp.json")
    env["SHARED_UNREAL_CONFIG"] = str(SHARED_CONFIG)
    env["UNREAL58_ROOT"] = str(workspace)
    entry["env"] = env
    return entry


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp-json", type=Path, default=DEFAULT_LMSTUDIO_ROOT / "mcp.json")
    parser.add_argument(
        "--python",
        type=Path,
        default=Path.home()
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "python"
        / "python.exe",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--node", type=Path, default=None)
    args = parser.parse_args()

    workspace = find_workspace_root()
    node_exe = (args.node or resolve_node_exe()).resolve()
    mcp_remote_proxy = resolve_mcp_remote_proxy()
    config = load_json(args.mcp_json)
    servers = config.setdefault("mcpServers", {})

    for name, entry in list(servers.items()):
        servers[name] = patch_node_commands(entry, node_exe, mcp_remote_proxy)

    if "unreal-rag" in servers:
        servers["unreal-rag"] = patch_unreal_rag(servers["unreal-rag"], workspace, args.python)
    if "unreal-agent" in servers:
        servers["unreal-agent"] = patch_unreal_agent(servers["unreal-agent"], workspace, node_exe)

    if args.dry_run:
        print(json.dumps(config, ensure_ascii=False, indent=2))
        return

    save_json(args.mcp_json, config)
    print(f"Patched {args.mcp_json}")


if __name__ == "__main__":
    main()
