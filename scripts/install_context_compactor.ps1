param(
    [string]$SourcePath = "",
    [string]$LmsExe = "",
    [string]$LmStudioHome = "",
    [switch]$SkipNpm,
    [switch]$SkipTests,
    [switch]$SkipBuild,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"

$sourceCandidate = if ($SourcePath) { $SourcePath } else { Join-Path $PSScriptRoot "..\lmstudio-context-compactor-plugin" }
if (-not (Test-Path -LiteralPath $sourceCandidate)) {
    throw "Context compactor source not found: $sourceCandidate"
}
$source = (Resolve-Path -LiteralPath $sourceCandidate).Path
$lmHome = if ($LmStudioHome) { $LmStudioHome } else { Join-Path $HOME ".lmstudio" }

if ($WhatIf) {
    Write-Host "[WhatIf] Would install LM Studio plugin from: $source" -ForegroundColor Cyan
    Write-Host "Installation file available: Y"
    return
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw "Node.js/npm is required." }
if (-not $LmsExe) {
    $lmsCommand = Get-Command lms -ErrorAction SilentlyContinue
    if ($lmsCommand) {
        $LmsExe = $lmsCommand.Source
    }
    else {
        $bundledLms = Join-Path $lmHome "bin\lms.exe"
        if (Test-Path -LiteralPath $bundledLms) { $LmsExe = $bundledLms }
    }
}
if (-not $LmsExe -or -not (Test-Path -LiteralPath $LmsExe)) {
    throw "LM Studio CLI (lms) is required. Open LM Studio Developer settings once or pass -LmsExe."
}

Push-Location $source
try {
    if ($SkipNpm) {
        if (-not (Test-Path -LiteralPath (Join-Path $source "node_modules\@lmstudio\sdk"))) {
            throw "-SkipNpm was requested but plugin node_modules are missing. Use the Full Portable package or remove -SkipNpm."
        }
    }
    else {
        npm ci --prefer-offline --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw "Plugin dependency installation failed (exit $LASTEXITCODE)" }
    }

    $builtByTests = $false
    if (-not $SkipTests) {
        npm test
        if ($LASTEXITCODE -ne 0) { throw "Plugin unit tests failed (exit $LASTEXITCODE)" }
        $builtByTests = $true
    }
    if (-not $SkipBuild -and -not $builtByTests) {
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "Plugin build failed (exit $LASTEXITCODE)" }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $source "dist\index.js"))) {
        throw "Plugin build output missing: dist\index.js"
    }

    & $LmsExe dev --install -y
    if ($LASTEXITCODE -ne 0) { throw "LM Studio plugin installation failed (exit $LASTEXITCODE)" }
}
finally {
    Pop-Location
}

$sourceManifest = Get-Content -LiteralPath (Join-Path $source "manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$installedManifestPath = Join-Path $lmHome "extensions\plugins\$($sourceManifest.owner)\$($sourceManifest.name)\manifest.json"
if (-not (Test-Path -LiteralPath $installedManifestPath)) {
    throw "LM Studio reported success but installed plugin manifest is missing: $installedManifestPath"
}
$installedManifest = Get-Content -LiteralPath $installedManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([int]$installedManifest.revision -ne [int]$sourceManifest.revision) {
    throw "Installed plugin revision mismatch: expected $($sourceManifest.revision), found $($installedManifest.revision)"
}
$installedRoot = Split-Path -Parent $installedManifestPath
$sourceGenerator = Join-Path $source "dist\generator.js"
$installedGenerator = Join-Path $installedRoot "dist\generator.js"
if (-not (Test-Path -LiteralPath $installedGenerator)) {
    throw "Installed plugin generator is missing: $installedGenerator"
}
if ((Get-FileHash -LiteralPath $sourceGenerator -Algorithm SHA256).Hash -ne
    (Get-FileHash -LiteralPath $installedGenerator -Algorithm SHA256).Hash) {
    throw "Installed plugin generator hash does not match the tested build."
}

Write-Host "Installation file available: Y"
Write-Host "Installed codex/unreal-context-compactor revision $($installedManifest.revision) through the LM Studio plugin manager."
Write-Host ""
Write-Host "IMPORTANT: installation does not change the model already selected in an existing chat." -ForegroundColor Yellow
Write-Host "Select 'unreal-context-compactor' in this chat's model dropdown." -ForegroundColor Yellow
Write-Host "Do not select the underlying Qwen/GPT model directly; that bypasses compaction." -ForegroundColor Yellow
Write-Host "targetModel is optional when exactly one underlying LLM is loaded."
Write-Host "After sending one proxy message, run: npm --prefix `"$source`" run status"
