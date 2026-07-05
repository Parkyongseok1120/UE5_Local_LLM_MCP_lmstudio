#!/usr/bin/env python
"""Create an ignored local live holdout config from the public-safe template."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXAMPLE = ROOT / "config" / "rag_eval_real_project_holdout_cases.local.example.json"
DEFAULT_OUTPUT = ROOT / "config" / "rag_eval_real_project_holdout_cases.local.json"
DEFAULT_SUITE_NAME = "real-project-holdout-local-v0"
DEFAULT_MODEL = "qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max"
DEFAULT_FIXTURE_ROOT = ROOT / "data" / "local_holdout_fixtures"
BASE_FIXTURE = DEFAULT_FIXTURE_ROOT / "_base_ue58_cpp_project"


EXPANSION_CASES_12: list[dict[str, Any]] = [
    {
        "id": "local_umg_missing_module",
        "category": "UMG dependency issue",
        "mode": "module_fix",
        "errorLog": "fatal error C1083: Cannot open include file: 'Blueprint/UserWidget.h': No such file or directory",
        "expectedFilesToRead": ["Source/<Module>/<Module>.Build.cs", "failing UI source/header"],
        "expectedPatchTargets": ["owner Build.cs"],
        "forbiddenPatchTargets": ["unrelated widget rewrite"],
        "expectedModules": ["UMG"],
        "expectedErrorSubkind": "C1083_MISSING_INCLUDE",
        "notes": "UE 5.8 local fixture; expected fix is adding UMG to the owner Build.cs.",
    },
    {
        "id": "local_niagara_missing_module",
        "category": "Niagara dependency issue",
        "mode": "module_fix",
        "errorLog": "fatal error C1083: Cannot open include file: 'NiagaraComponent.h': No such file or directory",
        "expectedFilesToRead": ["Source/<Module>/<Module>.Build.cs", "failing Niagara source/header"],
        "expectedPatchTargets": ["owner Build.cs"],
        "forbiddenPatchTargets": ["unrelated effects rewrite"],
        "expectedModules": ["Niagara"],
        "expectedErrorSubkind": "C1083_MISSING_INCLUDE",
        "notes": "UE 5.8 local fixture; expected fix is adding Niagara to the owner Build.cs.",
    },
    {
        "id": "local_aimodule_missing_module",
        "category": "AI module dependency issue",
        "mode": "module_fix",
        "errorLog": "fatal error C1083: Cannot open include file: 'AIController.h': No such file or directory",
        "expectedFilesToRead": ["Source/<Module>/<Module>.Build.cs", "failing AI source/header"],
        "expectedPatchTargets": ["owner Build.cs"],
        "forbiddenPatchTargets": ["unrelated behavior rewrite"],
        "expectedModules": ["AIModule"],
        "expectedErrorSubkind": "C1083_MISSING_INCLUDE",
        "notes": "UE 5.8 local fixture; expected fix is adding AIModule to the owner Build.cs.",
    },
    {
        "id": "local_navigation_system_missing_module",
        "category": "NavigationSystem dependency issue",
        "mode": "module_fix",
        "errorLog": "fatal error C1083: Cannot open include file: 'NavigationSystem.h': No such file or directory",
        "expectedFilesToRead": ["Source/<Module>/<Module>.Build.cs", "failing navigation source/header"],
        "expectedPatchTargets": ["owner Build.cs"],
        "forbiddenPatchTargets": ["unrelated movement rewrite"],
        "expectedModules": ["NavigationSystem"],
        "expectedErrorSubkind": "C1083_MISSING_INCLUDE",
        "notes": "UE 5.8 local fixture; expected fix is adding NavigationSystem to the owner Build.cs.",
    },
    {
        "id": "local_levelsequence_missing_module",
        "category": "LevelSequence dependency issue",
        "mode": "module_fix",
        "errorLog": "fatal error C1083: Cannot open include file: 'LevelSequence.h': No such file or directory",
        "expectedFilesToRead": ["Source/<Module>/<Module>.Build.cs", "failing sequence source/header"],
        "expectedPatchTargets": ["owner Build.cs"],
        "forbiddenPatchTargets": ["unrelated cinematic rewrite"],
        "expectedModules": ["LevelSequence"],
        "expectedErrorSubkind": "C1083_MISSING_INCLUDE",
        "notes": "UE 5.8 local fixture; expected fix is adding LevelSequence. Replace if live UBT proves this case needs multiple modules.",
    },
    {
        "id": "local_blueprint_native_event_missing_implementation",
        "category": "BlueprintNativeEvent signature issue",
        "mode": "reflection_fix",
        "errorLog": "BlueprintNativeEvent function OnHoldoutEvent_Implementation not found for declaration",
        "expectedFilesToRead": ["header declaration", "matching cpp file"],
        "expectedPatchTargets": ["matching cpp/header"],
        "forbiddenPatchTargets": ["unrelated interface rewrite"],
        "expectedErrorSubkind": "BLUEPRINT_NATIVE_EVENT_IMPL_MISSING",
        "notes": "UE 5.8 local fixture; expected fix is adding or correcting the _Implementation definition.",
    },
    {
        "id": "local_editor_only_runtime_boundary",
        "category": "editor-only include in runtime module",
        "mode": "module_fix",
        "errorLog": "Editor-only UnrealEd include is referenced from a runtime module source file",
        "expectedFilesToRead": ["failing runtime source/header", "module Build.cs"],
        "expectedPatchTargets": ["failing file", "module boundary files"],
        "forbiddenPatchTargets": ["adding UnrealEd to runtime module as default fix"],
        "expectedErrorSubkind": "EDITOR_ONLY_INCLUDE_IN_RUNTIME_MODULE",
        "notes": "UE 5.8 local fixture; expected fix should remove/guard editor-only runtime usage, not blindly add UnrealEd.",
    },
]


def detect_ubt_path() -> Path | None:
    """Return the UE 5.8 UBT path if present; never fail if absent."""
    base = Path("C:/") / "Program Files" / "Epic Games"
    candidate = base / "UE_5.8" / "Engine" / "Binaries" / "DotNET" / "UnrealBuildTool" / "UnrealBuildTool.exe"
    if candidate.is_file():
        return candidate
    return None


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def build_suite_config(data: dict[str, Any], suite: str = "5") -> dict[str, Any]:
    out = json.loads(json.dumps(data))
    if str(suite) == "5":
        return out
    if str(suite) != "12":
        raise ValueError("--suite must be 5 or 12")
    existing = {str(case.get("id")) for case in out.get("cases") or []}
    additions = [case for case in EXPANSION_CASES_12 if case["id"] not in existing]
    out.setdefault("cases", []).extend(json.loads(json.dumps(additions)))
    out["suite"] = "real-project-holdout-local-v0-12"
    out["description"] = (
        "UE 5.8 local 12-case holdout config. Fixture directories are local-only and ignored; "
        "do not commit this .local.json file."
    )
    out["engineVersion"] = "5.8"
    return out


def update_config(
    data: dict[str, Any],
    *,
    project_file: str = "",
    fixture_root: str = "",
    suite_name: str = DEFAULT_SUITE_NAME,
    suite: str = "5",
) -> dict[str, Any]:
    out = build_suite_config(data, suite=suite)
    if suite_name:
        out["suite"] = suite_name if str(suite) == "5" else f"{suite_name}-12"
    out["engineVersion"] = "5.8"
    fixture_root = fixture_root.rstrip("/\\")
    for case in out.get("cases") or []:
        case["engineVersion"] = "5.8"
        if project_file:
            case["projectFile"] = project_file
        if fixture_root:
            case["fixtureDir"] = f"{fixture_root}/{case.get('id')}"
        case.setdefault("projectFile", "HoldoutFixture.uproject")
        case.setdefault("target", "HoldoutFixtureEditor Win64 Development")
        case.setdefault("requestFile", "request.txt")
        if str(suite) == "12" and str(case.get("target") or "").startswith("<TARGET_NAME>"):
            case["target"] = "HoldoutFixtureEditor Win64 Development"
        if str(suite) == "12" and str(case.get("projectFile") or "").startswith("<PATH_TO_PROJECT>"):
            case["projectFile"] = "HoldoutFixture.uproject"
    return out


def write_local_config(
    *,
    example_path: Path = DEFAULT_EXAMPLE,
    output_path: Path = DEFAULT_OUTPUT,
    project_file: str = "",
    fixture_root: str = "",
    suite_name: str = DEFAULT_SUITE_NAME,
    suite: str = "5",
    force: bool = False,
) -> dict[str, Any]:
    if output_path.exists() and not force:
        raise FileExistsError(f"{output_path} already exists; use --force to overwrite")
    data = update_config(
        load_json(example_path),
        project_file=project_file,
        fixture_root=fixture_root,
        suite_name=suite_name,
        suite=suite,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _copy_base_fixture(fixture_dir: Path) -> None:
    if not BASE_FIXTURE.is_dir():
        raise FileNotFoundError(f"base fixture missing: {BASE_FIXTURE}")
    fixture_dir.mkdir(parents=True, exist_ok=True)
    for item in BASE_FIXTURE.iterdir():
        dest = fixture_dir / item.name
        if item.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)


def _source_paths(fixture_dir: Path, stem: str) -> tuple[Path, Path]:
    module = fixture_dir / "Source" / "HoldoutFixture"
    return module / "Public" / f"{stem}.h", module / "Private" / f"{stem}.cpp"


def write_fixture_case(case_id: str, fixture_root: Path = DEFAULT_FIXTURE_ROOT) -> Path:
    fixture_dir = fixture_root / case_id
    _copy_base_fixture(fixture_dir)
    writers = {
        "local_umg_missing_module": _write_umg_fixture,
        "local_niagara_missing_module": _write_niagara_fixture,
        "local_aimodule_missing_module": _write_ai_fixture,
        "local_navigation_system_missing_module": _write_navigation_fixture,
        "local_levelsequence_missing_module": _write_levelsequence_fixture,
        "local_blueprint_native_event_missing_implementation": _write_blueprint_native_event_fixture,
        "local_editor_only_runtime_boundary": _write_editor_only_fixture,
    }
    writer = writers.get(case_id)
    if not writer:
        return fixture_dir
    writer(fixture_dir)
    return fixture_dir


def write_fixture_cases(case_ids: list[str], fixture_root: Path = DEFAULT_FIXTURE_ROOT) -> list[Path]:
    return [write_fixture_case(case_id, fixture_root) for case_id in case_ids]


def _write_umg_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutWidgetHostComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "Blueprint/UserWidget.h"
#include "HoldoutWidgetHostComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutWidgetHostComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category="Holdout")
	TSubclassOf<UUserWidget> WidgetClass;
};
""",
    )
    _write(cpp, '#include "HoldoutWidgetHostComponent.h"\n')
    _write(
        fixture_dir / "request.txt",
        "Public header uses UUserWidget. Add the missing UMG module dependency to HoldoutFixture.Build.cs.\n",
    )


