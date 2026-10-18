"""OOXML 写回：把审稿意见变成 Word 批注（原因）与修订痕迹（改动）。

- 修改类意见（replace/insert/delete）写为 w:ins / w:del 修订，作者可直接「接受/拒绝」。
- 所有意见都写为批注，说明问题、建议与教材依据。
- 只替换 word/document.xml 与 comments 相关部件，其余包内容原样复制，保证不丢样式/图片/页眉页脚。
"""
from __future__ import annotations

import copy
import datetime as dt
import html
import re
import zipfile
import xml.etree.ElementTree as ET

from .reader import CONTAINER_TAGS, XML_NS, Doc, q

COMMENTS_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
COMMENTS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

ROOT_TAG_RE = re.compile(r"<(?:[A-Za-z0-9_.-]+:)?document\b[^>]*>")
XMLNS_RE = re.compile(r'xmlns:([A-Za-z0-9_.-]+)\s*=\s*"([^"]*)"')


def _root_decls(xml: bytes) -> list[tuple[str, str]]:
    """原文档根元素上的 xmlns 声明；ElementTree 会丢掉「只被 mc:Ignorable 引用」的前缀。"""
    m = ROOT_TAG_RE.search(xml.decode("utf-8", "ignore"))
    if not m:
        return []
    return XMLNS_RE.findall(m.group(0))


def _restore_decls(xml: bytes, decls: list[tuple[str, str]]) -> bytes:
    """把丢失的 xmlns 声明补回序列化后的根元素，保证 mc:Ignorable 引用的前缀都存在。"""
    if not decls:
        return xml
    s = xml.decode("utf-8")
    m = ROOT_TAG_RE.search(s)
    if not m:
        return xml
    tag = m.group(0)
    missing = [' xmlns:%s="%s"' % (p, u) for p, u in decls
               if p not in ("xml", "xmlns") and ("xmlns:%s=" % p) not in tag]
    if not missing:
        return xml
    if tag.endswith("/>"):
        new = tag[:-2] + "".join(missing) + "/>"
    else:
        new = tag[:-1] + "".join(missing) + ">"
    return (s[:m.start()] + new + s[m.end():]).encode("utf-8")



class AnchorError(Exception):
    pass


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_space(el):
    el.set("{%s}space" % XML_NS, "preserve")
    return el


def _new_t(text: str):
    t = ET.Element(q("t"))
    t.text = text
    return _ensure_space(t)


def _segments(p):
    """段内可见文本片段（跳过已删除内容），offset 与 reader 的 para.text 一致。"""
    segs = []
    off = 0

    class S:
        __slots__ = ("run", "tel", "text", "start", "direct")

        def __init__(self, run, tel, text, start, direct):
            self.run, self.tel, self.text, self.start, self.direct = run, tel, text, start, direct

        @property
        def end(self):
            return self.start + len(self.text)

    def walk(parent, direct):
        nonlocal off
        for c in parent:
            if c.tag == q("r"):
                for t in c:
                    if t.tag == q("t"):
                        txt = t.text or ""
                        if txt:
                            segs.append(S(c, t, txt, off, direct))
                            off += len(txt)
            elif c.tag in (q("del"), q("moveFrom")):
                continue
            elif c.tag in CONTAINER_TAGS:
                walk(c, False)

    walk(p, True)
    return segs


def _runs(p):
    """[(run, start, end)]，仅直接子 run；已插入（w:ins 内）的文字不参与再编辑。"""
    out = []
    for s in _segments(p):
        if not s.direct:
            continue
        if out and out[-1][0] is s.run:
            out[-1] = (s.run, out[-1][1], s.end)
        else:
            out.append((s.run, s.start, s.end))
    return out


def _split_run(p, run, k):
    if k <= 0:
        return run
    children = list(p)
    if run not in children:
        raise AnchorError("run 不是段落直接子节点")
    idx = children.index(run)
    rpr = run.find(q("rPr"))
    head, tail = ET.Element(q("r")), ET.Element(q("r"))
    if rpr is not None:
        head.append(copy.deepcopy(rpr))
        tail.append(copy.deepcopy(rpr))
    pos = 0
    for child in list(run):
        if child.tag == q("rPr"):
            continue
        if child.tag == q("t"):
            txt = child.text or ""
            if pos + len(txt) <= k:
                head.append(_new_t(txt))
            elif pos >= k:
                tail.append(_new_t(txt))
            else:
                cut = k - pos
                if txt[:cut]:
                    head.append(_new_t(txt[:cut]))
                if txt[cut:]:
                    tail.append(_new_t(txt[cut:]))
            pos += len(txt)
        else:
            (head if pos < k else tail).append(copy.deepcopy(child))
    p.remove(run)
    p.insert(idx, head)
    p.insert(idx + 1, tail)
    return tail


