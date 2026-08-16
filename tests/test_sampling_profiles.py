#!/usr/bin/env python
"""Tests for LM Studio sampling profile resolution."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import load_sampling_preset as sampling  # noqa: E402
import query_rag  # noqa: E402

SAMPLING_PATH = ROOT / "config" / "lmstudio_sampling.json"


def _profiles() -> dict:
    return json.loads(SAMPLING_PATH.read_text(encoding="utf-8-sig"))["profiles"]


def test_model_alias_resolves_gpt_oss_profile(monkeypatch):
    monkeypatch.delenv("UNREAL_RAG_MODEL_PROFILE", raising=False)
    sampling.set_sampling_profile("")

    assert sampling.resolve_profile_name_for_model("gpt-oss-20b (LM Studio live)") == "gpt_oss_20b"


def test_model_alias_resolves_qwen36_heretic_profile(monkeypatch):
    monkeypatch.delenv("UNREAL_RAG_MODEL_PROFILE", raising=False)
    sampling.set_sampling_profile("")

    assert (
        sampling.resolve_profile_name_for_model(
            "qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max (LM Studio)"
        )
        == "qwen3_6_27b"
    )


def test_model_alias_resolves_qwen36_lmstudio_gguf_profile(monkeypatch):
    monkeypatch.delenv("UNREAL_RAG_MODEL_PROFILE", raising=False)
    sampling.set_sampling_profile("")

    assert (
        sampling.resolve_profile_name_for_model(
            "lmstudio-community/Qwen3.6-27B-GGUF/Qwen3.6-27B-Q4_K_M.gguf"
        )
        == "qwen3_6_27b"
    )


def test_model_alias_resolves_qwen38_27b_without_family_fallback(monkeypatch):
    monkeypatch.delenv("UNREAL_RAG_MODEL_PROFILE", raising=False)
    sampling.set_sampling_profile("")

    assert (
        sampling.resolve_profile_name_for_model(
            "lmstudio-community/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf"
        )
        == "qwen3_8_27b"
    )


def test_qwen38_profile_has_explicit_server_reasoning_policy():
    limits = sampling.profile_edit_limits("qwen3_8_27b")
    policy = limits["reasoningPolicy"]
    plan = sampling.load_sampling_preset(turn="plan", profile="qwen3_8_27b")

    assert policy["owner"] == "server"
    assert policy["transport"] == "generator_raw_kv_config"
    assert policy["capabilityStatus"] == "model_profile_bound"
    assert policy["fieldKey"] == (
        "ext.virtualModel.customField.qwen.qwen3.827b.reasoningEffort"
    )
    assert policy["failClosedIfUnpinned"] is True
    assert policy["supportedEfforts"] == ["low", "medium", "extra_high"]
    assert limits["contextLength"] == 65536
    assert plan["reasoningEffort"] == "low"
    assert plan["topK"] == 20
    assert plan["minP"] == 0


def test_sampling_cli_can_resolve_profile_from_model_alias():
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "load_sampling_preset.py"),
            "--model",
            "qwen3.5-9b",
            "--show-profile",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["profile"] == "qwen3_5_9b"


def test_qwen36_profile_mcp_meta(monkeypatch):
    monkeypatch.delenv("UNREAL_RAG_MODEL_PROFILE", raising=False)
    limits = sampling.profile_edit_limits("qwen3_6_27b")

    assert limits["maxFilesPerEdit"] == 2
    assert limits["mcpEssentialTools"] is True
    assert "lmstudio_qwen36" in limits["recommendedSystemPrompt"]
    assert limits["contextLength"] == 32768
    assert limits["preferSymbolLookupOverFileRead"] is True
    assert limits["enforceRangeRead"] is True
    assert limits["rangeReadContextLines"] == 40
    assert limits["patchChangedLineLimit"] == 60
    assert limits["noOpGuard"] is True
    assert limits["defaultTopK"] == 6
    assert limits["deltaTopK"] == 3
    assert limits["candidateLimitScale"] == 16


def test_qwen36_plan_turn_max_tokens_is_3072(monkeypatch):
    monkeypatch.delenv("UNREAL_RAG_MODEL_PROFILE", raising=False)
    sampling.set_sampling_profile("qwen3_6_27b")
    preset = sampling.load_sampling_preset(mode="refactor_r0")
    assert preset["maxTokens"] == 3072


def test_module_fix_mode_uses_compile_fix_patch_preset(monkeypatch):
    monkeypatch.delenv("UNREAL_RAG_MODEL_PROFILE", raising=False)
    sampling.set_sampling_profile("qwen3_6_27b")

    preset = sampling.load_sampling_preset(mode="module_fix")

    assert preset["thinking"] == "off"
    assert preset["temperature"] == 0.07
    assert preset["topP"] == 0.82


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


def test_qwen35_flash_profile_uses_verified_long_context():
    profile = _profiles()["qwen3_5_9b_deepseek_v4_flash"]
    limits = sampling.profile_edit_limits("qwen3_5_9b_deepseek_v4_flash")

    assert profile["contextLength"] == 140032
    assert profile["contextLengthVariant_native_max"] == 262144
    assert limits["contextLength"] == 140032
    assert limits["recommendedParallelRequests"] == 1


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


def test_query_rag_ask_lmstudio_selects_loaded_model_profile(monkeypatch):
    monkeypatch.delenv("UNREAL_RAG_MODEL_PROFILE", raising=False)
    sampling.set_sampling_profile("")
    args = type(
        "Args",
        (),
        {
            "ask_lmstudio": True,
            "sampling_profile": "",
            "model": "",
        },
    )()
    monkeypatch.setattr(query_rag, "resolve_model", lambda _args: "qwen3.5-9b-deepseek-v4-flash")

    selected = query_rag.apply_model_profile_from_args(args)

    assert selected == "qwen3_5_9b_deepseek_v4_flash"
    assert args.model == "qwen3.5-9b-deepseek-v4-flash"
    assert sampling.resolve_profile_name() == "qwen3_5_9b_deepseek_v4_flash"
