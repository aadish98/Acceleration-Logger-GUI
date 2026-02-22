# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path


def _read_app_version():
    version_file = Path(__file__).resolve().parent.parent / "src" / "version.py"
    namespace = {}
    with open(version_file, "r", encoding="utf-8") as handle:
        exec(handle.read(), namespace)
    return namespace["APP_VERSION"]


APP_VERSION = os.environ.get("APP_VERSION", _read_app_version())
APP_BUILD_NAME = f"AccelerationLoggerGUI-{APP_VERSION}"

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
    icon=None,
    bundle_identifier=None,
)
