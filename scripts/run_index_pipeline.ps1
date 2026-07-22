param(
    [string]$WorkspaceRoot = "",
    [string]$PythonExe = "",
    [ValidateSet("lite", "standard", "full", "")]
    [string]$Tier = "",
    [switch]$SkipEditorIngest,
    [switch]$SkipEditorExport,
    [switch]$SkipBuild,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "unreal_workspace_config.ps1")

function Find-Python {
    param([string]$Preferred = "")

    if ($Preferred) {
        if (Test-Path -LiteralPath $Preferred) {
            return (Resolve-Path -LiteralPath $Preferred).Path
        }
        $preferredCommand = Get-Command $Preferred -ErrorAction SilentlyContinue
        if ($preferredCommand) { return $preferredCommand.Source }
        throw "Requested Python executable not found: $Preferred"
    }

    $bundled = Join-Path (Join-Path (Join-Path (Join-Path (Join-Path $HOME ".cache") "codex-runtimes") "codex-primary-runtime") "dependencies") "python/python.exe"
    if (Test-Path -LiteralPath $bundled) { return $bundled }
    foreach ($name in @("python3", "python")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notlike "*\WindowsApps\*") { return $cmd.Source }
    }
    throw "Python 3.10+ not found."
}

function Read-SharedConfigJson {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return @{} }
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Get-EngineSourceRoot {
    param($SharedConfig, [string]$Workspace)

    . (Join-Path $PSScriptRoot "installer_support\Install-PathHelpers.ps1")
    return Get-WorkspaceEngineSourcePath -RagRoot $Workspace
}

function Resolve-ActiveProjectInfo {
    param($SharedConfig)
    $active = [string]$SharedConfig.activeProject
    if ([string]::IsNullOrWhiteSpace($active) -or -not (Test-Path -LiteralPath $active)) {
        return $null
    }
    $resolved = (Resolve-Path -LiteralPath $active).Path
    $projectRoot = Split-Path -Parent $resolved
    $projectName = [System.IO.Path]::GetFileNameWithoutExtension($resolved)
    $sourceRoot = Join-Path $projectRoot "Source"
    if (-not (Test-Path -LiteralPath $sourceRoot)) {
        $sourceRoot = $projectRoot
    }
    return [ordered]@{
        UprojectPath = $resolved
        ProjectRoot  = $projectRoot
        ProjectName  = $projectName
        SourceRoot   = $sourceRoot
    }
}

function Remove-TierInput {
    param([string]$Path, [string]$Reason)
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Force
        Write-Host "[tier] removed $(Split-Path -Leaf $Path) ($Reason)"
    }
}

