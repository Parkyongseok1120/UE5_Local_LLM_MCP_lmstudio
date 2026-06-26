param(
    [string[]]$ProjectFile = @(),
    [string[]]$ProjectsRoot = @(
        (Join-Path $env:USERPROFILE "Documents\Github"),
        (Join-Path $env:USERPROFILE "Documents\Unreal Projects")
    ),
    [string]$EngineRoot = "C:\Program Files\Epic Games\UE_5.7",
    [switch]$InstallExtensions,
    [switch]$SkipProjectGeneration,
    [switch]$SkipGlobalSettings
)

$ErrorActionPreference = "Stop"

function Get-CodeCli {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\Microsoft VS Code\bin\code.cmd",
        "$env:ProgramFiles\Microsoft VS Code\bin\code.cmd",
        "${env:ProgramFiles(x86)}\Microsoft VS Code\bin\code.cmd"
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }
    return ""
}

function Get-LatestMsvcCompiler {
    $root = Join-Path $env:ProgramFiles "Microsoft Visual Studio"
    if (-not (Test-Path $root)) {
        return ""
    }
    $compiler = Get-ChildItem -Path $root -Recurse -Filter cl.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -like "*\bin\Hostx64\x64\cl.exe" } |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    if ($compiler) {
        return $compiler.FullName
    }
    return ""
}

function Read-JsonFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        return [ordered]@{}
    }
    $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    if (-not $raw.Trim()) {
        return [ordered]@{}
    }
    return $raw | ConvertFrom-Json
}

