"""PDF 解析层（「读者所见」层）：文字段抽取 + 页面渲染。

职责：
- parse()       把 PDF 每页的文字拆成「格式一致」的 PdfSpan（同字体/字号/颜色/行）。
- page_images() 把指定页渲染成 PNG（供审稿报告里画红框、看版面）。

实现基于 vendor/pypdfium2（pypdfium2 5.15.2 / pdfium 159），只用 ctypes 调 pdfium，
不引入额外依赖。

------------------------------------------------------------------ 坐标系（实测结论）
pdfium 的**文字** API（get_charbox / FPDFText_GetCharBox / FPDFText_GetMatrix）
返回的是 PDF 页面坐标：**原点在左下角，y 向上**（和 PDF 内容流一致）。
这不是「左上原点」，所以本模块统一翻成 model.py 的约定：原点左上角、y 向下。

实测证据（samples/字体.pdf 第 3 页，页面 541.42 × 754.02 pt，scale=1.0 渲染成 542×755 px）：
1) 'H'（HelveticaNeueLT-Black，78pt）charbox = (121.16, 553.37, 171.55, 609.06)，
   其中 bottom=553.37 与 FPDFText_GetMatrix 的 f（基线 y）完全相等，top=609.06 是字顶；
   按 y 向上算，它离页顶 754.02-609.06=144.95pt … 754.02-553.37=200.65pt。
   逐行扫描渲染图暗像素，该字带正好落在第 145..210 行 → 与「y 向上」吻合；
   若按 y 向下去理解会落在 553..609 行，那里是图片区，不吻合。
2) 同一页 'A Day Without'（GillSansNova-Bold，51.36pt）charbox y = 630.71..666.44，
   翻成自上而下是 87.58..123.58，扫描到的暗像素带是 88..126 → 再次吻合。
3) x 不翻转：同一行内从左到右 x 递增（'H' 121.16 → 'e' 178.41 → 'l' 229.58 …）。

本模块对外的 PdfSpan.x0/y0/x1/y1 已全部是「左上原点、y 向下、单位 pt」。

------------------------------------------------------------------ pdfium 取值要点
- 字号：FPDFText_GetFontSize() 返回的是**文本空间**字号。本样张是 InDesign 导出，
  字体尺寸全为 1.0，真实字号藏在文本矩阵里：FPDFText_GetMatrix() 的 a/b 两列缩放即
  实际字号（'H' 的 a=78.0 → 78pt；副标 a=51.36 → 51.36pt）。因此
  size = GetFontSize() × hypot(a, b)（旋转文本退化为 hypot(c, d)）。
- 字体名：FPDFText_GetFontInfo() -> 本机 pdfium 返回的是 UTF-8/ASCII 字节
  （"HelveticaNeueLT-Black"），而官方头文件写的是 UTF-16LE；两者都兼容解码，
  见 _decode_pdfium_str()。取不到时退化到 FPDFTextObj_GetFont +
  FPDFFont_GetBaseFontName / GetFamilyName。字体名的子集前缀（IUIWOS+）会被去掉。
- 颜色：FPDFText_GetFillColor() -> (r,g,b,a) 0..255，取 RGB；失败时回退黑色。
- 字符：tp.get_text_range(i, 1)；解码失败/无 Unicode 映射的字符跳过并计数，
  统计信息打印到 stderr（model.PdfDoc 没有 notes 字段，不改 model.py）。
"""
from __future__ import annotations

import ctypes
import math
import pathlib
import statistics
import struct
import sys
import zlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_ROOT / "vendor") not in sys.path:
    sys.path.insert(0, str(_ROOT / "vendor"))

import pypdfium2 as pdfium
from pypdfium2 import raw

from .model import PdfDoc, PdfPageInfo, PdfSpan

__all__ = ["parse", "page_images", "page_sheets", "sheet_boxes"]

