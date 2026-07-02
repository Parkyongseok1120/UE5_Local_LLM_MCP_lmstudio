# Rename Unreal58-RAG workspace to Unreal58-RAG (UE 5.8 canonical naming).
param(
    [switch]$SkipPortableBackup
)

$ErrorActionPreference = "Stop"
$lmstudio = Join-Path $HOME ".lmstudio"
$oldRoot = Join-Path $lmstudio "Unreal58-RAG"
$newRoot = Join-Path $lmstudio "Unreal58-RAG"
$oldData = Join-Path $oldRoot "data\unreal58"
$newData = Join-Path $newRoot "data\unreal58"

$replacePairs = @(
    @("UNREAL58_PORTABLE_ROOT", "UNREAL58_PORTABLE_ROOT"),
    @("UNREAL58_ROOT", "UNREAL58_ROOT"),
    @("Unreal58-RAG-Portable", "Unreal58-RAG-Portable"),
    @("Unreal58-RAG", "Unreal58-RAG"),
    @("data\unreal58", "data\unreal58"),
    @("data/unreal58", "data/unreal58")
)

function Update-TextFileReplacements {
    param([string[]]$Paths)
    foreach ($path in $Paths) {
        if (-not (Test-Path -LiteralPath $path)) { continue }
        $raw = [System.IO.File]::ReadAllText($path)
        $updated = $raw
        foreach ($pair in $replacePairs) {
            $updated = $updated.Replace($pair[0], $pair[1])
        }
        if ($updated -ne $raw) {
            $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
            [System.IO.File]::WriteAllText($path, $updated, $utf8NoBom)
            Write-Host "  updated: $path"
        }
    }
}

function Update-TreeReplacements {
    param(
        [string]$Root,
        [string[]]$Extensions,
        [string[]]$ExcludeDirNames = @("node_modules", ".git", "Intermediate", "Binaries", "LyraStarterGame", "StackOBot", "AdvancedPuzzleConstructor")
    )
    if (-not (Test-Path -LiteralPath $Root)) { return }
    Get-ChildItem -LiteralPath $Root -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {
        $rel = $_.FullName.Substring($Root.Length).TrimStart('\')
        foreach ($skip in $ExcludeDirNames) {
            if ($rel -like "$skip*") { return }
        }
        if ($Extensions -notcontains $_.Extension.ToLower()) { return }
        if ($_.Length -gt 500MB) {
            Write-Host "  skip large: $($_.FullName)"
            return
        }
        Update-TextFileReplacements @($_.FullName)
    }
}

Write-Host "=== Unreal57 -> Unreal58 rename ==="

if (-not (Test-Path -LiteralPath $oldRoot)) {
    if (Test-Path -LiteralPath $newRoot) {
        Write-Host "Already renamed: $newRoot"
    }
    else {
        throw "Source workspace not found: $oldRoot"
    }
}
else {
    if (Test-Path -LiteralPath $newRoot) {
        Write-Host "Target already exists: $newRoot (skipping folder copy/rename)"
    }
    else {
        Write-Host "Copying workspace (rename blocked if folder in use)..."
        & robocopy $oldRoot $newRoot /E /NFL /NDL /NJH /NJS /NC /NS /XD node_modules .git | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "robocopy failed copying workspace" }
        Write-Host "Copied: Unreal58-RAG -> Unreal58-RAG"
    }
}

if (Test-Path -LiteralPath $oldData) {
    Rename-Item -LiteralPath $oldData -NewName "unreal58"
    Write-Host "Renamed data: unreal57 -> unreal58"
}
elseif (Test-Path -LiteralPath $newData) {
    Write-Host "Data already at unreal58"
}
else {
    $legacyData = Join-Path $newRoot "data\unreal58"
    if (Test-Path -LiteralPath $legacyData) {
        Rename-Item -LiteralPath $legacyData -NewName "unreal58"
        Write-Host "Renamed data in copy: unreal57 -> unreal58"
    }
}

$seedOld = Join-Path $newRoot "config\unreal_57_seed_urls.txt"
$seedNew = Join-Path $newRoot "config\unreal_58_seed_urls.txt"
if (Test-Path -LiteralPath $seedOld) {
    Rename-Item -LiteralPath $seedOld -NewName "unreal_58_seed_urls.txt"
}

