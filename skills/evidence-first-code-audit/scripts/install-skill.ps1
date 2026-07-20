[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$DestinationRoot = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$skillRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$skillName = Split-Path -Leaf $skillRoot

if ([string]::IsNullOrWhiteSpace($DestinationRoot)) {
    $codexRoot = $env:CODEX_HOME
    if ([string]::IsNullOrWhiteSpace($codexRoot)) {
        if ([string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
            throw "Set CODEX_HOME or USERPROFILE, or pass -DestinationRoot explicitly."
        }
        $codexRoot = Join-Path $env:USERPROFILE ".codex"
    }
    $DestinationRoot = Join-Path $codexRoot "skills"
}

$destinationRootFull = [System.IO.Path]::GetFullPath($DestinationRoot)
$destination = [System.IO.Path]::GetFullPath((Join-Path $destinationRootFull $skillName))
$sourcePrefix = $skillRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if ($destination.Equals($skillRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    $destination.StartsWith($sourcePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Destination must not equal or be nested under the skill source: $destination"
}

if (Test-Path -LiteralPath $destination) {
    if (-not $Force) {
        throw "Destination already exists: $destination. Re-run with -Force to replace it."
    }
    if ($PSCmdlet.ShouldProcess($destination, "Remove existing skill before install")) {
        Remove-Item -LiteralPath $destination -Recurse -Force
    }
}

if ($PSCmdlet.ShouldProcess($destination, "Install $skillName")) {
    New-Item -ItemType Directory -Path $destinationRootFull -Force | Out-Null
    Copy-Item -LiteralPath $skillRoot -Destination $destinationRootFull -Recurse -Force
}

Write-Output "Skill source: $skillRoot"
Write-Output "Skill destination: $destination"
