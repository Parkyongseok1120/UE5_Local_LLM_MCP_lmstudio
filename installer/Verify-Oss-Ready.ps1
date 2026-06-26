param(
    [string]$RepoRoot = ""
)

$ErrorActionPreference = "Stop"

function Resolve-RepoRoot {
    param([string]$Override)
    if ($Override) {
        return (Resolve-Path $Override).Path
    }
    $here = $PSScriptRoot
    if (Test-Path (Join-Path $here "..\rag.ps1")) {
        return (Resolve-Path (Join-Path $here "..")).Path
    }
    return (Resolve-Path $here).Path
}

function Test-GitAvailable {
    try {
        $null = Get-Command git -ErrorAction Stop
        return $true
    }
    catch {
        return $false
    }
}

function Get-ScanFiles {
    param([string]$Root)
    $files = @()
    if (Test-GitAvailable) {
        Push-Location $Root
        try {
            $candidates = @(
                git ls-files --cached 2>$null
                git ls-files --others --exclude-standard 2>$null
            ) | Where-Object { $_ } | Select-Object -Unique
            foreach ($rel in $candidates) {
                $full = Join-Path $Root $rel
                if (Test-Path $full -PathType Leaf) {
                    $files += $full
                }
            }
        }
        finally {
            Pop-Location
        }
    }
    if ($files.Count -eq 0) {
        $files = Get-ChildItem -Path $Root -Recurse -File -ErrorAction SilentlyContinue |
            Where-Object {
                $_.FullName -notmatch '\\(data|node_modules|\.venv|\.git)\\' -and
                $_.Extension -notin @('.sqlite', '.pyc')
            } |
            Select-Object -ExpandProperty FullName
    }
    return $files
}

$root = Resolve-RepoRoot $RepoRoot
$fail = 0

function Fail([string]$Message) {
    Write-Host "[FAIL] $Message" -ForegroundColor Red
    $script:fail++
}

function Pass([string]$Message) {
    Write-Host "[PASS] $Message" -ForegroundColor Green
}

Write-Host "Verify-Oss-Ready: $root"

$forbiddenPathPatterns = @(
    '\\data\\',
    '\\node_modules\\',
    '\\Reports\\',
    '\\\.pytest_cache\\',
    '\\data\\baseline\\',
    '\\data\\wrapper_runs\\',
    '\\data\\scaffold_runs\\'
)

$forbiddenContentPatterns = @(
    'tvly-',
    'C:\\Users\\',
    'C:/Users/',
    'SoulslikePrototype',
    '\\Unreal Projects\\SoulslikePrototype'
)

$scanFiles = Get-ScanFiles $root
foreach ($file in $scanFiles) {
    $rel = $file.Substring($root.Length).TrimStart('\', '/')
    if ($rel -match '(?i)Verify-Oss-Ready\.ps1$') {
        continue
    }
    foreach ($pattern in $forbiddenPathPatterns) {
        if ($rel -match $pattern) {
            Fail "forbidden path in scan set: $rel"
            break
        }
    }
    if ($rel -match '\.sqlite$') {
        Fail "sqlite file in scan set: $rel"
    }
    if ($rel -eq 'config\workspace.json' -or $rel -eq 'config/agent-mcp.json') {
        Fail "live config should not be tracked: $rel"
    }

    $ext = [System.IO.Path]::GetExtension($file).ToLowerInvariant()
    if ($ext -in @('.json', '.md', '.py', '.ps1', '.yaml', '.yml', '.js', '.txt', '.bat', '.sh')) {
        try {
            $text = Get-Content -Path $file -Raw -ErrorAction Stop
        }
        catch {
            continue
        }
        foreach ($pattern in $forbiddenContentPatterns) {
            if ($text -match [regex]::Escape($pattern) -or $text -match $pattern) {
                if ($pattern -eq 'C:\\Users\\' -and $rel -match '(?i)(SECURITY\.md|Verify-Oss-Ready\.ps1)$') {
                    continue
                }
                if ($pattern -eq 'C:\\Users\\' -and $rel -match 'workspace_paths\.py$') {
                    continue
                }
                Fail "forbidden content '$pattern' in $rel"
            }
        }
    }
}

$required = @(
    'LICENSE',
    'EPIC_NOTICE.md',
    'SECURITY.md',
    'README.md',
    '.gitignore',
    'scripts\rag_doctor.py',
    'scripts\workspace_paths.py'
)
foreach ($item in $required) {
    if (-not (Test-Path (Join-Path $root $item))) {
        Fail "missing required ship file: $item"
    }
    else {
        Pass "required file present: $item"
    }
}

if ($fail -eq 0) {
    Write-Host ""
    Write-Host "push-ready: no OSS blockers detected in scanned files." -ForegroundColor Green
    exit 0
}

Write-Host ""
Write-Host "$fail OSS readiness check(s) failed." -ForegroundColor Red
exit 1
