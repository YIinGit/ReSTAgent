"""《排版要求》规则库：三审的所有可调参数与规则开关（动态调整，不写死在代码里）。

存放位置：用户目录下的 layout_rules.json（和 config.json 同级，升级程序不会覆盖）。
读取顺序：默认值 ← 用户文件。界面上改完立即写入，下次三审生效。

结构（都是扁平可编辑的数值/字符串，方便界面直接生成表单）：

    page.width_mm / height_mm / bleed_mm / tolerance_mm
    grid.font / point_size / char_aki / line_aki / char_width / line_height /
         columns / gutter_mm / chars_per_column / lines_per_page / tolerance
    roles.body.size / heading.size / subheading.size / author.size / caption.size / editbar.size
    tolerance.size_pt（字号允许偏差）
    rules.<规则ID> = true/false（false = 关闭该规则）
"""
from __future__ import annotations

import copy
import json

from .. import paths

RULES_PATH = paths.user_dir() / "layout_rules.json"

DEFAULTS: dict = {
    "page": {
        "width_mm": 185.0,
        "height_mm": 260.0,
        "bleed_mm": 3.0,
        "tolerance_mm": 0.5,
        "columns": 2,
        "gutter_mm": 8.0,
    },
    "grid": {
        "font": "正文衬线",
        "point_size": 9.0,
        "char_aki": 0.0,
        "line_aki": 7.5,
        "char_width": 9.0,
        "line_height": 16.5,
        "chars_per_column": 22,
        "lines_per_page": 38,
        "tolerance": 0.2,
    },
    "roles": {
        "body": {"size": 9.0, "leading": 16.5, "indent_mm": 6.4},
        "heading": {"size": 12.0, "leading": 16.5},
        "subheading": {"size": 10.0, "leading": 16.5},
        "author": {"size": 12.0},
        "caption": {"size": 7.5, "leading": 13.0, "indent_mm": 5.5},
        "editbar": {"size": 6.0},
    },
    "tolerance": {"size_pt": 0.6},
    # 规则开关：false = 关闭。关闭的规则不会出现在报告里。
    "rules": {
        "page.size": True,
        "page.bleed": True,
        "grid.missing": True,
        "grid.font": True,
        "grid.point_size": True,
        "grid.char_aki": True,
        "grid.line_aki": True,
        "grid.scale": True,
        "grid.columns": True,
        "grid.gutter": True,
        "type.stroke": True,
        "type.mixed_font": True,
        "type.size_diff": True,
        "text.single_char_line": True,
        "content.overflow": True,
    },
}

# 中文标签，用于界面表单
LABELS = {
    "page.width_mm": "页面宽度",
    "page.height_mm": "页面高度",
    "page.bleed_mm": "出血",
    "page.tolerance_mm": "页面容差",
    "page.columns": "分栏数",
    "page.gutter_mm": "栏间距",
    "grid.font": "网格字体",
    "grid.point_size": "网格字号",
    "grid.char_aki": "字间距",
    "grid.line_aki": "行间距",
    "grid.char_width": "字符间距（推导）",
    "grid.line_height": "行距（推导）",
    "grid.chars_per_column": "每栏字数",
    "grid.lines_per_page": "每页行数",
    "grid.tolerance": "网格数值容差",
    "roles.body.size": "正文字号",
    "roles.body.leading": "正文行距",
    "roles.body.indent_mm": "正文首行缩进",
    "roles.heading.size": "节标题字号",
    "roles.heading.leading": "节标题行距",
    "roles.subheading.size": "副节标题字号",
    "roles.subheading.leading": "副节标题行距",
    "roles.author.size": "作者名字号",
    "roles.caption.size": "图注字号",
    "roles.caption.leading": "图注行距",
    "roles.caption.indent_mm": "图注缩进",
    "roles.editbar.size": "编辑栏字号",
    "tolerance.size_pt": "字号容差",
}

UNITS = {
    "page.width_mm": "mm", "page.height_mm": "mm", "page.bleed_mm": "mm",
    "page.tolerance_mm": "mm", "page.gutter_mm": "mm",
    "grid.point_size": "pt", "grid.char_aki": "pt", "grid.line_aki": "pt",
    "grid.char_width": "pt", "grid.line_height": "pt",
    "roles.body.size": "pt", "roles.body.leading": "pt", "roles.body.indent_mm": "mm",
    "roles.heading.size": "pt", "roles.heading.leading": "pt",
    "roles.subheading.size": "pt", "roles.subheading.leading": "pt",
    "roles.author.size": "pt", "roles.caption.size": "pt", "roles.caption.leading": "pt",
    "roles.caption.indent_mm": "mm", "roles.editbar.size": "pt",
    "tolerance.size_pt": "pt",
}

RULE_LABELS = {
    "page.size": "页面尺寸 185×260mm",
    "page.bleed": "出血 3mm",
    "grid.missing": "必须启用版面网格",
    "grid.font": "网格字体为正文衬线",
    "grid.point_size": "网格字号 9 点",
    "grid.char_aki": "网格字间距 0",
    "grid.line_aki": "网格行间距 7.5",
    "grid.scale": "网格缩放 100%",
    "grid.columns": "两栏",
    "grid.gutter": "栏间距 8mm",
    "type.stroke": "文字描边必须禁用",
    "type.mixed_font": "正文同行不得混用字体",
    "type.size_diff": "正文字号必须为 9 点",
    "text.single_char_line": "不得单字成行",
    "content.overflow": "实义文字不得越界",
}


def _deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    """默认值 + 用户覆盖。文件损坏时退回默认值，不让三审挂掉。"""
    data: dict = {}
    try:
        if RULES_PATH.exists():
            data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    return _deep_merge(DEFAULTS, data)


def save(patch: dict) -> dict:
    """保存一份覆盖（深合并）。只允许 DEFAULTS 里存在的键，避免写脏数据。"""
    clean = _sanitize(patch)
    cur = load()
    merged = _deep_merge(cur, clean)
    RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    RULES_PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


def reset() -> dict:
    try:
        RULES_PATH.unlink()
    except FileNotFoundError:
        pass
    return load()


def _sanitize(patch: dict) -> dict:
    out: dict = {}

    def walk(src: dict, tpl: dict, dst: dict):
        for k, v in (src or {}).items():
            if k not in tpl:
                continue
            if isinstance(v, dict) and isinstance(tpl[k], dict):
                dst[k] = {}
                walk(v, tpl[k], dst[k])
            elif isinstance(tpl[k], bool):
                dst[k] = bool(v)
            elif isinstance(tpl[k], (int, float)) and not isinstance(tpl[k], bool):
                try:
                    dst[k] = float(v)
                except (TypeError, ValueError):
                    continue
            elif isinstance(tpl[k], str):
                dst[k] = str(v)
            elif isinstance(tpl[k], int):
                try:
                    dst[k] = int(v)
                except (TypeError, ValueError):
                    continue

    walk(patch or {}, DEFAULTS, out)
    return out


def flat() -> dict:
    """扁平化：{"page.width_mm": 185.0, ...}，给界面直接绑定。"""
    out: dict = {}

    def walk(d: dict, prefix: str = ""):
        for k, v in d.items():
            key = prefix + k
            if isinstance(v, dict):
                walk(v, key + ".")
            else:
                out[key] = v

    walk(load())
    return out


def rule_on(rules: dict, rule_id: str) -> bool:
    return bool((rules or {}).get(rule_id, True))
