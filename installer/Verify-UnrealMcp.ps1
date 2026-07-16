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
Check "unreal_rag_mcp.py compile" {
    Push-Location (Join-Path $ragRoot "scripts")
    try { & $py -m py_compile unreal_rag_mcp.py rag_search.py workspace_paths.py }
    finally { Pop-Location }
}
Check "agent server.js" { if (-not (Test-Path (Join-Path $agentRoot "src\server.js"))) { throw "missing" } }
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
    $previousEssential = $env:MCP_ESSENTIAL_TOOLS
    $previousStateRoot = $env:AGENT_STATE_ROOT
    $previousSharedConfig = $env:SHARED_UNREAL_CONFIG
    $verifyRoot = Join-Path $env:TEMP ("unreal-agent-verify-" + [guid]::NewGuid().ToString("N"))
    try {
        $env:MCP_ESSENTIAL_TOOLS = "1"
        $env:AGENT_STATE_ROOT = Join-Path $verifyRoot "state\unreal-agent"
        $env:SHARED_UNREAL_CONFIG = Join-Path $verifyRoot "config\unreal-workspace.json"
        $init = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"verify","version":"1.0"}}}'
        $list = '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
        $input = "$init`n$list`n"
        $stdout = ($input | & node (Join-Path $agentRoot "src\server.js") 2>$null | Out-String)
        if ($stdout -notmatch '"tools"') { throw "tools/list did not return tools array" }
        if ($stdout -notmatch 'read_file') { throw "essential tool read_file missing from tools/list" }
    }
    finally {
        if ($null -eq $previousEssential) { Remove-Item Env:MCP_ESSENTIAL_TOOLS -ErrorAction SilentlyContinue } else { $env:MCP_ESSENTIAL_TOOLS = $previousEssential }
        if ($null -eq $previousStateRoot) { Remove-Item Env:AGENT_STATE_ROOT -ErrorAction SilentlyContinue } else { $env:AGENT_STATE_ROOT = $previousStateRoot }
        if ($null -eq $previousSharedConfig) { Remove-Item Env:SHARED_UNREAL_CONFIG -ErrorAction SilentlyContinue } else { $env:SHARED_UNREAL_CONFIG = $previousSharedConfig }
        if (Test-Path -LiteralPath $verifyRoot) { Remove-Item -LiteralPath $verifyRoot -Recurse -Force }
    }
}
Check "agent state-root module" { if (-not (Test-Path (Join-Path $agentRoot "src\state-root.js"))) { throw "missing state-root.js" } }
Check "rag shared state_root.py" { if (-not (Test-Path (Join-Path $root "scripts\state_root.py"))) { throw "missing scripts/state_root.py" } }
Check "tool contract registry" { if (-not (Test-Path (Join-Path $root "config\tool_contract.json"))) { throw "missing config/tool_contract.json" } }
Check "context compactor source" {
    $pluginRoot = Join-Path $ragRoot "lmstudio-context-compactor-plugin"
    foreach ($required in @("manifest.json", "package.json", "src\generator.ts", "src\compaction-core.js")) {
        if (-not (Test-Path -LiteralPath (Join-Path $pluginRoot $required))) {
            throw "missing lmstudio-context-compactor-plugin\$required"
        }
    }
}
Check "agent write-locks.js" { if (-not (Test-Path (Join-Path $agentRoot "src\write-locks.js"))) { throw "missing write-locks.js (single-flight write guard)" } }
Check "agent mutation-history.js" { if (-not (Test-Path (Join-Path $agentRoot "src\mutation-history.js"))) { throw "missing mutation-history.js (duplicate-call loop breaker)" } }
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
        throw "plugin not installed - run INSTALL-AGENT-MODE.bat or Install_Context_Compactor.cmd"
    }
    $installedManifest = Get-Content -LiteralPath $installedManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([int]$installedManifest.revision -ne [int]$sourceManifest.revision) {
        throw "revision mismatch: source=$($sourceManifest.revision) installed=$($installedManifest.revision)"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $installedRoot ".lmstudio\production.js"))) {
        throw "installed plugin production entry missing"
    }
    $sourceGenerator = Join-Path $ragRoot "lmstudio-context-compactor-plugin\dist\generator.js"
    $installedGenerator = Join-Path $installedRoot "dist\generator.js"
    if (Test-Path -LiteralPath $sourceGenerator) {
        if (-not (Test-Path -LiteralPath $installedGenerator)) {
            throw "installed plugin generator missing"
        }
        if ((Get-FileHash -LiteralPath $sourceGenerator -Algorithm SHA256).Hash -ne
            (Get-FileHash -LiteralPath $installedGenerator -Algorithm SHA256).Hash) {
            throw "installed plugin does not match the local tested build"
        }
    }
}
$activationScript = Join-Path $ragRoot "scripts\Test-ContextCompactorActivation.ps1"
if (-not (Test-Path -LiteralPath $activationScript)) {
    Check "context compactor activation checker" { throw "missing scripts\Test-ContextCompactorActivation.ps1" }
}
else {
    $activationArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $activationScript, "-Json")
    if ($RequireContextCompaction) { $activationArgs += "-RequireCompaction" }
    $activationOutput = & powershell @activationArgs 2>&1 | Out-String
    $activationExit = $LASTEXITCODE
    if ($activationExit -eq 0) {
        Write-Host "[PASS] Context compactor activation evidence" -ForegroundColor Green
    }
    elseif ($RequireContextCompactorActivation -or $RequireContextCompaction) {
        Check "context compactor activation evidence" {
            throw "proxy activation was not proven: $($activationOutput.Trim())"
        }
    }
    else {
        Warn "Context compactor is installed but has no runtime activation evidence. Select unreal-context-compactor as the chat model; selecting the underlying model bypasses it."
    }
}
}
Check "mcp.json unreal-rag python" {
    $mcp = Join-Path $HOME ".lmstudio\mcp.json"
    if (-not (Test-Path $mcp)) { throw "mcp.json missing - run INSTALL-SAFE-MODE.bat" }
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
    if (-not (Test-Path $mcp)) { throw "mcp.json missing - run INSTALL-SAFE-MODE.bat" }
    $cfg = Get-Content -LiteralPath $mcp -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $cfg.mcpServers."unreal-rag") { throw "unreal-rag not in mcp.json" }
}
Check "mcp.json AGENT_STATE_ROOT parity" {
    $mcp = Join-Path $HOME ".lmstudio\mcp.json"
    if (-not (Test-Path $mcp)) { throw "mcp.json missing - run INSTALL-SAFE-MODE.bat" }
    $cfg = Get-Content -LiteralPath $mcp -Raw -Encoding UTF8 | ConvertFrom-Json
    $ragRoot = [string]$cfg.mcpServers."unreal-rag".env.AGENT_STATE_ROOT
    $agentRoot = [string]$cfg.mcpServers."unreal-agent".env.AGENT_STATE_ROOT
    if (-not $ragRoot) { throw "unreal-rag missing AGENT_STATE_ROOT" }
    if (-not $agentRoot) { throw "unreal-agent missing AGENT_STATE_ROOT" }
    if ($ragRoot -ne $agentRoot) {
        throw "AGENT_STATE_ROOT mismatch: rag=$ragRoot agent=$agentRoot"
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
            throw "unresolved placeholders in $p - re-run Install-ClineUnrealMcp.ps1"
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
        Warn "Cline MCP settings not configured; run Install-ClineUnrealMcp.ps1 only if you use Cline."
    }
}
Check "clinerules" {
    if (-not (Test-Path (Join-Path $ragRoot ".clinerules"))) { throw "missing .clinerules" }
}
Check "validate-write hook" {
    if (-not (Test-Path (Join-Path $agentRoot "src\validate-write.js"))) { throw "missing validate-write.js" }
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
