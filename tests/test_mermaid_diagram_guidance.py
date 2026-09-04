#!/usr/bin/env python
"""Tests that diagram guidance stays Mermaid-first and MCP-safe."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_prompt_guidance_requires_mermaid_before_ascii_fallback() -> None:
    for relative in (
        "prompts/lmstudio_direct_model_system.md",
        "prompts/cline_unreal_agent_system.md",
    ):
        prompt = _read(relative)
        assert "Mermaid를 먼저" in prompt
        assert "ASCII 텍스트 도식은 그 다음에" in prompt


def test_docs_keep_mermaid_first_without_removing_fallback() -> None:
    rules = _read("RAG_Project_Guidelines/Core_Architecture/06_Diagram_Response_Rules.md")
    discipline = _read("docs/LMStudio_MCP_Tool_Discipline.md")

    assert "Mermaid를 먼저" in rules
    assert "ASCII 텍스트 도식은 Mermaid 블록 다음에" in rules
    assert "Mermaid를 먼저" in discipline
    assert "ASCII 텍스트 도식은 Mermaid 블록 다음에" in discipline


def test_direct_catalog_does_not_add_a_server_owned_rendering_workflow() -> None:
    manifest = _read("config/stable_tool_manifest.json")

    assert "unreal_render_report" not in manifest
    assert "unreal_validate_mermaid" not in manifest
