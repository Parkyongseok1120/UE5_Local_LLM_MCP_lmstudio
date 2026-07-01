param(
    [string]$ProjectFile = "",
    [string]$WorkspaceRoot = "",
    [switch]$Force,
    [switch]$NoUpdate,
    [switch]$NoBuild,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$workspace = if ($WorkspaceRoot) {
    (Resolve-Path -LiteralPath $WorkspaceRoot).Path
}
else {
    (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
}

$py = & {
    $bundled = Join-Path $HOME ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path -LiteralPath $bundled) { return $bundled }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notlike "*\WindowsApps\*") { return $cmd.Source }
    throw "Python not found. Install Python 3.10+ or run from Codex with bundled Python."
}

$argsList = @(
    (Join-Path $workspace "scripts\install_editor_graph_plugin.py"),
    "--workspace", $workspace
)
if ($ProjectFile) {
    $argsList += @("--project", $ProjectFile)
}
if ($Force) {
    $argsList += "--force"
}
if (-not $NoUpdate) {
    $argsList += "--update"
}
if (-not $NoBuild) {
    $argsList += "--build"
}
if ($DryRun) {
    $argsList += "--dry-run"
}

& $py @argsList
exit $LASTEXITCODE
