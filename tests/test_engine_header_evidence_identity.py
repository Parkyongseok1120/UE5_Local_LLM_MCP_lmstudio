from __future__ import annotations

import sys
import pytest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import engine_header_evidence  # noqa: E402


def test_multiline_annotation_context_ignores_parentheses_in_strings_and_comments():
    source = '''class UDemo {
    UFUNCTION(
        BlueprintCallable,
        meta=(DisplayName="Find (node", // unmatched ( in comment
              Category="Debug")
    )
    void Run();
};'''
    result = engine_header_evidence._bounded_declaration(source, "Run", "UDemo")
    assert result is not None
    assert "UFUNCTION(" in result["excerpt"]
    assert result["excerptStartLine"] == 2
    assert result["excerptTruncated"] is False
    assert "absence_and_expansion_unverified" in result["annotationVerification"]
from engine_header_evidence import (  # noqa: E402
    _identity,
    clear_engine_header_catalog_cache,
    resolve_engine_include_path,
)


def _synthetic_header_root(tmp_path: Path) -> tuple[Path, Path]:
    engine_root = tmp_path / "EngineRoot"
    header_root = engine_root / "Engine" / "Source" / "Runtime" / "Demo" / "Public"
    header_root.mkdir(parents=True)
    return engine_root, header_root


def test_engine_header_catalog_uses_injected_host_case_rules(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine_root, header_root = _synthetic_header_root(tmp_path)
    header = header_root / "Inventory.h"
    header.write_text("struct FInventory {};\n", encoding="utf-8")
    monkeypatch.setattr(engine_header_evidence.shutil, "which", lambda _name: None)

    clear_engine_header_catalog_cache()
    posix = resolve_engine_include_path(
        engine_root,
        "Runtime/Demo/Public/inventory.h",
        host_platform="linux",
    )
    clear_engine_header_catalog_cache()
    windows = resolve_engine_include_path(
        engine_root,
        "Runtime/Demo/Public/inventory.h",
        host_platform="win32",
    )

    assert posix["ok"] is False
    assert windows["matches"] == [str(header)]


def test_engine_header_catalog_does_not_merge_unicode_casefold_aliases(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine_root, header_root = _synthetic_header_root(tmp_path)
    composed_name = "\u0130nventory.h"
    decomposed_name = "I\u0307nventory.h"
    composed = header_root / composed_name
    decomposed = header_root / decomposed_name
    composed.write_text("struct FComposedInventory {};\n", encoding="utf-8")
    decomposed.write_text("struct FDecomposedInventory {};\n", encoding="utf-8")
    assert composed_name.casefold() == decomposed_name.casefold()
    monkeypatch.setattr(engine_header_evidence.shutil, "which", lambda _name: None)

    include = f"Runtime/Demo/Public/{composed_name}"
    for host_platform in ("linux", "win32"):
        clear_engine_header_catalog_cache()
        result = resolve_engine_include_path(
            engine_root,
            include,
            host_platform=host_platform,
        )
        assert result["matches"] == [str(composed)]
        assert result["ambiguous"] is False


def test_engine_root_identity_preserves_unicode_and_injects_ascii_case_rules(
    tmp_path: Path,
) -> None:
    upper = tmp_path / "UpperEngine"
    lower = tmp_path / "upperengine"
    assert _identity(upper, host_platform="linux") != _identity(
        lower,
        host_platform="linux",
    )
    assert _identity(upper, host_platform="win32") == _identity(
        lower,
        host_platform="win32",
    )

    composed = tmp_path / "\u0130Engine"
    decomposed = tmp_path / "I\u0307Engine"
    for host_platform in ("linux", "win32"):
        assert _identity(composed, host_platform=host_platform) != _identity(
            decomposed,
            host_platform=host_platform,
        )


def test_fields_include_reflection_context_and_do_not_invent_parent_resolution():
    source = """class ADemo : public AParent {
    GENERATED_BODY()
    private:
    UPROPERTY(
        Transient,
        meta=(DisplayName="owner")
    )
    TObjectPtr<AActor> Owner;
    uint8 Enabled : 1;
    int Values[3] = {1, 2, 3};
    struct Nested { int Hidden; };
    int Other = Hidden;
    };"""
    owner = engine_header_evidence._bounded_declaration(source, "Owner", "ADemo")
    assert owner is not None
    assert owner["declarationKind"] == "field"
    assert owner["lexicalAccess"] == "private"
    assert "UPROPERTY" in owner["excerpt"]
    assert owner["parentCandidates"] == ["AParent"]
    assert owner["inheritanceVerification"] == "parent_declarations_not_searched"
    assert engine_header_evidence._bounded_declaration(source, "Enabled", "ADemo")["declarationKind"] == "field"
    assert engine_header_evidence._bounded_declaration(source, "Values", "ADemo")["declarationKind"] == "field"
    assert engine_header_evidence._bounded_declaration(source, "Hidden", "ADemo") is None


@pytest.mark.parametrize("declaration,name", [("using Alias = int;", "Alias"), ("class Forward;", "Forward"), ("struct Forward;", "Forward"), ("typedef int Alias;", "Alias")])
def test_type_declarations_are_not_fields(declaration, name):
    assert engine_header_evidence._bounded_declaration("class ADemo { public: " + declaration + " };", name, "ADemo") is None
