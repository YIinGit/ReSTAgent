"""视觉角色判定：多页拼版模式（省调用、省 token）。

思路：
把 2（或 3）个页面横向拼成一张图，让模型一次判完，再按「该页在拼版图里的像素矩形」
把归一化 bbox 换算回单页归一化 bbox。这样 10 页只需 5 次调用，token 也显著减少。

与逐页模式（vision.classify_pdf）的差异只在「打包方式」，输出结构完全一致：
    {页码: {"page_type":..., "blocks":[{"role","text","bbox"}], "image":...}, "_meta": {...}}
因此 rules.py 里的 _role_from_vision() 不用改。

干净回退：拼版渲染/请求/解析任一环节失败，调用方（service）自动改用逐页模式或启发式。
"""
from __future__ import annotations

import json
import pathlib

from .vision import ROLES, _client, _parse_json, _png_data_url

PROMPT_HEAD = """你在做杂志排版终审，看到的是**多页拼版预览图**（横向并排，页面之间是白缝）。

这次拼版包含 %d 个页面：%s
每一页左上角有黑色小字标注页码（如 P3），请以标注为准，不要靠位置猜。

请按页输出，识别每页的文字区域并判定角色。

角色只能取：author / title / subtitle / body / caption / sidebar / editbar / decoration / page_number / other

只输出 JSON，不要解释、不要代码块：
{"pages":[{"page":3,"page_type":"cover|blank|content","blocks":[{"role":"body","text":"开头15字","bbox":[x,y,w,h]}]}]}

bbox 规则（重要）：用**整张拼版图**的归一化坐标（0~1，原点在整张图左上角，x 向右、y 向下），
不是单页坐标。每页最多 30 个区域。文字看不清就给 other。
"""


def _sheet_prompt(pages: list[int]) -> str:
    return PROMPT_HEAD % (len(pages), "、".join("P%d" % p for p in pages))


def _to_page_bbox(bbox: list[float], rect: list[int], sheet_size: list[int]) -> list[float] | None:
    """整图归一化 bbox → 单页归一化 bbox。超出该页的部分裁掉。"""
    sw, sh = max(1, sheet_size[0]), max(1, sheet_size[1])
    dx, dy, pw, ph = rect
    x, y, w, h = bbox
    x0 = (x * sw - dx) / max(1, pw)
    y0 = (y * sh - dy) / max(1, ph)
    x1 = ((x + w) * sw - dx) / max(1, pw)
    y1 = ((y + h) * sh - dy) / max(1, ph)
    x0, x1 = max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))
    y0, y1 = max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))
    if x1 - x0 < 0.002 or y1 - y0 < 0.002:
        return None
    return [x0, y0, x1 - x0, y1 - y0]


