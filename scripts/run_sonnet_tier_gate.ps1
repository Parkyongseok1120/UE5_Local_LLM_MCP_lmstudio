param(
    [switch]$Live
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Push-Location $root
try {
    $steps = @(
        @{ name = "doctor"; cmd = { .\rag.ps1 doctor } },
        @{ name = "bench-mcp"; cmd = { .\rag.ps1 bench-mcp } },
        @{ name = "eval-genre"; cmd = { .\rag.ps1 eval-genre } },
        @{ name = "eval-refactor"; cmd = { .\rag.ps1 eval-refactor } },
        @{ name = "eval-unreal-programming"; cmd = { .\rag.ps1 eval-unreal-programming } },
        @{ name = "test-unreal-readiness"; cmd = { .\rag.ps1 test-unreal-readiness } },
        @{ name = "eval-reasoning"; cmd = { .\rag.ps1 eval-reasoning } },
        @{ name = "eval-agent-harness"; cmd = { .\rag.ps1 eval-agent-harness } },
        @{ name = "bench-token-budget"; cmd = { .\rag.ps1 bench-token-budget } },
        @{ name = "eval-project-review"; cmd = { .\rag.ps1 eval-project-review } }
    )

    if ($Live) {
        $steps += @(
            @{ name = "eval-soulslike-live"; cmd = { .\rag.ps1 eval-soulslike-live -Live } },
            @{ name = "eval-project-review-live"; cmd = { .\rag.ps1 eval-project-review -Live } }
        )
    }

    $results = @()
    foreach ($step in $steps) {
        Write-Host "`n=== $($step.name) ===" -ForegroundColor Cyan
        & $step.cmd
        $code = $LASTEXITCODE
        $results += [pscustomobject]@{ step = $step.name; exitCode = $code; pass = ($code -eq 0) }
        if ($code -ne 0 -and $step.name -in @("doctor", "eval-refactor", "test-unreal-readiness")) {
            Write-Host "FAIL-FAST: $($step.name)" -ForegroundColor Red
            break
        }
    }

    $out = Join-Path $root "data\baseline\sonnet-tier-latest.json"
    $payload = @{
        generatedAt = (Get-Date).ToUniversalTime().ToString("o")
        tier          = if ($Live) { "B-live" } else { "A-static" }
        results       = $results
        passCount     = @($results | Where-Object { $_.pass }).Count
        total         = $results.Count
    }
    $payload | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $out -Encoding UTF8
    Write-Host "`nWrote $out"
    $failed = @($results | Where-Object { -not $_.pass })
    if ($failed.Count -gt 0) {
        Write-Host "Sonnet-tier gate: $($failed.Count) step(s) failed." -ForegroundColor Yellow
        exit 1
    }
    Write-Host "Sonnet-tier gate: ALL PASS" -ForegroundColor Green
}
finally {
    Pop-Location
}
