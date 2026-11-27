"""整篇只给批注的导出（初审用）。

为什么单独写：`docx_engine/writer.py` 是给一/二审用的（要写修订痕迹），
初审的要求不一样——**原文一字不动，只在旁边加批注**，而且部分意见是「缺板块」，
没有可锚定的原文，得挂在标题或文末。

做法与 writer.py 一致：只改 word/document.xml、新增 comments.xml，
其余部件（样式、图片、页眉页脚）原样复制。生僻前缀声明会被 ElementTree 丢掉，
所以沿用 writer 里的 _root_decls / _restore_decls 修复。
"""
from __future__ import annotations

import datetime as dt
import html
import re
import zipfile
import xml.etree.ElementTree as ET

from ..docx_engine.writer import (COMMENTS_CT, COMMENTS_REL, CT_NS, REL_NS,
                                  _restore_decls, _root_decls)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}


def _q(tag: str) -> str:
    return "{%s}%s" % (W_NS, tag)


# ------------------------------------------------------------------ 段落定位
def paragraph_texts(xml: bytes) -> list[dict]:
    """提取正文段落（含表格内段落），返回 [{index, text, style}]，index 与 doc 里的顺序一致。"""
    root = ET.fromstring(xml)
    out = []
    body = root.find(_q("body"))
    if body is None:
        return out

    def walk(node, in_table=False):
        for child in node:
            tag = child.tag.split("}")[-1]
            if tag == "p":
                text = "".join(t.text or "" for t in child.iter(_q("t")))
                style = ""
                ppr = child.find(_q("pPr"))
                if ppr is not None:
                    st = ppr.find(_q("pStyle"))
                    if st is not None:
                        style = st.get(_q("val")) or ""
                out.append({"index": len(out), "text": text, "style": style,
                            "in_table": in_table})
            elif tag in ("tbl", "tr", "tc", "sdt", "sdtContent"):
                walk(child, in_table or tag in ("tbl", "tr", "tc"))

    walk(body)
    return out


# ------------------------------------------------------------------ 注解写入
def _comment_xml(comments: list[dict]) -> bytes:
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<w:comments xmlns:w="%s">' % W_NS]
    for c in comments:
        parts.append(
            '<w:comment w:id="%d" w:author="%s" w:date="%s" w:initials="%s">'
            '<w:p><w:pPr><w:pStyle w:val="CommentText"/></w:pPr>'
            '<w:r><w:rPr><w:rFonts w:ascii="Microsoft YaHei" w:eastAsia="Microsoft YaHei"/></w:rPr>'
            '<w:t xml:space="preserve">%s</w:t></w:r></w:p></w:comment>'
            % (c["id"], html.escape(c["author"]), c["date"], html.escape(c["initials"]),
               html.escape(c["text"])))
    parts.append("</w:comments>")
    return "".join(parts).encode("utf-8")


def _add_comment_rels(rels: bytes) -> bytes:
    s = rels.decode("utf-8")
    if "comments.xml" in s:
        return rels
    nums = [int(n) for n in re.findall(r'Id="rId(\d+)"', s)]
    rid = max(nums, default=0) + 1
    add = ('<Relationship Id="rId%d" Type="%s" Target="comments.xml"/>'
           % (rid, COMMENTS_REL))
    return s.replace("</Relationships>", add + "</Relationships>").encode("utf-8")


def _add_comment_ct(ct: bytes) -> bytes:
    s = ct.decode("utf-8")
    if COMMENTS_CT in s:
        return ct
    add = ('<Override PartName="/word/comments.xml" ContentType="%s"/>' % COMMENTS_CT)
    return s.replace("</Types>", add + "</Types>").encode("utf-8")


def _inject_marks(xml: bytes, marks: list[dict]) -> bytes:
    """在指定段落的文字前后插入 commentRangeStart/End 与 commentReference。"""
    if not marks:
        return xml
    decls = _root_decls(xml)
    root = ET.fromstring(xml)
    body = root.find(_q("body"))
    paras = []

    def walk(node):
        for child in node:
            tag = child.tag.split("}")[-1]
            if tag == "p":
                paras.append(child)
            elif tag in ("tbl", "tr", "tc", "sdt", "sdtContent"):
                walk(child)

    walk(body)
    for m in marks:
        idx = m["paragraph"]
        if idx < 0 or idx >= len(paras):
            continue
        p = paras[idx]
        cid = str(m["id"])
        runs = p.findall(_q("r"))
        start = ET.Element(_q("commentRangeStart"))
        start.set(_q("id"), cid)
        end = ET.Element(_q("commentRangeEnd"))
        end.set(_q("id"), cid)
        ref_run = ET.SubElement(p, _q("r"))
        rpr = ET.SubElement(ref_run, _q("rPr"))
        style = ET.SubElement(rpr, _q("rStyle"))
        style.set(_q("val"), "CommentReference")
        ref = ET.SubElement(ref_run, _q("commentReference"))
        ref.set(_q("id"), cid)
        if runs:
            p.insert(list(p).index(runs[0]), start)
            p.append(end)
        else:
            p.append(start)
            p.append(end)
    out = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return _restore_decls(out, decls)


def export_comments_only(src_path: str, out_path: str, comments: list[dict],
                         author: str = "活动部·初审") -> dict:
    """把批注写进 docx，原文字一个不动。comments: [{id, text, paragraph}]。"""
    now = dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = [{"id": c["id"], "text": c["text"], "author": author,
                "initials": "初", "date": now} for c in comments]
    marks = [{"id": c["id"], "paragraph": c["paragraph"]} for c in comments]

    with zipfile.ZipFile(src_path) as zin:
        names = zin.namelist()
        data = {n: zin.read(n) for n in names}

    doc_xml = data["word/document.xml"]
    data["word/document.xml"] = _inject_marks(doc_xml, marks)
    data["word/comments.xml"] = _comment_xml(payload)

    rels_name = "word/_rels/document.xml.rels"
    if rels_name in data:
        data[rels_name] = _add_comment_rels(data[rels_name])
    ct_name = "[Content_Types].xml"
    if ct_name in data:
        data[ct_name] = _add_comment_ct(data[ct_name])

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for n in names + ["word/comments.xml"]:
            if n in data:
                zout.writestr(n, data[n])
    return {"comments": len(payload), "out": str(out_path)}
