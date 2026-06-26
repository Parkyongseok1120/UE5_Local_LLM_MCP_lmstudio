param(
    [string]$OutputDir = "D:\Unreal58-RAG-Portable-Full",
    [string]$ZipPath = "D:\Unreal58-RAG-Portable-Full.zip"
)

$ErrorActionPreference = "Stop"

function Copy-FullTree {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Dest
    )

    if (-not (Test-Path -LiteralPath $Source)) {
        throw "Source not found: $Source"
    }

    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    & robocopy $Source $Dest /E /NFL /NDL /NJH /NJS /NC /NS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed for $Source"
    }
}

$workspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$lmstudioRoot = (Resolve-Path (Join-Path $workspaceRoot "..")).Path
$installerRoot = Join-Path $workspaceRoot "installer"
$workspaceSource = $workspaceRoot
$agentSource = Join-Path $lmstudioRoot "lmstudio-unreal-agent-mcp"
$mcpToolsSource = Join-Path $lmstudioRoot "mcp-tools"
$readmeSource = Join-Path $installerRoot "README-FULL.md"

foreach ($path in @($workspaceSource, $agentSource, $mcpToolsSource, $readmeSource)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Required path not found: $path"
    }
}

if (Test-Path -LiteralPath $OutputDir) {
    Remove-Item -LiteralPath $OutputDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

Write-Host "Staging full portable package at $OutputDir"

# Copy the full workspace and bundled MCP folders.
Write-Host "Copying full Unreal58-RAG workspace..."
Copy-FullTree -Source $workspaceSource -Dest (Join-Path $OutputDir "Unreal58-RAG")

Write-Host "Copying full lmstudio-unreal-agent-mcp folder..."
Copy-FullTree -Source $agentSource -Dest (Join-Path $OutputDir "lmstudio-unreal-agent-mcp")

Write-Host "Copying full mcp-tools folder..."
Copy-FullTree -Source $mcpToolsSource -Dest (Join-Path $OutputDir "mcp-tools")

# Write the root launcher and README copy.
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
echo.
echo Done. Restart LM Studio and connect MCP servers.
pause
'@ | Set-Content -LiteralPath (Join-Path $OutputDir "INSTALL.bat") -Encoding ASCII

Copy-Item -LiteralPath $readmeSource -Destination (Join-Path $OutputDir "README.txt") -Force

# Zip the staging directory.
if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}

Write-Host "Creating archive $ZipPath (may take several minutes)..."
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
