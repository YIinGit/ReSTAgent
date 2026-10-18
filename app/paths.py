"""路径约定：程序本体只读，用户数据放在程序目录之外。

数据目录（config.json、data/、日志）按以下优先级确定：
1. 环境变量 RESTAGENT_DATA 指定的目录（最优先，便于任意迁移）
2. 程序目录同级的 user/ 目录（发行包布局：<发布目录>/ReSTAgent/ 与 <发布目录>/user/）
3. 打包成 exe 且 exe 旁有 config.json → 便携模式，就用 exe 目录
4. 打包成 exe → %LOCALAPPDATA%/ReSTAgent
5. 源码运行且项目目录里有 config.json → 项目目录（开发习惯）
6. 兜底：%LOCALAPPDATA%/ReSTAgent

只读资源（web/、app/rules/、samples/）始终在程序目录或 _MEIPASS 里。
"""
from __future__ import annotations

import os
import pathlib
import sys

APP_NAME = "ReSTAgent"
ROOT = pathlib.Path(__file__).resolve().parent.parent

_FROZEN = bool(getattr(sys, "frozen", False))


def program_dir() -> pathlib.Path:
    """程序本体所在目录（exe 所在目录，或源码仓库根）。"""
    if _FROZEN:
        return pathlib.Path(sys.executable).resolve().parent
    return ROOT


def _localapp() -> pathlib.Path:
    return pathlib.Path(os.environ.get("LOCALAPPDATA") or pathlib.Path.home()) / APP_NAME


def data_dir() -> pathlib.Path:
    """用户数据目录的最终位置（不创建）。"""
    env = (os.environ.get("RESTAGENT_DATA") or "").strip()
    if env:
        return pathlib.Path(env).expanduser().resolve()
    side = program_dir().parent / "user"      # 发行包：程序与数据同级分开
    if side.is_dir():
        return side
    if _FROZEN:
        if (program_dir() / "config.json").exists():
            return program_dir()              # 便携模式：exe 旁带配置
        return _localapp()
    if (ROOT / "config.json").exists():
        return ROOT
    return _localapp()


def user_dir() -> pathlib.Path:
    base = data_dir()
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
