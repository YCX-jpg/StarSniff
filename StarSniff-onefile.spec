# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 单文件版：dist/StarSniff-portable.exe。

单文件版依赖系统 %TEMP% 解压；少数机器（临时目录受限 / 杀软拦截）会
启动失败，因此发布时同时提供目录版 zip 作为推荐下载。
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
    a.binaries,
    a.datas,
    [],
    name="StarSniff-portable",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS / "starsniff.ico"),
    version=str(ROOT / "version_info.txt"),
)
