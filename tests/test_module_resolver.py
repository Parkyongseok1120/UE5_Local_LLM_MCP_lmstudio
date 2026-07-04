from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import module_resolver  # noqa: E402


def test_resolve_common_unreal_modules_from_text():
    text = """
    #include "GameplayTagContainer.h"
    UInputAction* JumpAction;
    UUserWidget* Menu;
    EKeys::LeftMouseButton;
    """

    modules = module_resolver.resolve_modules_from_text(text)

    assert modules == ["GameplayTags", "EnhancedInput", "UMG", "InputCore"]


def test_build_cs_dependency_patch_suggestion_adds_module_sorted():
    build_cs = """
PublicDependencyModuleNames.AddRange(new string[] { "Core" });
PrivateDependencyModuleNames.AddRange(new string[]
{
    "Engine",
    "CoreUObject"
});
"""

    suggestion = module_resolver.suggest_build_cs_dependency_patch(build_cs, "GameplayTags")

    assert suggestion["ok"] is True
    assert suggestion["alreadyPresent"] is False
    assert '"GameplayTags"' in suggestion["newText"]
    assert module_resolver.build_cs_has_module(suggestion["newText"], "GameplayTags")


def test_build_cs_dependency_patch_detects_existing_module():
    suggestion = module_resolver.suggest_build_cs_dependency_patch(
        'PrivateDependencyModuleNames.AddRange(new string[] { "EnhancedInput" });',
        "EnhancedInput",
    )

    assert suggestion["alreadyPresent"] is True


def test_existing_module_hint_does_not_need_patch():
    build_cs = 'PrivateDependencyModuleNames.AddRange(new string[] { "UMG" });'

    assert module_resolver.build_cs_has_module(build_cs, "UMG") is True
    assert module_resolver.suggest_build_cs_dependency_patch(build_cs, "UMG")["alreadyPresent"] is True
