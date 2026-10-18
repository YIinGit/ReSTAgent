"""示例稿（samples/）回归：本地规则 → 全量导出 → 结构校验。"""
from __future__ import annotations

import pathlib
import sys
import threading
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.docx_engine.reader import Doc  # noqa: E402
from app.docx_engine.writer import export_docx  # noqa: E402
from app.review.pipeline import Reviewer  # noqa: E402

DEMO = ROOT / "samples" / "示例稿_待审.docx"
OUT = ROOT / "tests" / "demo_reviewed.docx"


def main():
    if not DEMO.exists():
        raise SystemExit("先运行 tests/make_demo.py")
    cfg = config.load()
    cfg["debug"]["mock"] = True
    doc = Doc(DEMO)
    print("demo:", doc.title, "|", doc.chars, "字 |", len(doc.paras), "段 |",
          "标题层级", len(doc.outline()))

    res = Reviewer(cfg, "second", "standard").review_file(doc, lambda m: None, threading.Event())
    print("findings:", len(res["findings"]))
    cats = {}
    for f in res["findings"]:
        cats[f["category"]] = cats.get(f["category"], 0) + 1
    print("by category:", cats)
    assert len(res["findings"]) >= 8, "示例稿应能扫出多条问题"

    items = []
    for f in res["findings"]:
        items.append({
            "pid": f["pid"], "start": f["start"], "end": f["end"],
            "action": f["action"], "after": f["after"],
            "comment": "[%s·%s] %s\n建议：%s\n依据：%s" % (
                f["severity"], f["category"], f["problem"], f["suggestion"], f["rule_ref"]),
        })
    stats = export_docx(doc, items, OUT, author="ReSTAgent")
    print("export:", stats)
    assert stats["stats"]["fallback"] == 0, "不应出现定位降级"
    with zipfile.ZipFile(OUT) as zf:
        assert zf.testzip() is None
        xml = zf.read("word/document.xml").decode("utf-8")
        com = zf.read("word/comments.xml").decode("utf-8")
    n_ref = xml.count("<w:commentReference ")
    print("refs=%d ins=%d del=%d comments=%d" % (n_ref, xml.count("<w:ins "),
                                                 xml.count("<w:del "), com.count("<w:comment ")))
    assert n_ref == com.count("<w:comment ")
    from docx import Document
    Document(str(OUT))
    print("reopen ok ->", OUT)

    # 结构体检：命名空间声明、批注区间、修订 id、core.xml 重复元素、内容类型、关系
    sys.path.insert(0, str(ROOT / "tests"))
    from validate_docx import validate
    problems = validate(str(OUT))
    if problems:
        for p in problems:
            print("     结构问题:", p)
        raise SystemExit("导出的 docx 结构不合法，Word 会报「无法读取的内容」")
    print("结构体检通过")


if __name__ == "__main__":
    main()
