#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

APP_VERSION="$(python -c 'namespace={}; exec(open("src/version.py", encoding="utf-8").read(), namespace); print(namespace["APP_VERSION"])')"
export APP_VERSION

python -m PyInstaller "Build/AccelerationLoggerGUI.spec"

echo "Built macOS artifact in dist/: Acceleration Logger v${APP_VERSION}.app"
