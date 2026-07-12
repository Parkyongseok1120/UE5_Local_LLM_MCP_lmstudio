"""Tests for project symbol include resolver."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from include_resolver import (  # noqa: E402
    classify_include_visibility,
    infer_usage_kind,
    project_relative_include,
    resolve_project_symbol_include,
)


def _fixture_tree(tmp_path: Path) -> Path:
    root = tmp_path / "Demo"
    root.mkdir(parents=True, exist_ok=True)
    (root / "Demo.uproject").write_text("{}", encoding="utf-8")
    public = root / "Source" / "Demo" / "Public" / "Components"
    private = root / "Source" / "Demo" / "Private" / "Character"
    public.mkdir(parents=True)
    private.mkdir(parents=True)
    (public / "TargetingComponent.h").write_text(
        "UCLASS()\nclass UTargetingComponent : public UActorComponent {};\n",
        encoding="utf-8",
    )
    (private / "MyCharacter.cpp").write_text(
        "void AMyCharacter::AMyCharacter() {\n"
        "  Sub = CreateDefaultSubobject<UTargetingComponent>(TEXT(\"T\"));\n"
        "}\n",
        encoding="utf-8",
    )
    return root


def test_project_relative_include_public_layout(tmp_path: Path) -> None:
    root = _fixture_tree(tmp_path)
    header = root / "Source" / "Demo" / "Public" / "Components" / "TargetingComponent.h"
    assert project_relative_include(header, root) == "Components/TargetingComponent.h"


def test_resolve_same_module_create_default_subobject(tmp_path: Path) -> None:
    root = _fixture_tree(tmp_path)
    cpp = root / "Source" / "Demo" / "Private" / "Character" / "MyCharacter.cpp"
    resolution = resolve_project_symbol_include(root, "UTargetingComponent", cpp, "create_default_subobject")
    assert resolution is not None
    assert resolution.preferred_include == "Components/TargetingComponent.h"
    assert resolution.build_cs_required is False
    assert resolution.requires_complete_type is True


def test_infer_usage_kind_create_default_subobject() -> None:
    text = "Root = CreateDefaultSubobject<UBoxComponent>(TEXT(\"Box\"));"
    kind = infer_usage_kind(text, "UBoxComponent", text.find("CreateDefaultSubobject"))
    assert kind == "create_default_subobject"


def test_private_cross_module_include_forbidden() -> None:
    visibility = classify_include_visibility(
        owner_module="Other",
        consumer_module="Demo",
        include_path="Other/Private/Secret.h",
        is_private_header=True,
    )
    assert visibility == "private_cross_module_forbidden"
