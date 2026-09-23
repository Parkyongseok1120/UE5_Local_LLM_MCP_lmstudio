param([Parameter(Mandatory=$true)][string]$Gate)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$pluginRoot = Join-Path $repoRoot 'lmstudio-context-compactor-plugin'
Push-Location $pluginRoot
try {
  npm run build *> (Join-Path $PSScriptRoot "$Gate-build.log")
  $buildExit = $LASTEXITCODE
  npm test *> (Join-Path $PSScriptRoot "$Gate-test.log")
  $testExit = $LASTEXITCODE
  node --test test/refactor-policies.test.cjs *> (Join-Path $PSScriptRoot "$Gate-focused.log")
  $focusedExit = $LASTEXITCODE
  @{ gate=$Gate; build=$buildExit; test=$testExit; focused=$focusedExit } | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $PSScriptRoot "$Gate-exits.json")
  Get-Content (Join-Path $PSScriptRoot "$Gate-test.log") -Tail 9
  Get-Content (Join-Path $PSScriptRoot "$Gate-focused.log") -Tail 14
  if ($buildExit -ne 0 -or $testExit -ne 0 -or $focusedExit -ne 0) { exit 1 }
} finally { Pop-Location }
