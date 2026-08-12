$ErrorActionPreference = "Stop"
$projectDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $projectDir
if (-not $env:PLAYWRIGHT_BROWSERS_PATH) {
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $projectDir ".playwright"
}

python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install "."
& .\.venv\Scripts\python.exe -m playwright install chromium

if ($env:OOPZBOT_INSTALL_JM -eq "1") {
    & .\.venv\Scripts\python.exe -m pip install ".[jm]"
    npm ci --omit=dev --prefix tools\qqbot-uploader
}

if (-not (Test-Path -LiteralPath .env)) {
    & .\.venv\Scripts\oopzbot.exe init
}

Write-Host "安装完成。编辑 .env 后执行："
Write-Host "  .\.venv\Scripts\oopzbot.exe check"
Write-Host "  .\.venv\Scripts\oopzbot.exe start"
