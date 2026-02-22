Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$version = py -c "namespace={}; exec(open('src/version.py', encoding='utf-8').read(), namespace); print(namespace['APP_VERSION'])"
$env:APP_VERSION = $version

py -m PyInstaller "Build/AccelerationLoggerGUI.spec"

Write-Host "Built Windows artifact in dist/: Acceleration Logger v$version.exe"
