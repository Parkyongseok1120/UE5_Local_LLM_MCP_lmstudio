param(
    [string]$OldPath = "",
    [string]$NewPath = "",
    [switch]$SkipRename,
    [switch]$SkipBuild,
    [switch]$SkipEval,
    [switch]$RemoveJunctionFirst
)

$ErrorActionPreference = "Stop"

function Assert-NoUtf8Bom($Path) {
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        throw "UTF-8 BOM detected: $Path"
    }
}

function Assert-JsonContainsNewPath($Path, $ExpectedPath, $OldPathText) {
    $raw = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    $raw | ConvertFrom-Json | Out-Null
    if ($raw -notlike "*$ExpectedPath*") {
        throw "Expected path missing from $Path"
    }
    if ($raw -like "*$OldPathText*") {
        throw "Old path remains in $Path"
    }
}

$oldFull = [System.IO.Path]::GetFullPath($OldPath)
$newFull = [System.IO.Path]::GetFullPath($NewPath)
$newName = Split-Path -Leaf $newFull

if (-not $SkipRename) {
    if ($RemoveJunctionFirst -and (Test-Path -LiteralPath $newFull)) {
        $item = Get-Item -LiteralPath $newFull -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            cmd /c rmdir "$newFull"
            Write-Host "Removed junction: $newFull"
        }
    }

    if ((Test-Path -LiteralPath $oldFull) -and -not (Test-Path -LiteralPath $newFull)) {
        Rename-Item -LiteralPath $oldFull -NewName $newName
        Write-Host "Renamed workspace folder to $newFull"
    }
    elseif ((Test-Path -LiteralPath $newFull) -and -not (Test-Path -LiteralPath $oldFull)) {
        Write-Host "Workspace already at $newFull"
    }
    elseif ((Test-Path -LiteralPath $oldFull) -and (Test-Path -LiteralPath $newFull)) {
        Write-Host "Both old and new paths exist. Remove junction with -RemoveJunctionFirst or rename manually."
    }
    else {
        throw "Neither old nor new workspace path exists."
    }
}

$workspace = (Resolve-Path -LiteralPath $newFull).Path
Set-Location -LiteralPath $workspace

if (-not $SkipBuild) {
    powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 build
}

if (-not $SkipEval) {
    powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 eval-unreal-programming
    powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 eval-game-design
}

powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_lmstudio_mcp.ps1

$mcpPath = Join-Path $HOME ".lmstudio\mcp.json"
$syncPath = Join-Path $HOME ".lmstudio\.internal\last-synced-mcp-state.json"
foreach ($path in @($mcpPath, $syncPath)) {
    Assert-NoUtf8Bom $path
    Assert-JsonContainsNewPath $path $newFull $oldFull
}

Write-Host "Workspace rename and LM Studio MCP refresh completed:"
Write-Host $workspace
