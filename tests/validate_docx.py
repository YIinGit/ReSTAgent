"""导出文件的结构体检：把「Word 说文件损坏」这一类问题在本地先抓出来。

用法：
    python tests/validate_docx.py 文件.docx [文件2.docx ...]

检查项（都是 Word 会直接报「无法读取的内容」的硬伤）：
  1. 包内每个 xml/rels 部件都能解析
  2. document.xml 根元素声明了 mc:Ignorable 里引用的所有前缀
  3. w:p 里 w:pPr 必须是第一个子元素；w:r 里 w:rPr 必须是第一个子元素
  4. 批注区间：每个 id 恰好一个 start / end / reference，start 与 end 在同一段内且顺序正确，
     且 reference 紧跟在同 id 的 end 之后
  5. w:ins / w:del 都有 w:id / w:author / w:date，修订 id 不重复
  6. w:del 里不许出现 w:t（必须改成 w:delText）
  7. comments.xml 的 id 唯一，且都能在正文里找到引用
  8. docProps/core.xml 里同一个元素不能出现两次
  9. 每个部件都能在 [Content_Types].xml 里找到类型；rels 指向的部件都真实存在
"""
from __future__ import annotations

import glob
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
XMLNS_RE = re.compile(r'xmlns:([A-Za-z0-9_.-]+)\s*=\s*"([^"]*)"')
ROOT_RE = re.compile(r"<(?:[A-Za-z0-9_.-]+:)?(?:document|comments|coreProperties)\b[^>]*>")


def w(tag: str) -> str:
    return "{%s}%s" % (W, tag)


