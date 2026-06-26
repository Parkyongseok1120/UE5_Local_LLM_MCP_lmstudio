param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectFile,

    [Parameter(Mandatory = $true)]
    [string]$Target,

    [string]$Platform = "Win64",
    [string]$Configuration = "Development",
    [string]$UbtPath = "C:\Program Files\Epic Games\UE_5.7\Engine\Binaries\DotNET\UnrealBuildTool\UnrealBuildTool.exe",
    [string]$Question = "latest UnrealBuildTool compile error fix",
    [string]$Mode = "compile_fix",
    [switch]$SkipBuild,
    [switch]$SkipEval
)

$ErrorActionPreference = "Stop"

function Find-Python {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return $python.Source
    }

    $bundled = Join-Path $HOME ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path $bundled) {
        return $bundled
    }

    throw "Python was not found. Install Python 3.10+ or check the Codex bundled Python path."
}

$projectPath = (Resolve-Path -LiteralPath $ProjectFile).Path
$projectRoot = Split-Path -Parent $projectPath
$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$py = Find-Python

Set-Location -LiteralPath $workspace

$logDir = Join-Path $workspace "data\unreal58\Logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$lastLog = Join-Path $logDir "ubt_feedback_last.log"

if (-not $SkipBuild) {
    if (-not (Test-Path -LiteralPath $UbtPath)) {
        throw "UnrealBuildTool not found: $UbtPath"
    }

    Write-Host "Running UnrealBuildTool..."
    & $UbtPath $Target $Platform $Configuration "-Project=$projectPath" -WaitMutex 2>&1 |
        Tee-Object -FilePath $lastLog
    Write-Host "UBT log captured: $lastLog"
}

Write-Host "Collecting project profile..."
& $py scripts\collect_unreal_project_profile.py --root $projectPath --out data\unreal58\raw_project_profiles.jsonl

Write-Host "Collecting build logs..."
& $py scripts\collect_build_logs.py --root $projectRoot --root $logDir --out data\unreal58\raw_build_logs.jsonl --logs-only

Write-Host "Rebuilding RAG index..."
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 build

if (-not $SkipEval) {
    powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 eval-unreal-programming
}

if ($Question) {
    Write-Host "Querying RAG with mode: $Mode"
    powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 query -Mode $Mode -Question $Question
}