def _ensure_boundary(p, offset):
    """确保 offset 处是 run 边界，返回「从 offset 开始的 run」；offset==段尾返回 None。"""
    segs = _segments(p)
    if not segs:
        return None
    total = segs[-1].end
    if offset <= 0:
        return segs[0].run
    if offset >= total:
        return None
    for seg in segs:
        if seg.start <= offset < seg.end:
            if not seg.direct:
                raise AnchorError("目标区间落在超链接/域等非直接段落内容中")
            run_start = next(s.start for s in segs if s.run is seg.run)
            if offset == run_start:
                return seg.run
            return _split_run(p, seg.run, offset - run_start)
    return None


class Exporter:
    def __init__(self, doc: Doc, author: str = "ReSTAgent", font: str = ""):
        self.doc = doc
        self.author = author or "ReSTAgent"
        self.font = (font or "").strip()
        self.comments: list[dict] = []
        self._comment_part = None   # 现有 comments.xml 的成员名
        self._max_comment_id = 0
        self._rev_id = 0
        self.stats = {"comments": 0, "revisions": 0, "fallback": 0, "skipped": 0}
        self._scan_existing()

    # ---------- 已有批注/修订探查 ----------
    def _scan_existing(self):
        with zipfile.ZipFile(self.doc.path) as zf:
            names = zf.namelist()
            rels_name = "word/_rels/document.xml.rels"
            if rels_name in names:
                rels = zf.read(rels_name).decode("utf-8", "ignore")
                m = re.search(
                    r'<Relationship[^>]*Type="[^"]*/comments"[^>]*Target="([^"]+)"', rels
                )
                if m:
                    target = m.group(1)
                    part = target if target.startswith("word/") else "word/" + target.lstrip("/")
                    if part in names:
                        self._comment_part = part
                        try:
                            root = ET.fromstring(zf.read(part))
                            for c in root.findall(q("comment")):
                                try:
                                    self._max_comment_id = max(
                                        self._max_comment_id, int(c.get(q("id")) or 0)
                                    )
                                except Exception:
                                    pass
                        except Exception:
                            pass
            docxml = zf.read("word/document.xml")
            for m in re.finditer(rb'<w:commentReference w:id="(\d+)"', docxml):
                try:
                    self._max_comment_id = max(self._max_comment_id, int(m.group(1)))
                except Exception:
                    pass
            for m in re.finditer(rb'<w:(?:ins|del) [^>]*w:id="(\d+)"', docxml):
                try:
                    self._rev_id = max(self._rev_id, int(m.group(1)))
                except Exception:
                    pass

    def _next_comment_id(self) -> int:
        self._max_comment_id += 1
        return self._max_comment_id

    def _next_rev_id(self) -> int:
        self._rev_id += 1
        return self._rev_id

    # ---------- 主入口 ----------
    def apply(self, items: list[dict]):
        """items: pid/start/end/action/after/comment。需按文档倒序处理外部先排好，这里再保险排序。"""
        def sort_key(it):
            pid = it.get("pid") or "P000"
            try:
                pno = int(pid[1:])
            except Exception:
                pno = 0
            return (pno, it.get("start", 0))

        for it in sorted(items, key=sort_key, reverse=True):
            try:
                self._apply_one(it)
            except AnchorError:
                self.stats["fallback"] += 1
                self._apply_fallback(it)
            except Exception:
                self.stats["skipped"] += 1

    def _apply_one(self, it: dict):
        pid = it.get("pid")
        para = self.doc.para(pid) if pid else None
        if para is None:
            raise AnchorError("段落不存在")
        p = para.el
        start = int(it.get("start") or 0)
        end = int(it.get("end") or 0)
        action = (it.get("action") or "comment_only").lower()
        after = it.get("after") or ""
        text = it.get("comment") or ""
        total = len(para.text)

        if action in ("replace", "delete", "insert"):
            if action != "insert" and end <= start:
                action = "comment_only"
            if action == "insert" and start < 0:
                raise AnchorError("插入位置非法")

        if action == "comment_only" and not text:
            self.stats["skipped"] += 1
            return

        if action == "replace" and not after:
            action = "delete"
        if action == "replace" and end <= start:
            action = "insert"
        if action == "delete" and end <= start:
            action = "comment_only"

        # 先切出精确的 run 边界（offset 基于段落可见文本，切分不改变文本长度）
        _ensure_boundary(p, max(0, min(start, total)))
        _ensure_boundary(p, max(0, min(end, total)))

        affected = [run for run, s, e in _runs(p) if s >= start and e <= end and e > s]
        if action in ("replace", "delete") and not affected:
            raise AnchorError("未找到可改动的 run")

        deleted = None
        if action in ("replace", "delete"):
            deleted = self._wrap_delete(p, affected)
            self.stats["revisions"] += 1

        inserted = None
        if action in ("replace", "insert") and after:
            anchor_run = affected[0] if affected else None
            if anchor_run is None:
                # 纯插入：插在 start 边界前
                runs = _runs(p)
                anchor_run = next((r for r, s, _e in runs if s == start), None)
            rpr = anchor_run.find(q("rPr")) if anchor_run is not None else None
            inserted = self._make_ins(p, after, rpr)
            children = list(p)
            if deleted is not None and deleted in children:
                p.insert(children.index(deleted) + 1, inserted)
            elif anchor_run is not None and anchor_run in children:
                p.insert(children.index(anchor_run), inserted)
            else:
                p.append(inserted)
            self.stats["revisions"] += 1

        cid = None
        if text:
            cid = self._next_comment_id()
            self.comments.append({"id": cid, "text": text, "date": _now()})

        if cid is None:
            return

        # 批注区间：包住本次被改动的内容（含修订痕迹）；纯批注则包住命中的原文
        first_el = None
        last_el = None
        if deleted is not None:
            first_el = deleted
            last_el = inserted if inserted is not None else deleted
        elif inserted is not None:
            first_el = last_el = inserted
        elif affected:
            first_el, last_el = affected[0], affected[-1]
        else:
            runs = _runs(p)
            cand = next((r for r, s, e in runs if s >= start and e > start), None)
            if cand is None and runs:
                cand = next((r for r, s, e in runs if e <= end), None)
            first_el = last_el = cand
        if first_el is None:
            raise AnchorError("无锚点")
        children = list(p)
        if first_el not in children:
            raise AnchorError("锚点已失效")
        i1 = children.index(first_el)
        # w:pPr 必须是 w:p 的第一个子元素，批注标记不能插到它前面
        if i1 == 0 and children and children[0].tag == q("pPr"):
            i1 = 1
        marker = ET.Element(q("commentRangeStart"))
        marker.set(q("id"), str(cid))
        p.insert(i1, marker)

        children = list(p)
        if last_el is not None and last_el in children:
            i2 = children.index(last_el) + 1
        else:
            i2 = len(children)
        endm = ET.Element(q("commentRangeEnd"))
        endm.set(q("id"), str(cid))
        ref = ET.Element(q("r"))
        rpr = ET.SubElement(ref, q("rPr"))
        st = ET.SubElement(rpr, q("rStyle"))
        st.set(q("val"), "CommentReference")
        cref = ET.SubElement(ref, q("commentReference"))
        cref.set(q("id"), str(cid))
        p.insert(i2, endm)
        p.insert(i2 + 1, ref)
        self.stats["comments"] += 1

    def _apply_fallback(self, it: dict):
        para = self.doc.para(it.get("pid") or "")
        text = it.get("comment") or ""
        if para is None or not text or not para.text:
            self.stats["skipped"] += 1
            return
        try:
            self._apply_one(
                {"pid": para.pid, "start": 0, "end": len(para.text),
                 "action": "comment_only", "comment": text}
            )
        except Exception:
            self.stats["skipped"] += 1

    def _wrap_delete(self, p, runs):
        children = list(p)
        idxs = [children.index(r) for r in runs]
        i1, i2 = min(idxs), max(idxs)
        groups, cur = [], []
        for i in range(i1, i2 + 1):
            c = children[i]
            if c.tag == q("r"):
                cur.append(c)
            elif cur:
                groups.append(cur)
                cur = []
        if cur:
            groups.append(cur)
        first_del = None
        for group in reversed(groups):
            idx0 = list(p).index(group[0])
            dele = ET.Element(q("del"))
            dele.set(q("id"), str(self._next_rev_id()))
            dele.set(q("author"), self.author)
            dele.set(q("date"), _now())
            for r in group:
                p.remove(r)
            for r in group:
                for t in r.findall(q("t")):
                    t.tag = q("delText")
                    _ensure_space(t)
                dele.append(r)
            p.insert(idx0, dele)
            first_del = dele
        return first_del

    def _make_ins(self, p, text, rpr_source):
        ins = ET.Element(q("ins"))
        ins.set(q("id"), str(self._next_rev_id()))
        ins.set(q("author"), self.author)
        ins.set(q("date"), _now())
        run = ET.SubElement(ins, q("r"))
        if rpr_source is not None:
            rpr = copy.deepcopy(rpr_source)
            for bad in rpr.findall(q("rPrChange")):
                rpr.remove(bad)
            run.append(rpr)
        run.append(_new_t(text))
        return ins

    # ---------- 保存 ----------
    def save(self, out_path):
        with zipfile.ZipFile(self.doc.path) as zin:
            infos = zin.infolist()
            members = {i.filename: zin.read(i.filename) for i in infos}

        # 先记下原文档根元素上的命名空间声明：ElementTree 只保留实际用到的前缀，
        # 会把 mc:Ignorable 里引用、但元素上没用到的 w14/wp14 声明丢掉，导致 Word 报文件损坏。
        decls = _root_decls(members["word/document.xml"])
        for prefix, uri in decls:
            try:
                ET.register_namespace(prefix, uri)
            except Exception:
                pass
        if self.font:
            _force_eastasia(self.doc.root, self.font)
            if "word/styles.xml" in members:
                members["word/styles.xml"] = _style_with_font(members["word/styles.xml"], self.font)
                for prefix, uri in decls:
                    try:
                        ET.register_namespace(prefix, uri)
                    except Exception:
                        pass
        members["word/document.xml"] = _restore_decls(
            ET.tostring(self.doc.root, encoding="UTF-8", xml_declaration=True), decls
        )

        if self.comments:
            if self._comment_part and self._comment_part in members:
                part = self._comment_part
                try:
                    root = ET.fromstring(members[part])
                except Exception:
                    root = ET.Element(q("comments"))
                root.extend(self._comment_elements())
                members[part] = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
            else:
                part = "word/comments.xml"
                root = ET.Element(q("comments"))
                root.extend(self._comment_elements())
                members[part] = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
                members["[Content_Types].xml"] = _add_content_type(members["[Content_Types].xml"])
                members["word/_rels/document.xml.rels"] = _add_relationship(
                    members["word/_rels/document.xml.rels"]
                )

        order = [i.filename for i in infos]
        if "word/comments.xml" in members and "word/comments.xml" not in order:
            order.append("word/comments.xml")
        if "docProps/core.xml" in members:
            members["docProps/core.xml"] = _stamp_core(members["docProps/core.xml"], self.author)
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for name in order:
                zout.writestr(name, members[name])

    def _comment_elements(self):
        out = []
        for c in self.comments:
            el = ET.Element(q("comment"))
            el.set(q("id"), str(c["id"]))
            el.set(q("author"), self.author)
            el.set(q("initials"), "RA")
            el.set(q("date"), c["date"])
            lines = [ln for ln in (c["text"] or "").split("\n")]
            for ln in lines:
                p = ET.SubElement(el, q("p"))
                r = ET.SubElement(p, q("r"))
                r.append(_new_t(ln))
            out.append(el)
        return out


