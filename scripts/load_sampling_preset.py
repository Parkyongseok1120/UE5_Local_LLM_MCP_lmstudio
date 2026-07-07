#!/usr/bin/env python
"""Load LM Studio sampling presets from config/lmstudio_sampling.json."""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

DEFAULT_PRESET = {
    "thinking": "off",
    "temperature": 0.15,
    "topP": 0.8,
    "maxTokens": 4096,
}

_PROFILE_OVERRIDE = ""


def set_sampling_profile(name: str) -> None:
    """Override active profile for this process (CLI --sampling-profile)."""
    global _PROFILE_OVERRIDE
    _PROFILE_OVERRIDE = str(name or "").strip()


def _normalized_model_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")


@lru_cache(maxsize=1)
def sampling_config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "config" / "lmstudio_sampling.json"


@lru_cache(maxsize=1)
def load_sampling_config() -> dict[str, Any]:
    path = sampling_config_path()
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def resolve_profile_name(config: dict[str, Any] | None = None) -> str:
    if _PROFILE_OVERRIDE:
        return _PROFILE_OVERRIDE
    env_name = os.environ.get("UNREAL_RAG_MODEL_PROFILE", "").strip()
    if env_name:
        return env_name
    cfg = config or load_sampling_config()
    return str(cfg.get("activeProfile") or cfg.get("model") or "qwen3_6_27b")


