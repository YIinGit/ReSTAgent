# -*- coding: utf-8 -*-
"""活动策划案初审：离线单元测试（不调用模型）。

覆盖：anchor 三级匹配、JSON 容错、等级不被覆盖、结论推导、批注写入。
运行：python tests/test_campaign.py
"""
from __future__ import annotations

import json
import pathlib
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.campaign import annotate, report, reviewer  # noqa: E402


def main() -> int:
    print("=== 1. anchor 三级匹配 ===")
    paras = [
        {"index": 0, "no": 1, "text": "“AI 与我的一天”科普工作坊策划案"},
        {"index": 1, "no": 2, "text": "本活动面向高一全体学生，吸引更多同学关注人工智能。"},
        {"index": 2, "no": 3, "text": "活动将在近期某个周六下午举行，大概两个半小时，具体时间待定。"},
        {"index": 3, "no": 4, "text": "本活动面向高一全体学生，吸引更多同学关注人工智能。"},
    ]
    # 一级：逐字
    loc = reviewer.locate("吸引更多同学关注人工智能", paras)
    assert loc and loc["level"] == "exact", loc
    print("  逐字匹配 OK →", loc["paragraph"])
    # occurrence 消歧
    loc2 = reviewer.locate("本活动面向高一全体学生，吸引更多同学关注人工智能", paras, occurrence=2)
    assert loc2 and loc2["paragraph"] == 3, loc2
    print("  occurrence 消歧 OK → 第", loc2["paragraph"], "段")
    # 二级：空格差异（中文标点不动）
    loc3 = reviewer.locate("活动将在近期某个周六下午举行，大概两个半小时，具体时间待定。", paras)
    assert loc3 and loc3["level"] == "exact", loc3
    loc3b = reviewer.locate("活动将在近期某个周六下午举行，大概两个半小时，具体时间待定。".replace("，", "， "), paras)
    assert loc3b, loc3b
    print("  空格差异可容错 OK（级别：%s）" % loc3b["level"])
    # 三级：近似
    loc4 = reviewer.locate("活动将在近期某个周六下午举行，大概两个半小时左右，时间待定", paras)
    assert loc4 and loc4["level"] == "fuzzy", loc4
    print("  近似匹配 OK，相似度", loc4.get("ratio"))
    # 完全不像 → 定位失败
    assert reviewer.locate("这段原文根本不存在于稿件里的任何一句话中", paras) is None
    print("  不匹配正确返回 None")

    print("\n=== 2. JSON 容错 ===")
    assert reviewer.parse_json('{"a":1}')["a"] == 1
    assert reviewer.parse_json('```json\n{"a":2}\n```')["a"] == 2
    assert reviewer.parse_json('好的，结果如下：{"a":3} 以上。')["a"] == 3
    print("  裸 JSON / 代码块 / 带解释文字 均可解析")

    print("\n=== 3. 意见校验（等级、编造、缺锚点）===")
    f, note = reviewer.check_finding({
        "level": "must", "rule": "R01", "anchor": "吸引更多同学关注人工智能",
        "problem": "不可衡量", "rewrite": "吸引 200 名同学"}, paras)
    assert f["level"] == "must", f["level"]          # 等级不能被匹配方式覆盖
    assert f["located"] and f["match"] == "exact"
    assert f["rewrite"] == "" and "降级" in note      # 编造的数字要被拦下
    print("  等级保留 OK；编造数字已降级 →", note)

    f2, _ = reviewer.check_finding({
        "level": "must", "rule": "R09", "anchor": "", "attach": "end",
        "problem": "缺危机预案"}, paras)
    assert f2["level"] == "must" and f2["attach"] == "end", f2
    print("  缺板块类（无 anchor）保持 must OK")

    f3, note3 = reviewer.check_finding({
        "level": "must", "rule": "R05", "anchor": "这句话原文里完全没有出现过的一整句",
        "problem": "无时间节点"}, paras)
    assert f3["level"] == "suggest" and "锚点未定位" in note3
    print("  锚点定位失败 → 降为建议并挂文末 OK")

    print("\n=== 4. 合并、上限与结论推导 ===")
    limits = {"must": 2, "suggest": 2}
    merged = reviewer.merge_findings([[
        {"level": "must", "rule": "R01", "anchor": "a", "paragraph": 1},
        {"level": "must", "rule": "R02", "anchor": "b", "paragraph": 2},
        {"level": "must", "rule": "R03", "anchor": "c", "paragraph": 3},
        {"level": "suggest", "rule": "R04", "anchor": "d", "paragraph": 4},
    ]], limits)
    assert len([f for f in merged if f["level"] == "must"]) == 2, merged
    print("  must 上限 2 生效 OK；id 连续：", [f["id"] for f in merged])

    rules = {"core_sections": ["综述", "流程", "人员", "目标", "危机预案"]}
    assert reviewer.decide_verdict([], [], rules) == "通过"
    print("  无必改 → 通过 OK")
    must_f = [{"level": "must"}]
    assert reviewer.decide_verdict(must_f, ["综述"], rules) == "修改后通过"
    print("  仅缺综述 → 修改后通过 OK")
    assert reviewer.decide_verdict(must_f, ["流程", "危机预案"], rules) == "退回修改"
    print("  缺流程+危机预案 → 退回修改 OK")
    assert reviewer.decide_verdict(must_f, ["危机预案"], rules) == "退回修改"
    print("  缺危机预案（关键板块）→ 退回修改 OK")

    print("\n=== 5. 板块缺失识别 ===")
    sample = ROOT / "tests" / "样例3_退回.docx"
    if sample.exists():
        ps = reviewer.read_paragraphs(str(sample))
        miss = reviewer.missing_sections(ps)
        assert "流程" in miss and "危机预案" in miss, miss
        print("  样例3 缺失板块：", miss)
    sample1 = ROOT / "tests" / "样例1_合规.docx"
    if sample1.exists():
        ps = reviewer.read_paragraphs(str(sample1))
        assert reviewer.missing_sections(ps) == [], reviewer.missing_sections(ps)
        print("  样例1 板块齐全（含表格中的流程）OK")

    print("\n=== 6. 批注写入 docx ===")
    if sample1.exists():
        out = ROOT / "_build" / "_camp_test_annotated.docx"
        out.parent.mkdir(exist_ok=True)
        res = {"findings": [
            {"level": "must", "rule": "R01", "dimension": "目标明确", "problem": "不可衡量",
             "rewrite": "改为不少于 40 人", "basis": "第五讲", "paragraph": 2},
            {"level": "suggest", "rule": "R09", "problem": "缺危机预案", "paragraph": -1},
        ]}
        cm = report.comments_for_word(res)
        annotate.export_comments_only(str(sample1), str(out), cm)
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
            doc = z.read("word/document.xml").decode("utf-8")
            com = z.read("word/comments.xml").decode("utf-8")
        assert "word/comments.xml" in names
        assert com.count("<w:comment ") == 2
        assert doc.count("commentRangeStart") == 1     # 只有一条能定位到段落
        assert "批注" not in doc                        # 原文不能被改动
        print("  comments.xml 新部件 OK；批注 2 条，可见锚点 1 条")
        out.unlink()

    print("\nALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
