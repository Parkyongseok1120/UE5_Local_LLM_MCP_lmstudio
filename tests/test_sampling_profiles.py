#!/usr/bin/env python
"""Tests for LM Studio sampling profile resolution."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import load_sampling_preset as sampling  # noqa: E402


def test_model_alias_resolves_gpt_oss_profile(monkeypatch):
    monkeypatch.delenv("UNREAL_RAG_MODEL_PROFILE", raising=False)
    sampling.set_sampling_profile("")

    assert sampling.resolve_profile_name_for_model("gpt-oss-20b (LM Studio live)") == "gpt_oss_20b"


def test_gpt_oss_compile_fix_analyze_preset_is_low_temperature(monkeypatch):
    monkeypatch.delenv("UNREAL_RAG_MODEL_PROFILE", raising=False)
    sampling.set_sampling_profile("")
    sampling.set_sampling_profile_for_model("gpt-oss-20b")

    preset = sampling.load_sampling_preset(mode="compile_fix")

    assert preset["temperature"] == 0.2
    assert preset["maxTokens"] == 2048


def test_model_alias_does_not_override_env_profile(monkeypatch):
    monkeypatch.setenv("UNREAL_RAG_MODEL_PROFILE", "conservative_compile_fix")
    sampling.set_sampling_profile("")

    selected = sampling.set_sampling_profile_for_model("gpt-oss-20b")

    assert selected == ""
    assert sampling.resolve_profile_name() == "conservative_compile_fix"
