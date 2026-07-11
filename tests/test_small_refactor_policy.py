#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from refactor_plan import _required_impact_roles, classify_refactor_scope  # noqa: E402


def test_small_scope_does_not_require_delegate_binding_without_surface():
    roles = _required_impact_roles("small_single_surface_refactor", {"declaration": 1, "definition": 1})
    assert "delegate_binding" not in roles
    assert "include_owner" not in roles


def test_include_owner_only_when_present_or_rename():
    roles = _required_impact_roles("medium_system_local_refactor", {"declaration": 1, "definition": 1})
    assert "include_owner" not in roles
    roles_with_include = _required_impact_roles(
        "medium_system_local_refactor",
        {"declaration": 1, "definition": 1, "include_owner": 1},
    )
    assert "include_owner" in roles_with_include


def test_callsite_required_only_when_detected():
    roles = _required_impact_roles("medium_system_local_refactor", {"declaration": 1, "definition": 1})
    assert "callsite" not in roles
    roles_calls = _required_impact_roles(
        "medium_system_local_refactor",
        {"declaration": 1, "definition": 1, "callsite": 2},
    )
    assert "callsite" in roles_calls


def test_small_single_surface_writes_allowed_by_default():
    scope = classify_refactor_scope("rename private helper DoWork in one file")
    assert scope["scope"] == "small_single_surface_refactor"
    assert scope["writesAllowedByDefault"] is True
