$ErrorActionPreference = "Stop"

function Expand-ConfigPathString {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $Value
    }
    $expanded = $Value.Replace("%USERPROFILE%", $HOME).Replace('$env:USERPROFILE', $HOME)
    return [Environment]::ExpandEnvironmentVariables($expanded)
}

function Get-EpicGamesRoot {
    param([string]$Override = "")
    if ($Override -and (Test-Path -LiteralPath $Override)) {
        return (Resolve-Path -LiteralPath $Override).Path
    }
    $candidates = @(
        (Join-Path ${env:ProgramFiles} "Epic Games"),
        (Join-Path ${env:ProgramFiles(x86)} "Epic Games")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return ""
}

function Get-DetectedUnrealEngineInstalls {
    param([string]$EpicGamesRoot = "")
    $root = Get-EpicGamesRoot -Override $EpicGamesRoot
    if ([string]::IsNullOrWhiteSpace($root) -or -not (Test-Path -LiteralPath $root)) {
        return @()
    }
    Get-ChildItem -LiteralPath $root -Directory -Filter "UE_5.*" -ErrorAction SilentlyContinue |
        ForEach-Object {
            $folder = $_.Name
            if ($folder -match "UE_5\.(\d+)") {
                [PSCustomObject]@{
                    FolderName = $folder
                    Version    = "5.$($Matches[1])"
                    Root       = $_.FullName
                }
            }
        } |
        Sort-Object { [version]$_.Version } -Descending
}

function Get-DefaultProjectSearchRoots {
    param([string]$DocumentsRoot = "")
    $docs = if ($DocumentsRoot) { $DocumentsRoot } else { Join-Path $HOME "Documents" }
    $candidates = @(
        (Join-Path $docs "Github"),
        (Join-Path $docs "GitHub"),
        (Join-Path $docs "Git"),
        (Join-Path $docs "Unreal Projects")
    )
    $roots = [System.Collections.Generic.List[string]]::new()
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate) -and -not $roots.Contains($candidate)) {
            [void]$roots.Add((Resolve-Path -LiteralPath $candidate).Path)
        }
    }
    return @($roots)
}

function Read-JsonObject {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Write-JsonUtf8 {
    param([string]$Path, $Object)
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    $json = $Object | ConvertTo-Json -Depth 40
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $json + [Environment]::NewLine, $utf8)
}

function Resolve-EngineSelection {
    param(
        [string]$EpicGamesRoot = "",
        [string]$PreferredVersion = ""
    )

    $engines = @(Get-DetectedUnrealEngineInstalls -EpicGamesRoot $EpicGamesRoot)
    if ($engines.Count -eq 0) {
        return [ordered]@{
            Version = if ($PreferredVersion) { $PreferredVersion } else { "5.8" }
            Root    = ""
            Source  = "not-found"
        }
    }

    if ($PreferredVersion) {
        $match = $engines | Where-Object { $_.Version -eq $PreferredVersion } | Select-Object -First 1
        if ($match) {
            return [ordered]@{
                Version = $match.Version
                Root    = $match.Root
                Source  = "preferred-version"
            }
        }
    }

    $pick = $engines[0]
    return [ordered]@{
        Version = $pick.Version
        Root    = $pick.Root
        Source  = "detected-latest"
    }
}

function Index-NamespaceFromVersion {
    param([string]$Version)
    $digits = ($Version -replace "[^\d]", "")
    if (-not $digits) { return "unreal58" }
    return "unreal$digits"
}

