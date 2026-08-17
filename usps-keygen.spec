# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all


patchright_data, patchright_binaries, patchright_hidden = collect_all("patchright")

analysis = Analysis(
    ["main.py"],
    pathex=[],
    binaries=patchright_binaries,
    datas=patchright_data + [("registrations_template.csv", ".")],
    hiddenimports=patchright_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="USPSBatchRegistration",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

distribution = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="USPSBatchRegistration",
)
