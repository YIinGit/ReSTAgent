"""OOXML 读取：把 .docx 解析成「段落编号 + 字符偏移」模型。

设计要点
- 零第三方依赖：只用 zipfile + xml.etree。
- 每个正文段落分配稳定编号 P001、P002……（含表格单元格内段落），
  模型只需返回段落号 + 原文片段，服务端负责精确定位。
- 保留 python-docx 无法表达的细节（run 级文本位置），供批注/修订注入使用。
"""
from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "v": "urn:schemas-microsoft-com:vml",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "w16cid": "http://schemas.microsoft.com/office/word/2016/wordml/cid",
    "wp14": "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing",
}

W = NS["w"]
XML_NS = "http://www.w3.org/XML/1998/namespace"

for _p, _u in NS.items():
    try:
        ET.register_namespace(_p, _u)
    except Exception:
        pass


def q(tag: str) -> str:
    return "{%s}%s" % (W, tag)


CONTAINER_TAGS = {
    q("hyperlink"),
    q("smartTag"),
    q("ins"),
    q("fldSimple"),
    q("sdt"),
    q("sdtContent"),
    q("customXml"),
    q("moveTo"),
    q("dir"),
    q("bdo"),
}

_XMLNS = re.compile(rb'xmlns:([A-Za-z0-9_.\-]+)="([^"]*)"')


def register_namespaces(data: bytes) -> list[tuple[str, str]]:
    """用文档自身的 xmlns 声明注册前缀，保证回写时前缀不漂移。"""
    seen = []
    head = data[:60000]
    for prefix_b, uri_b in _XMLNS.findall(head):
        prefix = prefix_b.decode("utf-8", "ignore")
        uri = uri_b.decode("utf-8", "ignore")
        if not prefix or prefix in ("xml", "xmlns"):
            continue
        try:
            ET.register_namespace(prefix, uri)
            seen.append((prefix, uri))
        except Exception:
            pass
    return seen


class Seg:
    """段内一个 w:t 文本片段。"""

    __slots__ = ("run", "tel", "text", "start", "direct")

    def __init__(self, run, tel, text, start, direct):
        self.run = run
        self.tel = tel
        self.text = text
        self.start = start
        self.direct = direct

    @property
    def end(self) -> int:
        return self.start + len(self.text)


class Para:
    __slots__ = ("index", "pid", "el", "text", "segs", "style", "level",
                 "in_table", "table_idx", "row", "col", "has_image", "is_list")

    def __init__(self, index, el):
        self.index = index
        self.pid = "P%03d" % index
        self.el = el
        self.text = ""
        self.segs: list[Seg] = []
        self.style = ""
        self.level = 0
        self.in_table = False
        self.table_idx = 0
        self.row = 0
        self.col = 0
        self.has_image = False
        self.is_list = False

    def __repr__(self):
        return "<Para %s %r>" % (self.pid, self.text[:20])


