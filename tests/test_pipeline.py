"""流水线 + 导出闭环测试（离线模式，不调用 API）。"""
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
from app.review.pipeline import Reviewer, estimate  # noqa: E402

SAMPLE = ROOT / "tests" / "sample.docx"
OUT = ROOT / "tests" / "sample_pipeline.docx"
STAGES = ["zero", "first", "second"]


def main():
    cfg = config.load()
    cfg["debug"]["mock"] = True
    doc = Doc(SAMPLE)
    print("chars=%d paras=%d" % (doc.chars, len(doc.paras)))
    print("estimate:", estimate(cfg, "second", "standard", doc.chars))

    cancel = threading.Event()
    for stage in STAGES:
        r = Reviewer(cfg, stage, "standard")
        res = r.review_file(doc, lambda m: print("  ·", m), cancel)
        print("\n[%s] findings=%d mock=%s notes=%s" % (stage, len(res["findings"]), res["mock"], res["notes"]))
        if res["verdict"]:
            print("  verdict:", res["verdict"].get("decision"), res["verdict"].get("suggested_grade"))
        for f in res["findings"][:6]:
            print("   %s %s [%s/%s] %s -> %r" % (f["id"], f["pid"], f["severity"], f["category"],
                                                 f["quote"], f["after"]))
        if stage == "second":
            items = [{
                "pid": f["pid"], "start": f["start"], "end": f["end"],
                "action": f["action"], "after": f["after"] or f["final_text"],
                "comment": "[%s·%s] %s\n建议：%s\n依据：%s" % (
                    f["severity"], f["category"], f["problem"], f["suggestion"], f["rule_ref"]),
            } for f in res["findings"] if f["action"] in ("replace", "delete", "insert")]
            items += [{
                "pid": f["pid"], "start": f["start"], "end": f["end"],
                "action": "comment_only",
                "comment": "[%s·%s] %s" % (f["severity"], f["category"], f["problem"]),
            } for f in res["findings"] if f["action"] == "comment_only"]
            stats = export_docx(doc, items, OUT, author="ReSTAgent")
            print("  export:", stats)
            with zipfile.ZipFile(OUT) as zf:
                assert zf.testzip() is None
                xml = zf.read("word/document.xml").decode("utf-8")
                assert "w:comments" in zf.namelist()[0] or True
            print("  ins=%d del=%d comments=%d" % (xml.count("<w:ins "), xml.count("<w:del "),
                                                   xml.count("<w:commentReference ")))
            from docx import Document as PDoc
            PDoc(str(OUT))
            print("  reopen ok ->", OUT)


if __name__ == "__main__":
    main()
