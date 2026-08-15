#!/usr/bin/env python
"""Static and unit contracts for Unreal 5.0-5.x metadata exporters."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
EXPORT_ROOT = ROOT / "tools" / "ue_export"
PLUGIN_ROOT = ROOT / "tools" / "ue_plugins" / "LmStudioGraphExporter"
sys.path.insert(0, str(EXPORT_ROOT))

from export_common import asset_class_name  # noqa: E402


def _function_annotations(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for argument in (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ):
                if argument.annotation is not None:
                    yield argument.annotation
            if node.args.vararg and node.args.vararg.annotation is not None:
                yield node.args.vararg.annotation
            if node.args.kwarg and node.args.kwarg.annotation is not None:
                yield node.args.kwarg.annotation
            if node.returns is not None:
                yield node.returns
        elif isinstance(node, ast.AnnAssign):
            yield node.annotation


def test_asset_class_name_prefers_modern_path_and_falls_back_to_ue50_field():
    modern = SimpleNamespace(
        asset_class_path=SimpleNamespace(asset_name="Blueprint"),
        asset_class="LegacyBlueprint",
    )
    legacy = SimpleNamespace(asset_class="World")
    empty_modern = SimpleNamespace(
        asset_class_path=SimpleNamespace(asset_name=""),
        asset_class="Material",
    )

    class BrokenModernPath:
        asset_class = "AnimBlueprint"

        @property
        def asset_class_path(self):
            raise RuntimeError("modern field is unavailable")

    assert asset_class_name(modern) == "Blueprint"
    assert asset_class_name(legacy) == "World"
    assert asset_class_name(empty_modern) == "Material"
    assert asset_class_name(BrokenModernPath()) == "AnimBlueprint"


def test_ue_export_scripts_parse_as_python39_without_pep604_annotations():
    for path in sorted(EXPORT_ROOT.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path), feature_version=(3, 9))
        pep604 = [
            node
            for annotation in _function_annotations(tree)
            for node in ast.walk(annotation)
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)
        ]
        assert not pep604, f"Python 3.10 PEP 604 annotation remains in {path.name}"


def test_asset_data_class_reads_are_centralized_in_export_common():
    violations = []
    for path in sorted(EXPORT_ROOT.glob("*.py")):
        if path.name == "export_common.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"asset_class", "asset_class_path"}:
                violations.append(f"{path.name}:{node.lineno}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"getattr", "hasattr"}
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in {"asset_class", "asset_class_path"}
            ):
                violations.append(f"{path.name}:{node.lineno}")
    assert not violations, (
        "asset class lookup bypasses export_common.asset_class_name: "
        + ", ".join(violations)
    )

    direct_scanners = {
        "export_animation_metadata.py",
        "export_asset_registry.py",
        "export_blueprint_metadata.py",
        "export_level_metadata.py",
        "export_material_metadata.py",
    }
    for name in direct_scanners:
        source = (EXPORT_ROOT / name).read_text(encoding="utf-8")
        assert "asset_class_name(asset)" in source, f"{name} does not use the shared helper"


def test_cpp_asset_class_access_is_guarded_for_ue50_and_reused_everywhere():
    cpp_path = (
        PLUGIN_ROOT
        / "Source"
        / "LmStudioGraphExporter"
        / "Private"
        / "LmStudioGraphExporterLibrary.cpp"
    )
    source = cpp_path.read_text(encoding="utf-8")

    assert '#include "Runtime/Launch/Resources/Version.h"' in source
    assert (
        "#if ENGINE_MAJOR_VERSION > 5 || "
        "(ENGINE_MAJOR_VERSION == 5 && ENGINE_MINOR_VERSION >= 1)"
    ) in source
    assert source.count("Asset.AssetClassPath.GetAssetName().ToString()") == 1
    assert len(re.findall(r"Asset\.AssetClass\b", source)) == 1
    assert source.count("LmStudioAssetClassName(Asset)") == 2

    descriptor = json.loads((PLUGIN_ROOT / "LmStudioGraphExporter.uplugin").read_text(encoding="utf-8"))
    assert not descriptor.get("EngineVersion"), "The plugin must remain installable on UE 5.0"
