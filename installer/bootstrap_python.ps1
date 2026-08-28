[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$InstallerArguments = @()
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$MaxManifestBytes = 1MB
$MaxUvArchiveBytes = 128MB

function Write-SeedMessage {
    param([string]$Message)
    [Console]::Error.WriteLine($Message)
}

function Get-InstallerStateHome {
    param([string[]]$Arguments)

    $rawStateHome = Join-Path ([Environment]::GetFolderPath("UserProfile")) ".evidence-first"
    for ($index = 0; $index -lt $Arguments.Count; $index++) {
        $argument = [string]$Arguments[$index]
        if ($argument -eq "--skip-runtime-bootstrap") {
            throw "Python is unavailable and --skip-runtime-bootstrap forbids the automatic Python bootstrap. Install Python 3.10+ or remove that flag."
        }
        if ($argument -eq "--state-home") {
            if ($index + 1 -ge $Arguments.Count) {
                throw "--state-home requires a path."
            }
            $index++
            $rawStateHome = [string]$Arguments[$index]
            continue
        }
        if ($argument.StartsWith("--state-home=", [StringComparison]::Ordinal)) {
            $rawStateHome = $argument.Substring("--state-home=".Length)
        }
    }

    if ([string]::IsNullOrWhiteSpace($rawStateHome)) {
        throw "The installer state-home path cannot be empty."
    }
    $userHome = [Environment]::GetFolderPath("UserProfile")
    if ($rawStateHome -eq "~") {
        $rawStateHome = $userHome
    }
    elseif ($rawStateHome.StartsWith("~/") -or $rawStateHome.StartsWith("~\")) {
        $rawStateHome = Join-Path $userHome $rawStateHome.Substring(2)
    }
    $resolved = [IO.Path]::GetFullPath($rawStateHome)
    if ($resolved -eq [IO.Path]::GetPathRoot($resolved)) {
        throw "The installer state-home path cannot be a filesystem root: $resolved"
    }
    return $resolved
}

function Get-WindowsArchitecture {
    foreach ($value in @($env:PROCESSOR_ARCHITEW6432, $env:PROCESSOR_ARCHITECTURE)) {
        $normalized = ([string]$value).Trim().ToLowerInvariant()
        if ($normalized -in @("arm64", "aarch64")) {
            return "arm64"
        }
        if ($normalized -in @("amd64", "x86_64", "x64")) {
            return "x64"
        }
    }
    throw "Unsupported Windows CPU architecture. The installer supports x64 and arm64."
}

function Invoke-NativeCapture {
    param(
        [string]$FilePath,
        [string[]]$Arguments
    )

    # Windows PowerShell 5.1 promotes native stderr redirected into 2>&1 to a
    # NativeCommandError when the script-wide preference is Stop. uv reports
    # ordinary download progress on stderr, so capture it under Continue and
    # decide success only from the native exit code.
    $previousErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $records = @(& $FilePath @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorPreference
    }
    if ($null -eq $exitCode) {
        $exitCode = 0
    }
    return [PSCustomObject]@{
        ExitCode = [int]$exitCode
        Output = $records
    }
}

function Test-PinnedUv {
    param(
        [string]$UvPath,
        [string]$ExpectedVersion
    )

    if (-not (Test-Path -LiteralPath $UvPath -PathType Leaf)) {
        return $false
    }
    try {
        $probe = Invoke-NativeCapture -FilePath $UvPath -Arguments @("--version")
        $versionText = ($probe.Output -join "`n").Trim()
        $versionPattern = "^uv " + [Regex]::Escape($ExpectedVersion) + "(?:\s|$)"
        return $probe.ExitCode -eq 0 -and $versionText -match $versionPattern
    }
    catch {
        return $false
    }
}

function Get-Sha256Hex {
    param([string]$Path)

    $stream = [IO.File]::OpenRead($Path)
    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $sha256.ComputeHash($stream)
        return (($bytes | ForEach-Object { $_.ToString("x2") }) -join "")
    }
    finally {
        $sha256.Dispose()
        $stream.Dispose()
    }
}

function Expand-PinnedUvExecutable {
    param(
        [string]$ArchivePath,
        [string]$DestinationPath
    )

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($ArchivePath)
    try {
        $matches = @($archive.Entries | Where-Object { [IO.Path]::GetFileName($_.FullName) -eq "uv.exe" })
        if ($matches.Count -ne 1) {
            throw "The pinned uv archive must contain exactly one uv.exe entry."
        }
        $entry = $matches[0]
        if ($entry.Length -le 0 -or $entry.Length -gt $MaxUvArchiveBytes) {
            throw "The uv executable size is outside the allowed range: $($entry.Length) bytes."
        }
        $input = $entry.Open()
        $output = [IO.File]::Create($DestinationPath)
        try {
            $input.CopyTo($output)
            $output.Flush()
        }
        finally {
            $output.Dispose()
            $input.Dispose()
        }
    }
    finally {
        $archive.Dispose()
    }
}

function Install-PinnedUv {
    param(
        [object]$Manifest,
        [string]$Architecture,
        [string]$RuntimeRoot
    )

    $uvDefinition = $Manifest.runtimes.uv
    $uvVersion = [string]$uvDefinition.version
    if ($uvVersion -notmatch '^\d+\.\d+\.\d+$') {
        throw "The pinned uv version is invalid."
    }
    $asset = @(
        $uvDefinition.assets | Where-Object {
            [string]$_.platform -eq "windows" -and [string]$_.architecture -eq $Architecture
        }
    )
    if ($asset.Count -ne 1) {
        throw "The runtime manifest must contain exactly one uv asset for windows-$Architecture."
    }
    $asset = $asset[0]
    $filename = [string]$asset.filename
    $expectedSha256 = ([string]$asset.sha256).ToLowerInvariant()
    if ($filename -notmatch '^uv-[A-Za-z0-9._-]+\.zip$' -or $expectedSha256 -notmatch '^[0-9a-f]{64}$') {
        throw "The pinned Windows uv asset metadata is invalid."
    }
    $url = ([string]$uvDefinition.urlTemplate).Replace("{version}", $uvVersion).Replace("{asset}", $filename)
    if (-not $url.StartsWith("https://github.com/astral-sh/uv/releases/download/", [StringComparison]::Ordinal)) {
        throw "The pinned uv download URL is outside the allowed HTTPS release origin."
    }

    $uvPath = Join-Path $RuntimeRoot "uv\uv.exe"
    if (Test-PinnedUv -UvPath $uvPath -ExpectedVersion $uvVersion) {
        return $uvPath
    }

    Write-SeedMessage "  Installing pinned uv $uvVersion for the initial Python bootstrap..."
    New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
    $temporaryRoot = Join-Path $RuntimeRoot (".python-seed-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $temporaryRoot | Out-Null
    try {
        $archive = Join-Path $temporaryRoot $filename
        $extractedUv = Join-Path $temporaryRoot "uv.exe"
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing
        $archiveInfo = Get-Item -LiteralPath $archive
        if ($archiveInfo.Length -le 0 -or $archiveInfo.Length -gt $MaxUvArchiveBytes) {
            throw "The uv archive size is outside the allowed range: $($archiveInfo.Length) bytes."
        }
        $actualSha256 = Get-Sha256Hex -Path $archive
        if ($actualSha256 -ne $expectedSha256) {
            throw "SHA-256 mismatch for $filename. The archive was not extracted."
        }
        Expand-PinnedUvExecutable -ArchivePath $archive -DestinationPath $extractedUv
        if (-not (Test-PinnedUv -UvPath $extractedUv -ExpectedVersion $uvVersion)) {
            throw "The extracted uv executable failed its version probe."
        }

        $uvDirectory = Split-Path -Parent $uvPath
        New-Item -ItemType Directory -Force -Path $uvDirectory | Out-Null
        $pendingUv = Join-Path $uvDirectory ("uv.exe.new." + [Guid]::NewGuid().ToString("N"))
        Copy-Item -LiteralPath $extractedUv -Destination $pendingUv
        if (Test-Path -LiteralPath $uvPath) {
            Remove-Item -LiteralPath $uvPath -Force
        }
        Move-Item -LiteralPath $pendingUv -Destination $uvPath
    }
    finally {
        if (Test-Path -LiteralPath $temporaryRoot) {
            Remove-Item -LiteralPath $temporaryRoot -Recurse -Force
        }
    }

    if (-not (Test-PinnedUv -UvPath $uvPath -ExpectedVersion $uvVersion)) {
        throw "The cached uv executable failed its post-install probe: $uvPath"
    }
    return $uvPath
}

try {
    $installer = [IO.Path]::GetFullPath($InstallerPath)
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
        throw "The integrated installer is missing: $installer"
    }
    $manifestPath = Join-Path $PSScriptRoot "runtime-manifest.json"
    $manifestInfo = Get-Item -LiteralPath $manifestPath
    if ($manifestInfo.Length -le 0 -or $manifestInfo.Length -gt $MaxManifestBytes) {
        throw "The runtime manifest is empty or oversized: $manifestPath"
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([int]$manifest.schemaVersion -ne 1) {
        throw "The runtime manifest schemaVersion must be 1."
    }
    $pythonVersion = [string]$manifest.runtimes.python.version
    if ($pythonVersion -notmatch '^3\.12\.\d+$' -or [string]$manifest.runtimes.python.delivery -ne "uv-managed") {
        throw "The runtime manifest does not define the supported managed Python 3.12 seed."
    }

    $stateHome = Get-InstallerStateHome -Arguments $InstallerArguments
    $runtimeRoot = Join-Path $stateHome "runtimes"
    $architecture = Get-WindowsArchitecture
    Write-SeedMessage "Initial Python bootstrap: windows-$architecture"
    Write-SeedMessage "  State home: $stateHome"
    $uvPath = Install-PinnedUv -Manifest $manifest -Architecture $architecture -RuntimeRoot $runtimeRoot

    $pythonInstallRoot = Join-Path $runtimeRoot "python"
    $pythonBinRoot = Join-Path $runtimeRoot "bin"
    New-Item -ItemType Directory -Force -Path $pythonInstallRoot, $pythonBinRoot | Out-Null
    $env:UV_PYTHON_INSTALL_DIR = $pythonInstallRoot
    $env:UV_PYTHON_BIN_DIR = $pythonBinRoot
    $env:XDG_BIN_HOME = $pythonBinRoot

    Write-SeedMessage "  Installing managed Python $pythonVersion..."
    $installResult = Invoke-NativeCapture -FilePath $uvPath -Arguments @("python", "install", $pythonVersion)
    foreach ($line in $installResult.Output) {
        Write-SeedMessage ([string]$line)
    }
    if ($installResult.ExitCode -ne 0) {
        throw "uv could not install managed Python $pythonVersion (exit $($installResult.ExitCode))."
    }

    $findResult = Invoke-NativeCapture -FilePath $uvPath -Arguments @("python", "find", $pythonVersion)
    if ($findResult.ExitCode -ne 0) {
        throw "uv installed Python but could not resolve it (exit $($findResult.ExitCode))."
    }
    $pythonPath = @(
        $findResult.Output |
            ForEach-Object { ([string]$_).Trim() } |
            Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }
    ) | Select-Object -Last 1
    if ([string]::IsNullOrWhiteSpace([string]$pythonPath)) {
        throw "uv did not return a usable managed Python path."
    }

    $versionProbe = Invoke-NativeCapture -FilePath $pythonPath -Arguments @("-c", "import sys; print(sys.version.split()[0])")
    $archProbe = Invoke-NativeCapture -FilePath $pythonPath -Arguments @("-c", "import platform; m=platform.machine().lower(); print('arm64' if m in {'arm64','aarch64'} else 'x64' if m in {'x86_64','amd64','x64'} else m)")
    if ($versionProbe.ExitCode -ne 0 -or (($versionProbe.Output -join "`n").Trim() -ne $pythonVersion)) {
        throw "Managed Python failed its pinned version probe: $pythonPath"
    }
    if ($archProbe.ExitCode -ne 0 -or (($archProbe.Output -join "`n").Trim() -ne $architecture)) {
        throw "Managed Python failed its architecture probe: $pythonPath"
    }

    Write-SeedMessage "  Launching the integrated installer with $pythonPath"
    & $pythonPath $installer @InstallerArguments
    $installerExit = $LASTEXITCODE
    if ($null -eq $installerExit) {
        $installerExit = 0
    }
    exit ([int]$installerExit)
}
catch {
    Write-SeedMessage ("Initial Python bootstrap failed: " + $_.Exception.Message)
    Write-SeedMessage "Install Python 3.12 manually from https://www.python.org/downloads/windows/ only if automatic recovery keeps failing."
    exit 127
}
