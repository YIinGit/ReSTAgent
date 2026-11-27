"""初审报告：Markdown 与前端用的结构化数据。"""
from __future__ import annotations

LEVEL_TEXT = {"must": "必改", "suggest": "建议"}


def _comment_text(f: dict) -> str:
    """写进 Word 批注的正文。"""
    parts = []
    head = "[%s]" % LEVEL_TEXT.get(f["level"], f["level"])
    if f.get("rule"):
        head += " %s" % f["rule"]
    if f.get("dimension"):
        head += " · %s" % f["dimension"]
    parts.append(head)
    parts.append(f.get("problem") or "")
    if f.get("rewrite"):
        parts.append("建议改为：" + f["rewrite"])
    if f.get("basis"):
        parts.append("依据：" + f["basis"])
    return "\n".join(p for p in parts if p)


def to_markdown(res: dict, title: str = "策划案初审意见") -> str:
    fs = res.get("findings") or []
    must = [f for f in fs if f["level"] == "must"]
    sug = [f for f in fs if f["level"] == "suggest"]
    lines = ["# %s" % title, ""]
    lines.append("**结论**：%s（必改 %d 条 / 建议 %d 条）"
                 % (res.get("verdict", ""), len(must), len(sug)))
    if res.get("summary"):
        lines.append("")
        lines.append("> %s" % res["summary"])
    if res.get("missing"):
        lines.append("")
        lines.append("**缺失板块**：%s" % "、".join(res["missing"]))
    lines.append("")

    def block(name, items):
        if not items:
            return
        lines.append("## %s（%d）" % (name, len(items)))
        lines.append("")
        for f in items:
            where = ("第 %d 段" % f["paragraph"]) if f.get("paragraph", -1) >= 0 else "全文"
            st = f.get("status") or "pending"
            mark = {"accepted": "（已接受）", "rejected": "（已忽略）"}.get(st, "")
            lines.append("### %s%s%s" % ("%s · " % f["rule"] if f.get("rule") else "", where, mark))
            if f.get("anchor"):
                lines.append("> %s" % f["anchor"])
            lines.append("")
            lines.append(f.get("problem") or "")
            if f.get("rewrite"):
                lines.append("")
                lines.append("**建议改为**：%s" % f["rewrite"])
            if f.get("basis"):
                lines.append("")
                lines.append("依据：%s" % f["basis"])
            lines.append("")

    block("必改", must)
    block("建议", sug)

    if res.get("notes"):
        lines.append("## 说明")
        for n in res["notes"]:
            lines.append("- %s" % n)
        lines.append("")
    return "\n".join(lines)


def to_ui(res: dict) -> dict:
    return {
        "verdict": res.get("verdict"),
        "summary": res.get("summary"),
        "missing": res.get("missing") or [],
        "findings": res.get("findings") or [],
        "notes": res.get("notes") or [],
        "stats": {
            "paragraphs": res.get("paragraphs"),
            "chunks": res.get("chunks"),
            "model": res.get("model"),
            "usage": res.get("usage"),
        },
        "markdown": to_markdown(res),
    }


def comments_for_word(res: dict) -> list[dict]:
    """转成 annotate.export_comments_only 需要的 [{id, text, paragraph}]。"""
    out = []
    for i, f in enumerate(res.get("findings") or [], 1):
        out.append({
            "id": i,
            "text": _comment_text(f),
            "paragraph": int(f.get("paragraph", -1)),
        })
    return out
