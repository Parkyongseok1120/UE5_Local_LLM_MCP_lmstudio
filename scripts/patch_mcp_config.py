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

from atomic_io import atomic_write_text
from workspace_paths import find_workspace_root as resolve_workspace_root

DEFAULT_LMSTUDIO_ROOT = Path.home() / ".lmstudio"
SHARED_CONFIG = DEFAULT_LMSTUDIO_ROOT / "config" / "unreal-workspace.json"
# LM Studio mcp.json server timeout is in milliseconds (0.3.18+). unreal_rag_refresh can
# run collect + index rebuild + optional Editor export for several minutes.
DEFAULT_UNREAL_RAG_MCP_TIMEOUT_MS = 420_000
# unreal-agent may run UBT builds up to COMMAND_TIMEOUT_MS (10 min) plus overhead.
DEFAULT_UNREAL_AGENT_MCP_TIMEOUT_MS = 720_000
FORBIDDEN_TOOL_CONFIRMATION_PATTERNS = {
    "lmstudio/js-code-sandbox:run_javascript",
    "lmstudio/js-code-sandbox:*",
    "mcp/unreal-agent:*",
    "mcp/unreal-rag:*",
    "mcp/unreal-rag:unreal_architecture_reasoning",
}
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


def entry_uses_mcp_remote(entry: dict[str, Any]) -> bool:
    command = str(entry.get("command") or "")
    args = list(entry.get("args") or [])
    if command not in {"node", "npx"} and not command.lower().endswith(("node.exe", "npx.cmd")):
        return False
    return any(str(arg) == "mcp-remote" for arg in args)


def patch_node_commands(
    entry: dict[str, Any], node_exe: Path, mcp_remote_proxy: Path | None
) -> dict[str, Any]:
    command = str(entry.get("command") or "")
    args = list(entry.get("args") or [])
    if command in {"node", "npx"} or command.lower().endswith(("node.exe", "npx.cmd")):
        if any(str(arg) == "mcp-remote" for arg in args):
            remote_url = next((str(arg) for arg in args if str(arg).startswith("http")), "")
            if remote_url:
                if mcp_remote_proxy is None:
                    mcp_remote_proxy = resolve_mcp_remote_proxy()
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
    return resolve_workspace_root(SCRIPTS_DIR.parent)


def patch_server(entry: dict[str, Any], workspace: Path, shared_config: Path) -> dict[str, Any]:
    env = dict(entry.get("env") or {})
    env["SHARED_UNREAL_CONFIG"] = str(shared_config)
    entry["env"] = env
    return entry


def patch_unreal_rag(
    entry: dict[str, Any],
    workspace: Path,
    python_exe: Path,
    *,
    context_compactor_advisory: bool | None = None,
) -> dict[str, Any]:
    entry["command"] = str(python_exe)
    # The RAG server resolves its index from workspace/shared configuration at
    # startup.  Do not pin the generated MCP entry to the engine version that
    # happened to be active when this repair command was run.
    entry["args"] = [str(workspace / "scripts" / "unreal_rag_direct.py")]
    entry = patch_server(entry, workspace, SHARED_CONFIG)
    env = dict(entry.get("env") or {})
    env["UNREAL58_ROOT"] = str(workspace)
    env["DIRECT_RAG_STATE_ROOT"] = str(DEFAULT_LMSTUDIO_ROOT / "state" / "unreal-rag-direct")
    env["MCP_FRONTEND"] = "lmstudio"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # Compaction is a transparent LM Studio chat plugin, not an MCP authority.
    # Clear every proxy-era telemetry/gate key while repairing older configs.
    del context_compactor_advisory
    for key in (
        "MCP_REQUIRE_CONTEXT_COMPACTOR_ACTIVE",
        "MCP_CONTEXT_COMPACTOR_REQUIRED_FRONTENDS",
        "MCP_CONTEXT_COMPACTOR_ADVISORY",
        "MCP_CONTEXT_COMPACTOR_MAX_AGE_SECONDS",
        "MCP_ESSENTIAL_TOOLS",
        "MCP_EXTENDED_TOOLS",
        "ALLOW_CONTROL_PLANE_TOOLS",
        "MCP_REQUIRE_PLAN_AUTH",
        "MCP_EXECUTION_MODE",
        "MCP_BRIDGE_PAIR_ID",
        "AGENT_STATE_ROOT",
        "CONTROL_RUNTIME_MANIFEST",
        "CONTROL_RUNTIME_COMPONENT",
        "CONTROL_RUNTIME_REQUIRED",
        "CONTROL_RUNTIME_GIT_COMMIT",
        "CONTROL_RUNTIME_EXPECTED_GIT_COMMIT",
    ):
        env.pop(key, None)
    entry["env"] = env
    entry["timeout"] = DEFAULT_UNREAL_RAG_MCP_TIMEOUT_MS
    return entry


