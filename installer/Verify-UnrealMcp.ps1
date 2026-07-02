param(
    [string]$PortableRoot = ""
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
        Write-Host "[PASS] $Label" -ForegroundColor Green
    }
    catch {
        Write-Host "[FAIL] $Label — $($_.Exception.Message)" -ForegroundColor Red
        $script:fail++
    }
}

function Warn([string]$Message) {
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

$engineRoot = Get-WorkspaceEngineRootPath -RagRoot $ragRoot
$ubtPath = Get-WorkspaceUbtPath -RagRoot $ragRoot

Check "Portable root" { if (-not (Test-Path $root)) { throw "missing $root" } }
Check "RAG workspace" { if (-not (Test-Path (Join-Path $ragRoot "rag.ps1"))) { throw "missing rag.ps1" } }
Check "RAG index" { if (-not (Test-Path (Join-Path $ragRoot "data\unreal58\rag.sqlite"))) { throw "missing rag.sqlite" } }
Check "workspace.json rootPath" {
    $cfg = Read-JsonObject (Join-Path $ragRoot "config\workspace.json")
    if (-not $cfg -or [string]::IsNullOrWhiteSpace([string]$cfg.rootPath)) {
        throw "rootPath empty — run installer or Sync-InstallMachinePaths.ps1"
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
        throw "projectSearchRoots missing — run INSTALL-*.bat"
    }
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
    if (-not (Test-Path -LiteralPath $engineRoot)) {
        Warn "Engine root not found: $engineRoot. Install UE or rerun Sync-InstallMachinePaths.ps1 after installing Epic Launcher."
        return
    }
    if (-not (Test-Path -LiteralPath $ubtPath)) {
        Warn "UBT not found: $ubtPath"
    }
}
Check "mcp.json unreal-rag python" {
    $mcp = Join-Path $HOME ".lmstudio\mcp.json"
    if (-not (Test-Path $mcp)) { throw "mcp.json missing — run INSTALL-SAFE-MODE.bat" }
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
    if (-not (Test-Path $mcp)) { throw "mcp.json missing — run INSTALL-SAFE-MODE.bat" }
    $cfg = Get-Content -LiteralPath $mcp -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $cfg.mcpServers."unreal-rag") { throw "unreal-rag not in mcp.json" }
}
Check "shared workspace config" {
    $p = Join-Path $HOME ".lmstudio\config\unreal-workspace.json"
    if (-not (Test-Path $p)) { throw "missing" }
}
Check "Cline MCP settings" {
    $paths = @(
        (Join-Path $HOME ".cline\data\settings\cline_mcp_settings.json"),
        (Join-Path $env:APPDATA "Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json")
    )
    $found = $false
    foreach ($p in $paths) {
        if (-not (Test-Path $p)) { continue }
        $cfg = Get-Content -LiteralPath $p -Raw -Encoding UTF8 | ConvertFrom-Json
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

if ($fail -gt 0) {
    Write-Host "`n$fail check(s) failed."
    exit 1
}
Write-Host "`nAll checks passed."
exit 0
