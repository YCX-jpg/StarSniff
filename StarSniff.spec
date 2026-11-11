# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 目录版打包配置：dist/StarSniff/StarSniff.exe。

目录版不依赖 %TEMP% 解压，启动更快，也不容易被杀毒软件拦截；
发布时整体压成 zip。单文件版见 StarSniff-onefile.spec。
"""

from pathlib import Path

ROOT = Path(SPECPATH)
ASSETS = ROOT / "assets"

a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ASSETS / "starsniff.png"), "assets"),
        (str(ASSETS / "starsniff.ico"), "assets"),
    ],
    hiddenimports=[
        "PIL.Image",
        "PIL.ImageTk",
        "PIL._tkinter_finder",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest", "numpy", "pandas", "matplotlib", "scipy", "IPython",
        "notebook", "sqlite3", "test", "unittest",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="StarSniff",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS / "starsniff.ico"),
    version=str(ROOT / "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="StarSniff",
)
