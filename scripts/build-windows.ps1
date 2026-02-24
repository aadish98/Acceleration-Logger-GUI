Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$iconPath = Join-Path $repoRoot "Build\app.ico"
if (-not (Test-Path $iconPath)) {
    throw "Missing app icon at $iconPath"
}

# Ensure Pillow is available for icon normalization.
try {
    py -c "import PIL" | Out-Null
} catch {
    py -m pip install Pillow
}

# Normalize icon to a real multi-resolution ICO file.
py -c "from PIL import Image; p=r'Build/app.ico'; im=Image.open(p).convert('RGBA'); im.save(p, format='ICO', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"

$version = py -c "namespace={}; exec(open('src/version.py', encoding='utf-8').read(), namespace); print(namespace['APP_VERSION'])"
$env:APP_VERSION = $version

py -m PyInstaller --noconfirm --clean --workpath "build" --distpath "dist" "Build/AccelerationLoggerGUI.spec"

Write-Host "Built Windows artifact in dist/: Acceleration Logger v$version.exe"
