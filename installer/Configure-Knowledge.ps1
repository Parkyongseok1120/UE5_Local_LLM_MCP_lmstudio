param(
    [string]$WorkspaceRoot = "",
    [string]$EpicGamesRoot = "C:\Program Files\Epic Games",
    [switch]$SkipBuild,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"

function Resolve-WorkspaceRoot {
    param([string]$Override)
    if ($Override) {
        return (Resolve-Path $Override).Path
    }
    $here = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    if (Test-Path (Join-Path $here "rag.ps1")) {
        return $here
    }
    throw "UE5_Local_LLM_MCP_lmstudio root not found (rag.ps1 missing). Pass -WorkspaceRoot."
}

function Find-Python {
    $bundled = Join-Path $HOME ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path $bundled) { return $bundled }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notlike "*\WindowsApps\*") { return $cmd.Source }
    throw "Python 3.10+ not found."
}

function Index-NamespaceFromVersion([string]$Version) {
    $digits = ($Version -replace "[^\d]", "")
    if (-not $digits) { return "unreal58" }
    return "unreal$digits"
}

function Scan-EngineInstalls([string]$Root) {
    if (-not (Test-Path $Root)) {
        return @()
    }
    Get-ChildItem -LiteralPath $Root -Directory -Filter "UE_5.*" -ErrorAction SilentlyContinue |
        ForEach-Object {
            $name = $_.Name
            if ($name -match "UE_5\.(\d+)") {
                [PSCustomObject]@{
                    FolderName = $name
                    Version    = "5.$($Matches[1])"
                    Root       = $_.FullName
                }
            }
        } |
        Sort-Object Version -Descending
}

function Read-JsonObject([string]$Path) {
    if (-not (Test-Path $Path)) { return $null }
    return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function Write-JsonUtf8([string]$Path, $Object) {
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $json = $Object | ConvertTo-Json -Depth 40
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $json + [Environment]::NewLine, $utf8)
}

$ragRoot = Resolve-WorkspaceRoot $WorkspaceRoot
$py = Find-Python
$configPath = Join-Path $ragRoot "config\workspace.json"
$templatePath = Join-Path $ragRoot "config\workspace.json.template"

Write-Host "Configure Knowledge — UE5 Local LLM MCP"
Write-Host "Workspace: $ragRoot"
Write-Host ""

$engines = @(Scan-EngineInstalls $EpicGamesRoot)
if ($engines.Count -eq 0) {
    Write-Warning "No UE_5.* installs found under: $EpicGamesRoot"
    Write-Host "You can set defaultEngineRoot manually in config\workspace.json later."
    $selectedVersion = "5.8"
    $selectedRoot = Join-Path $EpicGamesRoot "UE_5.8"
}
else {
    Write-Host "Detected engine installs:"
    for ($i = 0; $i -lt $engines.Count; $i++) {
        Write-Host ("  [{0}] {1} — {2}" -f ($i + 1), $engines[$i].Version, $engines[$i].Root)
    }
    if ($NonInteractive) {
        $pick = $engines[0]
    }
    else {
        $choice = Read-Host "Select engine [1-$($engines.Count)] (default 1)"
        $idx = 0
        if ($choice -and [int]::TryParse($choice, [ref]$idx)) {
            $idx = [Math]::Max(1, [Math]::Min($engines.Count, $idx)) - 1
        }
        $pick = $engines[$idx]
    }
    $selectedVersion = $pick.Version
    $selectedRoot = $pick.Root
}

$namespace = Index-NamespaceFromVersion $selectedVersion
$indexRel = "data\$namespace\rag.sqlite"

if ($selectedVersion -eq "5.4") {
    Write-Warning "UE 5.4 selected: best-effort only — eval sets are verified for 5.8, not 5.4."
    if (-not $NonInteractive) {
        $confirm = Read-Host "Continue with 5.4 namespace '$namespace'? [y/N]"
        if ($confirm -notmatch '^[Yy]') {
            throw "Aborted by user."
        }
    }
}

$existing = Read-JsonObject $configPath
if ($null -eq $existing) {
    $existing = Read-JsonObject $templatePath
    if ($null -eq $existing) {
        $existing = [ordered]@{}
    }
}

$workspace = [ordered]@{}
foreach ($prop in $existing.PSObject.Properties) {
    $workspace[$prop.Name] = $prop.Value
}
$workspace["rootPath"] = $ragRoot
$workspace["engineVersion"] = $selectedVersion
$workspace["indexNamespace"] = $namespace
$workspace["indexPath"] = ($indexRel -replace "\\", "/")
$workspace["embeddingsPath"] = ("data/$namespace/embeddings" -replace "\\", "/")
$workspace["defaultEngineRoot"] = $selectedRoot
if (-not $workspace.Contains("knowledgeRoots") -or $null -eq $workspace["knowledgeRoots"]) {
    $workspace["knowledgeRoots"] = [ordered]@{
        guidelines       = "RAG_Project_Guidelines"
        gameDesign       = "Game_Design_Docs"
        projectSnapshots = "data/unreal_projects/text_snapshot"
    }
}

Write-JsonUtf8 $configPath $workspace
Write-Host ""
Write-Host "Wrote config\workspace.json"
Write-Host "  engineVersion   : $($workspace.engineVersion)"
Write-Host "  indexNamespace  : $($workspace.indexNamespace)"
Write-Host "  indexPath       : $($workspace.indexPath)"
Write-Host "  defaultEngineRoot: $($workspace.defaultEngineRoot)"
Write-Host ""

$dataDir = Join-Path $ragRoot ("data\" + $namespace)
if (-not (Test-Path $dataDir)) {
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
    Write-Host "Created data directory: $dataDir"
}

if (-not $SkipBuild) {
    Write-Host "Next steps (manual or re-run without -SkipBuild after index exists):"
    Write-Host "  cd $ragRoot"
    Write-Host "  .\rag.ps1 update-engine"
    Write-Host "  .\rag.ps1 doctor"
}
else {
    Write-Host "Skipped build. Run .\rag.ps1 update-engine when ready."
}

Write-Host ""
Write-Host "Done."
