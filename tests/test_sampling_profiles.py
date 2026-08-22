#!/usr/bin/env python
"""Contracts for static, user-selected LM Studio recommendations."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import load_sampling_preset as sampling  # noqa: E402

SAMPLING_PATH = ROOT / "config" / "lmstudio_sampling.json"


def _config() -> dict:
    return json.loads(SAMPLING_PATH.read_text(encoding="utf-8-sig"))


def _profiles() -> dict:
    return _config()["profiles"]


def _walk_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        keys = {str(key) for key in value}
        for child in value.values():
            keys.update(_walk_keys(child))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for child in value:
            keys.update(_walk_keys(child))
        return keys
    return set()


@pytest.fixture(autouse=True)
def _reset_profile_state(monkeypatch):
    monkeypatch.delenv("UNREAL_RAG_MODEL_PROFILE", raising=False)
    sampling.set_sampling_profile("")
    yield
    sampling.set_sampling_profile("")


def test_model_alias_resolves_exact_and_embedded_model_ids():
    assert sampling.resolve_profile_name_for_model("gpt-oss-20b (LM Studio live)") == "gpt_oss_20b"
    assert (
        sampling.resolve_profile_name_for_model(
            "lmstudio-community/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf"
        )
        == "qwen3_8_27b"
    )
    assert (
        sampling.resolve_profile_name_for_model(
            "qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max"
        )
        == "qwen3_6_27b"
    )


def test_model_alias_does_not_override_explicit_environment_profile(monkeypatch):
    monkeypatch.setenv("UNREAL_RAG_MODEL_PROFILE", "qwen3_5_9b")

    assert sampling.set_sampling_profile_for_model("gpt-oss-20b") == ""
    assert sampling.resolve_profile_name() == "qwen3_5_9b"


def test_checked_in_schema_has_no_hidden_planner_or_phase_policy():
    config = _config()
    all_keys = _walk_keys(config)
    forbidden = {
        "modeMap",
        "turnPresets",
        "reasoningPolicy",
        "agentPolicy",
        "planningRequired",
        "mcpToolDiscipline",
        "strictRecommendedSystemPrompt",
        "promptContract",
        "targetTier",
        "assemblyBudgetScale",
        "compileFixMaxAttempts",
        "defaultTopK",
        "deltaTopK",
        "candidateLimitScale",
        "historyTurns",
        "twoPhase",
    }

    assert forbidden.isdisjoint(all_keys)
    assert "conservative_compile_fix" not in config["profiles"]
    assert "review_only" not in config["profiles"]


def test_profiles_are_only_static_load_chat_and_bounded_safety_metadata():
    allowed_profile_keys = {
        "contextLength",
        "contextLengthAlternatives",
        "quantDefault",
        "recommendedParallelRequests",
        "recommendedSystemPrompt",
        "sampling",
        "writeSafety",
        "notes",
    }
    allowed_sampling_keys = {"temperature", "topP", "topK", "minP", "maxTokens"}
    allowed_safety_keys = {"maxFilesPerEdit", "preferPatchOverFullFile"}

    for name, profile in _profiles().items():
        assert set(profile) <= allowed_profile_keys, name
        assert set(profile["sampling"]) <= allowed_sampling_keys, name
        assert set(profile["writeSafety"]) == allowed_safety_keys, name
        assert profile["writeSafety"]["maxFilesPerEdit"] <= 2, name
        assert profile["recommendedParallelRequests"] >= 1, name


def test_all_profiles_use_the_direct_model_authority_prompt():
    expected = "prompts/lmstudio_direct_model_system.md"
    for name, profile in _profiles().items():
        assert profile["recommendedSystemPrompt"] == expected, name

    direct_prompt = (ROOT / expected).read_text(encoding="utf-8")
    assert "You own the reasoning" in direct_prompt
    assert "choice and order" in direct_prompt
    assert "decision to stop" in direct_prompt
    assert "final answer" in direct_prompt


def test_mode_turn_and_wrapper_retry_flags_are_true_no_ops():
    base = sampling.load_sampling_preset(profile="qwen3_8_27b")

    assert sampling.load_sampling_preset(
        mode="refactor_r0",
        turn="plan",
        profile="qwen3_8_27b",
    ) == base
    assert sampling.load_sampling_preset(
        mode="compile_fix",
        turn="compile_fix_patch",
        profile="qwen3_8_27b",
    ) == base

    sampling.set_sampling_profile("qwen3_8_27b")
    assert sampling.preset_for_wrapper("agent_edit") == base
    assert sampling.preset_for_wrapper("compile_fix", compile_patch=True) == base


def test_cli_mode_and_turn_are_deprecated_no_ops():
    script = str(ROOT / "scripts" / "load_sampling_preset.py")
    base = subprocess.run(
        [sys.executable, script, "--sampling-profile", "qwen3_6_27b"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    staged = subprocess.run(
        [
            sys.executable,
            script,
            "--sampling-profile",
            "qwen3_6_27b",
            "--mode",
            "compile_fix",
            "--turn",
            "plan",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert base.returncode == staged.returncode == 0
    assert json.loads(base.stdout) == json.loads(staged.stdout)
    assert "deprecated no-ops" in staged.stderr


def test_show_profile_exposes_only_recommendations_and_bounded_safety():
    script = str(ROOT / "scripts" / "load_sampling_preset.py")
    proc = subprocess.run(
        [
            sys.executable,
            script,
            "--model",
            "qwen/qwen3.8-27b",
            "--show-profile",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    shown = json.loads(proc.stdout)
    assert shown["profile"] == "qwen3_8_27b"
    assert shown["contextLength"] == 65536
    assert shown["quantDefault"] == "Q4_K_M"
    assert shown["recommendedParallelRequests"] == 1
    assert shown["recommendedSystemPrompt"] == "prompts/lmstudio_direct_model_system.md"
    assert shown["maxFilesPerEdit"] == 2
    assert "planningRequired" not in shown
    assert "reasoningPolicy" not in shown


def test_legacy_policy_accessor_is_a_small_compatibility_view():
    policy = sampling.profile_agent_policy("qwen3_5_9b")

    assert set(policy) == {
        "contextLength",
        "contextLengthAlternatives",
        "quantDefault",
        "recommendedParallelRequests",
        "recommendedSystemPrompt",
        "sampling",
        "maxFilesPerEdit",
        "preferPatchOverFullFile",
    }
    assert policy["contextLength"] == 24576
    assert policy["maxFilesPerEdit"] == 2


def test_long_context_profile_keeps_portable_and_native_load_choices():
    recommendation = sampling.profile_recommendation(
        "qwen3_5_9b_deepseek_v4_flash"
    )

    assert recommendation["contextLength"] == 140032
    assert recommendation["contextLengthAlternatives"] == [65536, 262144]
    assert recommendation["recommendedParallelRequests"] == 1


def test_all_profiles_keep_minimum_context_and_known_quantization():
    for name, profile in _profiles().items():
        assert profile["contextLength"] >= 24576, name
        assert profile["quantDefault"] in {"Q4_K_M", "Q5_K_M"}, name


def test_primary_setup_never_recommends_a_historical_controller_prompt():
    setup = (ROOT / "docs" / "LMStudio_Unreal_Agent_Setup.md").read_text(encoding="utf-8")
    assert "lmstudio_direct_model_system.md" in setup
    for obsolete in (
        "lmstudio_compact_mcp_base.md",
        "lmstudio_qwen35_9b_compact_system.md",
        "lmstudio_qwen36_27b_compact_system.md",
        "lmstudio_gpt_oss_compact_system.md",
    ):
        assert obsolete not in setup