def validate(path: str) -> list[str]:
    problems: list[str] = []

    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        raw = {n: zf.read(n) for n in names}

    dup_entries = {n for n in names if names.count(n) > 1}
    if dup_entries:
        problems.append("[zip] 包内存在重复条目：%s" % ",".join(sorted(dup_entries)))

    # 1. 所有 xml 部件可解析
    for n in names:
        if n.endswith((".xml", ".rels")):
            try:
                ET.fromstring(raw[n])
            except ET.ParseError as e:
                problems.append("[xml] %s 解析失败：%s" % (n, e))

    # 2. 每个部件的根元素都要声明 mc:Ignorable 里引用的前缀
    for n in names:
        if not n.endswith((".xml", ".rels")) or n == "[Content_Types].xml":
            continue
        text = raw[n].decode("utf-8", "ignore")
        m = ROOT_RE.search(text)
        if not m:
            continue
        decl = dict(XMLNS_RE.findall(m.group(0)))
        ign = re.search(r'Ignorable="([^"]*)"', m.group(0))
        if not ign:
            continue
        for prefix in ign.group(1).split():
            if prefix not in decl:
                problems.append(
                    "[ns] %s 的 mc:Ignorable 引用了未声明的前缀 %r（Word 会判定文件损坏）"
                    % (n, prefix)
                )

    doc_xml = raw.get("word/document.xml", b"")
    document = ET.fromstring(doc_xml) if doc_xml else None

    # 3~6. 正文结构
    comment_ranges: dict[str, dict[str, int]] = {}
    comment_refs: dict[str, int] = {}
    rev_ids: list[str] = []
    if document is not None:
        for p in document.iter(w("p")):
            kids = list(p)
            for i, kid in enumerate(kids):
                if kid.tag == w("pPr") and i != 0:
                    problems.append("[order] w:p 的 w:pPr 不在第一位（第 %d 个）" % i)
            for r in p.iter(w("r")):
                rkids = list(r)
                for i, kid in enumerate(rkids):
                    if kid.tag == w("rPr") and i != 0:
                        problems.append("[order] w:r 的 w:rPr 不在第一位（第 %d 个）" % i)

            # 段内批注标记：start 必须先于 end，且在段内闭环
            pending: list[str] = []
            last_end: str | None = None
            for i, kid in enumerate(kids):
                if kid.tag == w("commentRangeStart"):
                    cid = kid.get(w("id"))
                    comment_ranges.setdefault(cid, {}).setdefault("start", 0)
                    comment_ranges[cid]["start"] += 1
                    pending.append(cid)
                elif kid.tag == w("commentRangeEnd"):
                    cid = kid.get(w("id"))
                    comment_ranges.setdefault(cid, {}).setdefault("end", 0)
                    comment_ranges[cid]["end"] += 1
                    if cid in pending:
                        pending.remove(cid)
                    last_end = cid
                    nxt = kids[i + 1] if i + 1 < len(kids) else None
                    ok = (
                        nxt is not None
                        and nxt.tag == w("r")
                        and any(c.tag == w("commentReference") and c.get(w("id")) == cid
                                for c in nxt)
                    )
                    if not ok:
                        problems.append(
                            "[comment] 批注 %s 的 commentRangeEnd 后面没有紧跟同 id 的 commentReference"
                            % cid
                        )
            if pending:
                problems.append("[comment] 段落内有未闭合的批注区间：%s" % ",".join(pending))
            # 引用写在 run 里，不是段落的直接子元素
            for ref in p.iter(w("commentReference")):
                cid = ref.get(w("id"))
                comment_refs[cid] = comment_refs.get(cid, 0) + 1

        for tag in ("ins", "del"):
            for el in document.iter(w(tag)):
                for attr in ("id", "author", "date"):
                    if not el.get(w(attr)):
                        problems.append("[rev] w:%s 缺少 w:%s" % (tag, attr))
                rid = el.get(w("id"))
                if rid:
                    rev_ids.append(rid)
                if tag == "del":
                    for t in el.iter(w("t")):
                        problems.append("[rev] w:del 里出现了 w:t，应使用 w:delText")

    dup = {i for i in rev_ids if rev_ids.count(i) > 1}
    if dup:
        problems.append("[rev] 修订 id 重复：%s" % ",".join(sorted(dup)))

    # 7. comments.xml
    comment_ids: list[str] = []
    if "word/comments.xml" in raw:
        try:
            croot = ET.fromstring(raw["word/comments.xml"])
        except ET.ParseError:
            croot = None
        if croot is not None:
            for c in croot.findall(w("comment")):
                cid = c.get(w("id"))
                comment_ids.append(cid)
                if c.find(w("p")) is None:
                    problems.append("[comment] 批注 %s 没有 w:p" % cid)
            d = {i for i in comment_ids if comment_ids.count(i) > 1}
            if d:
                problems.append("[comment] comments.xml 里 id 重复：%s" % ",".join(sorted(d)))
            missing = [i for i in comment_ids if i not in comment_refs]
            if missing:
                problems.append("[comment] 批注正文里有、正文却没引用：%s" % ",".join(missing))

    for cid, cnt in comment_ranges.items():
        if cnt.get("start", 0) != 1 or cnt.get("end", 0) != 1:
            problems.append("[comment] 批注 %s 的 start/end 数量异常：%s" % (cid, cnt))
    for cid, cnt in comment_refs.items():
        if cnt != 1:
            problems.append("[comment] 批注 %s 的 commentReference 出现 %d 次" % (cid, cnt))
        if cid not in comment_ids:
            problems.append("[comment] 正文引用了不存在的批注 id %s" % cid)

    # 8. core.xml 元素不重复
    if "docProps/core.xml" in raw:
        try:
            coreroot = ET.fromstring(raw["docProps/core.xml"])
        except ET.ParseError:
            coreroot = None
        if coreroot is not None:
            tags = [c.tag for c in coreroot]
            d = {t for t in tags if tags.count(t) > 1}
            if d:
                names_d = [t.split("}")[-1] for t in d]
                problems.append("[core] core.xml 里元素重复出现：%s" % ",".join(sorted(names_d)))

    # 9. 内容类型与关系
    ct_raw = raw.get("[Content_Types].xml")
    if ct_raw:
        ct = ET.fromstring(ct_raw)
        defaults = {e.get("Extension").lower() for e in ct.findall("{%s}Default" % CT)
                    if e.get("Extension")}
        overrides = {e.get("PartName") for e in ct.findall("{%s}Override" % CT)
                     if e.get("PartName")}
        for n in names:
            if n.endswith("/") or n == "[Content_Types].xml":
                continue
            ext = n.rsplit(".", 1)[-1].lower()
            if "/" + n not in overrides and ext not in defaults:
                problems.append("[ct] 部件没有内容类型：%s" % n)

    for rels_name, base in ((n, n.rsplit("_rels/", 1)[0]) for n in names if n.endswith(".rels")):
        try:
            rroot = ET.fromstring(raw[rels_name])
        except ET.ParseError:
            continue
        for rel in rroot.findall("{%s}Relationship" % PR):
            if rel.get("TargetMode") == "External":
                continue
            target = rel.get("Target") or ""
            if target.startswith("/"):
                full = target.lstrip("/")
            else:
                full = os.path.normpath(base + target).replace("\\", "/")
            if full not in names:
                problems.append("[rel] %s 指向不存在的部件：%s" % (rels_name, full))

    return problems


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    targets = sys.argv[1:] or sorted(glob.glob(os.path.join("tests", "*reviewed*.docx")))
    bad = 0
    for t in targets:
        probs = validate(t)
        if probs:
            bad += 1
            print("FAIL", t)
            for p in probs:
                print("     ", p)
        else:
            print("OK  ", t)
    sys.exit(1 if bad else 0)
