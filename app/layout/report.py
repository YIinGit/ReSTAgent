"""三审报告输出：Markdown / HTML 片段，以及前端页面高亮所需的数据。"""
from __future__ import annotations

from .model import LEVEL_LABEL, ReviewReport

LEVEL_ORDER = {"major": 0, "minor": 1, "info": 2}


def grouped(rep: ReviewReport) -> dict:
    """按等级分组，同一规则同一页的合并展示。"""
    out = {"major": [], "minor": [], "info": []}
    for f in sorted(rep.findings, key=lambda x: (LEVEL_ORDER.get(x.level, 9),
                                                 x.category, x.page)):
        out.setdefault(f.level, []).append(f)
    return out


def to_markdown(rep: ReviewReport, title: str = "三审报告") -> str:
    c = rep.counts()
    lines = ["# %s" % title, "",
             "**总计**：%d 条（必改 %d / 建议改 %d / 待核实 %d）"
             % (c["total"], c["major"], c["minor"], c["info"]), ""]

    g = grouped(rep)
    for level in ("major", "minor", "info"):
        items = g.get(level) or []
        if not items:
            continue
        lines.append("## %s（%d）" % (LEVEL_LABEL.get(level, level), len(items)))
        lines.append("")
        by_cat: dict[str, list] = {}
        for f in items:
            by_cat.setdefault(f.category or "其他", []).append(f)
        for cat, fs in by_cat.items():
            lines.append("### %s" % cat)
            for f in fs:
                where = ("第 %d 页" % f.page) if f.page > 0 else "全文"
                lines.append("- **%s**｜%s" % (where, f.message))
                if f.standard:
                    lines.append("  - 标准：%s" % f.standard)
                if f.suggestion:
                    lines.append("  - 建议：%s" % f.suggestion)
            lines.append("")

    if rep.notes:
        lines.append("## 说明")
        for n in rep.notes:
            lines.append("- %s" % n)
        lines.append("")

    if rep.stats:
        lines.append("## 统计")
        for k, v in rep.stats.items():
            if isinstance(v, dict):
                continue
            lines.append("- %s：%s" % (k, v))
        lines.append("")
    return "\n".join(lines)


def to_page_highlights(rep: ReviewReport) -> dict[int, list[dict]]:
    """页码 → 需要画红框的区域列表（供前端叠在页面图上）。"""
    out: dict[int, list[dict]] = {}
    for f in rep.findings:
        if not f.bbox or f.page <= 0:
            continue
        out.setdefault(f.page, []).append({
            "box": [round(v, 2) for v in f.bbox],
            "level": f.level,
            "rule": f.rule,
            "message": f.message,
        })
    return out


def to_ui(rep: ReviewReport) -> dict:
    """给前端的结果结构。"""
    return {
        "counts": rep.counts(),
        "findings": [f.as_dict() for f in rep.findings],
        "by_page": to_page_highlights(rep),
        "stats": rep.stats,
        "notes": rep.notes,
        "markdown": to_markdown(rep),
    }
