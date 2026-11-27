"""初审核心：读 docx → 分块送模型 → 校验与合并 JSON 意见。

严格性要点：
1. **anchor 三级匹配**：逐字 → 去空白/全半角归一 → difflib 相似度 ≥0.9。
   都不中就把意见降级成「文末批注」，绝不错位到别的句子。
2. **结论由代码算**，不交给模型：must=0 → 通过；缺 2 个及以上核心板块或流程/风险整体缺失 → 退回修改；
   其余 → 修改后通过。
3. **不编造**：rewrite 里出现原文没有的数字/引号内容就丢弃 rewrite，只留建议。
4. **数量上限**：must ≤12、suggest ≤8，按文档顺序截断。
"""
from __future__ import annotations

import difflib
import json
import re

from . import annotate as annotate_mod
from . import prompt as prompt_mod

SPLIT_LIMIT = 6000          # 单块字符上限
MIN_ANCHOR = 8              # anchor 太短容易误匹配


# ------------------------------------------------------------------ docx 读取
def read_paragraphs(path: str) -> list[dict]:
    import zipfile
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    ps = annotate_mod.paragraph_texts(xml)
    for i, p in enumerate(ps):
        p["index"] = i          # index 与 docx 里的段落顺序一致，批注按它定位
        p["no"] = i + 1
    return ps


def full_text(paragraphs: list[dict]) -> str:
    return "\n".join(p["text"] for p in paragraphs)


# ------------------------------------------------------------------ anchor 匹配
_WS = re.compile(r"\s+")
_PUNCT_MAP = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'",
                            "—": "-", "－": "-", "　": " "})


def _norm(s: str) -> str:
    s = (s or "").translate(_PUNCT_MAP)
    s = _WS.sub("", s)
    return s


def locate(anchor: str, paragraphs: list[dict], occurrence: int = 1) -> dict | None:
    """把 anchor 定位到具体段落。返回 {paragraph, start, end} 或 None（定位失败）。"""
    anchor = (anchor or "").strip()
    if len(anchor) < MIN_ANCHOR:
        return None
    # 一级：逐字
    hits = [(p["index"], p["text"].find(anchor)) for p in paragraphs
            if anchor in p["text"]]
    if hits:
        hits.sort()
        idx = hits[max(0, min(len(hits) - 1, occurrence - 1))][0]
        pos = next(p["text"].find(anchor) for p in paragraphs if p["index"] == idx)
        return {"paragraph": idx, "start": pos, "end": pos + len(anchor), "level": "exact"}

    # 二级：归一化（去空白、统一引号破折号）
    na = _norm(anchor)
    if len(na) < MIN_ANCHOR:
        return None
    for p in paragraphs:
        np_ = _norm(p["text"])
        if na in np_:
            return {"paragraph": p["index"], "start": 0, "end": len(p["text"]), "level": "normalized"}

    # 三级：相似度（同一段落内找最像的一句）
    best, best_ratio = None, 0.0
    for p in paragraphs:
        t = p["text"].strip()
        if not t:
            continue
        ratio = difflib.SequenceMatcher(None, na, _norm(t)).ratio()
        if ratio > best_ratio:
            best, best_ratio = p, ratio
    if best is not None and best_ratio >= 0.9:
        return {"paragraph": best["index"], "start": 0, "end": len(best["text"]),
                "level": "fuzzy", "ratio": round(best_ratio, 3)}
    return None


