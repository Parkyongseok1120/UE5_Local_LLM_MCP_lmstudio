# Start llama-server for Gemma4-12B v2 Agentic (Q6_K + MTP) — OpenAI API for LM Studio.
# Requires llama.cpp b9553 when using MTP draft (see docs/Gemma4_Llama_Server.md).

param(
    [string]$ModelPath = $env:GEMMA4_V2_MODEL,
    [string]$DraftPath = $env:GEMMA4_MTP_DRAFT,
    [string]$LlamaServer = $env:LLAMA_SERVER_EXE,
    [int]$CtxSize = $(if ($env:GEMMA4_CTX_SIZE) { [int]$env:GEMMA4_CTX_SIZE } else { 32768 }),
    [int]$Port = $(if ($env:GEMMA4_PORT) { [int]$env:GEMMA4_PORT } else { 18080 }),
    [switch]$NoDraft
)

$ErrorActionPreference = "Stop"
# Pin note: llama.cpp b9553 (commit 9e3b928fd) — see docs/Gemma4_Llama_Server.md
$LlamaCppMinBuild = "b9553"

if (-not $ModelPath) {
    $ModelPath = "gemma4-v2-Q6_K.gguf"
}
if (-not $DraftPath) {
    $DraftPath = Join-Path "MTP" "gemma-4-12B-it-MTP-Q8_0.gguf"
}
if (-not $LlamaServer) {
    $LlamaServer = "llama-server"
}

if ($CtxSize -lt 24576) {
    throw "GEMMA4_CTX_SIZE must be >= 24576 (got $CtxSize)"
}

if (-not (Test-Path -LiteralPath $ModelPath)) {
    throw "Main model not found: $ModelPath (set GEMMA4_V2_MODEL)"
}

$args = @(
    "-m", (Resolve-Path -LiteralPath $ModelPath).Path,
    "-ngl", "99",
    "-fa", "on",
    "--jinja",
    "--ctx-size", "$CtxSize",
    "--repeat-penalty", "1.1",
    "--host", "0.0.0.0",
    "--port", "$Port"
)

if (-not $NoDraft) {
    if (-not (Test-Path -LiteralPath $DraftPath)) {
        Write-Warning "Draft model not found: $DraftPath — starting without MTP (use -NoDraft to silence)"
    }
    else {
        $args += @(
            "--model-draft", (Resolve-Path -LiteralPath $DraftPath).Path,
            "--spec-type", "draft-mtp",
            "--spec-draft-n-max", "4",
            "-ngld", "99"
        )
    }
}

Write-Host "llama.cpp min build (MTP): $LlamaCppMinBuild"
Write-Host "Starting: $LlamaServer $($args -join ' ')"
& $LlamaServer @args
