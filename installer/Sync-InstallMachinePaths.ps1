param(
    [string]$PortableRoot = "",
    [string]$DocumentsRoot = "",
    [string]$SharedConfigPath = "",
    [string]$EpicGamesRoot = ""
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "Resolve-StackLayout.ps1")
. (Join-Path $PSScriptRoot "Install-PathHelpers.ps1")

$layout = Resolve-StackLayout $PortableRoot
$ragRoot = $layout.RagRoot
$agentRoot = $layout.AgentRoot
$sharedPath = if ($SharedConfigPath) {
    $SharedConfigPath
}
else {
    Join-Path $HOME ".lmstudio\config\unreal-workspace.json"
}

$result = Sync-InstallMachinePaths `
    -RagRoot $ragRoot `
    -AgentRoot $agentRoot `
    -DocumentsRoot $DocumentsRoot `
    -SharedConfigPath $sharedPath `
    -EpicGamesRoot $EpicGamesRoot

Write-Host "Synced machine-local install paths."
Write-Host "  RAG root         : $ragRoot"
Write-Host "  Engine root      : $($result.EngineRoot) ($($result.EngineSource))"
Write-Host "  Engine version   : $($result.EngineVersion)"
Write-Host "  Agent search roots: $($result.AgentConfig.projectSearchRoots.Count)"
