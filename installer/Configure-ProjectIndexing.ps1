param(
    [string]$SharedConfigPath = (Join-Path $HOME ".lmstudio\config\unreal-workspace.json"),
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "..\scripts\unreal_workspace_config.ps1")

function Read-YesNo {
    param(
        [string]$Prompt,
        [bool]$DefaultYes = $true
    )

    $suffix = if ($DefaultYes) { "[Y/n]" } else { "[y/N]" }
    $answer = Read-Host "$Prompt $suffix"
    if ([string]::IsNullOrWhiteSpace($answer)) {
        return $DefaultYes
    }
    return ($answer -match '^[Yy]')
}

function Read-IndexingTier {
    Write-Host ""
    Write-Host "Indexing tier:"
    Write-Host "  [1] Lite     - project text + asset paths only (fastest)"
    Write-Host "  [2] Standard - + project C++ symbols + engine API symbols (recommended)"
    Write-Host "  [3] Full     - + entire engine source text dump (large, slow)"
    $choice = Read-Host "Choice (Enter=2)"
    switch ($choice) {
        "1" { return "lite" }
        "3" { return "full" }
        default { return "standard" }
    }
}

function Get-InitialPickerDirectory {
    param($Config, [string[]]$Roots)

    if ($Config.activeProject -and (Test-Path -LiteralPath $Config.activeProject)) {
        return Split-Path -Parent $Config.activeProject
    }
    foreach ($root in $Roots) {
        if ($root -and (Test-Path -LiteralPath $root)) {
            return $root
        }
    }
    return $HOME
}

function Pick-UprojectPath {
    param([string]$InitialDirectory)

    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = "Select Unreal project (.uproject) to index"
    $dialog.Filter = "Unreal Project (*.uproject)|*.uproject"
    $dialog.CheckFileExists = $true
    $dialog.Multiselect = $false
    if ($InitialDirectory -and (Test-Path -LiteralPath $InitialDirectory)) {
        $dialog.InitialDirectory = $InitialDirectory
    }
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        return $dialog.FileName
    }
    return $null
}

function Pick-FolderPath {
    param(
        [string]$InitialDirectory,
        [string]$Description = "Select folder"
    )

    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = $Description
    $dialog.ShowNewFolderButton = $false
    if ($InitialDirectory -and (Test-Path -LiteralPath $InitialDirectory)) {
        $dialog.SelectedPath = $InitialDirectory
    }
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        return $dialog.SelectedPath
    }
    return $null
}

function Show-ConfiguredProjectSummary {
    param($Config)

    Write-Host ""
    Write-Host "Configured search roots:"
    foreach ($root in @($Config.projectSearchRoots)) {
        if ($root) {
            Write-Host "  - $root"
        }
    }
    if ($Config.activeProject) {
        Write-Host "Active project:"
        Write-Host "  $($Config.activeProject)"
    }
    Write-Host "Indexing tier: $($Config.indexingTier)"
    Write-Host "Auto Editor export: $($Config.autoEditorExport)"
    if ($Config.editorExportDir) {
        Write-Host "Editor export dir:"
        Write-Host "  $($Config.editorExportDir)"
    }
    if ($Config.editorExportContentPath) {
        Write-Host "Editor export content path:"
        Write-Host "  $($Config.editorExportContentPath)"
    }
}

$config = Read-SharedConfig -Path $SharedConfigPath
Sync-SharedConfigSearchRoots -Config $config -SharedConfigPath $SharedConfigPath
if (-not $config.indexingTier) {
    $config.indexingTier = "standard"
}
$initialDir = Get-InitialPickerDirectory -Config $config -Roots @($config.projectSearchRoots)

if ($NonInteractive) {
    if ($config.activeProject) {
        Initialize-EditorExportSettings -Config $config -EnableAutoExport:([bool]($config.autoEditorExport -ne $false)) | Out-Null
    }
    Save-SharedConfig -Path $SharedConfigPath -Config $config
    return
}

Write-Host ""
Write-Host "=== Project indexing setup ==="
Write-Host ""

if (-not (Read-YesNo -Prompt "Add project paths for indexing?" -DefaultYes $true)) {
    Write-Host ""
    Write-Host "Using default search roots."
}
else {
    $addedCount = 0
    while ($true) {
        Write-Host ""
        Write-Host "Add by:"
        Write-Host "  [1] .uproject file"
        Write-Host "  [2] folder"
        $mode = Read-Host "Choice (Enter=1)"
        $picked = if ($mode -eq "2") {
            Pick-FolderPath -InitialDirectory $initialDir -Description "Select folder to scan for Unreal projects"
        }
        else {
            Pick-UprojectPath -InitialDirectory $initialDir
        }

        if (-not $picked) {
            Write-Host "Selection cancelled."
        }
        else {
            try {
                $resolved = Add-IndexingTarget -Config $config -Path $picked
                Save-SharedConfig -Path $SharedConfigPath -Config $config
                $addedCount++
                Write-Host "Added: $resolved"
                if ($resolved -like "*.uproject") {
                    $initialDir = Split-Path -Parent $resolved
                }
                else {
                    $initialDir = $resolved
                }
            }
            catch {
                Write-Host "Error: $_" -ForegroundColor Red
            }
        }

        if (-not (Read-YesNo -Prompt "Add more folders or .uproject files to index?" -DefaultYes $false)) {
            break
        }
    }
}

$config.indexingTier = Read-IndexingTier

Write-Host ""
Write-Host "Blueprint/material metadata can be exported automatically from Unreal Editor during indexing."
Write-Host "Export files are stored under the active project's Saved/LmStudioMetadataExports folder by default."
$enableAutoExport = $false
if ($config.activeProject) {
    $enableAutoExport = Read-YesNo -Prompt "Automatically export Blueprint/Material metadata during install/indexing?" -DefaultYes $true
}
else {
    Write-Host "No active .uproject selected yet. Editor export will stay disabled until you run pick-project."
}

if ($config.activeProject) {
    $exportDir = Initialize-EditorExportSettings -Config $config -EnableAutoExport:$enableAutoExport
    Write-Host "Editor export dir: $exportDir"
    Write-Host "Editor export content path: $($config.editorExportContentPath)"
}
else {
    $config.autoEditorExport = $false
}

Show-ConfiguredProjectSummary -Config $config
Save-SharedConfig -Path $SharedConfigPath -Config $config
Write-Host ""
Write-Host "Project setup complete. Continuing with indexing/build..."
