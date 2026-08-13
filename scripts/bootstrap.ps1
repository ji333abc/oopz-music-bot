$ErrorActionPreference = "Stop"
$projectDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$arguments = @($args)
if ($env:OOPZBOT_INSTALL_JM -eq "1") {
    $arguments = @("-WithJm") + $arguments
}
& (Join-Path $projectDir "install.ps1") @arguments
exit $LASTEXITCODE
