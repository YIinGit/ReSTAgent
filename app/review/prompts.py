"""内容与结构审稿流水线的规则/提示词装配。"""
from __future__ import annotations

import pathlib

from .. import paths, rules_lib

RULE_DIR = paths.resource("app", "rules")
_cache: dict[str, tuple[float, str]] = {}
_parsed: dict[str, tuple[float, tuple]] = {}

STAGE_NAME = {"zero": "零审", "first": "一审", "second": "二审", "third": "三审"}
CATEGORIES = ["学术错误", "结构", "文风", "敏感", "冗余", "病句", "错别字",
              "用词", "术语", "标点", "数字与西文", "排版"]


def _rule_file(stage: str) -> pathlib.Path:
    return RULE_DIR / ("%s_stage.md" % stage)


def _mtime(stage: str) -> float:
    try:
        return _rule_file(stage).stat().st_mtime
    except OSError:
        return 0.0


def _read(stage: str) -> str:
    """读取规则文件；文件改动（mtime 变化）后自动重新读取，无需重启。"""
    stamp = _mtime(stage)
    hit = _cache.get(stage)
    if hit and hit[0] == stamp:
        return hit[1]
    f = _rule_file(stage)
    text = f.read_text(encoding="utf-8") if f.exists() else ""
    _cache[stage] = (stamp, text)
    _parsed.pop(stage, None)
    return text


def _parse(text: str) -> tuple[str, str, list[dict]]:
    """把规则 markdown 拆成（标题、前言、[章节]），章节以 `## ` 开头。"""
    title = ""
    intro: list[str] = []
    sections: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        if not title and cur is None and not sections and line.startswith("# "):
            title = line[2:].strip()
            continue
        if line.startswith("## "):
            if cur is not None:
                sections.append(cur)
            cur = {"title": line[3:].strip(), "lines": []}
            continue
        (cur["lines"] if cur is not None else intro).append(line)
    if cur is not None:
        sections.append(cur)
    out = [{"title": s["title"], "text": "\n".join(s["lines"]).strip()} for s in sections]
    return title, "\n".join(intro).strip(), out


def parse_rule_file(stage: str) -> tuple[str, str, list[dict]]:
    stamp = _mtime(stage)
    hit = _parsed.get(stage)
    if hit and hit[0] == stamp:
        return hit[1]
    parsed = _parse(_read(stage))
    _parsed[stage] = (stamp, parsed)
    return parsed


def rule_id(stage: str, section_title: str) -> str:
    return "%s|%s" % (stage, section_title)


def custom_rules(stage: str, cfg: dict | None = None) -> list[dict]:
    out = []
    for it in rules_lib.load_custom():
        if it.get("stage") != stage:
            continue
        if cfg is not None and not rules_lib.is_enabled(cfg, rules_lib.CUSTOM_PREFIX + it["id"]):
            continue
        out.append(it)
    return out


def rules(stage: str, cfg: dict | None = None) -> str:
    """按启停状态拼装规则正文；cfg 为 None 时全部启用（兼容旧调用）。"""
    title, intro, sections = parse_rule_file(stage)
    parts = []
    if title:
        parts.append("# " + title)
    if intro:
        parts.append(intro)
    for sec in sections:
        if cfg is not None and not rules_lib.is_enabled(cfg, rule_id(stage, sec["title"])):
            continue
        parts.append("## %s\n%s" % (sec["title"], sec["text"]))
    for it in custom_rules(stage, cfg):
        parts.append("## %s\n%s" % (it["title"], it["body"]))
    return "\n\n".join(p for p in parts if p)


JSON_SPEC = """{"findings":[{"para_id":"P012","quote":"原文连续片段","category":"标点","subcategory":"引号","severity":"must","action":"replace","before":"原文片段","after":"改后文本","problem":"问题说明","suggestion":"修改建议","rule_ref":"第十讲·引号","confidence":0.9,"needs_human_check":false}]"""

JSON_SPEC_FIRST = """{"findings":[{"para_id":"P012","quote":"原文连续片段","category":"结构","subcategory":"逻辑顺序","severity":"should","action":"comment_only","before":"","after":"","problem":"问题说明","suggestion":"修改建议","rule_ref":"第六讲·行文逻辑","confidence":0.8,"needs_human_check":false}],"structure_suggestions":[{"issue":"整体结构问题","advice":"调整建议","para_ids":["P004","P005"],"severity":"should"}]}"""

JSON_SPEC_ZERO = """{"verdict":{"decision":"revise","summary":"总体评估","quality":"medium","suggested_section":"化学","word_count_est":5200,"reasons":["理由1","理由2"]},"findings":[{"para_id":"P003","quote":"原文连续片段","category":"学术错误","subcategory":"史实","severity":"must","action":"comment_only","problem":"问题说明","suggestion":"修改建议","rule_ref":"第七讲·不符合客观事实","confidence":0.85,"needs_human_check":true}]}"""


