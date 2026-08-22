#!/usr/bin/env python
"""Tests for LM Studio MCP bench safeguards."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parents[1]
SCRIPTS = WORKSPACE / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bench_lmstudio_mcp as bench  # noqa: E402


def test_default_kpi_is_not_written_for_embedding_model():
    assert bench.should_write_output(
        bench.DEFAULT_BASELINE,
        "text-embedding-nomic-embed-text-v1.5",
    ) is False


def test_explicit_output_can_record_no_chat_diagnostic(tmp_path):
    out = tmp_path / "diagnostic.json"

    assert bench.should_write_output(out, "text-embedding-nomic-embed-text-v1.5") is True


def _tool_names() -> list[str]:
    return [row["function"]["name"] for row in bench.probe_tools_schema()]


def test_default_benchmark_is_direct_only():
    assert [row["expect_tool"] for row in bench.SCENARIOS] == [
        "unreal_get_active_project",
        "unreal_rag_search",
        "read_file",
    ]
    assert "unreal_agent_plan" not in _tool_names()
    assert all(
        row["expect_tool"] != "unreal_agent_plan"
        for row in bench.benchmark_scenarios()
    )


def test_removed_strict_flag_is_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "argv", ["bench_lmstudio_mcp.py", "--strict"])

    with pytest.raises(SystemExit) as raised:
        bench.main()

    assert raised.value.code == 2


def test_run_scenario_passes_only_direct_schema(monkeypatch: pytest.MonkeyPatch):
    observed: list[list[str]] = []

    def fake_completion(_base_url, _model, _messages, *, tools, timeout=120.0):
        del timeout
        names = [row["function"]["name"] for row in tools]
        observed.append(names)
        return {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {"function": {"name": "read_file", "arguments": "{}"}}
                        ]
                    }
                }
            ]
        }

    monkeypatch.setattr(bench, "chat_completion", fake_completion)

    direct = bench.run_scenario(
        "http://localhost:1234/v1",
        "chat-model",
        {"id": "direct", "user": "read", "expect_tool": "read_file"},
    )
    assert direct["pass"] is True
    assert "unreal_agent_plan" not in observed[0]
    assert observed == [["unreal_get_active_project", "unreal_rag_search", "read_file"]]


def test_reasoning_channel_is_not_mislabeled_as_visible_tool_call_content(
    monkeypatch: pytest.MonkeyPatch,
):
    def fake_completion(_base_url, _model, _messages, *, tools, timeout=120.0):
        del tools, timeout
        return {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "reasoning_content": "I should call the requested tool.",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "unreal_get_active_project",
                                    "arguments": "{}",
                                }
                            }
                        ],
                    }
                }
            ]
        }

    monkeypatch.setattr(bench, "chat_completion", fake_completion)
    result = bench.run_scenario(
        "http://localhost:1234/v1",
        "chat-model",
        {
            "id": "reasoning-channel",
            "user": "find project",
            "expect_tool": "unreal_get_active_project",
        },
    )

    assert result["pass"] is True
    assert result["reasoningContentPresent"] is True
    assert result["contentWithToolCall"] is False
    assert result["thinkingLeak"] is False
