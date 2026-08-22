param(
    [string]$PortableRoot = "",
    [switch]$RepoOnly,
    [switch]$SkipContextCompactor,
    [switch]$RequireContextCompactorActivation,
    [switch]$RequireContextCompaction
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "Resolve-StackLayout.ps1")
. (Join-Path $PSScriptRoot "Install-PathHelpers.ps1")

$layout = Resolve-StackLayout $PortableRoot
$root = $layout.Root
$ragRoot = $layout.RagRoot
$agentRoot = $layout.AgentRoot
$py = & {
    $bundled = Join-Path $HOME ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path $bundled) { return $bundled }
    $c = Get-Command python -ErrorAction SilentlyContinue
    if ($c -and $c.Source -notlike "*\WindowsApps\*") { return $c.Source }
    throw "python not found"
}

$fail = 0
function Check([string]$Label, [scriptblock]$Test) {
    try {
        & $Test
        Write-Host ('[PASS] ' + $Label) -ForegroundColor Green
    }
    catch {
        Write-Host ('[FAIL] ' + $Label + ' - ' + $_.Exception.Message) -ForegroundColor Red
        $script:fail++
    }
}

function Warn([string]$Message) {
    Write-Host ('[WARN] ' + $Message) -ForegroundColor Yellow
}

$engineRoot = Get-WorkspaceEngineRootPath -RagRoot $ragRoot
$ubtPath = Get-WorkspaceUbtPath -RagRoot $ragRoot

