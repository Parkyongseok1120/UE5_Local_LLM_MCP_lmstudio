#!/usr/bin/env python
"""Generate ceiling module-dependency compile-fix fixtures (one-shot maintainer script)."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CEILING = ROOT / "tests" / "fixtures" / "compile_fix_ceiling"

CASES = [
    {
        "id": "missing_aimodule_dep",
        "module": "CompileFixAI",
        "ue_module": "AIModule",
        "header": """#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "AIController.h"
#include "AIComponent.generated.h"

UCLASS()
class COMPILEFIXAI_API UAIComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere)
	AAIController* CachedController;
};
""",
        "cpp": """#include "AIComponent.h"
""",
        "log": """UBT compile failed. Diagnose from the log and apply the smallest fix.

Log excerpt:
  error C1083: Cannot open include file: 'AIController.h'
  error C2027: use of undefined type 'AAIController'

Do not explain only — return the patched file(s).
""",
    },
    {
        "id": "missing_umg_dep",
        "module": "CompileFixUMG",
        "ue_module": "UMG",
        "header": """#pragma once
#include "CoreMinimal.h"
#include "Blueprint/UserWidget.h"
#include "MenuWidget.generated.h"

UCLASS()
class COMPILEFIXUMG_API UMenuWidget : public UUserWidget
{
	GENERATED_BODY()
};
""",
        "cpp": """#include "MenuWidget.h"
""",
        "log": """UBT compile failed. Diagnose from the log and apply the smallest fix.

Log excerpt:
  error C1083: Cannot open include file: 'Blueprint/UserWidget.h'
  error C2504: 'UUserWidget': base class undefined

Do not explain only — return the patched file(s).
""",
    },
    {
        "id": "missing_niagara_dep",
        "module": "CompileFixNiagara",
        "ue_module": "Niagara",
        "header": """#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "NiagaraSystem.h"
#include "NiagaraHost.generated.h"

UCLASS()
class COMPILEFIXNIAGARA_API UNiagaraHost : public UActorComponent
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere)
	UNiagaraSystem* BurstSystem;
};
""",
        "cpp": """#include "NiagaraHost.h"
""",
        "log": """UBT compile failed. Diagnose from the log and apply the smallest fix.

Log excerpt:
  error C1083: Cannot open include file: 'NiagaraSystem.h'
  error C2027: use of undefined type 'UNiagaraSystem'

Do not explain only — return the patched file(s).
""",
    },
    {
        "id": "missing_navigation_system_dep",
        "module": "CompileFixNav",
        "ue_module": "NavigationSystem",
        "header": """#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "NavigationSystem.h"
#include "NavProbe.generated.h"

UCLASS()
class COMPILEFIXNAV_API UNavProbe : public UActorComponent
{
	GENERATED_BODY()

public:
	void ProbeNav();
};
""",
        "cpp": """#include "NavProbe.h"

void UNavProbe::ProbeNav()
{
	if (UWorld* World = GetWorld())
	{
		UNavigationSystemV1::GetCurrent(World);
	}
}
""",
        "log": """UBT compile failed. Diagnose from the log and apply the smallest fix.

Log excerpt:
  error C1083: Cannot open include file: 'NavigationSystem.h'
  error C2653: 'UNavigationSystemV1': is not a class or namespace name

Do not explain only — return the patched file(s).
""",
    },
    {
        "id": "missing_inputcore_dep",
        "module": "CompileFixIC",
        "ue_module": "InputCore",
        "header": """#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "InputCoreTypes.h"
#include "InputProbe.generated.h"

UCLASS()
class COMPILEFIXIC_API UInputProbe : public UActorComponent
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere)
	FKey ActionKey;
};
""",
        "cpp": """#include "InputProbe.h"
""",
        "log": """UBT compile failed. Diagnose from the log and apply the smallest fix.

