"""IDML 解析：把 InDesign 交换格式（.idml，本质是 zip + XML）读成三审模型。

解析内容
--------
- 文档级参数：页面尺寸 / 出血 / 页边距 / 分栏 / 版面网格（``Resources/Preferences.xml``）。
- 页面：``designmap.xml`` 中 ``<idPkg:Spread>`` 的文档顺序 → 每个 spread 里的 ``<Page>``
  依次编号（一个 spread 通常两个 Page：左页、右页）。
- 框：Spreads 与 MasterSpreads 里的 TextFrame / Rectangle / Oval / Polygon / GraphicLine，
  用 ``ItemTransform`` + ``PathGeometry/PathPoint`` 求包围盒。
- 样式：``Resources/Styles.xml`` 的 ParagraphStyle / CharacterStyle，按 ``BasedOn`` 继承。
- 段落：``Stories/*.xml`` 的 ParagraphStyleRange → CharacterStyleRange → Content，
  并按「段落样式 → 段落覆盖 → 字符覆盖」层叠出生效格式。

坐标约定（重要）
----------------
``model.py`` 要求**页面左上原点、Y 向下**（与 PDF 一致）。IDML 的 item 坐标经
``ItemTransform`` 变换后落在 spread 坐标系里；InDesign 的 spread/page 坐标本身就是
**Y 轴向下的**（和 InDesign 脚本的 geometricBounds 一致），页面 ``ItemTransform`` 的
``ty`` 常常是 ``-页面高度/2``（spread 原点在竖直中线附近）。因此把框换到"页面左上原点"
只需减去页面矩形自身的原点::

    x_page    = x_spread - page.x0      # page.x0 = page_tx（左页为 -页宽，右页为 0）
    y_page_top = y_spread - page.y0     # page.y0 = page_ty（本样张为 -页面高度/2）

**不要再做一次 ``y = page_height - y``**：那会把整页内容上下镜像。样张
``samples/字体.idml`` 已用 PDF 文字坐标交叉验证：第 4 页正文首行 PDF 换算后 y≈248.1pt，
与该页正文框上边 247.98pt 完全对齐；若按"IDML 是左下原点、Y 向上"再翻转一次，同一行
会落到 y≈489pt，与 PDF 对不上。

已知取不到 / 不精确的信息
--------------------------
- 行断、基线、溢流由 InDesign 排版引擎决定，IDML 不记录；``Frame.overflow`` 只能保守置
  False（IDML 的 TextFrame 没有 overflow 标志）。
- 段落里的字符级混排（同一段多种字体/字号）只保留"第一个有实质属性的 CharacterStyleRange"
  作为该段生效格式（模型 ``ParagraphRun`` 每段只有一个格式）。
- ``AppliedFont`` 可能是 `CompositeFont/$ID/正文衬线` 这类资源引用，这里取资源名
  ``正文衬线``；复合字体内部各字符实际落到哪个物理字体，IDML 不记录。
- 字色/描边色是色板引用（如 ``Color/u11e6`` / ``Swatch/None``），这里保留原始引用名，
  不解析 ``Resources/Graphic.xml`` 里的色值。
- 文本框 InsetSpacing、首行基线偏移、垂直对齐、网格起点（GridStartingPoint /
  UseMasterGrid）没有进入模型；由框坐标推"实际可排字数/行数"会有偏差。
- 图层可见性（designmap 的 ``<Layer Visible="false">``）、主页每页覆盖
  （OverrideList）、表格/脚注/嵌入对象内的文字没有展开。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import replace
from pathlib import Path

from .model import DocLayout, Frame, GridInfo, Margins, PageInfo, ParagraphRun, Style

__all__ = ["IdmlParseError", "parse"]

_IDPKG_NS = "http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging"
_IDPKG = "{%s}" % _IDPKG_NS

#: 需要作为「框」解析的页面项（TextFrame 之外的都是图形框）。
_FRAME_TAGS = frozenset({"TextFrame", "Rectangle", "Oval", "Polygon", "GraphicLine"})
#: 段落级可层叠的数值/文本属性（对应 ParagraphRun / Style 的字段）。
_NUM_ATTRS = {
    "PointSize": "point_size",
    "Leading": "leading",
    "FirstLineIndent": "first_line_indent",
    "StrokeWeight": "stroke_weight",
    "Tracking": "tracking",
}
_STR_ATTRS = {
    "FillColor": "fill_color",
    "StrokeColor": "stroke_color",
}

_FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")


class IdmlParseError(RuntimeError):
    """IDML 解析失败（异常信息带文件 / 部件上下文，不静默返回空结果）。"""


# ---------------------------------------------------------------- 基础工具
def _tag(el: ET.Element) -> str:
    tag = el.tag
    if isinstance(tag, str) and "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return str(tag)


def _find_first(root: ET.Element, name: str) -> ET.Element | None:
    """返回第一个指定标签的元素（先看 root 自身）。"""
    if _tag(root) == name:
        return root
    for el in root.iter():
        if _tag(el) == name:
            return el
    return None


def _find_container(root: ET.Element, name: str) -> ET.Element | None:
    """找到真正的内容容器元素。

    IDML 每个部件外层都包着 ``<idPkg:Spread>`` / ``<idPkg:Story>`` 这类同名包装元素，
    真正的 ``<Spread>`` / ``<Story>`` 是它的直接子元素；而直接导出的 XML 也可能以
    容器本身为根。这里先找直接子元素，再退回根/后代。
    """
    for child in root:
        if _tag(child) == name:
            return child
    if _tag(root) == name:
        return root
    return _find_first(root, name)


def _to_float(raw: str | None, default: float | None = None) -> float | None:
    if raw is None:
        return default
    raw = raw.strip()
    if not _FLOAT_RE.match(raw):
        return default
    try:
        return float(raw)
    except ValueError:  # pragma: no cover - _FLOAT_RE 已挡住
        return default


def _prop_text(el: ET.Element, prop: str) -> str:
    """取 ``<Properties><prop>text</prop></Properties>`` 的文本（只找直接子级）。"""
    for child in el:
        if _tag(child) != "Properties":
            continue
        for prop_el in child:
            if _tag(prop_el) == prop:
                return (prop_el.text or "").strip()
    return ""


def _normalize_font(raw: str | None) -> str:
    """把 ``CompositeFont/$ID/正文衬线`` 之类的资源引用还原成显示名。"""
    name = (raw or "").strip()
    if not name:
        return ""
    lowered = name.lower()
    if lowered.startswith("compositefont/") or lowered.startswith("font/"):
        parts = [p for p in name.split("/") if p and p != "$ID"]
        if parts:
            return parts[-1]
    return name


def _read_xml(zf: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        data = zf.read(name)
    except KeyError as exc:
        raise IdmlParseError("IDML 包内缺少 %s" % name) from exc
    except OSError as exc:  # pragma: no cover - zip 损坏
        raise IdmlParseError("读取 IDML 部件 %s 失败：%s" % (name, exc)) from exc
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise IdmlParseError("IDML 部件 %s 的 XML 解析失败：%s" % (name, exc)) from exc


# ---------------------------------------------------------------- 变换矩阵
# 矩阵 (a, b, c, d, tx, ty)，点 (x, y) -> (a*x + c*y + tx, b*x + d*y + ty)
_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_Matrix = tuple[float, float, float, float, float, float]


def _parse_transform(el: ET.Element) -> _Matrix | None:
    raw = el.get("ItemTransform")
    if not raw:
        return None
    parts = raw.split()
    if len(parts) != 6:
        return None
    try:
        return (float(parts[0]), float(parts[1]), float(parts[2]),
                float(parts[3]), float(parts[4]), float(parts[5]))
    except ValueError:
        return None


def _mat_mul(parent: _Matrix, child: _Matrix) -> _Matrix:
    """拼接矩阵：先 child 再 parent（父级在左）。"""
    a1, b1, c1, d1, e1, f1 = parent
    a2, b2, c2, d2, e2, f2 = child
    return (a1 * a2 + b1 * c2, a1 * b2 + b1 * d2,
            c1 * a2 + d1 * c2, c1 * b2 + d1 * d2,
            e1 * a2 + f1 * c2 + e2, e1 * b2 + f1 * d2 + f2)


def _apply(matrix: _Matrix, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, tx, ty = matrix
    return (a * x + c * y + tx, b * x + d * y + ty)


# ---------------------------------------------------------------- 文档级参数
def _read_document_preferences(zf: zipfile.ZipFile) -> tuple[float, float, Margins, GridInfo]:
    root = _read_xml(zf, "Resources/Preferences.xml")

    doc_pref = None
    margin_pref = None
    layout_grid = None
    for child in root:
        tag = _tag(child)
        if tag == "DocumentPreference" and doc_pref is None:
            doc_pref = child
        elif tag == "MarginPreference" and margin_pref is None:
            margin_pref = child
        elif tag == "LayoutGridDataInformation" and layout_grid is None:
            layout_grid = child

    if doc_pref is None:
        raise IdmlParseError("Resources/Preferences.xml 缺少 <DocumentPreference>，无法得到页面尺寸")
    page_w = _to_float(doc_pref.get("PageWidth"))
    page_h = _to_float(doc_pref.get("PageHeight"))
    if not page_w or not page_h:
        raise IdmlParseError(
            "Resources/Preferences.xml 的 <DocumentPreference> 缺少有效的 PageWidth/PageHeight")

    # 出血：四边取最大值；样张四边均 8.503937pt（3mm）。
    bleeds = [_to_float(doc_pref.get(name), 0.0) or 0.0 for name in (
        "DocumentBleedTopOffset", "DocumentBleedBottomOffset",
        "DocumentBleedInsideOrLeftOffset", "DocumentBleedOutsideOrRightOffset")]
    bleed = max(bleeds) if bleeds else 0.0

    if margin_pref is None:
        raise IdmlParseError("Resources/Preferences.xml 缺少 <MarginPreference>，无法得到页边距")
    margins = Margins(
        top=_to_float(margin_pref.get("Top"), 0.0) or 0.0,
        bottom=_to_float(margin_pref.get("Bottom"), 0.0) or 0.0,
        left=_to_float(margin_pref.get("Left"), 0.0) or 0.0,
        right=_to_float(margin_pref.get("Right"), 0.0) or 0.0,
        bleed=bleed,
        columns=int(_to_float(margin_pref.get("ColumnCount"), 1.0) or 1.0),
        gutter=_to_float(margin_pref.get("ColumnGutter"), 0.0) or 0.0,
    )

    # 版面网格 = LayoutGridDataInformation；该元素存在即视为启用了版面网格。
    # 注意与 <GridPreference>（文档网格）、<StoryGridDataInformation>（框架网格）区分。
    if layout_grid is None:
        grid = GridInfo(enabled=False)
    else:
        grid = GridInfo(
            enabled=True,
            font=_normalize_font(_prop_text(layout_grid, "AppliedFont")),
            point_size=_to_float(layout_grid.get("PointSize"), 0.0) or 0.0,
            char_aki=_to_float(layout_grid.get("CharacterAki"), 0.0) or 0.0,
            line_aki=_to_float(layout_grid.get("LineAki"), 0.0) or 0.0,
            h_scale=_to_float(layout_grid.get("HorizontalScale"), 100.0) or 100.0,
            v_scale=_to_float(layout_grid.get("VerticalScale"), 100.0) or 100.0,
        )
    return page_w, page_h, margins, grid


# ---------------------------------------------------------------- 样式
def _style_attr_values(el: ET.Element) -> dict:
    """读取一个样式元素自身声明的属性（不含 BasedOn 继承）。"""
    values: dict = {}
    for attr, field in _NUM_ATTRS.items():
        val = _to_float(el.get(attr))
        if val is not None:
            values[field] = val
    for attr, field in _STR_ATTRS.items():
        raw = el.get(attr)
        if raw:
            values[field] = raw.strip()
    font = _prop_text(el, "AppliedFont")
    if font:
        values["font"] = _normalize_font(font)
    # Leading / FirstLineIndent 也可能以 <Properties> 子元素形式出现。
    for prop, field in (("Leading", "leading"), ("FirstLineIndent", "first_line_indent")):
        if field in values:
            continue
        text = _prop_text(el, prop)
        val = _to_float(text)
        if val is not None:
            values[field] = val
    return values


def _parse_styles(zf: zipfile.ZipFile) -> dict[str, Style]:
    root = _read_xml(zf, "Resources/Styles.xml")
    raw: dict[str, tuple[str, str, dict, str]] = {}
    for el in root.iter():
        tag = _tag(el)
        if tag not in ("ParagraphStyle", "CharacterStyle"):
            continue
        style_id = el.get("Self")
        if not style_id:
            continue
        kind = "paragraph" if tag == "ParagraphStyle" else "character"
        raw[style_id] = (el.get("Name", ""), kind, _style_attr_values(el),
                         el.get("BasedOn", "") or "")

    resolved: dict[str, dict] = {}

    def resolve(style_id: str, seen: frozenset[str]) -> dict:
        if style_id in resolved:
            return resolved[style_id]
        if style_id not in raw or style_id in seen:
            return {}
        _, _, own, based_on = raw[style_id]
        merged = dict(resolve(based_on, seen | {style_id})) if based_on else {}
        merged.update(own)
        resolved[style_id] = merged
        return merged

    styles: dict[str, Style] = {}
    for style_id, (name, kind, _own, _based) in raw.items():
        merged = resolve(style_id, frozenset())
        styles[style_id] = Style(
            self_id=style_id,
            name=name,
            kind=kind,
            font=str(merged.get("font", "")),
            point_size=float(merged.get("point_size", 0.0)),
            leading=float(merged.get("leading", 0.0)),
            first_line_indent=float(merged.get("first_line_indent", 0.0)),
            tracking=float(merged.get("tracking", 0.0)),
            fill_color=str(merged.get("fill_color", "")),
            stroke_weight=float(merged.get("stroke_weight", -1.0)),
            stroke_color=str(merged.get("stroke_color", "")),
        )
    return styles


# ---------------------------------------------------------------- designmap 清单
def _parse_manifest(zf: zipfile.ZipFile) -> tuple[list[str], list[str], list[str]]:
    """按 designmap.xml 顺序返回 (spread, master spread, story) 部件名。"""
    root = _read_xml(zf, "designmap.xml")
    spreads: list[str] = []
    masters: list[str] = []
    stories: list[str] = []
    for el in root.iter():
        src = el.get("src")
        if not src:
            continue
        tag = _tag(el)
        if tag == "Spread":
            spreads.append(src)
        elif tag == "MasterSpread":
            masters.append(src)
        elif tag == "Story":
            stories.append(src)

    names = zf.namelist()
    if not spreads:
        spreads = sorted(n for n in names if n.startswith("Spreads/") and n.endswith(".xml"))
        if not spreads:
            raise IdmlParseError("designmap.xml 中没有 <idPkg:Spread>，包内也没有 Spreads/*.xml")
    if not masters:
        masters = sorted(n for n in names
                         if n.startswith("MasterSpreads/") and n.endswith(".xml"))
    if not stories:
        stories = sorted(n for n in names if n.startswith("Stories/") and n.endswith(".xml"))
    return spreads, masters, stories


# ---------------------------------------------------------------- 页面与框
class _PageGeom:
    """页面在 spread 坐标里的矩形 + 主页引用。"""

    __slots__ = ("index", "side", "x0", "y0", "x1", "y1", "applied_master")

    def __init__(self, index: int, side: int, x0: float, y0: float,
                 x1: float, y1: float, applied_master: str) -> None:
        self.index = index          # DocLayout.pages 中的 0 基下标
        self.side = side            # 在本 spread 内的顺序：0=左页，1=右页
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        self.applied_master = applied_master


def _geom_bounds(raw: str | None) -> tuple[float, float, float, float] | None:
    """``GeometricBounds="y0 x0 y1 x1"`` → (y0, x0, y1, x1)。"""
    if not raw:
        return None
    parts = raw.split()
    if len(parts) != 4:
        return None
    try:
        return float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3])
    except ValueError:
        return None


def _page_geoms(spread: ET.Element, doc_w: float, doc_h: float,
                offset: int) -> list[_PageGeom]:
    geoms: list[_PageGeom] = []
    for child in spread:
        if _tag(child) != "Page":
            continue
        matrix = _parse_transform(child) or _IDENTITY
        bounds = _geom_bounds(child.get("GeometricBounds"))
        if bounds is None:
            y0, x0, y1, x1 = 0.0, 0.0, doc_h, doc_w
        else:
            y0, x0, y1, x1 = bounds
        corners = [_apply(matrix, x0, y0), _apply(matrix, x1, y1)]
        xs = [p[0] for p in corners]
        ys = [p[1] for p in corners]
        geoms.append(_PageGeom(
            index=offset + len(geoms),
            side=len(geoms),
            x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys),
            applied_master=child.get("AppliedMaster", "") or "",
        ))
    return geoms


def _assign_page(bbox: tuple[float, float, float, float],
                 geoms: list[_PageGeom]) -> _PageGeom | None:
    """按 bbox 与页面矩形的重叠面积选页；完全在页外时取中心最近的页。"""
    if not geoms:
        return None
    bx0, by0, bx1, by1 = bbox
    best: _PageGeom | None = None
    best_area = 0.0
    for geom in geoms:
        ix0 = max(bx0, geom.x0)
        iy0 = max(by0, geom.y0)
        ix1 = min(bx1, geom.x1)
        iy1 = min(by1, geom.y1)
        if ix1 <= ix0 or iy1 <= iy0:
            continue
        area = (ix1 - ix0) * (iy1 - iy0)
        if area > best_area:
            best, best_area = geom, area
    if best is not None:
        return best
    cx = (bx0 + bx1) / 2.0
    cy = (by0 + by1) / 2.0
    return min(geoms, key=lambda g: (abs((g.x0 + g.x1) / 2.0 - cx)
                                     + abs((g.y0 + g.y1) / 2.0 - cy)))


def _has_image(el: ET.Element) -> bool:
    return any(_tag(node) == "Image" for node in el.iter())


def _frame_bbox(el: ET.Element, matrix: _Matrix) -> tuple[float, float, float, float] | None:
    points: list[tuple[float, float]] = []
    for node in el.iter():
        if _tag(node) not in ("PathPointType", "PathPoint"):
            continue
        anchor = node.get("Anchor")
        if not anchor:
            continue
        parts = anchor.split()
        if len(parts) < 2:
            continue
        try:
            px, py = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        points.append(_apply(matrix, px, py))
    if not points:
        # 极少数框没有 PathGeometry，退回 GeometricBounds（框本身坐标）。
        bounds = _geom_bounds(el.get("GeometricBounds"))
        if bounds is not None:
            y0, x0, y1, x1 = bounds
            points = [_apply(matrix, x0, y0), _apply(matrix, x1, y1)]
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _build_frame(el: ET.Element, tag: str, matrix: _Matrix,
                 geoms: list[_PageGeom]) -> Frame | None:
    """把页面项元素（可能是 Group 里的子项）转成 Frame；跳过隐藏/无变换项。"""
    if el.get("Hidden") == "true":
        return None
    local = _parse_transform(el)
    if local is None:
        return None
    bbox = _frame_bbox(el, _mat_mul(matrix, local))
    if bbox is None:
        return None
    geom = _assign_page(bbox, geoms)
    if geom is None:
        return None
    if tag == "TextFrame":
        kind = "text"
    elif _has_image(el):
        kind = "image"
    else:
        kind = "graphic"
    return Frame(
        self_id=el.get("Self", ""),
        kind=kind,
        story=el.get("ParentStory", "") or "",
        x0=bbox[0] - geom.x0,
        y0=bbox[1] - geom.y0,
        x1=bbox[2] - geom.x0,
        y1=bbox[3] - geom.y0,
        page_index=geom.index,
        overflow=False,   # IDML 不记录文本溢流状态
        stroke_weight=_to_float(el.get("StrokeWeight"), 0.0) or 0.0,
    )


def _walk_items(parent: ET.Element, matrix: _Matrix,
                geoms: list[_PageGeom], out: list[Frame]) -> None:
    """递归遍历 spread / group 下的页面项，组合嵌套 Group 的 ItemTransform。"""
    for child in parent:
        tag = _tag(child)
        if tag == "Group":
            local = _parse_transform(child)
            if local is None:      # 组无变换时无法定位子项，整组跳过
                continue
            _walk_items(child, _mat_mul(matrix, local), geoms, out)
        elif tag in _FRAME_TAGS:
            frame = _build_frame(child, tag, matrix, geoms)
            if frame is not None:
                out.append(frame)


def _build_pages(zf: zipfile.ZipFile, spread_srcs: list[str],
                 doc_w: float, doc_h: float, margins: Margins,
                 grid: GridInfo) -> tuple[list[PageInfo], list[str], list[int], dict]:
    """解析所有文档 spread，返回页面、每页主页 id、页在 spread 内的左右序、story→框映射。"""
    pages: list[PageInfo] = []
    page_master: list[str] = []
    page_side: list[int] = []
    story_frame: dict[str, tuple[int, str]] = {}

    for src in spread_srcs:
        root = _read_xml(zf, src)
        spread = _find_container(root, "Spread") or root
        geoms = _page_geoms(spread, doc_w, doc_h, len(pages))
        if not geoms:
            raise IdmlParseError("%s 中没有 <Page> 元素" % src)
        for geom in geoms:
            pages.append(PageInfo(
                index=geom.index,
                width=doc_w,
                height=doc_h,
                margins=replace(margins),
                grid=replace(grid),
            ))
            page_master.append(geom.applied_master)
            page_side.append(geom.side)
        frames: list[Frame] = []
        _walk_items(spread, _IDENTITY, geoms, frames)
        for frame in frames:
            pages[frame.page_index].frames.append(frame)
    if not pages:
        raise IdmlParseError("IDML 中没有任何页面（Spreads 下没有 Page 元素）")
    # story → 第一个文本框：按文档页顺序取第一次出现，保证结果与页面顺序一致。
    for page in pages:
        for frame in page.frames:
            if frame.story and frame.story not in story_frame:
                story_frame[frame.story] = (page.index, frame.self_id)
    return pages, page_master, page_side, story_frame


def _attach_master_frames(zf: zipfile.ZipFile, master_srcs: list[str],
                          pages: list[PageInfo], page_master: list[str],
                          page_side: list[int], doc_w: float, doc_h: float,
                          story_frame: dict[str, tuple[int, str]]) -> None:
    """把主页（MasterSpread）里的框挂到使用该主页的文档页上。

    IDML 里主页项并不在各文档页的 Spread 中，而是单独放在 MasterSpreads/*.xml；
    主页项会出现在所有套用该主页的页面上，所以这里按「主页页（左/右）→ 文档页（左/右）」
    复制一份到对应页面，坐标沿用主页页面原点（与文档页同尺寸）。
    """
    for src in master_srcs:
        root = _read_xml(zf, src)
        master = _find_container(root, "MasterSpread") or root
        master_id = master.get("Self", "") or ""
        if not master_id:
            continue
        geoms = _page_geoms(master, doc_w, doc_h, 0)
        if not geoms:
            continue
        frames: list[Frame] = []
        _walk_items(master, _IDENTITY, geoms, frames)
        if not frames:
            continue
        by_side: dict[int, list[Frame]] = {}
        for frame in frames:
            by_side.setdefault(frame.page_index, []).append(frame)
        for page_index, page in enumerate(pages):
            if page_master[page_index] != master_id:
                continue
            side = page_side[page_index] if page_side[page_index] in by_side else 0
            for template in by_side.get(side, []):
                frame = replace(template, page_index=page.index)
                page.frames.append(frame)
                if frame.story and frame.story not in story_frame:
                    story_frame[frame.story] = (page.index, frame.self_id)


# ---------------------------------------------------------------- 段落
def _format_overrides(el: ET.Element) -> dict:
    """读取 ParagraphStyleRange / CharacterStyleRange 上的直接格式覆盖。"""
    values: dict = {}
    for attr, field in _NUM_ATTRS.items():
        raw = el.get(attr)
        val = _to_float(raw)
        if val is not None:
            values[field] = val
    for attr, field in _STR_ATTRS.items():
        raw = el.get(attr)
        if raw:
            values[field] = raw.strip()
    font = _prop_text(el, "AppliedFont")
    if font:
        values["font"] = _normalize_font(font)
    # Leading 通常写在 <Properties><Leading type="unit">16.5</Leading>。
    if "leading" not in values:
        val = _to_float(_prop_text(el, "Leading"))
        if val is not None:
            values["leading"] = val
    if "first_line_indent" not in values:
        val = _to_float(_prop_text(el, "FirstLineIndent"))
        if val is not None:
            values["first_line_indent"] = val
    return values


def _csr_text(csr: ET.Element) -> str:
    parts: list[str] = []
    for child in csr:
        tag = _tag(child)
        if tag == "Content":
            parts.append(child.text or "")
        elif tag == "Br":
            parts.append("\n")
    return "".join(parts)


def _style_display_name(style_ref: str) -> str:
    if not style_ref:
        return ""
    return style_ref.rsplit("/", 1)[-1]


def _parse_paragraphs(zf: zipfile.ZipFile, story_srcs: list[str],
                      styles: dict[str, Style],
                      story_frame: dict[str, tuple[int, str]]) -> list[ParagraphRun]:
    runs: list[ParagraphRun] = []
    for src in story_srcs:
        root = _read_xml(zf, src)
        story = _find_container(root, "Story") or root
        story_id = story.get("Self", "") or Path(src).stem
        page_index, frame_id = story_frame.get(story_id, (-1, ""))
        for psr in story:
            if _tag(psr) != "ParagraphStyleRange":
                continue
            style_ref = psr.get("AppliedParagraphStyle", "") or ""
            style = styles.get(style_ref)
            style_name = style.name if style is not None else _style_display_name(style_ref)

            # 段落样式定义 → 段落覆盖 → 字符覆盖
            effective = {
                "font": style.font if style is not None else "",
                "point_size": style.point_size if style is not None else 0.0,
                "leading": style.leading if style is not None else 0.0,
                "first_line_indent": style.first_line_indent if style is not None else 0.0,
                "fill_color": style.fill_color if style is not None else "",
                "stroke_weight": style.stroke_weight if style is not None else -1.0,
            }
            effective.update(_format_overrides(psr))

            texts: list[str] = []
            chosen: dict | None = None
            first_override: dict | None = None
            for csr in psr:
                if _tag(csr) != "CharacterStyleRange":
                    continue
                override = _format_overrides(csr)
                if first_override is None:
                    first_override = override
                if chosen is None and override:
                    chosen = override
                texts.append(_csr_text(csr))
            if chosen is None:
                chosen = first_override or {}
            effective.update(chosen)

            runs.append(ParagraphRun(
                text="".join(texts),
                style_name=style_name,
                font=str(effective.get("font", "")),
                point_size=float(effective.get("point_size", 0.0)),
                leading=float(effective.get("leading", 0.0)),
                first_line_indent=float(effective.get("first_line_indent", 0.0)),
                fill_color=str(effective.get("fill_color", "")),
                stroke_weight=float(effective.get("stroke_weight", -1.0)),
                story=story_id,
                page_index=page_index,
                frame_id=frame_id,
            ))
    return runs


def _collect_fonts(grid: GridInfo, styles: dict[str, Style],
                   paragraphs: list[ParagraphRun]) -> list[str]:
    fonts: set[str] = set()
    if grid.font:
        fonts.add(grid.font)
    for style in styles.values():
        if style.font:
            fonts.add(style.font)
    for run in paragraphs:
        if run.font:
            fonts.add(run.font)
    return sorted(fonts)


# ---------------------------------------------------------------- 入口
def parse(path: str) -> DocLayout:
    """解析一个 .idml 文件，返回与 ``model.DocLayout`` 对应的三审版面数据。

    :raises IdmlParseError: 文件不存在 / 不是合法 zip / 缺少必要部件 / XML 损坏。
    """
    src = str(path)
    file_path = Path(src)
    if not file_path.is_file():
        raise IdmlParseError("IDML 文件不存在：%s" % src)
    try:
        zf = zipfile.ZipFile(file_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise IdmlParseError("无法作为 IDML(zip) 打开：%s（%s）" % (src, exc)) from exc

    with zf:
        page_w, page_h, margins, grid = _read_document_preferences(zf)
        styles = _parse_styles(zf)
        spread_srcs, master_srcs, story_srcs = _parse_manifest(zf)
        pages, page_master, page_side, story_frame = _build_pages(
            zf, spread_srcs, page_w, page_h, margins, grid)
        _attach_master_frames(zf, master_srcs, pages, page_master, page_side,
                              page_w, page_h, story_frame)
        paragraphs = _parse_paragraphs(zf, story_srcs, styles, story_frame)
        fonts = _collect_fonts(grid, styles, paragraphs)

    return DocLayout(source=src, pages=pages, styles=styles,
                     paragraphs=paragraphs, fonts=fonts)
