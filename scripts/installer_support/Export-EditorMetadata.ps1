param(
    [string]$ExportDir = "",
    [string]$ProjectName = "",
    [string]$WorkspaceRoot = "",
    [string]$ContentPath = "/Game",
    [switch]$IngestOnly,
    [switch]$PrintCommandsOnly,
    [switch]$RegisterMenu
)

$ErrorActionPreference = "Stop"

$workspace = if ($WorkspaceRoot) {
    (Resolve-Path $WorkspaceRoot).Path
}
else {
    (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}

. (Join-Path $workspace "scripts\unreal_workspace_config.ps1")

$sharedConfigPath = Join-Path $HOME ".lmstudio\config\unreal-workspace.json"
$shared = @{}
if (Test-Path $sharedConfigPath) {
    $shared = Read-SharedConfig -Path $sharedConfigPath
}

if (-not $ExportDir) {
    $ExportDir = Resolve-EditorExportDir -Config $shared
}

$toolsRoot = Join-Path $workspace "tools\ue_export"
$runAllScript = Join-Path $toolsRoot "run_all_exports.py"
$registerMenuScript = Join-Path $toolsRoot "register_export_menu.py"

Write-Host ""
Write-Host "=== Unreal Editor metadata export ==="
Write-Host ""
Write-Host "Export directory: $ExportDir"
Write-Host "Content path: $ContentPath"
Write-Host ""
Write-Host "Fully automatic (no manual Python paste):"
Write-Host "  .\rag.ps1 export-editor-metadata"
Write-Host "  .\rag.ps1 export-editor-metadata -Question '/Game/06_Environment/BossStage'"
Write-Host ""
Write-Host "exec(open(r'$runAllScript', encoding='utf-8').read())"
Write-Host "run_all_metadata_exports(r'$ExportDir', content_path='$ContentPath')"
Write-Host ""

if ($RegisterMenu) {
    Write-Host "Register Editor menu (run once per Editor session):"
    Write-Host "exec(open(r'$registerMenuScript', encoding='utf-8').read())"
    Write-Host "register_lmstudio_export_menu(r'$ExportDir', content_path='$ContentPath')"
    Write-Host ""
}

Write-Host "Folder-scoped examples:"
Write-Host "run_all_metadata_exports(r'$ExportDir', content_path='/Game/06_Environment/BossStage')"
Write-Host "export_materials_only(r'$ExportDir', content_path='/Game')"
Write-Host ""

Write-Host "Individual exports (legacy):"
$exports = @(
    @{ Script = "export_blueprint_metadata.py"; Func = "export_blueprint_metadata"; Out = "blueprints.jsonl" },
    @{ Script = "export_material_metadata.py"; Func = "export_material_metadata"; Out = "materials.jsonl" },
    @{ Script = "export_animation_metadata.py"; Func = "export_animation_metadata"; Out = "animation.jsonl" },
    @{ Script = "export_asset_registry.py"; Func = "export_asset_registry"; Out = "asset_registry.jsonl" },
    @{ Script = "export_project_settings.py"; Func = "export_project_settings"; Out = "project_settings.jsonl" },
    @{ Script = "export_level_metadata.py"; Func = "export_level_metadata"; Out = "level.jsonl" }
)

foreach ($item in $exports) {
    $scriptPath = Join-Path $toolsRoot $item.Script
    $outPath = Join-Path $ExportDir $item.Out
    Write-Host "# $($item.Out)"
    Write-Host "exec(open(r'$scriptPath', encoding='utf-8').read())"
    if ($item.Func -eq "export_project_settings") {
        Write-Host "$($item.Func)(r'$outPath')"
    }
    elseif ($item.Func -eq "export_level_metadata") {
        Write-Host "$($item.Func)('$ContentPath', r'$outPath')"
    }
    else {
        Write-Host "$($item.Func)('$ContentPath', r'$outPath')"
    }
    Write-Host ""
}

if ($PrintCommandsOnly) {
    exit 0
}

if (-not (Test-Path -LiteralPath $ExportDir)) {
    New-Item -ItemType Directory -Force -Path $ExportDir | Out-Null
    Write-Host "Created export directory: $ExportDir"
}

if ($IngestOnly -or (Read-Host "Sync exports into RAG index now? [y/N]") -match '^[Yy]') {
    $py = & {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notlike "*\WindowsApps\*") { return $cmd.Source }
        throw "Python not found"
    }
    Push-Location $workspace
    try {
        $syncArgs = @("scripts\sync_editor_metadata.py", "--refresh", "--export-dir", $ExportDir)
        if ($ProjectName) { $syncArgs += @("--project-name", $ProjectName) }
        & $py @syncArgs
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Write-Host ""
        Write-Host "Editor metadata synced and index rebuilt."
    }
    finally {
        Pop-Location
    }
}

Write-Host ""
Write-Host "Tip: set editorExportDir in unreal-workspace.json so sync-active-project and unreal_sync_editor_metadata auto-ingest."
