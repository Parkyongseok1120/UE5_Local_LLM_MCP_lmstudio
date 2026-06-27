#!/usr/bin/env python
"""Tests for Build.cs parser."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from parse_build_cs import parse_build_cs_file, public_modules_from_text  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "build_cs"


@pytest.mark.parametrize(
    "fixture,expected_public,expected_private",
    [
        ("addrange_string_array.Build.cs", ["Core", "Engine"], []),
        ("add_single.Build.cs", ["GameplayTags"], ["UMG"]),
        ("addrange_new_array.Build.cs", ["Core", "CoreUObject", "Engine"], []),
        ("editor_conditional.Build.cs", ["Core"], []),
    ],
)
def test_parse_dependencies(fixture, expected_public, expected_private):
    parsed = parse_build_cs_file(FIXTURES / fixture)
    deps = parsed["dependencies"]
    assert deps.get("PublicDependencyModuleNames", []) == expected_public
    assert deps.get("PrivateDependencyModuleNames", []) == expected_private


def test_editor_conditional_block():
    parsed = parse_build_cs_file(FIXTURES / "editor_conditional.Build.cs")
    cond = parsed["conditional_dependencies"]
    assert len(cond) == 1
    assert cond[0]["editor_only"] is True
    assert cond[0]["dependencies"]["PrivateDependencyModuleNames"] == ["UnrealEd"]


def test_public_modules_from_text():
    text = (FIXTURES / "add_single.Build.cs").read_text(encoding="utf-8")
    assert "GameplayTags" in public_modules_from_text(text)