Check "Portable root" { if (-not (Test-Path $root)) { throw "missing $root" } }
Check "RAG workspace" { if (-not (Test-Path (Join-Path $ragRoot "rag.ps1"))) { throw "missing rag.ps1" } }
Check "RAG index" {
    . (Join-Path $PSScriptRoot "Install-PathHelpers.ps1")
    $indexPath = Resolve-RagIndexPath -RagRoot $ragRoot
    if (-not (Test-Path $indexPath)) {
        if ($RepoOnly) {
            Warn "RAG index missing (BYOI): $indexPath"
            return
        }
        throw "missing $indexPath"
    }
}
Check "workspace.json rootPath" {
    $cfg = Read-JsonObject (Join-Path $ragRoot "config\workspace.json")
    if (-not $cfg -or [string]::IsNullOrWhiteSpace([string]$cfg.rootPath)) {
        if ($RepoOnly) {
            Warn "rootPath empty - expected for OSS clone until Sync-InstallMachinePaths.ps1"
            return
        }
        throw "rootPath empty - run installer or Sync-InstallMachinePaths.ps1"
    }
    if ([string]$cfg.rootPath -like "*\\Users\\*\\Users\\*") {
        throw "rootPath looks malformed: $($cfg.rootPath)"
    }
    $resolvedRoot = (Resolve-Path -LiteralPath $ragRoot).Path
    if ((Resolve-Path -LiteralPath ([string]$cfg.rootPath)).Path -ne $resolvedRoot) {
        Warn "workspace.json rootPath differs from repo root; run Sync-InstallMachinePaths.ps1"
    }
}
Check "agent-mcp.json search roots" {
    $agentCfg = Read-JsonObject (Join-Path $agentRoot "config\agent-mcp.json")
    if (-not $agentCfg -or -not $agentCfg.projectSearchRoots -or $agentCfg.projectSearchRoots.Count -eq 0) {
        if ($RepoOnly) {
            Warn "projectSearchRoots missing in agent-mcp.json template"
            return
        }
        throw "projectSearchRoots missing - run INSTALL-*.bat"
    }
    if ($RepoOnly) { return }
    foreach ($searchRoot in @($agentCfg.projectSearchRoots)) {
        $text = [string]$searchRoot
        if ($text -match '\\Users\\' -and -not $text.StartsWith($HOME, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "agent-mcp.json contains another machine/user path: $searchRoot"
        }
    }
}
Check "Direct RAG Python compile" {
    Push-Location (Join-Path $ragRoot "scripts")
    try {
        & $py -m py_compile `
            unreal_rag_direct.py `
            direct_rag_server.py `
            direct_rag_evidence.py `
            direct_rag_status.py `
            direct_rag_contract.py `
            direct_rag_corpus.py `
            direct_rag_delivery.py `
            direct_rag_atomic_replace.py `
            direct_rag_backup_restore.py `
            direct_rag_freshness.py `
            direct_rag_freshness_rows.py `
            direct_rag_generation_boundary.py `
            direct_rag_generation_identity.py `
            direct_rag_generation_swap.py `
            direct_rag_all_refresh.py `
            direct_rag_build_generation.py `
            direct_rag_public_build.py `
            direct_rag_raw_provenance.py `
            direct_rag_raw_scope.py `
            direct_rag_project_engine.py `
            direct_rag_project_generation.py `
            direct_rag_project_selectors.py `
            direct_rag_manifest_binding.py `
            direct_rag_index_registry.py `
            direct_rag_index_ownership.py `
            direct_rag_named_index.py `
            direct_rag_named_candidate.py `
            direct_rag_request_binding.py `
            direct_rag_shard_selection.py `
            direct_rag_unbuilt_shard.py `
            direct_rag_history.py `
            direct_rag_projects.py `
            direct_rag_project_cache.py `
            direct_rag_project_refresh.py `
            direct_rag_project_collection.py `
            direct_rag_project_merge.py `
            direct_rag_project_set.py `
            direct_rag_probe.py `
            direct_rag_editor_stage.py `
            direct_rag_editor_snapshot.py `
            direct_rag_engine_collection.py `
            direct_rag_engine_tier.py `
            direct_rag_refresh_target.py `
            direct_rag_refresh_facts.py `
            direct_rag_refresh_lock.py `
            direct_rag_refresh_journal.py `
            direct_rag_refresh_recovery.py `
            direct_rag_refresh_transaction.py `
            direct_rag_refresh_cli.py `
            direct_rag_startup_recovery.py `
            direct_rag_search.py `
            direct_rag_symbol.py `
            direct_rag_index.py `
            direct_rag_lexical.py `
            direct_rag_limits.py `
            direct_rag_retrieval.py `
            direct_rag_selection.py `
            direct_rag_result.py `
            direct_rag_runtime.py `
            direct_rag_sql.py `
            direct_rag_readonly_db.py `
            direct_rag_symbol_query.py `
            direct_rag_build_binding.py `
            rag_build_classification.py `
            rag_build_input.py `
            rag_build_metadata.py `
            rag_build_metadata_projection.py `
            rag_build_outputs.py `
            rag_build_schema.py `
            rag_build_writer.py `
            active_project_sync.py `
            active_project_paths.py `
            editor_export_paths.py `
            editor_export_runner.py `
            editor_export_settings.py `
            editor_export_location.py `
            editor_export_project.py `
            editor_export_markers.py `
            editor_export_process.py `
            editor_export_mode.py `
            editor_export_contract.py `
            editor_capture_state.py `
            editor_metadata_catalog.py `
            editor_metadata_provenance.py `
            editor_metadata_sources.py `
            editor_metadata_identity.py `
            editor_metadata_projection.py `
            editor_metadata_search_text.py `
            editor_metadata_jsonl.py `
            editor_metadata_merge.py `
            editor_metadata_cli.py `
            editor_sync_decision.py `
            sync_editor_metadata.py `
            editor_sync_context.py `
            editor_sync_capture.py `
            editor_sync_coordinator.py `
            editor_sync_cli.py `
            unreal_static_validate.py `
            unreal_static_model.py `
            unreal_static_scan.py `
            unreal_static_reflection.py `
            unreal_static_delegate.py `
            unreal_static_lifecycle.py `
            unreal_static_build.py `
            unreal_static_include.py `
            unreal_static_network.py `
            unreal_static_crossfile.py `
            unreal_static_safety.py `
            unreal_static_registry.py `
            unreal_static_runner.py `
            portable_path_identity.py `
            unreal_engine_discovery.py `
            unreal_engine_registration.py `
            unreal_engine_resolution.py `
            unreal_engine_runtime_paths.py `
            workspace_config.py `
            workspace_index_paths.py `
            workspace_locator.py `
            workspace_paths.py `
            ../installer/direct_rag_build.py `
            ../installer/direct_rag_build_model.py `
            ../installer/direct_rag_build_scope.py `
            ../installer/direct_rag_build_stage.py `
            ../installer/direct_rag_build_steps.py
    }
    finally { Pop-Location }
}
Check "Direct RAG startup smoke" {
    $init = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"verify","version":"1.0"}}}'
    $list = '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
    $requests = "$init`n$list`n"
    $stdout = ($requests | & $py (Join-Path $ragRoot "scripts\unreal_rag_direct.py") 2>$null | Out-String)
    if ($stdout -notmatch 'unreal_rag_search') { throw "Direct RAG search capability missing" }
    if ($stdout -match 'unreal_agent_plan|unreal_task_|taskAuthorization|requiredNextTool') {
        throw "Direct RAG catalog leaked legacy workflow control"
    }
}
Check "agent Direct server" { if (-not (Test-Path (Join-Path $agentRoot "src\direct-server.js"))) { throw "missing" } }
Check "agent Strict server" { if (-not (Test-Path (Join-Path $agentRoot "src\strict-server.js"))) { throw "missing" } }
Check "agent src JS syntax" {
    $jsFiles = Get-ChildItem -Path (Join-Path $agentRoot "src") -Filter *.js -Recurse
    if ($jsFiles.Count -eq 0) { throw "no JS files under agent src" }
    foreach ($file in $jsFiles) {
        $out = & node --check $file.FullName 2>&1
        if ($LASTEXITCODE -ne 0) { throw "syntax error in $($file.Name): $out" }
    }
}
Check "agent MCP startup smoke" {
    $nodeModules = Join-Path $agentRoot "node_modules\@modelcontextprotocol\sdk"
    if ($RepoOnly -and -not (Test-Path -LiteralPath $nodeModules)) {
        Warn "agent node_modules missing - skipped startup smoke (run npm ci in lmstudio-unreal-agent-mcp)"
        return
    }
    $previousStateRoot = $env:AGENT_STATE_ROOT
    $previousSharedConfig = $env:SHARED_UNREAL_CONFIG
    $previousUnrealRoot = $env:UNREAL58_ROOT
    $verifyRoot = Join-Path $env:TEMP ("unreal-agent-verify-" + [guid]::NewGuid().ToString("N"))
    try {
        $env:AGENT_STATE_ROOT = Join-Path $verifyRoot "state\unreal-agent"
        $env:SHARED_UNREAL_CONFIG = Join-Path $verifyRoot "config\unreal-workspace.json"
        $env:UNREAL58_ROOT = $ragRoot
        $init = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"verify","version":"1.0"}}}'
        $list = '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
        $requests = "$init`n$list`n"
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $stdout = ($requests | & node (Join-Path $agentRoot "src\direct-server.js") 2>$null | Out-String)
        }
        finally {
            $ErrorActionPreference = $prevEap
        }
        if ($stdout -notmatch '"tools"') { throw "tools/list did not return tools array" }
        if ($stdout -notmatch 'read_file') { throw "essential tool read_file missing from tools/list" }
        if ($stdout -match 'taskAuthorization|ownerCapability|requiredNextTool') { throw "Direct catalog leaked workflow control" }
    }
    finally {
        if ($null -eq $previousStateRoot) { Remove-Item Env:AGENT_STATE_ROOT -ErrorAction SilentlyContinue } else { $env:AGENT_STATE_ROOT = $previousStateRoot }
        if ($null -eq $previousSharedConfig) { Remove-Item Env:SHARED_UNREAL_CONFIG -ErrorAction SilentlyContinue } else { $env:SHARED_UNREAL_CONFIG = $previousSharedConfig }
        if ($null -eq $previousUnrealRoot) { Remove-Item Env:UNREAL58_ROOT -ErrorAction SilentlyContinue } else { $env:UNREAL58_ROOT = $previousUnrealRoot }
        if (Test-Path -LiteralPath $verifyRoot) { Remove-Item -LiteralPath $verifyRoot -Recurse -Force }
    }
}
Check "agent runtime-state-root module" { if (-not (Test-Path (Join-Path $agentRoot "src\runtime-state-root.js"))) { throw "missing runtime-state-root.js" } }
Check "tool contract registry" { if (-not (Test-Path (Join-Path $root "config\tool_contract.json"))) { throw "missing config/tool_contract.json" } }
Check "context compactor source" {
    $pluginRoot = Join-Path $ragRoot "lmstudio-context-compactor-plugin"
    foreach ($required in @(
        "manifest.json",
        "package.json",
        "src\index.ts",
        "src\prediction-loop.ts",
        "src\direct-compaction-core.js",
        "src\direct-config.ts",
        "scripts\status.cjs"
    )) {
        if (-not (Test-Path -LiteralPath (Join-Path $pluginRoot $required))) {
            throw "missing lmstudio-context-compactor-plugin\$required"
        }
    }
}
Check "agent write-locks.js" { if (-not (Test-Path (Join-Path $agentRoot "src\write-locks.js"))) { throw "missing write-locks.js (single-flight write guard)" } }
Check "agent write-lock reclaim bridge" { if (-not (Test-Path (Join-Path $agentRoot "src\write-lock-reclaim-bridge.py"))) { throw "missing write-lock-reclaim-bridge.py (transactional stale-lock recovery)" } }
Check "agent direct-repeat-cache.js" { if (-not (Test-Path (Join-Path $agentRoot "src\direct-repeat-cache.js"))) { throw "missing direct-repeat-cache.js (observable-state repeat suppression)" } }
Check "agent mutation-semantic-guard.js" { if (-not (Test-Path (Join-Path $agentRoot "src\mutation-semantic-guard.js"))) { throw "missing mutation-semantic-guard.js (write-path semantic denylist bridge)" } }
Check "mutation_semantic_guard.py present" { if (-not (Test-Path (Join-Path $ragRoot "scripts\mutation_semantic_guard.py"))) { throw "missing scripts/mutation_semantic_guard.py" } }
Check "unreal_api_denylist.py present" { if (-not (Test-Path (Join-Path $ragRoot "scripts\unreal_api_denylist.py"))) { throw "missing scripts/unreal_api_denylist.py" } }
Check "mutation semantic guard python probe" {
    $previous = $env:PYTHONPATH
    $scriptsDir = Join-Path $ragRoot "scripts"
    try {
        $env:PYTHONPATH = if ($previous) { "$scriptsDir$([IO.Path]::PathSeparator)$previous" } else { $scriptsDir }
        $out = & $py -c "from unreal_api_denylist import check_denylist; import json; print(json.dumps({'ok': True, 'hits': check_denylist('')}, ensure_ascii=False))" 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) { throw "denylist import probe failed: $out" }
        if ($out -notmatch '"ok"\s*:\s*true') { throw "denylist import probe returned unexpected payload: $out" }
        $guardScript = Join-Path $scriptsDir "mutation_semantic_guard.py"
        $guardOut = ("" | & $py $guardScript 2>&1 | Out-String)
        if ($LASTEXITCODE -ne 0) { throw "mutation_semantic_guard.py probe failed: $guardOut" }
        if ($guardOut -notmatch '"ok"\s*:\s*true') { throw "mutation_semantic_guard.py probe returned unexpected payload: $guardOut" }
    }
    finally {
        if ($null -eq $previous) { Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue } else { $env:PYTHONPATH = $previous }
    }
}
Check "python version" {
    $out = & $py --version 2>&1 | Out-String
    if ($out -notmatch "Python") { throw $out.Trim() }
    if ($out -match "Python (\d+)\.(\d+)") {
        $major = [int]$Matches[1]; $minor = [int]$Matches[2]
        if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
            throw "Python 3.10+ required, found: $($out.Trim())"
        }
    }
}
Check "Unreal Engine install" {
    if ([string]::IsNullOrWhiteSpace([string]$engineRoot)) {
        Warn "Engine root not configured. Install UE or rerun Sync-InstallMachinePaths.ps1 after installing Epic Launcher."
        return
    }
    if (-not (Test-Path -LiteralPath $engineRoot)) {
        Warn "Engine root not found: $engineRoot. Install UE or rerun Sync-InstallMachinePaths.ps1 after installing Epic Launcher."
        return
    }
    if (-not (Test-Path -LiteralPath $ubtPath)) {
        Warn "UBT not found: $ubtPath"
    }
}
if (-not $RepoOnly) {
if (-not $SkipContextCompactor) {
Check "installed context compactor" {
    $sourceManifestPath = Join-Path $ragRoot "lmstudio-context-compactor-plugin\manifest.json"
    $sourceManifest = Get-Content -LiteralPath $sourceManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $installedRoot = Join-Path $HOME ".lmstudio\extensions\plugins\$($sourceManifest.owner)\$($sourceManifest.name)"
    $installedManifestPath = Join-Path $installedRoot "manifest.json"
    if (-not (Test-Path -LiteralPath $installedManifestPath)) {
        throw "plugin not installed - run the root integrated installer and choose the FULL profile"
    }
    $installedManifest = Get-Content -LiteralPath $installedManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([int]$installedManifest.revision -ne [int]$sourceManifest.revision) {
        throw "revision mismatch: source=$($sourceManifest.revision) installed=$($installedManifest.revision)"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $installedRoot ".lmstudio\production.js"))) {
        throw "installed plugin production entry missing"
    }
    $sourcePredictionLoop = Join-Path $ragRoot "lmstudio-context-compactor-plugin\dist\prediction-loop.js"
    $installedPredictionLoop = Join-Path $installedRoot "dist\prediction-loop.js"
    if (Test-Path -LiteralPath $sourcePredictionLoop) {
        if (-not (Test-Path -LiteralPath $installedPredictionLoop)) {
            throw "installed plugin prediction-loop bundle missing"
        }
        if ((Get-FileHash -LiteralPath $sourcePredictionLoop -Algorithm SHA256).Hash -ne
            (Get-FileHash -LiteralPath $installedPredictionLoop -Algorithm SHA256).Hash) {
            throw "installed plugin does not match the local tested build"
        }
    }
}
$activationScript = Join-Path $ragRoot "lmstudio-context-compactor-plugin\scripts\status.cjs"
if (-not (Test-Path -LiteralPath $activationScript)) {
    Check "context compactor activation checker" { throw "missing lmstudio-context-compactor-plugin\scripts\status.cjs" }
}
else {
    $activationArgs = @($activationScript, "--json")
    $requireRuntime = $RequireContextCompactorActivation -or $RequireContextCompaction
    if ($requireRuntime) { $activationArgs += "--require-runtime" }
    $activationNode = Get-Command node -ErrorAction SilentlyContinue
    if (-not $activationNode) {
        if ($RequireContextCompactorActivation -or $RequireContextCompaction) {
            Check "context compactor activation evidence" {
                throw "Node.js 20+ is required for the activation checker"
            }
        }
        else {
            Warn "Context compactor activation was not checked because Node.js is unavailable."
        }
    }
    else {
        $activationOutput = & $activationNode.Source @activationArgs 2>&1 | Out-String
        $activationExit = $LASTEXITCODE
        if ($activationExit -eq 0) {
            Write-Host "[PASS] Context compactor direct source mode" -ForegroundColor Green
            Warn "Confirm the plugin is enabled in LM Studio for the chat that uses the actual selected LLM."
        }
        elseif ($requireRuntime -and $activationExit -eq 3) {
            Check "context compactor runtime activation evidence" {
                throw "runtime activation is not exposed by the current LM Studio hook; verify the chat plugin panel: $($activationOutput.Trim())"
            }
        }
        else {
            Check "context compactor source mode" {
                throw "source verification failed: $($activationOutput.Trim())"
            }
        }
    }
}
}
Check "mcp.json unreal-rag python" {
    $mcp = Join-Path $HOME ".lmstudio\mcp.json"
    if (-not (Test-Path $mcp)) { throw "mcp.json missing - run the root integrated installer" }
    $cfg = Get-Content -LiteralPath $mcp -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $cfg.mcpServers."unreal-rag") { throw "unreal-rag not in mcp.json" }
    $cmd = [string]$cfg.mcpServers."unreal-rag".command
    if ($cmd -like "*\WindowsApps\*") { throw "WindowsApps python stub: $cmd" }
    if (-not (Test-Path $cmd)) { throw "python command missing: $cmd" }
    $ver = & $cmd --version 2>&1 | Out-String
    if ($ver -notmatch "Python") { throw "bad python: $ver" }
    if ($ver -match "Python (\d+)\.(\d+)") {
        $major = [int]$Matches[1]; $minor = [int]$Matches[2]
        if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
            throw "Python 3.10+ required in mcp.json, found: $($ver.Trim())"
        }
    }
}
Check "node.js version" {
    $nodeCmd = Get-Command node -ErrorAction SilentlyContinue
    if (-not $nodeCmd) { throw "node.exe not found in PATH. Install Node.js 20+ from https://nodejs.org/" }
    $nodeVer = & node --version 2>&1 | Out-String
    if ($nodeVer -match "v(\d+)\.") {
        $major = [int]$Matches[1]
        if ($major -lt 20) { throw "Node.js 20+ required, found: $($nodeVer.Trim())" }
    }
}
Check "mcp.json unreal-rag entry" {
    $mcp = Join-Path $HOME ".lmstudio\mcp.json"
    if (-not (Test-Path $mcp)) { throw "mcp.json missing - run the root integrated installer" }
    $cfg = Get-Content -LiteralPath $mcp -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $cfg.mcpServers."unreal-rag") { throw "unreal-rag not in mcp.json" }
}
Check "mcp.json Direct RAG state root" {
    $mcp = Join-Path $HOME ".lmstudio\mcp.json"
    if (-not (Test-Path $mcp)) { throw "mcp.json missing - run the root integrated installer" }
    $cfg = Get-Content -LiteralPath $mcp -Raw -Encoding UTF8 | ConvertFrom-Json
    $directRagStateRoot = [string]$cfg.mcpServers."unreal-rag".env.DIRECT_RAG_STATE_ROOT
    if ([string]::IsNullOrWhiteSpace($directRagStateRoot)) {
        throw "unreal-rag missing DIRECT_RAG_STATE_ROOT"
    }
}
Check "mcp.json agent state root" {
    $mcp = Join-Path $HOME ".lmstudio\mcp.json"
    if (-not (Test-Path $mcp)) { throw "mcp.json missing - run the root integrated installer" }
    $cfg = Get-Content -LiteralPath $mcp -Raw -Encoding UTF8 | ConvertFrom-Json
    $agentStateRoot = [string]$cfg.mcpServers."unreal-agent".env.AGENT_STATE_ROOT
    if ([string]::IsNullOrWhiteSpace($agentStateRoot)) {
        throw "unreal-agent missing AGENT_STATE_ROOT"
    }
}
Check "shared workspace config" {
    $p = Join-Path $HOME ".lmstudio\config\unreal-workspace.json"
    if (-not (Test-Path $p)) { throw "missing" }
}
Check "Cline MCP settings" {
    . (Join-Path $PSScriptRoot "Install-PathHelpers.ps1")
    $paths = @(
        (Join-Path $HOME ".cline\data\settings\cline_mcp_settings.json"),
        (Join-Path $env:APPDATA "Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json")
    )
    $found = $false
    foreach ($p in $paths) {
        if (-not (Test-Path $p)) { continue }
        $raw = Get-Content -LiteralPath $p -Raw -Encoding UTF8
        if (Test-ClineMcpHasUnresolvedPlaceholders $raw) {
            throw "unresolved placeholders in $p - rerun the root integrated installer with the Cline component"
        }
        $cfg = $raw | ConvertFrom-Json
        foreach ($name in @("unreal-rag", "unreal-agent")) {
            $entry = $cfg.mcpServers.$name
            if (-not $entry) { continue }
            $cmd = [string]$entry.command
            if (-not (Test-Path -LiteralPath $cmd)) {
                throw "$name command missing: $cmd"
            }
            foreach ($arg in @($entry.args)) {
                $argText = [string]$arg
                if ($argText -match '\.(py|js)$' -and -not (Test-Path -LiteralPath $argText)) {
                    throw "$name arg target missing: $argText"
                }
            }
        }
        if ($cfg.mcpServers."unreal-rag" -and $cfg.mcpServers."unreal-agent") {
            $found = $true
            break
        }
    }
    if (-not $found) {
        Warn "Cline MCP settings not configured; rerun the root integrated installer with the Cline component only if you use Cline."
    }
}
Check "clinerules" {
    if (-not (Test-Path (Join-Path $ragRoot ".clinerules"))) { throw "missing .clinerules" }
}
Check "Direct atomic mutation owners" {
    foreach ($required in @(
        "direct-bundle-capability.js",
        "direct-edit-bundle.js",
        "direct-edit-bundle-commit.js",
        "direct-edit-bundle-plan.js",
        "direct-static-validation.js",
        "direct-transaction-recovery.js",
        "direct-transaction-store.js"
    )) {
        if (-not (Test-Path -LiteralPath (Join-Path $agentRoot "src\$required"))) {
            throw "missing Direct atomic mutation owner: $required"
        }
    }
}
}

if ($fail -gt 0) {
    Write-Host ""
    Write-Host ($fail.ToString() + " check(s) failed.")
    exit 1
}
Write-Host ""
Write-Host "All checks passed."
exit 0
