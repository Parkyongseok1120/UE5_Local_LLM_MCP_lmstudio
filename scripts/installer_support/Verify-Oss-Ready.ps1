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
    if (Test-Path (Join-Path $here "..\..\rag.ps1")) {
        return (Resolve-Path (Join-Path $here "..\..")).Path
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
$gitAvailable = Test-GitAvailable

$forbiddenPathPatterns = @(
    '(^|\\)data\\',
    '(^|\\)node_modules\\',
    '(^|\\)Reports\\',
    '(^|\\)\.pytest_cache\\',
    '(^|\\)data\\baseline\\',
    '(^|\\)data\\wrapper_runs\\',
    '(^|\\)data\\scaffold_runs\\',
    '(?i)local_ai_',
    '(?i)(^|\\)omock_',
    '(?i)_session\.json$',
    '(?i)\.out\.log$',
    '(?i)\.runner\.log$',
    '(?i)stage_campaign_marathon',
    '(?i)supervisor_local_ai',
    '(?i)lmstudio_e2e_driver',
    '(?i)run_omock_'
)

$ignoredLocalFiles = @(
    'PORTABLE_ROOT.txt',
    'tests\test_public_path_hygiene.py',
    'lmstudio-unreal-agent-mcp\config\agent-mcp.json',
    'lmstudio-unreal-agent-mcp\config\lmstudio-mcp-unreal-agent.json'
)

# Cross-platform absolute home paths + personal project markers + credentials.
# /Users/Shared is a system path on macOS and is allowed.
$forbiddenContentPatterns = @(
    @{ Name = 'tvly-api-key'; Regex = 'tvly-' },
    @{ Name = 'win-users-backslash'; Regex = 'C:\\Users\\(?!Public\\)' },
    @{ Name = 'win-users-slash'; Regex = 'C:/Users/(?!Public/)' },
    @{ Name = 'unix-users'; Regex = '(?<![A-Za-z0-9_])/Users/(?!Shared/)[A-Za-z]' },
    @{ Name = 'unix-home'; Regex = '(?<![A-Za-z0-9_])/home/[A-Za-z]' },
    @{ Name = 'O-Mock'; Regex = '\bO-Mock\b' },
    @{ Name = 'Project_MJS'; Regex = '\bProject_MJS\b' },
    @{ Name = 'credential-assignment'; Regex = '(?i)(api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*["''][^"'']{8,}' },
    @{ Name = 'epic-ue-path-backslash'; Regex = 'C:\\Program Files\\Epic Games\\UE_' },
    @{ Name = 'epic-ue-path-slash'; Regex = 'C:/Program Files/Epic Games/UE_' },
    @{ Name = 'SoulslikePrototype'; Regex = 'SoulslikePrototype' },
    @{ Name = 'portable-d-drive'; Regex = 'D:[\\/]Unreal58-RAG-Portable' }
)

# Scan all tracked text-like files, including .log/.json/.txt/.md.
$scanExtensions = @(
    '.json', '.md', '.py', '.ps1', '.yaml', '.yml', '.js', '.txt', '.bat', '.sh',
    '.log', '.csv', '.xml', '.toml', '.ini', '.cfg', '.command'
)

$scanFiles = Get-ScanFiles $root
foreach ($file in $scanFiles) {
    $rel = $file.Substring($root.Length).TrimStart('\', '/')
    $relNormalized = $rel.Replace('/', '\')
    $relPosix = $rel.Replace('\', '/')
    if ($rel -match '(?i)Verify-Oss-Ready\.ps1$') {
        continue
    }
    if ($ignoredLocalFiles -contains $relNormalized) {
        continue
    }
    $skipGeneratedPath = $false
    foreach ($pattern in $forbiddenPathPatterns) {
        if ($relNormalized -match $pattern -or $relPosix -match $pattern) {
            if ($gitAvailable) {
                Fail "forbidden tracked/generated path in scan set: $rel"
            }
            $skipGeneratedPath = $true
            break
        }
    }
    if ($skipGeneratedPath) {
        continue
    }
    if ($rel -match '\.sqlite$') {
        Fail "sqlite file in scan set: $rel"
    }
    if ($rel -eq 'config\workspace.json' -or $rel -eq 'config/agent-mcp.json') {
        try {
            $cfgText = Get-Content -LiteralPath $file -Raw -Encoding UTF8
            $cfg = $cfgText | ConvertFrom-Json
            if ($cfg.rootPath -or $cfg.defaultEngineRoot) {
                Fail "tracked workspace config must not contain machine-specific rootPath/defaultEngineRoot: $rel"
            }
        }
        catch {
            Fail "tracked workspace config is not valid JSON: $rel"
        }
    }
    if ($rel -match '(?i)^lmstudio-unreal-agent-mcp/config/agent-mcp\.json$') {
        Fail "live agent config should not be tracked: $rel"
    }

    $ext = [System.IO.Path]::GetExtension($file).ToLowerInvariant()
    $scanByName = $false
    if (-not $ext) {
        # Extensionless text files such as LICENSE / INSTALL helpers.
        $scanByName = $true
    }
    if ($ext -in $scanExtensions -or $scanByName) {
        try {
            $text = Get-Content -LiteralPath $file -Raw -Encoding UTF8 -ErrorAction Stop
        }
        catch {
            continue
        }
        foreach ($entry in $forbiddenContentPatterns) {
            $pattern = $entry.Regex
            $name = $entry.Name
            if ($text -notmatch $pattern) {
                continue
            }
            if ($name -in @('win-users-backslash', 'win-users-slash', 'unix-users', 'unix-home') -and
                $rel -match '(?i)(SECURITY\.md|Verify-Oss-Ready\.ps1|test_public_path_hygiene\.py|workspace_paths\.py)$') {
                continue
            }
            if ($name -in @('win-users-backslash', 'win-users-slash') -and
                $rel -match '(?i)(README.*\.md|CONTRIBUTING\.md|README-PORTABLE\.md|PORTABLE-INSTALL\.md)$') {
                if ($text -match '(?i)(avoid|do not|must not|never|example|<name>|<username>|YOUR_NAME|placeholder)') {
                    continue
                }
            }
            # Historical docs may mention Project_MJS as a past evaluation target.
            if ($name -eq 'Project_MJS' -and $relPosix -match '(?i)^docs/') {
                continue
            }
            # Synthetic fixture names inside unit tests.
            if ($name -eq 'Project_MJS' -and (
                    $relPosix -match '(?i)(^|/)tests?(/|$)' -or
                    $relPosix -match '\.test\.js$' -or
                    $relPosix -match '(?i)test_'
                )) {
                continue
            }
            # Synthetic placeholder homes used in unit tests.
            if ($name -eq 'unix-users' -and $text -match '/Users/example/') {
                continue
            }
            # Scanner / builder source that constructs path regexes from parts still may
            # mention path fragments in comments; allow only self-check scripts.
            if ($name -in @('win-users-backslash', 'win-users-slash', 'unix-users', 'unix-home') -and
                $relPosix -match '(?i)(validate_holdout_cases\.py|build_integrated_package\.py)$') {
                continue
            }
            Fail "forbidden content '$name' in $rel"
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
    'scripts\workspace_paths.py',
    'lmstudio-unreal-agent-mcp\src\server.js'
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