def _set_core_el(s: str, tag: str, value: str) -> str:
    """就地设置 core.xml 里的某个元素值：元素可重复出现是无效的，必须改而不是再追加一个。"""
    paired = re.compile(r"<%s(?:\s[^>]*)?>.*?</%s>" % (tag, tag), re.S)
    m = paired.search(s)
    if m:
        open_tag = re.match(r"<%s(?:\s[^>]*)?>" % tag, m.group(0)).group(0)
        return s[:m.start()] + open_tag + value + "</%s>" % tag + s[m.end():]
    selfclose = re.compile(r"<%s\s*/>" % tag)
    if selfclose.search(s):
        return selfclose.sub("<%s>%s</%s>" % (tag, value, tag), s, count=1)
    return s.replace("</cp:coreProperties>",
                     "<%s>%s</%s></cp:coreProperties>" % (tag, value, tag), 1)


def _ensure_rfonts(rpr, font: str):
    """确保 rPr 里有 rFonts 并把中文字体定为 font（去掉主题引用）。"""
    f = rpr.find(q("rFonts"))
    if f is None:
        f = ET.Element(q("rFonts"))
        idx = 0
        for i, c in enumerate(list(rpr)):
            if c.tag == q("rStyle"):
                idx = i + 1
        rpr.insert(idx, f)
    k = q("eastAsiaTheme")
    if k in f.attrib:
        del f.attrib[k]
    f.set(q("eastAsia"), font)
    return f


