param(
    [Parameter(Mandatory = $true)][string]$RagRoot
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Install-PathHelpers.ps1")
$indexPath = Resolve-RagIndexPath -RagRoot $RagRoot
Write-Output $indexPath
