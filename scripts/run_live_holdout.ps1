# Live 36-case holdout runner with progress monitoring.
# USER-TRIGGERED ONLY: do not auto-start from implementation gates; run when explicitly requested.
# Default holdout gate is scripts/run_dryrun_holdout.ps1 (golden/ + UBT, no LM Studio).
param(
    [string]$Model = "qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max",
    [int]$AbortAfterConsecutiveFailures = 5,
    [int]$WrapperTimeout = 1800,
    [string]$UbtPath = ""
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

Write-Host "Preflight: checking LM Studio at http://localhost:1234/v1 for model '$Model'..."
Write-Host "Restart LM Studio and reload the chat model before live eval if the server has been running for many hours."
$preflightJson = & $py scripts/preflight_lmstudio.py --url "http://localhost:1234/v1" --model $Model --json 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "LM Studio preflight failed. Restart LM Studio, load '$Model', then retry.`n$preflightJson"
}
try {
    $preflight = $preflightJson | ConvertFrom-Json
} catch {
    throw "LM Studio preflight returned invalid JSON: $preflightJson"
}
if (-not $preflight.ok) {
    $err = if ($preflight.error) { $preflight.error } else { "model not available" }
    throw "LM Studio preflight failed: $err. Loaded models: $($preflight.modelCount). Expected: $Model"
}
$resolvedModel = [string]$preflight.resolvedModel
if ($resolvedModel -and ($resolvedModel -ne $Model) -and ($Model -notin @("", "local-model", "local", "default"))) {
    Write-Warning "Requested model '$Model' resolved to loaded model '$resolvedModel'."
}
Write-Host "LM Studio OK ($($preflight.modelCount) models, resolved: $resolvedModel)"

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"
$env:UNREAL_RAG_MODEL_PROFILE = "qwen3_6_27b"

$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$runDir = Join-Path $Root "data\baseline\live_holdout\$ts"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

$config = Join-Path $Root "config\rag_eval_real_project_holdout_cases.local.json"
$evalLog = Join-Path $runDir "eval.log"
$evalErr = Join-Path $runDir "eval.err"
$progressLog = Join-Path $runDir "progress.log"
$monitorLog = Join-Path $runDir "monitor.log"
$kpiOut = Join-Path $runDir "kpi.json"

Write-Host "Run directory: $runDir"

$evalProc = Start-Process -FilePath $py `
    -ArgumentList @(
        '-u'
        "`"$(Join-Path $Root 'scripts\eval_pass_at_k.py')`""
        '--live'
        '--require-live'
        '--config'
        "`"$config`""
        '--model'
        $Model
        '--ubt-path'
        "`"$ubt`""
        '--wrapper-timeout'
        "$WrapperTimeout"
        '--abort-after-consecutive-failures'
        "$AbortAfterConsecutiveFailures"
        '--artifact-dir'
        "`"$runDir`""
        '--output'
        "`"$kpiOut`""
    ) `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $evalLog `
    -RedirectStandardError $evalErr `
    -PassThru `
    -WindowStyle Hidden

$monitorProc = Start-Process -FilePath $py `
    -ArgumentList @(
        "`"$(Join-Path $Root 'scripts\monitor_holdout_progress.py')`""
        '--artifact-dir'
        "`"$runDir`""
        '--progress-file'
        "`"$progressLog`""
        '--poll-seconds'
        '30'
        '--eval-pid'
        "$($evalProc.Id)"
    ) `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $monitorLog `
    -RedirectStandardError (Join-Path $runDir "monitor.err") `
    -PassThru `
    -WindowStyle Hidden

@{
    runDir = $runDir
    evalPid = $evalProc.Id
    monitorPid = $monitorProc.Id
    model = $Model
    startedAt = (Get-Date).ToString("o")
} | ConvertTo-Json | Set-Content -Path (Join-Path $runDir "run_meta.json") -Encoding UTF8

Write-Host "Started eval PID $($evalProc.Id), monitor PID $($monitorProc.Id)"
Write-Host "Tail progress: Get-Content '$progressLog' -Wait"