def resolve_agent_root(workspace: Path) -> Path:
    bundled = workspace / "lmstudio-unreal-agent-mcp"
    if (bundled / "src" / "direct-server.js").is_file():
        return bundled.resolve()
    fallback = DEFAULT_LMSTUDIO_ROOT / "lmstudio-unreal-agent-mcp"
    if (fallback / "src" / "direct-server.js").is_file():
        return fallback.resolve()
    raise FileNotFoundError(
        "lmstudio-unreal-agent-mcp not found beside workspace or under ~/.lmstudio"
    )


def patch_unreal_agent(
    entry: dict[str, Any],
    workspace: Path,
    node_exe: Path,
    python_exe: Path,
) -> dict[str, Any]:
    agent_root = resolve_agent_root(workspace)
    entry["command"] = str(node_exe)
    entry["args"] = [str(agent_root / "src" / "direct-server.js")]
    env = dict(entry.get("env") or {})
    env.setdefault("WORKSPACE_ROOT", str(Path.home() / "Documents"))
    env["AGENT_MCP_CONFIG"] = str(agent_root / "config" / "agent-mcp.json")
    env["SHARED_UNREAL_CONFIG"] = str(SHARED_CONFIG)
    env["UNREAL58_ROOT"] = str(workspace)
    env["PYTHON_EXE"] = str(python_exe)
    for key in (
        "MCP_ESSENTIAL_TOOLS",
        "MCP_REQUIRE_PLAN_AUTH",
        "MCP_AGENT_RESULT_MAX_CHARS",
        "BUILD_VERBOSE_OUTPUT",
        "VALIDATE_ON_WRITE",
        "VALIDATE_ON_WRITE_TIMEOUT_MS",
        "MCP_EXECUTION_MODE",
        "CONTROL_RUNTIME_MANIFEST",
        "CONTROL_RUNTIME_COMPONENT",
        "CONTROL_RUNTIME_REQUIRED",
        "CONTROL_RUNTIME_GIT_COMMIT",
        "CONTROL_RUNTIME_EXPECTED_GIT_COMMIT",
    ):
        env.pop(key, None)
    entry["env"] = env
    entry["timeout"] = DEFAULT_UNREAL_AGENT_MCP_TIMEOUT_MS
    return entry


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def prune_forbidden_tool_confirmation_patterns(settings: dict[str, Any]) -> list[str]:
    chat = settings.get("chat")
    if not isinstance(chat, dict):
        return []
    patterns = chat.get("skipToolConfirmationPatterns")
    if not isinstance(patterns, list):
        return []

    removed: list[str] = []
    kept: list[Any] = []
    for pattern in patterns:
        if isinstance(pattern, str) and pattern in FORBIDDEN_TOOL_CONFIRMATION_PATTERNS:
            removed.append(pattern)
            continue
        kept.append(pattern)
    chat["skipToolConfirmationPatterns"] = kept
    return removed


def patch_lmstudio_settings(settings_json: Path, dry_run: bool = False) -> list[str]:
    if not settings_json.is_file():
        return []
    settings = load_json(settings_json)
    removed = prune_forbidden_tool_confirmation_patterns(settings)
    if removed and not dry_run:
        save_json(settings_json, settings)
    return removed


