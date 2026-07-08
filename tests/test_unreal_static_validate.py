from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from unreal_static_validate import (  # noqa: E402
    validate_duplicate_source_basenames,
    validate_include_paths_exist,
    validate_unreal_readiness,
    validate_generated_h,
    validate_delegate_broadcast_consistency,
    build_source_include_index,
    has_static_errors,
)

FIXTURE = ROOT / "tests" / "fixtures" / "compile_fix_ceiling" / "missing_gameplaytags_dep"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_duplicate_source_basename_detected(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    _write(
        project / "Source" / "Demo" / "Public" / "Foo" / "HealthComponent.h",
        '#include "HealthComponent.generated.h"\n',
    )
    _write(
        project / "Source" / "Demo" / "Public" / "Bar" / "HealthComponent.h",
        '#include "HealthComponent.generated.h"\n',
    )

    findings = validate_duplicate_source_basenames(project)
    codes = {item.code for item in findings}

    assert "DUPLICATE_SOURCE_BASENAME" in codes
    assert has_static_errors(findings)


def test_include_path_not_found_detected(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    cpp = project / "Source" / "Demo" / "Private" / "Enemy.cpp"
    _write(
        project / "Source" / "Demo" / "Public" / "SharedComponent" / "HealthComponent.h",
        '#include "HealthComponent.generated.h"\n',
    )
    _write(cpp, '#include "Character/Player/Component/HealthComponent.h"\n')

    include_index = build_source_include_index(project)
    findings = validate_include_paths_exist(cpp, cpp.read_text(encoding="utf-8"), project, include_index)

    assert any(item.code == "INCLUDE_PATH_NOT_FOUND" for item in findings)


def test_module_fix_build_cs_patch_skips_include_path_gate(tmp_path: Path) -> None:
    project = tmp_path / "CompileFixTags"
    source_root = FIXTURE / "Source"
    for path in source_root.rglob("*"):
        if path.is_file():
            rel = path.relative_to(FIXTURE)
            dest = project / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    (project / "CompileFixTags.uproject").write_text(
        (FIXTURE / "CompileFixTags.uproject").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    before = validate_unreal_readiness(project)
    assert any(item.code == "INCLUDE_PATH_NOT_FOUND" for item in before)

    build_cs = project / "Source" / "CompileFixTags" / "CompileFixTags.Build.cs"
    build_cs.write_text(
        (FIXTURE / "golden" / "Source" / "CompileFixTags" / "CompileFixTags.Build.cs").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    after = validate_unreal_readiness(project, skip_include_path_checks=True)
    assert not any(item.code == "INCLUDE_PATH_NOT_FOUND" for item in after)
    assert not has_static_errors(after)


def test_generated_h_after_type_detected(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    header = project / "Source" / "Demo" / "Public" / "FooComponent.h"
    _write(
        header,
        """#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"

UCLASS()
class UFooComponent : public UActorComponent
{
	GENERATED_BODY()
};
#include "FooComponent.generated.h"
""",
    )
    findings = validate_generated_h(header, header.read_text(encoding="utf-8"), project)
    assert any(item.code == "GENERATED_H_AFTER_TYPE" for item in findings)


def test_delegate_broadcast_empty_args_detected(tmp_path: Path) -> None:
    project = tmp_path / "Demo"
    cpp = project / "Source" / "Demo" / "Private" / "Score.cpp"
    _write(
        cpp,
        """#include "Score.h"

void Trigger()
{
	OnScoreChanged.Broadcast();
}
""",
    )
    findings = validate_delegate_broadcast_consistency(cpp, cpp.read_text(encoding="utf-8"), project)
    assert any(item.code == "DELEGATE_BROADCAST_SIGNATURE_MISMATCH" for item in findings)

