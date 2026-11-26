"""三审任务管理：上传 IDML + PDF → 后台跑版式检查 → 进度 / 结果 / 页面图。

与一审/二审的 jobs.py 分开：
- 一审/二审处理 .docx，产出可接受/拒绝的意见条目，最终导出带修订的 Word；
- 三审处理 .indd 导出的 IDML + PDF，产出「版面问题报告 + 页面红框」，导出 Markdown 报告。

任务对象保留日志与百分比，前端用轮询（1~2 秒）取状态，因为视觉判定是分钟级。
"""
from __future__ import annotations

import pathlib
import threading
import time
import traceback
import uuid

from . import config as config_mod
from . import paths
from .layout import pdf as pdfmod
from .layout import service

DATA = paths.data_dir()
THIRD_DIR = DATA / "third"
MAX_FILES = {"idml": 80 * 1024 * 1024, "pdf": 300 * 1024 * 1024}


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def safe_name(name: str) -> str:
    name = (name or "file").replace("\\", "_").replace("/", "_")
    bad = '<>:"/\\|?*'
    for ch in bad:
        name = name.replace(ch, "_")
    return name.strip() or "file"


class ThirdJob:
    def __init__(self, idml_path: str, pdf_path: str, cfg: dict, vision: bool = True,
                 per_sheet: int = 2, scale: float = 2.2):
        self.id = "t" + uuid.uuid4().hex[:10]
        self.created = _now()
        self.finished = None
        self.status = "pending"
        self.pct = 0
        self.log: list[dict] = []
        self.error = None
        self.result = None
        self.idml_path = idml_path
        self.pdf_path = pdf_path
        self.vision = vision
        self.per_sheet = per_sheet
        self.scale = scale
        self.cfg = cfg
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.dir = THIRD_DIR / self.id
        self.dir.mkdir(parents=True, exist_ok=True)

    # ---------- 进度 ----------
    def add_log(self, msg: str, level: str = "info", pct=None):
        with self.lock:
            self.log.append({"t": time.strftime("%H:%M:%S"), "msg": str(msg), "level": level})
            if len(self.log) > 400:
                del self.log[:100]
            if pct is not None:
                self.pct = max(0, min(100, int(pct)))
        if level == "error":
            self.error = str(msg)

    def set_pct(self, pct):
        with self.lock:
            self.pct = max(self.pct, max(0, min(100, int(pct))))

    def to_dict(self, full: bool = False) -> dict:
        with self.lock:
            data = {
                "id": self.id,
                "created": self.created,
                "finished": self.finished,
                "status": self.status,
                "pct": self.pct,
                "log": list(self.log),
                "error": self.error,
                "vision": self.vision,
                "file": {
                    "idml": pathlib.Path(self.idml_path).name if self.idml_path else "",
                    "pdf": pathlib.Path(self.pdf_path).name if self.pdf_path else "",
                },
            }
            if full and self.result:
                data["result"] = self.result
        return data


class ThirdManager:
    def __init__(self):
        self.jobs: dict[str, ThirdJob] = {}
        self.lock = threading.Lock()

    def create(self, idml_bytes: bytes, idml_name: str, pdf_bytes: bytes, pdf_name: str,
               cfg: dict | None = None, vision: bool = True, per_sheet: int = 2,
               scale: float = 2.2) -> ThirdJob:
        if not idml_bytes and not pdf_bytes:
            raise ValueError("请至少选择 IDML 或 PDF 文件")
        if idml_bytes and len(idml_bytes) > MAX_FILES["idml"]:
            raise ValueError("IDML 文件过大（上限 80MB）")
        if pdf_bytes and len(pdf_bytes) > MAX_FILES["pdf"]:
            raise ValueError("PDF 文件过大（上限 300MB）")

        tmp = THIRD_DIR / ("up" + uuid.uuid4().hex[:10])
        tmp.mkdir(parents=True, exist_ok=True)
        idml_path = ""
        pdf_path = ""
        if idml_bytes:
            if not idml_name.lower().endswith(".idml"):
                raise ValueError("IDML 需要是 .idml 文件（InDesign：文件 → 导出 → InDesign 标记语言）")
            idml_path = str(tmp / safe_name(idml_name))
            pathlib.Path(idml_path).write_bytes(idml_bytes)
        if pdf_bytes:
            if not pdf_name.lower().endswith(".pdf"):
                raise ValueError("PDF 需要是 .pdf 文件")
            pdf_path = str(tmp / safe_name(pdf_name))
            pathlib.Path(pdf_path).write_bytes(pdf_bytes)

        job = ThirdJob(idml_path, pdf_path, cfg or config_mod.load(),
                       vision=bool(vision), per_sheet=int(per_sheet or 2),
                       scale=float(scale or 2.2))
        with self.lock:
            self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def get(self, job_id: str) -> ThirdJob:
        job = self.jobs.get(job_id)
        if job is None:
            raise KeyError("三审任务不存在或已过期")
        return job

    # ---------- 执行 ----------
    def _run(self, job: ThirdJob):
        job.status = "running"
        job.add_log("开始三审：IDML=%s，PDF=%s"
                    % (pathlib.Path(job.idml_path).name if job.idml_path else "无",
                       pathlib.Path(job.pdf_path).name if job.pdf_path else "无"), pct=2)

        def progress(msg, pct=None):
            job.add_log(msg, pct=pct)

        try:
            res = service.review(
                job.idml_path, job.pdf_path, job.cfg,
                use_vision=job.vision,
                out_dir=str(job.dir / "vision"),
                on_progress=progress,
            )
            pages = []
            if job.pdf_path:
                job.add_log("渲染高清页面图，供界面红框定位…", pct=97)
                try:
                    # 默认 2.2 倍（约 158dpi）：界面按原像素显示，不再被浏览器二次缩放糊掉
                    scale = float(getattr(job, "scale", 0) or 2.2)
                    imgs = pdfmod.page_images(job.pdf_path, str(job.dir / "pages"), scale=scale)
                    metas = res.get("pages_meta") or []
                    for i, img in enumerate(imgs):
                        meta = metas[i] if i < len(metas) else {}
                        w = float(meta.get("width") or 0)
                        h = float(meta.get("height") or 0)
                        pages.append({
                            "n": i + 1,
                            "image": pathlib.Path(img).name,
                            "width": w,
                            "height": h,
                            "px_width": round(w * scale),
                            "px_height": round(h * scale),
                        })
                except Exception as e:  # noqa: BLE001
                    job.add_log("页面图渲染失败：%s" % e, level="error")

            md = res.get("markdown") or ""
            try:
                (job.dir / "三审报告.md").write_text(md, encoding="utf-8")
            except Exception:
                pass

            ui = res.get("ui") or {}
            job.result = {
                "counts": ui.get("counts") or {},
                "findings": ui.get("findings") or [],
                "by_page": ui.get("by_page") or {},
                "stats": ui.get("stats") or {},
                "notes": ui.get("notes") or [],
                "markdown": md,
                "vision": res.get("vision") or {},
                "layout": res.get("layout") or {},
                "pdf": res.get("pdf") or {},
                "pages": pages,
                "scale": float(getattr(job, "scale", 2.2) or 2.2),
                "report_path": str(job.dir / "三审报告.md"),
            }
            job.status = "done"
            job.finished = _now()
            job.add_log("三审完成：%s"
                        % "、".join("%s %d" % (k, v) for k, v in (job.result["counts"] or {}).items()),
                        pct=100)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            job.status = "error"
            job.error = str(e)
            job.finished = _now()
            job.add_log("三审失败：%s" % e, level="error", pct=100)


THIRD = ThirdManager()
