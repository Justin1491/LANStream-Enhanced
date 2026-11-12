# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project = Path(SPECPATH)

a = Analysis(
    ["desktop_launcher.py"],
    pathex=[str(project)],
    binaries=[],
    datas=[
        (str(project / "templates"), "templates"),
        (str(project / "static"), "static"),
        (str(project / "docs" / "pystray-COPYING.LGPL"), "docs"),
        (str(project / "docs" / "pystray-COPYING"), "docs"),
        (str(project / "docs" / "six-LICENSE"), "docs"),
    ],
    hiddenimports=["pystray._win32"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LANStream",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(project / "static" / "lanstream.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="LANStream",
)
