# Live 36-case holdout runner with progress monitoring.
param(
    [string]$Model = "qwen3.6-27b-heretic-uncensored-finetune-neo-code-di-imatrix-max",
    [int]$AbortAfterConsecutiveFailures = 5,
    [int]$WrapperTimeout = 1800
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$py = "C:\Users\sster\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if (-not (Test-Path $py)) {
    throw "Python not found at $py"
}

$ubt = "C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\DotNET\UnrealBuildTool\UnrealBuildTool.exe"
if (-not (Test-Path $ubt)) {
    throw "UBT not found at $ubt"
}

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