# ------------------------------------------------------------------ JSON 解析
def parse_json(raw: str) -> dict:
    """模型偶尔包代码块或加解释，尽量把 JSON 抠出来。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            pass
    raise ValueError("模型没有返回合法 JSON：%s" % text[:120])


# ------------------------------------------------------------------ 校验
_DIGITS = re.compile(r"\d+(?:\.\d+)?")
_MUST_WORDS = ("很好", "精彩", "糟糕", "优秀", "较差", "不够用心", "圆满成功")


def _fabricated(rewrite: str, text: str) -> bool:
    """rewrite 里出现原文没有的数字，视为编造。"""
    if not rewrite:
        return False
    return any(d not in text for d in _DIGITS.findall(rewrite))


def check_finding(f: dict, paragraphs: list[dict]) -> tuple[dict, str]:
    """整理一条意见。返回 (规范化后的意见, 备注)。备注非空表示做了降级。"""
    note = ""
    level = str(f.get("level") or "").strip().lower()
    level = level if level in ("must", "suggest") else "suggest"
    out = {
        "level": level,
        "rule": str(f.get("rule") or "").strip(),
        "dimension": str(f.get("dimension") or "").strip(),
        "anchor": str(f.get("anchor") or "").strip(),
        "occurrence": int(f.get("occurrence") or 1),
        "action": str(f.get("action") or "suggest").strip().lower(),
        "problem": str(f.get("problem") or "").strip(),
        "basis": str(f.get("basis") or "").strip(),
        "rewrite": str(f.get("rewrite") or "").strip(),
        "attach": str(f.get("attach") or "").strip().lower(),
    }
    if out["action"] not in ("replace", "insert", "delete", "suggest"):
        out["action"] = "suggest"

    text = full_text(paragraphs)
    if _fabricated(out["rewrite"], text):
        out["rewrite"] = ""
        note = "改写含原文没有的数字，已降级为建议"

    loc = locate(out["anchor"], paragraphs, out["occurrence"])
    if loc:
        level_keep = out["level"]      # locate 也返回 level=匹配方式，别覆盖等级
        out.update(loc)
        out["level"] = level_keep
        out["match"] = loc.get("level")
        out["located"] = True
    else:
        # 没有锚点 = 缺板块类意见（anchor 本就为空），这是合法情况，不能降级
        out["located"] = bool(out["anchor"]) is False
        if out["attach"] == "title":
            out["paragraph"] = _first_heading_index(paragraphs)
        else:
            out["paragraph"] = -1
            out["attach"] = out["attach"] or "end"
        out["start"] = out["end"] = 0
        if out["anchor"]:
            out["level"] = "suggest" if out["level"] == "must" else out["level"]
            note = (note + "；" if note else "") + "锚点未定位，已挂到文末"
    return out, note


def _first_heading_index(paragraphs: list[dict]) -> int:
    """标题所在段落索引（正文第一段可用），找不到就 0。"""
    for p in paragraphs:
        if p.get("text", "").strip():
            return p["index"]
    return 0


def merge_findings(chunks: list[list[dict]], limits: dict) -> list[dict]:
    """合并多块结果：同一 rule+anchor 去重，按 must 优先，截断到上限。"""
    seen: set[tuple] = set()
    merged: list[dict] = []
    for items in chunks:
        for f in items:
            key = (f.get("rule"), _norm(f.get("anchor") or "")[:40], f.get("paragraph"))
            if key in seen:
                continue
            seen.add(key)
            merged.append(f)
    merged.sort(key=lambda f: (0 if f["level"] == "must" else 1,
                               f.get("paragraph", -1) if f.get("paragraph", -1) >= 0 else 10 ** 6))
    must_cap = int(limits.get("must", 12))
    sug_cap = int(limits.get("suggest", 8))
    out, nm, ns = [], 0, 0
    for f in merged:
        if f["level"] == "must":
            if nm >= must_cap:
                continue
            nm += 1
        else:
            if ns >= sug_cap:
                continue
            ns += 1
        f["id"] = "F%02d" % (len(out) + 1)
        out.append(f)
    return out


def decide_verdict(findings: list[dict], missing_sections: list[str], rules: dict) -> str:
    """结论由代码算。"""
    must = [f for f in findings if f["level"] == "must"]
    if not must:
        return "通过"
    core = rules.get("core_sections") or []
    missing_core = [s for s in missing_sections if s in core]
    critical = {"流程", "危机预案"}
    if len(missing_core) >= 2 or (critical & set(missing_core)):
        return "退回修改"
    return "修改后通过"


def outline(paragraphs: list[dict]) -> str:
    """全文结构参考：只给短行（标题、编号段），帮模型判断板块缺不缺。"""
    lines = []
    for p in paragraphs:
        t = p["text"].strip()
        if not t:
            continue
        if len(t) <= 30 or re.match(r"^[（(]?[一二三四五六七八九十\d]+[、.）)]", t):
            lines.append("[P%03d] %s" % (p["no"], t[:40]))
    return "\n".join(lines[:60])


# ------------------------------------------------------------------ 分块
def chunk_paragraphs(paragraphs: list[dict], limit: int = SPLIT_LIMIT) -> list[list[dict]]:
    chunks, cur, size = [], [], 0
    for p in paragraphs:
        t = p["text"]
        if size + len(t) > limit and cur:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(p)
        size += len(t) + 8
    if cur:
        chunks.append(cur)
    return chunks


# ------------------------------------------------------------------ 板块缺失
_SECTION_KEYS = {
    "综述": ("活动综述", "活动目的", "活动简介", "活动背景", "综述", "目的"),
    "流程": ("活动流程", "流程", "时间安排", "日程", "环节安排"),
    "人员": ("人员安排", "人员分工", "分工", "工作人员", "岗位"),
    "目标": ("预期目标", "活动目标", "目标", "预期效果"),
    "危机预案": ("危机预案", "应急预案", "风险预案", "风险", "突发"),
}
_TITLE_RE = re.compile(
    r"^\s*(?:[一二三四五六七八九十]+[、．.]|第[一二三四五六七八九十]+[部分章节][、．:：]?"
    r"|[（(][一二三四五六七八九十]+[）)]|[\d]+[、．.])\s*"
    r"(.{0,20}?)(?:[:：]?\s*$|[:：])")


def _is_heading(p: dict) -> bool:
    t = p["text"].strip()
    if not t:
        return False
    style = (p.get("style") or "").lower()
    if "heading" in style or style.startswith("标题") or "title" in style:
        return True
    if len(t) <= 24 and re.match(r"^(?:[一二三四五六七八九十]+[、．.]|第[一二三四五六七八九十]+[部分章节]"
                                 r"|[（(][一二三四五六七八九十]+[）)]|[\d]+[、．.])", t):
        return True
    return False


def missing_sections(paragraphs: list[dict]) -> list[str]:
    """判断五个核心板块里缺哪些。

    判定依据（任一命中即视为「有」）：
    1. 标题行里出现板块名；
    2. 某段（≤24 字）后面紧跟表格，且段里有板块名——「活动流程」常写成表格；
    3. 正文出现「XX：」这类引导；
    4. 正文提到该板块并带「见/如下/下表/以表格」等指向（如「活动流程见下表」）。
    """
    headings = [p["text"] for p in paragraphs if _is_heading(p)]
    with_table: set[int] = set()
    for i, p in enumerate(paragraphs):
        if p.get("in_table"):
            continue
        nxt = paragraphs[i + 1] if i + 1 < len(paragraphs) else None
        if nxt is not None and nxt.get("in_table"):
            with_table.add(i)
    body = [p["text"] for p in paragraphs]

    out = []
    for name, keys in _SECTION_KEYS.items():
        if any(k in h for h in headings for k in keys):
            continue
        found = False
        for i in with_table:
            t = body[i].strip()
            if len(t) <= 24 and any(k in t for k in keys):
                found = True
                break
        if not found:
            pat = "|".join(re.escape(k) for k in keys)
            for t in body:
                if re.search(r"(%s)\s*[:：]" % pat, t) or \
                   re.search(r"(%s)[^。]{0,6}(见|如下|下表|以表格)" % pat, t):
                    found = True
                    break
        if not found:
            out.append(name)
    return out


# ------------------------------------------------------------------ 主流程
def review(docx_path: str, cfg: dict, submission: str = "协内自用", kind: str = "",
           on_progress=None, cancel=None) -> dict:
    """跑完一次初审，返回 {findings, verdict, summary, missing, stats, paragraphs}。"""
    from ..review.llm import Client
    from .. import config as config_mod

    def emit(msg, pct=None):
        if on_progress:
            try:
                on_progress(msg, pct)
            except Exception:
                pass

    paragraphs = read_paragraphs(docx_path)
    for p in paragraphs:
        p["text"] = p["text"].replace("\r", "").strip()
    emit("已读取稿件：%d 段" % len(paragraphs), 6)

    rules = prompt_mod.load_rules()
    system = prompt_mod.build_system_prompt(kind=kind, submission=submission)
    outl = outline(paragraphs)
    chunks = chunk_paragraphs([p for p in paragraphs if p["text"].strip()])
    emit("分块完成：%d 块，开始送模型" % len(chunks), 10)

    client = Client(cfg)
    model = config_mod.model_for(cfg, "standard")
    results: list[list[dict]] = []
    notes: list[str] = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}

    for i, chunk in enumerate(chunks, 1):
        if cancel is not None and cancel.is_set():
            notes.append("已取消，只处理了前 %d 块" % (i - 1))
            break
        emit("正在审第 %d/%d 块（%d 段）…" % (i, len(chunks), len(chunk)),
             10 + int(80 * (i - 1) / max(1, len(chunks))))
        msgs = [{"role": "system", "content": system},
                {"role": "user", "content": prompt_mod.user_message(
                    chunk, chunk_no=i, chunk_total=len(chunks), whole_outline=outl)}]
        try:
            raw = client.chat(msgs, model=model, temperature=0.1,
                              max_tokens=int((cfg.get("api") or {}).get("max_tokens") or 0))
            for k in usage:
                usage[k] += int(client.usage.get(k) or 0)
            client.usage = {k: 0 for k in client.usage}
            data = parse_json(raw)
        except Exception as e:  # noqa: BLE001
            notes.append("第 %d 块失败：%s" % (i, e))
            continue
        items = []
        for f in (data.get("findings") or []):
            if not isinstance(f, dict):
                continue
            fixed, note = check_finding(f, paragraphs)
            if note:
                notes.append("%s：%s" % (fixed.get("rule") or "意见", note))
            items.append(fixed)
        results.append(items)

    findings = merge_findings(results, rules.get("limits") or {})
    missing = missing_sections(paragraphs)
    verdict = decide_verdict(findings, missing, rules)
    must_n = sum(1 for f in findings if f["level"] == "must")
    sug_n = len(findings) - must_n
    if missing:
        summary = "缺 %s；必改 %d 条" % ("、".join(missing), must_n) if must_n else "缺 %s" % "、".join(missing)
    else:
        summary = "必改 %d 条，建议 %d 条" % (must_n, sug_n)

    emit("初审完成：必改 %d / 建议 %d" % (must_n, sug_n), 96)
    return {
        "findings": findings,
        "verdict": verdict,
        "summary": summary,
        "missing": missing,
        "notes": notes,
        "usage": usage,
        "paragraphs": len(paragraphs),
        "chunks": len(chunks),
        "model": model,
    }
