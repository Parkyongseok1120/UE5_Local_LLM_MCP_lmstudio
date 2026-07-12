param(
    [string]$Path = "",
    [switch]$ExpectFailure
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Install-PathHelpers.ps1")

try {
    $resolved = Assert-SafePackagePath -Path $Path -SourceRoots @((Resolve-Path (Join-Path $PSScriptRoot "..")).Path)
    if ($ExpectFailure) {
        Write-Error "Expected Assert-SafePackagePath to fail for $Path"
        exit 1
    }
    Write-Output $resolved
    exit 0
}
catch {
    if ($ExpectFailure) {
        Write-Output $_.Exception.Message
        exit 0
    }
    throw
}
