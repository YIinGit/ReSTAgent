"""管理员模式：密码校验、公告/制作人员/版本号/默认 API 的读写。

密码只以「随机盐 + sha256 哈希」的形式保存在源代码文件
`app/admin_secret.py` 里（由管理员面板自动改写），不写入 config.json。
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import secrets

from . import APP_VERSION, paths

MODEL_KEYS = ("light", "standard", "strict", "max")

def _new_random_password() -> str:
    """首次运行生成随机初始口令（12 位，去掉易混字符）。"""
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(12))

def _seed_from_resource(target: pathlib.Path) -> bool:
    """首次运行没有密码文件时生成一份（随机口令，写到同目录的提示文件）。

    绝不使用固定默认口令——仓库是公开的。
    注意：只要同目录已有 *_secret.json 就算已初始化，否则会覆盖用户刚设的密码。
    """
    try:
        p = pathlib.Path(target)
        if p.exists() or p.with_suffix(".json").exists():
            return False
        p.parent.mkdir(parents=True, exist_ok=True)
        pwd = _new_random_password()
        salt = secrets.token_hex(16)
        write_secret(salt, _hash_scrypt(pwd, salt), p)
        # 不打印明文口令：发行包以无窗口方式启动，控制台本就看不到，
        # 且易被截图/日志留存。口令写在说明文件里，用完请删除。
        try:
            hint = p.parent / "管理员初始密码.txt"
            hint.write_text(
                "管理员初始密码：%s\n请登录「管理员」面板后立刻修改，然后删除本文件。\n" % pwd,
                encoding="utf-8")
        except Exception:
            pass
        return True
    except Exception:  # noqa: BLE001
        return False

if paths.is_frozen():
    # 打包后 _MEIPASS 只读：密码文件放在用户目录，首次运行时从包内种子复制
    SECRET_PATH = paths.user_dir() / "admin_secret.py"
    if not SECRET_PATH.exists():
        _seed_from_resource(SECRET_PATH)
elif os.environ.get("RESTAGENT_SECRET_DIR"):
    # 源码运行但密码文件放在仓库外（避免把密码哈希提交到公开仓库）
    SECRET_PATH = pathlib.Path(os.environ["RESTAGENT_SECRET_DIR"]) / "admin_secret.py"
    if not SECRET_PATH.exists():
        _seed_from_resource(SECRET_PATH)
elif (paths.ROOT / "admin_secret.py").exists():
    # 用户数据目录就在仓库根（config.json 在根目录）时，密码文件也放这里，
    # 已经被 .gitignore 排除，不会进公开仓库。
    SECRET_PATH = paths.ROOT / "admin_secret.py"
elif (paths.ROOT / "user" / "admin_secret.py").exists():
    SECRET_PATH = paths.ROOT / "user" / "admin_secret.py"
else:
    SECRET_PATH = paths.ROOT / "app" / "admin_secret.py"

def _hash(password: str, salt: str) -> str:
    """单轮 SHA-256：仅用于兼容旧密码文件，新文件一律用 scrypt。"""
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1


def _hash_scrypt(password: str, salt: str) -> str:
    """口令哈希用 scrypt（标准库自带），比单轮 SHA-256 抗 GPU 暴力破解强几个数量级。"""
    dk = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt),
                        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return "scrypt$%d$%d$%d$%s" % (SCRYPT_N, SCRYPT_R, SCRYPT_P, dk.hex())


def _verify_hash(password: str, salt: str, stored: str) -> bool:
    """支持 scrypt（新）与单轮 SHA-256（旧文件）。"""
    try:
        if stored.startswith("scrypt$"):
            _, n, r, p, hexdk = stored.split("$", 4)
            dk = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt),
                                n=int(n), r=int(r), p=int(p), dklen=len(hexdk) // 2)
            return secrets.compare_digest(dk.hex(), hexdk)
        return secrets.compare_digest(_hash(password, salt), stored)
    except Exception:  # noqa: BLE001
        return False


def load_secret(path: pathlib.Path | None = None) -> tuple[str, str]:
    """读取 (盐, 哈希)。

    优先读 JSON（*_secret.json，安全，不执行代码）；兼容旧的 .py 文件，
    但**不再 exec**——只用正则抽出常量，避免密码文件被写入任意代码后被执行。
    """
    p = pathlib.Path(path or SECRET_PATH)
    jp0 = p.with_suffix(".json")
    if not jp0.exists() and not p.exists() and p == SECRET_PATH:
        _seed_from_resource(p)
    if not jp0.exists() and not p.exists():
        raise ValueError("管理员密码文件缺失：%s" % p)

    # 新格式：JSON
    jp = p.with_suffix(".json")
    if jp.exists():
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
            salt = str(data.get("salt") or "")
            hashed = str(data.get("hash") or "")
            if salt and hashed:
                return salt, hashed
        except Exception:
            pass

    # 旧格式：.py 常量（正则提取，不执行）
    try:
        text = p.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        raise ValueError("管理员密码文件无法读取：%s (%s)" % (p, e)) from e
    m_s = re.search(r'^SALT\s*=\s*"([0-9a-fA-F]+)"', text, re.M)
    m_h = re.search(r'^HASH\s*=\s*"([^"]*)"', text, re.M)
    salt = m_s.group(1) if m_s else ""
    hashed = m_h.group(1) if m_h else ""
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
    return _verify_hash(str(password or ""), salt, stored)

def set_password(new_password: str) -> None:
    """修改密码：生成新盐与哈希，写回密码文件（打包运行时写用户目录里的副本）。"""
    pwd = str(new_password or "")
    if len(pwd) < 4:
        raise ValueError("新密码至少 4 位")
    if paths.is_frozen() and not pathlib.Path(SECRET_PATH).exists():
        _seed_from_resource(pathlib.Path(SECRET_PATH))
    salt = secrets.token_hex(16)
    hashed = _hash_scrypt(pwd, salt)
    write_secret(salt, hashed)

def write_secret(salt: str, hashed: str, path: pathlib.Path | None = None) -> None:
    """把盐与哈希写成 JSON（原子替换）。

    不再写可执行的 .py：密码文件在用户可写目录里，若被塞入任意代码，
    读回时 exec() 就会执行它。JSON 只做数据解析，没有这个风险。
    """
    p = pathlib.Path(path or SECRET_PATH)
    jp = p.with_suffix(".json")
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"salt": str(salt), "hash": str(hashed),
                          "algo": "scrypt"}, ensure_ascii=False, indent=1)
    tmp = jp.with_name(jp.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    try:
        os.replace(tmp, jp)
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise ValueError("无法写入管理员密码文件：%s" % e) from e

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
    """普通界面可见的只读信息（制作人员与版本号已移至 app/about.py）。

    保留此函数只为兼容旧调用，返回空公告；新代码请用 about.public()。
    """
    return {"notice": "", "version": APP_VERSION, "staff": []}


def save_patch(cfg: dict, body: dict) -> dict:
    """把管理员面板提交的内容整理成 config.save 的 patch，并处理「下发 Key」。"""
    adm = admin_cfg(cfg)
    patch: dict = {"admin": {}}
    # 公告 / 版本号 / 制作人员 已写死在 app/about.py，不再由面板维护

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
