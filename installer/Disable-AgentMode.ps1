# Disable agent mode - safe read-only profile
param(
    [string]$LmStudioHome = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Resolve-StackLayout.ps1")

$layout = Resolve-StackLayout -PortableRoot "" -LmStudioHome $LmStudioHome
$mcpPath = Join-Path $layout.LmStudioHome "mcp.json"

if (-not (Test-Path $mcpPath)) {
    throw "mcp.json not found at $mcpPath"
}

$config = Get-Content $mcpPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $config.mcpServers."unreal-agent") {
    Write-Host "unreal-agent not configured - nothing to do."
    exit 0
}

if (-not $config.mcpServers."unreal-agent".env) {
    $config.mcpServers."unreal-agent" | Add-Member -NotePropertyName env -NotePropertyValue ([ordered]@{})
}

$config.mcpServers."unreal-agent".env.ALLOW_WRITE = "0"
$config.mcpServers."unreal-agent".env.ALLOW_COMMANDS = "0"
$config.mcpServers."unreal-agent".env.ALLOW_UNREAL_BUILD = "0"

$json = $config | ConvertTo-Json -Depth 20
[System.IO.File]::WriteAllText($mcpPath, $json + [Environment]::NewLine, (New-Object System.Text.UTF8Encoding $false))
Write-Host "Safe mode ENABLED (read-only agent) in $mcpPath" -ForegroundColor Cyan
