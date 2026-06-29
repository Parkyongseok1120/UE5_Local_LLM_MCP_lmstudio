$ErrorActionPreference = "Stop"

$mcpPath = Join-Path $HOME ".lmstudio\mcp.json"
$syncPath = Join-Path $HOME ".lmstudio\.internal\last-synced-mcp-state.json"
$workspace = Resolve-Path (Join-Path $PSScriptRoot "..")
$python = Join-Path $HOME ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$server = Join-Path $workspace "scripts\unreal_rag_mcp.py"
$index = Join-Path $workspace "data\unreal58\rag.sqlite"

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

$config = Read-McpConfig @($mcpPath, $syncPath)
if (-not $config.Contains("mcpServers") -or $null -eq $config["mcpServers"]) {
    $config["mcpServers"] = [ordered]@{}
}

$config["mcpServers"]["unreal-rag"] = [ordered]@{
    command = $python
    args = @($server, "--index", $index)
    env = [ordered]@{
        SHARED_UNREAL_CONFIG = (Join-Path $HOME ".lmstudio\config\unreal-workspace.json")
        UNREAL58_ROOT          = "$workspace"
        PYTHONUTF8             = "1"
        PYTHONIOENCODING       = "utf-8"
        MCP_ESSENTIAL_TOOLS    = "1"
    }
}

$agentRoot = Join-Path $workspace "lmstudio-unreal-agent-mcp"
$node = Get-Command node -ErrorAction SilentlyContinue
if ($node -and (Test-Path (Join-Path $agentRoot "src\server.js"))) {
    $config["mcpServers"]["unreal-agent"] = [ordered]@{
        command = $node.Source
        args    = @((Join-Path $agentRoot "src\server.js"))
        env     = [ordered]@{
            WORKSPACE_ROOT       = (Join-Path $HOME "Documents")
            AGENT_MCP_CONFIG     = (Join-Path $agentRoot "config\agent-mcp.json")
            SHARED_UNREAL_CONFIG = (Join-Path $HOME ".lmstudio\config\unreal-workspace.json")
            UNREAL58_ROOT        = "$workspace"
            MCP_ESSENTIAL_TOOLS  = "1"
        }
    }
}

$patchScript = Join-Path $HOME ".lmstudio\scripts\patch_mcp_runtime_paths.ps1"
$json = $config | ConvertTo-Json -Depth 30
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$timestamp = Get-Date -Format yyyyMMddHHmmss

foreach ($path in @($mcpPath, $syncPath)) {
    if (Test-Path $path) {
        Copy-Item -LiteralPath $path -Destination "$path.bak-install-unreal-rag-$timestamp" -Force
    }
    $directory = Split-Path -Parent $path
    if (-not (Test-Path $directory)) {
        New-Item -ItemType Directory -Path $directory | Out-Null
    }
    [System.IO.File]::WriteAllText($path, $json + [Environment]::NewLine, $utf8NoBom)
}

if (Test-Path $patchScript) {
    powershell -NoProfile -ExecutionPolicy Bypass -File $patchScript | Out-Host
}

$patchPy = Join-Path $workspace "scripts\patch_mcp_config.py"
if (Test-Path $patchPy) {
    & $python $patchPy | Out-Host
}

Write-Host "Installed LM Studio MCP config as UTF-8 without BOM:"
Write-Host $mcpPath
Write-Host $syncPath
