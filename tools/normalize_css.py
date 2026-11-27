"""把 style.css 里的硬编码颜色与字号换成令牌变量。

按 UI重构方案 §3.2/§3.3 的映射表逐条替换（不是盲目正则）。
运行：python tools/normalize_css.py
"""
from __future__ import annotations

import pathlib
import re

CSS = pathlib.Path(__file__).resolve().parent.parent / "web" / "style.css"

# 硬编码颜色 → 令牌（按语境映射，见方案步骤 1）
COLOR_MAP = {
    "#2b59d4": "var(--signal)",
    "#244bb8": "var(--signal)",
    "#5b8cff": "var(--signal)",
    "#6d95ff": "var(--signal)",
    "#eef3ff": "var(--signal-glow)",
    "#1c2947": "var(--signal-glow)",
    "#101828": "var(--ink-1)",
    "#e8ebf1": "var(--ink-1)",
    "#eef2f8": "var(--ink-1)",
    "#c9d3e4": "var(--ink-2)",
    "#c2c9d5": "var(--ink-2)",
    "#344054": "var(--ink-2)",
    "#667085": "var(--ink-3)",
    "#8a94a6": "var(--ink-3)",
    "#b42318": "var(--sev-must)",
    "#ff9c8f": "var(--sev-must)",
    "#b54708": "var(--sev-should)",
    "#f0c574": "var(--sev-should)",
    "#067647": "var(--ok)",
    "#6fdaa1": "var(--ok)",
    "#fff": "var(--paper)",
    "#ffffff": "var(--paper)",
    "#ffe9a3": "var(--paper-mark)",
}

# 字号 → 阶梯
SIZE_MAP = {
    "11": "var(--t-xs)",
    "11.5": "var(--t-sm)",
    "12": "var(--t-sm)",
    "12.5": "var(--t-md)",
    "13": "var(--t-md)",
    "13.5": "var(--t-md)",
    "14": "var(--t-md)",
    "15": "var(--t-lg)",
    "16": "var(--t-doc)",
    "17": "var(--t-lg)",
    "20": "var(--t-2xl)",
}

# 圆角 → 4 档
RADIUS_MAP = {"7px": "var(--r-sm)", "9px": "var(--r-sm)", "10px": "var(--r-md)",
              "12px": "var(--r-md)", "14px": "var(--r-lg)", "999px": "var(--r-full)"}


def main() -> int:
    s = CSS.read_text(encoding="utf-8")
    before_colors = len(re.findall(r"#[0-9a-fA-F]{3,8}\b", s))
    before_sizes = len(re.findall(r"font-size:\s*[\d.]+px", s))

    # 1) 颜色：先长后短，避免 #fff 吃掉 #ffffff
    for old in sorted(COLOR_MAP, key=len, reverse=True):
        s = re.sub(re.escape(old) + r"\b", COLOR_MAP[old], s, flags=re.I)

    # 2) rgba(43,89,212,...) → signal-glow
    s = re.sub(r"rgba\(43\s*,\s*89\s*,\s*212\s*,[^)]*\)", "var(--signal-glow)", s)
    s = re.sub(r"rgba\(79\s*,\s*227\s*,\s*193\s*,[^)]*\)", "var(--signal-glow)", s)

    # 3) 字号
    def size_repl(m: re.Match) -> str:
        val = m.group(1)
        return "font-size:%s" % SIZE_MAP.get(val, "var(--t-md)")

    s = re.sub(r"font-size:\s*([\d.]+)px", size_repl, s)

    # 4) 圆角
    for old, new in RADIUS_MAP.items():
        s = re.sub(r"border-radius:\s*" + re.escape(old), "border-radius:" + new, s)

    # 5) 字体栈
    s = re.sub(r"font:\s*14px/1\.62[^;]*;",
               "font: var(--t-md)/1.62 var(--font-sans);", s)

    CSS.write_text(s, encoding="utf-8")
    after_colors = len(re.findall(r"#[0-9a-fA-F]{3,8}\b", s))
    after_sizes = len(re.findall(r"font-size:\s*[\d.]+px", s))
    left_sizes = sorted(set(re.findall(r"font-size:\s*([\d.]+)px", s)))
    print("硬编码颜色：%d → %d" % (before_colors, after_colors))
    print("字号 px 值：%d → %d（残留：%s）" % (before_sizes, after_sizes, left_sizes or "无"))
    if after_colors:
        left = sorted(set(re.findall(r"#[0-9a-fA-F]{3,8}\b", s)))
        print("残留颜色：", left[:20])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