Log excerpt:
  error C1083: Cannot open include file: 'InputCoreTypes.h'
  error C2079: 'UInputProbe::ActionKey' uses undefined struct 'FKey'

Do not explain only — return the patched file(s).
""",
    },
    {
        "id": "missing_slate_dep",
        "module": "CompileFixSlate",
        "ue_module": "Slate",
        "header": """#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "Styling/SlateBrush.h"
#include "SlateProbe.generated.h"

UCLASS()
class COMPILEFIXSLATE_API USlateProbe : public UActorComponent
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere)
	FSlateBrush IconBrush;
};
""",
        "cpp": """#include "SlateProbe.h"
""",
        "log": """UBT compile failed. Diagnose from the log and apply the smallest fix.

Log excerpt:
  error C1083: Cannot open include file: 'Styling/SlateBrush.h'
  error C2079: uses undefined struct 'FSlateBrush'

Do not explain only — return the patched file(s).
""",
    },
    {
        "id": "missing_movie_scene_dep",
        "module": "CompileFixMS",
        "ue_module": "MovieScene",
        "header": """#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "MovieScene.h"
#include "MovieSceneProbe.generated.h"

UCLASS()
class COMPILEFIXMS_API UMovieSceneProbe : public UActorComponent
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere)
	UMovieScene* TrackedScene;
};
""",
        "cpp": """#include "MovieSceneProbe.h"
""",
        "log": """UBT compile failed. Diagnose from the log and apply the smallest fix.

Log excerpt:
  error C1083: Cannot open include file: 'MovieScene.h'
  error C2027: use of undefined type 'UMovieScene'

Do not explain only — return the patched file(s).
""",
    },
    {
        "id": "missing_levelsequence_dep",
        "module": "CompileFixLS",
        "ue_module": "LevelSequence",
        "header": """#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "LevelSequence.h"
#include "LevelSequenceProbe.generated.h"

UCLASS()
class COMPILEFIXLS_API ULevelSequenceProbe : public UActorComponent
{
	GENERATED_BODY()

public:
	UPROPERTY(EditAnywhere)
	ULevelSequence* SequenceAsset;
};
""",
        "cpp": """#include "LevelSequenceProbe.h"
""",
        "log": """UBT compile failed. Diagnose from the log and apply the smallest fix.

Log excerpt:
  error C1083: Cannot open include file: 'LevelSequence.h'
  error C2027: use of undefined type 'ULevelSequence'

Do not explain only — return the patched file(s).
""",
    },
]


def write_build_cs(module: str, extra_modules: list[str]) -> str:
    deps = ["Core", "CoreUObject", "Engine", *extra_modules]
    quoted = ", ".join(f'"{name}"' for name in deps)
    return f"""using UnrealBuildTool;

public class {module} : ModuleRules
{{
	public {module}(ReadOnlyTargetRules Target) : base(Target)
	{{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
		PublicDependencyModuleNames.AddRange(new string[] {{ {quoted} }});
	}}
}}
"""


def write_target(module: str, *, editor: bool) -> str:
    target = f"{module}Editor" if editor else module
    kind = "Editor" if editor else "Game"
    extra = "\n\t\tbOverrideBuildEnvironment = true;" if editor else ""
    return f"""using UnrealBuildTool;

