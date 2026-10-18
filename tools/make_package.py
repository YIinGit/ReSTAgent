"""把程序主体打包复制到 _package/，用户数据放在程序目录之外的 user/。

布局：
    _package/
      ReSTAgent/      程序本体（app、web、samples、启动脚本）——只读，不含用户数据
      user/           用户数据（config.json、data/）——放外面，可单独备份/迁移

用法：python tools/make_package.py [输出目录]
"""
from __future__ import annotations

import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "_package"
APP_DIR = OUT / "ReSTAgent"
USER_DIR = OUT / "user"

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git"}
SKIP_SUFFIX = {".pyc", ".pyo"}

COPY_TREES = ["app", "web", "samples"]
COPY_FILES = ["ReSTAgent.pyw", "ReSTAgent.bat", "start.bat", "README.md"]
USER_FILES = ["config.json"]
USER_TREES = ["data"]


def copytree(src: pathlib.Path, dst: pathlib.Path) -> int:
    n = 0
    for p in src.rglob("*"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() in SKIP_SUFFIX:
            continue
        rel = p.relative_to(src)
        target = dst / rel
        if p.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
            n += 1
    return n


def main():
    if OUT.exists():
        print("清理旧输出:", OUT)
        shutil.rmtree(OUT)
    APP_DIR.mkdir(parents=True)
    USER_DIR.mkdir(parents=True)

    total = 0
    for name in COPY_TREES:
        src = ROOT / name
        if src.is_dir():
            c = copytree(src, APP_DIR / name)
            print("  + %-10s %3d 个文件" % (name + "/", c))
            total += c
    for name in COPY_FILES:
        src = ROOT / name
        if src.is_file():
            shutil.copy2(src, APP_DIR / name)
            print("  + %-10s" % name)
            total += 1

    for name in USER_FILES:
        src = ROOT / name
        if src.is_file():
            shutil.copy2(src, USER_DIR / name)
            print("  ~ user/%s" % name)
    for name in USER_TREES:
        src = ROOT / name
        if src.is_dir():
            c = copytree(src, USER_DIR / name)
            if c:
                print("  ~ user/%-8s %3d 个文件" % (name + "/", c))

    print("\n输出目录:", OUT)
    print("程序: %s（%d 个文件）" % (APP_DIR, total))
    print("用户数据: %s" % USER_DIR)
    print("\n目录结构:")
    for p in sorted(OUT.rglob("*")):
        if p.is_dir():
            depth = len(p.relative_to(OUT).parts)
            print("   " * depth + p.name + "/")


if __name__ == "__main__":
    main()