def classify_sheet(sheet: dict, cfg: dict, model: str | None = None) -> dict:
    """判定一张拼版图。返回 {"pages": {页码: {...}}, "error": ...}。"""
    client, model = _client(cfg, model)
    if not client.available():
        return {"error": "未配置 API Key"}
    pages = list(sheet.get("pages") or [])
    rects = sheet.get("boxes") or {}
    size = sheet.get("size") or [0, 0]
    if not pages:
        return {"error": "拼版图没有页面"}

    msgs = [{
        "role": "user",
        "content": [
            {"type": "text", "text": _sheet_prompt(pages)},
            {"type": "image_url", "image_url": {"url": _png_data_url(sheet["image"])}},
        ],
    }]
    try:
        raw = client.chat(msgs, model=model, temperature=0.0, max_tokens=0)
    except Exception as e:
        return {"error": str(e)}
    data = _parse_json(raw)
    if not data:
        return {"error": "视觉模型返回的不是 JSON：" + (raw or "")[:120]}

    got = data.get("pages")
    if not isinstance(got, list):
        # 兼容模型只返回单页结构的情况
        got = [{"page": pages[0], "page_type": data.get("page_type"), "blocks": data.get("blocks")}]

    out: dict[int, dict] = {}
    for item in got[:len(pages)]:
        if not isinstance(item, dict):
            continue
        pno = item.get("page")
        try:
            pno = int(pno)
        except Exception:
            pno = None
        if pno not in pages:
            # 页码标错时按顺序兜底
            pno = pages[len(out)] if len(out) < len(pages) else None
        if pno is None:
            continue
        rect = rects.get(pno) or rects.get(str(pno))
        blocks = []
        for b in (item.get("blocks") or [])[:60]:
            if not isinstance(b, dict):
                continue
            role = str(b.get("role") or "other").strip()
            if role not in ROLES:
                role = "other"
            bb = b.get("bbox") or []
            try:
                box = [max(0.0, min(1.0, float(v))) for v in bb[:4]]
            except Exception:
                continue
            if len(box) != 4:
                continue
            if rect:
                box = _to_page_bbox(box, list(rect), list(size))
                if not box:
                    continue
            blocks.append({"role": role, "text": str(b.get("text") or "")[:40],
                           "bbox": box})
        out[pno] = {"page_type": str(item.get("page_type") or "content"),
                    "blocks": blocks, "sheet": sheet["image"]}
    if not out:
        return {"error": "拼版结果里没有任何页面"}
    return {"pages": out}


def classify_pdf_sheets(pdf_path: str, cfg: dict, model: str | None = None,
                        per_sheet: int = 2, scale: float = 0.32,
                        max_pages: int = 40, out_dir: str = "",
                        workers: int = 3, on_progress=None) -> dict:
    """多页拼版模式：返回与 vision.classify_pdf 相同的结构（页码 → 页结果 + _meta）。

    on_progress(msg, pct) 可选：每完成一张拼版上报一次。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from . import pdf as pdfmod

    out = pathlib.Path(out_dir or (pathlib.Path(pdf_path).parent / "_third_sheets"))
    out.mkdir(parents=True, exist_ok=True)
    cache_path = out / "vision_sheets.json"
    try:
        sheets = pdfmod.page_sheets(pdf_path, str(out), scale=scale,
                                    per_sheet=per_sheet, max_pages=max_pages)
    except Exception as e:
        return {"_meta": {"vision": False, "error": "拼版渲染失败：%s" % e}}
    if not sheets:
        return {"_meta": {"vision": False, "error": "拼版渲染为空"}}

    cache: dict = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    def key_of(sh: dict) -> str:
        return "v1:%s:%s" % (pathlib.Path(sh["image"]).name, ",".join(map(str, sh["pages"])))

    def work(sh):
        k = key_of(sh)
        if k in cache:
            return sh, cache[k], None
        r = classify_sheet(sh, cfg, model=model)
        if "error" in r:
            return sh, None, "sheet %s: %s" % (sh["pages"], r["error"])
        return sh, r["pages"], None

    result: dict = {}
    errors: list[str] = []
    done = 0
    total_sheets = max(1, len(sheets))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(work, sh): sh for sh in sheets}
        for fut in as_completed(futures):
            sh, pages, err = fut.result()
            done += 1
            if err:
                errors.append(err)
            else:
                for pno, pr in pages.items():
                    pr.setdefault("image", sh["image"])
                    result[pno] = pr
                cache[key_of(sh)] = pages
                try:
                    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
                except Exception:
                    pass
            if on_progress:
                try:
                    on_progress("已判定 %d/%d 张拼版（页 %s）"
                                % (done, total_sheets, "、".join(map(str, sh["pages"]))),
                                int(100 * done / total_sheets))
                except Exception:
                    pass

    try:
        cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    total = len(sheets) * per_sheet
    return {
        **result,
        "_meta": {"vision": bool(result), "ok_pages": len(result), "total_pages": total,
                  "sheets": len(sheets), "per_sheet": per_sheet,
                  "errors": errors[:5], "dir": str(out), "cache": str(cache_path),
                  "mode": "sheets"},
    }
