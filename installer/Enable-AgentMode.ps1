param(
    [string]$PortableRoot = "",
    [string]$LmStudioHome = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Resolve-StackLayout.ps1")

$layout = Resolve-StackLayout -PortableRoot $PortableRoot -LmStudioHome $LmStudioHome
$mcpPath = Join-Path $layout.LmStudioHome "mcp.json"

if (-not (Test-Path $mcpPath)) {
    throw "mcp.json not found at $mcpPath — run Install-UnrealMcp.ps1 first."
}

$config = Get-Content $mcpPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $config.mcpServers."unreal-agent") {
    throw "unreal-agent server not configured in mcp.json"
}

if (-not $config.mcpServers."unreal-agent".env) {
    $config.mcpServers."unreal-agent" | Add-Member -NotePropertyName env -NotePropertyValue ([ordered]@{})
}

$config.mcpServers."unreal-agent".env.ALLOW_WRITE = "1"
$config.mcpServers."unreal-agent".env.ALLOW_COMMANDS = "1"
$config.mcpServers."unreal-agent".env.ALLOW_UNREAL_BUILD = "1"
$config.mcpServers."unreal-agent".env.VALIDATE_ON_WRITE = "1"
if (-not $config.mcpServers."unreal-agent".PSObject.Properties.Name.Contains("timeout")) {
    $config.mcpServers."unreal-agent" | Add-Member -NotePropertyName timeout -NotePropertyValue 720000
} else {
    $config.mcpServers."unreal-agent".timeout = 720000
}

$json = $config | ConvertTo-Json -Depth 20
[System.IO.File]::WriteAllText($mcpPath, $json + [Environment]::NewLine, (New-Object System.Text.UTF8Encoding $false))
Write-Host "Agent mode ENABLED in $mcpPath" -ForegroundColor Green
Write-Host "Restart LM Studio MCP servers to apply."
