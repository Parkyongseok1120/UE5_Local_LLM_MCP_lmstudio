param(
    [string]$OutputDir = "D:\Unreal58-RAG-Portable",
    [string]$ZipPath = "D:\Unreal58-RAG-Portable.zip"
)

$ErrorActionPreference = "Stop"

$lmstudio = Join-Path $HOME ".lmstudio"
$staging = $OutputDir

if (Test-Path $staging) {
    Remove-Item -LiteralPath $staging -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $staging | Out-Null

Write-Host "Staging portable package at $staging"

function Robo-CopyFiltered([string]$Source, [string]$Dest, [string[]]$ExcludeDirs, [string[]]$ExcludeFiles) {
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    $xd = ($ExcludeDirs | ForEach-Object { "/XD", $_ }) -join " "
    $xf = ($ExcludeFiles | ForEach-Object { "/XF", $_ }) -join " "
    $args = @($Source, $Dest, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NC", "/NS")
    foreach ($d in $ExcludeDirs) { $args += "/XD"; $args += $d }
    foreach ($f in $ExcludeFiles) { $args += "/XF"; $args += $f }
    & robocopy @args | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed for $Source" }
}

# Unreal58-RAG (slim)
Robo-CopyFiltered `
    (Join-Path $lmstudio "Unreal58-RAG") `
    (Join-Path $staging "Unreal58-RAG") `
    @("LyraStarterGame", "StackOBot", "AdvancedPuzzleConstructor", "wrapper_runs", "node_modules", ".git") `
    @("*.bak*", "raw_source.jsonl", "chunks.jsonl", "rag.staging.sqlite")

# Agent MCP (no node_modules — npm install on target)
Robo-CopyFiltered `
    (Join-Path $lmstudio "lmstudio-unreal-agent-mcp") `
    (Join-Path $staging "lmstudio-unreal-agent-mcp") `
    @("node_modules") `
    @()

# mcp-tools minimal
$mcpToolsDest = Join-Path $staging "mcp-tools"
New-Item -ItemType Directory -Force -Path $mcpToolsDest | Out-Null
Copy-Item (Join-Path $lmstudio "mcp-tools\package.json") $mcpToolsDest -Force
Copy-Item (Join-Path $lmstudio "mcp-tools\current-datetime.js") $mcpToolsDest -Force -ErrorAction SilentlyContinue
if (Test-Path (Join-Path $lmstudio "mcp-tools\package-lock.json")) {
    Copy-Item (Join-Path $lmstudio "mcp-tools\package-lock.json") $mcpToolsDest -Force
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

Copy-Item (Join-Path $lmstudio "Unreal58-RAG\installer\README-PORTABLE.md") (Join-Path $staging "README.txt") -Force

# Zip
if (Test-Path $ZipPath) { Remove-Item -LiteralPath $ZipPath -Force }
Write-Host "Creating archive $ZipPath (may take several minutes)..."
$tar = Get-Command tar -ErrorAction SilentlyContinue
if ($tar) {
    Push-Location (Split-Path $staging -Parent)
    try {
        & tar -a -c -f $ZipPath (Split-Path $staging -Leaf)
    }
    finally {
        Pop-Location
    }
}
else {
    Compress-Archive -Path $staging -DestinationPath $ZipPath -CompressionLevel Optimal
}

$zipSize = [math]::Round((Get-Item $ZipPath).Length / 1MB, 1)
Write-Host "Done: $ZipPath ($zipSize MB)"
Write-Host "Folder: $staging"
