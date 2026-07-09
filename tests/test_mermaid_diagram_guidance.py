#!/usr/bin/env python
"""Tests that diagram guidance stays Mermaid-first and MCP-safe."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_prompt_guidance_requires_mermaid_before_ascii_fallback() -> None:
    compact = _read("prompts/lmstudio_compact_mcp_base.md")
    system = _read("prompts/lmstudio_unreal_agent_system.md")
    assistant = _read("prompts/unreal_cpp_assistant.md")

    assert "Mermaid code fence first" in compact
    assert "Immediately after the Mermaid block" in compact
    assert "show a compact Mermaid diagram first" in system
    assert "Mermaid diagram first and a plain ASCII/text fallback second" in assistant


def test_docs_keep_mermaid_first_without_removing_fallback() -> None:
    rules = _read("RAG_Project_Guidelines/Core_Architecture/06_Diagram_Response_Rules.md")
    discipline = _read("docs/LMStudio_MCP_Tool_Discipline.md")

    assert "A Mermaid diagram first" in rules
    assert "plain ASCII/text fallback second" in rules
    assert "show Mermaid first" in discipline
    assert "ASCII/text only after the Mermaid block" in discipline


def test_unreal_render_report_mentions_mermaid_validation_without_new_tool() -> None:
    mcp = _read("scripts/unreal_rag_mcp.py")

    assert '"unreal_render_report"' in mcp
    assert "Mermaid fences are validated when present" in mcp
    assert "unreal_validate_mermaid" not in mcp
