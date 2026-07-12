param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet("collect-docs", "collect-source", "collect-projects", "collect-guidelines", "collect-game-design", "collect-symbols", "collect-module-graph", "collect-project-profile", "collect-project-architecture", "collect-blueprint-metadata", "collect-material-metadata", "collect-animation-metadata", "collect-skeletal-mesh-metadata", "collect-anim-blueprint-metadata", "collect-anim-montage-metadata", "collect-sequencer-metadata", "collect-editor-metadata", "collect-failure-memory", "collect-build-logs", "build", "build-incremental", "build-embeddings", "build-embeddings-full", "sync-active-project", "sync-editor-metadata", "export-editor-metadata", "install-editor-graph-plugin", "watch-active-project", "index-full", "ingest-editor-exports", "warm-cache", "phase3-finish", "pick-project", "promote-index", "query", "ask", "eval-game-design", "eval-unreal-programming", "eval-prototype", "eval-refactor", "eval-refactor-rag", "eval-unreal-review", "eval-debug", "eval-sequencer", "eval-genre", "eval-e2e-compile", "eval-reasoning", "eval-agent-harness", "eval-project-review", "eval-soulslike-live", "eval-pass-at-k", "eval-harness", "eval-regression", "summarize-real-project-eval", "preflight-lmstudio", "report-tier-kpi", "sonnet-tier-gate", "verify-release", "build-project-graph", "agent-plan", "reject-failure-memory", "knowledge-audit", "test-build-logs", "test-unreal-readiness", "ubt-feedback", "wrapper", "review-project", "lmstudio-models", "doctor", "bench-mcp", "bench-token-budget", "scaffold-prototype", "agent-session", "update-engine", "update-project", "update-guidelines", "validate-index")]
    [string]$Command,

    [string]$IndexNamespace = "",

    [string]$Question = "",
    [string]$RequestFile = "",
    [string]$SourceRoot = "",
    [string]$ProjectsRoot = "data",
    [string]$ProjectFile = "",
    [string]$GuidelinesRoot = "RAG_Project_Guidelines",
    [string]$GameDesignRoot = "Game_Design_Docs",
    [string[]]$BuildLogRoot = @("."),
    [string]$QuerySet = "config\rag_eval_game_design_queries.json",
    [string]$ProgrammingQuerySet = "config\rag_eval_unreal_programming_queries.json",
    [string]$ReviewCaseSet = "config\unreal_review_eval_cases.json",
    [string]$Answers = "",
    [string]$AnswersDir = "",
    [string]$Model = "",
    [string]$UbtTarget = "",
    [string]$UbtPlatform = "Win64",
    [string]$UbtConfiguration = "Development",
    [string]$UbtPath = "",
    [string]$ModuleGraph = "",
    [string]$ProjectName = "ScratchPrototype",
    [string]$ScratchRoot = "data\wrapper_runs",
    [int]$MaxAttempts = 4,
    [int]$BuildTimeout = 1200,
    [int]$TopK = 8,
    [ValidateSet("auto", "planning", "design", "implementation", "review", "agent_edit", "codegen", "shader", "material_analysis", "material_porting", "blueprint_analysis", "blueprint_verification", "compile_fix", "runtime_debug", "api_lookup", "module_fix", "reflection_fix", "prototype_component", "prototype_subsystem", "refactor_r0", "refactor_r1", "refactor_r2", "refactor_r3", "refactor_r4")]
    [string]$Mode = "auto",
    [ValidateSet("shooter", "action_combat", "platformer")]
    [string]$ScaffoldGenre = "shooter",
    [string[]]$Source = @(),
    [string[]]$Project = @(),
    [string[]]$Layer = @(),
    [string[]]$DocType = @(),
    [string[]]$Genre = @(),
    [string[]]$Extension = @(),
    [string[]]$RequiredTerm = @(),
    [int]$MaxPages = 50,
    [switch]$IncludeProjectSymbols,
    [switch]$EngineOnlySymbols,
    [switch]$SkipEditorIngest,
    [switch]$IncludeSymbolDefinitions,
    [switch]$CopyProjectText,
    [switch]$SkipAssetPaths,
    [switch]$LogsOnly,
    [switch]$SkipBuild,
    [switch]$SkipStaticGate,
    [switch]$AllowEmptyFiles,
    [switch]$AllowDirectProjectWrite,
    [switch]$DryRun,
    [switch]$Force,
    [switch]$Live,
    [switch]$PrintPrompts,
    [switch]$Explorer,
    [switch]$ClearActiveProject,
    [switch]$RunUbt,
    [switch]$RequireLive
)

$ErrorActionPreference = "Stop"

function Find-Python {
    $bundled = Join-Path $HOME ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path $bundled) {
        return $bundled
    }

    $localCandidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python310\python.exe")
    )
    foreach ($candidate in $localCandidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python -and $python.Source -notlike "*\WindowsApps\*") {
        return $python.Source
    }

    throw "Python was not found. Install Python 3.10+ or check the Codex bundled Python path."
}

$py = Find-Python

