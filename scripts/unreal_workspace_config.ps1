$ErrorActionPreference = "Stop"

function Expand-ConfigPathString {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $Value
    }
    $expanded = $Value.Replace("%USERPROFILE%", $HOME).Replace('$env:USERPROFILE', $HOME)
    return [Environment]::ExpandEnvironmentVariables($expanded)
}

function ConvertTo-ConfigHashtable {
    param($Object)
    if ($null -eq $Object) {
        return [ordered]@{}
    }
    if ($Object -is [System.Collections.IDictionary]) {
        $result = [ordered]@{}
        foreach ($key in $Object.Keys) {
            $result[$key] = $Object[$key]
        }
        return $result
    }
    $result = [ordered]@{}
    foreach ($prop in $Object.PSObject.Properties) {
        $result[$prop.Name] = $prop.Value
    }
    return $result
}

function Read-SharedConfig {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        return [ordered]@{
            activeProject        = $null
            projectSearchRoots   = @(
                (Join-Path $HOME "Documents\Github"),
                (Join-Path $HOME "Documents\Git"),
                (Join-Path $HOME "Documents\Unreal Projects")
            )
            defaultEngineRoot    = ""
            defaultPlatform      = "Win64"
            defaultConfiguration = "Development"
            indexingTier            = "standard"
            editorExportDir         = $null
            editorExportContentPath = "/Game"
            editorExportScope       = "all"
            editorExportTimeoutSec  = 1800
            autoEditorExport        = $true
            installEditorGraphPlugin = $false
        }
    }
    $parsed = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    return ConvertTo-ConfigHashtable $parsed
}

function Resolve-EditorExportDir {
    param($Config)

    $active = [string]$Config.activeProject
    $projectRoot = ""
    if ($active -and (Test-Path -LiteralPath $active)) {
        $projectRoot = Split-Path -Parent $active
    }

    $default = if ($projectRoot) {
        Join-Path $projectRoot "Saved\LmStudioMetadataExports"
    }
    else {
        Join-Path $env:LOCALAPPDATA "LmStudio\UnrealMetadataExports"
    }

    $configured = Expand-ConfigPathString ([string]$Config.editorExportDir)
    if (-not $configured) {
        return $default
    }

    if ($projectRoot) {
        try {
            $configuredResolved = (Resolve-Path -LiteralPath $configured).Path
            $projectResolved = (Resolve-Path -LiteralPath $projectRoot).Path
            if ($configuredResolved -eq $projectResolved) {
                return $default
            }
        }
        catch {
            if ($configured -eq $projectRoot) {
                return $default
            }
        }
    }

    if (Test-Path -LiteralPath $configured) {
        return (Resolve-Path -LiteralPath $configured).Path
    }
    return $configured
}

function Initialize-EditorExportSettings {
    param(
        $Config,
        [bool]$EnableAutoExport = $true
    )

    $exportDir = Resolve-EditorExportDir -Config $Config
    $null = New-Item -ItemType Directory -Force -Path $exportDir | Out-Null
    $Config.editorExportDir = $exportDir
    if (-not $Config.editorExportContentPath) {
        $Config.editorExportContentPath = "/Game"
    }
    if (-not $Config.editorExportScope) {
        $Config.editorExportScope = "all"
    }
    if (-not $Config.editorExportTimeoutSec) {
        $Config.editorExportTimeoutSec = 1800
    }
    $Config.autoEditorExport = [bool]$EnableAutoExport
    return $exportDir
}

function Save-SharedConfig {
    param([string]$Path, $Config)
    $directory = Split-Path -Parent $Path
    if (-not (Test-Path $directory)) {
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
    }
    $Config["updatedAt"] = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $json = $Config | ConvertTo-Json -Depth 10
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $json + [Environment]::NewLine, $utf8NoBom)
}

function Get-DefaultProjectSearchRoots {
    $candidates = @(
        (Join-Path $HOME "Documents\Github"),
        (Join-Path $HOME "Documents\Git"),
        (Join-Path $HOME "Documents\Unreal Projects")
    )
    return @($candidates | Where-Object { Test-Path -LiteralPath $_ })
}

function Resolve-ProjectSearchRoots {
    param([string[]]$ConfiguredRoots)

    $roots = [System.Collections.Generic.List[string]]::new()
    foreach ($root in $ConfiguredRoots) {
        $expanded = Expand-ConfigPathString ([string]$root)
        if (-not $expanded -or ($expanded -like "*\Unreal58-RAG\data")) {
            continue
        }
        if (Test-Path -LiteralPath $expanded) {
            $resolved = (Resolve-Path -LiteralPath $expanded).Path
            if (-not $roots.Contains($resolved)) {
                [void]$roots.Add($resolved)
            }
            continue
        }
        if ($expanded -like "*\Documents\Git") {
            $github = Join-Path $HOME "Documents\Github"
            if ((Test-Path -LiteralPath $github) -and -not $roots.Contains($github)) {
                [void]$roots.Add((Resolve-Path -LiteralPath $github).Path)
            }
        }
    }

    if ($roots.Count -eq 0) {
        foreach ($defaultRoot in (Get-DefaultProjectSearchRoots)) {
            [void]$roots.Add($defaultRoot)
        }
    }

    return @($roots)
}

function Add-SearchRootIfMissing {
    param($Config, [string]$Root)

    $resolved = (Resolve-Path -LiteralPath $Root).Path
    $roots = @($Config.projectSearchRoots)
    if ($roots -notcontains $resolved) {
        $Config.projectSearchRoots = @($roots + $resolved)
    }
    return $resolved
}

function Ensure-ProjectSearchRootForPath {
    param($Config, [string]$ProjectPath)

    $docsRoot = Join-Path $HOME "Documents"
    foreach ($name in @("Github", "Git", "Unreal Projects")) {
        $candidate = Join-Path $docsRoot $name
        if ($ProjectPath -like "$candidate*") {
            Add-SearchRootIfMissing -Config $Config -Root $candidate | Out-Null
            return
        }
    }
}

function Add-IndexingTarget {
    param($Config, [string]$Path)

    $resolved = (Resolve-Path -LiteralPath $Path).Path
    if ($resolved -like "*.uproject") {
        if (-not $Config.activeProject) {
            $Config.activeProject = $resolved
        }
        Ensure-ProjectSearchRootForPath -Config $Config -ProjectPath $resolved
        Add-SearchRootIfMissing -Config $Config -Root (Split-Path -Parent $resolved) | Out-Null
        return $resolved
    }

    if (-not (Test-Path -LiteralPath $resolved -PathType Container)) {
        throw "Select a folder or .uproject file: $resolved"
    }

    return (Add-SearchRootIfMissing -Config $Config -Root $resolved)
}

function Sync-SharedConfigSearchRoots {
    param($Config, [string]$SharedConfigPath)

    $originalRoots = @($Config.projectSearchRoots)
    $roots = Resolve-ProjectSearchRoots -ConfiguredRoots $originalRoots
    if ($roots.Count -gt 0) {
        $Config.projectSearchRoots = $roots
    }
    $rootsChanged = ($originalRoots -join "|") -ne (@($Config.projectSearchRoots) -join "|")
    if ($rootsChanged) {
        Save-SharedConfig -Path $SharedConfigPath -Config $Config
    }
}
