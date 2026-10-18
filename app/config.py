"""ReSTAgent 配置。

配置保存在项目根目录 config.json，首次启动自动生成默认值。
API Key 仅存本地明文，不随任何请求外发（除目标 API 服务商）。
"""
from __future__ import annotations

import copy
import json
import pathlib

from . import APP_VERSION, paths

ROOT = paths.ROOT
CONFIG_PATH = paths.user_dir() / "config.json"

DEFAULTS = {
    "api": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "timeout": 240,
        "max_retries": 2,
        "max_tokens": 32768,        # 单次输出上限；0 = 不传该参数，用服务端默认（等于不设上限）
    },
    # 力度 -> 模型档位。留空则用 standard 的模型。
    "models": {
        "light": "deepseek-chat",
        "standard": "deepseek-chat",
        "strict": "deepseek-reasoner",
        "max": "deepseek-reasoner",
    },
    # 力度 -> 审稿强度（轮次由 pipeline 解释）
    "levels": {
        "light": {"label": "轻", "desc": "单遍速览，只报必改项", "temperature": 0.2, "max_tokens": 8192},
        "standard": {"label": "标准", "desc": "全规则单遍，日常使用", "temperature": 0.2, "max_tokens": 16384},
        "strict": {"label": "严格", "desc": "逐块审 + 复核轮，定稿前用", "temperature": 0.15, "max_tokens": 16384},
        "max": {"label": "极致", "desc": "多视角并行 + 交叉复核", "temperature": 0.1, "max_tokens": 16384},
    },
    "review": {
        "chunk_chars": 2800,        # 单块目标字数
        "chunk_overlap": 0,         # 相邻块重叠段落数（靠段落上下文代替，默认 0）
        "concurrency": 2,           # 同时在途的模型请求数
        "author": "ReSTAgent",      # 批注/修订作者名
        "max_findings_per_chunk": 40,
    },
    "ui": {"open_word_after_export": True},
    "export": {
        "font": "宋体",             # 空 = 跟随原稿；默认把正文中文字体统一为宋体
    },
    # 单价：人民币 / 每百万 token，用来估算与结算费用
    "pricing": {"input_per_m": 10.0, "output_per_m": 30.0, "currency": "CNY"},
    # 规则库开关：{规则ID: false 表示关闭}；未出现的规则默认启用
    "rules": {},
    # 管理员模式（密码的盐与哈希保存在源代码 app/admin_secret.py，不写这里）
    "admin": {
        "staff": [],
        "notice": "",
        "version": APP_VERSION,
        "api_defaults": {
            "base_url": "",
            "api_key": "",
            "models": {"light": "", "standard": "", "strict": "", "max": ""},
            "send_key": False,
        },
    },
    "debug": {"mock": False},       # true = 不调用真实 API，使用本地规则引擎出意见
}


def _deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    data: dict = {}
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    return _deep_merge(DEFAULTS, data)


def save(patch: dict) -> dict:
    cfg = _deep_merge(load(), patch or {})
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def masked(cfg: dict) -> dict:
    """返回给前端的配置副本：隐藏 key 明文与管理员密码哈希。"""
    out = copy.deepcopy(cfg)
    key = out.get("api", {}).get("api_key") or ""
    out["api"]["api_key_set"] = bool(key)
    out["api"]["api_key_hint"] = (key[:4] + "…" + key[-4:]) if len(key) > 8 else ("****" if key else "")
    out["api"]["api_key"] = ""
    out["admin"] = mask_admin(out.get("admin") or {})
    return out


def mask_admin(admin: dict) -> dict:
    """管理员配置的只读视图：密码不在这里，Key 只给是否已设置。"""
    out = copy.deepcopy(admin or {})
    out.pop("password_salt", None)     # 兼容旧配置：即便有残留也不外泄
    out.pop("password_hash", None)
    ad = out.get("api_defaults") or {}
    key = ad.get("api_key") or ""
    ad["api_key_set"] = bool(key)
    ad["api_key_hint"] = (key[:4] + "…" + key[-4:]) if len(key) > 8 else ("****" if key else "")
    ad["api_key"] = ""
    out["api_defaults"] = ad
    return out


def prices(cfg: dict) -> tuple[float, float]:
    """(输入单价, 输出单价)，单位：人民币元 / 每百万 token。"""
    p = (cfg or {}).get("pricing") or {}
    try:
        pin = float(p.get("input_per_m", 10) or 0)
    except (TypeError, ValueError):
        pin = 10.0
    try:
        pout = float(p.get("output_per_m", 30) or 0)
    except (TypeError, ValueError):
        pout = 30.0
    return pin, pout


def cost_of(usage: dict, cfg: dict) -> float:
    """按实际 usage 结算费用（元）。"""
    pin, pout = prices(cfg)
    u = usage or {}
    try:
        in_tok = int(u.get("prompt_tokens") or 0)
        out_tok = int(u.get("completion_tokens") or 0)
    except (TypeError, ValueError):
        in_tok = out_tok = 0
    return round((in_tok * pin + out_tok * pout) / 1000000.0, 4)


def model_for(cfg: dict, level: str) -> str:
    models = cfg.get("models", {})
    return models.get(level) or models.get("standard") or "deepseek-chat"
