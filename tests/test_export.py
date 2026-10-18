"""批注/修订注入回归测试：{python} tests/test_export.py"""
from __future__ import annotations

import pathlib
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.docx_engine.reader import Doc  # noqa: E402
from app.docx_engine.writer import export_docx  # noqa: E402

SAMPLE = ROOT / "tests" / "sample.docx"
OUT = ROOT / "tests" / "sample_reviewed.docx"


def find(doc, needle):
    for p in doc.paras:
        if needle in p.text:
            return p
    raise AssertionError("找不到段落: %r" % needle)


def main():
    if not SAMPLE.exists():
        raise SystemExit("先运行 tests/make_sample.py 生成示例稿")

    doc = Doc(SAMPLE)
    print("title:", doc.title, "| chars:", doc.chars, "| paras:", len(doc.paras))
    for p in doc.paras[:12]:
        print(" ", p.pid, ("H%d" % p.level) if p.level else "  ", repr(p.text[:28]))

    items = []

    # 1) 直引号 -> 中文引号（替换）
    p = next(x for x in doc.paras if '他说：' in x.text)
    r = doc.resolve(p.pid, '"这就是自由基"')
    assert r, "定位失败 #1"
    items.append({
        "pid": p.pid, "start": r[0], "end": r[1], "action": "replace",
        "after": "“这就是自由基”",
        "comment": "【必改·标点·引号】\n问题：使用了直引号（傻瓜引号），排版禁止使用。\n建议：改为中文弯引号“”。\n依据：第十讲·引号；第十一讲·第六节",
    })

    # 2) 的/地/得（替换）
    p = find(doc, "高兴的说到")
    r = doc.resolve(p.pid, "高兴的说到")
    assert r, "定位失败 #2"
    items.append({
        "pid": p.pid, "start": r[0], "end": r[1], "action": "replace", "after": "高兴地说到",
        "comment": "【必改·错别字·的/地/得】\n问题：“的”后接动词，应为“地”。\n依据：第八讲·第二节",
    })

    # 3) 冗余“那么”（删除）
    p = next(x for x in doc.paras if x.text.startswith("那么，根据"))
    r = doc.resolve(p.pid, "那么，")
    assert r, "定位失败 #3"
    items.append({
        "pid": p.pid, "start": r[0], "end": r[1], "action": "delete",
        "comment": "【建议·文风】\n问题：“那么”在一段内连续出现，冗余。\n依据：第六讲·第三节",
    })

    # 4) 缺逗号（插入）+ 仅批注
    p = find(doc, "首先是加热")
    r = doc.resolve(p.pid, "首先是加热")
    assert r, "定位失败 #4"
    items.append({
        "pid": p.pid, "start": r[1], "end": r[1], "action": "insert", "after": "，",
        "comment": "【建议·标点】\n问题：两个并列分句之间缺逗号。",
    })

    # 5) 纯批注（不改文）
    p = next(x for x in doc.paras if "伯利兹" in x.text)
    r = doc.resolve(p.pid, "直径 318m 洞深 125m")
    assert r, "定位失败 #5"
    items.append({
        "pid": p.pid, "start": r[0], "end": r[1], "action": "comment_only",
        "comment": "【必改·学术错误】\n问题：数据存疑，不同来源不一致。\n建议：核实原始英尺数据后换算。\n依据：第七讲·第三节",
    })

    stats = export_docx(doc, items, OUT, author="ReSTAgent")
    print("export:", stats)

    with zipfile.ZipFile(OUT) as zf:
        assert zf.testzip() is None
        names = zf.namelist()
        assert "word/comments.xml" in names, "缺少 comments.xml"
        docxml = zf.read("word/document.xml").decode("utf-8")
        comments = zf.read("word/comments.xml").decode("utf-8")
        ct = zf.read("[Content_Types].xml").decode("utf-8")
        rels = zf.read("word/_rels/document.xml.rels").decode("utf-8")
    print("comments part:", len(comments), "bytes; doc.xml:", len(docxml), "bytes")
    assert "commentRangeStart" in docxml
    assert "<w:ins " in docxml and "<w:del " in docxml
    assert "comments+xml" in ct
    assert "/comments" in rels
    print("comments count in part:", comments.count("<w:comment "))

    # python-docx 复读（结构合法性交叉验证）
    from docx import Document as PDoc

    d = PDoc(str(OUT))
    print("reopen ok, paragraphs:", len(d.paragraphs))
    # 修订后的可见文本（python-docx 会包含 delText? 直接看 XML 片段）
    i = docxml.find("<w:ins ")
    print("ins snippet:", docxml[i:i + 260].replace("\n", " "))

    print("ALL OK ->", OUT)


if __name__ == "__main__":
    main()
