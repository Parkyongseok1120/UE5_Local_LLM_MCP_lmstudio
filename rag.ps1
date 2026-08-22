# Direct RAG command-line entry point.
#
# Keep the repository launcher intentionally tiny: the same bounded command
# surface is used in development checkouts and portable packages.  Model eval,
# planner, task, route, and workflow-controller commands are not runtime duties.

$ErrorActionPreference = "Stop"
$directLauncher = Join-Path $PSScriptRoot "scripts\portable_rag.ps1"
if (-not (Test-Path -LiteralPath $directLauncher -PathType Leaf)) {
    throw "Direct RAG launcher is missing: $directLauncher"
}

& $directLauncher @args
exit $LASTEXITCODE
