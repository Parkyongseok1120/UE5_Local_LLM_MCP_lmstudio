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


def test_prune_forbidden_mutating_mcp_and_sandbox_auto_approval_patterns() -> None:
    mod = load_module()
    settings = {
        "chat": {
            "skipToolConfirmationPatterns": [
                "mcp/unreal-agent:*",
                "lmstudio/js-code-sandbox:run_javascript",
                "lmstudio/js-code-sandbox:*",
                "mcp/unreal-rag:*",
                "mcp/unreal-rag:unreal_architecture_reasoning",
            ]
        }
    }

    removed = mod.prune_forbidden_tool_confirmation_patterns(settings)

    assert removed == [
        "mcp/unreal-agent:*",
        "lmstudio/js-code-sandbox:run_javascript",
        "lmstudio/js-code-sandbox:*",
        "mcp/unreal-rag:*",
        "mcp/unreal-rag:unreal_architecture_reasoning",
    ]
    assert settings["chat"]["skipToolConfirmationPatterns"] == []


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
    assert patched["args"] == [str(workspace / "scripts" / "unreal_rag_direct.py")]


def test_patch_unreal_rag_overwrites_existing_timeout(tmp_path) -> None:
    mod = load_module()
    python = tmp_path / "python.exe"
    python.write_text("", encoding="utf-8")
    entry = {"command": "python", "args": [], "env": {}, "timeout": 900_000}

    patched = mod.patch_unreal_rag(entry, ROOT, python)

    assert patched["timeout"] == mod.DEFAULT_UNREAL_RAG_MCP_TIMEOUT_MS


def test_patch_unreal_rag_removes_old_proxy_authority_keys(tmp_path) -> None:
    mod = load_module()
    python = tmp_path / "python.exe"
    python.write_text("", encoding="utf-8")
    entry = {
        "command": "python",
        "args": [],
        "env": {
            "MCP_REQUIRE_CONTEXT_COMPACTOR_ACTIVE": "1",
            "MCP_EXECUTION_MODE": "strict",
            "MCP_EXTENDED_TOOLS": "1",
            "CONTROL_RUNTIME_REQUIRED": "1",
            "CONTROL_RUNTIME_MANIFEST": "legacy.json",
        },
    }

    patched = mod.patch_unreal_rag(
        entry,
        ROOT,
        python,
        context_compactor_advisory=True,
    )

    assert patched["env"]["MCP_FRONTEND"] == "lmstudio"
    assert "MCP_EXECUTION_MODE" not in patched["env"]
    assert not any(key.startswith("MCP_CONTEXT_COMPACTOR_") for key in patched["env"])
    assert "MCP_REQUIRE_CONTEXT_COMPACTOR_ACTIVE" not in patched["env"]
    assert "MCP_EXTENDED_TOOLS" not in patched["env"]
    assert "CONTROL_RUNTIME_REQUIRED" not in patched["env"]
    assert "CONTROL_RUNTIME_MANIFEST" not in patched["env"]


def test_patch_unreal_rag_clears_old_proxy_keys_regardless_of_install_detection(tmp_path) -> None:
    mod = load_module()
    python = tmp_path / "python.exe"
    python.write_text("", encoding="utf-8")
    entry = {
        "command": "python",
        "args": [],
        "env": {
            "MCP_REQUIRE_CONTEXT_COMPACTOR_ACTIVE": "1",
            "MCP_CONTEXT_COMPACTOR_ADVISORY": "1",
            "MCP_CONTEXT_COMPACTOR_MAX_AGE_SECONDS": "999",
        },
    }

    patched = mod.patch_unreal_rag(
        entry,
        ROOT,
        python,
        context_compactor_advisory=False,
    )

    assert "MCP_REQUIRE_CONTEXT_COMPACTOR_ACTIVE" not in patched["env"]
    assert "MCP_CONTEXT_COMPACTOR_REQUIRED_FRONTENDS" not in patched["env"]
    assert "MCP_CONTEXT_COMPACTOR_ADVISORY" not in patched["env"]
    assert "MCP_CONTEXT_COMPACTOR_MAX_AGE_SECONDS" not in patched["env"]


def test_patch_unreal_agent_selects_direct_entry_and_removes_workflow_gates(tmp_path) -> None:
    mod = load_module()
    node = tmp_path / "node.exe"
    node.write_text("", encoding="utf-8")
    entry = {"command": "node", "args": [], "env": {"ALLOW_WRITE": "1"}}

    python = tmp_path / "python3"
    python.write_text("", encoding="utf-8")
    patched = mod.patch_unreal_agent(entry, ROOT, node, python)

    assert patched["timeout"] == mod.DEFAULT_UNREAL_AGENT_MCP_TIMEOUT_MS
    assert patched["args"] == [str(ROOT / "lmstudio-unreal-agent-mcp" / "src" / "direct-server.js")]
    assert "MCP_EXECUTION_MODE" not in patched["env"]
    for key in ("MCP_REQUIRE_PLAN_AUTH", "VALIDATE_ON_WRITE", "VALIDATE_ON_WRITE_TIMEOUT_MS", "MCP_AGENT_RESULT_MAX_CHARS", "BUILD_VERBOSE_OUTPUT"):
        assert key not in patched["env"]
    assert patched["env"]["PYTHON_EXE"] == str(python)


def test_patch_unreal_agent_removes_existing_proxy_control_values(tmp_path) -> None:
    mod = load_module()
    node = tmp_path / "node.exe"
    node.write_text("", encoding="utf-8")
    entry = {
        "command": "node",
        "args": [],
        "env": {
            "MCP_AGENT_RESULT_MAX_CHARS": "12000",
            "BUILD_VERBOSE_OUTPUT": "1",
        },
    }

    python = tmp_path / "python3"
    python.write_text("", encoding="utf-8")
    patched = mod.patch_unreal_agent(entry, ROOT, node, python)

    assert "MCP_AGENT_RESULT_MAX_CHARS" not in patched["env"]
    assert "BUILD_VERBOSE_OUTPUT" not in patched["env"]


def test_upgrade_cleanup_removes_only_legacy_python_control_entries() -> None:
    mod = load_module()
    config = {
        "mcpServers": {
            "unreal-rag-strict": {
                "command": "python",
                "args": ["renamed.py"],
                "env": {"MCP_EXECUTION_MODE": "strict"},
            },
            "copied-rag": {
                "command": "C:/Python/python.exe",
                "args": [r"C:\old\scripts\unreal_rag_mcp.py"],
            },
            "renamed-control": {
                "command": "python3",
                "args": ["custom_name.py"],
                "env": {
                    "CONTROL_RUNTIME_COMPONENT": "rag",
                    "CONTROL_RUNTIME_MANIFEST": "control-runtime.json",
                },
            },
            "keep-python-strict": {
                "command": "python",
                "args": ["unrelated.py"],
                "env": {"MCP_EXECUTION_MODE": "strict"},
            },
            "keep-node-custom": {
                "command": "node",
                "args": ["custom.js"],
                "env": {
                    "CONTROL_RUNTIME_COMPONENT": "rag",
                    "CONTROL_RUNTIME_MANIFEST": "custom.json",
                },
            },
            "keep-unrelated": {"command": "example", "args": []},
        }
    }

    removed = mod.remove_legacy_python_control_entries(config)

    assert removed == ["unreal-rag-strict", "copied-rag", "renamed-control"]
    assert set(config["mcpServers"]) == {
        "keep-python-strict",
        "keep-node-custom",
        "keep-unrelated",
    }
