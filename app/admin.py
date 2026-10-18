"""管理员模式：密码哈希校验、公告/制作人员/版本号/默认 API 的读写。

密码只保存 `password_salt`（随机盐）与 `password_hash`（hashlib.sha256 结果），
配置落在 config.json 的 admin 段里，绝不保存明文。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from . import APP_VERSION

DEFAULT_PASSWORD = "cdqzzy1103"
MODEL_KEYS = ("light", "standard", "strict", "max")


def _hash(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def verify(cfg: dict, password: str) -> bool:
    """校验密码；从未改过密码时与内置默认密码比对。"""
    adm = (cfg or {}).get("admin") or {}
    salt = str(adm.get("password_salt") or "")
    stored = str(adm.get("password_hash") or "")
    if not stored:
        return hmac.compare_digest(str(password or ""), DEFAULT_PASSWORD)
    return hmac.compare_digest(_hash(str(password or ""), salt), stored)


def password_patch(new_password: str) -> dict:
    """生成设置新密码用的配置片段（盐 + 哈希）。"""
    pwd = str(new_password or "")
    if len(pwd) < 4:
        raise ValueError("新密码至少 4 位")
    salt = secrets.token_hex(16)
    return {"password_salt": salt, "password_hash": _hash(pwd, salt)}


def admin_cfg(cfg: dict) -> dict:
    """补齐默认值后的 admin 配置（供后端逻辑使用）。"""
    adm = dict((cfg or {}).get("admin") or {})
    adm.setdefault("staff", [])
    adm.setdefault("notice", "")
    adm.setdefault("version", APP_VERSION)
    ad = dict(adm.get("api_defaults") or {})
    ad.setdefault("base_url", "")
    ad.setdefault("api_key", "")
    ad.setdefault("send_key", False)
    models = dict(ad.get("models") or {})
    for k in MODEL_KEYS:
        models.setdefault(k, "")
    ad["models"] = models
    adm["api_defaults"] = ad
    return adm


def public(cfg: dict) -> dict:
    """普通界面可见的只读信息：公告、版本号、制作人员。"""
    adm = admin_cfg(cfg)
    staff = []
    for it in adm.get("staff") or []:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        role = str(it.get("role") or "").strip()
        if name or role:
            staff.append({"name": name, "role": role})
    return {
        "notice": str(adm.get("notice") or ""),
        "version": str(adm.get("version") or APP_VERSION),
        "staff": staff,
    }


def _clean_staff(items) -> list[dict]:
    out = []
    for it in (items or [])[:80]:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()[:40]
        role = str(it.get("role") or "").strip()[:40]
        if name or role:
            out.append({"name": name, "role": role})
    return out


def save_patch(cfg: dict, body: dict) -> dict:
    """把管理员面板提交的内容整理成 config.save 的 patch，并处理「下发 Key」。"""
    adm = admin_cfg(cfg)
    patch: dict = {"admin": {}}
    if "notice" in body:
        patch["admin"]["notice"] = str(body.get("notice") or "")[:2000]
    if "version" in body:
        patch["admin"]["version"] = str(body.get("version") or "").strip()[:40]
    if "staff" in body:
        patch["admin"]["staff"] = _clean_staff(body.get("staff"))

    ad_body = body.get("api_defaults")
    if isinstance(ad_body, dict):
        cur = adm["api_defaults"]
        models = {}
        for k in MODEL_KEYS:
            val = ad_body.get("models", {}).get(k) if isinstance(ad_body.get("models"), dict) else None
            models[k] = str(val if val is not None else cur["models"].get(k, "")).strip()[:120]
        key = str(ad_body.get("api_key") or "").strip()
        new_ad = {
            "base_url": str(ad_body.get("base_url", cur.get("base_url", "")) or "").strip()[:300],
            "api_key": key or str(cur.get("api_key") or ""),
            "models": models,
            "send_key": bool(ad_body.get("send_key", cur.get("send_key"))),
        }
        patch["admin"]["api_defaults"] = new_ad
        # Base URL 与模型名作为「默认 API」直接生效；Key 只有勾选「同时下发」才写入
        api_patch = patch.setdefault("api", {})
        if new_ad["base_url"]:
            api_patch["base_url"] = new_ad["base_url"]
        if new_ad["send_key"] and new_ad["api_key"]:
            api_patch["api_key"] = new_ad["api_key"]
        model_patch = {k: v for k, v in models.items() if v}
        if model_patch:
            patch["models"] = model_patch
    return patch
