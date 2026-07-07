param(
    [string]$PortableRoot = "",
    [string]$LmStudioHome = "",
    [string]$DocumentsRoot = "",
    [switch]$SkipNpm,
    [switch]$SkipPythonDeps,
    [switch]$EnableAgentMode,
    [switch]$SkipProjectSetup
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "Resolve-StackLayout.ps1")

function Find-PythonExe {
    $bundled = Join-Path $HOME ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path $bundled) {
        return $bundled
    }
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
    throw "Python 3.10+ not found. Install from https://www.python.org/downloads/"
}

function Assert-PythonVersion([string]$PythonExe) {
    $ver = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Cannot run Python at: $PythonExe" }
    $parts = $ver.Trim().Split(".")
    $major = [int]$parts[0]; $minor = [int]$parts[1]
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
        throw "Python 3.10+ required (found $ver). Install from https://www.python.org/downloads/"
    }
    Write-Host "Python version: $ver OK"
}

function Find-NodeExe {
    $found = [System.Collections.Generic.List[string]]::new()
    foreach ($path in @(
            (Join-Path $env:ProgramFiles "nodejs\node.exe"),
            (Join-Path ${env:ProgramFiles(x86)} "nodejs\node.exe"),
            (Join-Path $env:LOCALAPPDATA "Programs\nodejs\node.exe")
        )) {
        if (Test-Path $path) { $found.Add((Resolve-Path $path).Path) }
    }
    $cmd = Get-Command node -ErrorAction SilentlyContinue
    if ($cmd -and (Test-Path $cmd.Source)) { $found.Add($cmd.Source) }
    if ($found.Count -eq 0) {
        throw "Node.js 20+ not found. Install from https://nodejs.org/"
    }
    return $found[0]
}

function Assert-NodeVersion([string]$NodeExe) {
    $ver = & $NodeExe --version 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Cannot run Node.js at: $NodeExe" }
    $clean = $ver.Trim().TrimStart("v")
    $major = [int]($clean.Split(".")[0])
    if ($major -lt 20) {
        throw "Node.js 20+ required (found v$clean). Install from https://nodejs.org/"
    }
    Write-Host "Node.js version: v$clean OK"
}

function Expand-TemplateValue($Value) {
    if ($null -eq $Value) {
        return $null
    }
    if ($Value -is [System.Management.Automation.PSCustomObject]) {
        $hash = [ordered]@{}
        foreach ($property in $Value.PSObject.Properties) {
            $hash[$property.Name] = Expand-TemplateValue $property.Value
        }
        return $hash
    }
    if ($Value -is [System.Collections.IDictionary]) {
        $hash = [ordered]@{}
        foreach ($key in $Value.Keys) {
            $hash[$key] = Expand-TemplateValue $Value[$key]
        }
        return $hash
    }
    if (($Value -is [System.Collections.IEnumerable]) -and -not ($Value -is [string])) {
        $items = @()
        foreach ($item in $Value) {
            $items += Expand-TemplateValue $item
        }
        return $items
    }
    if ($Value -is [string]) {
        return $Value.Replace("%USERPROFILE%", $HOME).Replace('$env:USERPROFILE', $HOME)
    }
    return $Value
}

function Read-JsonObject([string]$Path) {
    if (-not (Test-Path $Path)) { return $null }
    return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function Write-JsonUtf8([string]$Path, $Object) {
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $json = $Object | ConvertTo-Json -Depth 40
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $json + [Environment]::NewLine, $utf8)
}

function Merge-McpServer($Servers, [string]$Name, $Entry) {
    if ($null -eq $Servers) {
        $Servers = [ordered]@{}
    }
  if ($Servers -is [System.Collections.IDictionary]) {
        $Servers[$Name] = $Entry
        return $Servers
    }
    $Servers | Add-Member -NotePropertyName $Name -NotePropertyValue $Entry -Force
    return $Servers
}

$layout = Resolve-StackLayout $PortableRoot
$root = $layout.Root
$ragRoot = $layout.RagRoot
$agentRoot = $layout.AgentRoot
$mcpToolsRoot = $layout.McpToolsRoot

