import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "patch_mcp_config.py"


def load_module():
    spec = importlib.util.spec_from_file_location("patch_mcp_config", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_prune_forbidden_js_sandbox_auto_approval_patterns() -> None:
    mod = load_module()
    settings = {
        "chat": {
            "skipToolConfirmationPatterns": [
                "mcp/unreal-agent:*",
                "lmstudio/js-code-sandbox:run_javascript",
                "lmstudio/js-code-sandbox:*",
                "mcp/unreal-rag:*",
            ]
        }
    }

    removed = mod.prune_forbidden_tool_confirmation_patterns(settings)

    assert removed == [
        "lmstudio/js-code-sandbox:run_javascript",
        "lmstudio/js-code-sandbox:*",
    ]
    assert settings["chat"]["skipToolConfirmationPatterns"] == [
        "mcp/unreal-agent:*",
        "mcp/unreal-rag:*",
    ]


def test_prune_forbidden_patterns_is_noop_without_chat_settings() -> None:
    mod = load_module()
    settings = {"language": "en"}

    assert mod.prune_forbidden_tool_confirmation_patterns(settings) == []
    assert settings == {"language": "en"}


def test_patch_node_commands_does_not_require_mcp_remote_for_local_node_entry(tmp_path) -> None:
    mod = load_module()
    node = tmp_path / "node.exe"
    entry = {"command": "node", "args": ["local-server.js"]}

    patched = mod.patch_node_commands(entry, node, None)

    assert patched["command"] == str(node)
    assert patched["args"] == ["local-server.js"]


def test_find_workspace_root_prefers_script_repository() -> None:
    mod = load_module()

    assert mod.find_workspace_root() == ROOT


def test_patch_unreal_rag_sets_long_tool_timeout(tmp_path) -> None:
    mod = load_module()
    python = tmp_path / "python.exe"
    python.write_text("", encoding="utf-8")
    workspace = ROOT
    entry = {"command": "python", "args": [], "env": {}}

    patched = mod.patch_unreal_rag(entry, workspace, python)

    assert patched["timeout"] == mod.DEFAULT_UNREAL_RAG_MCP_TIMEOUT_MS


def test_patch_unreal_rag_overwrites_existing_timeout(tmp_path) -> None:
    mod = load_module()
    python = tmp_path / "python.exe"
    python.write_text("", encoding="utf-8")
    entry = {"command": "python", "args": [], "env": {}, "timeout": 900_000}

    patched = mod.patch_unreal_rag(entry, ROOT, python)

    assert patched["timeout"] == mod.DEFAULT_UNREAL_RAG_MCP_TIMEOUT_MS
