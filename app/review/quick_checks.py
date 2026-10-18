"""本地规则预检：不调用模型也能出高置信度问题（也用于离线演示）。"""
from __future__ import annotations

import re


def _cjk(ch: str) -> bool:
    return bool(ch) and ("\u4e00" <= ch <= "\u9fff")


CJK = "\u4e00-\u9fff"

CHECKS = []


def check(name, category, sub, severity, rule_ref, action="replace"):
    def deco(fn):
        CHECKS.append({"name": name, "category": category, "sub": sub,
                       "severity": severity, "rule_ref": rule_ref, "action": action, "fn": fn})
        return fn
    return deco


@check("直引号", "标点", "引号", "must", "第十讲·引号；第十一讲·第六节")
def _(text):
    for m in re.finditer(r'"([^"\n]{1,60})"', text):
        yield m.start(), m.end(), "使用了直引号（傻瓜引号），排版中禁止使用。", "改为中文弯引号“”。", "“%s”" % m.group(1)


@check("直单引号", "标点", "引号", "must", "第十讲·引号")
def _(text):
    for m in re.finditer(r"'([^'\n]{1,40})'", text):
        if not any(_cjk(c) for c in m.group(1)):
            continue
        yield m.start(), m.end(), "中文内容使用了直单引号。", "改为中文单引号‘’。", "‘%s’" % m.group(1)


HALF_MAP = {",": "，", ";": "；", ":": "：", "?": "？", "!": "！"}


@check("中文中的半角标点", "标点", "全半角", "should", "第十讲·全半角符号")
def _(text):
    for i, ch in enumerate(text):
        if ch in HALF_MAP and i > 0 and _cjk(text[i - 1]):
            yield i, i + 1, "中文语境中使用了半角标点「%s」。" % ch, "改为全角「%s」。" % HALF_MAP[ch], HALF_MAP[ch]


@check("重复点号", "标点", "点号", "must", "第十讲·句号/逗号")
def _(text):
    for m in re.finditer(r"([，。；：、])\1+", text):
        yield m.start(), m.end(), "点号重复使用。", "删去多余的「%s」。" % m.group(1), m.group(1)


@check("省略号写法", "标点", "省略号", "must", "第十讲·省略号")
def _(text):
    for m in re.finditer(r"。{3,}|\.{3,}", text):
        yield m.start(), m.end(), "省略号写法不规范。", "应写作「……」（六个点，占两字）。", "……"


@check("破折号写法", "标点", "破折号", "must", "第十讲·破折号")
def _(text):
    for m in re.finditer(r"(?<!—)—(?!—)|--+", text):
        yield m.start(), m.end(), "破折号应为两个连用的破折号（占两字）。", "改为「——」。", "——"


@check("顿号与等", "标点", "顿号", "should", "第十讲·标点符号若干用法", "delete")
def _(text):
    for m in re.finditer(r"、等(?![等])", text):
        yield m.start(), m.start() + 1, "并列成分用顿号时，“等/等等”前不加顿号。", "删去顿号。", ""


@check("地/得误用", "错别字", "的/地/得", "must", "第八讲·第二节")
def _(text):
    pat = re.compile(
        r"(高兴|认真|仔细|快速|缓慢|大声|小声|轻轻|悄悄|默默|努力|积极|拼命|不停|慢慢|渐渐|匆匆|急忙|"
        r"愤怒|激动|兴奋|自信|优雅|安静|平静|失望|惊讶|疑惑|坚定|耐心|温柔|严厉)的(?=[说道看听走跑笑哭叫问答想做写读喊望点摇])"
    )
    for m in pat.finditer(text):
        yield m.start() + len(m.group(1)), m.start() + len(m.group(1)) + 1, "状语修饰动词应用「地」。", "“的”改为“地”。", "地"
    pat2 = re.compile(r"(?<=[\u4e00-\u9fff])的(?=(很|非常|十分|极了|要命|不得了))")
    for m in pat2.finditer(text):
        yield m.start(), m.end(), "动词/形容词后的补语应用「得」。", "“的”改为“得”。", "得"


