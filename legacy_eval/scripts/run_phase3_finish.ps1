# Archived evaluation phase runner.
$ErrorActionPreference = "Stop"
$workspace = Split-Path $PSScriptRoot -Parent
. (Join-Path $workspace "scripts\installer_support\Install-PathHelpers.ps1")
$ragPaths = Get-RagDataPaths -RagRoot $workspace
$baselineDir = Join-Path $workspace "data\baseline"
New-Item -ItemType Directory -Force -Path $baselineDir | Out-Null
$stamp = Get-Date -Format "yyyyMMddHHmmss"
$log = Join-Path $baselineDir "phase3-$stamp.txt"

function Log($text) { $text | Tee-Object -FilePath $log -Append }

Push-Location $workspace
try {
    Log "=== Phase 3 finish $stamp ==="
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\sync_active_project.ps1 2>&1 | ForEach-Object { Log $_ }
    powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 build-embeddings-full 2>&1 | ForEach-Object { Log $_ }
    & {
        $py = (Get-Command python -ErrorAction SilentlyContinue).Source
        if (-not $py) { $py = Join-Path $HOME ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" }
        & $py scripts\rag_embeddings.py --index $ragPaths.IndexPath --status
    } 2>&1 | ForEach-Object { Log $_ }
    powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 eval-unreal-programming 2>&1 | ForEach-Object { Log $_ }
    Log "Phase 3 log: $log"
}
finally {
    Pop-Location
}
