"""上传文件、审稿任务、导出文件的管理。"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import threading
import time
import uuid
import zipfile

from . import config as config_mod
from . import paths
from .docx_engine.reader import Doc
from .docx_engine.writer import export_docx
from .review.pipeline import STAGE_LABEL, Reviewer, estimate

ROOT = paths.ROOT
DATA = paths.user_dir() / "data"
UPLOADS = DATA / "uploads"
EXPORTS = DATA / "exports"
SESSIONS = DATA / "sessions"
HISTORY = DATA / "history"
for d in (UPLOADS, EXPORTS, SESSIONS, HISTORY):
    d.mkdir(parents=True, exist_ok=True)

SEV_LABEL = {"must": "必改", "should": "建议", "suggest": "可选"}
STATUS_LABEL = {"pending": "待处理", "accepted": "已接受", "edited": "已改写", "rejected": "已忽略"}


def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "_", name or "稿件")
    return name.strip()[:80] or "稿件"


class Job:
    def __init__(self, files, stage, level, config):
        self.id = "j" + uuid.uuid4().hex[:10]
        self.created = _now()
        self.finished = None
        self.stage = stage
        self.level = level
        self.config = config
        self.model = config_mod.model_for(config, level)
        self.status = "queued"
        self.cancel = threading.Event()
        self.message = "排队中"
        self.total = max(1, sum(estimate(config, stage, level, f["chars"])["calls"] for f in files))
        self.done = 0
        self.pct = 0
        self.file_pct: dict[str, int] = {}
        self.events: list[dict] = []
        self.ev_base = 0
        self.log: list[dict] = []
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "estimated": 0}
        self.exports = {}
        self.files = []
        for f in files:
            self.files.append({
                "file_id": f["file_id"],
                "name": f["name"],
                "chars": f["chars"],
                "status": "queued",
                "error": None,
                "result": None,
            })
        self.lock = threading.Lock()

    def add_log(self, msg, level="info"):
        with self.lock:
            self.log.append({"t": _now(), "msg": msg, "level": level})
            self.log = self.log[-400:]
            self.message = msg

    # ---------- 实时输出（SSE） ----------
    def add_delta(self, text, file_name=""):
        if not text:
            return
        with self.lock:
            self.events.append({"type": "delta", "file": file_name, "text": str(text)[:4000]})
            if len(self.events) > 4000:                 # 只保留最近的增量，避免内存膨胀
                drop = len(self.events) - 2000
                del self.events[:drop]
                self.ev_base += drop

    def read_events(self, since: int = 0):
        """返回 (事件列表, 下一事件的绝对序号)；事件按绝对序号 1 起编号。"""
        with self.lock:
            start = int(since or 0) - self.ev_base
            start = max(0, min(start, len(self.events)))
            return list(self.events[start:]), self.ev_base + len(self.events)

    # ---------- 进度 ----------
    def set_pct(self, file_id: str, pct, allow_back: bool = False):
        with self.lock:
            try:
                new = max(0, min(100, int(round(float(pct)))))
            except (TypeError, ValueError):
                return
            old = self.file_pct.get(file_id, 0)
            # 默认只进不退，避免心跳平滑值与真实步骤值互相拉扯
            self.file_pct[file_id] = new if (allow_back or new >= old) else old
            self._recalc_pct()

    def _recalc_pct(self):
        n = max(1, len(self.files))
        total = 0.0
        for fe in self.files:
            if fe.get("status") in ("done", "error", "cancelled"):
                total += 100
            else:
                total += self.file_pct.get(fe["file_id"], 0)
        self.pct = int(round(total / n))
        self.done = min(self.total, int(round(self.total * self.pct / 100.0)))

    def to_dict(self, with_docs=False):
        return {
            "id": self.id,
            "created": self.created,
            "finished": self.finished,
            "stage": self.stage,
            "stage_label": STAGE_LABEL.get(self.stage, self.stage),
            "level": self.level,
            "model": getattr(self, "model", None),
            "status": self.status,
            "message": self.message,
            "progress": {"done": self.done, "total": self.total, "pct": self.pct},
            "files": self.files,
            "log": self.log,
            "usage": self.usage,
            "cost": config_mod.cost_of(self.usage, self.config),
            "exports": getattr(self, "exports", {}),
        }


class Manager:
    def __init__(self):
        self.cfg = config_mod.load()
        self.docs: dict[str, Doc] = {}
        self.files: dict[str, dict] = {}
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    # ---------- 文件 ----------
    def register_upload(self, filename: str, blob: bytes) -> dict:
        if not filename.lower().endswith(".docx"):
            raise ValueError("只支持 .docx 文件。如果是旧版 .doc，请用 Word「另存为」.docx 后再试。")
        fid = "f" + uuid.uuid4().hex[:10]
        folder = UPLOADS / fid
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / safe_name(filename)
        path.write_bytes(blob)
        doc = Doc(path)
        info = doc.summary()
        info["file_id"] = fid
        info["path"] = str(path)
        with self.lock:
            self.docs[fid] = doc
            self.files[fid] = info
        return info

    def get_doc(self, fid: str) -> Doc:
        doc = self.docs.get(fid)
        if doc is None:
            info = self.files.get(fid)
            if not info:
                raise KeyError("文件不存在")
            doc = Doc(info["path"])
            self.docs[fid] = doc
        return doc

    # ---------- 任务 ----------
    def create_job(self, file_ids: list[str], stage: str, level: str) -> Job:
        if stage not in ("zero", "first", "second", "third"):
            raise ValueError("未知审稿环节")
        if level not in ("light", "standard", "strict", "max"):
            raise ValueError("未知审稿力度")
        files = []
        for fid in file_ids:
            info = self.files.get(fid)
            if not info:
                raise ValueError("文件未上传或已失效：%s" % fid)
            files.append(info)
        if not files:
            raise ValueError("请先选择稿件")
        job = Job(files, stage, level, self.cfg)
        with self.lock:
            self.jobs[job.id] = job
        job.exports = {}
        t = threading.Thread(target=self._run, args=(job,), daemon=True)
        t.start()
        return job

    def _heartbeat(self, job: Job):
        """看门狗：模型长时间生成时定期写日志，避免界面看起来卡死。"""
        import time as _t
        waited = 0
        while True:
            _t.sleep(15)
            if job.status != "running":
                return
            if job.cancel.is_set():
                return
            waited += 15
            alive = [f for f in job.files if f["status"] == "running"]
            if not alive:
                continue
            with job.lock:
                chars = sum(len((e.get("text") or "")) for e in job.events)
            job.add_log("模型仍在生成…已等待 %d 秒，已收到约 %d 字输出" % (waited, chars))
            # 长时间静默时轻微推进进度条，避免观感上「卡死」
            for f in alive:
                cur = job.file_pct.get(f["file_id"], 0)
                if cur < 95:
                    job.set_pct(f["file_id"], min(95, cur + 2))

    def _run(self, job: Job):
        job.status = "running"
        job.add_log("开始%s（力度：%s），共 %d 份稿件"
                    % (STAGE_LABEL.get(job.stage), job.level, len(job.files)))
        threading.Thread(target=self._heartbeat, args=(job,), daemon=True).start()
        for fe in job.files:
            if job.cancel.is_set():
                break
            fe["status"] = "running"
            job.set_pct(fe["file_id"], 0)
            try:
                doc = self.get_doc(fe["file_id"])
                reviewer = Reviewer(job.config, job.stage, job.level,
                                    on_delta=lambda text, _fe=fe: job.add_delta(text, _fe["name"]))
                job.add_log("正在审读《%s》" % fe["name"])

                def progress(msg, pct=None, _fe=fe):
                    job.add_log("[%s] %s" % (_fe["name"], msg))
                    if pct is not None:
                        job.set_pct(_fe["file_id"], pct)

                res = reviewer.review_file(doc, progress, job.cancel)
                fe["result"] = res
                fe["status"] = "done" if not job.cancel.is_set() else "cancelled"
                with job.lock:
                    for k in job.usage:
                        job.usage[k] += int((res.get("usage") or {}).get(k) or 0)
                job.set_pct(fe["file_id"], 100)
                job.add_log("《%s》完成：%d 条意见%s"
                            % (fe["name"], len(res.get("findings") or []),
                               "（本地预检）" if res.get("mock") else ""))
            except Exception as e:  # noqa: BLE001
                fe["status"] = "error"
                fe["error"] = str(e)
                job.set_pct(fe["file_id"], 100)
                job.add_log("《%s》失败：%s" % (fe["name"], e), "error")
        failed = [f for f in job.files if f["status"] == "error"]
        job.status = "cancelled" if job.cancel.is_set() else ("error" if failed and len(failed) == len(job.files) else "done")
        job.done = job.total
        job.pct = 100
        job.finished = _now()
        job.add_log("全部结束" if job.status == "done" else ("已取消" if job.status == "cancelled" else "部分失败"))
        self._save(job)

    # ---------- 结果 ----------
    def decide(self, job: Job, file_id: str, finding_id: str | None,
               status: str, final_text: str | None = None, ids: list[str] | None = None):
        fe = next((f for f in job.files if f["file_id"] == file_id), None)
        if not fe or not fe.get("result"):
            raise ValueError("该稿件还没有审稿结果")
        targets = ids or ([finding_id] if finding_id else [])
        changed = 0
        for f in fe["result"]["findings"]:
            if f["id"] in targets:
                if status not in ("pending", "accepted", "edited", "rejected", "comment_only"):
                    raise ValueError("未知状态")
                if status == "comment_only":
                    f["action"] = "comment_only"
                    f["status"] = "accepted"
                else:
                    f["status"] = status
                if final_text is not None:
                    f["final_text"] = final_text
                    if status in ("accepted", "edited") and f["action"] in ("replace", "insert"):
                        f["after"] = final_text
                changed += 1
        self._save(job)
        return changed

    def export(self, job: Job, file_id: str, include_pending=False) -> dict:
        fe = next((f for f in job.files if f["file_id"] == file_id), None)
        if not fe or not fe.get("result"):
            raise ValueError("该稿件还没有审稿结果")
        doc = self.get_doc(file_id)
        items = []
        for f in fe["result"]["findings"]:
            if f["status"] in ("rejected",):
                continue
            if f["status"] == "pending" and not include_pending:
                continue
            action = f["action"]
            after = f.get("final_text") or f.get("after") or ""
            if f["status"] == "comment_only":
                action = "comment_only"
            if action in ("replace", "insert") and not after:
                action = "comment_only"
            comment = self._comment_text(fe, f)
            items.append({
                "pid": f["pid"], "start": f["start"], "end": f["end"],
                "action": action, "after": after, "comment": comment,
            })
        if not items:
            raise ValueError("没有已接受的修改或批注，先处理意见再导出。")
        stem = pathlib.Path(fe["name"]).stem
        out_name = "%s_%s_%s.docx" % (stem, STAGE_LABEL.get(job.stage, job.stage),
                                      dt.datetime.now().strftime("%Y%m%d_%H%M"))
        out_path = EXPORTS / ("%s_%s" % (job.id, safe_name(out_name)))
        stats = export_docx(
            doc, items, out_path,
            author=job.config.get("review", {}).get("author", "ReSTAgent"),
            font=(job.config.get("export", {}) or {}).get("font") or "",
        )
        job.exports[file_id] = {"path": str(out_path), "name": out_path.name, "stats": stats, "at": _now()}
        self._save(job)
        return job.exports[file_id]

    def _comment_text(self, fe, f) -> str:
        head = "【%s·%s】%s" % (SEV_LABEL.get(f["severity"], ""), f["category"],
                                f.get("subcategory") or "")
        lines = [head.strip("· ")]
        if f.get("problem"):
            lines.append("问题：" + f["problem"])
        if f.get("suggestion"):
            lines.append("建议：" + f["suggestion"])
        if f.get("action") in ("replace", "insert") and (f.get("final_text") or f.get("after")):
            lines.append("改为：" + (f.get("final_text") or f.get("after")))
        if f.get("rule_ref"):
            lines.append("依据：" + f["rule_ref"])
        if f.get("needs_human_check"):
            lines.append("（此条需人工核实）")
        return "\n".join(lines)

    def export_all(self, job: Job) -> dict:
        done = []
        for fe in job.files:
            if fe.get("result"):
                try:
                    done.append(self.export(job, fe["file_id"]))
                except Exception as e:  # noqa: BLE001
                    job.add_log("《%s》导出失败：%s" % (fe["name"], e), "error")
        if not done:
            raise ValueError("没有可导出的稿件")
        zpath = EXPORTS / ("%s_全部导出_%s.zip" % (job.id, dt.datetime.now().strftime("%Y%m%d_%H%M")))
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            for d in done:
                zf.write(d["path"], pathlib.Path(d["path"]).name)
        return {"path": str(zpath), "name": zpath.name, "count": len(done)}

    # ---------- 持久化 ----------
    def _save(self, job: Job):
        try:
            data = job.to_dict()
            (SESSIONS / ("%s.json" % job.id)).write_text(
                json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass
        self._save_history(job)

    def _save_history(self, job: Job):
        """每次审稿完成 / 决定 / 导出后刷新 data/history/<job_id>.json。"""
        try:
            snap = self._snapshot(job)
            HISTORY.mkdir(parents=True, exist_ok=True)
            _history_file(job.id).write_text(
                json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass

    def _snapshot(self, job: Job) -> dict:
        data = job.to_dict()
        for fe in data["files"]:
            info = self.files.get(fe.get("file_id")) or {}
            fe["path"] = info.get("path") or ""
        data["job_id"] = job.id
        data["updated"] = _now()
        data["name"] = data["files"][0]["name"] if data["files"] else ""
        data["names"] = [f.get("name") for f in data["files"]]
        data["pricing"] = dict((job.config or {}).get("pricing") or {})
        return data

    # ---------- 历史记录 ----------
    def history_list(self) -> list[dict]:
        out = []
        try:
            paths_ = sorted(HISTORY.glob("*.json"))
        except OSError:
            paths_ = []
        for p in paths_:
            try:
                snap = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(snap, dict):
                continue
            findings = 0
            decided = 0
            for fe in snap.get("files") or []:
                for f in ((fe.get("result") or {}).get("findings") or []):
                    findings += 1
                    if f.get("status") not in (None, "", "pending"):
                        decided += 1
            out.append({
                "job_id": snap.get("job_id") or snap.get("id") or p.stem,
                "name": snap.get("name") or "",
                "names": snap.get("names") or [],
                "stage": snap.get("stage") or "",
                "stage_label": snap.get("stage_label") or STAGE_LABEL.get(snap.get("stage"), ""),
                "level": snap.get("level") or "",
                "model": snap.get("model") or "",
                "status": snap.get("status") or "",
                "created": snap.get("created") or "",
                "updated": snap.get("updated") or snap.get("finished") or "",
                "findings": findings,
                "decided": decided,
                "usage": snap.get("usage") or {},
                "cost": snap.get("cost") or 0,
                "exports": len(snap.get("exports") or {}),
            })
        out.sort(key=lambda x: str(x.get("updated") or x.get("created") or ""), reverse=True)
        return out

    def history_detail(self, job_id: str) -> dict:
        p = _history_file(job_id)
        if not p.exists():
            raise KeyError("历史记录不存在")
        snap = json.loads(p.read_text(encoding="utf-8"))
        job = self.load_history(job_id, snap)
        return {"history": snap, "job": job.to_dict()}

    def load_history(self, job_id: str, snap: dict | None = None) -> Job:
        """把历史快照还原成可继续 decide / export 的 Job（并登记到内存）。"""
        existing = self.jobs.get(job_id)
        if existing is not None and any(fe.get("result") for fe in existing.files):
            return existing
        if snap is None:
            p = _history_file(job_id)
            if not p.exists():
                raise KeyError("历史记录不存在")
            snap = json.loads(p.read_text(encoding="utf-8"))
        job = self._job_from_snapshot(snap)
        with self.lock:
            self.jobs[job.id] = job
        return job

    def _job_from_snapshot(self, snap: dict) -> Job:
        raw_files = [f for f in (snap.get("files") or [])
                     if isinstance(f, dict) and f.get("file_id")]
        if not raw_files:
            raise ValueError("历史记录里没有稿件")
        init_files = []
        for fe in raw_files:
            fid = fe["file_id"]
            name = fe.get("name") or "稿件"
            chars = int(fe.get("chars") or 0)
            init_files.append({"file_id": fid, "name": name, "chars": chars})
            info = dict(self.files.get(fid) or {})
            info.update({"file_id": fid, "name": name, "chars": chars,
                         "paragraphs": info.get("paragraphs", 0),
                         "tables": info.get("tables", 0),
                         "images": info.get("images", 0),
                         "path": fe.get("path") or info.get("path") or ""})
            self.files[fid] = info
        job = Job(init_files, snap.get("stage") or "second", snap.get("level") or "standard", self.cfg)
        job.id = snap.get("job_id") or snap.get("id") or job.id
        job.created = snap.get("created") or job.created
        job.finished = snap.get("finished")
        job.model = snap.get("model") or job.model
        job.status = snap.get("status") or "done"
        job.done = job.total
        job.pct = 100
        job.log = list(snap.get("log") or [])
        job.usage = dict(snap.get("usage") or job.usage)
        job.exports = dict(snap.get("exports") or {})
        pricing = snap.get("pricing")
        if isinstance(pricing, dict) and pricing:
            # 历史记录按当时的单价结算，不因后来改价而变化
            job.config = dict(self.cfg)
            job.config["pricing"] = pricing
        for fe_snap, fe_job in zip(raw_files, job.files):
            fe_job["status"] = fe_snap.get("status") or "done"
            fe_job["error"] = fe_snap.get("error")
            fe_job["result"] = fe_snap.get("result")
            job.file_pct[fe_job["file_id"]] = 100
        return job

    def history_delete(self, job_id: str) -> bool:
        p = _history_file(job_id)
        existed = p.exists()
        if existed:
            p.unlink()
        with self.lock:
            self.jobs.pop(job_id, None)
        return existed


def _history_file(job_id: str) -> pathlib.Path:
    return HISTORY / ("%s.json" % safe_name(str(job_id)))


def open_in_explorer(path: str):
    import os
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError("文件不存在")
    os.startfile(str(p))  # noqa: S606


def verify_inside(path: str, base: pathlib.Path) -> pathlib.Path:
    p = pathlib.Path(path).resolve()
    b = base.resolve()
    if b not in p.parents and p != b:
        raise PermissionError("路径不在允许范围内")
    return p
