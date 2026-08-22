#!/usr/bin/env python
"""Resolve static, user-selected LM Studio load and chat recommendations.

This module deliberately has no task, phase, turn, planner, tool-order, or
compactor policy.  The model selected in LM Studio owns those decisions.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


DEFAULT_SAMPLING = {
    "temperature": 0.15,
    "topP": 0.85,
    "maxTokens": 4096,
}

_PROFILE_OVERRIDE = ""
_MODEL_PROFILE = ""


def set_sampling_profile(name: str) -> None:
    """Select one profile for this process; an empty value resets selection."""
    global _MODEL_PROFILE, _PROFILE_OVERRIDE
    _PROFILE_OVERRIDE = str(name or "").strip()
    if not _PROFILE_OVERRIDE:
        _MODEL_PROFILE = ""


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
    loaded = json.loads(path.read_text(encoding="utf-8-sig"))
    return loaded if isinstance(loaded, dict) else {}


def resolve_profile_name_for_model(
    model_name: str,
    config: dict[str, Any] | None = None,
) -> str:
    """Resolve a known profile from an LM Studio model id or filename."""
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


def _known_profile_name(value: str, config: dict[str, Any]) -> str:
    profiles = config.get("profiles") or {}
    candidate = str(value or "").strip()
    if candidate in profiles:
        return candidate
    return resolve_profile_name_for_model(candidate, config)


def resolve_profile_name(config: dict[str, Any] | None = None) -> str:
    """Return the selected known profile, falling back to the checked-in default."""
    cfg = config or load_sampling_config()
    requested = (
        _PROFILE_OVERRIDE
        or os.environ.get("UNREAL_RAG_MODEL_PROFILE", "").strip()
        or _MODEL_PROFILE
        or str(cfg.get("activeProfile") or "")
    )
    resolved = _known_profile_name(requested, cfg)
    if resolved:
        return resolved

    fallback = _known_profile_name(str(cfg.get("activeProfile") or ""), cfg)
    if fallback:
        return fallback
    profiles = cfg.get("profiles") or {}
    return str(next(iter(profiles), ""))


def set_sampling_profile_for_model(
    model_name: str,
    config: dict[str, Any] | None = None,
) -> str:
    """Remember a model alias unless the user explicitly selected a profile."""
    global _MODEL_PROFILE
    if _PROFILE_OVERRIDE or os.environ.get("UNREAL_RAG_MODEL_PROFILE", "").strip():
        return ""
    profile = resolve_profile_name_for_model(model_name, config)
    if profile:
        _MODEL_PROFILE = profile
    return profile


def resolve_active_profile(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or load_sampling_config()
    profiles = cfg.get("profiles") or {}
    active = profiles.get(resolve_profile_name(cfg))
    return dict(active) if isinstance(active, dict) else {}


def _profile(profile: str = "") -> dict[str, Any]:
    config = load_sampling_config()
    if profile:
        profile_name = _known_profile_name(profile, config)
        selected = (config.get("profiles") or {}).get(profile_name)
        if isinstance(selected, dict):
            return dict(selected)
    return resolve_active_profile(config)


def load_sampling_preset(
    mode: str = "",
    turn: str = "",
    profile: str = "",
) -> dict[str, Any]:
    """Return one static chat recommendation.

    ``mode`` and ``turn`` remain accepted only for compatibility and are
    intentional no-ops. They never change the returned sampling values.
    """
    del mode, turn
    preset = dict(DEFAULT_SAMPLING)
    sampling = _profile(profile).get("sampling") or {}
    if isinstance(sampling, dict):
        preset.update(sampling)
    return preset


def preset_for_wrapper(
    mode: str = "",
    *,
    compile_patch: bool = False,
) -> dict[str, Any]:
    """Compatibility adapter returning the same static recommendation."""
    del mode, compile_patch
    return load_sampling_preset()


def profile_recommendation(profile: str = "") -> dict[str, Any]:
    """Return load, chat, and bounded write-safety metadata for one profile."""
    active = _profile(profile)
    safety = active.get("writeSafety") or {}
    if not isinstance(safety, dict):
        safety = {}
    return {
        "contextLength": int(active.get("contextLength") or 0) or None,
        "contextLengthAlternatives": list(active.get("contextLengthAlternatives") or []),
        "quantDefault": str(active.get("quantDefault") or ""),
        "recommendedParallelRequests": max(
            1,
            int(active.get("recommendedParallelRequests") or 1),
        ),
        "recommendedSystemPrompt": str(active.get("recommendedSystemPrompt") or ""),
        "sampling": load_sampling_preset(profile=profile),
        "maxFilesPerEdit": max(0, int(safety.get("maxFilesPerEdit") or 0)),
        "preferPatchOverFullFile": bool(safety.get("preferPatchOverFullFile", True)),
    }


def profile_edit_limits(profile: str = "") -> dict[str, Any]:
    """Compatibility name for the bounded recommendation view."""
    return profile_recommendation(profile)


def profile_agent_policy(profile: str = "") -> dict[str, Any]:
    """Deprecated compatibility name; no agent policy is returned."""
    return profile_recommendation(profile)


def main() -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Print a static LM Studio load/chat recommendation."
    )
    parser.add_argument(
        "--mode",
        default="",
        help="Deprecated no-op; profiles never switch settings by task stage.",
    )
    parser.add_argument(
        "--turn",
        default="",
        help="Deprecated no-op; profiles never switch settings by conversation turn.",
    )
    parser.add_argument(
        "--sampling-profile",
        default="",
        help="Select one checked-in profile for this invocation.",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Resolve a profile from an LM Studio model id or GGUF filename.",
    )
    parser.add_argument(
        "--show-profile",
        action="store_true",
        help="Print load, chat, sampling, and bounded write-safety recommendations.",
    )
    args = parser.parse_args()

    if args.mode or args.turn:
        print(
            "warning: --mode and --turn are deprecated no-ops; static profile values are unchanged",
            file=sys.stderr,
        )
    if args.sampling_profile:
        set_sampling_profile(args.sampling_profile)
    elif args.model:
        set_sampling_profile_for_model(args.model)

    if args.show_profile:
        recommendation = profile_recommendation()
        print(
            json.dumps(
                {
                    "profile": resolve_profile_name(),
                    **recommendation,
                },
                indent=2,
            )
        )
        return 0

    print(json.dumps(load_sampling_preset(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
