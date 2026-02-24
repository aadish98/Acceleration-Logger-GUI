#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Pillow is required for PyInstaller to convert .ico -> .icns on macOS.
python3 -c "import PIL" >/dev/null 2>&1 || python3 -m pip install Pillow

APP_VERSION="$(python -c 'namespace={}; exec(open("src/version.py", encoding="utf-8").read(), namespace); print(namespace["APP_VERSION"])')"
export APP_VERSION

python3 -m PyInstaller --noconfirm "Build/AccelerationLoggerGUI.spec"

echo "Built macOS artifact in dist/: Acceleration Logger v${APP_VERSION}.app"
