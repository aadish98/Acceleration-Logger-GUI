#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Pillow is required for PyInstaller to convert .ico -> .icns on macOS.
python3 -c "import PIL" >/dev/null 2>&1 || python3 -m pip install Pillow

# Normalize icon to a real multi-resolution ICO so Windows and macOS
# builds both start from the same valid source icon file.
python3 -c "from PIL import Image; p='Build/app.ico'; im=Image.open(p).convert('RGBA'); im.save(p, format='ICO', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"

APP_VERSION="$(python -c 'namespace={}; exec(open("src/version.py", encoding="utf-8").read(), namespace); print(namespace["APP_VERSION"])')"
export APP_VERSION

python3 -m PyInstaller --noconfirm --clean --workpath build --distpath dist "Build/AccelerationLoggerGUI.spec"

echo "Built macOS artifact in dist/: Acceleration Logger v${APP_VERSION}.app"
