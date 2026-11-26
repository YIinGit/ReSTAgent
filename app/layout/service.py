"""三审服务层：一次调用完成 解析 → 角色判定 → 规则 → 报告。

给服务端/界面用的唯一入口：

    from app.layout import service
    res = service.review("a.idml", "a.pdf", cfg)
    res["ui"]      # 给前端：counts / findings / by_page / markdown
    res["images"]  # 渲染出来的页面 PNG（画红框用）

视觉判定（vision.py）优先；没有视觉模型、调用失败、返回不合法时自动回退到
字号/字体启发式，不会阻塞三审。
"""
from __future__ import annotations

import pathlib

from . import idml as idmlmod
from . import pdf as pdfmod
from . import report as reportmod
from . import rules as rulesmod
from . import vision as visionmod
from . import vision_sheets as sheetsmod


def review(idml_path: str = "", pdf_path: str = "", cfg: dict | None = None,
           use_vision: bool | None = None, out_dir: str = "",
           max_vision_pages: int = 40, on_progress=None) -> dict:
    """执行三审。idml_path / pdf_path 至少给一个（只给 PDF 也能查到字体、字号、越界）。

    on_progress(msg, pct) 可选：用于界面/任务日志上报进度（pct 为 0~100 的整数）。
    """
    def emit(msg: str, pct=None):
        if on_progress:
            try:
                on_progress(msg, pct)
            except Exception:
                pass

    cfg = cfg or {}
    doc = None
    pdfdoc = None
    notes: list[str] = []

    if idml_path and pathlib.Path(idml_path).exists():
        doc = idmlmod.parse(idml_path)
    elif idml_path:
        notes.append("IDML 文件不存在：%s" % idml_path)

    if pdf_path and pathlib.Path(pdf_path).exists():
        pdfdoc = pdfmod.parse(pdf_path)
    elif pdf_path:
        notes.append("PDF 文件不存在：%s" % pdf_path)

    if doc is None and pdfdoc is None:
        raise ValueError("三审需要 IDML 或 PDF 文件")

    emit("已解析文件：IDML %s / PDF %s"
         % (("%d 页" % len(doc.pages)) if doc else "未提供",
            ("%d 页" % len(pdfdoc.pages)) if pdfdoc else "未提供"), 10)

    if use_vision is None:
        use_vision = bool((cfg.get("review") or {}).get("vision", True))
    mode = str((cfg.get("review") or {}).get("vision_mode") or "sheets").lower()

    vision = {}
    images: list[str] = []
    if use_vision and pdfdoc is not None and mode != "off":
        vmodel = (cfg.get("models") or {}).get("vision") or None

        def vision_progress(msg, pct=None):
            emit(msg, 10 + int((pct or 0) * 0.8))

        if mode == "page":
            emit("用视觉模型逐页判定文字角色（%d 页）…" % len(pdfdoc.pages))
            vision = visionmod.classify_pdf(
                pdf_path, cfg, model=vmodel,
                max_pages=max_vision_pages, out_dir=out_dir or "")
        else:
            # 默认：多页拼版（2 页/张），显著减少调用次数与图片 token
            per = int((cfg.get("vision_api") or {}).get("per_sheet") or (cfg.get("review") or {}).get("vision_per_sheet") or 2)
            emit("用视觉模型判定文字角色（每张拼版 %d 页）…" % per)
            vision = sheetsmod.classify_pdf_sheets(
                pdf_path, cfg, model=vmodel, per_sheet=per,
                max_pages=max_vision_pages, out_dir=out_dir or "",
                on_progress=vision_progress)
            if not (vision.get("_meta") or {}).get("vision"):
                err = (vision.get("_meta") or {}).get("error")
                notes.append("拼版视觉判定未生效，改用逐页视觉：%s" % (err or "未知原因"))
                vision = visionmod.classify_pdf(
                    pdf_path, cfg, model=vmodel,
                    max_pages=max_vision_pages, out_dir=out_dir or "")
        meta = vision.get("_meta") or {}
        if meta.get("vision"):
            emit("视觉判定完成：%d 页，%d 张拼版" % (meta.get("ok_pages") or 0,
                                                meta.get("sheets") or 0), 90)
        else:
            notes.append("视觉角色判定未生效，已回退字号/字体启发式：%s"
                         % (meta.get("error") or "模型不支持图片"))
            emit("视觉判定未生效，回退字号/字体启发式", 90)

    rep = rulesmod.check(doc=doc, pdf=pdfdoc, vision=vision)
    rep.notes = list(rep.notes) + notes
    emit("规则检查完成：%s" % ("、".join("%s %d" % (k, v) for k, v in rep.counts().items())), 96)
    ui = reportmod.to_ui(rep)
    pages_meta = []
    if pdfdoc is not None:
        for pg in pdfdoc.pages:
            pages_meta.append({"n": pg.index + 1, "width": round(pg.width, 2),
                               "height": round(pg.height, 2)})
    emit("报告已生成", 100)
    return {
        "ui": ui,
        "markdown": ui.get("markdown", ""),
        "images": images,
        "pages_meta": pages_meta,
        "vision": {k: v for k, v in (vision.get("_meta") or {}).items() if k != "images"},
        "layout": doc.summary() if doc else {},
        "pdf": pdfdoc.summary() if pdfdoc else {},
    }
