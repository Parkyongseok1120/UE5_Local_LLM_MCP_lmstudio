param(
    [string]$RagRoot = "",
    [string]$IndexNamespace = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Install-PathHelpers.ps1")

if (-not $RagRoot) {
    $RagRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

$configPath = Join-Path $RagRoot "config\workspace.json"
if ($IndexNamespace) {
    $cfg = Read-JsonObject $configPath
    $payload = [ordered]@{}
    if ($null -ne $cfg) {
        foreach ($prop in $cfg.PSObject.Properties) {
            $payload[$prop.Name] = $prop.Value
        }
    }
    $payload.indexNamespace = $IndexNamespace
    $payload.indexPath = "data/$IndexNamespace/rag.sqlite"
    Write-JsonUtf8Atomic -Path $configPath -Object $payload | Out-Null
}

$paths = Get-RagDataPaths -RagRoot $RagRoot -NamespaceOverride $IndexNamespace
Write-Output $paths.DataDir
Write-Output $paths.IndexPath
