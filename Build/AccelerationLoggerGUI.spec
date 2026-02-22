# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path


def _resolve_repo_root():
    spec_file = globals().get("__file__")
    if spec_file:
        return Path(spec_file).resolve().parent.parent
    # PyInstaller may execute spec files without defining __file__.
    return Path.cwd()


def _read_app_version():
    repo_root = _resolve_repo_root()
    version_file = repo_root / "src" / "version.py"
    namespace = {}
    with open(version_file, "r", encoding="utf-8") as handle:
        exec(handle.read(), namespace)
    return namespace["APP_VERSION"]


APP_VERSION = os.environ.get("APP_VERSION", _read_app_version())
APP_BUILD_NAME = f"Acceleration Logger v{APP_VERSION}"
APP_ICON = _resolve_repo_root() / "Build" / "app.ico"
APP_ICON = str(APP_ICON) if APP_ICON.exists() else None

a = Analysis(
    ['../src/AccelerationLoggerGUI.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_BUILD_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    icon=APP_ICON,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
app = BUNDLE(
    exe,
    name=f'{APP_BUILD_NAME}.app',
    icon=APP_ICON,
    bundle_identifier=None,
)
