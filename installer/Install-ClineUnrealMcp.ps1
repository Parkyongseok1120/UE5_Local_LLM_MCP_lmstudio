param(
    [switch]$VsCode,
    [switch]$Cli,
    [switch]$All
)

$ErrorActionPreference = "Stop"

if (-not $VsCode -and -not $Cli) {
    $All = $true
}

$ragRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$templatePath = Join-Path $ragRoot "config\cline_mcp_settings.template.json"
if (-not (Test-Path $templatePath)) {
    throw "Template not found: $templatePath"
}

$template = Get-Content $templatePath -Raw -Encoding UTF8 | ConvertFrom-Json

function Merge-McpSettings($targetPath) {
    $dir = Split-Path $targetPath -Parent
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }

    $existing = @{ mcpServers = @{} }
    if (Test-Path $targetPath) {
        $existing = Get-Content $targetPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if (-not $existing.mcpServers) {
            $existing | Add-Member -NotePropertyName mcpServers -NotePropertyValue @{}
        }
    }

    foreach ($name in $template.mcpServers.PSObject.Properties.Name) {
        $existing.mcpServers | Add-Member -NotePropertyName $name -NotePropertyValue $template.mcpServers.$name -Force
    }

    $json = $existing | ConvertTo-Json -Depth 20
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($targetPath, $json + [Environment]::NewLine, $utf8NoBom)
    Write-Host "Updated: $targetPath"
}

if ($All -or $VsCode) {
    $vscodePath = Join-Path $env:APPDATA "Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json"
    Merge-McpSettings $vscodePath
}

if ($All -or $Cli) {
    $cliPath = Join-Path $HOME ".cline\data\settings\cline_mcp_settings.json"
    Merge-McpSettings $cliPath
}

Write-Host "Done. Restart Cline and confirm unreal-rag / unreal-agent appear in MCP Servers."
