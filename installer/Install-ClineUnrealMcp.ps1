param(
    [string]$PortableRoot = "",
    [string]$LmStudioHome = "",
    [string]$DocumentsRoot = "",
    [switch]$VsCode,
    [switch]$Cli,
    [switch]$All,
    [switch]$EnableAgentMode,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"

if (-not $VsCode -and -not $Cli) {
    $All = $true
}

. (Join-Path $PSScriptRoot "Resolve-StackLayout.ps1")
. (Join-Path $PSScriptRoot "Install-PathHelpers.ps1")

function Find-PythonExe {
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

function Assert-PythonVersion([string]$PythonExe) {
    $ver = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Cannot run Python at: $PythonExe" }
    $parts = $ver.Trim().Split(".")
    $major = [int]$parts[0]; $minor = [int]$parts[1]
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
        throw "Python 3.10+ required (found $ver)."
    }
    Write-Host "Python version: $ver OK"
}

function Assert-NodeVersion([string]$NodeExe) {
    $ver = & $NodeExe --version 2>&1 | Out-String
    if ($ver -match "v(\d+)\.") {
        $major = [int]$Matches[1]
        if ($major -lt 20) { throw "Node.js 20+ required, found: $($ver.Trim())" }
    }
    Write-Host "Node version: $($ver.Trim()) OK"
}

$layout = Resolve-StackLayout $PortableRoot
$ragRoot = $layout.RagRoot
$agentRoot = $layout.AgentRoot
$root = $layout.Root
if ($WhatIf) {
    $python = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $python) { $python = "python.exe" }
    $node = (Get-Command node -ErrorAction SilentlyContinue).Source
    if (-not $node) { $node = "node.exe" }
}
else {
    $python = Find-PythonExe
    $node = Find-NodeExe
    Assert-PythonVersion $python
    Assert-NodeVersion $node
}
$lmHome = if ($LmStudioHome) {
    if (Test-Path -LiteralPath $LmStudioHome) {
        (Resolve-Path -LiteralPath $LmStudioHome).Path
    }
    else {
        $LmStudioHome
    }
}
else {
    Join-Path $HOME ".lmstudio"
}
$docsRoot = if ($DocumentsRoot) { $DocumentsRoot } else { Join-Path $HOME "Documents" }
$sharedConfigPath = Join-Path $lmHome "config\unreal-workspace.json"
$agentConfigPath = Join-Path $agentRoot "config\agent-mcp.json"

if (-not (Test-Path $sharedConfigPath)) {
    if ($WhatIf) {
        Write-Host "[WhatIf] Shared config missing at $sharedConfigPath - preview only." -ForegroundColor Cyan
    }
    else {
        throw "Shared config missing: $sharedConfigPath - run Install-UnrealMcp.ps1 first."
    }
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
    -WriteCli:($All -or $Cli) `
    -WhatIf:$WhatIf

if (-not $WhatIf) {
    foreach ($path in @($result.VsCodePath, $result.CliPath)) {
        if ($path) {
            Write-Host "Updated: $path" -ForegroundColor Green
        }
    }
}

Write-Host ""
Write-Host "=== Cline MCP install complete ==="
if ($WhatIf) {
    Write-Host "WhatIf mode - no files were written." -ForegroundColor Cyan
}
Write-Host "Restart Cline and confirm unreal-rag + unreal-agent appear in MCP Servers."
if (-not $EnableAgentMode) {
    Write-Host "Safe mode (read-only agent). Re-run with -EnableAgentMode for writes/builds." -ForegroundColor Cyan
}
