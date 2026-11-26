"""三审规则引擎：以「版面网格」为准（C），并**按文字角色分别判定**。

关键设计（v2）：
1. 先给每段文字判定角色：正文 / 节标题 / 副节标题 / 作者 / 图注小框 / 编辑栏 / 装饰样张。
   不同角色适用不同标准，拿「正文 9 点」去卡大标题和装饰字必然误报。
2. 版面网格本身按参数校验（字号、字间距、行间距、缩放、栏数、栏距）。
3. **忽略标准里的页边距数值**——网格起点会造成边距数值不一致，且用户已确认可忽略。
4. 报告分「建议改 / 待核实」，拿不准的一律进待核实，交人工判断，不臆断。
"""
from __future__ import annotations

from dataclasses import dataclass

from . import standard as std_mod
from .model import (DocLayout, Finding, PdfDoc, PdfPageInfo, ReviewReport, pt2mm)

# 角色
ROLE_LABEL = {
    "body": "正文", "heading": "标题", "subheading": "副节标题", "author": "作者",
    "caption": "图注/小框", "editbar": "编辑栏", "display": "首字下沉/装饰元素",
    "unknown": "未识别",
}

# 《排版标准 260920》——按角色拆分
ROLE_STD = {
    "body": {"size": 9.0, "leading": 16.5, "indent": 6.4, "font": "正文衬线"},
    "heading": {"size": 12.0, "leading": 16.5, "font": "节标题衬线"},
    "subheading": {"size": 10.0, "leading": 16.5, "font": "图片注释字体"},
    "author": {"size": 12.0, "font": "节标题衬线"},
    "caption": {"size": 7.5, "leading": 13.0, "indent": 5.5, "font": "图片注释字体"},
    "editbar": {"size": 6.0, "font": "正文无衬线"},
}


@dataclass
class Standard:
    page_w_mm: float = 185.0
    page_h_mm: float = 260.0
    bleed_mm: float = 3.0
    columns: int = 2
    gutter_mm: float = 8.0
    grid_font: str = "正文衬线"
    grid_point_size: float = 9.0
    grid_char_aki: float = 0.0
    grid_line_aki: float = 7.5
    grid_char_width: float = 9.0
    grid_line_height: float = 16.5
    chars_per_column: int = 22
    lines_per_page: int = 38
    page_tol_mm: float = 0.5
    value_tol: float = 0.2
    size_tol: float = 0.6          # 字号容差（点）
    body_size: float = 9.0         # 正文角色标准字号（与网格字号分开配置）

    @classmethod
    def from_dict(cls, d: dict) -> "Standard":
        """从《排版要求》规则库（standard.load() 的结果）构造。缺项自动取默认。"""
        d = d or {}
        page = d.get("page") or {}
        grid = d.get("grid") or {}
        tol = d.get("tolerance") or {}
        roles = d.get("roles") or {}

        def f(v, default):
            try:
                return float(v)
            except (TypeError, ValueError):
                return default

        body_size = f((roles.get("body") or {}).get("size"), 9.0)
        return cls(
            page_w_mm=f(page.get("width_mm"), 185.0),
            page_h_mm=f(page.get("height_mm"), 260.0),
            bleed_mm=f(page.get("bleed_mm"), 3.0),
            columns=int(f(page.get("columns"), 2)),
            gutter_mm=f(page.get("gutter_mm"), 8.0),
            grid_font=str(grid.get("font") or "正文衬线"),
            grid_point_size=f(grid.get("point_size"), 9.0),
            body_size=body_size,
            grid_char_aki=f(grid.get("char_aki"), 0.0),
            grid_line_aki=f(grid.get("line_aki"), 7.5),
            grid_char_width=f(grid.get("char_width"), 9.0),
            grid_line_height=f(grid.get("line_height"), 16.5),
            chars_per_column=int(f(grid.get("chars_per_column"), 22)),
            lines_per_page=int(f(grid.get("lines_per_page"), 38)),
            page_tol_mm=f(page.get("tolerance_mm"), 0.5),
            value_tol=f(grid.get("tolerance"), 0.2),
            size_tol=f(tol.get("size_pt"), 0.6),
        )


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF) or (0x3400 <= o <= 0x4DBF) or (0xF900 <= o <= 0xFAFF)


def _cjk_count(s: str) -> int:
    return sum(1 for c in s if _is_cjk(c))


