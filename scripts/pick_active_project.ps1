param(
    [switch]$Explorer,
    [switch]$Clear,
    [string]$SharedConfig = (Join-Path $HOME ".lmstudio\config\unreal-workspace.json")
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "unreal_workspace_config.ps1")

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
        throw "Selected file is not a .uproject: $resolved"
    }
    $previous = [string]$Config.activeProject
    $Config.activeProject = $resolved
    Ensure-ProjectSearchRootForPath -Config $Config -ProjectPath $resolved
    Save-SharedConfig -Path $SharedConfigPath -Config $Config
    Write-Host ""
    Write-Host "Active project set:"
    Write-Host "  $resolved"
    Invoke-ActiveProjectSetup -ProjectPath $resolved -SharedConfigPath $SharedConfigPath -PreviousProjectPath $previous
    return $resolved
}

function Invoke-ActiveProjectSetup {
    param(
        [string]$ProjectPath,
        [string]$SharedConfigPath,
        [string]$PreviousProjectPath = ""
    )

    $cfg = Read-SharedConfig -Path $SharedConfigPath
    $autoSetup = $true
    if ($null -ne $cfg.autoSetupOnProjectSwitch) {
        $autoSetup = [bool]$cfg.autoSetupOnProjectSwitch
    }
    if (-not $autoSetup) {
        Write-Host "Auto setup on project switch is disabled (autoSetupOnProjectSwitch=false)."
        return
    }

    $py = & {
        $bundled = Join-Path $HOME ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
        if (Test-Path $bundled) { return $bundled }
        $python = Get-Command python -ErrorAction SilentlyContinue
        if ($python -and $python.Source -notlike "*\WindowsApps\*") { return $python.Source }
        throw "Python not found"
    }

    $script = Join-Path $PSScriptRoot "on_active_project_changed.py"
    Write-Host ""
    Write-Host "Checking plugin install and project index for active project..."
    $args = @($script, "--project", $ProjectPath)
    if ($PreviousProjectPath -and ($PreviousProjectPath -eq $ProjectPath)) {
        $args += @("--previous-project", $PreviousProjectPath)
    }
    & $py @args
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Auto setup reported issues. Run .\rag.ps1 sync-active-project manually if needed."
    }
}

$config = Read-SharedConfig -Path $SharedConfig
Sync-SharedConfigSearchRoots -Config $config -SharedConfigPath $SharedConfig
$roots = @($config.projectSearchRoots)

if ($Clear) {
    $config.activeProject = $null
    Save-SharedConfig -Path $SharedConfig -Config $config
    Write-Host "Active project cleared."
    exit 0
}

if ($Explorer) {
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = "Select Unreal project (.uproject)"
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
        Write-Host "Selection cancelled."
        exit 0
    }

    Set-ActiveProjectPath -Path $dialog.FileName -Config $config -SharedConfigPath $SharedConfig | Out-Null
    exit 0
}

$projects = @(Find-UnrealProjects -Roots $roots)
$activePath = [string]$config.activeProject
if ($activePath -and (Test-Path -LiteralPath $activePath)) {
    $activeFile = Get-Item -LiteralPath $activePath
    $alreadyListed = $false
    foreach ($project in $projects) {
        if ($project.FullName -eq $activeFile.FullName) {
            $alreadyListed = $true
            break
        }
    }
    if (-not $alreadyListed) {
        $projects = @($activeFile) + $projects
    }
}

if ($projects.Count -eq 0) {
    Write-Host "No .uproject files found under configured search roots."
    Write-Host "To pick via file explorer, run:"
    Write-Host "  .\rag.ps1 pick-project -Explorer"
    exit 1
}

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

$title = "Select Unreal Active Project (click one row, then OK)"
$selected = $rows | Out-GridView -Title $title -OutputMode Single

if (-not $selected) {
    Write-Host "Selection cancelled."
    Write-Host "To pick via file explorer, run: .\rag.ps1 pick-project -Explorer"
    exit 0
}

Set-ActiveProjectPath -Path $selected.FullPath -Config $config -SharedConfigPath $SharedConfig | Out-Null
