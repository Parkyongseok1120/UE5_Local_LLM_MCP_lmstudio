param(
    [ValidateSet("shooter", "action_combat", "platformer")]
    [string]$Genre = "shooter",
    [string]$ModuleName = "PrototypeModule",
    [string]$OutputRoot = "",
    [string]$EngineAssociation = ""
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

function Get-ScaffoldEngineAssociation {
    param([string]$RequestedAssociation)

    $requested = ([string]$RequestedAssociation).Trim()
    if ($requested) {
        return $requested
    }

    # Reuse the selected project's exact association when one is configured.
    # If no project is selected, omit EngineAssociation rather than guessing a
    # version or the newest installed engine.
    $configPath = ([string]$env:SHARED_UNREAL_CONFIG).Trim()
    if (-not $configPath) {
        $configPath = Join-Path $HOME ".lmstudio/config/unreal-workspace.json"
    }
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        return ""
    }
    try {
        $sharedConfig = Get-Content -LiteralPath $configPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        $activeProject = ([string]$sharedConfig.activeProject).Trim()
        if (-not $activeProject -or -not (Test-Path -LiteralPath $activeProject -PathType Leaf)) {
            return ""
        }
        $descriptor = Get-Content -LiteralPath $activeProject -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        return ([string]$descriptor.EngineAssociation).Trim()
    }
    catch {
        return ""
    }
}

$resolvedEngineAssociation = Get-ScaffoldEngineAssociation $EngineAssociation

$buildCs = @"
using UnrealBuildTool;
public class $ModuleName : ModuleRules
{
    public $ModuleName(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PublicDependencyModuleNames.AddRange(new string[] { "Core", "CoreUObject", "Engine", "InputCore" });
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

$uprojectDescriptor = [ordered]@{
    FileVersion = 3
    Category = ""
    Description = "Scaffold $Genre prototype"
    Modules = @(
        [ordered]@{ Name = $ModuleName; Type = "Runtime"; LoadingPhase = "Default" }
    )
}
if ($resolvedEngineAssociation) {
    $uprojectDescriptor["EngineAssociation"] = $resolvedEngineAssociation
}
$uproject = $uprojectDescriptor | ConvertTo-Json -Depth 6
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
        ExtraModuleNames.Add("$ModuleName");
    }
}
"@
$sourceRoot = Join-Path $OutputRoot "Source"
Set-Content -Path (Join-Path $sourceRoot "$ModuleName.Target.cs") -Value $targetGame -Encoding UTF8
Set-Content -Path (Join-Path $sourceRoot "${ModuleName}Editor.Target.cs") -Value $targetEditor -Encoding UTF8

Write-Host "Scaffold created: $OutputRoot"
if ($resolvedEngineAssociation) {
    Write-Host "Engine association: $resolvedEngineAssociation"
}
else {
    Write-Host "Engine association: omitted (pass -EngineAssociation to bind a new project explicitly)."
}
Write-Host "Next: open the uproject in its selected Unreal Engine and run build_unreal_project via unreal-agent."