function Get-WorkspaceIndexNamespace {
    param([string]$Override)
    if ($Override) {
        return $Override
    }
    $cfgPath = Join-Path $PSScriptRoot "config\workspace.json"
    if (Test-Path $cfgPath) {
        try {
            $cfg = Get-Content -LiteralPath $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($cfg.indexNamespace) {
                return [string]$cfg.indexNamespace
            }
            if ($cfg.engineVersion) {
                $digits = ($cfg.engineVersion -replace "[^\d]", "")
                if ($digits) {
                    return "unreal$digits"
                }
            }
        }
        catch {
            Write-Warning "Could not read indexNamespace from $cfgPath"
        }
    }
    return "unreal58"
}

function Get-WorkspaceEngineRoot {
    param([string]$Fallback)
    if ($env:UNREAL_ENGINE_ROOT -and (Test-Path -LiteralPath $env:UNREAL_ENGINE_ROOT)) {
        return (Resolve-Path -LiteralPath $env:UNREAL_ENGINE_ROOT).Path
    }
    $cfgPath = Join-Path $PSScriptRoot "config\workspace.json"
    if (Test-Path $cfgPath) {
        try {
            $cfg = Get-Content -LiteralPath $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($cfg.defaultEngineRoot) {
                return [string]$cfg.defaultEngineRoot
            }
        }
        catch {
            Write-Warning "Could not read defaultEngineRoot from $cfgPath"
        }
    }
    $sharedConfig = Join-Path $HOME ".lmstudio\config\unreal-workspace.json"
    if (Test-Path $sharedConfig) {
        try {
            $shared = Get-Content -LiteralPath $sharedConfig -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($shared.defaultEngineRoot) {
                return [string]$shared.defaultEngineRoot
            }
        }
        catch {
            Write-Warning "Could not read defaultEngineRoot from $sharedConfig"
        }
    }
    foreach ($envName in @("ProgramFiles", "ProgramFiles(x86)")) {
        $base = [Environment]::GetEnvironmentVariable($envName)
        if (-not $base) { continue }
        $epic = Join-Path $base "Epic Games"
        if (-not (Test-Path -LiteralPath $epic)) { continue }
        $engine = Get-ChildItem -LiteralPath $epic -Directory -Filter "UE_5.*" -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            Select-Object -First 1
        if ($engine) {
            return $engine.FullName
        }
    }
    return $Fallback
}

function Get-SharedActiveProjectInfo {
    $sharedConfig = Join-Path $HOME ".lmstudio\config\unreal-workspace.json"
    if (-not (Test-Path $sharedConfig)) { return $null }
    try {
        $shared = Get-Content -LiteralPath $sharedConfig -Raw -Encoding UTF8 | ConvertFrom-Json
        $active = [string]$shared.activeProject
        if ([string]::IsNullOrWhiteSpace($active) -or -not (Test-Path -LiteralPath $active)) {
            return $null
        }
        $resolved = (Resolve-Path -LiteralPath $active).Path
        $projectRoot = Split-Path -Parent $resolved
        $sourceRoot = Join-Path $projectRoot "Source"
        if (-not (Test-Path -LiteralPath $sourceRoot)) {
            $sourceRoot = $projectRoot
        }
        return [ordered]@{
            UprojectPath = $resolved
            ProjectRoot  = $projectRoot
            ProjectName  = [System.IO.Path]::GetFileNameWithoutExtension($resolved)
            SourceRoot   = $sourceRoot
        }
    }
    catch {
        return $null
    }
}

. (Join-Path $PSScriptRoot "installer\Install-PathHelpers.ps1")
$namespaceOverride = if ($IndexNamespace) { $IndexNamespace } else { "" }
$ragPaths = Get-RagDataPaths -RagRoot $PSScriptRoot -NamespaceOverride $namespaceOverride
$resolvedNamespace = $ragPaths.Namespace
$dataDir = $ragPaths.DataDir
$indexPath = $ragPaths.IndexPath
$moduleGraphPath = if ($ModuleGraph) { $ModuleGraph } else { $ragPaths.ModuleGraphPath }

$commandsNeedingSourceRoot = @("collect-source", "collect-symbols", "update-engine")
$commandsNeedingUbtPath = @("ubt-feedback", "wrapper", "bench-mcp")
$engineRoot = $null

if (($Command -in $commandsNeedingSourceRoot) -and (-not $SourceRoot)) {
    $engineRoot = Get-WorkspaceEngineRoot -Fallback ""
    if (-not $engineRoot) {
        throw "Unreal Engine root not found. Set UNREAL_ENGINE_ROOT or config/workspace.json defaultEngineRoot."
    }
    $SourceRoot = Join-Path $engineRoot "Engine\Source"
}

if (($Command -in $commandsNeedingUbtPath) -and (-not $UbtPath)) {
    if (-not $engineRoot) {
        $engineRoot = Get-WorkspaceEngineRoot -Fallback ""
    }
    if (-not $engineRoot) {
        throw "Unreal Engine root not found. Set UNREAL_ENGINE_ROOT or config/workspace.json defaultEngineRoot."
    }
    $UbtPath = Join-Path $engineRoot "Engine\Binaries\DotNET\UnrealBuildTool\UnrealBuildTool.exe"
}