function Set-JsonProperty {
    param(
        [object]$Object,
        [string]$Name,
        [object]$Value
    )
    $property = $Object.PSObject.Properties[$Name]
    if ($property) {
        $property.Value = $Value
    }
    else {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

function Write-JsonFile {
    param(
        [string]$Path,
        [object]$Value
    )
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    if (Test-Path $Path) {
        Copy-Item -LiteralPath $Path -Destination "$Path.codex-backup" -Force
    }
    $json = ($Value | ConvertTo-Json -Depth 20) + [Environment]::NewLine
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $json, $encoding)
}

function Get-UprojectInfo {
    param([string]$Path)
    $json = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    $name = [System.IO.Path]::GetFileNameWithoutExtension($Path)
    if ($json.Modules -and $json.Modules.Count -gt 0 -and $json.Modules[0].Name) {
        $name = [string]$json.Modules[0].Name
    }
    $association = ""
    if ($json.EngineAssociation) {
        $association = [string]$json.EngineAssociation
    }
    [pscustomobject]@{
        Name = $name
        EngineAssociation = $association
    }
}

function Merge-UnrealVsCodeSettings {
    param(
        [string]$SettingsPath,
        [string]$CompilerPath,
        [string]$CompileCommandsPath
    )
    $settings = Read-JsonFile $SettingsPath
    Set-JsonProperty $settings "C_Cpp.default.compilerPath" $CompilerPath
    Set-JsonProperty $settings "C_Cpp.default.cppStandard" "c++20"
    Set-JsonProperty $settings "C_Cpp.default.cStandard" "c17"
    Set-JsonProperty $settings "C_Cpp.default.intelliSenseMode" "msvc-x64"
    if ($CompileCommandsPath) {
        Set-JsonProperty $settings "C_Cpp.default.compileCommands" $CompileCommandsPath
    }
    Set-JsonProperty $settings "C_Cpp.errorSquiggles" "enabled"
    Set-JsonProperty $settings "C_Cpp.intelliSenseEngine" "default"
    Set-JsonProperty $settings "C_Cpp.workspaceParsingPriority" "medium"
    Set-JsonProperty $settings "C_Cpp.autocompleteAddParentheses" $true
    Set-JsonProperty $settings "C_Cpp.codeAnalysis.clangTidy.enabled" $false
    Set-JsonProperty $settings "cmake.configureOnOpen" $false
    Set-JsonProperty $settings "dotnet.completion.showCompletionItemsFromUnimportedNamespaces" $true

    Set-JsonProperty $settings "files.associations" ([ordered]@{
        "*.Build.cs" = "csharp"
        "*.Target.cs" = "csharp"
        "*.uproject" = "jsonc"
        "*.uplugin" = "jsonc"
        "*.ini" = "ini"
        "*.usf" = "hlsl"
        "*.ush" = "hlsl"
    })
    Set-JsonProperty $settings "files.exclude" ([ordered]@{
        "**/.vs" = $true
        "**/Binaries" = $true
        "**/DerivedDataCache" = $true
        "**/Intermediate" = $true
        "**/Saved" = $true
    })
    Set-JsonProperty $settings "search.exclude" ([ordered]@{
        "**/.vs" = $true
        "**/Binaries" = $true
        "**/DerivedDataCache" = $true
        "**/Intermediate" = $true
        "**/Saved" = $true
    })
    Set-JsonProperty $settings "files.watcherExclude" ([ordered]@{
        "**/.vs/**" = $true
        "**/Binaries/**" = $true
        "**/DerivedDataCache/**" = $true
        "**/Intermediate/**" = $true
        "**/Saved/**" = $true
    })
    Write-JsonFile $SettingsPath $settings
}

function Write-ExtensionsJson {
    param([string]$Path)
    Write-JsonFile $Path ([ordered]@{
        recommendations = @(
            "ms-vscode.cpptools",
            "ms-vscode.cmake-tools",
            "ms-dotnettools.csharp",
            "EditorConfig.EditorConfig"
        )
    })
}

function Write-TasksJson {
    param(
        [string]$Path,
        [string]$EngineRoot,
        [string]$ProjectFile,
        [string]$TargetName
    )
    $buildBat = Join-Path $EngineRoot "Engine\Build\BatchFiles\Build.bat"
    $cleanBat = Join-Path $EngineRoot "Engine\Build\BatchFiles\Clean.bat"
    $ubt = Join-Path $EngineRoot "Engine\Binaries\DotNET\UnrealBuildTool\UnrealBuildTool.exe"
    $tasks = [ordered]@{
        version = "2.0.0"
        tasks = @(
            [ordered]@{
                label = "UE: Generate Project Files"
                type = "shell"
                command = $ubt
                args = @("-Mode=GenerateProjectFiles", "-Project=$ProjectFile", "-Game", "-Engine", "-VSCode", "-Progress")
                problemMatcher = @()
                options = [ordered]@{ cwd = Split-Path -Parent $ProjectFile }
            },
            [ordered]@{
                label = "UE: Build Editor Development"
                group = "build"
                type = "shell"
                command = $buildBat
                args = @($TargetName, "Win64", "Development", $ProjectFile, "-waitmutex")
                problemMatcher = "`$msCompile"
                options = [ordered]@{ cwd = $EngineRoot }
            },
            [ordered]@{
                label = "UE: Build Editor DebugGame"
                group = "build"
                type = "shell"
                command = $buildBat
                args = @($TargetName, "Win64", "DebugGame", $ProjectFile, "-waitmutex")
                problemMatcher = "`$msCompile"
                options = [ordered]@{ cwd = $EngineRoot }
            },
            [ordered]@{
                label = "UE: Clean Editor Development"
                type = "shell"
                command = $cleanBat
                args = @($TargetName, "Win64", "Development", $ProjectFile, "-waitmutex")
                problemMatcher = "`$msCompile"
                options = [ordered]@{ cwd = $EngineRoot }
            }
        )
    }
    Write-JsonFile $Path $tasks
}

function Write-LaunchJson {
    param(
        [string]$Path,
        [string]$EngineRoot,
        [string]$ProjectFile
    )
    $editorExe = Join-Path $EngineRoot "Engine\Binaries\Win64\UnrealEditor.exe"
    $natvis = Join-Path $EngineRoot "Engine\Extras\VisualStudioDebugging\Unreal.natvis"
    $launch = [ordered]@{
        version = "0.2.0"
        configurations = @(
            [ordered]@{
                name = "UE: Launch Editor (Development)"
                type = "cppvsdbg"
                request = "launch"
                program = $editorExe
                args = @($ProjectFile)
                cwd = Split-Path -Parent $ProjectFile
                stopAtEntry = $false
                console = "integratedTerminal"
                preLaunchTask = "UE: Build Editor Development"
                visualizerFile = $natvis
                sourceFileMap = [ordered]@{
                    "D:\build\++UE5\Sync" = $EngineRoot
                }
            },
            [ordered]@{
                name = "UE: Attach to UnrealEditor"
                type = "cppvsdbg"
                request = "attach"
                processId = '${command:pickProcess}'
                visualizerFile = $natvis
                sourceFileMap = [ordered]@{
                    "D:\build\++UE5\Sync" = $EngineRoot
                }
            }
        )
    }
    Write-JsonFile $Path $launch
}

function Set-UnrealGlobalSettings {
    param([string]$EngineRoot)
    $buildConfigDir = Join-Path $env:APPDATA "Unreal Engine\UnrealBuildTool"
    New-Item -ItemType Directory -Force -Path $buildConfigDir | Out-Null
    $buildConfig = Join-Path $buildConfigDir "BuildConfiguration.xml"
    if (Test-Path $buildConfig) {
        Copy-Item -LiteralPath $buildConfig -Destination "$buildConfig.codex-backup" -Force
    }
    @"
<?xml version="1.0" encoding="utf-8"?>
<Configuration xmlns="https://www.unrealengine.com/BuildConfiguration">
  <ProjectFileGenerator>
    <Format>VisualStudioCode</Format>
  </ProjectFileGenerator>
</Configuration>
"@ | Set-Content -LiteralPath $buildConfig -Encoding UTF8

    $version = Split-Path -Leaf $EngineRoot
    $version = $version -replace "^UE_", ""
    $editorSettings = Join-Path $env:LOCALAPPDATA "UnrealEngine\$version\Saved\Config\WindowsEditor\EditorSettings.ini"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $editorSettings) | Out-Null
    if (Test-Path $editorSettings) {
        Copy-Item -LiteralPath $editorSettings -Destination "$editorSettings.codex-backup" -Force
        $text = Get-Content -LiteralPath $editorSettings -Raw -Encoding UTF8
    }
    else {
        $text = ""
    }
    if ($text -match '(?m)^\[/Script/SourceCodeAccess\.SourceCodeAccessSettings\]') {
        if ($text -match '(?m)^PreferredAccessor=') {
            $text = [regex]::Replace($text, '(?m)^PreferredAccessor=.*$', 'PreferredAccessor=VisualStudioCode')
        }
        else {
            $text = [regex]::Replace($text, '(?m)^\[/Script/SourceCodeAccess\.SourceCodeAccessSettings\]$', "[/Script/SourceCodeAccess.SourceCodeAccessSettings]`r`nPreferredAccessor=VisualStudioCode")
        }
    }
    else {
        $text = $text.TrimEnd() + "`r`n`r`n[/Script/SourceCodeAccess.SourceCodeAccessSettings]`r`nPreferredAccessor=VisualStudioCode`r`n"
    }
    Set-Content -LiteralPath $editorSettings -Value $text -Encoding UTF8
}

