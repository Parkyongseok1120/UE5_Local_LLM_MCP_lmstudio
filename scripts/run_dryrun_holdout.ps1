# Dry-run 36-case holdout gate (golden/ + UBT only; no LM Studio).
param(
    [string]$UbtPath = "",
    [string]$Config = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

function Find-Python {
    $bundled = Join-Path $HOME ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path $bundled) {
        return $bundled
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        $candidate = $python.Source
        if ($candidate -notlike "*WindowsApps*") {
            return $candidate
        }
    }

    throw "Python was not found. Install Python 3.10+ or set PATH."
}

function Resolve-UbtPath {
    if ($UbtPath) { return $UbtPath }
    if ($env:UNREAL_UBT_PATH) { return $env:UNREAL_UBT_PATH }
    if ($env:UNREAL_ENGINE_ROOT) {
        return (Join-Path $env:UNREAL_ENGINE_ROOT "Engine\Binaries\DotNET\UnrealBuildTool\UnrealBuildTool.exe")
    }
    $workspaceConfig = Join-Path $Root "config\workspace.json"
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

$py = Find-Python
$ubt = Resolve-UbtPath
if (-not (Test-Path -LiteralPath $ubt)) {
    throw "UnrealBuildTool not found: $ubt. Set -UbtPath, UNREAL_UBT_PATH, or UNREAL_ENGINE_ROOT."
}

if (-not $Config) {
    $Config = Join-Path $Root "config\rag_eval_real_project_holdout_cases.local.json"
}
if (-not (Test-Path -LiteralPath $Config)) {
    throw "Holdout config not found: $Config"
}

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"

$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$runDir = Join-Path $Root "data\baseline\dryrun_holdout\$ts"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

$kpiOut = Join-Path $runDir "kpi.json"
$evalLog = Join-Path $runDir "eval.log"

Write-Host "Dry-run holdout gate (no LM Studio)"
Write-Host "Run directory: $runDir"
Write-Host "Config: $Config"

& $py -u (Join-Path $Root "scripts\eval_pass_at_k.py") `
    --dry-run `
    --config $Config `
    --ubt-path $ubt `
    --output $kpiOut `
    2>&1 | Tee-Object -FilePath $evalLog

if ($LASTEXITCODE -ne 0) {
    throw "Dry-run holdout failed (exit $LASTEXITCODE). See $evalLog"
}

@{
    runDir = $runDir
    config = $Config
    kpiOut = $kpiOut
    mode = "dry-run"
    startedAt = (Get-Date).ToString("o")
} | ConvertTo-Json | Set-Content -Path (Join-Path $runDir "run_meta.json") -Encoding UTF8

Write-Host "Dry-run PASS. KPI: $kpiOut"