switch ($Command) {
    "collect-docs" {
        & $py scripts\collect_unreal_docs.py --seeds config\unreal_58_seed_urls.txt --out "$dataDir\raw_docs.jsonl" --max-pages $MaxPages --delay 0.5
    }
    "collect-source" {
        $sourceArgs = @("scripts\collect_unreal_source.py", "--root", $SourceRoot, "--out", "$dataDir\raw_source.jsonl")
        if ($IncludeThirdParty) {
            $sourceArgs += "--include-third-party"
        }
        & $py @sourceArgs
    }
    "collect-projects" {
        $sharedConfig = Join-Path $HOME ".lmstudio\config\unreal-workspace.json"
        $searchRoots = @(
            (Join-Path $env:USERPROFILE "Documents\Git"),
            (Join-Path $env:USERPROFILE "Documents\Unreal Projects")
        )
        if (Test-Path $sharedConfig) {
            try {
                $shared = Get-Content -LiteralPath $sharedConfig -Raw -Encoding UTF8 | ConvertFrom-Json
                if ($shared.projectSearchRoots) {
                    $searchRoots = @($shared.projectSearchRoots | Where-Object { $_ -and ($_ -notlike "*\Unreal58-RAG\data") })
                }
            }
            catch {
                Write-Warning "Could not read projectSearchRoots from $sharedConfig"
            }
        }
        if ($ProjectsRoot -and $ProjectsRoot -ne "data") {
            $searchRoots = @($ProjectsRoot)
        }
        $projectArgs = @("scripts\collect_unreal_projects.py", "--out", "$dataDir\raw_projects.jsonl")
        foreach ($root in $searchRoots) {
            $projectArgs += @("--root", $root)
        }
        if ($CopyProjectText) {
            $projectArgs += @("--copy-text-to", "data\unreal_projects\text_snapshot")
        }
        if ($SkipAssetPaths) {
            $projectArgs += "--skip-asset-paths"
        }
        & $py @projectArgs
    }
    "collect-guidelines" {
        & $py scripts\collect_project_guidelines.py --root $GuidelinesRoot --out "$dataDir\raw_guidelines.jsonl"
    }
    "collect-game-design" {
        & $py scripts\collect_game_design_docs.py --root $GameDesignRoot --out "$dataDir\raw_game_design.jsonl"
    }
    "collect-symbols" {
        $symbolsOut = Join-Path $dataDir "raw_symbols.jsonl"
        $sidecarOut = Join-Path $dataDir "sidecar_symbols_meta.jsonl"
        $projectInfo = Get-SharedActiveProjectInfo
        $collectProject = ($IncludeProjectSymbols -or (-not $EngineOnlySymbols)) -and $null -ne $projectInfo

        if (-not $EngineOnlySymbols) {
            if (Test-Path $symbolsOut) { Remove-Item -LiteralPath $symbolsOut -Force }
            if (Test-Path $sidecarOut) { Remove-Item -LiteralPath $sidecarOut -Force }
            $symbolArgs = @(
                "scripts\collect_unreal_symbols.py",
                "--root", $SourceRoot,
                "--out", $symbolsOut,
                "--sidecar-out", $sidecarOut,
                "--tier", "public",
                "--scope", "engine"
            )
            if ($IncludeThirdParty) { $symbolArgs += "--include-third-party" }
            if ($IncludeSymbolDefinitions) {
                $symbolArgs += @("--include-definitions", "--tier", "full")
            }
            & $py @symbolArgs
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        }

        if ($collectProject) {
            $projectSymbolsOut = Join-Path $dataDir "raw_project_symbols.jsonl"
            if (Test-Path $projectSymbolsOut) { Remove-Item -LiteralPath $projectSymbolsOut -Force }
            $projectArgs = @(
                "scripts\collect_unreal_symbols.py",
                "--root", $projectInfo.SourceRoot,
                "--out", $projectSymbolsOut,
                "--tier", "full",
                "--scope", "project",
                "--project-name", $projectInfo.ProjectName
            )
            if ($IncludeSymbolDefinitions) { $projectArgs += "--include-definitions" }
            & $py @projectArgs
            if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        }
    }
    "collect-module-graph" {
        $symbolsPath = Join-Path $dataDir "raw_symbols.jsonl"
        if (-not (Test-Path $symbolsPath)) {
            throw "Run collect-symbols first so $symbolsPath exists."
        }
        $graphArgs = @(
            "scripts\build_unreal_module_graph.py",
            "--symbols", $symbolsPath,
            "--out", $moduleGraphPath,
            "--report", "Reports\unreal_module_include_graph.md"
        )
        $projectSymbolsPath = Join-Path $dataDir "raw_project_symbols.jsonl"
        if (Test-Path $projectSymbolsPath) {
            $graphArgs += @("--symbols", $projectSymbolsPath)
        }
        & $py @graphArgs
    }
    "collect-project-profile" {
        $profileRoot = if ($ProjectFile) { $ProjectFile } else { $ProjectsRoot }
        & $py scripts\collect_unreal_project_profile.py --root $profileRoot --out "$dataDir\raw_project_profiles.jsonl"
    }
    "collect-project-architecture" {
        $archArgs = @("scripts\collect_project_architecture.py", "--out-dir", $dataDir, "--jsonl", "$dataDir\raw_project_architecture.jsonl")
        if ($ProjectFile) {
            $archArgs += @("--project", $ProjectFile)
        }
        & $py @archArgs
    }
    "collect-build-logs" {
        $logArgs = @("scripts\collect_build_logs.py", "--out", "$dataDir\raw_build_logs.jsonl")
        foreach ($value in $BuildLogRoot) { $logArgs += @("--root", $value) }
        if ($LogsOnly) {
            $logArgs += "--logs-only"
        }
        & $py @logArgs
    }
    "collect-blueprint-metadata" {
        if (-not $Question) {
            throw "collect-blueprint-metadata requires -Question pointing to Editor-export JSONL"
        }
        $proj = if ($ProjectName) { $ProjectName } else { "UnknownProject" }
        & $py scripts\collect_editor_metadata.py --project-name $proj --out-dir $dataDir --export "${Question}:blueprint"
    }
    "collect-material-metadata" {
        if (-not $Question) {
            throw "collect-material-metadata requires -Question pointing to Editor-export JSONL"
        }
        $proj = if ($ProjectName) { $ProjectName } else { "UnknownProject" }
        & $py scripts\collect_editor_metadata.py --project-name $proj --out-dir $dataDir --export "${Question}:material"
    }
    "collect-animation-metadata" {
        if (-not $Question) {
            throw "collect-animation-metadata requires -Question pointing to Editor-export JSONL"
        }
        $proj = if ($ProjectName) { $ProjectName } else { "UnknownProject" }
        & $py scripts\collect_editor_metadata.py --project-name $proj --out-dir $dataDir --export "${Question}:animation"
    }
    "collect-skeletal-mesh-metadata" {
        if (-not $Question) {
            throw "collect-skeletal-mesh-metadata requires -Question pointing to Editor-export JSONL"
        }
        $proj = if ($ProjectName) { $ProjectName } else { "UnknownProject" }
        & $py scripts\collect_editor_metadata.py --project-name $proj --out-dir $dataDir --export "${Question}:skeletal_mesh"
    }
    "collect-anim-blueprint-metadata" {
        if (-not $Question) {
            throw "collect-anim-blueprint-metadata requires -Question pointing to Editor-export JSONL"
        }
        $proj = if ($ProjectName) { $ProjectName } else { "UnknownProject" }
        & $py scripts\collect_editor_metadata.py --project-name $proj --out-dir $dataDir --export "${Question}:anim_blueprint"
    }
    "collect-anim-montage-metadata" {
        if (-not $Question) {
            throw "collect-anim-montage-metadata requires -Question pointing to Editor-export JSONL"
        }
        $proj = if ($ProjectName) { $ProjectName } else { "UnknownProject" }
        & $py scripts\collect_editor_metadata.py --project-name $proj --out-dir $dataDir --export "${Question}:anim_montage"
    }
    "collect-sequencer-metadata" {
        if (-not $Question) {
            throw "collect-sequencer-metadata requires -Question pointing to Editor-export JSONL"
        }
        $proj = if ($ProjectName) { $ProjectName } else { "UnknownProject" }
        & $py scripts\collect_editor_metadata.py --project-name $proj --out-dir $dataDir --export "${Question}:sequencer"
    }
    "collect-failure-memory" {
        & $py scripts\collect_failure_memory.py --out "$dataDir\raw_failure_memory.jsonl"
    }
    "build" {
        if (Test-Path $GuidelinesRoot) {
            & $py scripts\collect_project_guidelines.py --root $GuidelinesRoot --out "$dataDir\raw_guidelines.jsonl"
        }
        if (Test-Path $GameDesignRoot) {
            & $py scripts\collect_game_design_docs.py --root $GameDesignRoot --out "$dataDir\raw_game_design.jsonl"
        }
        $inputs = @()
        if (Test-Path "$dataDir\raw_guidelines.jsonl") {
            $inputs += "$dataDir\raw_guidelines.jsonl"
        }
        if (Test-Path "$dataDir\raw_game_design.jsonl") {
            $inputs += "$dataDir\raw_game_design.jsonl"
        }
        if (Test-Path "$dataDir\raw_symbols.jsonl") {
            $inputs += "$dataDir\raw_symbols.jsonl"
        }
        if (Test-Path "$dataDir\raw_module_graph.jsonl") {
            $inputs += "$dataDir\raw_module_graph.jsonl"
        }
        if (Test-Path "$dataDir\raw_project_profiles.jsonl") {
            $inputs += "$dataDir\raw_project_profiles.jsonl"
        }
        if (Test-Path "$dataDir\raw_build_logs.jsonl") {
            $inputs += "$dataDir\raw_build_logs.jsonl"
        }
        if (Test-Path "$dataDir\raw_docs.jsonl") {
            $inputs += "$dataDir\raw_docs.jsonl"
        }
        if (Test-Path "$dataDir\raw_source.jsonl") {
            $inputs += "$dataDir\raw_source.jsonl"
        }
        if (Test-Path "$dataDir\raw_projects.jsonl") {
            $inputs += "$dataDir\raw_projects.jsonl"
        }
        if (Test-Path "$dataDir\raw_project_architecture.jsonl") {
            $inputs += "$dataDir\raw_project_architecture.jsonl"
        }
        if (Test-Path "$dataDir\raw_blueprint_metadata.jsonl") {
            $inputs += "$dataDir\raw_blueprint_metadata.jsonl"
        }
        if (Test-Path "$dataDir\raw_material_metadata.jsonl") {
            $inputs += "$dataDir\raw_material_metadata.jsonl"
        }
        if (Test-Path "$dataDir\raw_animation_metadata.jsonl") {
            $inputs += "$dataDir\raw_animation_metadata.jsonl"
        }
        if (Test-Path "$dataDir\raw_skeletal_mesh_metadata.jsonl") {
            $inputs += "$dataDir\raw_skeletal_mesh_metadata.jsonl"
        }
        if (Test-Path "$dataDir\raw_anim_blueprint_metadata.jsonl") {
            $inputs += "$dataDir\raw_anim_blueprint_metadata.jsonl"
        }
        if (Test-Path "$dataDir\raw_anim_montage_metadata.jsonl") {
            $inputs += "$dataDir\raw_anim_montage_metadata.jsonl"
        }
        if (Test-Path "$dataDir\raw_sequencer_metadata.jsonl") {
            $inputs += "$dataDir\raw_sequencer_metadata.jsonl"
        }
        if (Test-Path "$dataDir\raw_asset_registry.jsonl") {
            $inputs += "$dataDir\raw_asset_registry.jsonl"
        }
        if (Test-Path "$dataDir\raw_project_settings.jsonl") {
            $inputs += "$dataDir\raw_project_settings.jsonl"
        }
        if (Test-Path "$dataDir\raw_level_metadata.jsonl") {
            $inputs += "$dataDir\raw_level_metadata.jsonl"
        }
        if (Test-Path "$dataDir\raw_failure_memory.jsonl") {
            $inputs += "$dataDir\raw_failure_memory.jsonl"
        }
        if ($inputs.Count -eq 0) {
            throw "Run collect-source, collect-projects, or collect-docs first."
        }
        $workspaceRoot = (Resolve-Path $PSScriptRoot).Path
        & $py scripts\build_rag_index.py --input @inputs --out-dir $dataDir --workspace-root $workspaceRoot
    }
    "build-incremental" {
        & $py scripts\incremental_build.py --out-dir $dataDir
    }
    "build-embeddings" {
        & $py scripts\rag_embeddings.py --index $indexPath --priority-only
    }
    "build-embeddings-full" {
        & $py scripts\rag_embeddings.py --index $indexPath
    }
    "sync-active-project" {
        powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "scripts\sync_active_project.ps1")
    }
    "sync-editor-metadata" {
        $syncArgs = @(
            "scripts\sync_editor_metadata.py",
            "--index-dir", ("data\" + $resolvedNamespace),
            "--auto-export"
        )
        if ($ProjectsRoot -and $ProjectsRoot -ne "data") {
            $syncArgs += @("--export-dir", $ProjectsRoot)
        }
        if ($ProjectName -and $ProjectName -ne "ScratchPrototype") {
            $syncArgs += @("--project-name", $ProjectName)
        }
        if ($SkipBuild) { $syncArgs += "--no-rebuild" }
        & $py @syncArgs
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    "export-editor-metadata" {
        $refreshArgs = @(
            "scripts\sync_editor_metadata.py",
            "--refresh",
            "--index-dir", ("data\" + $resolvedNamespace)
        )
        if ($ProjectsRoot -and $ProjectsRoot -ne "data") {
            $refreshArgs += @("--export-dir", $ProjectsRoot)
        }
        if ($ProjectName -and $ProjectName -ne "ScratchPrototype") {
            $refreshArgs += @("--project-name", $ProjectName)
        }
        if ($Question -and $Question -like "/Game*") {
            $refreshArgs += @("--content-path", $Question)
        }
        if ($SkipBuild) { $refreshArgs += "--no-rebuild" }
        & $py @refreshArgs
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    "install-editor-graph-plugin" {
        $pluginArgs = @(
            "scripts\install_editor_graph_plugin.py",
            "--workspace", $PSScriptRoot
        )
        if ($ProjectFile) {
            $pluginArgs += @("--project", $ProjectFile)
        }
        if (-not $Force) { $pluginArgs += "--update" }
        if ($Force) { $pluginArgs += "--force" }
        if (-not $SkipBuild) { $pluginArgs += "--build" }
        if ($DryRun) { $pluginArgs += "--dry-run" }
        & $py @pluginArgs
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    "watch-active-project" {
        $watchArgs = @("scripts\watch_active_project.py")
        if ($ProjectFile) {
            $watchArgs += @("--project", $ProjectFile)
        }
        if ($DryRun) { $watchArgs += "--dry-run" }
        & $py @watchArgs
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    "index-full" {
        $pipelineArgs = @(
            "-File", (Join-Path $PSScriptRoot "scripts\run_index_pipeline.ps1"),
            "-WorkspaceRoot", $PSScriptRoot
        )
        if ($SkipEditorIngest) { $pipelineArgs += "-SkipEditorIngest" }
        if ($SkipBuild) { $pipelineArgs += "-SkipBuild" }
        powershell -NoProfile -ExecutionPolicy Bypass @pipelineArgs
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    "ingest-editor-exports" {
        if (-not $ProjectsRoot -or $ProjectsRoot -eq "data") {
            throw "ingest-editor-exports requires -ProjectsRoot pointing to Editor export JSONL directory"
        }
        $ingestArgs = @(
            "scripts\ingest_editor_exports.py",
            "--export-dir", $ProjectsRoot,
            "--out-dir", ("data\" + $resolvedNamespace)
        )
        if ($ProjectName -and $ProjectName -ne "ScratchPrototype") {
            $ingestArgs += @("--project-name", $ProjectName)
        }
        & $py @ingestArgs
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        if (-not $SkipBuild) {
            & $py scripts\incremental_build.py --out-dir ("data\" + $resolvedNamespace) --force
        }
    }
    "warm-cache" {
        & $py scripts\warm_symbol_cache.py
    }
    "phase3-finish" {
        powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "scripts\run_phase3_finish.ps1")
    }
    "pick-project" {
        $pickArgs = @("-File", (Join-Path $PSScriptRoot "scripts\pick_active_project.ps1"))
        if ($Explorer) { $pickArgs += "-Explorer" }
        if ($ClearActiveProject) { $pickArgs += "-Clear" }
        powershell -NoProfile -ExecutionPolicy Bypass @pickArgs
    }
    "promote-index" {
        powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "scripts\promote_staging_index.ps1")
    }
    "query" {
        if (-not $Question) {
            throw "Pass a value with -Question."
        }
        $queryArgs = @("scripts\query_rag.py", "--index", "$dataDir\rag.sqlite", "--mode", $Mode)
        if ($PSBoundParameters.ContainsKey("TopK")) {
            $queryArgs += @("--top-k", $TopK)
        }
        if ($PrintPrompts) {
            $queryArgs += "--print-prompt"
        }
        foreach ($value in $Source) { $queryArgs += @("--source", $value) }
        foreach ($value in $Project) { $queryArgs += @("--project", $value) }
        foreach ($value in $Layer) { $queryArgs += @("--layer", $value) }
        foreach ($value in $DocType) { $queryArgs += @("--doc-type", $value) }
        foreach ($value in $Genre) { $queryArgs += @("--genre", $value) }
        foreach ($value in $Extension) { $queryArgs += @("--extension", $value) }
        foreach ($value in $RequiredTerm) { $queryArgs += @("--required-term", $value) }
        $queryArgs += $Question
        & $py @queryArgs
    }
    "ask" {
        if (-not $Question) {
            throw "Pass a value with -Question."
        }
        $askArgs = @("scripts\query_rag.py", "--index", "$dataDir\rag.sqlite", "--ask-lmstudio", "--mode", $Mode)
        if ($PSBoundParameters.ContainsKey("TopK")) {
            $askArgs += @("--top-k", $TopK)
        }
        if ($Model) {
            $askArgs += @("--model", $Model)
        }
        foreach ($value in $Source) { $askArgs += @("--source", $value) }
        foreach ($value in $Project) { $askArgs += @("--project", $value) }
        foreach ($value in $Layer) { $askArgs += @("--layer", $value) }
        foreach ($value in $DocType) { $askArgs += @("--doc-type", $value) }
        foreach ($value in $Genre) { $askArgs += @("--genre", $value) }
        foreach ($value in $Extension) { $askArgs += @("--extension", $value) }
        foreach ($value in $RequiredTerm) { $askArgs += @("--required-term", $value) }
        $askArgs += $Question
        & $py @askArgs
    }
    "eval-game-design" {
        & $py scripts\evaluate_rag_queries.py --index $indexPath --query-set $QuerySet
    }
    "eval-unreal-programming" {
        & $py scripts\evaluate_rag_queries.py --index $indexPath --query-set $ProgrammingQuerySet
    }
    "eval-prototype" {
        & $py scripts\evaluate_rag_queries.py --index $indexPath --query-set config\rag_eval_prototype_queries.json
    }
    "eval-refactor" {
        & $py scripts\evaluate_refactor_plans.py
    }
    "eval-refactor-rag" {
        & $py scripts\evaluate_rag_queries.py --index $indexPath --query-set config\rag_eval_refactor_queries.json
    }
    "eval-unreal-review" {
        $reviewArgs = @("scripts\evaluate_unreal_review_answers.py", "--case-set", $ReviewCaseSet)
        if ($Answers) {
            $reviewArgs += @("--answers", $Answers)
        } elseif (-not $AnswersDir -and -not $PrintPrompts) {
            $defaultAnswers = Join-Path $PSScriptRoot "tests\fixtures\unreal_review_answers\good_answers.jsonl"
            if (Test-Path $defaultAnswers) {
                $reviewArgs += @("--answers", $defaultAnswers)
            }
        }
        if ($AnswersDir) {
            $reviewArgs += @("--answers-dir", $AnswersDir)
        }
        if ($PrintPrompts) {
            $reviewArgs += "--print-prompts"
        }
        & $py @reviewArgs
    }
    "eval-debug" {
        & $py scripts\evaluate_rag_queries.py --index $indexPath --query-set config\rag_eval_debug_queries.json
    }
    "eval-sequencer" {
        & $py scripts\evaluate_rag_queries.py --index $indexPath --query-set config\rag_eval_sequencer_queries.json
    }
    "knowledge-audit" {
        & $py scripts\knowledge_audit.py
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
    "eval-genre" {
        & $py scripts\evaluate_rag_queries.py --index $indexPath --query-set config\rag_eval_genre_queries.json
    }
    "eval-e2e-compile" {
        $e2eArgs = @("scripts\eval_e2e_compile.py")
        if ($RunUbt) { $e2eArgs += "--run-ubt" }
        & $py @e2eArgs
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    "eval-reasoning" {
        & $py scripts\eval_reasoning.py
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    "eval-agent-harness" {
        & $py scripts\eval_agent_harness.py
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    "eval-project-review" {
        $reviewProjectArgs = @("scripts\eval_project_review.py")
        if ($Live) { $reviewProjectArgs += "--live" }
        if ($RequireLive) { $reviewProjectArgs += "--require-live" }
        if ($Model) { $reviewProjectArgs += @("--model", $Model) }
        & $py @reviewProjectArgs
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    "eval-soulslike-live" {
        $soulslikeArgs = @("scripts\eval_soulslike_live.py")
        if ($Live) { $soulslikeArgs += "--no-dry-run" }
        if ($RequireLive) { $soulslikeArgs += "--require-live" }
        if ($Model) { $soulslikeArgs += @("--model", $Model) }
        & $py @soulslikeArgs
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    "eval-pass-at-k" {
        $pakArgs = @("scripts\eval_pass_at_k.py")
        if ($Live) { $pakArgs += "--live" } else { $pakArgs += "--dry-run" }
        if ($RequireLive) { $pakArgs += "--require-live" }
        if ($Model) { $pakArgs += @("--model", $Model) }
        & $py @pakArgs
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    "preflight-lmstudio" {
        $pfArgs = @("scripts\preflight_lmstudio.py")
        if ($RequireLive) { $pfArgs += "--json" }
        & $py @pfArgs
        if ($RequireLive -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    "report-tier-kpi" {
        & $py scripts\report_tier_kpi.py
    }
    "eval-harness" {
        & $py scripts\run_eval_harness.py
    }
    "eval-regression" {
        $regArgs = @("scripts\run_eval_regression.py")
        if ($Live) { $regArgs += "--live" }
        & $py @regArgs
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    "summarize-real-project-eval" {
        if (-not $Question) {
            throw "summarize-real-project-eval requires -Question pointing to a real-project eval JSON file"
        }
        & $py scripts\summarize_real_project_eval.py --input $Question --out-dir Reports\real_project_eval\latest
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    "verify-release" {
        & $py scripts\verify_release.py
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    "build-project-graph" {
        if (-not $ProjectFile) {
            $cfg = Get-Content -LiteralPath (Join-Path $HOME ".lmstudio\config\unreal-workspace.json") -Raw -Encoding UTF8 | ConvertFrom-Json
            $ProjectFile = $cfg.activeProject
        }
        if (-not $ProjectFile) { throw "build-project-graph requires -ProjectFile or activeProject" }
        $projRoot = Split-Path $ProjectFile -Parent
        $graphArgs = @("scripts\build_project_graph.py", "--project-root", $projRoot)
        if ($ProjectName) { $graphArgs += @("--project-name", $ProjectName) }
        & $py @graphArgs
    }
    "agent-plan" {
        if (-not $Question) { throw "agent-plan requires -Question" }
        & $py scripts\agent_orchestrator.py --request $Question --mode $Mode --json
    }
    "reject-failure-memory" {
        if (-not $ProjectName -or -not $Question) {
            throw "reject-failure-memory requires -ProjectName and -Question (record id)"
        }
        & $py scripts\reject_failure_memory.py --project-name $ProjectName --record-id $Question
    }
    "collect-editor-metadata" {
        if (-not $ProjectName) { throw "collect-editor-metadata requires -ProjectName" }
        if (-not $Question) { throw "collect-editor-metadata requires -Question as export spec path:type" }
        & $py scripts\collect_editor_metadata.py --project-name $ProjectName --out-dir $dataDir --export $Question
    }
    "sonnet-tier-gate" {
        $gateArgs = @("-File", (Join-Path $PSScriptRoot "scripts\run_sonnet_tier_gate.ps1"))
        if ($Live) { $gateArgs += "-Live" }
        powershell -NoProfile -ExecutionPolicy Bypass @gateArgs
    }
    "scaffold-prototype" {
        & powershell -NoProfile -ExecutionPolicy Bypass -File scripts\scaffold_prototype.ps1 -Genre $ScaffoldGenre
    }
    "agent-session" {
        if (-not $Question) {
            throw "agent-session requires -Question"
        }
        $sessionArgs = @("scripts\unreal_agent_session.py", "--request", $Question, "--mode", $Mode)
        & $py @sessionArgs
    }
    "test-build-logs" {
        & $py scripts\test_collect_build_logs_fixture.py
    }
    "test-unreal-readiness" {
        & $py scripts\test_unreal_readiness_fixture.py
    }
    "ubt-feedback" {
        if (-not $ProjectFile) {
            throw "Pass -ProjectFile with a .uproject path."
        }
        if (-not $UbtTarget) {
            throw "Pass -UbtTarget, for example MyGameEditor."
        }
        $feedbackArgs = @(
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            ".\scripts\run_ubt_feedback_loop.ps1",
            "-ProjectFile",
            $ProjectFile,
            "-Target",
            $UbtTarget,
            "-Platform",
            $UbtPlatform,
            "-Configuration",
            $UbtConfiguration,
            "-UbtPath",
            $UbtPath,
            "-Mode",
            $Mode,
            "-Question",
            $Question
        )
        powershell @feedbackArgs
    }
    "wrapper" {
        if (-not $Question -and -not $RequestFile) {
            throw "Pass -Question or -RequestFile with the implementation request."
        }
        $wrapperMode = if ($Mode -eq "auto") { "agent_edit" } else { $Mode }
        $wrapperArgs = @(
            "scripts\lmstudio_unreal_wrapper.py",
            "--index",
            "$dataDir\rag.sqlite",
            "--module-graph",
            $ModuleGraph,
            "--mode",
            $wrapperMode,
            "--top-k",
            $TopK,
            "--project-name",
            $ProjectName,
            "--scratch-root",
            $ScratchRoot,
            "--max-attempts",
            $MaxAttempts,
            "--ubt-path",
            $UbtPath,
            "--platform",
            $UbtPlatform,
            "--configuration",
            $UbtConfiguration,
            "--build-timeout",
            $BuildTimeout
        )
        if ($Question) {
            $wrapperArgs += @("--request", $Question)
        }
        if ($RequestFile) {
            $wrapperArgs += @("--request-file", $RequestFile)
        }
        if ($Model) {
            $wrapperArgs += @("--model", $Model)
        }
        if ($ProjectFile) {
            $wrapperArgs += @("--project-file", $ProjectFile)
        }
        if ($UbtTarget) {
            $wrapperArgs += @("--target", $UbtTarget)
        }
        if ($SkipBuild) {
            $wrapperArgs += "--skip-build"
        }
        if ($SkipStaticGate) {
            $wrapperArgs += "--skip-static-gate"
        }
        if ($AllowEmptyFiles) {
            $wrapperArgs += "--allow-empty-files"
        }
        if ($AllowDirectProjectWrite) {
            $wrapperArgs += "--allow-direct-project-write"
        }
        if ($DryRun) {
            $wrapperArgs += "--dry-run"
        }
        & $py @wrapperArgs
    }
    "lmstudio-models" {
        try {
            Invoke-RestMethod -Uri "http://localhost:1234/v1/models" -Method Get -TimeoutSec 5 | ConvertTo-Json -Depth 8
        }
        catch {
            Write-Host "LM Studio server is not reachable at http://localhost:1234/v1"
            Write-Host "Open LM Studio, load a model, and start the local server from the Developer tab."
        }
    }
    "doctor" {
        & $py scripts\rag_doctor.py --rag-root (Get-Location).Path
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
    "bench-mcp" {
        & $py scripts\bench_mcp.py --rag-root (Get-Location).Path --index $indexPath --ubt-path $UbtPath
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
    "bench-token-budget" {
        & $py scripts\bench_token_budget.py
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
    "review-project" {
        if (-not $Question) {
            throw "review-project requires -Question"
        }
        $reviewArgs = @(
            "scripts\lmstudio_unreal_wrapper.py",
            "--index", $indexPath,
            "--mode", "review",
            "--top-k", $TopK,
            "--request", $Question,
            "--skip-build",
            "--dry-run"
        )
        if ($Model) { $reviewArgs += @("--model", $Model) }
        if ($ProjectFile) { $reviewArgs += @("--project-file", $ProjectFile) }
        & $py @reviewArgs
    }
    "validate-index" {
        & $py scripts\validate_index.py --index $indexPath
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
    "update-engine" {
        $sourceArgs = @("scripts\collect_unreal_source.py", "--root", $SourceRoot, "--out", (Join-Path $dataDir "raw_source.jsonl"))
        if ($IncludeThirdParty) {
            $sourceArgs += "--include-third-party"
        }
        & $py @sourceArgs
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

        $symbolArgs = @(
            "scripts\collect_unreal_symbols.py",
            "--root", $SourceRoot,
            "--out", (Join-Path $dataDir "raw_symbols.jsonl"),
            "--sidecar-out", (Join-Path $dataDir "sidecar_symbols_meta.jsonl"),
            "--tier", "public"
        )
        if ($IncludeThirdParty) {
            $symbolArgs += "--include-third-party"
        }
        if ($IncludeSymbolDefinitions) {
            $symbolArgs += "--include-definitions"
            $symbolArgs += "--tier"
            $symbolArgs += "full"
        }
        & $py @symbolArgs
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

        $symbolsPath = Join-Path $dataDir "raw_symbols.jsonl"
        if (-not (Test-Path $symbolsPath)) {
            throw "collect-symbols did not produce $symbolsPath"
        }
        & $py scripts\build_unreal_module_graph.py --symbols $symbolsPath --out (Join-Path $dataDir "raw_module_graph.jsonl") --report Reports\unreal_module_include_graph.md
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

        & $py scripts\incremental_build.py --out-dir $dataDir
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    "update-project" {
        powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "scripts\sync_active_project.ps1")
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    "update-guidelines" {
        & $py scripts\collect_project_guidelines.py --root $GuidelinesRoot --out (Join-Path $dataDir "raw_guidelines.jsonl")
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $py scripts\incremental_build.py --out-dir $dataDir
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
}
