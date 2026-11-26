"""三审：用视觉模型判定每页文字的角色（正文/作者/标题/图注/边栏/装饰）。

为什么需要它：
仅靠字号和字体族名判角色，遇到"作者名 12pt 与节标题 12pt"、"装饰性大字与真标题同为 24pt"
这类情况必然分错。视觉模型能看着版面判断"这块是作者名、那块是正文、右边那条是边栏"。

设计：
- 有视觉能力就用视觉结果（覆盖字号启发式）；
- 没有视觉模型 / 调用失败 / 返回不合法时，**自动回退**到 heuristics（rules.role_from_size），
  绝不因为视觉失败而阻塞三审。
- 判定结果按"页 → 文字块列表（角色 + 归一化 bbox）"返回，规则引擎再按 bbox 把 PDF span 归到角色。
"""
from __future__ import annotations

import base64
import json
import pathlib
import re

ROLES = ("author", "body", "title", "subtitle", "caption", "sidebar",
         "editbar", "decoration", "page_number", "other")

PROMPT = """你在做杂志排版终审。这是一本 185×260mm 双栏杂志的一页（含 3mm 出血）。

请识别页面上的**每一块文字区域**，判断它在刊物里的角色。

角色只能取这些值：
- author：文章作者名（通常在标题附近、字号小于标题、常带"撰文/文/"字样）
- title：文章大标题 / 节标题
- subtitle：副节标题、小标题
- body：正文段落（大段连续文字）
- caption：图片注释、图注、小框里的说明文字
- sidebar：边栏、编辑栏、名片式小栏目
- editbar：页脚编辑信息（美编、校对等极小字）
- decoration：装饰性文字（大字、图形化、样张展示，不承担阅读功能）
- page_number：页码
- other：无法判断

只输出 JSON，不要解释、不要代码块：
{"page_type":"cover|blank|content",
 "blocks":[{"role":"body","text":"该区域开头 15 个字","bbox":[x,y,w,h]}]}

bbox 用**归一化坐标**（0~1，原点在页面左上角，x 向右、y 向下）。
每页最多 40 个区域；文字看不清就用 other。
"""


def _client(cfg, model: str | None = None):
    """视觉模型可以走独立的 API 段（vision_api），没填则回退主 api。"""
    from ..review.llm import Client
    v = cfg.get("vision_api") or {}
    c = Client(cfg, section="vision_api")
    try:
        c.timeout_override = int(v.get("timeout") or 900)   # 读图 + 推理比纯文本慢得多
    except (TypeError, ValueError):
        c.timeout_override = 900
    m = model or v.get("model") or (cfg.get("models") or {}).get("vision") \
        or (cfg.get("models") or {}).get("standard") or ""
    return c, m


def _png_data_url(path: str) -> str:
    raw = pathlib.Path(path).read_bytes()
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


_JSON_RE = re.compile(r"\{.*\}", re.S)


def _parse_json(raw: str) -> dict | None:
    m = _JSON_RE.search(raw or "")
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def classify_page(png_path: str, cfg: dict, model: str | None = None,
                  page_no: int = 1) -> dict:
    """判定一页的文字块角色。失败返回 {"error": ...}，调用方据此回退。"""
    client, model = _client(cfg, model)
    if not client.available():
        return {"error": "未配置 API Key"}
    msgs = [{
        "role": "user",
        "content": [
            {"type": "text", "text": PROMPT + "\n这是第 %d 页。" % page_no},
            {"type": "image_url", "image_url": {"url": _png_data_url(png_path)}},
        ],
    }]
    client.timeout_override = 900
    try:
        raw = client.chat(msgs, model=model, temperature=0.0, max_tokens=0)
    except Exception as e:            # 模型不支持图片、超时、鉴权失败……
        return {"error": str(e)}
    data = _parse_json(raw)
    if not data:
        return {"error": "视觉模型返回的不是 JSON：" + (raw or "")[:120]}
    blocks = []
    for b in (data.get("blocks") or [])[:60]:
        if not isinstance(b, dict):
            continue
        role = str(b.get("role") or "other").strip()
        if role not in ROLES:
            role = "other"
        bb = b.get("bbox") or []
        try:
            box = [max(0.0, min(1.0, float(v))) for v in bb[:4]]
        except Exception:
            box = []
        if len(box) != 4:
            continue
        blocks.append({"role": role, "text": str(b.get("text") or "")[:40], "bbox": box})
    return {"page_type": str(data.get("page_type") or "content"), "blocks": blocks}


def classify_pdf(pdf_path: str, cfg: dict, model: str | None = None,
                 max_pages: int = 20, scale: float = 0.6,
                 out_dir: str = "", workers: int = 3) -> dict:
    """逐页判定角色（并发 + 磁盘缓存）。返回 {page_no: {...}, "_meta": {...}}。

    任何一页失败都不影响其它页；全部失败时 _meta.vision=False，调用方回退启发式。
    """
    import json as _json
    from concurrent.futures import ThreadPoolExecutor
    from . import pdf as pdfmod

    out = pathlib.Path(out_dir or (pathlib.Path(pdf_path).parent / "_third_pages"))
    out.mkdir(parents=True, exist_ok=True)
    cache_path = out / "vision.json"
    try:
        images = pdfmod.page_images(pdf_path, str(out), pages=None, scale=scale)
    except Exception as e:
        return {"_meta": {"vision": False, "error": "渲染页面失败：%s" % e}}
    if not images:
        return {"_meta": {"vision": False, "error": "渲染页面为空"}}

    cache: dict = {}
    if cache_path.exists():
        try:
            cache = _json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    todo = [(i + 1, img) for i, img in enumerate(images[:max_pages])]

    def work(item):
        pno, img = item
        key = "%d:%s" % (pno, pathlib.Path(img).name)
        if key in cache:
            return pno, cache[key], None
        r = classify_page(img, cfg, model=model, page_no=pno)
        if "error" in r:
            return pno, None, "p%d: %s" % (pno, r["error"])
        r["image"] = img
        return pno, r, None

    result: dict = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for pno, r, err in pool.map(work, todo):
            if err:
                errors.append(err)
                continue
            result[pno] = r
            cache["%d:%s" % (pno, pathlib.Path(r.get("image", "")).name)] = r

    try:
        cache_path.write_text(_json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    return {
        **result,
        "_meta": {"vision": bool(result), "ok_pages": len(result),
                  "total_pages": len(images), "errors": errors[:5],
                  "dir": str(out), "cache": str(cache_path)},
    }
