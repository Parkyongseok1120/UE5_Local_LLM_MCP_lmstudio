$ErrorActionPreference = "Stop"
$workspace = Split-Path $PSScriptRoot -Parent
$baselineDir = Join-Path $workspace "data\baseline"
New-Item -ItemType Directory -Force -Path $baselineDir | Out-Null
$stamp = Get-Date -Format "yyyyMMddHHmmss"
$log = Join-Path $baselineDir "phase0-$stamp.txt"

function Log($text) {
    $text | Tee-Object -FilePath $log -Append
}

Push-Location $workspace
try {
    Log "=== Phase 0 baseline $stamp ==="
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\promote_staging_index.ps1 2>&1 | ForEach-Object { Log $_ }
    powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 collect-guidelines 2>&1 | ForEach-Object { Log $_ }
    powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 build-incremental 2>&1 | ForEach-Object { Log $_ }
    powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 test-unreal-readiness 2>&1 | ForEach-Object { Log $_ }
    powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 eval-unreal-programming 2>&1 | ForEach-Object { Log $_ }
    powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 eval-prototype 2>&1 | ForEach-Object { Log $_ }
    powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 eval-refactor 2>&1 | ForEach-Object { Log $_ }
    Log "Baseline log: $log"
}
finally {
    Pop-Location
}
