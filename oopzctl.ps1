$ErrorActionPreference = "Stop"
$scriptPath = Join-Path $PSScriptRoot "scripts\oopzctl.py"
python $scriptPath @args
exit $LASTEXITCODE
