"""规则库：内置规则章节的启停状态、自定义规则的落盘读写。

- 内置规则正文在 app/rules/<stage>_stage.md（只读资源）
- 自定义规则追加到 data/rules_custom.json
- 开关状态（内置章节与自定义规则）统一存 config.json 的 rules 段
"""
from __future__ import annotations

import datetime as dt
import json
import uuid

from . import paths

DATA = paths.user_dir() / "data"
CUSTOM_PATH = DATA / "rules_custom.json"

STAGES = ("zero", "first", "second")
STAGE_LABEL = {"zero": "零审", "first": "一审", "second": "二审", "third": "三审"}
CUSTOM_PREFIX = "custom:"


def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_custom() -> list[dict]:
    if not CUSTOM_PATH.exists():
        return []
    try:
        data = json.loads(CUSTOM_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for it in data if isinstance(data, list) else []:
        if isinstance(it, dict) and it.get("id") and it.get("title"):
            out.append(it)
    return out


def save_custom(items: list[dict]) -> None:
    CUSTOM_PATH.parent.mkdir(parents=True, exist_ok=True)
    CUSTOM_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def add_custom(title: str, body: str, stage: str) -> dict:
    title = str(title or "").strip()
    body = str(body or "").strip()
    if not title:
        raise ValueError("规则标题不能为空")
    if not body:
        raise ValueError("规则正文不能为空")
    if stage not in STAGES:
        raise ValueError("未知审稿环节")
    item = {
        "id": "c" + uuid.uuid4().hex[:8],
        "title": title[:80],
        "body": body[:4000],
        "stage": stage,
        "created": _now(),
    }
    items = load_custom()
    items.append(item)
    save_custom(items)
    return item


def is_enabled(cfg: dict, rule_id: str) -> bool:
    rules = (cfg or {}).get("rules") or {}
    return bool(rules.get(rule_id, True))


def enabled_ids(cfg: dict) -> dict:
    return dict((cfg or {}).get("rules") or {})