public class {target}Target : TargetRules
{{
	public {target}Target(TargetInfo Target) : base(Target)
	{{
		Type = TargetType.{kind};
		DefaultBuildSettings = BuildSettingsVersion.V5;
		IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
		ExtraModuleNames.Add("{module}");{extra}
	}}
}}
"""


def write_uproject(module: str, description: str) -> str:
    return (
        "{\n"
        '\t"FileVersion": 3,\n'
        '\t"EngineAssociation": "5.8",\n'
        '\t"Category": "",\n'
        f'\t"Description": "{description}",\n'
        '\t"Modules": [\n'
        "\t\t{\n"
        f'\t\t\t"Name": "{module}",\n'
        '\t\t\t"Type": "Runtime",\n'
        '\t\t\t"LoadingPhase": "Default"\n'
        "\t\t}\n"
        "\t]\n"
        "}\n"
    )


def generate_case(case: dict) -> None:
    case_id = case["id"]
    module = case["module"]
    ue_module = case["ue_module"]
    root = CEILING / case_id
    if "class " in case["header"]:
        for line in case["header"].splitlines():
            if line.strip().startswith("class ") and "API" in line:
                break
    # derive public header filename from generated include
    gen_line = next(line for line in case["header"].splitlines() if ".generated.h" in line)
    header_file = gen_line.split('"')[1]

    (root / f"{module}.uproject").write_text(
        write_uproject(module, f"Ceiling eval — {case_id}"), encoding="utf-8"
    )
    (root / "Source" / f"{module}.Target.cs").write_text(write_target(module, editor=False), encoding="utf-8")
    (root / "Source" / f"{module}Editor.Target.cs").write_text(write_target(module, editor=True), encoding="utf-8")
    mod_dir = root / "Source" / module
    (mod_dir / f"{module}.Build.cs").write_text(write_build_cs(module, []), encoding="utf-8")
    (mod_dir / "Public" / header_file).write_text(case["header"], encoding="utf-8")
    cpp_name = header_file.replace(".h", ".cpp")
    (mod_dir / "Private" / cpp_name).write_text(case["cpp"], encoding="utf-8")
    (root / "request_log_only.txt").write_text(case["log"], encoding="utf-8")
    golden_build = root / "golden" / "Source" / module / f"{module}.Build.cs"
    golden_build.parent.mkdir(parents=True, exist_ok=True)
    golden_build.write_text(write_build_cs(module, [ue_module]), encoding="utf-8")


def copy_gameplaytags_fixture() -> None:
    import shutil

    src = ROOT / "tests" / "fixtures" / "compile_fix" / "missing_gameplaytags_dep"
    dst = CEILING / "missing_gameplaytags_dep"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("golden"))
    golden_build = dst / "golden" / "Source" / "CompileFixTags" / "CompileFixTags.Build.cs"
    golden_build.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        src / "golden" / "Source" / "CompileFixTags" / "CompileFixTags.Build.cs",
        golden_build,
    )
    (dst / "request_log_only.txt").write_text(
        """UBT compile failed. Diagnose from the log and apply the smallest fix.

Log excerpt:
  error C2065: 'FGameplayTagContainer': undeclared identifier
    Source/CompileFixTags/Public/TaggedActorComponent.h(15)

Do not explain only — return the patched file(s).
""",
        encoding="utf-8",
    )


def rename_enhancedinput() -> None:
    import shutil

    old = CEILING / "missing_enhanced_input_dep"
    new = CEILING / "missing_enhancedinput_dep"
    if old.is_dir() and not new.is_dir():
        shutil.move(str(old), str(new))


def main() -> None:
    copy_gameplaytags_fixture()
    rename_enhancedinput()
    for case in CASES:
        generate_case(case)

    config_cases = []
    for case_id, module in [
        ("missing_gameplaytags_dep", "CompileFixTags"),
        ("missing_enhancedinput_dep", "CompileFixEI"),
        *((c["id"], c["module"]) for c in CASES),
    ]:
        config_cases.append(
            {
                "id": case_id,
                "fixtureDir": f"tests/fixtures/compile_fix_ceiling/{case_id}",
                "projectFile": f"{module}.uproject",
                "target": f"{module}Editor",
                "mode": "module_fix",
                "requestFile": "request_log_only.txt",
            }
        )

    config_path = ROOT / "config" / "rag_eval_pass_at_k_ceiling_cases.json"
    payload = json.loads(config_path.read_text(encoding="utf-8-sig"))
    payload["cases"] = config_cases
    payload["defaults"]["minPassRate"] = 0.4
    config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(config_cases)} ceiling cases")


if __name__ == "__main__":
    main()
