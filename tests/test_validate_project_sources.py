from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from validate_project_sources import normalize_scope_target, resolve_project_root  # noqa: E402


def test_resolve_project_root_from_uproject(tmp_path: Path) -> None:
    uproject = tmp_path / "Demo.uproject"
    uproject.write_text("{}", encoding="utf-8")
    assert resolve_project_root(uproject) == tmp_path


def test_resolve_project_root_from_source_dir(tmp_path: Path) -> None:
    source = tmp_path / "Source"
    source.mkdir()
    assert resolve_project_root(source) == tmp_path


def test_cli_missing_source_returns_exit_code_2(tmp_path: Path) -> None:
    script = SCRIPTS / "validate_project_sources.py"
    result = subprocess.run(
        [sys.executable, str(script), "--project-root", str(tmp_path), "--json"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2


def test_cli_scans_plugin_only_project_without_game_source(tmp_path: Path) -> None:
    project = tmp_path / "ContentProject"
    project.mkdir()
    (project / "ContentProject.uproject").write_text("{}", encoding="utf-8")
    plugin = project / "Plugins" / "PortablePlugin"
    module = plugin / "Source" / "PortablePlugin"
    (module / "Public").mkdir(parents=True)
    (module / "Private").mkdir()
    (plugin / "PortablePlugin.uplugin").write_text(
        json.dumps(
            {
                "FileVersion": 3,
                "Modules": [{"Name": "PortablePlugin", "Type": "Runtime"}],
            }
        ),
        encoding="utf-8",
    )
    (module / "PortablePlugin.Build.cs").write_text(
        'PublicDependencyModuleNames.AddRange(new string[] { "Core" });\n',
        encoding="utf-8",
    )
    (module / "Public" / "PortableObject.hxx").write_text(
        "#pragma once\nUCLASS()\nclass UPortableObject { GENERATED_BODY() };\n",
        encoding="utf-8",
    )

    script = SCRIPTS / "validate_project_sources.py"
    result = subprocess.run(
        [sys.executable, str(script), "--project-root", str(project), "--json"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["scanRoots"] == [str(module)]
    assert any(
        item["path"].endswith("PortableObject.hxx")
        and item["code"] == "GENERATED_H_MISSING"
        for item in payload["findings"]
    )

    scoped = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-root",
            str(project),
            "--json",
            "--scope-target",
            "Plugins/PortablePlugin/Source/PortablePlugin/Public/PortableObject.hxx",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    scoped_payload = json.loads(scoped.stdout)
    assert scoped.returncode == 1
    assert scoped_payload["scopeKind"] == "task_slice"
    assert scoped_payload["scopedFileCount"] >= 1
    assert scoped_payload["hasBlockingErrors"] is True


def test_scope_target_rejects_paths_outside_project_code_roots() -> None:
    assert normalize_scope_target(r"Source\Demo\Actor.cpp") == "Source/Demo/Actor.cpp"
    assert normalize_scope_target("Plugins/Demo/Source/Demo/Actor.cpp").startswith("Plugins/")

    for unsafe in ("../Outside.cpp", "/tmp/Outside.cpp", "C:/Outside.cpp", "Config/DefaultGame.ini"):
        try:
            normalize_scope_target(unsafe)
        except ValueError:
            continue
        raise AssertionError(f"unsafe scope target was accepted: {unsafe}")


def test_cli_json_output_on_clean_fixture(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    (project / "Source" / "Demo").mkdir(parents=True)
    (project / "Source" / "Demo" / "Demo.Build.cs").write_text(
        'using UnrealBuildTool;\npublic class Demo : ModuleRules { public Demo(ReadOnlyTargetRules Target) : base(Target) {} }\n',
        encoding="utf-8",
    )
    script = SCRIPTS / "validate_project_sources.py"
    result = subprocess.run(
        [sys.executable, str(script), "--project-root", str(project), "--json"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(result.stdout)
    assert "findingCount" in payload
    assert "hasErrors" in payload
    # No --write-target: hasBlockingErrors falls back to the full-project hasErrors value.
    assert payload["hasBlockingErrors"] == payload["hasErrors"]
    assert payload["deferredCount"] == 0
    assert payload["preExistingCount"] == 0


def _write_two_file_project(tmp_path: Path) -> Path:
    project = tmp_path / "Demo"
    module_dir = project / "Source" / "Demo"
    module_dir.mkdir(parents=True)
    (module_dir / "Demo.Build.cs").write_text(
        'using UnrealBuildTool;\npublic class Demo : ModuleRules { public Demo(ReadOnlyTargetRules Target) : base(Target) {} }\n',
        encoding="utf-8",
    )
    # Pre-existing error in a file the model has NOT touched this turn.
    (module_dir / "Existing.h").write_text(
        '#pragma once\n#include "Existing.generated.h"\n#include "CoreMinimal.h"\n\nUCLASS()\nclass DEMO_API UExisting : public UObject\n{\n\tGENERATED_BODY()\n};\n',
        encoding="utf-8",
    )
    # Freshly written header that only declares a method (its .cpp hasn't been written yet).
    (module_dir / "New.h").write_text(
        '#pragma once\n#include "CoreMinimal.h"\n#include "New.generated.h"\n\nUCLASS()\nclass DEMO_API UNew : public UObject\n{\n\tGENERATED_BODY()\npublic:\n\tvoid DoThing();\n};\n',
        encoding="utf-8",
    )
    # At least one .cpp must exist for validate_cpp_definitions_missing to run at all.
    (module_dir / "Dummy.cpp").write_text('#include "Existing.h"\n', encoding="utf-8")
    return project


def test_cli_write_target_defers_own_missing_definition(tmp_path: Path) -> None:
    project = _write_two_file_project(tmp_path)
    script = SCRIPTS / "validate_project_sources.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-root",
            str(project),
            "--json",
            "--write-target",
            "Source/Demo/New.h",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(result.stdout)
    assert payload["writeTarget"] == "Source/Demo/New.h"
    # Header-only scoped write skips cpp-missing checks when no paired .cpp is in scope.
    assert payload["hasBlockingErrors"] is False
    assert payload["deferredCount"] == 0


def test_cli_write_target_ignores_pre_existing_error_in_other_file(tmp_path: Path) -> None:
    project = _write_two_file_project(tmp_path)
    # Break the pre-existing file's generated.h placement so it has a real blocking-class error.
    (project / "Source" / "Demo" / "Existing.h").write_text(
        '#pragma once\n#include "CoreMinimal.h"\n\nUCLASS()\nclass DEMO_API UExisting : public UObject\n{\n\tGENERATED_BODY()\n};\n#include "Existing.generated.h"\n',
        encoding="utf-8",
    )
    script = SCRIPTS / "validate_project_sources.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-root",
            str(project),
            "--json",
            "--write-target",
            "Source/Demo/New.h",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(result.stdout)
    assert payload["scanMode"] == "scoped"
    assert payload["scopedFileCount"] >= 1
    assert payload["elapsedMs"] >= 0
    # Scoped write exits 0 when the write-target itself has no blocking errors.
    assert result.returncode == 0
    # Pre-existing errors in unscanned files are not surfaced on the write path.
    assert payload["hasBlockingErrors"] is False
    assert payload["preExistingCount"] == 0


def test_cli_task_scope_does_not_promote_unrelated_project_errors(tmp_path: Path) -> None:
    project = _write_two_file_project(tmp_path)
    (project / "Source" / "Demo" / "Existing.h").write_text(
        '#pragma once\n#include "CoreMinimal.h"\n\nUCLASS()\n'
        'class DEMO_API UExisting : public UObject\n{\n\tGENERATED_BODY()\n};\n'
        '#include "Existing.generated.h"\n',
        encoding="utf-8",
    )
    script = SCRIPTS / "validate_project_sources.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-root",
            str(project),
            "--json",
            "--scope-target",
            "Source/Demo/New.h",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert payload["scopeKind"] == "task_slice"
    assert payload["scopeTargets"] == ["Source/Demo/New.h"]
    assert not any("Existing.h" in item["path"] for item in payload["findings"])


def test_cli_task_scope_keeps_unselected_pair_findings_advisory(tmp_path: Path) -> None:
    project = _write_two_file_project(tmp_path)
    module = project / "Source" / "Demo"
    (module / "New.cpp").write_text('#include "New.h"\n', encoding="utf-8")
    (module / "New.h").write_text(
        '#pragma once\n#include "CoreMinimal.h"\n\nUCLASS()\n'
        'class DEMO_API UNew : public UObject\n{\n\tGENERATED_BODY()\n};\n'
        '#include "New.generated.h"\n',
        encoding="utf-8",
    )
    script = SCRIPTS / "validate_project_sources.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-root",
            str(project),
            "--json",
            "--scope-target",
            "Source/Demo/New.cpp",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(result.stdout)

    paired = [item for item in payload["findings"] if item["path"].endswith("New.h")]
    assert paired
    assert all(item["blocking"] is False for item in paired)
    assert payload["preExistingCount"] >= 1
    assert payload["hasBlockingErrors"] is False
    assert result.returncode == 0


def test_cli_task_scope_blocks_selected_file_error(tmp_path: Path) -> None:
    project = _write_two_file_project(tmp_path)
    (project / "Source" / "Demo" / "New.h").write_text(
        '#pragma once\n#include "CoreMinimal.h"\n\nUCLASS()\n'
        'class DEMO_API UNew : public UObject\n{\n\tGENERATED_BODY()\n};\n'
        '#include "New.generated.h"\n',
        encoding="utf-8",
    )
    script = SCRIPTS / "validate_project_sources.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-root",
            str(project),
            "--json",
            "--scope-target",
            "Source/Demo/New.h",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(result.stdout)

    assert payload["hasBlockingErrors"] is True
    assert any(item["blocking"] is True for item in payload["findings"])
    assert result.returncode == 1


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX exact-path scope regression")
def test_cli_task_scope_does_not_promote_i_dot_twin_file_error(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    module = project / "Source" / "Demo"
    selected = module / "Public" / "I\u0307" / "Thing.h"
    other = module / "Public" / "\u0130" / "Thing.h"
    selected.parent.mkdir(parents=True)
    other.parent.mkdir(parents=True)
    (module / "Demo.Build.cs").write_text(
        "using UnrealBuildTool;\npublic class Demo : ModuleRules "
        "{ public Demo(ReadOnlyTargetRules Target) : base(Target) {} }\n",
        encoding="utf-8",
    )
    selected.write_text('#pragma once\n#include "\u0130/Thing.h"\n', encoding="utf-8")
    other.write_text(
        '#pragma once\n#include "CoreMinimal.h"\n\nUCLASS()\n'
        'class DEMO_API UBroken : public UObject\n{\n\tGENERATED_BODY()\n};\n'
        '#include "Thing.generated.h"\n',
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "validate_project_sources.py"),
            "--project-root",
            str(project),
            "--json",
            "--scope-target",
            "Source/Demo/Public/I\u0307/Thing.h",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(result.stdout)
    twin_findings = [item for item in payload["findings"] if "/\u0130/Thing.h" in item["path"].replace("\\", "/")]
    assert twin_findings
    assert all(item["blocking"] is False for item in twin_findings)
    assert payload["hasBlockingErrors"] is False
    assert result.returncode == 0


def test_cli_write_target_scoped_scan_is_fast(tmp_path: Path) -> None:
    project = _write_two_file_project(tmp_path)
    script = SCRIPTS / "validate_project_sources.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-root",
            str(project),
            "--json",
            "--write-target",
            "Source/Demo/New.h",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(result.stdout)
    assert payload["scanMode"] == "scoped"
    assert payload["scopedFileCount"] <= 3
    assert payload["elapsedMs"] < 2000


def test_cli_write_target_blocks_error_on_written_file(tmp_path: Path) -> None:
    project = _write_two_file_project(tmp_path)
    (project / "Source" / "Demo" / "New.h").write_text(
        '#pragma once\n#include "CoreMinimal.h"\n\nUCLASS()\nclass DEMO_API UNew : public UObject\n{\n\tGENERATED_BODY()\n};\n#include "New.generated.h"\n',
        encoding="utf-8",
    )
    script = SCRIPTS / "validate_project_sources.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-root",
            str(project),
            "--json",
            "--write-target",
            "Source/Demo/New.h",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(result.stdout)
    assert payload["hasBlockingErrors"] is True
    assert result.returncode == 1


def _write_mjs_like_project(tmp_path: Path) -> Path:
    project = tmp_path / "Project_MJS"
    public_dir = project / "Source" / "Game" / "Public" / "Character" / "Player" / "Component"
    private_dir = project / "Source" / "Game" / "Private" / "Character" / "Player" / "Component"
    other_public = project / "Source" / "Game" / "Public" / "Character" / "Player"
    other_public.mkdir(parents=True, exist_ok=True)
    public_dir.mkdir(parents=True, exist_ok=True)
    private_dir.mkdir(parents=True, exist_ok=True)
    (project / "Source" / "Game" / "Game.Build.cs").write_text(
        'using UnrealBuildTool;\npublic class Game : ModuleRules { public Game(ReadOnlyTargetRules Target) : base(Target) {} }\n',
        encoding="utf-8",
    )
    (other_public / "TargetingTypes.h").write_text(
        '#pragma once\n#include "CoreMinimal.h"\n',
        encoding="utf-8",
    )
    (public_dir / "SkillComponent.h").write_text(
        '#pragma once\n#include "CoreMinimal.h"\n#include "Components/ActorComponent.h"\n'
        '#include "TimerManager.h"\n'
        'class USphereComponent;\n'
        '#include "SkillComponent.generated.h"\n\n'
        'UCLASS()\nclass GAME_API USkillComponent : public UActorComponent\n{\n'
        '\tGENERATED_BODY()\npublic:\n\tvoid BeginPlay();\n};\n',
        encoding="utf-8",
    )
    (private_dir / "SkillComponent.cpp").write_text(
        '#include "Character/Player/Component/SkillComponent.h"\n#include "Engine/World.h"\n\n'
        'void USkillComponent::BeginPlay()\n{\n\tSuper::BeginPlay();\n}\n',
        encoding="utf-8",
    )
    (public_dir / "TargetingComponent.h").write_text(
        '#pragma once\n#include "CoreMinimal.h"\n#include "Components/ActorComponent.h"\n'
        '#include "Character/Player/TargetingTypes.h"\n#include "TargetingComponent.generated.h"\n\n'
        'UCLASS()\nclass GAME_API UTargetingComponent : public UActorComponent\n{\n'
        '\tGENERATED_BODY()\npublic:\n\tvoid BeginPlay();\n};\n',
        encoding="utf-8",
    )
    (private_dir / "TargetingComponent.cpp").write_text(
        '#include "Character/Player/Component/TargetingComponent.h"\n\n'
        'void UTargetingComponent::BeginPlay()\n{\n\tSuper::BeginPlay();\n}\n',
        encoding="utf-8",
    )
    return project


def test_cli_write_target_public_private_pair_scopes_both_files(tmp_path: Path) -> None:
    project = _write_mjs_like_project(tmp_path)
    script = SCRIPTS / "validate_project_sources.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-root",
            str(project),
            "--json",
            "--write-target",
            "Source/Game/Public/Character/Player/Component/SkillComponent.h",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(result.stdout)
    assert payload["scopedFileCount"] >= 2
    assert payload["hasBlockingErrors"] is False
    assert result.returncode == 0
    assert payload["elapsedMs"] < 2000


def test_cli_write_target_cpp_public_private_pair(tmp_path: Path) -> None:
    project = _write_mjs_like_project(tmp_path)
    script = SCRIPTS / "validate_project_sources.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-root",
            str(project),
            "--json",
            "--write-target",
            "Source/Game/Private/Character/Player/Component/SkillComponent.cpp",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(result.stdout)
    assert payload["scopedFileCount"] >= 2
    assert payload["hasBlockingErrors"] is False
    assert result.returncode == 0


def test_cli_write_target_header_only_skips_cpp_definition_missing(tmp_path: Path) -> None:
    project = _write_mjs_like_project(tmp_path)
    header = project / "Source" / "Game" / "Public" / "Character" / "Player" / "Component" / "NewOnly.h"
    header.write_text(
        '#pragma once\n#include "CoreMinimal.h"\n#include "NewOnly.generated.h"\n\n'
        'UCLASS()\nclass GAME_API UNewOnly : public UObject\n{\n\tGENERATED_BODY()\npublic:\n\tvoid DoThing();\n};\n',
        encoding="utf-8",
    )
    script = SCRIPTS / "validate_project_sources.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--project-root",
            str(project),
            "--json",
            "--write-target",
            "Source/Game/Public/Character/Player/Component/NewOnly.h",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(result.stdout)
    assert payload["hasBlockingErrors"] is False
    assert not any(
        item.get("code") == "CPP_DEFINITION_MISSING" and item.get("severity") == "error"
        for item in payload.get("findings", [])
    )

