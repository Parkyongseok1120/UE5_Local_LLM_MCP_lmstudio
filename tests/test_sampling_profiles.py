#!/usr/bin/env python
"""Tests for LM Studio sampling profile resolution."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import load_sampling_preset as sampling  # noqa: E402

SAMPLING_PATH = ROOT / "config" / "lmstudio_sampling.json"


def _profiles() -> dict:
    return json.loads(SAMPLING_PATH.read_text(encoding="utf-8-sig"))["profiles"]


def test_model_alias_resolves_gpt_oss_profile(monkeypatch):
    monkeypatch.delenv("UNREAL_RAG_MODEL_PROFILE", raising=False)
    sampling.set_sampling_profile("")

    assert sampling.resolve_profile_name_for_model("gpt-oss-20b (LM Studio live)") == "gpt_oss_20b"


def test_model_alias_resolves_gemma_4_profile(monkeypatch):
    monkeypatch.delenv("UNREAL_RAG_MODEL_PROFILE", raising=False)
    sampling.set_sampling_profile("")

    assert (
        sampling.resolve_profile_name_for_model("gemma-4-26B-A4B-it-Q4_K_M (LM Studio)")
        == "gemma_4_26b_a4b_it_q4_k_m"
    )


def test_model_alias_resolves_gemma4_v2_agentic(monkeypatch):
    monkeypatch.delenv("UNREAL_RAG_MODEL_PROFILE", raising=False)
    sampling.set_sampling_profile("")

    assert (
        sampling.resolve_profile_name_for_model("gemma4-v2-Q6_K (LM Studio)")
        == "gemma4_12b_v2_agentic"
    )


def test_gemma_v2_plan_preset_thinking_on(monkeypatch):
    monkeypatch.delenv("UNREAL_RAG_MODEL_PROFILE", raising=False)
    sampling.set_sampling_profile("")

    preset = sampling.load_sampling_preset(turn="plan", profile="gemma4_12b_v2_agentic")

    assert preset["thinking"] == "on"
    assert preset["temperature"] == 1.0


def test_gemma_v2_execute_preset_thinking_off(monkeypatch):
    monkeypatch.delenv("UNREAL_RAG_MODEL_PROFILE", raising=False)
    sampling.set_sampling_profile("")

    preset = sampling.load_sampling_preset(turn="execute", profile="gemma4_12b_v2_agentic")

    assert preset["thinking"] == "off"
    assert preset["temperature"] == 0.1


def test_gemma_profile_execute_preset_thinking_off(monkeypatch):
    monkeypatch.delenv("UNREAL_RAG_MODEL_PROFILE", raising=False)
    sampling.set_sampling_profile("")

    preset = sampling.load_sampling_preset(
        turn="execute", profile="gemma_4_26b_a4b_it_q4_k_m"
    )

    assert preset["thinking"] == "off"
    assert preset["temperature"] == 0.1


def test_gemma_26b_plan_preset_thinking_on(monkeypatch):
    monkeypatch.delenv("UNREAL_RAG_MODEL_PROFILE", raising=False)
    preset = sampling.load_sampling_preset(turn="plan", profile="gemma_4_26b_a4b_it_q4_k_m")

    assert preset["thinking"] == "on"
    assert preset["temperature"] == 1.0


def test_gemma_profile_edit_limits(monkeypatch):
    monkeypatch.delenv("UNREAL_RAG_MODEL_PROFILE", raising=False)
    limits = sampling.profile_edit_limits("gemma_4_26b_a4b_it_q4_k_m")

    assert limits["maxFilesPerEdit"] == 2
    assert limits["allowRefactorModes"] is False
    assert limits["promptContract"] == "gemma4_compact_mcp_thinking_hybrid"
    assert limits["mcpEssentialTools"] is True
    assert "lmstudio_gemma4" in limits["recommendedSystemPrompt"]


def test_gpt_oss_compile_fix_analyze_preset_is_low_temperature(monkeypatch):
    monkeypatch.delenv("UNREAL_RAG_MODEL_PROFILE", raising=False)
    sampling.set_sampling_profile("")
    sampling.set_sampling_profile_for_model("gpt-oss-20b")

    preset = sampling.load_sampling_preset(mode="compile_fix")
    limits = sampling.profile_edit_limits("gpt_oss_20b")

    assert preset["temperature"] == 0.08
    assert preset["maxTokens"] == 2560
    assert limits["maxFilesPerEdit"] == 2
    assert limits["compileFixMaxAttempts"] == 3
    assert limits["contextLength"] == 32768


def test_all_profiles_context_at_least_24576():
    for name, profile in _profiles().items():
        assert profile["contextLength"] >= 24576, f"{name} context below 24576"


def test_gpt_oss_profiles_context_32768():
    profiles = _profiles()
    for name in ("gpt_oss_20b", "gpt_oss_small", "gpt_oss_120b", "gpt_oss_20b_claude_opus_sonnet_reasoning_i1"):
        assert profiles[name]["contextLength"] == 32768


def test_model_alias_does_not_override_env_profile(monkeypatch):
    monkeypatch.setenv("UNREAL_RAG_MODEL_PROFILE", "conservative_compile_fix")
    sampling.set_sampling_profile("")

    selected = sampling.set_sampling_profile_for_model("gpt-oss-20b")

    assert selected == ""
    assert sampling.resolve_profile_name() == "conservative_compile_fix"
