"""管理员模式：密码校验、公告/制作人员/版本号/默认 API 的读写。

密码只以「随机盐 + sha256 哈希」的形式保存在源代码文件
`app/admin_secret.py` 里（由管理员面板自动改写），不写入 config.json。
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import secrets

from . import APP_VERSION, paths

MODEL_KEYS = ("light", "standard", "strict", "max")

def _seed_from_resource(target: pathlib.Path) -> bool:
    """打包运行时把内置的盐/哈希复制到可写目录，之后改密写在副本上。"""
    try:
        src = paths.resource("app", "admin_secret.py")
        if not src.exists() or src.resolve() == target.resolve():
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        return True
    except Exception:  # noqa: BLE001
        return False


if paths.is_frozen():
    # 打包后 _MEIPASS 只读：密码文件放在用户目录，首次运行时从包内种子复制
    SECRET_PATH = paths.user_dir() / "admin_secret.py"
    if not SECRET_PATH.exists():
        _seed_from_resource(SECRET_PATH)
else:
    SECRET_PATH = paths.ROOT / "app" / "admin_secret.py"


def _hash(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def load_secret(path: pathlib.Path | None = None) -> tuple[str, str]:
    """读取源代码里的 (盐, 哈希)。每次读取都重新解析文件，改密后立即生效。

    注意：不能用 importlib 加载——新旧文件字节数相同且同一秒内改写时，
    会命中 __pycache__ 里的旧 .pyc，导致旧密码改完仍然有效。
    """
    p = pathlib.Path(path or SECRET_PATH)
    if not p.exists():
        raise ValueError("管理员密码文件缺失：%s" % p)
    try:
        text = p.read_text(encoding="utf-8")
        ns: dict = {}
        exec(compile(text, str(p), "exec"), ns)  # noqa: S102 - 只执行本地固定格式文件
    except Exception as e:  # noqa: BLE001
        raise ValueError("管理员密码文件无法读取：%s (%s)" % (p, e)) from e
    salt = str(ns.get("SALT") or "")
    hashed = str(ns.get("HASH") or "")
    if not salt or not hashed:
        raise ValueError("管理员密码文件缺少 SALT/HASH：%s" % p)
    return salt, hashed


def verify(cfg: dict, password: str) -> bool:
    """校验管理员密码。密码不再存在 config 里，cfg 参数仅为兼容旧调用保留。"""
    try:
        salt, stored = load_secret()
    except Exception:  # noqa: BLE001
        return False
    if not stored:
        return False
    return secrets.compare_digest(_hash(str(password or ""), salt), stored)


def set_password(new_password: str) -> None:
    """修改密码：生成新盐与哈希，写回密码文件（打包运行时写用户目录里的副本）。"""
    pwd = str(new_password or "")
    if len(pwd) < 4:
        raise ValueError("新密码至少 4 位")
    if paths.is_frozen() and not pathlib.Path(SECRET_PATH).exists():
        _seed_from_resource(pathlib.Path(SECRET_PATH))
    salt = secrets.token_hex(16)
    hashed = _hash(pwd, salt)
    write_secret(salt, hashed)


def write_secret(salt: str, hashed: str, path: pathlib.Path | None = None) -> None:
    """把盐与哈希写回 Python 源代码文件（先写临时文件再原子替换）。"""
    p = pathlib.Path(path or SECRET_PATH)
    text = (
        '"""管理员密码的盐与哈希。由管理员面板自动改写，请勿手工编辑。"""\n'
        'SALT = "%s"\n'
        'HASH = "%s"\n'
    ) % (str(salt), str(hashed))
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.replace(tmp, p)
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise ValueError("无法写入管理员密码源代码文件：%s" % e) from e


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
