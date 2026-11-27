"""初审任务管理：上传 docx → 后台跑初审 → 进度 / 结果 / 导出批注版。

与 jobs.py（一/二审）、third_jobs.py（三审）分开，互不影响。
"""
from __future__ import annotations

import pathlib
import threading
import time
import traceback
import uuid

from . import config as config_mod
from . import paths
from .campaign import annotate as annotate_mod
from .campaign import report as report_mod
from .campaign import reviewer as reviewer_mod

DATA = paths.data_dir()
CAMP_DIR = DATA / "campaign"
MAX_SIZE = 40 * 1024 * 1024


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def safe_name(name: str) -> str:
    name = (name or "策划案.docx").replace("\\", "_").replace("/", "_")
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    return name.strip() or "策划案.docx"


class CampJob:
    def __init__(self, src_path: str, name: str, cfg: dict, kind: str, submission: str):
        self.id = "c" + uuid.uuid4().hex[:10]
        self.created = _now()
        self.finished = None
        self.status = "running"
        self.pct = 0
        self.log: list[dict] = []
        self.error = None
        self.result = None
        self.source = src_path
        self.name = name
        self.kind = kind
        self.submission = submission
        self.cfg = cfg
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.dir = CAMP_DIR / self.id
        self.dir.mkdir(parents=True, exist_ok=True)

    def add_log(self, msg, level="info", pct=None):
        with self.lock:
            self.log.append({"t": time.strftime("%H:%M:%S"), "msg": str(msg), "level": level})
            if len(self.log) > 300:
                del self.log[:80]
            if pct is not None:
                self.pct = max(0, min(100, int(pct)))
        if level == "error":
            self.error = str(msg)

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
                "file": {"name": self.name},
                "kind": self.kind,
                "submission": self.submission,
            }
            if full and self.result:
                data["result"] = report_mod.to_ui(self.result)
            return data


class CampManager:
    def __init__(self):
        self.jobs: dict[str, CampJob] = {}
        self.lock = threading.Lock()

    def create(self, blob: bytes, name: str, cfg: dict | None = None,
               kind: str = "", submission: str = "协内自用") -> CampJob:
        if not blob:
            raise ValueError("没有收到文件")
        if len(blob) > MAX_SIZE:
            raise ValueError("文件过大（上限 40MB）")
        if not name.lower().endswith(".docx"):
            raise ValueError("只支持 .docx；旧版 .doc 请先用 Word「另存为」.docx")
        tmp = CAMP_DIR / ("up" + uuid.uuid4().hex[:10])
        tmp.mkdir(parents=True, exist_ok=True)
        src = tmp / safe_name(name)
        src.write_bytes(blob)
        job = CampJob(str(src), safe_name(name), cfg or config_mod.load(), kind, submission)
        with self.lock:
            self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def get(self, job_id: str) -> CampJob:
        job = self.jobs.get(job_id)
        if job is None:
            raise KeyError("初审任务不存在或已过期")
        return job

    def _run(self, job: CampJob):
        job.add_log("开始初审：%s（%s · %s）" % (job.name, job.kind or "类型待判", job.submission), pct=3)
        try:
            res = reviewer_mod.review(
                job.source, job.cfg, submission=job.submission, kind=job.kind,
                on_progress=lambda m, p=None: job.add_log(m, pct=p),
                cancel=job.cancel,
            )
            job.result = res
            md = report_mod.to_markdown(res, title="策划案初审意见：%s" % job.name)
            (job.dir / "初审意见.md").write_text(md, encoding="utf-8")
            job.status = "done"
            job.finished = _now()
            job.add_log("初审完成：%s（必改 %d / 建议 %d）"
                        % (res["verdict"],
                           sum(1 for f in res["findings"] if f["level"] == "must"),
                           sum(1 for f in res["findings"] if f["level"] == "suggest")), pct=100)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            job.status = "error"
            job.error = str(e)
            job.finished = _now()
            job.add_log("初审失败：%s" % e, level="error", pct=100)

    # ---------- 导出 ----------
    def export_docx(self, job: CampJob) -> pathlib.Path:
        if not job.result:
            raise ValueError("还没有初审结果")
        out = job.dir / (pathlib.Path(job.name).stem + "_初审批注版.docx")
        comments = report_mod.comments_for_word(job.result)
        annotate_mod.export_comments_only(job.source, str(out), comments)
        return out

    def export_md(self, job: CampJob) -> pathlib.Path:
        p = job.dir / "初审意见.md"
        if not p.exists():
            raise ValueError("报告尚未生成")
        return p


CAMP = CampManager()
