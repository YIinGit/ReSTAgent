"""按 IDML 的框几何 + 字体族名判定段落角色（不依赖大模型，确定性）。

为什么优先用几何：
- 字号和字体族名只能粗分，遇到"节标题 12pt / 作者 12pt"就分不开；
- 但「框的位置和尺寸」是排版时留下的确定信息：
  · 贴着页面底部的窄条小字 = 编辑栏
  · 高度很矮（≤3 行）的小文字块 = 图注/注释/小框
  · 宽度不到正文栏宽一半 = 边栏
  · 正文起始页上、短、居中、在正文之上的 11–13 点 = 作者
  · 段首字数达到数倍字号的 = 首字下沉（装饰）
- 视觉模型（vision.py）给出结果时优先用视觉；没有时用本模块；都没有再用字号启发式。
"""
from __future__ import annotations

from .model import DocLayout, Frame, ParagraphRun, pt2mm

# 与 rules.ROLE_LABEL 保持一致
_HINT_ORDER = [
    (("图片注释", "图注", "注释"), "caption"),
    (("编辑栏", "编辑"), "editbar"),
    (("正文",), "body"),
    (("几何主义", "首字下沉", "装饰"), "display"),
    (("副节", "副标题"), "subheading"),
    (("节标题", "标题"), "heading"),
]


def role_from_font(font: str) -> str:
    f = (font or "").strip()
    for keys, role in _HINT_ORDER:
        if any(k in f for k in keys):
            return role
    return ""


def _mode_size(runs: list[ParagraphRun]) -> float:
    w: dict[float, int] = {}
    for r in runs:
        t = (r.text or "").strip()
        if t:
            k = round(r.point_size or 0, 1)
            w[k] = w.get(k, 0) + len(t)
    if not w:
        return 9.0
    return max(w.items(), key=lambda kv: kv[1])[0]


def first_body_page(doc: DocLayout, body_size: float, min_chars: int = 60) -> int:
    """正文起始页（1 起）：第一页有足量正文字数的页，跳过空白页与封面页。"""
    per: dict[int, int] = {}
    for r in doc.paragraphs:
        if r.page_index < 0:
            continue
        if abs((r.point_size or 0) - body_size) <= 0.6:
            per[r.page_index + 1] = per.get(r.page_index + 1, 0) + len((r.text or "").strip())
    for pno in sorted(per):
        if per[pno] >= min_chars:
            return pno
    return 1


def classify(doc: DocLayout | None) -> dict[int, str]:
    """返回 {段落下标: 角色}。"""
    if doc is None or not doc.paragraphs:
        return {}
    frames: dict[str, tuple] = {}
    for pg in doc.pages:
        for f in pg.frames:
            frames[f.self_id] = (pg, f)
    body_size = _mode_size(doc.paragraphs)
    author_page = first_body_page(doc, body_size)

    out: dict[int, str] = {}
    for i, run in enumerate(doc.paragraphs):
        text = (run.text or "").strip()
        if not text:
            out[i] = "unknown"
            continue
        hint = role_from_font(run.font)
        pno = run.page_index + 1 if run.page_index >= 0 else 0
        pg_f = frames.get(run.frame_id)
        size = run.point_size or 0.0

        geo = ""
        if pg_f:
            page, fr = pg_f
            content_w = page.width - page.margins.left - page.margins.right
            h_mm = pt2mm(fr.height)
            near_bottom = (page.height - fr.y1) < 70 and fr.y0 > page.height * 0.8
            if near_bottom and size <= 7.5 and h_mm <= 25:
                geo = "editbar"
            elif size <= 8.5 and h_mm <= 22:
                geo = "caption"
            elif content_w > 0 and fr.width < content_w * 0.45 and size <= 9.5:
                geo = "caption"

        # 字体族名优先，但「正文」这个族名不能压过几何判定（编辑栏就是正文无衬线 6 点）
        if hint and not (hint == "body" and geo in ("editbar", "caption")):
            out[i] = hint
            continue
        if geo:
            out[i] = geo
            continue
        # 首字下沉/装饰：段首字号远大于正文
        if size >= 20:
            out[i] = "display"
            continue
        # 作者：正文起始页、11–13 点、短文本
        if 11.0 <= size <= 13.0:
            if pno == author_page and len(text) <= 24:
                out[i] = "author"
            else:
                out[i] = "heading"
            continue
        if 9.4 <= size <= 10.6:
            out[i] = "subheading"
            continue
        if abs(size - body_size) <= 0.6:
            out[i] = "body"
            continue
        out[i] = "unknown"
    return out
