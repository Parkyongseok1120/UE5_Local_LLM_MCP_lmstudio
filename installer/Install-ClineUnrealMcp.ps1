param(
    [string]$PortableRoot = "",
    [string]$LmStudioHome = "",
    [string]$DocumentsRoot = "",
    [switch]$VsCode,
    [switch]$Cli,
    [switch]$All,
    [switch]$EnableAgentMode
)

$ErrorActionPreference = "Stop"

if (-not $VsCode -and -not $Cli) {
    $All = $true
}

. (Join-Path $PSScriptRoot "Resolve-StackLayout.ps1")
. (Join-Path $PSScriptRoot "Install-PathHelpers.ps1")

function Find-PythonExe {
    $bundled = Join-Path $HOME ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path $bundled) { return $bundled }
    foreach ($path in @(
            (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
            (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
            (Join-Path $env:LOCALAPPDATA "Programs\Python\Python310\python.exe")
        )) {
        if (Test-Path $path) { return $path }
    }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notlike "*\WindowsApps\*" -and (Test-Path $cmd.Source)) {
        return $cmd.Source
    }
    throw "Python 3.10+ not found."
}

function Find-NodeExe {
    foreach ($path in @(
            (Join-Path $env:ProgramFiles "nodejs\node.exe"),
            (Join-Path ${env:ProgramFiles(x86)} "nodejs\node.exe"),
            (Join-Path $env:LOCALAPPDATA "Programs\nodejs\node.exe")
        )) {
        if (Test-Path $path) { return (Resolve-Path $path).Path }
    }
    $cmd = Get-Command node -ErrorAction SilentlyContinue
    if ($cmd -and (Test-Path $cmd.Source)) { return $cmd.Source }
    throw "Node.js 20+ not found."
}

$layout = Resolve-StackLayout $PortableRoot
$ragRoot = $layout.RagRoot
$agentRoot = $layout.AgentRoot
$root = $layout.Root
$python = Find-PythonExe
$node = Find-NodeExe
$lmHome = if ($LmStudioHome) { (Resolve-Path $LmStudioHome).Path } else { Join-Path $HOME ".lmstudio" }
$docsRoot = if ($DocumentsRoot) { $DocumentsRoot } else { Join-Path $HOME "Documents" }
$sharedConfigPath = Join-Path $lmHome "config\unreal-workspace.json"
$agentConfigPath = Join-Path $agentRoot "config\agent-mcp.json"

if (-not (Test-Path $sharedConfigPath)) {
    throw "Shared config missing: $sharedConfigPath — run Install-UnrealMcp.ps1 first."
}

$result = Sync-ClineMcpSettings `
    -RagRoot $ragRoot `
    -AgentRoot $agentRoot `
    -DocumentsRoot $docsRoot `
    -SharedConfigPath $sharedConfigPath `
    -PythonExe $python `
    -NodeExe $node `
    -PortableRoot $root `
    -EnableAgentMode:$EnableAgentMode `
    -WriteVsCode:($All -or $VsCode) `
    -WriteCli:($All -or $Cli)

foreach ($path in @($result.VsCodePath, $result.CliPath)) {
    if ($path) {
        Write-Host "Updated: $path" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "=== Cline MCP install complete ==="
Write-Host "Restart Cline and confirm unreal-rag + unreal-agent appear in MCP Servers."
if (-not $EnableAgentMode) {
    Write-Host "Safe mode (read-only agent). Re-run with -EnableAgentMode for writes/builds." -ForegroundColor Cyan
}
