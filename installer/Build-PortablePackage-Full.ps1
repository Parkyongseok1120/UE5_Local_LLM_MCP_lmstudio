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
$installerRoot = Join-Path $ragRoot "installer"
$readmeSource = Join-Path $installerRoot "README-FULL.md"
$packExcludes = Get-PortablePackageRobocopyExcludes

if (-not $OutputDir) {
    $OutputDir = Join-Path $env:TEMP "Unreal58-RAG-Portable-Full"
}
if (-not $ZipPath) {
    $ZipPath = Join-Path $env:TEMP "Unreal58-RAG-Portable-Full.zip"
}

function Copy-FullTreeFiltered {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Dest,
        [string[]]$ExtraExcludeDirs = @(),
        [string[]]$ExtraExcludeFiles = @()
    )

    if (-not (Test-Path -LiteralPath $Source)) {
        throw "Source not found: $Source"
    }

    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    $excludeDirs = @($packExcludes.ExcludeDirs + $ExtraExcludeDirs)
    $excludeFiles = @($packExcludes.ExcludeFiles + $ExtraExcludeFiles)
    $args = @($Source, $Dest, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NC", "/NS", "/NP")
    foreach ($d in $excludeDirs) { $args += "/XD"; $args += $d }
    foreach ($f in $excludeFiles) { $args += "/XF"; $args += $f }
    & robocopy @args | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed for $Source"
    }
}

$sourceRoots = @($ragRoot, $agentRoot, $mcpToolsRoot) | Where-Object { $_ }
$OutputDir = Assert-SafePackagePath -Path $OutputDir -SourceRoots $sourceRoots -ForceUnsafePath:$ForceUnsafePath
$ZipPath = Assert-SafePackagePath -Path $ZipPath -SourceRoots $sourceRoots -ForceUnsafePath:$ForceUnsafePath

foreach ($path in @($ragRoot, $agentRoot, $readmeSource)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Required path not found: $path"
    }
}

if (Test-Path -LiteralPath $OutputDir) {
    Remove-Item -LiteralPath $OutputDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

Write-Host "Staging full portable package at $OutputDir"
Write-Host "Source RAG root : $ragRoot"
Write-Host "Source agent    : $agentRoot"

Write-Host "Copying full RAG workspace..."
Copy-FullTreeFiltered -Source $ragRoot -Dest (Join-Path $OutputDir "Unreal58-RAG") `
    -ExtraExcludeFiles @("config\unreal-workspace.json", "config\cline-workspace.json")

Write-Host "Copying full lmstudio-unreal-agent-mcp folder..."
Copy-FullTreeFiltered -Source $agentRoot -Dest (Join-Path $OutputDir "lmstudio-unreal-agent-mcp")

if (Test-Path -LiteralPath $mcpToolsRoot) {
    Write-Host "Copying mcp-tools folder..."
    Copy-FullTreeFiltered -Source $mcpToolsRoot -Dest (Join-Path $OutputDir "mcp-tools")
}
else {
    Write-Host "Skipping mcp-tools (not present at $mcpToolsRoot)."
}

@'
@echo off
setlocal
cd /d "%~dp0"
echo Unreal58-RAG Full Portable MCP Installer
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Unreal58-RAG\installer\Install-UnrealMcp.ps1" -PortableRoot "%~dp0" -SkipNpm -SkipPythonDeps
if errorlevel 1 (
  echo.
  echo Install failed. See messages above.
  pause
  exit /b 1
)
echo Install OK. Restart LM Studio.
pause
'@ | Set-Content -LiteralPath (Join-Path $OutputDir "INSTALL.bat") -Encoding ASCII

Copy-Item -LiteralPath $readmeSource -Destination (Join-Path $OutputDir "README.txt") -Force

if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
Write-Host "Creating archive $ZipPath ..."
$tar = Get-Command tar -ErrorAction SilentlyContinue
if ($tar) {
    Push-Location (Split-Path $OutputDir -Parent)
    try {
        & tar -a -c -f $ZipPath (Split-Path $OutputDir -Leaf)
    }
    finally {
        Pop-Location
    }
}
else {
    Compress-Archive -Path $OutputDir -DestinationPath $ZipPath -CompressionLevel Optimal
}

$zipSize = [math]::Round((Get-Item $ZipPath).Length / 1MB, 1)
Write-Host "Done: $ZipPath ($zipSize MB)"
Write-Host "Folder: $OutputDir"
