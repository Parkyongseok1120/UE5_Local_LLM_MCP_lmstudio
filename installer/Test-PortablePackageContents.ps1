param(
    [Parameter(Mandatory = $true)][string]$ZipPath
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ZipPath)) {
    throw "Zip not found: $ZipPath"
}

$forbiddenPatterns = @(
    '\\Users\\',
    '/Users/',
    '\\.git/',
    '/.git/',
    '\\.agent/',
    '/.agent/',
    'local_holdout_fixtures',
    'PORTABLE_ROOT.txt',
    'Epic Games\\UE_',
    'Program Files\\Epic'
)

$failures = @()
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead((Resolve-Path -LiteralPath $ZipPath).Path)
try {
    foreach ($entry in $zip.Entries) {
        $name = $entry.FullName.Replace('\', '/')
        foreach ($pattern in $forbiddenPatterns) {
            if ($name -match $pattern) {
                $failures += "Forbidden entry ($pattern): $name"
            }
        }
        if ($name -match '(^|/)data/[^/]+/[^/]+\.uproject$' -and $name -match 'holdout|fixture') {
            $failures += "Fixture uproject in package: $name"
        }
    }
}
finally {
    $zip.Dispose()
}

if ($failures.Count -gt 0) {
    foreach ($item in $failures) {
        Write-Host "[FAIL] $item" -ForegroundColor Red
    }
    throw "Portable package content scan failed with $($failures.Count) issue(s)."
}

Write-Host "[PASS] Portable package content scan: $ZipPath" -ForegroundColor Green
