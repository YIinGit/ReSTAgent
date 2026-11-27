"""组装初审用的提示词。

系统提示词优先读 `docs/AI评审提示词.md` 的「一、系统提示词」段（改 md 即改行为）。
该文件是内部材料，不进公开仓库，但随发行包分发；找不到时用内置的精简版，功能不失效。

规则库读 `rules.json`，自动生成 R01—R20 速查表追加到系统提示词里，
所以增删规则只需要改 rules.json 一处。
"""
from __future__ import annotations

import json
import pathlib
import re

from .. import paths

HERE = pathlib.Path(__file__).resolve().parent
RULES_PATH = HERE / "rules.json"
PROMPT_MD_CANDIDATES = [
    "docs/AI评审提示词.md",
]

BUILTIN_SYSTEM = """你是成都七中科协活动部的策划案初审员，依据《2026活动部教案》对高一新成员提交的活动策划案做初审。
产出会由程序转写为文档批注，必须严格按 JSON 格式输出。

审查立场：
- 只判断是否符合教案要求、信息是否足以支撑执行，不评价策划人的能力、态度或创意好坏。
- 语言客观、严谨、可核查：陈述「缺什么、与哪条要求不一致、会导致什么执行后果」。
- 不必要不修改：只标注影响执行、违反硬性规范或明确可优化之处。
- 不虚构事实：原文没有的数字、姓名、场地、金额不得编造，用（待确认）占位。
- 不改动活动立意与内容取向，只修正表述、结构与缺项。

分级：
- must（必改）：信息缺失或前后矛盾，导致活动无法执行、无法验收，或违反教案硬性规范。
- suggest（建议）：不影响执行，但按教案标准可提升。
- 同一处问题只报一条，按 must 优先。

常见误判口径：
- 「吸引更多同学」属目标不可衡量（must）；若已给出人数与验收方式则不判。
- 活动流程用文字简述、时长短于一天：允许，不因「没用表格」判 must。
- 协内自用文本允许口语化；仅当用于上报学校、对外宣传时才按书面语要求。
- 已写明「待学校审批后确定」的场地不判缺失，但需有确定节点与责任人。
- 只写「加强管理」「及时处理」「听从安排」而无具体动作的，判风险预案不可执行（must）。
- 不要修改：活动名称、立意、已明确的合理数值、已到岗的责任人姓名、协内自用的口语段落。
"""

BUILTIN_FORMAT = """只输出 JSON，不要 Markdown 代码块，不要任何解释文字：
{
  "meta": {"title": "", "verdict": "通过|修改后通过|退回修改", "summary": ""},
  "findings": [
    {"id": "F01", "level": "must|suggest", "rule": "R05", "dimension": "",
     "anchor": "从原文逐字复制的片段，12—60 字，不得改写", "occurrence": 1,
     "action": "replace|insert|delete|suggest", "problem": "不超过 60 字",
     "basis": "教案依据，如：第七讲（二）2", "rewrite": "不超过 80 字，无则留空",
     "attach": ""}
  ]
}
anchor 必须与原文逐字一致且唯一；重复出现时用 occurrence 指定第几次。
缺板块类问题 anchor 留空，attach 填 end（落文末）或 title（落标题）。
"""


def find_prompt_md() -> pathlib.Path | None:
    for name in PROMPT_MD_CANDIDATES:
        p = paths.ROOT / name
        if p.exists():
            return p
        p2 = paths.resource(name)
        if p2.exists():
            return p2
    return None


def _extract_system_section(text: str) -> str:
    """从 AI评审提示词.md 里取「一、系统提示词」到「二、用户消息模板」之间的内容。"""
    start = text.find("## 一、系统提示词")
    if start < 0:
        return ""
    start = text.find("\n", start) + 1
    end = text.find("## 二、", start)
    if end < 0:
        end = len(text)
    body = text[start:end]
    # 去掉 markdown 分隔线
    body = re.sub(r"^\s*---\s*$", "", body, flags=re.M)
    return body.strip()


def load_rules() -> dict:
    try:
        return json.loads(RULES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"rules": [], "limits": {"must": 12, "suggest": 8}, "core_sections": []}


def rules_table(rules: dict) -> str:
    """把规则库转成提示词用的速查表。"""
    lines = ["", "【规则速查表】"]
    for r in rules.get("rules") or []:
        lines.append("- %s %s（%s）：%s" % (r.get("id"), r.get("name"),
                                            r.get("dimension", ""), r.get("check", "")))
    return "\n".join(lines)


def build_system_prompt(kind: str = "", submission: str = "") -> str:
    """系统提示词 = 提示词文件里的正文（或内置版）+ 规则表 + 输出格式 + 本次约束。"""
    md = find_prompt_md()
    body = ""
    if md is not None:
        try:
            body = _extract_system_section(md.read_text(encoding="utf-8"))
        except Exception:
            body = ""
    rules = load_rules()
    parts = [body or BUILTIN_SYSTEM, "", BUILTIN_FORMAT, rules_table(rules)]

    limits = rules.get("limits") or {}
    parts.append("")
    parts.append("【本次约束】")
    if kind:
        parts.append("- 活动类型：%s" % kind)
    if submission:
        parts.append("- 提交对象：%s（%s）" % (
            submission,
            "按书面语要求判 R15" if submission in ("上报学校", "对外宣传") else "协内自用，不因口语判 R15"))
    parts.append("- must 不超过 %d 条，suggest 不超过 %d 条，按文档出现顺序排列。"
                 % (limits.get("must", 12), limits.get("suggest", 8)))
    parts.append("- 只针对本段文本给意见；若某问题在更早段落已出现，不要重复报。")
    return "\n".join(parts).strip()


def user_message(paragraphs: list[dict], title: str = "", chunk_no: int = 1,
                 chunk_total: int = 1, whole_outline: str = "") -> str:
    """拼用户消息：段落带 [Pxxx] 编号，便于模型定位（编号不得写进 anchor）。"""
    lines = []
    lines.append("【任务】对下面的高一新成员活动策划案做初审，依据《2026活动部教案》输出 JSON。")
    if whole_outline:
        lines.append("")
        lines.append("【全文结构参考】（只用于判断缺板块，不要据此报重复问题）")
        lines.append(whole_outline)
    lines.append("")
    if chunk_total > 1:
        lines.append("【本次只审第 %d/%d 段】前面已审过的部分不在此列。" % (chunk_no, chunk_total))
    lines.append("【策划案正文】")
    for p in paragraphs:
        lines.append("[P%03d] %s" % (p["no"], p["text"]))
    lines.append("")
    lines.append("anchor 必须是正文里逐字存在的片段，且不要带上 [Pxxx] 编号。")
    return "\n".join(lines)