function Find-Projects {
    param(
        [string[]]$ProjectFile,
        [string[]]$ProjectsRoot
    )
    $paths = @()
    foreach ($project in $ProjectFile) {
        if (Test-Path $project) {
            $paths += (Resolve-Path $project).Path
        }
    }
    if ($paths.Count -eq 0) {
        foreach ($root in $ProjectsRoot) {
            if (Test-Path $root) {
                $paths += Get-ChildItem -Path $root -Recurse -Filter *.uproject -ErrorAction SilentlyContinue |
                    Where-Object { $_.FullName -notlike "*\Intermediate\*" -and $_.FullName -notlike "*\Saved\*" -and $_.FullName -notlike "*\text_snapshot\*" } |
                    ForEach-Object { $_.FullName }
            }
        }
    }
    $paths | Sort-Object -Unique
}

$ubt = Join-Path $EngineRoot "Engine\Binaries\DotNET\UnrealBuildTool\UnrealBuildTool.exe"
if (-not (Test-Path $ubt)) {
    throw "UnrealBuildTool not found: $ubt"
}

$compilerPath = Get-LatestMsvcCompiler
if (-not $compilerPath) {
    throw "MSVC cl.exe was not found. Install Visual Studio 2022 C++ game/native workload."
}

if ($InstallExtensions) {
    $codeCli = Get-CodeCli
    if (-not $codeCli) {
        throw "VSCode code.cmd was not found."
    }
    foreach ($extension in @("ms-vscode.cpptools", "ms-vscode.cmake-tools", "ms-dotnettools.csharp", "EditorConfig.EditorConfig")) {
        & $codeCli --install-extension $extension --force
    }
}

