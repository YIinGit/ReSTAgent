"""内置的制作人员与版本信息（写死在代码里，不再由管理员面板维护）。

想改名单或版本号，直接改这个文件；改完重启程序即可。
版本号最终的显示来源仍是 `app/__init__.py` 的 APP_VERSION，这里只是人员名单与昵称。
"""
from __future__ import annotations

# 制作人员：name 姓名 / role 分工。留空数组则界面不显示这一块。
STAFF: list[dict] = []

# 显示在顶栏与健康检查里的名称/标语（一般不用改）
from . import APP_NAME, APP_TAGLINE, APP_VERSION  # noqa: E402

NAME = APP_NAME
TAGLINE = APP_TAGLINE
VERSION = APP_VERSION


def public() -> dict:
    """给界面的只读信息。"""
    return {
        "name": NAME,
        "tagline": TAGLINE,
        "version": VERSION,
        "staff": [dict(s) for s in STAFF if (s.get("name") or s.get("role"))],
    }
