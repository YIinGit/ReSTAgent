# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：产出单文件 ReSTAgent.exe（无控制台窗口）。

在仓库根目录执行：
    pip install pyinstaller
    pyinstaller packaging/ReSTAgent.spec --noconfirm

产物：dist/ReSTAgent.exe
"""
import pathlib

ROOT = pathlib.Path(SPECPATH).resolve().parent  # packaging/ 的上一级 = 仓库根

a = Analysis(
    [str(ROOT / "ReSTAgent.pyw")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "web"), "web"),
        (str(ROOT / "app" / "rules"), "app/rules"),
        (str(ROOT / "samples"), "samples"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter.test", "unittest", "pydoc", "doctest", "test", "distutils"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ReSTAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon=str(ROOT / "web" / "icon.ico"),
)
