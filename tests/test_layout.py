"""三审子系统测试：IDML / PDF 解析 + 规则引擎 + 报告。

用法：D:\\Python\\python.exe tests\\test_layout.py
样张：samples\\字体.idml + samples\\字体.pdf（正式稿）
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

IDML = ROOT / "samples" / "字体.idml"
PDF = ROOT / "samples" / "字体.pdf"


def main() -> int:
    from app.layout import idml, pdf, report, rules

    print("=== 1. IDML 解析 ===")
    doc = idml.parse(str(IDML))
    s = doc.summary()
    print(json.dumps(s, ensure_ascii=False))
    assert s["pages"] >= 10, "页数不足：%s" % s["pages"]
    w, h = s["page_size_mm"]
    assert abs(w - 185) < 0.5 and abs(h - 260) < 0.5, "页面尺寸不对：%s" % s["page_size_mm"]
    g = s["grid"]
    assert g["enabled"] is True, "未识别到版面网格"
    assert abs(g["point_size"] - 9) < 0.01, g
    assert abs(g["char_width"] - 9) < 0.01, g
    assert abs(g["line_height"] - 16.5) < 0.01, g
    assert s["paragraphs"] > 0, "没有解析出段落"
    assert s["frames"] > 0, "没有解析出文本框"

    print("\n=== 2. PDF 解析 ===")
    pdoc = pdf.parse(str(PDF), max_pages=4)
    ps = pdoc.summary()
    print(json.dumps(ps, ensure_ascii=False))
    assert ps["pages"] == 4
    assert ps["chars"] > 100, "PDF 文字层过少：%s" % ps["chars"]
    assert any(sp.font for pg in pdoc.pages for sp in pg.spans), "没有取到字体名"
    assert any(sp.size > 0 for pg in pdoc.pages for sp in pg.spans), "没有取到字号"

    print("\n=== 3. 规则引擎 ===")
    rep = rules.check(doc=doc, pdf=pdoc)
    c = rep.counts()
    print("问题统计:", c)
    print("关键统计:", {k: v for k, v in rep.stats.items() if not isinstance(v, dict)})
    assert c["total"] > 0, "规则没跑出任何结果"
    # 必改项里应包含网格/页面类结论（本样张页面尺寸与网格都合规，允许 0 条必改）
    for f in rep.findings[:8]:
        print("  [%s] p%s %s" % (f.level, f.page, f.message[:60]))

    print("\n=== 4. 报告 ===")
    md = report.to_markdown(rep)
    assert "# 三审报告" in md
    print("Markdown 长度:", len(md), "行数:", md.count("\n"))
    ui = report.to_ui(rep)
    assert "counts" in ui and "by_page" in ui
    print("by_page 页数:", len(ui["by_page"]))


    print("\n=== 5. 角色判定（几何 + 视觉映射）===")
    from app.layout import roles as rolesmod
    from app.layout import rules as rulesmod
    mapping = rolesmod.classify(doc)
    from collections import Counter
    dist = Counter(mapping.values())
    print("段落角色分布:", dict(dist))
    assert dist.get("body", 0) > 0, "没有识别出正文"
    assert dist.get("caption", 0) > 0, "没有识别出图注/小框"
    # 视觉角色映射：给一个假页，验证 bbox 命中逻辑
    from app.layout.model import PdfSpan
    fake = {1: {"blocks": [{"role": "author", "bbox": [0.5, 0.1, 0.2, 0.05], "text": "撰文/某某"}]},
            "_meta": {"vision": True}}
    ok = rulesmod._role_from_vision(
        PdfSpan(text="撰文/某某", x0=110, y0=35, x1=150, y1=45, page_index=0),
        1, fake, 200.0, 300.0)
    assert ok == "author", "视觉映射失败：%s" % ok
    print("视觉 bbox 映射 OK →", ok)


    print("\n=== 6. 《排版要求》规则库动态调整 ===")
    from app.layout import standard as stdmod
    full_pdf = pdf.parse(str(PDF))          # 全量 PDF（前面为省时间只解析了 4 页）
    base = rulesmod.check(doc=doc, pdf=full_pdf).counts()
    assert base["minor"] >= 1, "样例稿应有至少 1 条建议改：%s" % base
    try:
        stdmod.save({"rules": {"type.mixed_font": False}})
        off = rulesmod.check(doc=doc, pdf=full_pdf).counts()
        assert off["minor"] == base["minor"] - 1, \
            "关闭字体混用规则后应正好少 1 条：%s → %s" % (base, off)
        stdmod.save({"grid": {"point_size": 10.0}})
        rep2 = rulesmod.check(doc=doc, pdf=full_pdf)
        assert any(f.rule == "grid.point_size" and f.level == "major" for f in rep2.findings), \
            "改网格字号后应报网格字号不符"
    finally:
        stdmod.reset()
    again = rulesmod.check(doc=doc, pdf=full_pdf).counts()
    assert again == base, "恢复默认后结果应一致：%s vs %s" % (again, base)
    print("规则库动态调整 OK：", base, "→ 关闭/改参数 → 恢复", again)

    print("\nALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