if (-not $SkipGlobalSettings) {
    Set-UnrealGlobalSettings -EngineRoot $EngineRoot
    $userSettings = Join-Path $env:APPDATA "Code\User\settings.json"
    Merge-UnrealVsCodeSettings -SettingsPath $userSettings -CompilerPath $compilerPath -CompileCommandsPath ""
}

$projects = Find-Projects -ProjectFile $ProjectFile -ProjectsRoot $ProjectsRoot
if ($projects.Count -eq 0) {
    Write-Host "No .uproject files found."
    exit 0
}

foreach ($projectPath in $projects) {
    $projectDir = Split-Path -Parent $projectPath
    $info = Get-UprojectInfo -Path $projectPath
    $targetName = "$($info.Name)Editor"
    Write-Host "Configuring VSCode for $projectPath"

    if (-not (Test-Path (Join-Path $projectDir "Source"))) {
        Write-Warning "Skipping $projectPath because it has no Source folder. Blueprint-only projects do not need C++ IntelliSense project files."
        continue
    }

    if (-not $SkipProjectGeneration) {
        & $ubt -Mode=GenerateProjectFiles "-Project=$projectPath" -Game -Engine -VSCode -Progress
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "GenerateProjectFiles failed for $projectPath. Skipping project-specific VSCode files. Blueprint-only projects commonly have no Source folder."
            continue
        }
    }

    $vscodeDir = Join-Path $projectDir ".vscode"
    New-Item -ItemType Directory -Force -Path $vscodeDir | Out-Null

    $projectStem = [System.IO.Path]::GetFileNameWithoutExtension($projectPath)
    $compileCommandCandidates = @(
        (Join-Path $vscodeDir "compileCommands_$projectStem.json"),
        (Join-Path $vscodeDir "compileCommands_$($info.Name).json")
    )
    $compileCommands = ""
    foreach ($candidate in $compileCommandCandidates) {
        if (Test-Path $candidate) {
            $compileCommands = $candidate
            break
        }
    }
    if (-not $compileCommands) {
        $fallback = Get-ChildItem -Path $vscodeDir -Filter "compileCommands_*.json" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($fallback) {
            $compileCommands = $fallback.FullName
        }
    }

    $settingsCompileCommands = ""
    if ($compileCommands) {
        $settingsCompileCommands = $compileCommands
    }

    Merge-UnrealVsCodeSettings -SettingsPath (Join-Path $vscodeDir "settings.json") -CompilerPath $compilerPath -CompileCommandsPath $settingsCompileCommands
    Write-ExtensionsJson -Path (Join-Path $vscodeDir "extensions.json")
    Write-TasksJson -Path (Join-Path $vscodeDir "tasks.json") -EngineRoot $EngineRoot -ProjectFile $projectPath -TargetName $targetName
    Write-LaunchJson -Path (Join-Path $vscodeDir "launch.json") -EngineRoot $EngineRoot -ProjectFile $projectPath
}

Write-Host "Done. Open the generated .code-workspace file when possible; folder mode also has .vscode tasks and launch configs."
