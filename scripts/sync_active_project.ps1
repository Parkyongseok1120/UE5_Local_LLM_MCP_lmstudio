$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "unreal_workspace_config.ps1")

$workspace = Split-Path $PSScriptRoot -Parent
$sharedConfig = Join-Path $HOME ".lmstudio\config\unreal-workspace.json"
if (-not (Test-Path $sharedConfig)) {
    throw "Shared config not found: $sharedConfig"
}

$config = Read-SharedConfig -Path $sharedConfig
$active = [string]$config.activeProject
if ([string]::IsNullOrWhiteSpace($active) -or -not (Test-Path -LiteralPath $active)) {
    throw "activeProject is not set or missing. Run .\rag.ps1 pick-project first."
}

$py = & {
    $bundled = Join-Path $HOME ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path $bundled) { return $bundled }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python -and $python.Source -notlike "*\WindowsApps\*") { return $python.Source }
    throw "Python not found"
}

Push-Location $workspace
try {
    & $py scripts\active_project_sync.py
    if ($LASTEXITCODE -ne 0) {
        throw "active_project_sync.py failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

Write-Host "Active project synced: $active"