def is_legacy_python_control_entry(name: str, entry: Any) -> bool:
    """Match the unsupported Python RAG controller without pruning custom MCPs."""

    if not isinstance(entry, dict):
        return False
    normalized_name = str(name or "").strip().casefold()
    if normalized_name == "unreal-rag-strict":
        return True
    args = entry.get("args")
    arguments = list(args) if isinstance(args, list) else []
    basenames = {
        str(value).replace("\\", "/").rsplit("/", 1)[-1].casefold()
        for value in arguments
    }
    if "unreal_rag_mcp.py" in basenames:
        return True
    env = entry.get("env")
    if not isinstance(env, dict):
        return False
    command_name = str(entry.get("command") or "").replace("\\", "/").rsplit("/", 1)[-1]
    python_entry = command_name.casefold() in {"py", "py.exe"} or command_name.casefold().startswith(
        "python"
    ) or any(str(value).casefold().endswith(".py") for value in arguments)
    if not python_entry:
        return False
    component = str(env.get("CONTROL_RUNTIME_COMPONENT") or "").strip().casefold()
    if component == "rag" and any(
        key in env for key in ("CONTROL_RUNTIME_MANIFEST", "CONTROL_RUNTIME_REQUIRED")
    ):
        return True
    strict = str(env.get("MCP_EXECUTION_MODE") or "").strip().casefold() == "strict"
    task_keys = {
        "MCP_ESSENTIAL_TOOLS",
        "MCP_EXTENDED_TOOLS",
        "ALLOW_CONTROL_PLANE_TOOLS",
        "MCP_REQUIRE_PLAN_AUTH",
        "CONTROL_RUNTIME_COMPONENT",
    }
    return strict and (
        normalized_name.startswith("unreal-rag") or any(key in env for key in task_keys)
    )


def remove_legacy_python_control_entries(config: dict[str, Any]) -> list[str]:
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        return []
    removed = [
        str(name)
        for name, entry in list(servers.items())
        if is_legacy_python_control_entry(str(name), entry)
    ]
    for name in removed:
        servers.pop(name, None)
    return removed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp-json", type=Path, default=DEFAULT_LMSTUDIO_ROOT / "mcp.json")
    parser.add_argument("--settings-json", type=Path, default=DEFAULT_LMSTUDIO_ROOT / "settings.json")
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
    config = load_json(args.mcp_json)
    servers = config.setdefault("mcpServers", {})
    existing_standard_rag = servers.get("unreal-rag") if isinstance(servers, dict) else None
    removed_legacy = remove_legacy_python_control_entries(config)
    if isinstance(existing_standard_rag, dict) and "unreal-rag" not in servers:
        # The standard key is replaced in place below; only duplicate/renamed
        # legacy entries disappear from the resulting configuration.
        servers["unreal-rag"] = existing_standard_rag
    mcp_remote_proxy = (
        resolve_mcp_remote_proxy()
        if any(entry_uses_mcp_remote(entry) for entry in servers.values())
        else None
    )

    for name, entry in list(servers.items()):
        servers[name] = patch_node_commands(entry, node_exe, mcp_remote_proxy)

    if "unreal-rag" in servers:
        servers["unreal-rag"] = patch_unreal_rag(servers["unreal-rag"], workspace, args.python)
    if "unreal-agent" in servers:
        servers["unreal-agent"] = patch_unreal_agent(
            servers["unreal-agent"], workspace, node_exe, args.python
        )

    if args.dry_run:
        print(json.dumps(config, ensure_ascii=False, indent=2))
        if removed_legacy:
            print(
                "Safety normalization would remove unsupported Python control MCP entries: "
                + ", ".join(removed_legacy),
                file=sys.stderr,
            )
        removed = patch_lmstudio_settings(args.settings_json, dry_run=True)
        if removed:
            print(
                "Would remove forbidden LM Studio tool auto-approval patterns: "
                + ", ".join(removed),
                file=sys.stderr,
            )
        return

    save_json(args.mcp_json, config)
    if removed_legacy:
        print(
            "Safety normalization removed unsupported Python control MCP entries: "
            + ", ".join(removed_legacy)
        )
    removed = patch_lmstudio_settings(args.settings_json)
    if removed:
        print(
            "Removed forbidden LM Studio tool auto-approval patterns: "
            + ", ".join(removed)
        )
    print(f"Patched {args.mcp_json}")


if __name__ == "__main__":
    main()
