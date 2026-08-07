# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files


a = Analysis(
    ['AutoRewarder.py'],
    pathex=[],
    binaries=[],
    datas=[('gui', 'gui'), ('assets', 'assets')] + collect_data_files('nlpaug'),
    hiddenimports=[
        'selenium.webdriver.edge.webdriver',
        'pystray',
        'pystray._win32',
        'PIL',
        'PIL.Image',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # urllib3 discovers Brotli backends at import time. PyInstaller can bundle
    # brotlicffi without its extension, producing a partial module that lacks
    # ``error`` and crashes the frozen app before the UI starts. Brotli support
    # is optional for AutoRewarder's HTTP requests, so leave both backends out.
    excludes=['brotli', 'brotli._brotli', 'brotlicffi', 'brotlicffi._brotlicffi'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AutoRewarder',
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
    icon=['assets\\icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AutoRewarder',
)