def _force_eastasia(root, font: str):
    """正文里已有 rFonts 的 run：把中文字体改成 font（原本没写的靠样式继承，不必逐段加）。"""
    for rpr in root.iter(q("rPr")):
        if rpr.find(q("rFonts")) is not None:
            _ensure_rfonts(rpr, font)


def _style_with_font(data: bytes, font: str) -> bytes:
    """改写 styles.xml：文档默认字体与 Normal 样式的中文字体。"""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return data

    dd = root.find(q("docDefaults"))
    if dd is not None:
        rpr_default = dd.find(q("rPrDefault"))
        if rpr_default is not None:
            rpr = rpr_default.find(q("rPr"))
            if rpr is None:
                rpr = ET.SubElement(rpr_default, q("rPr"))
            _ensure_rfonts(rpr, font)

    tail = {q("tblPr"), q("trPr"), q("tcPr"), q("tblStylePr")}
    for st in root.findall(q("style")):
        sid = st.get(q("styleId")) or ""
        is_default = st.get(q("default")) == "1"
        if sid in ("Normal", "a") or is_default:
            rpr = st.find(q("rPr"))
            if rpr is None:
                rpr = ET.Element(q("rPr"))
                idx = len(list(st))
                for i, c in enumerate(list(st)):
                    if c.tag in tail:
                        idx = i
                        break
                st.insert(idx, rpr)
            _ensure_rfonts(rpr, font)
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True)


