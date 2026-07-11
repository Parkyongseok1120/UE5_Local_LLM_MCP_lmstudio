param(
    [string]$OutputDir = "",
    [string]$ZipPath = "",
    [string]$SourceRoot = "",
    [switch]$ForceUnsafePath
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "Resolve-StackLayout.ps1")
. (Join-Path $PSScriptRoot "Install-PathHelpers.ps1")

$layout = Resolve-StackLayout $SourceRoot
$ragRoot = $layout.RagRoot
$agentRoot = $layout.AgentRoot
$mcpToolsRoot = $layout.McpToolsRoot
$packExcludes = Get-PortablePackageRobocopyExcludes

if (-not $OutputDir) {
    $OutputDir = Join-Path $env:TEMP "Unreal58-RAG-Portable"
}
if (-not $ZipPath) {
    $ZipPath = Join-Path $env:TEMP "Unreal58-RAG-Portable.zip"
}

$sourceRoots = @($ragRoot, $agentRoot, $mcpToolsRoot) | Where-Object { $_ }
$staging = Assert-SafePackagePath -Path $OutputDir -SourceRoots $sourceRoots -ForceUnsafePath:$ForceUnsafePath
$safeZipPath = Assert-SafePackagePath -Path $ZipPath -SourceRoots $sourceRoots -ForceUnsafePath:$ForceUnsafePath

if (Test-Path -LiteralPath $staging) {
    Remove-Item -LiteralPath $staging -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $staging | Out-Null

Write-Host "Staging portable package at $staging"
Write-Host "Source RAG root : $ragRoot"
Write-Host "Source agent    : $agentRoot"

function Robo-CopyFiltered([string]$Source, [string]$Dest, [string[]]$ExcludeDirs, [string[]]$ExcludeFiles) {
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    $args = @($Source, $Dest, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NC", "/NS")
    foreach ($d in $ExcludeDirs) { $args += "/XD"; $args += $d }
    foreach ($f in $ExcludeFiles) { $args += "/XF"; $args += $f }
    & robocopy @args | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed for $Source" }
}

# RAG workspace (slim)
Robo-CopyFiltered `
    $ragRoot `
    (Join-Path $staging "Unreal58-RAG") `
    $packExcludes.ExcludeDirs `
    $packExcludes.ExcludeFiles

# Agent MCP (no node_modules — npm install on target)
Robo-CopyFiltered `
    $agentRoot `
    (Join-Path $staging "lmstudio-unreal-agent-mcp") `
    @("node_modules") `
    @()

# mcp-tools (optional — current-datetime MCP)
$mcpToolsDest = Join-Path $staging "mcp-tools"
New-Item -ItemType Directory -Force -Path $mcpToolsDest | Out-Null
$mcpPkg = Join-Path $mcpToolsRoot "package.json"
if (Test-Path -LiteralPath $mcpPkg) {
    Copy-Item -LiteralPath $mcpPkg -Destination $mcpToolsDest -Force
    $dateTimeJs = Join-Path $mcpToolsRoot "current-datetime.js"
    if (Test-Path -LiteralPath $dateTimeJs) {
        Copy-Item -LiteralPath $dateTimeJs -Destination $mcpToolsDest -Force
    }
    $lock = Join-Path $mcpToolsRoot "package-lock.json"
    if (Test-Path -LiteralPath $lock) {
        Copy-Item -LiteralPath $lock -Destination $mcpToolsDest -Force
    }
    Write-Host "Included optional mcp-tools bundle."
}
else {
    Write-Host "Skipping mcp-tools (no package.json at $mcpToolsRoot)."
}

# Root installer launcher
@'
@echo off
setlocal
cd /d "%~dp0"
echo Unreal58-RAG Portable MCP Installer
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Unreal58-RAG\installer\Install-UnrealMcp.ps1" -PortableRoot "%~dp0"
if errorlevel 1 (
  echo Install failed.
  pause
  exit /b 1
)
echo Install OK. Restart LM Studio.
pause
'@ | Set-Content -LiteralPath (Join-Path $staging "INSTALL.bat") -Encoding ASCII

$readmePortable = Join-Path $ragRoot "installer\README-PORTABLE.md"
if (Test-Path -LiteralPath $readmePortable) {
    Copy-Item -LiteralPath $readmePortable -Destination (Join-Path $staging "README.txt") -Force
}

# Zip
if (Test-Path -LiteralPath $safeZipPath) { Remove-Item -LiteralPath $safeZipPath -Force }
Write-Host "Creating archive $safeZipPath (may take several minutes)..."
$tar = Get-Command tar -ErrorAction SilentlyContinue
if ($tar) {
    Push-Location (Split-Path $staging -Parent)
    try {
        & tar -a -c -f $safeZipPath (Split-Path $staging -Leaf)
    }
    finally {
        Pop-Location
    }
}
else {
    Compress-Archive -Path $staging -DestinationPath $safeZipPath -CompressionLevel Optimal
}

$zipSize = [math]::Round((Get-Item $safeZipPath).Length / 1MB, 1)
Write-Host "Done: $safeZipPath ($zipSize MB)"
Write-Host "Folder: $staging"