# ------------------------------------------------------------------ 角色判定
_FONT_ROLE_HINTS = [
    (("图片注释", "图注", "注释"), "caption"),
    (("编辑栏", "编辑"), "editbar"),
    (("正文",), "body"),
    # 「标题几何主义」是首字下沉/装饰用字，不是标题——必须先于「标题」匹配
    (("几何主义", "首字下沉", "装饰"), "display"),
    (("副节", "副标题"), "subheading"),
    (("节标题", "标题"), "heading"),
]


def role_from_font(font: str, size: float = 0.0) -> str:
    """按字体族名判角色（本社字体族直接以用途命名：正文衬线/节标题衬线/图片注释字体…）。"""
    f = (font or "").strip()
    for keys, role in _FONT_ROLE_HINTS:
        if any(k in f for k in keys):
            return role
    return ""


def role_from_size(size: float, body_size: float, page_no: int | None = None,
                   author_page: int | None = None) -> str:
    """按字号推测角色（PDF 里的字体名是真实字体，只能靠字号）。

    作者名只在「正文起始页」才认；其它页 11–13 点按标题处理，避免把节标题当作者。
    """
    if size >= 14.0:
        return "display"          # 大字号：标题字/装饰，不套正文标准
    if abs(size - body_size) <= 0.6:
        return "body"
    if size <= 6.5:
        return "editbar"
    if 7.0 <= size <= 8.2:
        return "caption"
    if 9.4 <= size <= 10.6:
        return "subheading"
    if 11.0 <= size <= 13.0:
        if author_page is None or page_no == author_page:
            return "author"
        return "heading"
    return "unknown"


def first_body_page(pdf: "PdfDoc", body_size: float, min_chars: int = 60) -> int:
    """正文起始页：第一页真正有大段正文的页（跳过空白页/纯封面页）。

    跨页封面会在前两页没有正文，天然被跳过；页码从 1 起。
    """
    for pg in pdf.pages:
        n = sum(len(s.text.strip()) for s in pg.spans
                if abs(s.size - body_size) <= 0.6 and _cjk_count(s.text) > 0)
        if n >= min_chars:
            return pg.index + 1
    return 1


_VISION_ROLE_MAP = {
    "body": "body", "author": "author", "caption": "caption",
    "title": "heading", "subtitle": "subheading",
    "sidebar": "editbar", "editbar": "editbar",
    "decoration": "display", "page_number": "info", "other": "unknown",
}


def _role_from_vision(span, page_no: int, vision: dict, page_w: float, page_h: float) -> str | None:
    """用视觉模型给的归一化 bbox，判断这个 span 属于哪个角色块。命中返回角色，否则 None。"""
    page = vision.get(page_no) or {}
    blocks = page.get("blocks") or []
    if not blocks:
        return None
    cx = ((span.x0 + span.x1) / 2.0) / max(1.0, page_w)
    cy = ((span.y0 + span.y1) / 2.0) / max(1.0, page_h)
    hits = []
    for b in blocks:
        x, y, w, h = b["bbox"]
        if x - 0.01 <= cx <= x + w + 0.01 and y - 0.01 <= cy <= y + h + 0.01:
            hits.append(b)
    if not hits:
        return None
    # 取面积最小的命中块：大区域常把相邻图注/边栏一并框进去
    hits.sort(key=lambda b: b["bbox"][2] * b["bbox"][3])
    return _VISION_ROLE_MAP.get(hits[0].get("role") or "other", "unknown")


def _body_size(pdf: PdfDoc) -> float:
    weight: dict[float, int] = {}
    for pg in pdf.pages:
        for s in pg.spans:
            t = s.text.strip()
            if t:
                k = round(s.size, 1)
                weight[k] = weight.get(k, 0) + len(t)
    if not weight:
        return 9.0
    return max(weight.items(), key=lambda kv: kv[1])[0]


class _Ctx:
    def __init__(self, doc, pdf, std, vision=None):
        self.doc, self.pdf, self.std = doc, pdf, std
        self.vision = vision or {}
        self.rep = ReviewReport()
        self.notes: list[str] = []

    def add(self, rule, level, category, message, standard="", suggestion="",
            page=-1, bbox=None, evidence=None):
        self.rep.findings.append(Finding(
            rule=rule, level=level, category=category, message=message,
            standard=standard, suggestion=suggestion, page=page,
            bbox=list(bbox or []), evidence=dict(evidence or {})))

    def note(self, m):
        self.notes.append(m)


