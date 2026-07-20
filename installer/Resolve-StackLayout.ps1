function Resolve-StackLayout {
    param(
        [Alias("PortableRoot")]
        [string]$PortableRootOverride,
        [string]$LmStudioHome = ""
    )

    $resolvedLmStudioHome = if ($LmStudioHome) {
        [System.IO.Path]::GetFullPath($LmStudioHome)
    } else {
        Join-Path $HOME ".lmstudio"
    }

    if ($PortableRootOverride) {
        $portable = (Resolve-Path $PortableRootOverride).Path
        $ragRoot = $null
        foreach ($candidate in @(
                (Join-Path $portable "Unreal58-RAG"),
                $portable
            )) {
            if (Test-Path (Join-Path $candidate "rag.ps1")) {
                $ragRoot = $candidate
                break
            }
        }
        if (-not $ragRoot) {
            throw "Unreal58-RAG/rag.ps1 not found under portable root: $portable"
        }
        $agentRoot = Join-Path $portable "lmstudio-unreal-agent-mcp"
        if (-not (Test-Path (Join-Path $agentRoot "src\server.js"))) {
            throw "lmstudio-unreal-agent-mcp not found under: $portable"
        }
        return [ordered]@{
            Root         = $portable
            RagRoot      = $ragRoot
            AgentRoot    = $agentRoot
            McpToolsRoot = Join-Path $portable "mcp-tools"
            LmStudioHome = $resolvedLmStudioHome
        }
    }

    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

    if ((Test-Path (Join-Path $repoRoot "rag.ps1")) -and
        (Test-Path (Join-Path $repoRoot "lmstudio-unreal-agent-mcp\src\server.js"))) {
        return [ordered]@{
            Root         = $repoRoot
            RagRoot      = $repoRoot
            AgentRoot    = Join-Path $repoRoot "lmstudio-unreal-agent-mcp"
            McpToolsRoot = Join-Path $repoRoot "mcp-tools"
            LmStudioHome = $resolvedLmStudioHome
        }
    }

    if ((Split-Path $repoRoot -Leaf) -eq "Unreal58-RAG") {
        $grand = (Resolve-Path (Join-Path $repoRoot "..")).Path
        $agentRoot = Join-Path $grand "lmstudio-unreal-agent-mcp"
        if (Test-Path (Join-Path $agentRoot "src\server.js")) {
            return [ordered]@{
                Root         = $grand
                RagRoot      = $repoRoot
                AgentRoot    = $agentRoot
                McpToolsRoot = Join-Path $grand "mcp-tools"
                LmStudioHome = $resolvedLmStudioHome
            }
        }
    }

    throw "Could not locate RAG workspace (rag.ps1) and lmstudio-unreal-agent-mcp."
}
