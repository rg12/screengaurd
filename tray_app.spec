# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    ['tray_app.py'],
    pathex=[],
    binaries=[],
    # Bundle the icon assets: create_icon_image()/_apply_window_icon() look them
    # up under Path(__file__).parent, which in a onefile build is the temp
    # extraction dir — without this they fall back to the generated icon.
    # sv_ttk's .tcl theme files and spritesheet PNGs are package data, not
    # Python modules, so PyInstaller won't pick them up automatically either.
    datas=[('assets', 'assets')] + collect_data_files('sv_ttk'),
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
    name='tray_app',
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
    icon=['assets/screengaurd.ico'],
)
