[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet(
        "doctor",
        "collect-source",
        "collect-projects",
        "collect-symbols",
        "build",
        "build-incremental",
        "set-project",
        "clear-project",
        "refresh"
    )]
    [string]$Command = "doctor",

    [string[]]$Root = @(),
    [string]$Out = "",
    [string]$OutDir = "",
    [string]$CopyTextTo = "",
    [string]$ProjectFile = "",
    [string]$ProjectName = "",
    [ValidateSet("engine", "project")]
    [string]$SymbolScope = "engine",
    [ValidateSet("public", "full")]
    [string]$Tier = "public",
    [ValidateSet("project_source", "editor_metadata", "all")]
    [string]$RefreshScope = "project_source",
    [switch]$Force,
    [switch]$AllowEditorLaunch
)

$ErrorActionPreference = "Stop"

function Resolve-PortableRoot {
    $candidate = $PSScriptRoot
    if (Test-Path -LiteralPath (Join-Path $candidate "scripts/unreal_rag_direct.py") -PathType Leaf) {
        return $candidate
    }
    $parent = Split-Path -Parent $candidate
    if (Test-Path -LiteralPath (Join-Path $parent "scripts/unreal_rag_direct.py") -PathType Leaf) {
        return $parent
    }
    throw "Cannot locate the portable Direct RAG runtime from $PSScriptRoot"
}

function Resolve-PythonLauncher {
    if ($env:PYTHON_EXE) {
        $explicit = Get-Command $env:PYTHON_EXE -ErrorAction SilentlyContinue
        if ($explicit) {
            return @{ Command = $explicit.Source; Prefix = @() }
        }
        if (Test-Path -LiteralPath $env:PYTHON_EXE -PathType Leaf) {
            return @{ Command = (Resolve-Path -LiteralPath $env:PYTHON_EXE).Path; Prefix = @() }
        }
    }

    $bundled = Join-Path $HOME ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe"
    if (Test-Path -LiteralPath $bundled -PathType Leaf) {
        return @{ Command = $bundled; Prefix = @() }
    }
    foreach ($name in @("python3", "python")) {
        $candidate = Get-Command $name -ErrorAction SilentlyContinue
        if ($candidate -and $candidate.Source -notlike "*WindowsApps*") {
            return @{ Command = $candidate.Source; Prefix = @() }
        }
    }
    $py = Get-Command "py" -ErrorAction SilentlyContinue
    if ($py) {
        return @{ Command = $py.Source; Prefix = @("-3") }
    }
    throw "Python 3 was not found. Set PYTHON_EXE or run the integrated installer first."
}

function Require-Values {
    param([object[]]$Values, [string]$Label)
    if (-not $Values -or @($Values).Count -eq 0) {
        throw "$Command requires -$Label."
    }
}

function Invoke-PortablePython {
    param([string]$ScriptName, [string[]]$ScriptArguments = @())
    $scriptPath = Join-Path $script:PortableRoot "scripts/$ScriptName"
    if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
        throw "Portable runtime file is missing: $scriptPath"
    }
    $arguments = @($script:PythonLauncher.Prefix) + @("-B", $scriptPath) + @($ScriptArguments)
    & $script:PythonLauncher.Command @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$ScriptName failed with exit code $LASTEXITCODE."
    }
}

$script:PortableRoot = Resolve-PortableRoot
$script:PythonLauncher = Resolve-PythonLauncher
Push-Location $script:PortableRoot
try {
    switch ($Command) {
        "doctor" {
            $arguments = @()
            if ($Out) { $arguments += @("--index", $Out) }
            Invoke-PortablePython "direct_rag_status.py" $arguments
        }
        "collect-source" {
            Require-Values $Root "Root"
            if (@($Root).Count -ne 1) { throw "collect-source accepts exactly one -Root." }
            $arguments = @("--root", $Root[0])
            if ($Out) { $arguments += @("--out", $Out) }
            Invoke-PortablePython "collect_unreal_source.py" $arguments
        }
        "collect-projects" {
            Require-Values $Root "Root"
            $arguments = @()
            foreach ($value in $Root) { $arguments += @("--root", $value) }
            if ($Out) { $arguments += @("--out", $Out) }
            if ($CopyTextTo) { $arguments += @("--copy-text-to", $CopyTextTo) }
            Invoke-PortablePython "collect_unreal_projects.py" $arguments
        }
        "collect-symbols" {
            Require-Values $Root "Root"
            $arguments = @()
            foreach ($value in $Root) { $arguments += @("--root", $value) }
            $arguments += @("--tier", $Tier, "--scope", $SymbolScope)
            if ($Out) { $arguments += @("--out", $Out) }
            if ($ProjectName) { $arguments += @("--project-name", $ProjectName) }
            Invoke-PortablePython "collect_unreal_symbols.py" $arguments
        }
        "build" {
            $arguments = @("--force")
            if ($OutDir) { $arguments += @("--out-dir", $OutDir) }
            if ($ProjectFile) { $arguments += @("--project", $ProjectFile) }
            Invoke-PortablePython "incremental_build.py" $arguments
        }
        "build-incremental" {
            $arguments = @()
            if ($OutDir) { $arguments += @("--out-dir", $OutDir) }
            if ($Force) { $arguments += "--force" }
            if ($ProjectFile) { $arguments += @("--project", $ProjectFile) }
            Invoke-PortablePython "incremental_build.py" $arguments
        }
        "set-project" {
            if (-not $ProjectFile) { throw "set-project requires -ProjectFile with one exact .uproject path." }
            Invoke-PortablePython "project_controller.py" @("--switch", $ProjectFile)
        }
        "clear-project" {
            Invoke-PortablePython "project_controller.py" @("--clear")
        }
        "refresh" {
            $arguments = @("--scope", $RefreshScope)
            if ($Force) { $arguments += "--force" }
            if ($AllowEditorLaunch) { $arguments += "--allow-editor-launch" }
            Invoke-PortablePython "rag_refresh.py" $arguments
        }
    }
}
finally {
    Pop-Location
}
