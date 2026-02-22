Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$version = python -c "namespace={}; exec(open('src/version.py', encoding='utf-8').read(), namespace); print(namespace['APP_VERSION'])"
$env:APP_VERSION = $version

python -m PyInstaller "Build/AccelerationLoggerGUI.spec"

Write-Host "Built Windows artifact in dist/: AccelerationLoggerGUI-$version.exe"