$workspace = if ($WorkspaceRoot) {
    (Resolve-Path $WorkspaceRoot).Path
}
else {
    (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

$py = Find-Python -Preferred $PythonExe
$sharedConfigPath = Join-Path (Join-Path (Join-Path $HOME ".lmstudio") "config") "unreal-workspace.json"
$shared = Read-SharedConfigJson -Path $sharedConfigPath

$resolvedTier = if ($Tier) { $Tier.ToLowerInvariant() } else { [string]$shared.indexingTier }
if ([string]::IsNullOrWhiteSpace($resolvedTier)) { $resolvedTier = "standard" }
if ($resolvedTier -notin @("lite", "standard", "full")) { $resolvedTier = "standard" }

$namespace = "unreal58"
$cfgPath = Join-Path (Join-Path $workspace "config") "workspace.json"
if (Test-Path $cfgPath) {
    try {
        $cfg = Get-Content -LiteralPath $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($cfg.indexNamespace) { $namespace = [string]$cfg.indexNamespace }
    }
    catch { }
}
$dataDir = Join-Path (Join-Path $workspace "data") $namespace
$scriptsDir = Join-Path $workspace "scripts"
$symbolsPath = Join-Path $dataDir "raw_symbols.jsonl"
$sidecarPath = Join-Path $dataDir "sidecar_symbols_meta.jsonl"
$moduleGraphPath = Join-Path $dataDir "raw_module_graph.jsonl"
$sourcePath = Join-Path $dataDir "raw_source.jsonl"
$projectSymbolsPath = Join-Path $dataDir "raw_project_symbols.jsonl"
$projectProfilesPath = Join-Path $dataDir "raw_project_profiles.jsonl"
$projectArchitecturePath = Join-Path $dataDir "raw_project_architecture.jsonl"
$engineSourceRoot = Get-EngineSourceRoot -SharedConfig $shared -Workspace $workspace
$projectInfo = Resolve-ActiveProjectInfo -SharedConfig $shared

Write-Host ""
Write-Host "=== RAG indexing pipeline (tier: $resolvedTier) ==="
Write-Host "Workspace : $workspace"
Write-Host "Data dir  : $dataDir"
if ($projectInfo) {
    Write-Host "Active    : $($projectInfo.UprojectPath)"
}
Write-Host ""

Push-Location $workspace
try {
    if ($resolvedTier -eq "lite") {
        foreach ($excluded in @(
                $symbolsPath,
                $sidecarPath,
                $moduleGraphPath,
                $projectSymbolsPath,
                $projectProfilesPath,
                $projectArchitecturePath
            )) {
            Remove-TierInput -Path $excluded -Reason "excluded by lite tier"
        }
    }
    if ($resolvedTier -ne "full") {
        Remove-TierInput -Path $sourcePath -Reason "excluded by $resolvedTier tier"
    }

    Write-Host "[1/7] collect-projects"
    $searchRoots = @($shared.projectSearchRoots | Where-Object {
            $_ -and (([string]$_).Replace('\', '/') -notlike "*/Unreal58-RAG/data") -and (Test-Path -LiteralPath $_)
        })
    if ($searchRoots.Count -eq 0) {
        $searchRoots = @(
            (Join-Path (Join-Path $HOME "Documents") "Github"),
            (Join-Path (Join-Path $HOME "Documents") "Git"),
            (Join-Path (Join-Path $HOME "Documents") "Unreal Projects")
        ) | Where-Object { Test-Path -LiteralPath $_ }
    }
    $projectArgs = @(
        (Join-Path $scriptsDir "collect_unreal_projects.py"),
        "--out", (Join-Path $dataDir "raw_projects.jsonl"),
        "--copy-text-to", (Join-Path (Join-Path $workspace "data") "unreal_projects/text_snapshot")
    )
    foreach ($root in $searchRoots) {
        $projectArgs += @("--root", [string]$root)
    }
    if ($projectArgs -notcontains "--root") {
        throw "No project search roots found. Run installer project setup or set projectSearchRoots."
    }
    & $py @projectArgs
    if ($LASTEXITCODE -ne 0) { throw "collect-projects failed" }

    if ($resolvedTier -in @("standard", "full")) {
        if (-not $engineSourceRoot -or -not (Test-Path -LiteralPath $engineSourceRoot -PathType Container)) {
            throw "Engine/Source not found. Re-run installer with --engine-root or set UNREAL_ENGINE_ROOT."
        }
        Write-Host "[2/7] collect-symbols (engine)"
        if (Test-Path $symbolsPath) { Remove-Item -LiteralPath $symbolsPath -Force }
        if (Test-Path $sidecarPath) { Remove-Item -LiteralPath $sidecarPath -Force }
        & $py (Join-Path $scriptsDir "collect_unreal_symbols.py") `
            --root $engineSourceRoot `
            --out $symbolsPath `
            --sidecar-out $sidecarPath `
            --tier public `
            --scope engine
        if ($LASTEXITCODE -ne 0) { throw "collect-symbols (engine) failed" }

        if ($projectInfo -and (Test-Path -LiteralPath $projectInfo.SourceRoot)) {
            Write-Host "[3/7] collect-symbols (project: $($projectInfo.ProjectName))"
            if (Test-Path $projectSymbolsPath) { Remove-Item -LiteralPath $projectSymbolsPath -Force }
            & $py (Join-Path $scriptsDir "collect_unreal_symbols.py") `
                --root $projectInfo.SourceRoot `
                --out $projectSymbolsPath `
                --tier full `
                --scope project `
                --project-name $projectInfo.ProjectName
            if ($LASTEXITCODE -ne 0) { throw "collect-symbols (project) failed" }

            Write-Host "[4/7] collect-project-profile + architecture"
            Remove-TierInput -Path $projectProfilesPath -Reason "refresh active project profile"
            Remove-TierInput -Path $projectArchitecturePath -Reason "refresh active project architecture"
            & $py (Join-Path $scriptsDir "collect_unreal_project_profile.py") `
                --root $projectInfo.ProjectRoot `
                --out $projectProfilesPath
            if ($LASTEXITCODE -ne 0) { throw "collect-project-profile failed" }
            & $py (Join-Path $scriptsDir "collect_project_architecture.py") `
                --project $projectInfo.UprojectPath `
                --out-dir (Join-Path $dataDir "project_architecture") `
                --jsonl $projectArchitecturePath
            if ($LASTEXITCODE -ne 0) { Write-Warning "collect-project-architecture failed (continuing)" }
        }
        else {
            Write-Host "[3/7] skip project symbols (no active project Source/)"
            Write-Host "[4/7] skip project profile"
            foreach ($staleProjectInput in @($projectSymbolsPath, $projectProfilesPath, $projectArchitecturePath)) {
                Remove-TierInput -Path $staleProjectInput -Reason "no active project"
            }
        }

        Write-Host "[5/7] collect-module-graph"
        $graphArgs = @(
            (Join-Path $scriptsDir "build_unreal_module_graph.py"),
            "--symbols", $symbolsPath,
            "--out", $moduleGraphPath,
            "--report", (Join-Path (Join-Path $workspace "Reports") "unreal_module_include_graph.md")
        )
        if (Test-Path $projectSymbolsPath) {
            $graphArgs += @("--symbols", $projectSymbolsPath)
        }
        & $py @graphArgs
        if ($LASTEXITCODE -ne 0) { throw "collect-module-graph failed" }
    }
    else {
        Write-Host "[2/7] skip engine/project symbols (lite tier)"
        Write-Host "[3/7] skip project symbols (lite tier)"
        Write-Host "[4/7] skip project profile (lite tier)"
        Write-Host "[5/7] skip module graph (lite tier)"
    }

    if ($resolvedTier -eq "full") {
        Write-Host "[6/7] collect-source (engine full text)"
        Remove-TierInput -Path $sourcePath -Reason "refresh full-tier engine source"
        & $py (Join-Path $scriptsDir "collect_unreal_source.py") `
            --root $engineSourceRoot `
            --out $sourcePath
        if ($LASTEXITCODE -ne 0) { throw "collect-source failed" }
    }
    else {
        Write-Host "[6/7] skip engine source dump (tier=$resolvedTier)"
    }

    if (-not $SkipEditorIngest) {
        $autoExport = $true
        if ($null -ne $shared.autoEditorExport) {
            $autoExport = [bool]$shared.autoEditorExport
        }
        $installGraphPlugin = $false
        if ($null -ne $shared.installEditorGraphPlugin) {
            $installGraphPlugin = [bool]$shared.installEditorGraphPlugin
        }
        $exportDir = Resolve-EditorExportDir -Config $shared
        $projName = if ($projectInfo) { $projectInfo.ProjectName } else { "" }

        if ($installGraphPlugin -and $projectInfo) {
            Write-Host "[editor] ensure Blueprint graph exporter plugin"
            & $py (Join-Path $scriptsDir "install_editor_graph_plugin.py") `
                --workspace $workspace `
                --project $projectInfo.UprojectPath `
                --update `
                --build
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "Editor graph plugin install/build failed. Automatic export will continue with the Python fallback if possible."
            }
        }

        if (-not $SkipEditorExport -and $autoExport -and $projectInfo) {
            Write-Host "[editor] automatic export + ingest (active project)"
            $refreshArgs = @(
                (Join-Path $scriptsDir "sync_editor_metadata.py"),
                "--refresh",
                "--export-dir", $exportDir,
                "--index-dir", $dataDir
            )
            if ($projName) { $refreshArgs += @("--project-name", $projName) }
            if ($shared.editorExportContentPath) {
                $refreshArgs += @("--content-path", [string]$shared.editorExportContentPath)
            }
            & $py @refreshArgs
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "Automatic Editor metadata export/sync failed. Indexing continues without fresh asset graphs."
                Write-Warning "Ensure Unreal Editor/engine for this project is installed, then run: .\rag.ps1 export-editor-metadata"
            }
        }
        elseif ($exportDir -and (Test-Path -LiteralPath $exportDir)) {
            Write-Host "[editor] ingest exports from $exportDir"
            $ingestArgs = @(
                (Join-Path $scriptsDir "sync_editor_metadata.py"),
                "--export-dir", $exportDir,
                "--index-dir", $dataDir
            )
            if ($projName) { $ingestArgs += @("--project-name", $projName) }
            & $py @ingestArgs
            if ($LASTEXITCODE -ne 0) { Write-Warning "editor export ingest reported issues" }
        }
        else {
            Write-Host "[editor] no export dir available yet (skip editor metadata)"
        }
    }

    if (-not $SkipBuild) {
        Write-Host "[7/7] build index"
        & $py (Join-Path $scriptsDir "incremental_build.py") --out-dir $dataDir --force
        if ($LASTEXITCODE -ne 0) { throw "build failed" }

        if ($projectInfo) {
            & $py (Join-Path $scriptsDir "warm_symbol_cache.py")
        }
    }
    else {
        Write-Host "[7/7] skip build"
    }
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "Indexing pipeline finished (tier: $resolvedTier)."