# workspace.json explicit fields
$wsConfig = Join-Path $newRoot "config\workspace.json"
if (Test-Path $wsConfig) {
    $ws = Get-Content $wsConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    $ws.workspaceName = "Unreal58-RAG"
    $ws.rootPath = $newRoot
    $ws.indexPath = "data/unreal58/rag.sqlite"
    if ($ws.legacyPaths -notcontains $oldRoot) {
        $ws.legacyPaths = @($oldRoot) + @($ws.legacyPaths)
    }
    ($ws | ConvertTo-Json -Depth 10) + "`n" | Set-Content -LiteralPath $wsConfig -Encoding UTF8
    Write-Host "Updated config/workspace.json"
}

Write-Host "Patching text files in workspace..."
Update-TreeReplacements -Root $newRoot -Extensions @(
    ".ps1", ".py", ".json", ".md", ".js", ".yaml", ".yml", ".txt", ".bat", ".jsonl", ".html", ".cs"
)

Write-Host "Patching lmstudio-unreal-agent-mcp..."
Update-TreeReplacements -Root (Join-Path $lmstudio "lmstudio-unreal-agent-mcp") -Extensions @(".ps1", ".py", ".json", ".md", ".js") -ExcludeDirNames @("node_modules")

Write-Host "Patching .lmstudio root configs..."
Update-TextFileReplacements @(
    (Join-Path $lmstudio "mcp.json"),
    (Join-Path $lmstudio ".internal\last-synced-mcp-state.json"),
    (Join-Path $lmstudio "config\unreal-workspace.json"),
    (Join-Path $HOME ".cline\data\settings\cline_mcp_settings.json"),
    (Join-Path $env:APPDATA "Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json"),
    (Join-Path $lmstudio "extensions\plugins\mcp\unreal-rag\mcp-bridge-config.json"),
    (Join-Path $lmstudio "extensions\plugins\mcp\unreal-agent\mcp-bridge-config.json")
)

# workspace_paths.py legacy support
$wp = Join-Path $newRoot "scripts\workspace_paths.py"
if (Test-Path $wp) {
    $py = Get-Content $wp -Raw -Encoding UTF8
    if ($py -notlike '*"Unreal58-RAG"*') {
        $py = $py.Replace(
            'WORKSPACE_DIR_NAMES = ("Unreal58-RAG", "Gemma4 LORA", "UnrealEngine57Dev_RAG")',
            'WORKSPACE_DIR_NAMES = ("Unreal58-RAG", "Unreal58-RAG", "Gemma4 LORA", "UnrealEngine57Dev_RAG")'
        )
        $py = $py.Replace(
            'env_root = os.environ.get("UNREAL58_ROOT", "").strip()',
            'env_root = os.environ.get("UNREAL58_ROOT", os.environ.get("UNREAL58_ROOT", "")).strip()'
        )
        $py = $py.Replace(
            '"""Resolve Unreal58-RAG workspace paths',
            '"""Resolve Unreal58-RAG workspace paths'
        )
        Set-Content -LiteralPath $wp -Value $py -Encoding UTF8
        Write-Host "Updated workspace_paths.py (legacy Unreal58-RAG kept)"
    }
}

Write-Host "Refreshing LM Studio + Cline MCP paths..."
Set-Location -LiteralPath $newRoot
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_lmstudio_mcp.ps1
$patch = Join-Path $lmstudio "scripts\patch_mcp_runtime_paths.ps1"
if (Test-Path $patch) {
    powershell -NoProfile -ExecutionPolicy Bypass -File $patch | Out-Host
}
powershell -NoProfile -ExecutionPolicy Bypass -File .\installer\Install-ClineUnrealMcp.ps1

if (-not $SkipPortableBackup) {
    Write-Host "Building D: portable backup..."
    powershell -NoProfile -ExecutionPolicy Bypass -File .\installer\Build-PortablePackage.ps1 `
        -OutputDir "D:\Unreal58-RAG-Portable" `
        -ZipPath "D:\Unreal58-RAG-Portable.zip"
}

Write-Host "=== Done. Restart LM Studio and Cline. ==="
Write-Host "Workspace: $newRoot"