# ------------------------------------------------------------------ 页面 / 网格
def _check_page(ctx: _Ctx):
    std, doc = ctx.std, ctx.doc
    if not doc or not doc.pages:
        return
    p = doc.pages[0]
    w, h = pt2mm(p.width), pt2mm(p.height)
    if abs(w - std.page_w_mm) > std.page_tol_mm or abs(h - std.page_h_mm) > std.page_tol_mm:
        ctx.add("page.size", "major", "页面",
                "页面尺寸 %.1f × %.1f mm，标准 %.0f × %.0f mm。" % (w, h, std.page_w_mm, std.page_h_mm),
                "页面大小：185mm*260mm", "把页面尺寸改为 185×260mm。",
                evidence={"actual_mm": [w, h], "standard_mm": [std.page_w_mm, std.page_h_mm]})
    else:
        ctx.add("page.size", "info", "页面", "页面尺寸 %.1f × %.1f mm，符合标准。" % (w, h))

    bleed = pt2mm(p.margins.bleed)
    if abs(bleed - std.bleed_mm) > 0.2:
        ctx.add("page.bleed", "major", "页面", "出血 %.2f mm，标准四边 %.0f mm。"
                % (bleed, std.bleed_mm), "出血：上下内外均为3mm",
                "在「文档设置」里把出血改为 3mm。", evidence={"actual_mm": bleed})
    else:
        ctx.add("page.bleed", "info", "页面", "出血 %.2f mm，符合标准。" % bleed)

    m = p.margins
    if m.columns != std.columns:
        ctx.add("grid.columns", "major", "版面网格",
                "分栏 %d 栏，标准 %d 栏。" % (m.columns, std.columns), "栏数2", "改为两栏。")
    gutter = pt2mm(m.gutter)
    if abs(gutter - std.gutter_mm) > 0.2:
        ctx.add("grid.gutter", "major", "版面网格",
                "栏间距 %.2f mm，标准 %.0f mm。" % (gutter, std.gutter_mm), "栏间距8mm",
                "把栏间距设为 8mm。")
    ctx.note("按最新确认：页边距数值与每页行数不参与判定（网格起点会造成边距不一致，网格本身已被确认正确）。")


def _check_grid(ctx: _Ctx):
    std, doc = ctx.std, ctx.doc
    if not doc or not doc.pages:
        return
    grid = doc.pages[0].grid
    if not grid.enabled:
        ctx.add("grid.missing", "major", "版面网格",
                "文档未启用「版面网格」。", "版面网格：字体正文衬线 / 9 点 / 行距 16.5 点",
                "在「版面网格」对话框中按标准设置。")
        return
    ev = {"actual": grid.as_dict()}
    if grid.font and std.grid_font not in grid.font:
        ctx.add("grid.font", "minor", "版面网格",
                "网格字体「%s」，标准「%s」。" % (grid.font, std.grid_font),
                "字体正文衬线", "把版面网格字体改为「正文衬线」。", evidence=ev)
    if abs(grid.point_size - std.grid_point_size) > std.value_tol:
        ctx.add("grid.point_size", "major", "版面网格",
                "网格字号 %.2f 点，标准 %.1f 点。" % (grid.point_size, std.grid_point_size),
                "大小9点", "改为 9 点。", evidence=ev)
    if abs(grid.char_aki - std.grid_char_aki) > std.value_tol:
        ctx.add("grid.char_aki", "minor", "版面网格",
                "字间距 %.2f 点，标准 %.1f 点。" % (grid.char_aki, std.grid_char_aki),
                "字间距0点，字符间距9点", "改为 0 点。", evidence=ev)
    if abs(grid.line_aki - std.grid_line_aki) > std.value_tol:
        ctx.add("grid.line_aki", "major", "版面网格",
                "行间距 %.2f 点，标准 %.1f 点。" % (grid.line_aki, std.grid_line_aki),
                "行间距7.5点，行距16.5点", "改为 7.5 点。", evidence=ev)
    if abs(grid.h_scale - 100.0) > 0.5 or abs(grid.v_scale - 100.0) > 0.5:
        ctx.add("grid.scale", "minor", "版面网格",
                "网格缩放 %.0f%% / %.0f%%，标准 100%% / 100%%。" % (grid.h_scale, grid.v_scale),
                "垂直100%，水平100%", "改回 100%。", evidence=ev)
    else:
        ctx.add("grid.scale", "info", "版面网格",
                "网格参数 %.0f 点 / 字间距 %.1f / 行间距 %.1f / 缩放 100%% / 计 %d 字 %d 行，符合标准。"
                % (grid.point_size, grid.char_aki, grid.line_aki,
                   std.chars_per_column, std.lines_per_page), evidence=ev)


