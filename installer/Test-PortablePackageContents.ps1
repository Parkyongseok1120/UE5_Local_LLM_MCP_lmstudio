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

$contentScanPatterns = @(
    '\\Users\\[^\\]+\\Users\\',
    'lmstudio-unreal-agent-mcp[/\\]lmstudio-unreal-agent-mcp',
    'api[_-]?key\s*[:=]\s*["'']?[a-zA-Z0-9]{16,}'
)

$textExtensions = @('.json', '.ps1', '.js', '.py', '.md', '.txt', '.bat', '.yml', '.yaml')

$failures = @()
$serverJsPaths = @()
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
        if ($name -match '(^|/)server\.js$') {
            $serverJsPaths += $name
        }
        $ext = [System.IO.Path]::GetExtension($name).ToLowerInvariant()
        if ($textExtensions -contains $ext -and $entry.Length -gt 0 -and $entry.Length -lt 5MB) {
            $reader = New-Object System.IO.StreamReader($entry.Open())
            try {
                $text = $reader.ReadToEnd()
                foreach ($pattern in $contentScanPatterns) {
                    if ($text -match $pattern) {
                        $failures += "Forbidden content ($pattern) in $name"
                    }
                }
            }
            finally {
                $reader.Dispose()
            }
        }
    }
}
finally {
    $zip.Dispose()
}

if ($serverJsPaths.Count -ne 1) {
    $failures += "Expected exactly one server.js in portable package, found $($serverJsPaths.Count): $($serverJsPaths -join ', ')"
}

if ($failures.Count -gt 0) {
    foreach ($item in $failures) {
        Write-Host "[FAIL] $item" -ForegroundColor Red
    }
    throw "Portable package content scan failed with $($failures.Count) issue(s)."
}

Write-Host "[PASS] Portable package content scan: $ZipPath" -ForegroundColor Green
