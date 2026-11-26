"""三审数据模型：页面 / 版面网格 / 文本框 / 文字段 / 问题项。

单位约定：
- 内部一律用 **pt**（PDF 与 IDML 都是 pt），对外展示转 mm（1pt = 25.4/72 mm）。
- 坐标原点：页面左上角为 (0,0)，x 向右、y 向下（与 PDF 一致）。
  IDML 是左下原点、Y 向上，解析器负责翻转；翻转后统一成"左上原点"。
"""
from __future__ import annotations

from dataclasses import dataclass, field

PT2MM = 25.4 / 72.0
MM2PT = 72.0 / 25.4


def pt2mm(v: float) -> float:
    return round(float(v) * PT2MM, 2)


def mm2pt(v: float) -> float:
    return float(v) * MM2PT


# ---------------------------------------------------------------- 版面网格
@dataclass
class GridInfo:
    """InDesign「版面网格」参数（IDML: LayoutGridDataInformation）。"""
    enabled: bool = False
    font: str = ""
    point_size: float = 0.0      # 字号（标准：9 点）
    char_aki: float = 0.0        # 字间距（标准：0 点）
    line_aki: float = 0.0        # 行间距（标准：7.5 点）
    h_scale: float = 100.0
    v_scale: float = 100.0

    @property
    def char_width(self) -> float:
        """字符间距 = 字号 × 水平缩放 + 字间距（标准：9 + 0 = 9 点）。"""
        return self.point_size * self.h_scale / 100.0 + self.char_aki

    @property
    def line_height(self) -> float:
        """行距 = 字号 × 垂直缩放 + 行间距（标准：9 + 7.5 = 16.5 点）。"""
        return self.point_size * self.v_scale / 100.0 + self.line_aki

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled, "font": self.font,
            "point_size": round(self.point_size, 3),
            "char_aki": round(self.char_aki, 3), "line_aki": round(self.line_aki, 3),
            "h_scale": round(self.h_scale, 2), "v_scale": round(self.v_scale, 2),
            "char_width": round(self.char_width, 3),
            "line_height": round(self.line_height, 3),
            "char_width_mm": pt2mm(self.char_width),
            "line_height_mm": pt2mm(self.line_height),
        }


@dataclass
class Margins:
    top: float = 0.0
    bottom: float = 0.0
    left: float = 0.0      # 内（装订侧）
    right: float = 0.0     # 外
    bleed: float = 0.0
    columns: int = 1
    gutter: float = 0.0

    def as_dict(self) -> dict:
        return {
            "top_mm": pt2mm(self.top), "bottom_mm": pt2mm(self.bottom),
            "left_mm": pt2mm(self.left), "right_mm": pt2mm(self.right),
            "bleed_mm": pt2mm(self.bleed), "columns": self.columns,
            "gutter_mm": pt2mm(self.gutter),
        }


@dataclass
class Frame:
    """页面上的一个框（文本框 / 图片框 / 图形）。"""
    self_id: str = ""
    kind: str = "text"            # text | image | graphic
    story: str = ""               # 文本框关联的故事流
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    page_index: int = -1
    overflow: bool = False         # IDML 的 Overflows / 文本溢流标记
    stroke_weight: float = 0.0     # 描边宽度

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass
class Style:
    """段落 / 字符样式定义（Resources/Styles.xml）。"""
    self_id: str = ""
    name: str = ""
    kind: str = "paragraph"       # paragraph | character
    font: str = ""
    point_size: float = 0.0
    leading: float = 0.0
    first_line_indent: float = 0.0
    tracking: float = 0.0
    fill_color: str = ""
    stroke_weight: float = -1.0   # -1 表示未设置
    stroke_color: str = ""


@dataclass
class ParagraphRun:
    """一段文字及其生效格式（已按 样式→段落覆盖→字符覆盖 层叠）。"""
    text: str = ""
    style_name: str = ""
    font: str = ""
    point_size: float = 0.0
    leading: float = 0.0
    first_line_indent: float = 0.0
    fill_color: str = ""
    stroke_weight: float = -1.0
    story: str = ""
    page_index: int = -1
    frame_id: str = ""