def _write_niagara_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutNiagaraHostComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "NiagaraComponent.h"
#include "HoldoutNiagaraHostComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutNiagaraHostComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category="Holdout")
	TObjectPtr<UNiagaraComponent> NiagaraComponent;
};
""",
    )
    _write(cpp, '#include "HoldoutNiagaraHostComponent.h"\n')
    _write(
        fixture_dir / "request.txt",
        "Public header uses UNiagaraComponent. Add the missing Niagara module dependency to HoldoutFixture.Build.cs.\n",
    )


def _write_ai_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutAIProbeComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "AIController.h"
#include "HoldoutAIProbeComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutAIProbeComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category="Holdout")
	TObjectPtr<AAIController> CachedController;
};
""",
    )
    _write(cpp, '#include "HoldoutAIProbeComponent.h"\n')
    _write(
        fixture_dir / "request.txt",
        "Public header uses AAIController. Add the missing AIModule dependency to HoldoutFixture.Build.cs.\n",
    )


def _write_navigation_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutNavigationProbeComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "HoldoutNavigationProbeComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutNavigationProbeComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UFUNCTION(BlueprintCallable, Category="Holdout")
	bool HasNavigationSystem() const;
};
""",
    )
    _write(
        cpp,
        """#include "HoldoutNavigationProbeComponent.h"
