[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputPath,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$skillRoot = Split-Path -Parent $PSScriptRoot
$source = [System.IO.Path]::GetFullPath((Join-Path $skillRoot "references\portable-rule.md"))
$destination = [System.IO.Path]::GetFullPath($OutputPath)
$destinationParent = Split-Path -Parent $destination

if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "Portable rule source is missing: $source"
}
if ($destination.Equals($source, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Output must not overwrite the portable rule source: $destination"
}
if (Test-Path -LiteralPath $destination -PathType Leaf) {
    if (-not $Force) {
        throw "Output already exists: $destination. Re-run with -Force to replace it."
    }
}

if ($PSCmdlet.ShouldProcess($destination, "Install portable evidence-first agent rule")) {
    if (-not [string]::IsNullOrWhiteSpace($destinationParent)) {
        New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
    }
    Copy-Item -LiteralPath $source -Destination $destination -Force
}

Write-Output "Rule source: $source"
Write-Output "Rule destination: $destination"
