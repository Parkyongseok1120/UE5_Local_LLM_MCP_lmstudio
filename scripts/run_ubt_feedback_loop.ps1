param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectFile,

    [Parameter(Mandatory = $true)]
    [string]$Target,

    [string]$Platform = "Win64",
    [string]$Configuration = "Development",
    [string]$UbtPath = "",
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

function Resolve-UbtPath {
    param([string]$Workspace)
    if ($UbtPath) { return $UbtPath }
    if ($env:UNREAL_UBT_PATH) { return $env:UNREAL_UBT_PATH }
    if ($env:UNREAL_ENGINE_ROOT) {
        return (Join-Path $env:UNREAL_ENGINE_ROOT "Engine\Binaries\DotNET\UnrealBuildTool\UnrealBuildTool.exe")
    }
    $workspaceConfig = Join-Path $Workspace "config\workspace.json"
    if (Test-Path -LiteralPath $workspaceConfig) {
        try {
            $cfg = Get-Content -LiteralPath $workspaceConfig -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($cfg.defaultEngineRoot) {
                return (Join-Path ([string]$cfg.defaultEngineRoot) "Engine\Binaries\DotNET\UnrealBuildTool\UnrealBuildTool.exe")
            }
        }
        catch {
        }
    }
    return "UnrealBuildTool.exe"
}

$projectPath = (Resolve-Path -LiteralPath $ProjectFile).Path
$projectRoot = Split-Path -Parent $projectPath
$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$py = Find-Python
$resolvedUbtPath = Resolve-UbtPath -Workspace $workspace

Set-Location -LiteralPath $workspace

$logDir = Join-Path $workspace "data\unreal58\Logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$lastLog = Join-Path $logDir "ubt_feedback_last.log"

if (-not $SkipBuild) {
    if (-not (Test-Path -LiteralPath $resolvedUbtPath)) {
        throw "UnrealBuildTool not found: $resolvedUbtPath. Set -UbtPath, UNREAL_UBT_PATH, or UNREAL_ENGINE_ROOT."
    }

    Write-Host "Running UnrealBuildTool..."
    & $resolvedUbtPath $Target $Platform $Configuration "-Project=$projectPath" -WaitMutex 2>&1 |
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

