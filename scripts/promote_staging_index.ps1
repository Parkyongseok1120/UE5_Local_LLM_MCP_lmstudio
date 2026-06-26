$ErrorActionPreference = "Stop"

$dataDir = Join-Path $PSScriptRoot "..\data\unreal58" | Resolve-Path
$main = Join-Path $dataDir "rag.sqlite"
$staging = Join-Path $dataDir "rag.staging.sqlite"
$embeddings = Join-Path $dataDir "rag.embeddings.sqlite"

if (-not (Test-Path $staging)) {
    Write-Host "No staging index found: $staging"
    exit 0
}

$stagingNewer = $false
if (Test-Path $main) {
    $stagingNewer = (Get-Item $staging).LastWriteTime -gt (Get-Item $main).LastWriteTime
}
else {
    $stagingNewer = $true
}

if (-not $stagingNewer) {
    Write-Host "Main index is already newer than staging. Nothing to promote."
    exit 0
}

if (Test-Path $main) {
    $backup = Join-Path $dataDir ("rag.sqlite.bak-{0}" -f (Get-Date -Format "yyyyMMddHHmmss"))
    try {
        Move-Item -LiteralPath $main -Destination $backup -Force
        Write-Host "Backed up main index to $backup"
    }
    catch {
        Write-Host "Could not replace locked rag.sqlite. Close LM Studio/Cursor and rerun."
        Write-Host $_.Exception.Message
        exit 1
    }
}

Move-Item -LiteralPath $staging -Destination $main -Force
Write-Host "Promoted staging -> rag.sqlite"

if (Test-Path $embeddings) {
    $embedBackup = Join-Path $dataDir ("rag.embeddings.sqlite.bak-{0}" -f (Get-Date -Format "yyyyMMddHHmmss"))
    Move-Item -LiteralPath $embeddings -Destination $embedBackup -Force
    Write-Host "Backed up old embeddings to $embedBackup"
}

$workspace = Split-Path $PSScriptRoot -Parent
Push-Location $workspace
try {
    powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 build-embeddings
}
finally {
    Pop-Location
}

Write-Host "Phase 0 index promotion complete."