if (-not (Test-Path (Join-Path $ragRoot "rag.ps1"))) {
    throw "UE5_Local_LLM_MCP_lmstudio not found under: $root"
}
if (-not (Test-Path (Join-Path $agentRoot "src\server.js"))) {
    throw "lmstudio-unreal-agent-mcp not found under: $root"
}

$python = Find-PythonExe
$node = Find-NodeExe
Assert-PythonVersion $python
Assert-NodeVersion $node
$lmHome = if ($LmStudioHome) { (Resolve-Path $LmStudioHome).Path } else { Join-Path $HOME ".lmstudio" }
$docsRoot = if ($DocumentsRoot) { $DocumentsRoot } else { Join-Path $HOME "Documents" }

Write-Host "Portable root : $root"
Write-Host "Python        : $python"
Write-Host "Node          : $node"
Write-Host "LM Studio home: $lmHome"

# npm dependencies
if (-not $SkipNpm) {
    foreach ($pair in @(
            @{ Dir = $agentRoot; Name = "unreal-agent" },
            @{ Dir = $mcpToolsRoot; Name = "mcp-tools" }
        )) {
        $pkg = Join-Path $pair.Dir "package.json"
        if (-not (Test-Path $pkg)) { continue }
        Write-Host "npm install in $($pair.Name)..."
        Push-Location $pair.Dir
        try {
            cmd /c "npm install --no-fund --no-audit 2>&1"
            if ($LASTEXITCODE -ne 0) {
                throw "npm install failed in $($pair.Name) (exit $LASTEXITCODE)"
            }
        }
        finally {
            Pop-Location
        }
    }
}

