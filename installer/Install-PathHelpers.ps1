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
    if (-not (Test-Path -LiteralPath $root)) {
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
        Sort-Object Version -Descending
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

function Sync-WorkspaceJson {
    param(
        [string]$RagRoot,
        [string]$EngineRoot,
        [string]$EngineVersion
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
    $workspace = [ordered]@{}
    foreach ($prop in $existing.PSObject.Properties) {
        $workspace[$prop.Name] = $prop.Value
    }
    $workspace.rootPath = (Resolve-Path -LiteralPath $RagRoot).Path
    $workspace.engineVersion = $EngineVersion
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

    Write-JsonUtf8 $configPath $workspace
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
    $dataRoot = Join-Path $RagRoot "data"
    if ((Test-Path -LiteralPath $dataRoot) -and -not $roots.Contains($dataRoot)) {
        [void]$roots.Add((Resolve-Path -LiteralPath $dataRoot).Path)
    }

    $payload = [ordered]@{
        projectSearchRoots    = @($roots)
        defaultEngineRoot     = $EngineRoot
        defaultPlatform       = "Win64"
        defaultConfiguration  = "Development"
        activeProject         = $null
    }
    Write-JsonUtf8 $configPath $payload
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
        [string]$PreferredEngineVersion = ""
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

    return [ordered]@{
        EngineRoot  = $engine.Root
        EngineVersion = $engine.Version
        EngineSource = $engine.Source
        Workspace   = $workspace
        AgentConfig = $agent
        SharedConfig = $sharedConfig
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