# ------------------------------------------------------------------ 文字（按角色）
def _line_groups(page: PdfPageInfo) -> list[list]:
    spans = [s for s in page.spans if s.text.strip()]
    spans.sort(key=lambda s: (round((s.y0 + s.y1) / 2.0, 1), s.x0))
    groups: list[list] = []
    for s in spans:
        cy = (s.y0 + s.y1) / 2.0
        if groups:
            g = groups[-1]
            gy = sum((t.y0 + t.y1) / 2.0 for t in g) / len(g)
            if abs(cy - gy) <= max(2.0, (s.y1 - s.y0) * 0.5):
                g.append(s)
                continue
        groups.append([s])
    return groups


def _check_text(ctx: _Ctx):
    std, pdf, doc = ctx.std, ctx.pdf, ctx.doc
    if not pdf or not pdf.pages:
        ctx.note("未提供 PDF：跳过字体、字号、越界等「读者所见」检查。")
        return
    body_size = _body_size(pdf)
    author_page = first_body_page(pdf, body_size)
    vision_ok = bool((ctx.vision or {}).get("_meta", {}).get("vision"))
    if vision_ok:
        ctx.note("角色判定：使用视觉模型（第 %d 页起为正文起始页，作者名据此定位）。" % author_page)
    else:
        err = ((ctx.vision or {}).get("_meta") or {}).get("error")
        ctx.note("角色判定：未用视觉模型（%s），改用字号与字体族名启发式。"
                 % (err or "未启用"))

    def role_of(span, pno: int, page_w: float, page_h: float) -> str:
        fallback = role_from_size(span.size, body_size, pno, author_page)
        if not vision_ok:
            return fallback
        r = _role_from_vision(span, pno, ctx.vision, page_w, page_h)
        if not r or r == "unknown":
            return fallback
        # 视觉说「正文」但字号明显不是正文，以字号为准（视觉的大框容易盖住图注）
        if r == "body" and abs(span.size - body_size) > 1.2:
            return fallback
        # 视觉说「图注」但字号明显大于图注，也以字号为准
        if r == "caption" and span.size > 9.0:
            return fallback
        return r

    role_stats: dict[str, int] = {}

    mixed, size_bad, overflow, single = 0, 0, 0, 0
    for pg in pdf.pages:
        pno = pg.index + 1
        for line in _line_groups(pg):
            text = "".join(s.text for s in line).strip()
            if not text:
                continue
            roles = [role_of(s, pno, pg.width, pg.height) for s in line]
            for r in roles:
                role_stats[r] = role_stats.get(r, 0) + 1

            # 字体一致性：以「字号≈正文」为准（不依赖视觉判定，视觉结果每次会有细微差异），
            # 但含装饰/大标题字符的行跳过。
            cjk_spans = [s for s in line if _cjk_count(s.text) > 0]
            body_sized = [s for s in cjk_spans if abs(s.size - body_size) <= 0.6]
            if (len(body_sized) >= 2 and len(body_sized) == len(cjk_spans)
                    and "display" not in roles and mixed < 10):
                fonts = {s.font for s in body_sized if s.font}
                if len(fonts) > 1:
                    mixed += 1
                    ctx.add("type.mixed_font", "minor", "字形",
                            "第 %d 页正文同一行出现 %d 种中文字体：%s"
                            % (pno, len(fonts), "、".join(sorted(fonts))),
                            "字体混用错误：同级信息不应出现字体混用",
                            "请核实是否特殊排版；若否，统一字体。",
                            page=pno,
                            bbox=[min(s.x0 for s in body_sized), min(s.y0 for s in body_sized),
                                  max(s.x1 for s in body_sized), max(s.y1 for s in body_sized)],
                            evidence={"fonts": sorted(fonts), "text": text[:40]})

            # 字号：只在「正文」角色上判，且与网格字号 9 点比
            for s in line:
                if role_of(s, pno, pg.width, pg.height) != "body":
                    continue
                if _cjk_count(s.text) >= 2 and abs(s.size - std.body_size) > std.size_tol and size_bad < 15:
                    size_bad += 1
                    ctx.add("type.size_diff", "minor", "字形",
                            "第 %d 页正文有 %.1f 点中文，标准正文 9 点：%s"
                            % (pno, s.size, s.text.strip()[:20]),
                            "正文：9 点，行距 16.5 点", "改回 9 点。",
                            page=pno, bbox=[s.x0, s.y0, s.x1, s.y1],
                            evidence={"size": round(s.size, 1)})

            # 单字成行：仅正文
            if len(text) == 1 and _is_cjk(text) and "body" in roles and single < 20:
                single += 1
                s = line[0]
                ctx.add("text.single_char_line", "minor", "页面排列",
                        "第 %d 页出现单字成行：「%s」" % (pno, text),
                        "单字成行/单句成页", "微调文字或文本框宽度。",
                        page=pno, bbox=[s.x0, s.y0, s.x1, s.y1])

        # 越界：只查正文/图注，装饰与标题不查
        if doc and doc.pages and pno <= len(doc.pages):
            dp = doc.pages[pno - 1]
            off = dp.margins.bleed
            frames = [f for f in dp.frames if f.kind == "text"]
            if frames:
                left = min(f.x0 for f in frames) + off
                right = max(f.x1 for f in frames) + off
                top = min(f.y0 for f in frames) + off
                bottom = max(f.y1 for f in frames) + off
            else:
                left, right = dp.margins.left + off, dp.width - dp.margins.right + off
                top, bottom = dp.margins.top + off, dp.height - dp.margins.bottom + off
            for s in pg.spans:
                t = s.text.strip()
                if not t or _cjk_count(t) == 0:
                    continue
                if role_of(s, pno, pg.width, pg.height) not in ("body", "caption"):
                    continue
                if (s.x0 < left - 8 or s.x1 > right + 8 or s.y0 < top - 8 or s.y1 > bottom + 8) and overflow < 10:
                    overflow += 1
                    ctx.add("content.overflow", "info", "页面排列",
                            "第 %d 页有正文/图注可能越出内容区：%s" % (pno, t[:20]),
                            "实义文字不得越出边距（重复纹样、装饰元素可越界）",
                            "请核实：若是实义文字，移回内容区；若是装饰/居中特排，忽略。",
                            page=pno, bbox=[s.x0, s.y0, s.x1, s.y1])

    ctx.rep.stats.update({"body_size": body_size, "mixed_font_lines": mixed,
                          "role_spans": role_stats})


