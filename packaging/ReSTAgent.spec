# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：产出单文件 ReSTAgent.exe（无控制台窗口）。

用法（在仓库根目录）：
    python -m PyInstaller packaging/ReSTAgent.spec --noconfirm

产物：dist/ReSTAgent.exe

内含：
- web/（前端）、app/rules/（一~三审规则文本）、samples/（示例稿）
- vendor/（内置的 pypdfium2，三审解析 PDF 用，无需联网安装）

不含：
- config.json（API Key）、data/、third/、admin_secret.py 的真实哈希——都放在用户目录
"""
import pathlib

ROOT = pathlib.Path(SPECPATH).resolve().parent  # packaging/ 的上一级 = 仓库根

datas = [
    (str(ROOT / "web"), "web"),
    (str(ROOT / "app" / "rules"), "app/rules"),
    (str(ROOT / "samples"), "samples"),
    (str(ROOT / "vendor"), "vendor"),
]

a = Analysis(
    [str(ROOT / "ReSTAgent.pyw")],
    pathex=[str(ROOT), str(ROOT / "vendor")],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "tkinter.test", "unittest", "pydoc", "doctest", "test",
              "distutils"],
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
