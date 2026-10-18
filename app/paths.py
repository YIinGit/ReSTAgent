"""路径约定：源码运行写在项目目录，打包成 exe 后写到用户目录。

- user_dir()  可写数据根（config.json、data/、日志）
- resource()  只读资源根（web/、app/rules/、samples/），打包后在 _MEIPASS 里
"""
from __future__ import annotations

import os
import pathlib
import sys

APP_NAME = "ReSTAgent"
ROOT = pathlib.Path(__file__).resolve().parent.parent

_FROZEN = bool(getattr(sys, "frozen", False))


def user_dir() -> pathlib.Path:
    if _FROZEN:
        base = pathlib.Path(os.environ.get("LOCALAPPDATA") or pathlib.Path.home()) / APP_NAME
    else:
        base = ROOT
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return base


def resource(*parts: str) -> pathlib.Path:
    base = pathlib.Path(getattr(sys, "_MEIPASS", ROOT))
    return base.joinpath(*parts)


def is_frozen() -> bool:
    return _FROZEN
