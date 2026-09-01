[CmdletBinding()]
param(
    [switch]$WithJm,
    [switch]$WithoutQqMusicLogin,
    [switch]$SkipBrowser,
    [switch]$ExternalMusicApi,
    [switch]$NonInteractive,
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$originalLocation = (Get-Location).Path
$projectDir = $PSScriptRoot
Set-Location $projectDir
trap {
    Set-Location $originalLocation
    throw $_
}

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @()
    )
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit code $LASTEXITCODE): $FilePath $($ArgumentList -join ' ')"
    }
}

function Read-YesNo {
    param(
        [Parameter(Mandatory = $true)][string]$Question,
        [bool]$Default = $true
    )
    $hint = if ($Default) { "Y/n" } else { "y/N" }
    while ($true) {
        $answer = (Read-Host "$Question [$hint]").Trim().ToLowerInvariant()
        if (-not $answer) { return $Default }
        if ($answer -in @("y", "yes")) { return $true }
        if ($answer -in @("n", "no")) { return $false }
        Write-Host "Enter y or n."
    }
}

$interactive = -not $NonInteractive -and -not [Console]::IsInputRedirected
$installJm = [bool]$WithJm
$installQqMusicLogin = -not [bool]$WithoutQqMusicLogin
$installBrowser = -not [bool]$SkipBrowser
$installMusicApi = -not [bool]$ExternalMusicApi
if ($interactive) {
    Write-Host ""
    Write-Host "=== OOPZ Music Bot Setup ==="
    if (-not $PSBoundParameters.ContainsKey("WithJm")) {
        $installJm = Read-YesNo "Install the optional JM file tasks?" $false
    }
    if (-not $PSBoundParameters.ContainsKey("WithoutQqMusicLogin")) {
        $installQqMusicLogin = Read-YesNo "Install QQ Music QR login and automatic cookie refresh?" $true
    }
    if (-not $PSBoundParameters.ContainsKey("SkipBrowser")) {
        $installBrowser = Read-YesNo "Download Chromium for voice playback?" $true
    }
    if (-not $PSBoundParameters.ContainsKey("ExternalMusicApi")) {
        $installMusicApi = Read-YesNo "Install the pinned compatible QQ Music API?" $true
    }
    Write-Host ""
    Write-Host "Installation will now start. Existing .env and virtual environments are preserved."
}

try {
    & $Python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
} catch {
    throw "Python was not found: $Python. Install Python 3.11+ or use -Python."
}
if ($LASTEXITCODE -ne 0) {
    $version = & $Python --version 2>&1
    throw "Python 3.11+ is required. Current version: $version"
}

if ($installJm -or $installMusicApi) {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        throw "The default music API and JM extension require Node.js 18+."
    }
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "The default music API and JM extension require npm."
    }
    Invoke-Native node -ArgumentList @(
        "-e",
        "const major=Number(process.versions.node.split('.')[0]); process.exit(major >= 18 ? 0 : 1)"
    )
}
if ($installMusicApi -and -not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Installing the pinned QQ Music API requires Git."
}

Write-Host "[1/6] Creating the Python virtual environment"
$venvPython = Join-Path $projectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    Invoke-Native $Python -ArgumentList @("-m", "venv", ".venv")
}
$venvBot = Join-Path $projectDir ".venv\Scripts\oopzbot.exe"

Write-Host "[2/6] Installing Python dependencies"
Invoke-Native $venvPython -ArgumentList @("-m", "pip", "install", "--upgrade", "pip")
$extras = @()
if ($installJm) { $extras += "jm" }
if ($installQqMusicLogin) { $extras += "qqmusic-login" }
$package = if ($extras.Count) { ".[" + ($extras -join ",") + "]" } else { "." }
Invoke-Native $venvPython -ArgumentList @("-m", "pip", "install", $package)

if (-not $env:PLAYWRIGHT_BROWSERS_PATH) {
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $projectDir ".playwright"
}
if (-not $installBrowser) {
    Write-Host "[3/6] Skipping Chromium installation"
} else {
    Write-Host "[3/6] Installing Chromium"
    Invoke-Native $venvPython -ArgumentList @("-m", "playwright", "install", "chromium")
}

if ($installMusicApi) {
    Write-Host "[4/6] Installing the pinned QQ Music API"
    Invoke-Native $venvPython -ArgumentList @("scripts\install_qqmusic.py")
} else {
    Write-Host "[4/6] Using an external music API"
}

if ($installJm) {
    Write-Host "[5/6] Installing JM uploader dependencies"
    Invoke-Native npm -ArgumentList @(
        "ci", "--omit=dev", "--prefix", "tools\qqbot-uploader"
    )
} else {
    Write-Host "[5/6] JM extension is disabled"
}

$musicMode = if ($installMusicApi) { "managed" } else { "external" }
Write-Host "[6/6] Configuring the bot"
if ($interactive) {
    $wizardArguments = @("scripts\configure.py", "--music-mode", $musicMode)
    if ($installJm) { $wizardArguments += "--with-jm" }
    Invoke-Native $venvPython -ArgumentList $wizardArguments
    Write-Host "Checking configuration..."
    & $venvBot check
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Configuration is incomplete. Update .env and run oopzbot check again."
    }
} elseif (-not (Test-Path -LiteralPath .env)) {
    Invoke-Native $venvBot -ArgumentList @("init")
    Invoke-Native $venvPython -ArgumentList @(
        "scripts\configure.py", "--music-mode", $musicMode, "--set-music-mode-only"
    )
} else {
    Write-Host "Keeping the existing .env file."
    Invoke-Native $venvPython -ArgumentList @(
        "scripts\configure.py", "--music-mode", $musicMode, "--set-music-mode-only"
    )
}

Write-Host ""
Write-Host "Installation completed."
Write-Host ""
Write-Host "1. Edit configuration: $projectDir\.env"
Write-Host "2. Check configuration: $venvBot check"
Write-Host "3. Discover channels: $venvBot discover"
Write-Host "4. Start the bot: $venvBot start"
if ($installJm) {
    $jmService = Join-Path $projectDir ".venv\Scripts\oopzbot-jm-service.exe"
    Write-Host "5. Start the independent JM worker: $jmService (Redis required)"
}
Set-Location $originalLocation
$global:LASTEXITCODE = 0
