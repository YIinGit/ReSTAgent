"""导出正文字体：选「统一为宋体」时，文档默认字体与正文 run 都要落到宋体。"""
from __future__ import annotations

import pathlib
import sys
import threading
import xml.etree.ElementTree as ET
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from app import config  # noqa: E402
from app.docx_engine.reader import Doc  # noqa: E402
from app.docx_engine.writer import export_docx  # noqa: E402
from app.review.pipeline import Reviewer  # noqa: E402
from validate_docx import validate  # noqa: E402

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def q(t):
    return "{%s}%s" % (W, t)


DEMO = ROOT / "samples" / "示例稿_待审.docx"
OUT = ROOT / "tests" / "font_reviewed.docx"


def eastasia_defaults(path) -> str | None:
    z = zipfile.ZipFile(path)
    styles = ET.fromstring(z.read("word/styles.xml"))
    dd = styles.find(q("docDefaults"))
    rpr = dd.find(q("rPrDefault")).find(q("rPr"))
    f = rpr.find(q("rFonts"))
    return f.get(q("eastAsia")) if f is not None else None


def main():
    if not DEMO.exists():
        raise SystemExit("先运行 tests/make_demo.py")
    cfg = config.load()
    cfg["debug"]["mock"] = True
    doc = Doc(DEMO)

    res = Reviewer(cfg, "second", "standard").review_file(doc, lambda m: None, threading.Event())
    items = [{"pid": f["pid"], "start": f["start"], "end": f["end"], "action": f["action"],
              "after": f["after"], "comment": f["problem"]} for f in res["findings"]]
    assert items, "示例稿应能扫出意见"

    # 1. 跟随原稿：默认字体不动
    export_docx(doc, items, OUT, font="")
    before = eastasia_defaults(OUT)
    print("跟随原稿时 docDefaults eastAsia =", before)

    # 2. 统一宋体
    doc2 = Doc(DEMO)
    export_docx(doc2, items, OUT, font="宋体")
    after = eastasia_defaults(OUT)
    print("统一宋体后 docDefaults eastAsia =", after)
    assert after == "宋体", "docDefaults 没改成宋体"

    z = zipfile.ZipFile(OUT)
    styles = ET.fromstring(z.read("word/styles.xml"))
    normal = [s for s in styles.findall(q("style")) if s.get(q("styleId")) == "Normal"][0]
    nf = normal.find(q("rPr")).find(q("rFonts"))
    print("Normal 样式 eastAsia =", nf.get(q("eastAsia")))
    assert nf.get(q("eastAsia")) == "宋体"

    docxml = ET.fromstring(z.read("word/document.xml"))
    themed = 0
    for f in docxml.iter(q("rFonts")):
        if f.get(q("eastAsiaTheme")):
            themed += 1
        assert f.get(q("eastAsia")) == "宋体", "正文 run 的 eastAsia 不是宋体"
    print("正文 rFonts 全部为宋体，残留主题引用:", themed)
    assert themed == 0

    problems = validate(str(OUT))
    assert not problems, problems
    print("结构体检通过 | 意见 %d 条" % len(items))


if __name__ == "__main__":
    main()
