from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

COMPACT_BASE = ROOT / "prompts" / "lmstudio_compact_mcp_base.md"
AGENT_SYSTEM = ROOT / "prompts" / "lmstudio_unreal_agent_system.md"
BOOTSTRAP = ROOT / "prompts" / "lmstudio_session_bootstrap.md"
TOOL_DISCIPLINE = ROOT / "docs" / "LMStudio_MCP_Tool_Discipline.md"
QWEN36_SYSTEM = ROOT / "prompts" / "lmstudio_qwen36_27b_compact_system.md"
ANTI_PATTERNS = ROOT / "RAG_Project_Guidelines" / "06_Unreal_AntiPatterns.md"
SUBSYSTEM_RECIPES = ROOT / "RAG_Project_Guidelines" / "Unreal_Programming" / "11_Prototype_Component_Subsystem_Recipes.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_compact_base_has_timeout_retry_ban_and_create_only() -> None:
    text = _read(COMPACT_BASE)
    lowered = text.lower()
    assert "create-only" in lowered
    assert "-32001" in text
    assert "never retry `write_file`" in lowered
    # Risk-triggered checkpoint wording (continue on success, stop on risk signals).
    assert "continue automatically" in lowered
    assert "model failed to generate a tool call" in lowered
    assert "progress summary" in lowered


def test_compact_base_has_no_stop_after_every_write_rule() -> None:
    lowered = _read(COMPACT_BASE).lower()
    forbidden = [
        "stop after every write",
        "stop after each write",
        "wait for the user after every file",
        "wait for user confirmation after each",
        "pause after every successful write",
    ]
    for phrase in forbidden:
        assert phrase not in lowered, phrase


def test_agent_system_has_flow_and_checkpoint_rules() -> None:
    lowered = _read(AGENT_SYSTEM).lower()
    assert "continue automatically" in lowered
    assert "-32001" in _read(AGENT_SYSTEM)
    assert "create-only" in lowered
    assert "fresh session" in lowered


def test_bootstrap_mentions_create_only_and_flow() -> None:
    lowered = _read(BOOTSTRAP).lower()
    assert "create-only" in lowered
    assert "continue automatically" in lowered


def test_tool_discipline_documents_write_safety_and_budget() -> None:
    lowered = _read(TOOL_DISCIPLINE).lower()
    assert "create-only" in lowered
    assert "-32001" in _read(TOOL_DISCIPLINE)
    assert "validate_on_write_timeout_ms" in lowered
    assert "rollback skipped" in lowered


def test_compact_base_has_loop_guard_and_uht_conditional_rules() -> None:
    text = _read(COMPACT_BASE)
    lowered = text.lower()
    assert "byte-identical" in lowered
    assert "identical ... call already attempted" in lowered
    assert "never re-edit a file without re-reading it first" in lowered
    assert "WITH_EDITORONLY_DATA" in text
    assert "#if !UE_BUILD_SHIPPING" in text or "UE_BUILD_SHIPPING" in text
    assert "GEngine->GetWorld()" in text
    assert "GEngine->GetGameInstance()" in text


def test_qwen36_prompt_has_reflection_and_loop_rules() -> None:
    text = _read(QWEN36_SYSTEM)
    assert "WITH_EDITORONLY_DATA" in text
    assert "GENERATED_BODY" in text
    assert "GEngine->GetWorld()" in text
    assert "byte-identical" in text.lower()


def test_tool_discipline_troubleshooting_covers_new_guards() -> None:
    text = _read(TOOL_DISCIPLINE)
    assert "identical ... call already attempted" in text
    assert "UHT_MACRO_IN_CONDITIONAL_BLOCK" in text
    assert "GENGINE_WORLD_CONTEXT" in text
    assert "Duplicate-call loop breaker" in text


def test_anti_patterns_doc_covers_uht_and_world_context() -> None:
    text = _read(ANTI_PATTERNS)
    assert "UHT_MACRO_IN_CONDITIONAL_BLOCK" in text
    assert "GENGINE_WORLD_CONTEXT" in text
    assert "STATIC_MUTABLE_CONTAINER_MEMBER" in text
    assert "UE_BUILD_SHIPPING" in text
    assert "GEngine->GetWorld()" in text


def test_subsystem_recipe_has_world_context_dispatcher_rules() -> None:
    text = _read(SUBSYSTEM_RECIPES)
    assert "GEngine->GetWorld()" in text
    assert "Deinitialize()" in text
    assert "TWeakObjectPtr" in text
    assert "static TMap" in text
