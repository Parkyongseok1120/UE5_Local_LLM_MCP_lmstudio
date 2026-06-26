$ErrorActionPreference = "Stop"

$workspace = Split-Path $PSScriptRoot -Parent
$sharedConfig = Join-Path $HOME ".lmstudio\config\unreal-workspace.json"
if (-not (Test-Path $sharedConfig)) {
    throw "Shared config not found: $sharedConfig"
}

$shared = Get-Content -LiteralPath $sharedConfig -Raw -Encoding UTF8 | ConvertFrom-Json
$active = [string]$shared.activeProject
if ([string]::IsNullOrWhiteSpace($active) -or -not (Test-Path -LiteralPath $active)) {
    throw "activeProject is not set or missing. Run .\rag.ps1 pick-project first."
}

$projectRoot = Split-Path -Parent $active
$py = & {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return $python.Source }
    $bundled = Join-Path $HOME ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path $bundled) { return $bundled }
    throw "Python not found"
}

Push-Location $workspace
try {
    & $py scripts\collect_unreal_projects.py --out data\unreal58\raw_projects.jsonl --root $projectRoot
    & $py scripts\collect_unreal_project_profile.py --root $projectRoot --out data\unreal58\raw_project_profiles.jsonl
    & $py scripts\incremental_build.py --out-dir data\unreal58
    & $py scripts\warm_symbol_cache.py
}
finally {
    Pop-Location
}

Write-Host "Active project synced: $active"