function Get-RagDataPaths {
    param(
        [string]$RagRoot,
        [string]$NamespaceOverride = ""
    )
    $configPath = Join-Path $RagRoot "config\workspace.json"
    $cfg = Read-JsonObject $configPath
    $ns = if ($NamespaceOverride) {
        $NamespaceOverride
    }
    elseif ($cfg -and $cfg.indexNamespace) {
        [string]$cfg.indexNamespace
    }
    else {
        if ($cfg -and $cfg.engineVersion) {
            Index-NamespaceFromVersion ([string]$cfg.engineVersion)
        }
        else {
            "unreal58"
        }
    }
    $dir = Join-Path $RagRoot ("data\" + $ns)
    return [PSCustomObject]@{
        Namespace       = $ns
        DataDir         = $dir
        IndexPath       = Join-Path $dir "rag.sqlite"
        ModuleGraphPath = Join-Path $dir "raw_module_graph.jsonl"
    }
}

function Resolve-RagIndexPath {
    param([string]$RagRoot)
    $configPath = Join-Path $RagRoot "config\workspace.json"
    $cfg = Read-JsonObject $configPath
    if ($cfg -and $cfg.indexPath) {
        $rel = ([string]$cfg.indexPath) -replace "/", "\"
        return Join-Path $RagRoot $rel
    }
    if ($cfg -and $cfg.indexNamespace) {
        return Join-Path $RagRoot ("data\$($cfg.indexNamespace)\rag.sqlite")
    }
    return Join-Path $RagRoot "data\unreal58\rag.sqlite"
}

function Assert-SafePackagePath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string[]]$SourceRoots = @(),
        [switch]$ForceUnsafePath
    )

    if (-not $Path) {
        throw "Package path is required."
    }

    $resolved = [System.IO.Path]::GetFullPath($Path)
    if ($resolved -match '^[A-Za-z]:\\?$') {
        throw "Unsafe package path (drive root): $resolved"
    }

    if (-not $ForceUnsafePath) {
        $tempRoot = [System.IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
        if (-not $resolved.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Package path must be under `$env:TEMP ($($tempRoot.TrimEnd('\')) or pass -ForceUnsafePath. Got: $resolved"
        }
    }

    foreach ($src in @($SourceRoots)) {
        if (-not $src) { continue }
        $srcFull = [System.IO.Path]::GetFullPath($src)
        if ($srcFull.Equals($resolved, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Package path must not equal source root: $resolved"
        }
        $srcPrefix = $srcFull.TrimEnd('\') + '\'
        $outPrefix = $resolved.TrimEnd('\') + '\'
        if ($resolved.StartsWith($srcPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Package path is nested inside source root: $resolved"
        }
        if ($srcFull.StartsWith($outPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Package path contains source root: $resolved"
        }
    }

    return $resolved
}

function Get-PortablePackageRobocopyExcludes {
    return [PSCustomObject]@{
        ExcludeDirs = @(
            ".git", ".agent", "node_modules", "wrapper_runs", "local_holdout_fixtures",
            "text_snapshot", "editor_export_jobs", "LyraStarterGame", "StackOBot",
            "AdvancedPuzzleConstructor", ".pytest_cache", ".pytest_tmp", ".venv",
            "DerivedDataCache", "Intermediate", "Saved", "Binaries",
            "lmstudio-unreal-agent-mcp", "mcp-tools"
        )
        ExcludeFiles = @("*.bak*", "PORTABLE_ROOT.txt", "raw_source.jsonl", "chunks.jsonl", "rag.staging.sqlite", "rag.sqlite", "*.sqlite")
    }
}

function Sync-WorkspaceJson {
    param(
        [string]$RagRoot,
        [string]$EngineRoot,
        [string]$EngineVersion,
        [switch]$ForceEngineResync
    )

    $configPath = Join-Path $RagRoot "config\workspace.json"
    $templatePath = Join-Path $RagRoot "config\workspace.json.template"
    $existing = Read-JsonObject $configPath
    if ($null -eq $existing) {
        $existing = Read-JsonObject $templatePath
    }
    if ($null -eq $existing) {
        $existing = [ordered]@{}
    }

    $namespace = Index-NamespaceFromVersion $EngineVersion
    if ($existing.indexNamespace -and -not $ForceEngineResync) {
        $namespace = [string]$existing.indexNamespace
    }
    $resolvedEngineVersion = $EngineVersion
    if ($existing.engineVersion -and -not $ForceEngineResync) {
        $resolvedEngineVersion = [string]$existing.engineVersion
    }
    $workspace = [ordered]@{}
    foreach ($prop in $existing.PSObject.Properties) {
        $workspace[$prop.Name] = $prop.Value
    }
    $workspace.rootPath = (Resolve-Path -LiteralPath $RagRoot).Path
    $workspace.engineVersion = $resolvedEngineVersion
    $workspace.indexNamespace = $namespace
    $workspace.indexPath = ("data/$namespace/rag.sqlite" -replace "\\", "/")
    $workspace.embeddingsPath = ("data/$namespace/embeddings" -replace "\\", "/")
    $workspace.defaultEngineRoot = $EngineRoot
    if (-not $workspace.Contains("knowledgeRoots") -or $null -eq $workspace["knowledgeRoots"]) {
        $workspace.knowledgeRoots = [ordered]@{
            guidelines       = "RAG_Project_Guidelines"
            gameDesign       = "Game_Design_Docs"
            projectSnapshots = "data/unreal_projects/text_snapshot"
        }
    }

    Write-JsonUtf8Atomic -Path $configPath -Object $workspace | Out-Null
    return $workspace
}

function Sync-AgentMcpJson {
    param(
        [string]$AgentRoot,
        [string]$RagRoot,
        [string]$DocumentsRoot,
        [string]$EngineRoot,
        [string[]]$SearchRoots = @()
    )

    $configPath = Join-Path $AgentRoot "config\agent-mcp.json"
    $roots = [System.Collections.Generic.List[string]]::new()
    foreach ($root in $SearchRoots) {
        $expanded = Expand-ConfigPathString $root
        if ($expanded -and (Test-Path -LiteralPath $expanded) -and -not $roots.Contains($expanded)) {
            [void]$roots.Add((Resolve-Path -LiteralPath $expanded).Path)
        }
    }
    foreach ($root in (Get-DefaultProjectSearchRoots -DocumentsRoot $DocumentsRoot)) {
        if (-not $roots.Contains($root)) {
            [void]$roots.Add($root)
        }
    }

    $payload = [ordered]@{
        projectSearchRoots    = @($roots)
        defaultEngineRoot     = $EngineRoot
        defaultPlatform       = "Win64"
        defaultConfiguration  = "Development"
        activeProject         = $null
    }
    Write-JsonUtf8Atomic -Path $configPath -Object $payload | Out-Null
    return $payload
}

function Sync-SharedWorkspaceEngine {
    param(
        [string]$SharedConfigPath,
        [string]$EngineRoot,
        [string[]]$SearchRoots = @()
    )

    if (-not (Test-Path -LiteralPath $SharedConfigPath)) {
        return $null
    }

    . (Join-Path (Split-Path $PSScriptRoot -Parent) "scripts\unreal_workspace_config.ps1")
    $config = Read-SharedConfig -Path $SharedConfigPath
    $config.defaultEngineRoot = $EngineRoot

    $expandedRoots = [System.Collections.Generic.List[string]]::new()
    foreach ($root in @($config.projectSearchRoots)) {
        $expanded = Expand-ConfigPathString ([string]$root)
        if ($expanded -and -not $expandedRoots.Contains($expanded)) {
            [void]$expandedRoots.Add($expanded)
        }
    }
    foreach ($root in $SearchRoots) {
        $expanded = Expand-ConfigPathString $root
        if ($expanded -and (Test-Path -LiteralPath $expanded)) {
            $resolved = (Resolve-Path -LiteralPath $expanded).Path
            if (-not $expandedRoots.Contains($resolved)) {
                [void]$expandedRoots.Add($resolved)
            }
        }
    }
    if ($expandedRoots.Count -gt 0) {
        $config.projectSearchRoots = @($expandedRoots)
    }

    Save-SharedConfig -Path $SharedConfigPath -Config $config
    return $config
}

function Sync-InstallMachinePaths {
    param(
        [string]$RagRoot,
        [string]$AgentRoot,
        [string]$DocumentsRoot = "",
        [string]$SharedConfigPath = "",
        [string]$EpicGamesRoot = "",
        [string]$PreferredEngineVersion = "",
        [switch]$SyncCline,
        [switch]$EnableAgentMode
    )

    $engine = Resolve-EngineSelection -EpicGamesRoot $EpicGamesRoot -PreferredVersion $PreferredEngineVersion
    $workspace = Sync-WorkspaceJson -RagRoot $RagRoot -EngineRoot $engine.Root -EngineVersion $engine.Version
    $searchRoots = @()
    if ($SharedConfigPath -and (Test-Path -LiteralPath $SharedConfigPath)) {
        $shared = Read-JsonObject $SharedConfigPath
        if ($shared -and $shared.projectSearchRoots) {
            $searchRoots = @($shared.projectSearchRoots)
        }
    }
    $agent = Sync-AgentMcpJson `
        -AgentRoot $AgentRoot `
        -RagRoot $RagRoot `
        -DocumentsRoot $DocumentsRoot `
        -EngineRoot $engine.Root `
        -SearchRoots $searchRoots

    $sharedConfig = $null
    if ($SharedConfigPath) {
        $sharedConfig = Sync-SharedWorkspaceEngine `
            -SharedConfigPath $SharedConfigPath `
            -EngineRoot $engine.Root `
            -SearchRoots $searchRoots
    }

    $clinePaths = Get-ClineMcpSettingsPaths
    $writeVsCode = $false
    $writeCli = $false
    $cline = $null
    if ($SyncCline) {
        $writeVsCode = Test-Path -LiteralPath $clinePaths.VsCode
        $writeCli = Test-Path -LiteralPath $clinePaths.Cli
        $cline = Sync-ClineMcpSettings `
            -RagRoot $RagRoot `
            -AgentRoot $AgentRoot `
            -DocumentsRoot $DocumentsRoot `
            -SharedConfigPath $SharedConfigPath `
            -PortableRoot (Split-Path -Parent $RagRoot) `
            -EnableAgentMode:$EnableAgentMode `
            -WriteVsCode:$writeVsCode `
            -WriteCli:$writeCli
    }

    return [ordered]@{
        EngineRoot  = $engine.Root
        EngineVersion = $engine.Version
        EngineSource = $engine.Source
        Workspace   = $workspace
        AgentConfig = $agent
        SharedConfig = $sharedConfig
        Cline       = $cline
    }
}

function Write-JsonUtf8NoBom([string]$Path, $Object) {
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $json = $Object | ConvertTo-Json -Depth 40
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $json + [Environment]::NewLine, $utf8)
}

function Get-ClineMcpSettingsPaths {
    return [ordered]@{
        VsCode = Join-Path $env:APPDATA "Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json"
        Cli    = Join-Path $HOME ".cline\data\settings\cline_mcp_settings.json"
    }
}

function Test-ClineMcpHasUnresolvedPlaceholders([string]$JsonText) {
    return ($JsonText -match '\{PYTHON_EXE\}' -or
        $JsonText -match '\{REPO_ROOT\}' -or
        $JsonText -match '\{NODE_EXE\}' -or
        $JsonText -match '\{AGENT_MCP_ROOT\}' -or
        $JsonText -match '\{LMSTUDIO_HOME\}' -or
        $JsonText -match '\{USER_DOCUMENTS\}')
}

function Build-ClineMcpConfig {
    param(
        [string]$RagRoot,
        [string]$AgentRoot,
        [string]$DocumentsRoot,
        [string]$SharedConfigPath,
        [string]$AgentConfigPath,
        [string]$PythonExe,
        [string]$NodeExe,
        [string]$PortableRoot,
        [string]$AgentStateRoot,
        [switch]$EnableAgentMode
    )

    $ragServer = Join-Path $RagRoot "scripts\unreal_rag_mcp.py"
    $ragIndex = Resolve-RagIndexPath -RagRoot $RagRoot
    $agentServer = Join-Path $AgentRoot "src\server.js"
    $allowWrite = if ($EnableAgentMode) { "1" } else { "0" }
    $allowCommands = if ($EnableAgentMode) { "1" } else { "0" }
    $allowBuild = if ($EnableAgentMode) { "1" } else { "0" }
    $validateOnWrite = if ($EnableAgentMode) { "1" } else { "0" }

    $workspaceRoot = $DocumentsRoot
    if ($SharedConfigPath -and (Test-Path -LiteralPath $SharedConfigPath)) {
        $shared = Read-JsonObject $SharedConfigPath
        $activeProject = [string]$shared.activeProject
        if ($activeProject -and (Test-Path -LiteralPath $activeProject)) {
            $workspaceRoot = Split-Path -Parent $activeProject
        }
    }

    return [ordered]@{
        mcpServers = [ordered]@{
            "unreal-rag" = [ordered]@{
                command = $PythonExe
                args    = @($ragServer, "--index", $ragIndex)
                timeout = 420000
                env     = [ordered]@{
                    SHARED_UNREAL_CONFIG   = $SharedConfigPath
                    AGENT_STATE_ROOT       = $AgentStateRoot
                    UNREAL58_ROOT          = $RagRoot
                    UNREAL58_PORTABLE_ROOT = $PortableRoot
                    PYTHONUTF8             = "1"
                    PYTHONIOENCODING       = "utf-8"
                    MCP_ESSENTIAL_TOOLS    = "1"
                }
            }
            "unreal-agent" = [ordered]@{
                command = $NodeExe
                args    = @($agentServer)
                timeout = 720000
                env     = [ordered]@{
                    WORKSPACE_ROOT              = $workspaceRoot
                    AGENT_MCP_CONFIG            = $AgentConfigPath
                    SHARED_UNREAL_CONFIG        = $SharedConfigPath
                    AGENT_STATE_ROOT            = $AgentStateRoot
                    UNREAL58_ROOT               = $RagRoot
                    UNREAL58_PORTABLE_ROOT      = $PortableRoot
                    ALLOW_WRITE                 = $allowWrite
                    ALLOW_COMMANDS              = $allowCommands
                    ALLOW_UNREAL_BUILD          = $allowBuild
                    VALIDATE_ON_WRITE           = $validateOnWrite
                    VALIDATE_ON_WRITE_TIMEOUT_MS = "45000"
                    MAX_READ_BYTES              = "524288"
                    MAX_OUTPUT_BYTES            = "262144"
                    COMMAND_TIMEOUT_MS          = "600000"
                    MCP_ESSENTIAL_TOOLS         = "1"
                }
            }
        }
    }
}

function Merge-ClineMcpSettings {
    param(
        $Existing,
        $Desired
    )

    if ($null -eq $Existing) {
        return $Desired
    }

    $merged = [ordered]@{}
    foreach ($property in $Existing.PSObject.Properties) {
        if ($property.Name -ne "mcpServers") {
            $merged[$property.Name] = $property.Value
        }
    }

    $servers = [ordered]@{}
    if ($Existing.mcpServers) {
        foreach ($property in $Existing.mcpServers.PSObject.Properties) {
            if ($property.Name -notin @("unreal-rag", "unreal-agent")) {
                $servers[$property.Name] = $property.Value
            }
        }
    }
    foreach ($name in @("unreal-rag", "unreal-agent")) {
        if ($Desired.mcpServers.$name) {
            $servers[$name] = $Desired.mcpServers.$name
        }
    }
    $merged["mcpServers"] = $servers
    return $merged
}

function Write-JsonUtf8Atomic {
    param(
        [string]$Path,
        $Object,
        [switch]$WhatIf
    )

    $json = ($Object | ConvertTo-Json -Depth 40) + [Environment]::NewLine
    if ($WhatIf) {
        return [ordered]@{
            Path    = $Path
            Changed = $true
            Preview = $json
        }
    }

    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }

    if (Test-Path -LiteralPath $Path) {
        try {
            $existingRaw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
            $existingObj = $existingRaw | ConvertFrom-Json
            $existingNorm = ($existingObj | ConvertTo-Json -Depth 40) + [Environment]::NewLine
            if ($existingNorm -eq $json) {
                return [ordered]@{
                    Path    = $Path
                    Changed = $false
                }
            }
        }
        catch {
            # Existing file unreadable - proceed with replace.
        }
    }

    $backupPath = $null
    if (Test-Path -LiteralPath $Path) {
        $backupPath = "$Path.bak-$(Get-Date -Format yyyyMMddHHmmss)"
        Copy-Item -LiteralPath $Path -Destination $backupPath -Force
    }

    $tempPath = "$Path.$PID.$([guid]::NewGuid().ToString('N')).tmp"
    try {
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText($tempPath, $json, $utf8)
        $null = Get-Content -LiteralPath $tempPath -Raw -Encoding UTF8 | ConvertFrom-Json
        Move-Item -LiteralPath $tempPath -Destination $Path -Force
    }
    catch {
        if (Test-Path -LiteralPath $tempPath) {
            Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
        }
        if ($backupPath -and (Test-Path -LiteralPath $backupPath)) {
            Copy-Item -LiteralPath $backupPath -Destination $Path -Force
        }
        throw
    }

    return [ordered]@{
        Path       = $Path
        BackupPath = $backupPath
        Changed    = $true
    }
}

function Write-McpConfigBatch {
    param(
        [Parameter(Mandatory = $true)]
        [array]$Entries
    )

    $completed = [System.Collections.Generic.List[object]]::new()
    try {
        foreach ($entry in $Entries) {
            $existedBefore = Test-Path -LiteralPath $entry.Path
            $result = Write-JsonUtf8Atomic -Path $entry.Path -Object $entry.Object
            $completed.Add([ordered]@{
                Path           = $entry.Path
                BackupPath     = $result.BackupPath
                Changed        = $result.Changed
                ExistedBefore  = $existedBefore
            })
        }
    }
    catch {
        for ($i = $completed.Count - 1; $i -ge 0; $i--) {
            $item = $completed[$i]
            if ($item.BackupPath -and (Test-Path -LiteralPath $item.BackupPath)) {
                Copy-Item -LiteralPath $item.BackupPath -Destination $item.Path -Force
            }
            elseif (-not $item.ExistedBefore -and (Test-Path -LiteralPath $item.Path)) {
                Remove-Item -LiteralPath $item.Path -Force -ErrorAction SilentlyContinue
            }
        }
        throw
    }
    return @($completed)
}

function Sync-ClineMcpSettings {
    param(
        [string]$RagRoot,
        [string]$AgentRoot,
        [string]$DocumentsRoot = "",
        [string]$SharedConfigPath = "",
        [string]$PythonExe = "",
        [string]$NodeExe = "",
        [string]$PortableRoot = "",
        [switch]$EnableAgentMode,
        [switch]$WriteVsCode,
        [switch]$WriteCli,
        [switch]$WhatIf
    )

    if (-not $DocumentsRoot) {
        $DocumentsRoot = Join-Path $HOME "Documents"
    }
    if (-not $SharedConfigPath) {
        $SharedConfigPath = Join-Path $HOME ".lmstudio\config\unreal-workspace.json"
    }
    if (-not $PortableRoot) {
        $PortableRoot = Split-Path -Parent $RagRoot
    }
    $agentConfigPath = Join-Path $AgentRoot "config\agent-mcp.json"
    $agentStateRoot = Join-Path (Split-Path (Split-Path $SharedConfigPath -Parent) -Parent) "state\unreal-agent"

    if (-not $PythonExe) {
        foreach ($path in @(
                (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
                (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
                (Join-Path $env:LOCALAPPDATA "Programs\Python\Python310\python.exe")
            )) {
            if (Test-Path $path) { $PythonExe = $path; break }
        }
        if (-not $PythonExe) {
            $cmd = Get-Command python -ErrorAction SilentlyContinue
            if ($cmd -and $cmd.Source -notlike "*\WindowsApps\*") { $PythonExe = $cmd.Source }
        }
    }
    if (-not $NodeExe) {
        $cmd = Get-Command node -ErrorAction SilentlyContinue
        if ($cmd) { $NodeExe = $cmd.Source }
    }

    $desired = Build-ClineMcpConfig `
        -RagRoot $RagRoot `
        -AgentRoot $AgentRoot `
        -DocumentsRoot $DocumentsRoot `
        -SharedConfigPath $SharedConfigPath `
        -AgentConfigPath $agentConfigPath `
        -PythonExe $PythonExe `
        -NodeExe $NodeExe `
        -PortableRoot $PortableRoot `
        -AgentStateRoot $agentStateRoot `
        -EnableAgentMode:$EnableAgentMode

    $paths = Get-ClineMcpSettingsPaths
    $writes = @()
    if ($WriteVsCode) { $writes += $paths.VsCode }
    if ($WriteCli) { $writes += $paths.Cli }

    $results = @()
    foreach ($targetPath in $writes) {
        $existing = Read-JsonObject $targetPath
        $merged = Merge-ClineMcpSettings -Existing $existing -Desired $desired
        $changedKeys = @("unreal-rag", "unreal-agent")
        if ($WhatIf) {
            Write-Host "[WhatIf] Would update $targetPath (keys: $($changedKeys -join ', '))" -ForegroundColor Cyan
        }
        $results += Write-JsonUtf8Atomic -Path $targetPath -Object $merged -WhatIf:$WhatIf
    }

    return [ordered]@{
        VsCodePath = if ($WriteVsCode) { $paths.VsCode } else { $null }
        CliPath    = if ($WriteCli) { $paths.Cli } else { $null }
        Config     = $desired
        Writes     = $results
        WhatIf     = [bool]$WhatIf
    }
}

function Get-WorkspaceEngineRootPath {
    param(
        [string]$RagRoot,
        [string]$FallbackEngineRoot = ""
    )

    $configPath = Join-Path $RagRoot "config\workspace.json"
    $cfg = Read-JsonObject $configPath
    if ($cfg -and $cfg.defaultEngineRoot) {
        $engineRoot = Expand-ConfigPathString ([string]$cfg.defaultEngineRoot)
        if ($engineRoot) {
            return $engineRoot
        }
    }

    $sharedPath = Join-Path $HOME ".lmstudio\config\unreal-workspace.json"
    $shared = Read-JsonObject $sharedPath
    if ($shared -and $shared.defaultEngineRoot) {
        $engineRoot = Expand-ConfigPathString ([string]$shared.defaultEngineRoot)
        if ($engineRoot) {
            return $engineRoot
        }
    }

    if ($FallbackEngineRoot) {
        return $FallbackEngineRoot
    }

    $engine = Resolve-EngineSelection
    return [string]$engine.Root
}

function Get-WorkspaceEngineSourcePath {
    param(
        [string]$RagRoot,
        [string]$FallbackEngineRoot = ""
    )

    $engineRoot = Get-WorkspaceEngineRootPath -RagRoot $RagRoot -FallbackEngineRoot $FallbackEngineRoot
    if (-not $engineRoot) {
        return ""
    }
    return Join-Path $engineRoot "Engine\Source"
}

function Get-WorkspaceUbtPath {
    param(
        [string]$RagRoot,
        [string]$FallbackEngineRoot = ""
    )

    $engineRoot = Get-WorkspaceEngineRootPath -RagRoot $RagRoot -FallbackEngineRoot $FallbackEngineRoot
    if (-not $engineRoot) {
        return "UnrealBuildTool.exe"
    }
    return Join-Path $engineRoot "Engine\Binaries\DotNET\UnrealBuildTool\UnrealBuildTool.exe"
}
