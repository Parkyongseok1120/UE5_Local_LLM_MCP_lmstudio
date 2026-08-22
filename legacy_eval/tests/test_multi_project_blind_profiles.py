# Archived workflow code-generation profile tests.
from __future__ import annotations

import json
from pathlib import Path

import pytest

from build_symbol_graph import build_symbol_graph
from code_generation_contract import build_generation_contract
from unreal_capability_detection import detect_unreal_capabilities


PROFILES = (
    ("NetSandbox", ["OnlineSubsystem"], True, False),
    ("SoloAdventure", [], False, False),
    ("ComponentLab", [], False, False),
    ("AbilityPrototype", ["GameplayAbilities"], False, True),
    ("ModularHost", ["GenericFeaturePlugin"], False, False),
)


def _create_project(tmp_path: Path, name: str, plugins: list[str], automation: bool) -> Path:
    root = tmp_path / name
    root.mkdir()
    project = root / f"{name}.uproject"
    project.write_text(
        json.dumps(
            {
                "EngineAssociation": "custom",
                "Modules": [{"Name": f"{name}Runtime", "Type": "Runtime"}],
                "Plugins": [{"Name": item, "Enabled": True} for item in plugins],
            }
        ),
        encoding="utf-8",
    )
    module = root / "Source" / f"{name}Runtime"
    public = module / "Public"
    private = module / "Private"
    public.mkdir(parents=True)
    private.mkdir()
    (module / f"{name}Runtime.Build.cs").write_text(
        f"public class {name}Runtime : ModuleRules {{}}\n",
        encoding="utf-8",
    )
    (public / "FeatureComponent.h").write_text(
        "#pragma once\n"
        "#include \"Components/ActorComponent.h\"\n"
        "#include \"FeatureComponent.generated.h\"\n"
        "UCLASS()\n"
        "class UFeatureComponent : public UActorComponent\n"
        "{\n"
        "    GENERATED_BODY()\n"
        "};\n",
        encoding="utf-8",
    )
    test_macro = (
        f'IMPLEMENT_SIMPLE_AUTOMATION_TEST(F{name}RuntimeTest, "{name}.Runtime.Contract", 0)\n'
        if automation
        else ""
    )
    (private / "FeatureComponent.cpp").write_text(
        '#include "FeatureComponent.h"\n' + test_macro,
        encoding="utf-8",
    )
    if name == "ModularHost":
        plugin_module = root / "Plugins" / "GenericFeaturePlugin" / "Source" / "GenericFeatureRuntime"
        plugin_module.mkdir(parents=True)
        (plugin_module / "GenericFeatureRuntime.Build.cs").write_text(
            "public class GenericFeatureRuntime : ModuleRules {}\n",
            encoding="utf-8",
        )
        (root / "Plugins" / "GenericFeaturePlugin" / "GenericFeaturePlugin.uplugin").write_text(
            json.dumps({"FileVersion": 3}), encoding="utf-8"
        )
    return project


@pytest.mark.parametrize("name,plugins,automation,gas_expected", PROFILES)
def test_blind_project_profiles_have_source_bound_contracts(
    tmp_path: Path,
    name: str,
    plugins: list[str],
    automation: bool,
    gas_expected: bool,
) -> None:
    project = _create_project(tmp_path, name, plugins, automation)
    capabilities = detect_unreal_capabilities(project, host_platform="linux")
    assert capabilities["ok"] is True
    assert capabilities["project"]["sourceAvailable"] is True
    assert f"{name}Runtime" in capabilities["project"]["modules"]
    assert capabilities["execution"]["automationDeclared"] is automation
    assert capabilities["features"]["gameplayAbilities"]["available"] is gas_expected

    graph = build_symbol_graph(project.parent / "Source")
    assert any(row.get("symbol_name") == "UFeatureComponent" for row in graph["symbols"])
    relative = f"Source/{name}Runtime/Public/FeatureComponent.h"
    contract = build_generation_contract(
        "Add one bounded property to the existing component",
        project_root=project.parent,
        target_files=[relative],
        change_kind="modify_existing",
        validation_plan=["Compile the owning module"],
        graph=graph,
    )
    assert contract["projectSpecific"] is True
    assert contract["targets"][0]["exists"] is True
    assert contract["targets"][0]["path"] == relative


def test_plugin_module_is_discovered_without_project_name_special_case(tmp_path: Path) -> None:
    project = _create_project(tmp_path, "ModularHost", ["GenericFeaturePlugin"], False)
    capabilities = detect_unreal_capabilities(project, host_platform="linux")
    assert "GenericFeatureRuntime" in capabilities["project"]["modules"]
    assert "GenericFeaturePlugin" in capabilities["project"]["plugins"]
