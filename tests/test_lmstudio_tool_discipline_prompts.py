from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_chat_bootstrap_forbids_js_sandbox_file_io() -> None:
    text = read_text("prompts/lmstudio_session_bootstrap.md")

    assert "run_javascript" in text
    assert "js-code-sandbox" in text
    assert "Deno.readTextFile" in text
    assert "replace_in_file" in text


def test_chat_docs_do_not_recommend_write_file_for_existing_sources() -> None:
    discipline = read_text("docs/LMStudio_MCP_Tool_Discipline.md")
    setup = read_text("docs/LMStudio_Unreal_Agent_Setup.md")

    assert "Use `write_file` only for brand-new files" in discipline
    assert "Existing source files are patch-only" in setup
    assert "RAG search -> read_file -> write_file" not in setup


def test_user_prompts_forbid_js_sandbox_edits() -> None:
    edit = read_text("prompts/lmstudio_user_agent_edit.md")
    compile_fix = read_text("prompts/lmstudio_user_compile_fix.md")

    for text in (edit, compile_fix):
        assert "run_javascript" in text
        assert "js-code-sandbox" in text
        assert "replace_in_file" in text


def test_qwen35_prompt_has_plan_only_first_tool_gate() -> None:
    text = read_text("prompts/lmstudio_qwen35_9b_compact_system.md")

    assert "Plan-only hard gate" in text
    assert "first visible action" in text
    assert "writeGate.writesAllowed=false" in text


def test_qwen35_prompt_requires_filename_aware_component_discovery() -> None:
    text = read_text("prompts/lmstudio_qwen35_9b_compact_system.md")

    assert "Project component discovery hard gate" in text
    assert 'matchFileNames=true' in text
    assert 'path="project://Source"' in text
    assert "fileNameResults=[]" in text
    assert "RAG misses are not proof of absence" in text


def test_qwen36_prompt_requires_gate_completion_auth_refresh() -> None:
    text = read_text("prompts/lmstudio_qwen36_27b_compact_system.md")

    assert "gateCompletion.taskAuthorization" in text
    assert "TASK_ROUTE_STALE" in text
    assert "changeKind=new_file" in text


def test_base_prompt_documents_seven_field_auth_refresh() -> None:
    text = read_text("prompts/lmstudio_compact_mcp_base.md")

    assert "routeHash" in text
    assert "routePhase" in text
    assert "gateCompletion.taskAuthorization" in text
    assert "TASK_ROUTE_STALE" in text


def test_base_prompt_documents_sketch_slice_and_checkpoint_gate_rules() -> None:
    text = read_text("prompts/lmstudio_compact_mcp_base.md")
    assert "active-slice draft" in text
    assert "12k characters" in text
    assert "architectureSymbols" in text
    assert "REPLAN_BUDGET_EXHAUSTED" in text
    assert "cannot complete `requiredBeforeWrite` gates" in text
    assert "GATE_VALIDATION_FAILED" in text


def test_qwen35_prompt_uses_seven_field_auth_refresh() -> None:
    text = read_text("prompts/lmstudio_qwen35_9b_compact_system.md")

    assert "seven" in text.lower()
    assert "gateCompletion.taskAuthorization" in text
    assert "TASK_ROUTE_STALE" in text
    assert "five fields" not in text


def test_lmstudio_setup_requires_base_plus_qwen35_delta() -> None:
    text = read_text("docs/LMStudio_Unreal_Agent_Setup.md")

    qwen_row = next(line for line in text.splitlines() if "Qwen 3.5 9B / 8B" in line)
    assert "lmstudio_compact_mcp_base.md" in qwen_row
    assert "lmstudio_qwen35_9b_compact_system.md" in qwen_row
    assert "does not load the linked file" in text