class Doc:
    def __init__(self, path):
        import pathlib

        self.path = pathlib.Path(path)
        self.name = self.path.name
        self._zip_names: list[str] = []
        self.root = None
        self.body = None
        self.paras: list[Para] = []
        self.by_pid: dict[str, Para] = {}
        self.styles: dict[str, dict] = {}
        self.title = ""
        self._load()

    # ---------- 解析 ----------
    def _load(self):
        with zipfile.ZipFile(self.path) as zf:
            self._zip_names = zf.namelist()
            if "word/document.xml" not in self._zip_names:
                raise ValueError("不是有效的 .docx：缺少 word/document.xml")
            docxml = zf.read("word/document.xml")
            register_namespaces(docxml)
            self.root = ET.fromstring(docxml)
            if "word/styles.xml" in self._zip_names:
                try:
                    self.styles = _parse_styles(zf.read("word/styles.xml"))
                except Exception:
                    self.styles = {}
        self.body = self.root.find(q("body"))
        if self.body is None:
            raise ValueError("文档结构异常：找不到 w:body")
        self._walk(self.body, None)
        self._pick_title()

    def _walk(self, container, ctx):
        for child in list(container):
            tag = child.tag
            if tag == q("p"):
                self._add_para(child, ctx)
            elif tag == q("tbl"):
                tctx = {"table": (ctx or {}).get("table", 0) + 1, "row": 0, "col": 0}
                for ri, tr in enumerate(child.findall(q("tr"))):
                    for ci, tc in enumerate(tr.findall(q("tc"))):
                        self._walk(tc, {"table": tctx["table"], "row": ri, "col": ci})
            elif tag in (q("sdt"),):
                content = child.find(q("sdtContent"))
                if content is not None:
                    self._walk(content, ctx)
            elif tag == q("customXml"):
                self._walk(child, ctx)

    def _add_para(self, p_el, ctx):
        para = Para(len(self.paras) + 1, p_el)
        if ctx:
            para.in_table = True
            para.table_idx = ctx.get("table", 0)
            para.row = ctx.get("row", 0)
            para.col = ctx.get("col", 0)
        segs: list[Seg] = []
        offset = 0

        def walk_runs(parent, direct):
            nonlocal offset
            for child in parent:
                if child.tag == q("r"):
                    for c in child:
                        if c.tag == q("t"):
                            txt = c.text or ""
                            if txt:
                                segs.append(Seg(child, c, txt, offset, direct))
                                offset += len(txt)
                elif child.tag == q("del") or child.tag == q("moveFrom"):
                    continue  # 已删除内容不计入可见文本
                elif child.tag in CONTAINER_TAGS:
                    walk_runs(child, False)

        walk_runs(p_el, True)
        para.segs = segs
        para.text = "".join(s.text for s in segs)
        ppr = p_el.find(q("pPr"))
        if ppr is not None:
            ps = ppr.find(q("pStyle"))
            if ps is not None:
                sid = ps.get(q("val")) or ""
                para.style = sid
                info = self.styles.get(sid, {})
                lv = info.get("outline") or _heading_level(info.get("name", "") or sid)
                if lv:
                    para.level = lv
            ol = ppr.find(q("outlineLvl"))
            if ol is not None:
                try:
                    para.level = int(ol.get(q("val"))) + 1
                except Exception:
                    pass
            para.is_list = ppr.find(q("numPr")) is not None
        para.has_image = (
            p_el.find(".//" + q("drawing")) is not None
            or p_el.find(".//" + q("pict")) is not None
        )
        self.paras.append(para)
        self.by_pid[para.pid] = para

    def _pick_title(self):
        for para in self.paras:
            t = para.text.strip()
            if not t:
                continue
            if para.level and len(t) <= 80:
                self.title = t
                return
        for para in self.paras:
            t = para.text.strip()
            if t:
                self.title = t[:60]
                return

    # ---------- 查询 ----------
    @property
    def chars(self) -> int:
        return sum(len(p.text) for p in self.paras)

    @property
    def images(self) -> int:
        return sum(1 for p in self.paras if p.has_image)

    @property
    def tables(self) -> int:
        return max((p.table_idx for p in self.paras), default=0)

    def outline(self) -> list[dict]:
        out = []
        for p in self.paras:
            if p.level and p.text.strip():
                out.append({"pid": p.pid, "level": p.level, "text": p.text.strip()[:80]})
        return out

    def plain_text(self) -> str:
        return "\n".join(p.text for p in self.paras if p.text.strip())

    def para(self, pid: str) -> Para | None:
        return self.by_pid.get(pid)

    def summary(self) -> dict:
        body_paras = [p for p in self.paras if p.text.strip()]
        return {
            "name": self.name,
            "path": str(self.path),
            "title": self.title,
            "chars": self.chars,
            "paragraphs": len(self.paras),
            "nonempty": len(body_paras),
            "tables": self.tables,
            "images": self.images,
            "outline": self.outline(),
        }

    # ---------- 文本定位 ----------
    def resolve(self, pid: str, quote: str, occurrence: int = 0):
        """在指定段落内定位 quote，返回 (start, end)；失败返回 None。"""
        para = self.by_pid.get(pid)
        if para is None or not para.text:
            return None
        text = para.text
        qq = (quote or "").strip()
        if not qq:
            return None
        # 1) 精确匹配（第 occurrence 次）
        pos = -1
        for _ in range(occurrence + 1):
            pos = text.find(qq, pos + 1)
            if pos < 0:
                break
        if pos >= 0:
            return pos, pos + len(qq)
        # 2) 空白归一化匹配
        ntext, idx = _normalize(text)
        nquote, _ = _normalize(qq)
        if nquote and nquote in ntext:
            st = ntext.find(nquote)
            return idx[st], idx[st + len(nquote) - 1] + 1
        # 3) 最长公共子串兜底（只接受高相似度，避免张冠李戴）
        import difflib

        sm = difflib.SequenceMatcher(None, text, qq, autojunk=False)
        block = sm.find_longest_match(0, len(text), 0, len(qq))
        if block.size >= max(6, int(len(qq) * 0.6)):
            return block.a, block.a + block.size
        return None


def _normalize(s: str):
    keep = []
    idx = []
    for i, ch in enumerate(s):
        if ch.isspace():
            continue
        keep.append(ch)
        idx.append(i)
    return "".join(keep), idx


def _parse_styles(data: bytes) -> dict:
    root = ET.fromstring(data)
    out = {}
    for st in root.findall(q("style")):
        sid = st.get(q("styleId")) or ""
        name_el = st.find(q("name"))
        name = (name_el.get(q("val")) if name_el is not None else "") or ""
        olvl = None
        ppr = st.find(q("pPr"))
        if ppr is not None:
            ol = ppr.find(q("outlineLvl"))
            if ol is not None:
                try:
                    olvl = int(ol.get(q("val"))) + 1
                except Exception:
                    olvl = None
        out[sid] = {"name": name, "outline": olvl}
    return out


def _heading_level(name: str) -> int:
    if not name:
        return 0
    low = name.lower()
    if "heading" in low or "标题" in name:
        m = re.search(r"(\d)", name)
        if m:
            return int(m.group(1))
        return 1
    return 0
