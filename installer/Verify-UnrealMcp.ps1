param(
    [string]$PortableRoot = ""
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "Resolve-StackLayout.ps1")

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
$ubt58 = "C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\DotNET\UnrealBuildTool\UnrealBuildTool.exe"

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

Check "Portable root" { if (-not (Test-Path $root)) { throw "missing $root" } }
Check "Unreal58-RAG" { if (-not (Test-Path (Join-Path $ragRoot "rag.ps1"))) { throw "missing rag.ps1" } }
Check "RAG index" { if (-not (Test-Path (Join-Path $ragRoot "data\unreal58\rag.sqlite"))) { throw "missing rag.sqlite" } }
Check "unreal_rag_mcp.py compile" {
    Push-Location (Join-Path $ragRoot "scripts")
    try { & $py -m py_compile unreal_rag_mcp.py rag_search.py workspace_paths.py }
    finally { Pop-Location }
}
Check "agent server.js" { if (-not (Test-Path (Join-Path $agentRoot "src\server.js"))) { throw "missing" } }
Check "python version" {
    $out = & $py --version 2>&1 | Out-String
    if ($out -notmatch "Python") { throw $out.Trim() }
}
Check "UE 5.8 UBT" { if (-not (Test-Path $ubt58)) { throw "missing $ubt58" } }
Check "mcp.json unreal-rag python" {
    $mcp = Join-Path $HOME ".lmstudio\mcp.json"
    if (-not (Test-Path $mcp)) { throw "mcp.json missing — run INSTALL.bat" }
    $cfg = Get-Content $mcp -Raw | ConvertFrom-Json
    if (-not $cfg.mcpServers."unreal-rag") { throw "unreal-rag not in mcp.json" }
    $cmd = [string]$cfg.mcpServers."unreal-rag".command
    if ($cmd -like "*\WindowsApps\*") { throw "WindowsApps python stub: $cmd" }
    if (-not (Test-Path $cmd)) { throw "python command missing: $cmd" }
    $ver = & $cmd --version 2>&1 | Out-String
    if ($ver -notmatch "Python") { throw "bad python: $ver" }
}
Check "mcp.json unreal-rag entry" {
    $mcp = Join-Path $HOME ".lmstudio\mcp.json"
    if (-not (Test-Path $mcp)) { throw "mcp.json missing — run INSTALL.bat" }
    $cfg = Get-Content $mcp -Raw | ConvertFrom-Json
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
        $cfg = Get-Content $p -Raw | ConvertFrom-Json
        if ($cfg.mcpServers."unreal-rag" -and $cfg.mcpServers."unreal-agent") {
            $found = $true
            break
        }
    }
    if (-not $found) { throw "unreal-rag/unreal-agent not in Cline MCP settings — run Install-ClineUnrealMcp.ps1" }
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