#include "NavigationSystem.h"

bool UHoldoutNavigationProbeComponent::HasNavigationSystem() const
{
	return UNavigationSystemV1::GetCurrent(GetWorld()) != nullptr;
}
""",
    )
    _write(
        fixture_dir / "request.txt",
        "Source uses UNavigationSystemV1 from NavigationSystem.h. Add the missing NavigationSystem module dependency to HoldoutFixture.Build.cs.\n",
    )


def _write_levelsequence_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutLevelSequenceProbeComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "LevelSequence.h"
#include "HoldoutLevelSequenceProbeComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutLevelSequenceProbeComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere, BlueprintReadOnly, Category="Holdout")
	TObjectPtr<ULevelSequence> Sequence;
};
""",
    )
    _write(cpp, '#include "HoldoutLevelSequenceProbeComponent.h"\n')
    _write(
        fixture_dir / "request.txt",
        "Public header uses ULevelSequence. Add the missing LevelSequence module dependency to HoldoutFixture.Build.cs. If live UBT proves this needs extra modules, report the blocker instead of inventing unrelated code.\n",
    )


def _write_blueprint_native_event_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutNativeEventComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "HoldoutNativeEventComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutNativeEventComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UFUNCTION(BlueprintNativeEvent, Category="Holdout")
	void OnHoldoutEvent();
};
""",
    )
    _write(cpp, '#include "HoldoutNativeEventComponent.h"\n')
    _write(
        fixture_dir / "request.txt",
        "Fix the BlueprintNativeEvent implementation gap. Add the missing OnHoldoutEvent_Implementation definition in the matching cpp file.\n",
    )


def _write_editor_only_fixture(fixture_dir: Path) -> None:
    header, cpp = _source_paths(fixture_dir, "HoldoutEditorBoundaryComponent")
    _write(
        header,
        """#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "UnrealEd.h"