def system_prompt(stage: str, level: str, cfg: dict | None = None) -> str:
    name = STAGE_NAME.get(stage, "二审")
    spec = {"zero": JSON_SPEC_ZERO, "first": JSON_SPEC_FIRST}.get(stage, JSON_SPEC)
    extra = ""
    if level == "light":
        extra = ("\n【力度：轻】只报必须修改的硬错误（severity=must），最多 12 条；"
                 "不要报风格类、优化类问题。")
    elif level == "strict":
        extra = ("\n【力度：严格】宁缺毋滥但不要漏：重点核查数据与专有名词、逻辑衔接、"
                 "同类错误的其余出现位置。")
    elif level == "max":
        extra = ("\n【力度：极致】逐句核对，可疑处也要报（severity=should）；"
                 "对同一现象在不同段落的重复出现分别定位。")
    if stage == "zero":
        extra += ("\n【文章质量】verdict.quality 只能是 high / medium / low 三者之一："
                  "high=高（选用）、medium=中（需修改并提出意见）、low=低（不选用）；"
                  "不要再输出分级（1级/2级/3级）字段。")

    role = {
        "zero": "你是《未来梦》杂志编辑部文稿组的组长，负责稿件录用把关（零审）。",
        "first": "你是《未来梦》杂志编辑部文稿组的一审编辑，负责内容、结构与文风。",
        "second": "你是《未来梦》杂志编辑部文稿组的二审编辑，负责文字与语法。",
    }.get(stage, "你是《未来梦》杂志编辑部的审稿编辑。")

    return f"""{role}

## 审稿规则
{rules(stage, cfg)}

## 输出格式
只输出一个 JSON 对象，不要任何解释文字，不要 Markdown 代码块：
{spec}

## 硬性要求
1. para_id 必须使用给定段落编号；quote 必须是该段原文中**逐字连续**的一段，不要加省略号、不要改写、不要跨段。
2. **最小改动原则（最重要）**：quote 只截取「改动本身所必需的最小片段」，能改一个字就不要抄一个词，能改一个词就不要抄整句。quote 通常在 1–12 字之间；只有问题确实横跨整句（成分残缺、语序混乱、逻辑不通）时才引用整句，并在 problem 里说明为什么要动整句。
3. replace 的 after 必须与 quote **一一对应**：after 只写「quote 那一小段改完之后的样子」，不是重写整句。例如「仔细的\"分析」应写 quote=`的\"`、after=`地\"`，绝不能 quote 整句、after 再抄一遍整句；只有引号/标点单独出错时，quote 就只写那个标点。
4. 一条只报一个问题；同一处不要重复。
5. action 只能是 replace / insert / delete / comment_only。replace 必须给 after（可直接使用的替换文本）；insert 必须给 after（要插入的文字）；delete 不需要 after；拿不准怎么改时用 comment_only。
6. severity：must=必改（错别字、错误标点、病句、事实错误、敏感）；should=建议改（用词、逻辑、冗余）；suggest=可选（阅读体验）。
7. 不要报告无法指到原文的问题；不要臆造数据、出处、引文。不确定的写 needs_human_check=true 并在 problem 中说明"存疑"。
8. 不要改作者的观点、立场与文学风格；科幻作品尊重原文，只报理解障碍与设定矛盾。
9. 输出必须是可解析的 JSON；字符串内不要出现裸换行（用 \\n），不要输出注释。{extra}"""


def context_block(doc, chunk, prev_para=None, next_para=None) -> str:
    lines = []
    if prev_para is not None and prev_para.text.strip():
        lines.append("【上一段（仅作上下文，不审）】[%s] %s" % (prev_para.pid, prev_para.text[:400]))
    lines.append("【本块正文（只审这部分）】")
    for p in chunk:
        tag = "[%s]%s" % (p.pid, ("<标题>" if p.level else ""))
        lines.append(tag + " " + p.text)
    if next_para is not None and next_para.text.strip():
        lines.append("【下一段（仅作上下文，不审）】[%s] %s" % (next_para.pid, next_para.text[:400]))
    return "\n".join(lines)


def doc_header(doc, chunk_index=None, chunk_total=None) -> str:
    outline = doc.outline()
    head = ["【稿件信息】标题：%s；全文约 %d 字，%d 段；含标题层级 %d 个。"
            % (doc.title or "（未命名）", doc.chars, len(doc.paras), len(outline))]
    if chunk_index is not None:
        head.append("当前为第 %d/%d 块。" % (chunk_index, chunk_total))
    if outline:
        head.append("【大纲】" + "；".join(
            "%s%s" % ("　" * max(0, o["level"] - 1), o["text"]) for o in outline[:40]))
    return "\n".join(head)


def chunk_user_message(doc, chunk, index, total, prev_para=None, next_para=None) -> str:
    return (doc_header(doc, index, total) + "\n" + context_block(doc, chunk, prev_para, next_para) +
            "\n请按要求输出 JSON。")


def outline_user_message(doc, sample: str, stage: str) -> str:
    return (doc_header(doc) + "\n【全文内容（如过长则为各节抽取）】\n" + sample +
            "\n请从**整体**上判断这篇文章的结构、逻辑顺序、首尾照应、文风一致性与内容定位，"
            "输出 JSON（可含 structure_suggestions）。尽量把问题定位到具体段落。")


def zero_user_message(doc, sample: str) -> str:
    return (doc_header(doc) + "\n【稿件正文（如过长则为摘要式抽取）】\n" + sample +
            "\n请按零审规则给出录用评估 JSON。")


def review_user_message(doc, existing: list[dict], sample: str) -> str:
    seen = "\n".join("- [%s] %s：%s" % (f.get("para_id"), (f.get("problem") or "")[:40],
                                       (f.get("quote") or "")[:30]) for f in existing[:120])
    return (doc_header(doc) +
            "\n【已发现的问题（不要重复报告）】\n" + (seen or "（暂无）") +
            "\n【全文（如过长则为各节抽取）】\n" + sample +
            "\n请只报告**尚未发现**的问题：跨段落的重复错误、前后不一致（术语/译名/数据/称谓）、"
            "逻辑与顺序、遗漏的硬错误。输出 JSON。")
