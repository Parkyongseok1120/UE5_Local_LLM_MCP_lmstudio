#!/usr/bin/env python
"""Tests for project routing scope classification."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from project_routing import classify_query_scope, resolve_project_filters  # noqa: E402


def test_engine_api_lookup():
    assert classify_query_scope("how to use UActorComponent in Unreal", "api_lookup") == "engine"


def test_project_agent_edit():
    assert classify_query_scope("fix compile error in Source/MyGame/Player.cpp", "agent_edit") == "project"


def test_material_query_prefers_project_scope_with_active_project():
    assert (
        classify_query_scope(
            "find MI_PlayerArmor material instance texture parameters",
            "auto",
            active_project_path="C:/Games/MyGame/MyGame.uproject",
        )
        == "project"
    )


def test_resolve_engine_scope_clears_project_filter():
    projects, scope = resolve_project_filters(
        "UActorComponent include path BeginPlay example",
        "api_lookup",
        [],
        ["MyGame"],
        scope="engine",
        use_active_project=True,
        active_project_path="C:/Games/MyGame/MyGame.uproject",
    )
    assert scope == "engine"
    assert projects == []
