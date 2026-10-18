"""二次审读：在已经导出过的文件上再导出一次，包结构不能被破坏。

用户很自然会「审一遍 → 导出 → 隔天再打开这份导出的文件再审一遍」，
这条路径会走「已有批注部件」的分支，最容易出现批注 id 冲突、rels/内容类型重复等问题。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.docx_engine.reader import Doc  # noqa: E402
from app.docx_engine.writer import export_docx  # noqa: E402
from validate_docx import validate  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIRST = os.path.join(HERE, "demo_reviewed.docx")
SECOND = os.path.join(HERE, "demo_reexport.docx")


def main() -> int:
    if not os.path.exists(FIRST):
        print("先运行 tests/test_demo_export.py 生成基础导出文件")
        return 1

    doc = Doc(FIRST)
    paras = [p for p in doc.paras if len(p.text) > 20]
    if not paras:
        print("样例文件里没有足够长的段落")
        return 1

    items = [
        {"pid": paras[0].pid, "start": 0, "end": 2, "action": "comment_only",
         "comment": "【必改·文风】二次审读的批注"},
        {"pid": paras[1].pid, "start": 1, "end": 3, "action": "replace", "after": "改写",
         "comment": "【必改·用词】二次审读的修订"},
    ]
    res = export_docx(doc, items, SECOND, author="ReSTAgent")
    print("二次导出:", res)

    problems = validate(SECOND)
    if problems:
        print("FAIL", SECOND)
        for p in problems:
            print("     ", p)
        return 1

    doc2 = Doc(SECOND)
    text = "".join(p.text for p in doc2.paras)
    assert "二次审读的批注" not in text, "批注正文不应该进入正文"
    print("OK  ", SECOND, "| 段落", len(doc2.paras))
    return 0


if __name__ == "__main__":
    sys.exit(main())