# optional python deps for hybrid search
if (-not $SkipPythonDeps) {
    Write-Host "Installing fastembed (optional hybrid search)..."
    cmd /c "`"$python`" -m pip install fastembed --quiet 2>&1"
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "fastembed install failed (optional — hybrid search will be unavailable). Run: pip install fastembed"
    } else {
        Write-Host "fastembed installed OK."
    }
}

# shared workspace config
$sharedConfigPath = Join-Path $lmHome "config\unreal-workspace.json"
if (-not (Test-Path $sharedConfigPath)) {
    $templatePath = Join-Path $PSScriptRoot "templates\unreal-workspace.template.json"
    $template = Get-Content -LiteralPath $templatePath -Raw -Encoding UTF8 | ConvertFrom-Json
    $sharedConfig = Expand-TemplateValue $template
    $sharedDir = Split-Path -Parent $sharedConfigPath
    New-Item -ItemType Directory -Force -Path $sharedDir | Out-Null
    Write-JsonUtf8 $sharedConfigPath $sharedConfig
    Write-Host "Created $sharedConfigPath"
}

# agent config — rewritten on every install from detected local paths
$agentConfigPath = Join-Path $agentRoot "config\agent-mcp.json"

$ragServer = Join-Path $ragRoot "scripts\unreal_rag_mcp.py"
$ragIndex = Join-Path $ragRoot "data\unreal58\rag.sqlite"
$agentServer = Join-Path $agentRoot "src\server.js"
$dateTimeJs = Join-Path $mcpToolsRoot "current-datetime.js"
$mcpRemoteProxy = Join-Path $mcpToolsRoot "node_modules\mcp-remote\dist\proxy.js"

$mcpPaths = @(
    (Join-Path $lmHome "mcp.json"),
    (Join-Path $lmHome ".internal\last-synced-mcp-state.json")
)

foreach ($mcpPath in $mcpPaths) {
    $config = Read-JsonObject $mcpPath
    if ($null -eq $config) {
        $config = [ordered]@{ mcpServers = [ordered]@{} }
    }
    if ($null -eq $config.mcpServers) {
        $config.mcpServers = [ordered]@{}
    }

    $config.mcpServers = Merge-McpServer $config.mcpServers "unreal-rag" ([ordered]@{
        command = $python
        args    = @($ragServer, "--index", $ragIndex)
        timeout = 420000
        env     = [ordered]@{
            SHARED_UNREAL_CONFIG = $sharedConfigPath
            UNREAL58_ROOT        = $ragRoot
            UNREAL58_PORTABLE_ROOT = $root
            PYTHONUTF8           = "1"
            PYTHONIOENCODING     = "utf-8"
            MCP_ESSENTIAL_TOOLS  = "1"
        }
    })

    $allowWrite = if ($EnableAgentMode) { "1" } else { "0" }
    $allowCommands = if ($EnableAgentMode) { "1" } else { "0" }
    $allowBuild = if ($EnableAgentMode) { "1" } else { "0" }

    $config.mcpServers = Merge-McpServer $config.mcpServers "unreal-agent" ([ordered]@{
        command = $node
        args    = @($agentServer)
        env     = [ordered]@{
            WORKSPACE_ROOT       = $docsRoot
            AGENT_MCP_CONFIG     = $agentConfigPath
            SHARED_UNREAL_CONFIG = $sharedConfigPath
            UNREAL58_ROOT        = $ragRoot
            ALLOW_WRITE          = $allowWrite
            ALLOW_COMMANDS       = $allowCommands
            ALLOW_UNREAL_BUILD   = $allowBuild
            MAX_READ_BYTES       = "524288"
            MAX_OUTPUT_BYTES     = "262144"
            COMMAND_TIMEOUT_MS   = "600000"
            MCP_ESSENTIAL_TOOLS  = "1"
        }
    })

    if ($EnableAgentMode) {
        Write-Host "Agent mode ENABLED (write/build/commands)." -ForegroundColor Yellow
    } else {
        Write-Host "Safe mode default (read-only agent). Run installer/Enable-AgentMode.ps1 to enable writes/builds." -ForegroundColor Cyan
    }

    if (Test-Path $dateTimeJs) {
        $config.mcpServers = Merge-McpServer $config.mcpServers "current-datetime" ([ordered]@{
            command = $node
            args    = @($dateTimeJs)
        })
    }

    if (Test-Path $mcpRemoteProxy) {
        # preserve existing tavily entry only if not already set — optional remote search
        if (-not $config.mcpServers."tavily-remote") {
            Write-Host "Note: tavily-remote not configured (needs API key in mcp.json)."
        }
    }

    if (Test-Path $mcpPath) {
        Copy-Item -LiteralPath $mcpPath -Destination "$mcpPath.bak-portable-$(Get-Date -Format yyyyMMddHHmmss)" -Force
    }
    Write-JsonUtf8 $mcpPath $config
    Write-Host "Updated $mcpPath"
}

# portable marker for user
$markerPath = Join-Path $root "PORTABLE_ROOT.txt"
Set-Content -LiteralPath $markerPath -Encoding UTF8 -Value @(
    "UNREAL58_PORTABLE_ROOT=$root"
    "InstalledAt=$(Get-Date -Format o)"
    "Python=$python"
    "Node=$node"
)

if (-not $SkipProjectSetup) {
    Write-Host ""
    & (Join-Path $PSScriptRoot "Configure-ProjectIndexing.ps1") -SharedConfigPath $sharedConfigPath
}

. (Join-Path $PSScriptRoot "Install-PathHelpers.ps1")
$pathSync = Sync-InstallMachinePaths `
    -RagRoot $ragRoot `
    -AgentRoot $agentRoot `
    -DocumentsRoot $docsRoot `
    -SharedConfigPath $sharedConfigPath
Write-Host "Machine-local paths synced (engine: $($pathSync.EngineRoot))."

Write-Host ""
Write-Host "=== Install complete ==="
Write-Host "1. Restart LM Studio"
Write-Host "2. Enable MCP: unreal-rag, unreal-agent, current-datetime"
if ($SkipProjectSetup) {
    Write-Host "3. cd `"$ragRoot`""
    Write-Host "   .\rag.ps1 pick-project"
    Write-Host "4. Optional verify: .\installer\Verify-UnrealMcp.ps1"
}
else {
    Write-Host "3. Optional verify: .\installer\Verify-UnrealMcp.ps1"
}
