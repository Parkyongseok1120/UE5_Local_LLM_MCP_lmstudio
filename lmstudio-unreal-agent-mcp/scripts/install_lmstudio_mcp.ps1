$ErrorActionPreference = "Stop"

$mcpPath = Join-Path $HOME ".lmstudio\mcp.json"
$syncPath = Join-Path $HOME ".lmstudio\.internal\last-synced-mcp-state.json"
$workspace = Resolve-Path (Join-Path $PSScriptRoot "..")
$server = Join-Path $workspace "src\server.js"
$configExample = Join-Path $workspace "config\lmstudio-mcp-unreal-agent.json"

function ConvertTo-OrderedHashtable($value) {
    if ($null -eq $value) {
        return $null
    }
    if ($value -is [System.Management.Automation.PSCustomObject]) {
        $hash = [ordered]@{}
        foreach ($property in $value.PSObject.Properties) {
            $hash[$property.Name] = ConvertTo-OrderedHashtable $property.Value
        }
        return $hash
    }
    if (($value -is [System.Collections.IEnumerable]) -and -not ($value -is [string])) {
        $items = @()
        foreach ($item in $value) {
            $items += ConvertTo-OrderedHashtable $item
        }
        return $items
    }
    return $value
}

function Read-McpConfig($paths) {
    foreach ($path in $paths) {
        if (-not (Test-Path $path)) {
            continue
        }
        try {
            $raw = Get-Content -LiteralPath $path -Raw -Encoding UTF8
            if ([string]::IsNullOrWhiteSpace($raw)) {
                continue
            }
            return ConvertTo-OrderedHashtable ($raw | ConvertFrom-Json)
        }
        catch {
            Write-Warning "Could not parse existing MCP config: $path"
        }
    }
    return [ordered]@{}
}

if (-not (Test-Path $server)) {
    throw "MCP server not found: $server"
}

if (-not (Test-Path (Join-Path $workspace "node_modules"))) {
    Write-Host "Running npm install in $workspace"
    Push-Location $workspace
    try {
        npm install
    }
    finally {
        Pop-Location
    }
}

$fragment = $null
if (Test-Path $configExample) {
    $fragment = (Get-Content -LiteralPath $configExample -Raw -Encoding UTF8 | ConvertFrom-Json).mcpServers."unreal-agent"
}

if ($null -eq $fragment) {
    $fragment = [ordered]@{
        command = "node"
        args = @($server)
        env = [ordered]@{
            WORKSPACE_ROOT = (Join-Path $HOME "Documents")
            AGENT_MCP_CONFIG = (Join-Path $workspace "config\agent-mcp.json")
            ALLOW_WRITE = "1"
            ALLOW_COMMANDS = "1"
            ALLOW_UNREAL_BUILD = "1"
            MAX_READ_BYTES = "524288"
            MAX_OUTPUT_BYTES = "262144"
            COMMAND_TIMEOUT_MS = "600000"
        }
    }
}

$config = Read-McpConfig @($mcpPath, $syncPath)
if (-not $config.Contains("mcpServers") -or $null -eq $config["mcpServers"]) {
    $config["mcpServers"] = [ordered]@{}
}

$config["mcpServers"]["unreal-agent"] = ConvertTo-OrderedHashtable $fragment

$json = $config | ConvertTo-Json -Depth 30
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$timestamp = Get-Date -Format yyyyMMddHHmmss

foreach ($path in @($mcpPath, $syncPath)) {
    if (Test-Path $path) {
        Copy-Item -LiteralPath $path -Destination "$path.bak-install-unreal-agent-$timestamp" -Force
    }
    $directory = Split-Path -Parent $path
    if (-not (Test-Path $directory)) {
        New-Item -ItemType Directory -Path $directory | Out-Null
    }
    [System.IO.File]::WriteAllText($path, $json + [Environment]::NewLine, $utf8NoBom)
}

$pluginRoot = Join-Path $HOME ".lmstudio\extensions\plugins\mcp\unreal-agent"
New-Item -ItemType Directory -Force -Path $pluginRoot | Out-Null
@{
    type = "plugin"
    runner = "mcpBridge"
    owner = "mcp"
    name = "unreal-agent"
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $pluginRoot "manifest.json") -Encoding UTF8

@{
    command = $fragment.command
    args = $fragment.args
    env = $fragment.env
} | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $pluginRoot "mcp-bridge-config.json") -Encoding UTF8

Write-Host "Installed LM Studio unreal-agent MCP:"
Write-Host $mcpPath
Write-Host $syncPath
Write-Host $pluginRoot