# Pillow 是可选依赖：有就用 to_pil() 存 PNG，没有就用下面的纯标准库 PNG 编码器。
try:  # pragma: no cover - 取决于运行环境
    from PIL import Image as _PILImage
    _HAS_PIL = True
except Exception:  # pragma: no cover
    _PILImage = None
    _HAS_PIL = False

_WHITESPACE_ONLY = "\r\n\t "


# ====================================================================== 工具函数
def _decode_pdfium_str(data: bytes) -> str:
    """解码 pdfium 输出的字符串。

    pdfium 头文件规定字体名是 UTF-16LE，但实测 pypdfium2 5.15.2（pdfium 159）返回的是
    UTF-8/ASCII 字节；两种都兼容：含大量 NUL 或 UTF-8 解不开时按 UTF-16LE 解。
    """
    data = data.rstrip(b"\x00")
    if not data:
        return ""
    if len(data) >= 4:
        odd_nul = data[1::2].count(0)
        if odd_nul >= (len(data) // 2) * 0.8:
            return data.decode("utf-16-le", "ignore").rstrip("\x00")
    try:
        return data.decode("utf-8").rstrip("\x00")
    except UnicodeDecodeError:
        return data.decode("utf-16-le", "ignore").rstrip("\x00")


def _strip_subset(font_name: str) -> str:
    """去掉嵌入子集前缀（形如 "IUIWOS+FZLTTHK--GBK1-0" 的前 7 个字符）。"""
    if len(font_name) > 7 and font_name[6] == "+" and font_name[:6].isalnum():
        return font_name[7:]
    return font_name


def _read_cstr(func, handle, capacity: int = 512) -> str:
    """通用：func(handle, buffer, buflen) -> 长度（含结尾 NUL）的字符串读取。"""
    buf = ctypes.create_string_buffer(capacity)
    try:
        n = func(handle, buf, capacity - 1)
    except Exception:
        return ""
    if not n:
        return ""
    n = int(n)
    if n > capacity:  # 缓冲区不够，按返回长度重试一次
        buf = ctypes.create_string_buffer(n * 2 + 8)
        try:
            n = int(func(handle, buf, n * 2))
        except Exception:
            return ""
        if not n:
            return ""
    # 本机 pdfium 返回字节长度（含 NUL）；UTF-16 语义下则是字符数。
    # 多取一点再 rstrip，两种语义都安全。
    return _decode_pdfium_str(buf.raw[: min(len(buf.raw), max(2, n * 2))])


def _font_name(tp, i: int) -> str:
    """字符的字体名：优先 FPDFText_GetFontInfo，失败退化到文本对象。"""
    name = ""
    if hasattr(raw, "FPDFText_GetFontInfo"):
        buf = ctypes.create_string_buffer(1024)
        flags = ctypes.c_int(0)
        try:
            n = int(raw.FPDFText_GetFontInfo(tp.raw, i, buf, 512, ctypes.byref(flags)))
        except Exception:
            n = 0
        if n:
            if n * 2 > len(buf.raw):
                buf = ctypes.create_string_buffer(n * 4 + 8)
                try:
                    n = int(raw.FPDFText_GetFontInfo(tp.raw, i, buf, n * 2, ctypes.byref(flags)))
                except Exception:
                    n = 0
            if n:
                name = _decode_pdfium_str(buf.raw[: min(len(buf.raw), max(2, n * 2))])
    if not name:
        name = _font_from_textobj(tp, i)
    return _strip_subset(name)


def _text_object(tp, i: int):
    """字符对应的页面对象（FPDF_PAGEOBJECT），可能为 NULL。"""
    try:
        obj = raw.FPDFText_GetTextObject(tp.raw, i)
    except Exception:
        return None
    if not obj:
        return None
    try:
        if ctypes.cast(obj, ctypes.c_void_p).value is None:
            return None
    except Exception:
        return None
    return obj


def _font_from_textobj(tp, i: int) -> str:
    obj = _text_object(tp, i)
    if obj is None:
        return ""
    try:
        font = raw.FPDFTextObj_GetFont(obj)
    except Exception:
        return ""
    if not font:
        return ""
    name = _read_cstr(raw.FPDFFont_GetBaseFontName, font)
    if not name:
        name = _read_cstr(raw.FPDFFont_GetFamilyName, font)
    return name


def _char_size(tp, i: int, box_bottom: float, box_top: float):
    """返回 (字号 pt, 行基准坐标, 是否旋转)。

    字号 = FPDFText_GetFontSize()（文本空间） × 文本矩阵缩放。
    行基准：横排用矩阵 f（基线 y），旋转文本用矩阵 e（基线 x）。
    """
    text_size = 0.0
    try:
        text_size = float(raw.FPDFText_GetFontSize(tp.raw, i))
    except Exception:
        text_size = 0.0

    scale = 0.0
    base = (box_bottom + box_top) / 2.0
    rotated = False
    try:
        mat = raw.FS_MATRIX()
        ok = raw.FPDFText_GetMatrix(tp.raw, i, ctypes.byref(mat))
    except Exception:
        ok = 0
    if ok:
        sx = math.hypot(float(mat.a), float(mat.b))
        sy = math.hypot(float(mat.c), float(mat.d))
        scale = sx if sx > 1e-6 else sy
        rotated = abs(float(mat.c)) > abs(float(mat.a)) + 1e-6
        base = float(mat.e) if rotated else float(mat.f)

    if text_size > 0.0 and scale > 1e-6:
        size = text_size * scale
    elif scale > 1e-6:
        size = scale
    else:
        size = text_size

    # 基线合理性检查：pdfium 合成字符（如自动补的空格）可能给出全 0 / 单位矩阵，
    # 此时 baseline 会跑到 0 处，退回字框中线做行分组。
    slack = max(1.0, 0.35 * (size if size > 0 else 1.0))
    if base < box_bottom - slack or base > box_top + slack:
        base = (box_bottom + box_top) / 2.0
        rotated = False
    return size, base, rotated


def _fill_color(tp, i: int):
    """字符填充色 (r,g,b)；取不到返回 None。"""
    try:
        r = ctypes.c_uint(0)
        g = ctypes.c_uint(0)
        b = ctypes.c_uint(0)
        a = ctypes.c_uint(0)
        ok = raw.FPDFText_GetFillColor(
            tp.raw, i, ctypes.byref(r), ctypes.byref(g), ctypes.byref(b), ctypes.byref(a))
    except Exception:
        return None
    if not ok:
        return None
    return (int(r.value) & 0xFF, int(g.value) & 0xFF, int(b.value) & 0xFF)


class _Stats:
    """一页的解析统计（只用于 stderr 汇报，不进 model）。"""

    __slots__ = ("chars", "skipped", "unicode_err", "no_font", "est_size", "no_color")

    def __init__(self) -> None:
        self.chars = 0
        self.skipped = 0
        self.unicode_err = 0
        self.no_font = 0
        self.est_size = 0
        self.no_color = 0

    @property
    def degraded(self) -> bool:
        return bool(self.skipped or self.unicode_err or self.no_font
                    or self.est_size or self.no_color)


def _same_line(line: dict, base: float, bottom: float, top: float,
               size: float, rotated: bool) -> bool:
    """判断字符是否还在当前行：基线接近 + 字框纵向相交。"""
    if line is None or line["rotated"] != rotated:
        return False
    tol = max(2.0, 0.45 * max(size, line["size"]))
    if abs(base - line["base"]) > tol:
        return False
    if rotated:
        return True
    if top < line["bot"] - 1.5 or bottom > line["top"] + 1.5:
        return False
    return True


def _flush_span(spans: list, cur: dict | None, page_height: float, page_index: int):
    """把累积中的 span 落盘成 PdfSpan（y 翻成「左上原点、向下」）。"""
    if not cur or not cur["parts"]:
        return None
    top = cur["top"]
    bot = cur["bot"]
    y0 = page_height - top           # 字框上沿距页顶
    y1 = page_height - bot           # 字框下沿距页顶
    if y1 - y0 < 0.05:               # 空格等零高字框，给个最小高度便于画框
        y1 = y0 + 0.05
    size = cur["size"]
    if cur.get("est_sizes"):         # 字号是估的：取该段中位数，别跟着每个字形跳
        size = statistics.median(cur["est_sizes"])
    spans.append(PdfSpan(
        text="".join(cur["parts"]),
        font=cur["font"],
        size=round(size, 2),
        color=cur["color"],
        x0=round(cur["x0"], 2),
        y0=round(y0, 2),
        x1=round(cur["x1"], 2),
        y1=round(y1, 2),
        page_index=page_index,
    ))
    return None


def _extract_spans(tp, info: PdfPageInfo, stats: _Stats) -> None:
    """按「字体 + 字号 + 颜色 + 行」切分 PdfSpan。"""
    height = info.height
    spans = info.spans
    n = stats.chars
    cur = None            # 当前 span 的累积状态
    line = None           # 当前行的状态（旋转文本的「行」沿 x 方向）
    last_attr = None      # 上一个有效 (font, size, color)，给无字体名的空格继承用

    def flush():
        nonlocal cur
        cur = _flush_span(spans, cur, height, info.index)

    for i in range(n):
        try:
            ch = tp.get_text_range(i, 1)
        except Exception:
            stats.skipped += 1
            continue
        if not ch or ch in ("\x00", "\ufffd"):
            stats.skipped += 1
            try:
                if raw.FPDFText_HasUnicodeMapError(tp.raw, i):
                    stats.unicode_err += 1
            except Exception:
                pass
            continue
        if ch in ("\r", "\n"):
            flush()
            line = None
            continue
        try:
            box = tp.get_charbox(i)
            left, bottom, right, top = (float(box[0]), float(box[1]),
                                        float(box[2]), float(box[3]))
        except Exception:
            stats.skipped += 1
            continue
        if left > right:
            left, right = right, left
        if bottom > top:
            bottom, top = top, bottom

        font = size = None
        color = None
        base = (bottom + top) / 2.0
        rotated = False
        est_size = False
        try:
            font = _font_name(tp, i)
            size, base, rotated = _char_size(tp, i, bottom, top)
        except Exception:
            stats.skipped += 1
            continue
        try:
            color = _fill_color(tp, i)
        except Exception:
            color = None

        # pdfium 合成的空格字符：字体名/矩阵常常不可靠（空字体名、单位矩阵→字号 1.0），
        # 让它们继承前一个字符的格式，别把一句话从中间切断。
        inherited = False
        if ch in _WHITESPACE_ONLY and last_attr is not None:
            if not font:
                font, size, color = last_attr[0], last_attr[1], last_attr[2]
                est_size = bool(last_attr[3])
                inherited = True
            else:
                if not size or size <= 1.05:
                    size = last_attr[1]
                    est_size = bool(last_attr[3])
                    inherited = True
                if color is None:
                    color = last_attr[2]
                    inherited = True
        elif not font and ch not in _WHITESPACE_ONLY:
            stats.no_font += 1
        if color is None:
            color = (0, 0, 0)
            if not inherited:
                stats.no_color += 1

        # 字号退化：矩阵取不到时 FPDFText_GetFontSize 可能只剩 1.0（InDesign 导出即如此），
        # 这种情况下按字框高度估：大写字母高 ≈ 0.72 em。
        box_h = top - bottom
        suspicious = (not size or size <= 0
                      or (ch not in _WHITESPACE_ONLY and size <= 1.05
                          and box_h > 2.0 * max(size, 0.0)))
        if suspicious:
            if ch not in _WHITESPACE_ONLY and box_h > 0.5:
                size = box_h / 0.72
                stats.est_size += 1
                est_size = True
            elif not size or size <= 0:
                size = 1.0

        if font:
            last_attr = (font, size, color, est_size)

        if not _same_line(line, base, bottom, top, size, rotated):
            flush()
            line = {"base": base, "bot": bottom, "top": top,
                    "size": size, "rotated": rotated}
        else:
            if bottom < line["bot"]:
                line["bot"] = bottom
            if top > line["top"]:
                line["top"] = top
            if size > line["size"]:
                line["size"] = size

        size_key = None if est_size else round(size, 2)   # 估来的字号不参与切分
        same_size = ((size_key is None and cur is not None and cur["size_key"] is None)
                     or (cur is not None and cur["size_key"] == size_key))
        if (cur is not None and cur["line"] is line
                and cur["font"] == font and same_size
                and cur["color"] == color):
            cur["parts"].append(ch)
            if left < cur["x0"]:
                cur["x0"] = left
            if right > cur["x1"]:
                cur["x1"] = right
            if top > cur["top"]:
                cur["top"] = top
            if bottom < cur["bot"]:
                cur["bot"] = bottom
            if cur["est_sizes"] is not None:
                cur["est_sizes"].append(size)
            elif size > cur["size"]:
                cur["size"] = size
        else:
            flush()
            cur = {"parts": [ch], "font": font, "size": size, "size_key": size_key,
                   "color": color, "x0": left, "x1": right, "top": top,
                   "bot": bottom, "line": line,
                   "est_sizes": [size] if est_size else None}

    flush()


def _report_stats(stats: _Stats, index: int) -> None:
    if not stats.degraded:
        return
    print("[pdf] 第 %d 页（index=%d）：字符 %d，跳过 %d（Unicode 映射错误 %d），"
          "字体名缺失 %d，字号按字框估算 %d，颜色回退黑 %d"
          % (index + 1, index, stats.chars, stats.skipped, stats.unicode_err,
             stats.no_font, stats.est_size, stats.no_color),
          file=sys.stderr)


# ====================================================================== PDF 打开
def _open_doc(path):
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError("PDF 不存在：%s" % p)
    try:
        return pdfium.PdfDocument(str(p))
    except Exception as exc:
        raise RuntimeError("无法打开 PDF：%s（%s）" % (p, exc)) from exc


# ====================================================================== 对外 API
def parse(path: str, max_pages: int = 0) -> PdfDoc:
    """解析 PDF 的文字层，返回 PdfDoc（每页 PdfPageInfo.spans）。

    :param path: PDF 路径。
    :param max_pages: 最多解析多少页；0 或负数表示全部。
    """
    doc_pdf = _open_doc(path)
    doc = PdfDoc(source=str(path))
    try:
        total = len(doc_pdf)
        limit = total if not max_pages or max_pages <= 0 else min(int(max_pages), total)
        for index in range(limit):
            page = doc_pdf[index]
            width, height = page.get_size()
            info = PdfPageInfo(index=index, width=float(width), height=float(height))
            stats = _Stats()
            tp = None
            try:
                tp = page.get_textpage()
                stats.chars = int(tp.count_chars())
                _extract_spans(tp, info, stats)
            except Exception as exc:
                # 单页失败不拖垮整篇：留空页 + stderr 说明。
                print("[pdf] 第 %d 页（index=%d）解析失败，已跳过：%s"
                      % (index + 1, index, exc), file=sys.stderr)
            finally:
                if tp is not None:
                    try:
                        tp.close()
                    except Exception:
                        pass
                try:
                    page.close()
                except Exception:
                    pass
            _report_stats(stats, index)
            doc.pages.append(info)
    finally:
        try:
            doc_pdf.close()
        except Exception:
            pass
    return doc


def _resolve_pages(pages, total: int, one_based: bool) -> list[int]:
    """把调用方给的页号规整成 0 起的下标列表。"""
    if pages is None:
        return list(range(total))
    if isinstance(pages, int):
        pages = [pages]
    out: list[int] = []
    for p in pages:
        idx = int(p) - 1 if one_based else int(p)
        if idx < 0 or idx >= total:
            raise IndexError("页号超出范围：%r（共 %d 页，%s）"
                             % (p, total, "1 起" if one_based else "0 起"))
        if idx not in out:
            out.append(idx)
    return out


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


def _write_png_from_bitmap(dst, bitmap) -> None:
    """纯标准库 PNG 编码（无 Pillow 时用）。

    pdfium 的位图默认是 BGR(3 通道) / BGRx / BGRA，行可能有 padding（stride）。
    输出 8bit RGB、无交错、每行 filter=0 的 PNG：
    签名 + IHDR + IDAT(zlib) + IEND。
    """
    width = int(bitmap.width)
    height = int(bitmap.height)
    stride = int(bitmap.stride)
    nch = int(bitmap.n_channels)
    buf = bytes(bitmap.buffer)
    if width <= 0 or height <= 0:
        raise RuntimeError("位图尺寸非法：%dx%d" % (width, height))
    if nch not in (1, 3, 4):
        raise RuntimeError("不支持的位图格式：channels=%d" % nch)

    rows = bytearray()
    for y in range(height):
        start = y * stride
        row = buf[start:start + width * nch]
        line = bytearray(width * 3)
        if nch == 1:            # 灰度：三通道同值
            line[0::3] = row
            line[1::3] = row
            line[2::3] = row
        else:                   # BGR / BGRx / BGRA：内存里是 B,G,R[,A]
            line[0::3] = row[2:nch * width:nch]
            line[1::3] = row[1:nch * width:nch]
            line[2::3] = row[0:nch * width:nch]
        rows.append(0)          # filter type 0 (None)
        rows += line

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8bit truecolor
    data = (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", zlib.compress(bytes(rows), 6))
            + _png_chunk(b"IEND", b""))
    with open(dst, "wb") as fh:
        fh.write(data)


def page_images(path: str, out_dir: str, pages=None, scale: float = 1.5,
                one_based: bool = False) -> list[str]:
    """把指定页渲染成 PNG，返回文件路径列表（按 pages 顺序）。

    :param path: PDF 路径。
    :param out_dir: 输出目录（不存在会自动创建）。
    :param pages: 页下标列表，默认全部；用 one_based=True 可传 1 起的页码。
    :param scale: 渲染倍率（1.0 = 72dpi；1.5 ≈ 108dpi）。
    :param one_based: pages 是否按 1 起解释。
    """
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    doc_pdf = _open_doc(path)
    paths: list[str] = []
    try:
        total = len(doc_pdf)
        for idx in _resolve_pages(pages, total, one_based):
            page = doc_pdf[idx]
            bitmap = None
            try:
                bitmap = page.render(scale=float(scale))
                dst = out / ("page-%03d.png" % (idx + 1))
                if _HAS_PIL:
                    img = bitmap.to_pil().convert("RGB")
                    img.save(str(dst), format="PNG")
                else:
                    _write_png_from_bitmap(dst, bitmap)
                paths.append(str(dst))
            finally:
                if bitmap is not None:
                    try:
                        bitmap.close()
                    except Exception:
                        pass
                try:
                    page.close()
                except Exception:
                    pass
    finally:
        try:
            doc_pdf.close()
        except Exception:
            pass
    return paths


# ====================================================================== 拼版预览（省 token）
def sheet_layout(count: int, per_sheet: int = 2, gap: int = 12,
                 label_h: int = 22) -> list[list[tuple[int, int]]]:
    """计算一张拼版图里，每个页面的像素偏移 [(dx, dy), ...]，下标与页序对应。

    横向排列（1×N），页间留 gap 像素白缝，上方留 label_h 像素用来标页码。
    """
    per_sheet = max(1, int(per_sheet))
    return [[(i * gap, label_h) for i in range(per_sheet)]]


def page_sheets(path: str, out_dir: str, scale: float = 0.32,
                per_sheet: int = 2, max_pages: int = 0,
                gap: int = 12) -> list[dict]:
    """把多页拼成一张 PNG（横向并排），显著减少视觉模型的图片 token 与调用次数。

    返回 [{"image": 路径, "pages": [页码(1起), ...], "boxes": {页码: [x,y,w,h]}}, ...]，
    boxes 是该页在这张拼版图里的像素矩形，用于把归一化 bbox 换算回单页坐标。
    """
    if not _HAS_PIL:
        raise RuntimeError("拼版需要 Pillow；当前环境没有 PIL，请改用 page_images()")
    from PIL import Image as _I, ImageDraw as _D

    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    doc_pdf = _open_doc(path)
    per_sheet = max(1, int(per_sheet))
    label_h = 22
    sheets: list[dict] = []
    try:
        total = len(doc_pdf)
        idxs = list(range(total))[:max_pages] if max_pages else list(range(total))
        for start in range(0, len(idxs), per_sheet):
            chunk = idxs[start:start + per_sheet]
            imgs = []
            for idx in chunk:
                page = doc_pdf[idx]
                bitmap = None
                try:
                    bitmap = page.render(scale=float(scale))
                    imgs.append(bitmap.to_pil().convert("RGB"))
                finally:
                    if bitmap is not None:
                        try:
                            bitmap.close()
                        except Exception:
                            pass
                    try:
                        page.close()
                    except Exception:
                        pass
            if not imgs:
                continue
            w = sum(im.width for im in imgs) + gap * (len(imgs) - 1)
            h = max(im.height for im in imgs) + label_h
            sheet = _I.new("RGB", (w, h), (255, 255, 255))
            draw = _D.Draw(sheet)
            boxes: dict[int, list[int]] = {}
            dx = 0
            for im, idx in zip(imgs, chunk):
                sheet.paste(im, (dx, label_h))
                boxes[idx + 1] = [dx, label_h, im.width, im.height]
                draw.text((dx + 6, 5), "P%d" % (idx + 1), fill=(0, 0, 0))
                dx += im.width + gap
            dst = out / ("sheet-%03d.png" % (start // per_sheet + 1))
            sheet.save(str(dst), format="PNG")
            sheets.append({"image": str(dst), "pages": [i + 1 for i in chunk],
                           "boxes": boxes, "size": [w, h]})
    finally:
        try:
            doc_pdf.close()
        except Exception:
            pass
    return sheets


def sheet_boxes(sheet: dict) -> dict[int, list[int]]:
    """取拼版图里各页的像素矩形。"""
    return dict(sheet.get("boxes") or {})


# ====================================================================== 命令行自检
def _main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="PDF 文字层解析 / 页面渲染（三审用）")
    ap.add_argument("pdf", help="PDF 路径")
    ap.add_argument("--pages", type=int, default=0, help="最多解析几页，0=全部")
    ap.add_argument("--images", default="", help="渲染 PNG 的输出目录")
    ap.add_argument("--image-pages", default="", help="要渲染的页下标，逗号分隔（0 起）")
    ap.add_argument("--scale", type=float, default=1.5, help="渲染倍率，默认 1.5")
    args = ap.parse_args(argv)

    doc = parse(args.pdf, max_pages=args.pages)
    print("summary:", doc.summary())
    for pg in doc.pages:
        if pg.spans:
            print("第 %d 页 %d 个 span，首个：%r" % (pg.index + 1, len(pg.spans),
                                                  pg.spans[0]))
    if args.images:
        pg_list = ([int(x) for x in args.image_pages.split(",") if x.strip()]
                   if args.image_pages else None)
        print("images:", page_images(args.pdf, args.images, pages=pg_list,
                                     scale=args.scale))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
