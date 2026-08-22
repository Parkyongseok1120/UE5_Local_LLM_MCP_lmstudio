param(
    [switch]$RequireRuntime,
    [switch]$Json
)

$ErrorActionPreference = "Stop"

function Write-StatusFailure([string]$Reason, [string]$Message) {
    if ($Json) {
        @{
            active = $false
            reason = $Reason
            error = $Message
        } | ConvertTo-Json -Depth 4 -Compress
    }
    else {
        Write-Host ("[FAIL] " + $Message) -ForegroundColor Red
    }
    exit 4
}

$nodeChecker = Join-Path (Split-Path -Parent $PSScriptRoot) "lmstudio-context-compactor-plugin\scripts\status.cjs"
$nodeCommand = Get-Command node -ErrorAction SilentlyContinue
if (-not $nodeCommand) {
    Write-StatusFailure "node_unavailable" "Node.js 20+ is required for the context-compactor status check."
}
if (-not (Test-Path -LiteralPath $nodeChecker)) {
    Write-StatusFailure "status_checker_missing" "Missing context-compactor status checker: $nodeChecker"
}

$nodeArgs = @($nodeChecker)
if ($RequireRuntime) { $nodeArgs += "--require-runtime" }
if ($Json) { $nodeArgs += "--json" }
& $nodeCommand.Source @nodeArgs
exit $LASTEXITCODE
