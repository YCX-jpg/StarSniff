# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 目录版打包配置：dist/StarSniff/StarSniff.exe。

界面走「本地 HTTP 桥 + Edge 应用窗口」，所以不依赖 pythonnet / .NET；
目录版不依赖 %TEMP% 解压，启动快，也不容易被杀毒误报。
发布时整体压 zip（绿色版），Inno Setup 也复用这个目录做安装包。
"""

from pathlib import Path

ROOT = Path(SPECPATH)
ASSETS = ROOT / "assets"
WEBUI = ROOT / "resource_sniffer" / "webui"

a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ASSETS / "starsniff.png"), "assets"),
        (str(ASSETS / "starsniff.ico"), "assets"),
        (str(WEBUI / "index.html"), "resource_sniffer/webui"),
        (str(WEBUI / "app.css"), "resource_sniffer/webui"),
        (str(WEBUI / "app.js"), "resource_sniffer/webui"),
        (str(WEBUI / "bridge.js"), "resource_sniffer/webui"),
    ],
    hiddenimports=[
        "PIL.Image",
        "PIL.ImageGrab",
        "PIL.ImageTk",
        "PIL._tkinter_finder",
        "tkinter.filedialog",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest", "numpy", "pandas", "matplotlib", "scipy", "IPython",
        "notebook", "sqlite3", "test", "unittest",
        "webview", "clr", "pythonnet", "clr_loader", "bottle", "proxy_tools",
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
