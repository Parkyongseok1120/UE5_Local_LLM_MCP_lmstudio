param(
    [string]$StateRoot = "",
    [switch]$RequireCompaction,
    [switch]$Json
)

$ErrorActionPreference = "Stop"

$root = if ($StateRoot) {
    $StateRoot
}
else {
    Join-Path $HOME ".lmstudio\unreal-context-compactor\sessions"
}

function Write-Result([hashtable]$Result, [int]$ExitCode) {
    if ($Json) {
        $Result | ConvertTo-Json -Depth 8 -Compress
    }
    elseif ($Result.active) {
        Write-Host "[PASS] Context compactor proxy activation evidence found." -ForegroundColor Green
        Write-Host "Target model: $($Result.targetModel)"
        Write-Host "Latest measurement: $($Result.measuredAt)"
        Write-Host "Input/context tokens: $($Result.inputTokens)/$($Result.contextLength)"
        if ($Result.compactionApplied) {
            Write-Host "Latest applied compaction: $($Result.compactedAt)"
            Write-Host "Post-compaction remaining tokens: $($Result.postRemainingTokens)"
        }
        else {
            Write-Host "Compaction applied: not yet (the proxy is active, but its threshold has not been crossed)."
        }
    }
    else {
        Write-Host "[FAIL] No context compactor proxy activation evidence was found." -ForegroundColor Red
        Write-Host "Select 'unreal-context-compactor' in this chat's model dropdown, then send one message."
        Write-Host "Selecting the underlying Qwen/GPT model directly bypasses the installed proxy."
    }
    exit $ExitCode
}

if (-not (Test-Path -LiteralPath $root)) {
    Write-Result @{
        active = $false
        reason = "state_root_missing"
        stateRoot = $root
    } 2
}

$events = New-Object System.Collections.Generic.List[object]
Get-ChildItem -LiteralPath $root -Recurse -File -Filter "events*.jsonl" -ErrorAction SilentlyContinue | ForEach-Object {
    Get-Content -LiteralPath $_.FullName -Encoding UTF8 | ForEach-Object {
        if ([string]::IsNullOrWhiteSpace($_)) { return }
        try {
            $events.Add(($_ | ConvertFrom-Json))
        }
        catch {
            # Ignore a partially written final telemetry line; earlier complete evidence remains valid.
        }
    }
}

$measurement = $events |
    Where-Object { $_.type -eq "context_measurement" -and $_.proxyActive -eq $true } |
    Sort-Object { [datetime]$_.at } -Descending |
    Select-Object -First 1

if (-not $measurement) {
    Write-Result @{
        active = $false
        reason = "no_proxy_measurement"
        stateRoot = $root
    } 2
}

$compaction = $events |
    Where-Object { $_.type -eq "compaction_decision" -and $_.applied -eq $true } |
    Sort-Object { [datetime]$_.at } -Descending |
    Select-Object -First 1

$result = @{
    active = $true
    stateRoot = $root
    targetModel = [string]$measurement.targetModel
    measuredAt = [string]$measurement.at
    inputTokens = [long]$measurement.inputTokens
    contextLength = [long]$measurement.contextLength
    action = [string]$measurement.decision.action
    compactionApplied = $null -ne $compaction
    compactedAt = if ($compaction) { [string]$compaction.at } else { $null }
    postRemainingTokens = if ($compaction) { [long]$compaction.postRemainingTokens } else { $null }
}

if ($RequireCompaction -and -not $compaction) {
    if ($Json) {
        $result.reason = "no_applied_compaction"
        $result | ConvertTo-Json -Depth 8 -Compress
    }
    else {
        Write-Host "[FAIL] The proxy is active, but no applied compaction is recorded yet." -ForegroundColor Red
        Write-Host "Continue the proxy chat until its threshold is crossed, then retry."
    }
    exit 3
}

Write-Result $result 0