@check("叠字重复", "错别字", "重复", "must", "第八讲·第三节")
def _(text):
    for m in re.finditer(r"(的的|了了|是是|在在|和和|地地|得得)", text):
        yield m.start(), m.end(), "相邻重复字，疑为误录。", "删去重复字。", m.group(1)[0]


@check("全角数字字母", "数字与西文", "全半角", "should", "第十讲·全半角符号")
def _(text):
    for m in re.finditer(r"[０-９Ａ-Ｚａ-ｚ]+", text):
        yield m.start(), m.end(), "数字/字母使用了全角字符。", "改为半角。", m.group(0).translate(
            str.maketrans("０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
                          "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"))


@check("标点前空格", "标点", "空格", "should", "第十讲·标点符号的位置", "delete")
def _(text):
    for m in re.finditer(r"[ \t]+([，。；：、！？])", text):
        yield m.start(), m.start() + 1, "标点前多了空格。", "删去空格。", ""


@check("other-usage", "用词", "规范", "suggest", "第八讲·第四节", "replace")
def _(text):
    for m in re.finditer(r"其它", text):
        yield m.start(), m.end(), "规范写法为「其他」。", "改为「其他」。", "其他"


# 只出批注（不改文）的启发式检查
COMMENT_CHECKS = []


def comment_check(name, category, sub, severity, rule_ref):
    def deco(fn):
        COMMENT_CHECKS.append({"name": name, "category": category, "sub": sub,
                               "severity": severity, "rule_ref": rule_ref, "fn": fn})
        return fn
    return deco


@comment_check("口头禅高频", "文风", "冗余", "suggest", "第六讲·第三节")
def _(text):
    for word in ("那么", "然后", "其实", "但是", "所以", "而且"):
        if text.count(word) >= 4:
            i = text.find(word)
            yield i, i + len(word), "「%s」在本段出现 %d 次，行文显冗。" % (word, text.count(word)), "删减重复的连接词。"
            break


@comment_check("长段落", "结构", "分节", "suggest", "第五讲·第三节")
def _(text):
    if len(text) >= 520:
        yield 0, min(30, len(text)), "本段约 %d 字，段落过长，读者易疲劳。" % len(text), "考虑拆分段落或分节。"


@comment_check("段末无标点", "标点", "句末", "should", "第十讲·句号")
def _(text):
    if len(text) >= 40 and _cjk(text[-1]):
        yield len(text) - 1, len(text), "段末缺少句末标点。", "补句号。"


def run(doc, max_per_para: int = 6) -> list[dict]:
    """返回未定位（仅文本区间）的问题列表。"""
    out = []
    for para in doc.paras:
        text = para.text
        if not text.strip():
            continue
        items = []
        for spec in CHECKS:
            for start, end, problem, suggestion, after in spec["fn"](text):
                if end <= start:
                    continue
                items.append({
                    "pid": para.pid,
                    "start": start,
                    "end": end,
                    "quote": text[start:end],
                    "before": text[start:end],
                    "after": after,
                    "action": spec["action"] if after else "delete",
                    "category": spec["category"],
                    "subcategory": spec["sub"],
                    "severity": spec["severity"],
                    "problem": problem,
                    "suggestion": suggestion,
                    "rule_ref": spec["rule_ref"],
                    "confidence": 0.9,
                    "needs_human_check": False,
                    "_source": "local",
                })
        for spec in COMMENT_CHECKS:
            for start, end, problem, suggestion in spec["fn"](text):
                items.append({
                    "pid": para.pid,
                    "start": start,
                    "end": max(end, start + 1),
                    "quote": text[start:max(end, start + 1)],
                    "before": text[start:end],
                    "after": "",
                    "action": "comment_only",
                    "category": spec["category"],
                    "subcategory": spec["sub"],
                    "severity": spec["severity"],
                    "problem": problem,
                    "suggestion": suggestion,
                    "rule_ref": spec["rule_ref"],
                    "confidence": 0.6,
                    "needs_human_check": False,
                    "_source": "local",
                })
        # 段内按位置排序，并限制数量
        items.sort(key=lambda x: x["start"])
        out.extend(items[:max_per_para])
    return out