# ---------------------------------------------------------------- 文档
@dataclass
class PageInfo:
    index: int = 0
    width: float = 0.0
    height: float = 0.0
    margins: Margins = field(default_factory=Margins)
    grid: GridInfo = field(default_factory=GridInfo)
    frames: list[Frame] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "size_mm": [pt2mm(self.width), pt2mm(self.height)],
            "margins": self.margins.as_dict(),
            "grid": self.grid.as_dict(),
            "frames": len(self.frames),
        }


@dataclass
class DocLayout:
    """IDML 解析结果。"""
    source: str = ""
    pages: list[PageInfo] = field(default_factory=list)
    styles: dict[str, Style] = field(default_factory=dict)
    paragraphs: list[ParagraphRun] = field(default_factory=list)
    fonts: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "source": self.source,
            "pages": len(self.pages),
            "page_size_mm": ([pt2mm(self.pages[0].width), pt2mm(self.pages[0].height)]
                             if self.pages else []),
            "margins": self.pages[0].margins.as_dict() if self.pages else {},
            "grid": self.pages[0].grid.as_dict() if self.pages else {},
            "frames": sum(len(p.frames) for p in self.pages),
            "paragraphs": len(self.paragraphs),
            "styles": len(self.styles),
            "fonts": self.fonts[:12],
        }


@dataclass
class PdfSpan:
    """PDF 上一段格式一致的文字。"""
    text: str = ""
    font: str = ""
    size: float = 0.0
    color: tuple[int, int, int] = (0, 0, 0)
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    page_index: int = -1


@dataclass
class PdfPageInfo:
    index: int = 0
    width: float = 0.0
    height: float = 0.0
    spans: list[PdfSpan] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.spans)


@dataclass
class PdfDoc:
    source: str = ""
    pages: list[PdfPageInfo] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "source": self.source,
            "pages": len(self.pages),
            "size_mm": ([pt2mm(self.pages[0].width), pt2mm(self.pages[0].height)]
                        if self.pages else []),
            "spans": sum(len(p.spans) for p in self.pages),
            "chars": sum(len(p.text) for p in self.pages),
        }


# ---------------------------------------------------------------- 问题项
LEVEL_LABEL = {"major": "必改", "minor": "建议改", "info": "仅供核实"}


@dataclass
class Finding:
    """一条三审问题。"""
    rule: str = ""                     # 规则 id，如 grid.missing
    level: str = "minor"               # major | minor | info
    category: str = ""                 # 版面网格 / 边距 / 字形 / 页码 …
    message: str = ""                  # 给人看的问题描述
    standard: str = ""                 # 对应标准条款原文
    suggestion: str = ""               # 建议怎么改
    page: int = -1                     # 1 起的页码，-1 表示全文档
    bbox: list[float] = field(default_factory=list)   # [x0,y0,x1,y1] pt，画红框用
    evidence: dict = field(default_factory=dict)      # 实测值/标准值等

    def as_dict(self) -> dict:
        d = {
            "rule": self.rule, "level": self.level,
            "level_label": LEVEL_LABEL.get(self.level, self.level),
            "category": self.category, "message": self.message,
            "standard": self.standard, "suggestion": self.suggestion,
            "page": self.page,
        }
        if self.bbox:
            d["bbox"] = [round(v, 2) for v in self.bbox]
        if self.evidence:
            d["evidence"] = self.evidence
        return d


@dataclass
class ReviewReport:
    """一次三审的完整结果。"""
    findings: list[Finding] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def counts(self) -> dict:
        c = {"major": 0, "minor": 0, "info": 0}
        for f in self.findings:
            c[f.level] = c.get(f.level, 0) + 1
        c["total"] = len(self.findings)
        return c

    def as_dict(self) -> dict:
        return {
            "findings": [f.as_dict() for f in self.findings],
            "counts": self.counts(),
            "stats": self.stats,
            "notes": self.notes,
        }