#include "HoldoutEditorBoundaryComponent.generated.h"

UCLASS(ClassGroup=(Holdout), meta=(BlueprintSpawnableComponent))
class HOLDOUTFIXTURE_API UHoldoutEditorBoundaryComponent : public UActorComponent
{
	GENERATED_BODY()
};
""",
    )
    _write(cpp, '#include "HoldoutEditorBoundaryComponent.h"\n')
    _write(
        fixture_dir / "request.txt",
        "Fix the runtime module boundary error caused by editor-only UnrealEd usage. Do not blindly add UnrealEd to the runtime module; remove or guard the editor-only dependency.\n",
    )


def next_step_text(output_path: Path, model: str = DEFAULT_MODEL, ubt_path: Path | None = None) -> str:
    config = output_path.as_posix()
    ubt_arg = str(ubt_path) if ubt_path else "<UnrealBuildTool.exe>"
    return "\n".join(
        [
            "Next steps:",
            f"python scripts/validate_holdout_cases.py --config {config} --allow-local-paths",
            "python scripts/build_symbol_graph.py",
            f"python scripts/eval_pass_at_k.py --metrics-only --config {config}",
            (
                "python scripts/eval_pass_at_k.py --live --require-live "
                f"--config {config} --model {model} --ubt-path \"{ubt_arg}\" --wrapper-timeout 1800"
            ),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap ignored local live holdout config.")
    parser.add_argument("--suite", choices=["5", "12"], default="5", help="Local holdout suite size to prepare.")
    parser.add_argument("--project-file", default="", help="Path to the local .uproject for all generated cases.")
    parser.add_argument("--fixture-root", default="", help="Root containing one fixture directory per case id.")
    parser.add_argument("--suite-name", default=DEFAULT_SUITE_NAME)
    parser.add_argument("--force", action="store_true", help="Overwrite existing local config.")
    parser.add_argument("--example-config", type=Path, default=DEFAULT_EXAMPLE)
    parser.add_argument("--output-config", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    fixture_root = args.fixture_root
    project_file = args.project_file
    if args.suite == "12":
        fixture_root = fixture_root or DEFAULT_FIXTURE_ROOT.as_posix()
        project_file = project_file or "HoldoutFixture.uproject"

    try:
        data = write_local_config(
            example_path=args.example_config,
            output_path=args.output_config,
            project_file=project_file,
            fixture_root=fixture_root,
            suite_name=args.suite_name,
            suite=args.suite,
            force=args.force,
        )
        created = []
        if args.suite == "12":
            created = write_fixture_cases(
                [str(case["id"]) for case in EXPANSION_CASES_12],
                Path(fixture_root),
            )
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"failed to bootstrap local holdout config: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {args.output_config}")
    if args.suite == "12":
        print(f"Prepared {len(created)} local fixture directories under {fixture_root}")
    detected_ubt = detect_ubt_path()
    if detected_ubt:
        print(f"Detected UBT candidate: {detected_ubt}")
    else:
        print("UE 5.8 UBT candidate not detected; pass --ubt-path explicitly for live eval.")
    print(next_step_text(args.output_config, ubt_path=detected_ubt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
