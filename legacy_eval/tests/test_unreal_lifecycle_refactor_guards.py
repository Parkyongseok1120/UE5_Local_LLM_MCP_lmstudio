from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import lmstudio_unreal_wrapper as wrapper  # noqa: E402
from refactor_plan import validate_refactor_plan  # noqa: E402


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_world_subsystem_rejects_invalid_destroyed_override(tmp_path: Path) -> None:
    header = tmp_path / "Source" / "AnyGame" / "Public" / "AnyWorldSubsystem.h"
    write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Subsystems/WorldSubsystem.h"
#include "AnyWorldSubsystem.generated.h"

UCLASS()
class ANYGAME_API UAnyWorldSubsystem : public UWorldSubsystem
{
    GENERATED_BODY()

protected:
    virtual void OnWorldDestroyed(UWorld* World) override;
};
""",
    )

    findings = wrapper.validate_unreal_readiness(tmp_path)

    assert any(finding.code == "INVALID_UNREAL_LIFECYCLE_OVERRIDE" for finding in findings)
    assert any("OnWorldEndPlay" in finding.message for finding in findings)


def test_world_subsystem_accepts_valid_endplay_override(tmp_path: Path) -> None:
    header = tmp_path / "Source" / "AnyGame" / "Public" / "AnyWorldSubsystem.h"
    write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Subsystems/WorldSubsystem.h"
#include "AnyWorldSubsystem.generated.h"

UCLASS()
class ANYGAME_API UAnyWorldSubsystem : public UWorldSubsystem
{
    GENERATED_BODY()

protected:
    virtual void OnWorldEndPlay(UWorld& InWorld) override;
    virtual void PreDeinitialize() override;
};
""",
    )

    findings = wrapper.validate_unreal_readiness(tmp_path)

    assert not any(finding.code == "INVALID_UNREAL_LIFECYCLE_OVERRIDE" for finding in findings)


def test_actor_component_rejects_world_subsystem_lifecycle_override(tmp_path: Path) -> None:
    header = tmp_path / "Source" / "Demo" / "Public" / "DemoComponent.h"
    write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "DemoComponent.generated.h"

UCLASS()
class DEMO_API UDemoComponent : public UActorComponent
{
    GENERATED_BODY()

protected:
    virtual void OnWorldEndPlay(UWorld& InWorld) override;
};
""",
    )

    findings = wrapper.validate_unreal_readiness(tmp_path)

    assert any(finding.code == "INVALID_UNREAL_LIFECYCLE_OVERRIDE" for finding in findings)


def test_refactor_plan_rejects_invalid_unreal_lifecycle_name() -> None:
    result = validate_refactor_plan(
        "R2",
        (
            "Patch Source/Demo/Public/DemoWorldSubsystem.h and add OnWorldDestroyed "
            "for subsystem cleanup. Run UBT after patch."
        ),
    )

    assert result["ok"] is False
    assert any("OnWorldDestroyed" in issue for issue in result["issues"])


def test_project_root_relative_path_guard_rejects_parent_repo_paths(tmp_path: Path) -> None:
    try:
        wrapper.safe_output_path(
            tmp_path,
            "Github/AnyGame/Source/AnyGame/Private/AnyComponent.cpp",
        )
    except ValueError as exc:
        assert "writes are limited" in str(exc)
    else:
        raise AssertionError("expected Github/... project-parent path to be rejected")