def resolve_profile_name_for_model(model_name: str, config: dict[str, Any] | None = None) -> str:
    """Resolve a sampling profile from the loaded LM Studio model id."""
    cfg = config or load_sampling_config()
    profiles = cfg.get("profiles") or {}
    aliases = cfg.get("modelAliases") or {}
    raw_model = str(model_name or "").strip().lower()
    normalized_model = _normalized_model_key(raw_model)
    if not raw_model:
        return ""

    alias_items = sorted(
        ((str(alias).lower(), str(profile)) for alias, profile in aliases.items()),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    for alias, profile in alias_items:
        if profile not in profiles:
            continue
        normalized_alias = _normalized_model_key(alias)
        if raw_model == alias or normalized_model == normalized_alias:
            return profile
    for alias, profile in alias_items:
        if profile not in profiles:
            continue
        normalized_alias = _normalized_model_key(alias)
        if alias in raw_model or (normalized_alias and normalized_alias in normalized_model):
            return profile

    for profile in profiles:
        normalized_profile = _normalized_model_key(str(profile))
        if normalized_profile and normalized_profile in normalized_model:
            return str(profile)
    return ""


def set_sampling_profile_for_model(model_name: str, config: dict[str, Any] | None = None) -> str:
    """Use modelAliases unless the user explicitly selected a profile."""
    if _PROFILE_OVERRIDE or os.environ.get("UNREAL_RAG_MODEL_PROFILE", "").strip():
        return ""
    profile = resolve_profile_name_for_model(model_name, config)
    if profile:
        set_sampling_profile(profile)
    return profile


def resolve_active_profile(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_sampling_config()
    profile_name = resolve_profile_name(cfg)
    profiles = cfg.get("profiles") or {}
    if isinstance(profiles.get(profile_name), dict):
        return dict(profiles[profile_name])
    legacy = cfg.get(profile_name)
    if isinstance(legacy, dict) and legacy.get("turnPresets"):
        return dict(legacy)
    fallback = profiles.get("qwen3_6_27b") or cfg.get("qwen3_6_27b")
    if isinstance(fallback, dict):
        return dict(fallback)
    return {}


def resolve_turn_name(mode: str = "", turn: str = "") -> str:
    if turn:
        return turn.strip().lower()
    config = load_sampling_config()
    mode_map = config.get("modeMap") or {}
    mapped = mode_map.get(str(mode or "").strip().lower())
    if mapped:
        return str(mapped)
    if str(mode or "").startswith("refactor_r"):
        return "plan" if mode.endswith("0") or mode.endswith("1") else "execute"
    return "execute"


def load_sampling_preset(mode: str = "", turn: str = "", profile: str = "") -> dict[str, Any]:
    config = load_sampling_config()
    if profile:
        profiles = config.get("profiles") or {}
        active = profiles.get(profile) if isinstance(profiles.get(profile), dict) else resolve_active_profile(config)
    else:
        active = resolve_active_profile(config)
    turn_presets = active.get("turnPresets") or {}
    turn_name = resolve_turn_name(mode, turn)
    preset = dict(DEFAULT_PRESET)
    preset.update(turn_presets.get(turn_name) or {})
    return preset


def preset_for_wrapper(mode: str = "agent_edit", *, compile_patch: bool = False) -> dict[str, Any]:
    turn = "compile_fix_patch" if compile_patch else resolve_turn_name(mode)
    return load_sampling_preset(turn=turn)


def profile_edit_limits(profile: str = "") -> dict[str, Any]:
    """Return maxFilesPerEdit and preferPatchOverFullFile from active profile."""
    config = load_sampling_config()
    if profile:
        profiles = config.get("profiles") or {}
        active = profiles.get(profile) if isinstance(profiles.get(profile), dict) else resolve_active_profile(config)
    else:
        active = resolve_active_profile(config)
    policy = active.get("agentPolicy") or {}
    return {
        "maxFilesPerEdit": int(policy.get("maxFilesPerEdit") or active.get("maxFilesPerEdit") or 4),
        "preferPatchOverFullFile": bool(
            policy.get("preferPatch") if "preferPatch" in policy else active.get("preferPatchOverFullFile", True)
        ),
        "assemblyBudgetScale": float(policy.get("ragBudgetScale") or active.get("assemblyBudgetScale") or 1.0),
        "compileFixMaxAttempts": int(policy.get("compileFixMaxAttempts") or 4),
        "planningRequired": bool(policy.get("planningRequired", True)),
        "deepSearch": bool(policy.get("deepSearch", False)),
        "allowRefactorModes": bool(policy.get("allowRefactorModes", True)),
        "jsonRepairStrict": bool(policy.get("jsonRepairStrict", True)),
        "historyTurns": int(policy.get("historyTurns") or 4),
        "defaultTopK": int(policy.get("defaultTopK") or active.get("defaultTopK") or 8),
        "deltaTopK": int(policy.get("deltaTopK") or active.get("deltaTopK") or 4),
        "candidateLimitScale": int(policy.get("candidateLimitScale") or active.get("candidateLimitScale") or 20),
        "targetTier": str(policy.get("targetTier") or active.get("targetTier") or ""),
        "promptContract": str(policy.get("promptContract") or active.get("promptContract") or ""),
        "contextLength": int(active.get("contextLength") or 0) or None,
        "mcpEssentialTools": bool(
            policy.get("mcpEssentialTools") if "mcpEssentialTools" in policy else active.get("mcpEssentialTools", False)
        ),
        "recommendedSystemPrompt": str(
            policy.get("recommendedSystemPrompt") or active.get("recommendedSystemPrompt") or ""
        ),
        "mcpToolDiscipline": str(
            policy.get("mcpToolDiscipline") or active.get("mcpToolDiscipline") or ""
        ),
        "preferSymbolLookupOverFileRead": bool(policy.get("preferSymbolLookupOverFileRead", False)),
        "enforceRangeRead": bool(policy.get("enforceRangeRead", False)),
        "rangeReadContextLines": int(policy.get("rangeReadContextLines") or 0),
        "patchChangedLineLimit": int(policy.get("patchChangedLineLimit") or 0),
        "noOpGuard": bool(policy.get("noOpGuard", False)),
        "twoPhase": bool(policy.get("twoPhase", False)),
    }


def profile_agent_policy(profile: str = "") -> dict[str, Any]:
    return profile_edit_limits(profile)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Print resolved LM Studio sampling preset.")
    parser.add_argument("--mode", default="")
    parser.add_argument("--turn", default="")
    parser.add_argument("--sampling-profile", default="", help="Override active profile for this invocation.")
    parser.add_argument("--show-profile", action="store_true", help="Print active profile name and scale.")
    args = parser.parse_args()

    if args.sampling_profile:
        set_sampling_profile(args.sampling_profile)

    if args.show_profile:
        cfg = load_sampling_config()
        profile = resolve_active_profile(cfg)
        print(
            json.dumps(
                {
                    "profile": resolve_profile_name(cfg),
                    "assemblyBudgetScale": profile.get("assemblyBudgetScale", 1.0),
                    "contextLength": profile.get("contextLength"),
                    "targetTier": profile.get("targetTier", ""),
                    "defaultTopK": (profile.get("agentPolicy") or {}).get("defaultTopK") or profile.get("defaultTopK"),
                    "maxFilesPerEdit": (profile.get("agentPolicy") or {}).get("maxFilesPerEdit") or profile.get("maxFilesPerEdit"),
                },
                indent=2,
            )
        )
        return 0

    preset = load_sampling_preset(mode=args.mode, turn=args.turn, profile=args.sampling_profile)
    print(json.dumps(preset, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
