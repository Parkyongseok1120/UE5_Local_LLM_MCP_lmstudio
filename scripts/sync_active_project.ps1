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

$projectRoot = Split-Path -Parent $active
$projectName = [System.IO.Path]::GetFileNameWithoutExtension($active)
$sourceRoot = Join-Path $projectRoot "Source"
if (-not (Test-Path -LiteralPath $sourceRoot)) {
    $sourceRoot = $projectRoot
}

$namespace = "unreal58"
$cfgPath = Join-Path $workspace "config\workspace.json"
if (Test-Path $cfgPath) {
    try {
        $cfg = Get-Content -LiteralPath $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($cfg.indexNamespace) { $namespace = [string]$cfg.indexNamespace }
    }
    catch { }
}
$dataRel = "data\$namespace"

$py = & {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return $python.Source }
    $bundled = Join-Path $HOME ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path $bundled) { return $bundled }
    throw "Python not found"
}

$engineSource = & {
    . (Join-Path $workspace "installer\Install-PathHelpers.ps1")
    Get-WorkspaceEngineSourcePath -RagRoot $workspace
}

Push-Location $workspace
try {
    & $py scripts\collect_unreal_projects.py --out "$dataRel\raw_projects.jsonl" --root $projectRoot
    & $py scripts\collect_unreal_project_profile.py --root $projectRoot --out "$dataRel\raw_project_profiles.jsonl"
    & $py scripts\collect_project_architecture.py `
        --project $active `
        --out-dir "$dataRel\project_architecture" `
        --jsonl "$dataRel\raw_project_architecture.jsonl"

    $symbolsPath = Join-Path $dataRel "raw_project_symbols.jsonl"
    if (Test-Path -LiteralPath $sourceRoot) {
        if (Test-Path -LiteralPath $symbolsPath) { Remove-Item -LiteralPath $symbolsPath -Force }
        & $py scripts\collect_unreal_symbols.py `
            --root $sourceRoot `
            --out $symbolsPath `
            --tier full `
            --scope project `
            --project-name $projectName
    }

    $exportDir = Resolve-EditorExportDir -Config $config
    $autoExport = $true
    if ($null -ne $config.autoEditorExport) {
        $autoExport = [bool]$config.autoEditorExport
    }
    if ($autoExport -and (Test-Path -LiteralPath $active)) {
        $refreshArgs = @(
            "scripts\sync_editor_metadata.py",
            "--refresh",
            "--export-dir", $exportDir,
            "--index-dir", $dataRel,
            "--project-name", $projectName
        )
        if ($config.editorExportContentPath) {
            $refreshArgs += @("--content-path", [string]$config.editorExportContentPath)
        }
        & $py @refreshArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Automatic Editor metadata export/sync reported issues; continuing project sync."
        }
    }
    elseif ($exportDir -and (Test-Path -LiteralPath $exportDir)) {
        & $py scripts\sync_editor_metadata.py `
            --export-dir $exportDir `
            --index-dir $dataRel `
            --project-name $projectName
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Editor metadata sync reported issues; continuing project sync."
        }
    }

    & $py scripts\incremental_build.py --out-dir $dataRel
    & $py scripts\warm_symbol_cache.py
}
finally {
    Pop-Location
}

Write-Host "Active project synced: $active"
