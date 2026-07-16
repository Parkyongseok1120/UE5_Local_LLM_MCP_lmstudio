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
$agentServerPaths = @()
$entryNames = @()
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead((Resolve-Path -LiteralPath $ZipPath).Path)
try {
    foreach ($entry in $zip.Entries) {
        $name = $entry.FullName.Replace('\', '/')
        $entryNames += $name
        foreach ($pattern in $forbiddenPatterns) {
            if ($name -match $pattern) {
                $failures += "Forbidden entry ($pattern): $name"
            }
        }
        if ($name -match '(^|/)data/[^/]+/[^/]+\.uproject$' -and $name -match 'holdout|fixture') {
            $failures += "Fixture uproject in package: $name"
        }
        if ($name -match '/lmstudio-unreal-agent-mcp/src/server\.js$') {
            $agentServerPaths += $name
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

if ($agentServerPaths.Count -ne 1) {
    $failures += "Expected exactly one agent MCP entrypoint in portable package, found $($agentServerPaths.Count): $($agentServerPaths -join ', ')"
}

$requiredEntryPatterns = @(
    '/Unreal58-RAG/lmstudio-context-compactor-plugin/manifest\.json$',
    '/Unreal58-RAG/lmstudio-context-compactor-plugin/package\.json$',
    '/Unreal58-RAG/lmstudio-context-compactor-plugin/src/generator\.ts$',
    '/Unreal58-RAG/scripts/install_context_compactor\.ps1$',
    '/Unreal58-RAG/scripts/Test-ContextCompactorActivation\.ps1$',
    '/Unreal58-RAG/installer/INSTALL-AGENT-MODE\.bat$'
)
foreach ($pattern in $requiredEntryPatterns) {
    if (-not ($entryNames | Where-Object { $_ -match $pattern })) {
        $failures += "Required context-compactor installer entry missing ($pattern)"
    }
}

if ($failures.Count -gt 0) {
    foreach ($item in $failures) {
        Write-Host "[FAIL] $item" -ForegroundColor Red
    }
    throw "Portable package content scan failed with $($failures.Count) issue(s)."
}

Write-Host "[PASS] Portable package content scan: $ZipPath" -ForegroundColor Green
