"""把运行所需的最少文件整理到一个文件夹里，作为 Release 分发。

不算程序（exe），只复制运行需要的文件，用户装了 Python 3.10+ 就能直接双击启动。

输出布局（默认 release/ReSTAgent_v0.2/）：

    ReSTAgent_v0.2/
      ReSTAgent.bat        双击启动（自动找 Python，无窗口）
      ReSTAgent.pyw        直接启动入口
      README.md            使用说明
      app/                 程序代码（一/二/三审、服务端、DOCX 处理）
      web/                 前端页面
      samples/             示例稿件
      vendor/              内置 pypdfium2（三审解析 PDF，无需联网安装）

不包含用户数据（config.json、data/、third/、layout_rules.json、admin_secret.py），
首次运行会在用户目录自动生成，不落在程序目录里。

用法：
    python tools/make_release.py [版本号]
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERSION = sys.argv[1] if len(sys.argv) > 1 else "0.2"
OUT = ROOT / "release" / ("ReSTAgent_v%s" % VERSION)

COPY_TREES = ["app", "web", "vendor", "docs"]
COPY_FILES = ["ReSTAgent.bat", "ReSTAgent.pyw", "README.md"]
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git"}
SKIP_SUFFIX = {".pyc", ".pyo"}
# 敏感/生成文件：绝不进发行包
SKIP_FILES = {"admin_secret.py", "config.json", "layout_rules.json",
              "2026活动部教案-给AI的版本.docx"}


def copytree(src: pathlib.Path, dst: pathlib.Path) -> int:
    n = 0
    for p in src.rglob("*"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() in SKIP_SUFFIX or p.name in SKIP_FILES:
            continue
        target = dst / p.relative_to(src)
        if p.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
            n += 1
    return n


def make_sample_pack(app_dir: pathlib.Path) -> int:
    """samples/ 里只带示例 Word；三审样张体积大且是真实稿件，不进发行包。"""
    src = ROOT / "samples"
    if not src.is_dir():
        return 0
    dst = app_dir / "samples"
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in src.iterdir():
        if p.is_file() and p.suffix.lower() == ".docx" and p.stat().st_size < 3 * 1024 * 1024:
            shutil.copy2(p, dst / p.name)
            n += 1
    return n


def main() -> int:
    if OUT.exists():
        print("清理旧输出：%s" % OUT)
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    total = 0
    for name in COPY_TREES:
        src = ROOT / name
        if not src.is_dir():
            print("跳过（不存在）：%s" % name)
            continue
        c = copytree(src, OUT / name)
        print("  + %-10s %4d 个文件" % (name + "/", c))
        total += c
    for name in COPY_FILES:
        src = ROOT / name
        if src.is_file():
            shutil.copy2(src, OUT / name)
            print("  + %-10s" % name)
            total += 1

    c = make_sample_pack(OUT)
    if c:
        print("  + %-10s %4d 个文件（仅示例 Word）" % ("samples/", c))
        total += c

    # 版本信息（供界面显示与排查）
    (OUT / "VERSION.txt").write_text(
        "ReSTAgent v%s\n构建于 %s\n" % (VERSION, __import__("time").strftime("%Y-%m-%d %H:%M")),
        encoding="utf-8")

    size = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    print("\n输出：%s" % OUT)
    print("文件 %d 个，合计 %.1f MB" % (total + 1, size / 1024 / 1024))
    print("\n运行方式：双击 ReSTAgent.bat（自动查找 Python 3.10+）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
