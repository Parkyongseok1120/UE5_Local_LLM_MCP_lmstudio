param(
    [ValidateSet("shooter", "action_combat", "platformer")]
    [string]$Genre = "shooter",
    [string]$ModuleName = "PrototypeModule",
    [string]$OutputRoot = ""
)

$ErrorActionPreference = "Stop"
$ragRoot = Split-Path $PSScriptRoot -Parent
if (-not $OutputRoot) {
    $OutputRoot = Join-Path $ragRoot "data\scaffold_runs\$Genre-$(Get-Date -Format 'yyyyMMdd_HHmmss')"
}
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
$source = Join-Path $OutputRoot "Source\$ModuleName"
New-Item -ItemType Directory -Force -Path (Join-Path $source "Public") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $source "Private") | Out-Null

$buildCs = @"
using UnrealBuildTool;
public class $ModuleName : ModuleRules
{
    public $ModuleName(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine", "InputCore", "EnhancedInput" });
    }
}
"@
Set-Content -Path (Join-Path $source "$ModuleName.Build.cs") -Value $buildCs -Encoding UTF8

$header = @"
#pragma once
#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "$($ModuleName)Component.generated.h"

UCLASS(ClassGroup=(Custom), meta=(BlueprintSpawnableComponent))
class $($ModuleName.ToUpper())_API U${ModuleName}Component : public UActorComponent
{
    GENERATED_BODY()
public:
    U${ModuleName}Component();
protected:
    virtual void BeginPlay() override;
};
"@
Set-Content -Path (Join-Path $source "Public\${ModuleName}Component.h") -Value $header -Encoding UTF8

$cpp = @"
#include "${ModuleName}Component.h"

U${ModuleName}Component::U${ModuleName}Component()
{
    PrimaryComponentTick.bCanEverTick = false;
}

void U${ModuleName}Component::BeginPlay()
{
    Super::BeginPlay();
}
"@
Set-Content -Path (Join-Path $source "Private\${ModuleName}Component.cpp") -Value $cpp -Encoding UTF8

$uproject = @"
{
    "FileVersion": 3,
    "EngineAssociation": "5.8",
    "Category": "",
    "Description": "Scaffold $Genre prototype",
    "Modules": [
        { "Name": "$ModuleName", "Type": "Runtime", "LoadingPhase": "Default" }
    ],
    "Plugins": [
        { "Name": "EnhancedInput", "Enabled": true }
    ]
}
"@
Set-Content -Path (Join-Path $OutputRoot "$ModuleName.uproject") -Value $uproject -Encoding UTF8

$moduleCpp = @"
#include "Modules/ModuleManager.h"
IMPLEMENT_PRIMARY_GAME_MODULE(FDefaultGameModuleImpl, $ModuleName, "$ModuleName");
"@
Set-Content -Path (Join-Path $source "$ModuleName.cpp") -Value $moduleCpp -Encoding UTF8

$targetGame = @"
using UnrealBuildTool;
using System.Collections.Generic;
public class ${ModuleName}Target : TargetRules
{
    public ${ModuleName}Target(TargetInfo Target) : base(Target)
    {
        Type = TargetType.Game;
        DefaultBuildSettings = BuildSettingsVersion.V7;
        IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
        ExtraModuleNames.Add("$ModuleName");
    }
}
"@
$targetEditor = @"
using UnrealBuildTool;
using System.Collections.Generic;
public class ${ModuleName}EditorTarget : TargetRules
{
    public ${ModuleName}EditorTarget(TargetInfo Target) : base(Target)
    {
        Type = TargetType.Editor;
        DefaultBuildSettings = BuildSettingsVersion.V7;
        IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
        ExtraModuleNames.Add("$ModuleName");
    }
}
"@
$sourceRoot = Join-Path $OutputRoot "Source"
Set-Content -Path (Join-Path $sourceRoot "$ModuleName.Target.cs") -Value $targetGame -Encoding UTF8
Set-Content -Path (Join-Path $sourceRoot "${ModuleName}Editor.Target.cs") -Value $targetEditor -Encoding UTF8

Write-Host "Scaffold created: $OutputRoot"
Write-Host "Next: open uproject in UE 5.8 and run build_unreal_project via unreal-agent."