def _stamp_core(core: bytes, author: str) -> bytes:
    """在文档属性里留下「最后由谁改的」，方便事后辨认哪些文件被审过。"""
    try:
        s = core.decode("utf-8")
    except Exception:
        return core
    if "</cp:coreProperties>" not in s:
        return core
    name = html.escape(author or "ReSTAgent")
    s = _set_core_el(s, "cp:lastModifiedBy", name)
    s = _set_core_el(s, "dcterms:modified", _now())
    return s.encode("utf-8")


def _add_content_type(data: bytes) -> bytes:
    if b"comments+xml" in data:
        return data
    s = data.decode("utf-8")
    s = s.replace(
        "</Types>",
        '<Override PartName="/word/comments.xml" ContentType="%s"/></Types>' % COMMENTS_CT,
    )
    return s.encode("utf-8")


def _add_relationship(data: bytes) -> bytes:
    s = data.decode("utf-8")
    ids = [int(m) for m in re.findall(r'Id="rId(\d+)"', s)]
    rid = max(ids + [0]) + 1
    s = s.replace(
        "</Relationships>",
        '<Relationship Id="rId%d" Type="%s" Target="comments.xml"/></Relationships>'
        % (rid, COMMENTS_REL),
    )
    return s.encode("utf-8")


def export_docx(doc: Doc, items: list[dict], out_path, author: str = "ReSTAgent",
                font: str = "") -> dict:
    """items 需含 pid/start/end/action/after/comment。font 非空则统一正文字体。返回统计。"""
    exp = Exporter(doc, author, font)
    exp.apply(items)
    exp.save(out_path)
    return {"stats": exp.stats, "comments": len(exp.comments)}
