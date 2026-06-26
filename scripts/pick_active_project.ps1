param(
    [switch]$Explorer,
    [switch]$Clear,
    [string]$SharedConfig = (Join-Path $HOME ".lmstudio\config\unreal-workspace.json")
)

$ErrorActionPreference = "Stop"

function Read-SharedConfig {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        return [ordered]@{
            activeProject        = $null
            projectSearchRoots   = @(
                (Join-Path $HOME "Documents\Git"),
                (Join-Path $HOME "Documents\Unreal Projects")
            )
            defaultEngineRoot    = "C:\Program Files\Epic Games\UE_5.8"
            defaultPlatform      = "Win64"
            defaultConfiguration = "Development"
        }
    }
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Save-SharedConfig {
    param([string]$Path, $Config)
    $directory = Split-Path -Parent $Path
    if (-not (Test-Path $directory)) {
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
    }
    $Config.updatedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $json = $Config | ConvertTo-Json -Depth 10
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $json + [Environment]::NewLine, $utf8NoBom)
}

function Get-EngineAssociation {
    param([string]$ProjectPath)
    try {
        $json = Get-Content -LiteralPath $ProjectPath -Raw -Encoding UTF8 | ConvertFrom-Json
        return [string]$json.EngineAssociation
    }
    catch {
        return ""
    }
}

function Find-UnrealProjects {
    param([string[]]$Roots)

    $skipParts = @(".git", ".vs", "Binaries", "Intermediate", "Saved", "DerivedDataCache", "node_modules")
    $results = @{}
    foreach ($root in $Roots) {
        if (-not $root -or -not (Test-Path -LiteralPath $root)) {
            continue
        }
        Get-ChildItem -LiteralPath $root -Recurse -Filter "*.uproject" -File -ErrorAction SilentlyContinue |
            Where-Object {
                $skip = $false
                foreach ($part in $_.FullName.Split([IO.Path]::DirectorySeparatorChar)) {
                    if ($skipParts -contains $part) { $skip = $true; break }
                }
                -not $skip
            } |
            ForEach-Object {
                $key = $_.FullName.ToLowerInvariant()
                if (-not $results.ContainsKey($key)) {
                    $results[$key] = $_
                }
            }
    }
    return $results.Values | Sort-Object LastWriteTime -Descending
}

function Set-ActiveProjectPath {
    param([string]$Path, $Config, [string]$SharedConfigPath)
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    if ($resolved -notlike "*.uproject") {
        throw "선택한 파일이 .uproject 가 아닙니다: $resolved"
    }
    $Config.activeProject = $resolved
    Save-SharedConfig -Path $SharedConfigPath -Config $Config
    Write-Host ""
    Write-Host "Active project 설정 완료:"
    Write-Host "  $resolved"
    return $resolved
}

$config = Read-SharedConfig -Path $SharedConfig
$roots = @($config.projectSearchRoots | Where-Object { $_ -and ($_ -notlike "*\Unreal58-RAG\data") })
if ($roots.Count -eq 0) {
    $roots = @(
        (Join-Path $HOME "Documents\Git"),
        (Join-Path $HOME "Documents\Unreal Projects")
    )
}

if ($Clear) {
    $config.activeProject = $null
    Save-SharedConfig -Path $SharedConfig -Config $config
    Write-Host "Active project 를 해제했습니다."
    exit 0
}

if ($Explorer) {
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = "Unreal 프로젝트 (.uproject) 선택"
    $dialog.Filter = "Unreal Project (*.uproject)|*.uproject"
    $dialog.CheckFileExists = $true
    $dialog.Multiselect = $false
    if ($config.activeProject -and (Test-Path -LiteralPath $config.activeProject)) {
        $dialog.InitialDirectory = Split-Path -Parent $config.activeProject
    }
    elseif ($roots.Count -gt 0 -and (Test-Path -LiteralPath $roots[0])) {
        $dialog.InitialDirectory = $roots[0]
    }

    if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
        Write-Host "선택이 취소되었습니다."
        exit 0
    }

    Set-ActiveProjectPath -Path $dialog.FileName -Config $config -SharedConfigPath $SharedConfig | Out-Null
    exit 0
}

$projects = Find-UnrealProjects -Roots $roots
if (-not $projects -or $projects.Count -eq 0) {
    Write-Host "검색 경로에서 .uproject 를 찾지 못했습니다."
    Write-Host "파일 탐색기로 선택하려면:"
    Write-Host "  .\rag.ps1 pick-project -Explorer"
    exit 1
}

$activePath = [string]$config.activeProject
$rows = foreach ($project in $projects) {
    $engine = Get-EngineAssociation -ProjectPath $project.FullName
    $isActive = ($activePath -and ($project.FullName -eq $activePath))
  [PSCustomObject]@{
        Active    = if ($isActive) { "*" } else { "" }
        Project   = $project.BaseName
        Engine    = if ($engine) { $engine } else { "?" }
        Modified  = $project.LastWriteTime.ToString("yyyy-MM-dd HH:mm")
        Folder    = $project.DirectoryName
        FullPath  = $project.FullName
    }
}

$title = "Unreal Active Project 선택 (행 하나 클릭 후 OK)"
$selected = $rows | Out-GridView -Title $title -OutputMode Single

if (-not $selected) {
    Write-Host "선택이 취소되었습니다."
    Write-Host "파일 탐색기로 고르려면: .\rag.ps1 pick-project -Explorer"
    exit 0
}

Set-ActiveProjectPath -Path $selected.FullPath -Config $config -SharedConfigPath $SharedConfig | Out-Null