def _check_stroke(ctx: _Ctx):
    """描边：按字体族名判角色，只对正文/图注报错。"""
    doc = ctx.doc
    if not doc or not doc.paragraphs:
        return
    n = 0
    for run in doc.paragraphs:
        if not (run.stroke_weight and run.stroke_weight > 0.02):
            continue
        role = role_from_font(run.font) or "unknown"
        if role in ("display", "heading", "editbar"):
            continue        # 装饰性/标题描边可能是有意设计
        if _cjk_count(run.text) == 0:
            continue        # 纯西文样张（如字体展示页）不判描边
        n += 1
        ctx.add("type.stroke", "minor", "字形",
                "%s文字描边 %.2f 点：%s" % (ROLE_LABEL.get(role, ""), run.stroke_weight,
                                          run.text.strip()[:24]),
                "描边错误：必须直接禁用描边，而非设为 0.00pt",
                "在字符面板把描边设为「无」。",
                page=(run.page_index + 1) if run.page_index >= 0 else -1,
                evidence={"role": role, "stroke_pt": round(run.stroke_weight, 3)})
        if n >= 10:
            break
    ctx.rep.stats["stroke_runs"] = n


def check(doc: DocLayout | None = None, pdf: PdfDoc | None = None,
          std: Standard | None = None, vision: dict | None = None,
          rules: dict | None = None) -> ReviewReport:
    """rules 为《排版要求》规则开关（standard.load()["rules"]）；关闭的规则不进入报告。"""
    data = std_mod.load()
    if std is None:
        std = Standard.from_dict(data)
    if rules is None:
        rules = data.get("rules") or {}

    ctx = _Ctx(doc, pdf, std, vision)
    _check_page(ctx)
    _check_grid(ctx)
    _check_stroke(ctx)
    _check_text(ctx)
    ctx.rep.stats["pages_idml"] = len(doc.pages) if doc else 0
    ctx.rep.stats["pages_pdf"] = len(pdf.pages) if pdf else 0
    ctx.rep.notes = ctx.notes
    if rules:
        off = sorted(set(f.rule for f in ctx.rep.findings if not std_mod.rule_on(rules, f.rule)))
        if off:
            ctx.rep.findings = [f for f in ctx.rep.findings if std_mod.rule_on(rules, f.rule)]
            ctx.rep.notes.append("已按《排版要求》关闭以下规则：%s" % "、".join(off))
        ctx.rep.stats["rules_on"] = sum(1 for v in rules.values() if v)
        ctx.rep.stats["rules_off"] = sum(1 for v in rules.values() if not v)
    return ctx.rep
