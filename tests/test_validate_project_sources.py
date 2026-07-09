from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from validate_project_sources import resolve_project_root  # noqa: E402


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
    # CPP_DEFINITION_MISSING for the just-written header is deferred, not blocking.
    assert payload["hasBlockingErrors"] is False
    assert payload["deferredCount"] >= 1


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
    # The full-project exit code still reflects the pre-existing error...
    assert result.returncode == 1
    assert payload["hasErrors"] is True
    # ...but the write to New.h itself is not blocked by it.
    assert payload["hasBlockingErrors"] is False
    assert payload["preExistingCount"] >= 1
