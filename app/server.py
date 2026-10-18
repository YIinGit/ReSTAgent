"""本地 HTTP 服务（标准库实现）。运行：python -m app.server [--port 5731] [--no-browser]"""
from __future__ import annotations

import argparse
import json
import mimetypes
import pathlib
import socket
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = None  # 运行期由 paths.resource("web") 决定（打包后在 _MEIPASS 里）

if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
    from app import APP_NAME, APP_TAGLINE, APP_VERSION  # noqa: E402
    from app import admin as admin_mod  # noqa: E402
    from app import config as config_mod  # noqa: E402
    from app import jobs as jobs_mod  # noqa: E402
    from app import paths  # noqa: E402
    from app import rules_lib  # noqa: E402
    from app.review import prompts  # noqa: E402
    from app.review.llm import Client, LLMError  # noqa: E402
    from app.review.pipeline import estimate  # noqa: E402
else:
    from . import APP_NAME, APP_TAGLINE, APP_VERSION  # noqa: E402
    from . import admin as admin_mod  # noqa: E402
    from . import config as config_mod  # noqa: E402
    from . import jobs as jobs_mod  # noqa: E402
    from . import paths  # noqa: E402
    from . import rules_lib  # noqa: E402
    from .review import prompts  # noqa: E402
    from .review.llm import Client, LLMError  # noqa: E402
    from .review.pipeline import estimate  # noqa: E402

WEB = paths.resource("web")

MANAGER = jobs_mod.Manager()
MAX_UPLOAD = 40 * 1024 * 1024
LOG_REQUESTS = False
HITS = {"n": 0}   # 首页被请求的次数，桌面启动器用它判断窗口是否真的起来了
ADMIN_TOKENS: set[str] = set()   # 管理员登录后颁发的一次性令牌（重启即失效）


def parse_multipart(body: bytes, boundary: bytes):
    files = []
    for chunk in body.split(b"--" + boundary):
        if not chunk or chunk in (b"--\r\n", b"--"):
            continue
        chunk = chunk.lstrip(b"\r\n")
        if b"\r\n\r\n" not in chunk:
            continue
        head, _, data = chunk.partition(b"\r\n\r\n")
        if data.endswith(b"\r\n"):
            data = data[:-2]
        filename = None
        for line in head.split(b"\r\n"):
            if b"content-disposition" in line.lower():
                for seg in line.split(b";"):
                    seg = seg.strip()
                    if seg.lower().startswith(b"name="):
                        pass
                    if seg.lower().startswith(b"filename="):
                        filename = seg[len(b"filename="):].strip().strip(b'"').decode("utf-8", "replace")
        if filename:
            files.append((filename, data))
    return files


class Handler(BaseHTTPRequestHandler):
    server_version = "%s/%s" % (APP_NAME, APP_VERSION)
    protocol_version = "HTTP/1.1"

    # ---------- 基础 ----------
    def log_message(self, fmt, *args):  # 默认安静，--log 时输出以便排查
        if LOG_REQUESTS:
            sys.stderr.write("[%s] %s\n" % (self.address_string(), fmt % args))
            sys.stderr.flush()

    def _send(self, status: int, body: bytes, ctype: str = "application/json; charset=utf-8",
              extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, status=200):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _error(self, msg, status=400):
        self._json({"error": str(msg)}, status)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            raise ValueError("文件过大")
        return self.rfile.read(n) if n else b""

    def _json_body(self) -> dict:
        raw = self._body()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            raise ValueError("请求体不是合法 JSON")

    # ---------- 路由 ----------
    def do_GET(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            if path in ("/", "/index.html"):
                HITS["n"] += 1
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/api/config":
                return self._json(config_mod.masked(config_mod.load()))
            if path == "/api/health":
                return self._json(self._health())
            if path == "/api/history":
                return self._json({"items": MANAGER.history_list()})
            if path.startswith("/api/history/"):
                job_id = urllib.parse.unquote(path[len("/api/history/"):].strip("/"))
                if not job_id:
                    return self._error("缺少记录 ID", 400)
                try:
                    return self._json(MANAGER.history_detail(job_id))
                except KeyError:
                    return self._error("历史记录不存在", 404)
            if path == "/api/rules":
                return self._json(self._rules_payload(config_mod.load()))
            if path == "/api/download":
                q = urllib.parse.parse_qs(parsed.query)
                p = (q.get("path") or [""])[0]
                return self._download(p)
            if path.startswith("/api/jobs/"):
                parts = path.strip("/").split("/")
                jid = parts[2] if len(parts) > 2 else ""
                if len(parts) > 3 and parts[3] == "stream":
                    return self._stream(jid)
                job = MANAGER.jobs.get(jid)
                if not job:
                    return self._error("任务不存在", 404)
                return self._json(job.to_dict())
            return self._error("未知路径", 404)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._error(e, 500)

    def do_POST(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            if path == "/api/upload":
                return self._upload()
            if path == "/api/demo":
                sample = paths.resource("samples", "示例稿_待审.docx")
                if not sample.exists():
                    return self._error("示例稿件缺失，请先运行 tests/make_demo.py", 404)
                info = MANAGER.register_upload(sample.name, sample.read_bytes())
                return self._json(info)
            if path == "/api/config":
                patch = self._json_body()
                cfg = config_mod.save(patch)
                MANAGER.cfg = cfg
                return self._json(config_mod.masked(cfg))
            if path == "/api/check":
                return self._check()
            if path == "/api/estimate":
                body = self._json_body()
                cfg = config_mod.load()
                stage = body.get("stage") or "second"
                level = body.get("level") or "standard"
                chars = int(body.get("chars") or 0)
                return self._json(estimate(cfg, stage, level, chars))
            if path == "/api/rules/toggle":
                return self._rules_toggle()
            if path == "/api/rules/custom":
                return self._rules_custom()
            if path.startswith("/api/admin/"):
                return self._admin(path[len("/api/admin/"):])
            if path == "/api/jobs":
                body = self._json_body()
                job = MANAGER.create_job(body.get("file_ids") or [], body.get("stage") or "second",
                                         body.get("level") or "standard")
                return self._json(job.to_dict())
            if path.startswith("/api/jobs/"):
                parts = path.strip("/").split("/")
                jid = parts[2]
                job = MANAGER.jobs.get(jid)
                if not job:
                    return self._error("任务不存在", 404)
                action = parts[3] if len(parts) > 3 else ""
                body = self._json_body()
                if action == "decide":
                    n = MANAGER.decide(job, body.get("file_id"), body.get("finding_id"),
                                       body.get("status") or "pending", body.get("final_text"),
                                       ids=body.get("ids"))
                    return self._json({"changed": n, "job": job.to_dict()})
                if action == "cancel":
                    job.cancel.set()
                    job.add_log("收到取消指令")
                    return self._json({"ok": True})
                if action == "export":
                    info = MANAGER.export(job, body.get("file_id"), bool(body.get("include_pending")))
                    return self._json(info)
                if action == "export_all":
                    info = MANAGER.export_all(job)
                    return self._json(info)
                return self._error("未知操作", 404)
            if path == "/api/open":
                body = self._json_body()
                p = jobs_mod.verify_inside(body.get("path") or "", jobs_mod.EXPORTS)
                jobs_mod.open_in_explorer(str(p))
                return self._json({"ok": True})
            return self._error("未知路径", 404)
        except ValueError as e:
            return self._error(e, 400)
        except LLMError as e:
            return self._error(e, 502)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._error(e, 500)

    def do_DELETE(self):
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            if path.startswith("/api/history/"):
                job_id = urllib.parse.unquote(path[len("/api/history/"):].strip("/"))
                if not job_id:
                    return self._error("缺少记录 ID", 400)
                existed = MANAGER.history_delete(job_id)
                if not existed:
                    return self._error("历史记录不存在", 404)
                return self._json({"ok": True, "deleted": job_id})
            return self._error("未知路径", 404)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._error(e, 500)

    # ---------- 具体处理 ----------
    def _health(self) -> dict:
        cfg = config_mod.load()
        pub = admin_mod.public(cfg)
        return {
            "ok": True,
            "name": APP_NAME,
            "version": pub["version"] or APP_VERSION,
            "app_version": APP_VERSION,
            "tagline": APP_TAGLINE,
            "notice": pub["notice"],
            "staff": pub["staff"],
        }

    def _rules_payload(self, cfg: dict) -> dict:
        groups = []
        for stage in rules_lib.STAGES:
            title, _intro, sections = prompts.parse_rule_file(stage)
            if not sections and not title:
                continue
            groups.append({
                "stage": stage,
                "label": rules_lib.STAGE_LABEL.get(stage, stage),
                "title": title,
                "sections": [{
                    "id": prompts.rule_id(stage, sec["title"]),
                    "title": sec["title"],
                    "chars": len(sec["text"]),
                    "enabled": rules_lib.is_enabled(cfg, prompts.rule_id(stage, sec["title"])),
                } for sec in sections],
            })
        custom = []
        for it in rules_lib.load_custom():
            rid = rules_lib.CUSTOM_PREFIX + it["id"]
            custom.append({
                "id": rid,
                "title": it.get("title") or "",
                "stage": it.get("stage") or "",
                "stage_label": rules_lib.STAGE_LABEL.get(it.get("stage"), it.get("stage") or ""),
                "preview": (it.get("body") or "")[:60],
                "enabled": rules_lib.is_enabled(cfg, rid),
            })
        return {"groups": groups, "custom": custom}

    def _rules_toggle(self):
        body = self._json_body()
        rid = str(body.get("id") or "").strip()
        if not rid:
            raise ValueError("缺少规则 ID")
        enabled = bool(body.get("enabled", True))
        cfg = config_mod.save({"rules": {rid: enabled}})
        MANAGER.cfg = cfg
        payload = self._rules_payload(cfg)
        payload["ok"] = True
        return self._json(payload)

    def _rules_custom(self):
        body = self._json_body()
        item = rules_lib.add_custom(body.get("title"), body.get("body"), body.get("stage"))
        cfg = config_mod.load()
        MANAGER.cfg = cfg
        payload = self._rules_payload(cfg)
        payload["ok"] = True
        payload["added"] = item
        return self._json(payload)

    # ---------- 管理员 ----------
    def _admin_ok(self) -> bool:
        token = self.headers.get("X-Admin-Token") or ""
        return bool(token) and token in ADMIN_TOKENS

    def _admin(self, action: str):
        if action == "login":
            body = self._json_body()
            cfg = config_mod.load()
            if not admin_mod.verify(cfg, str(body.get("password") or "")):
                return self._json({"ok": False, "error": "密码不正确"}, 401)
            token = uuid.uuid4().hex
            ADMIN_TOKENS.add(token)
            return self._json({"ok": True, "token": token})
        if not self._admin_ok():
            return self._error("需要管理员登录", 401)
        if action == "state":
            return self._json({"ok": True, "admin": config_mod.masked(config_mod.load())["admin"],
                               "health": self._health()})
        if action == "save":
            body = self._json_body()
            cfg = config_mod.load()
            patch = admin_mod.save_patch(cfg, body)
            cfg = config_mod.save(patch)
            MANAGER.cfg = cfg
            return self._json({"ok": True, "admin": config_mod.masked(cfg)["admin"],
                               "health": self._health()})
        if action == "password":
            body = self._json_body()
            patch = admin_mod.password_patch(str(body.get("new_password") or ""))
            cfg = config_mod.save({"admin": patch})
            MANAGER.cfg = cfg
            return self._json({"ok": True, "admin": config_mod.masked(cfg)["admin"]})
        return self._error("未知操作", 404)

    def _stream(self, job_id: str):
        """SSE：推送模型增量文本与进度；任务结束后发送 done 并关闭连接。"""
        job = MANAGER.jobs.get(job_id)
        if not job:
            return self._error("任务不存在", 404)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            since = int(self.headers.get("Last-Event-ID") or 0)
        except (TypeError, ValueError):
            since = 0

        def send_event(ev: dict, event_id: int | None = None):
            chunk = ""
            if event_id is not None:
                chunk += "id: %d\n" % event_id
            chunk += "data: %s\n\n" % json.dumps(ev, ensure_ascii=False)
            self.wfile.write(chunk.encode("utf-8"))

        def send_deltas():
            nonlocal since
            events, next_idx = job.read_events(since)
            first = next_idx - len(events)
            for i, ev in enumerate(events):
                send_event(ev, first + i + 1)
            since = next_idx

        last_status = None
        try:
            send_event({"type": "hello", "job_id": job.id, "status": job.status})
            self.wfile.flush()
            idle = 0
            while True:
                send_deltas()
                snap = job.to_dict()
                key = (snap["status"], snap["progress"]["pct"], snap["message"])
                if key != last_status:
                    last_status = key
                    send_event({"type": "status", "status": snap["status"],
                                "pct": snap["progress"]["pct"], "message": snap["message"]})
                self.wfile.flush()
                if snap["status"] not in ("queued", "running"):
                    send_deltas()
                    send_event({"type": "done", "status": snap["status"]})
                    self.wfile.flush()
                    break
                idle += 1
                if idle > 3600:   # 约 12 分钟没有任何结束迹象：主动断开，客户端可重连
                    break
                time.sleep(0.2)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        return None

    def _static(self, rel: str):
        p = (WEB / rel).resolve()
        if WEB.resolve() not in p.parents and p != WEB.resolve():
            return self._error("非法路径", 403)
        if not p.exists() or p.is_dir():
            return self._error("文件不存在", 404)
        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self._send(200, p.read_bytes(), ctype)

    def _download(self, path: str):
        try:
            p = jobs_mod.verify_inside(path, jobs_mod.DATA)
        except PermissionError:
            return self._error("非法路径", 403)
        if not p.exists():
            return self._error("文件不存在", 404)
        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        extra = {"Content-Disposition": "attachment; filename*=UTF-8''%s" %
                                      urllib.parse.quote(p.name)}
        self._send(200, p.read_bytes(), ctype, extra)

    def _upload(self):
        ctype = self.headers.get("Content-Type") or ""
        if "multipart/form-data" not in ctype:
            return self._error("请用 multipart/form-data 上传")
        boundary = None
        for seg in ctype.split(";"):
            seg = seg.strip()
            if seg.startswith("boundary="):
                boundary = seg[len("boundary="):].strip('"').encode()
        if not boundary:
            return self._error("缺少 boundary")
        body = self._body()
        files = parse_multipart(body, boundary)
        if not files:
            return self._error("没有收到文件")
        out = []
        for name, blob in files:
            try:
                out.append(MANAGER.register_upload(name, blob))
            except Exception as e:  # noqa: BLE001
                out.append({"name": name, "error": str(e)})
        return self._json({"files": out})

    def _check(self):
        cfg = config_mod.load()
        model = config_mod.model_for(cfg, "standard")
        try:
            from app.review.llm import probe
            res = probe(cfg, model)
        except Exception as e:  # noqa: BLE001
            return self._json({"ok": False, "error": str(e), "hint": "测试过程本身出错：%s" % e})
        return self._json(res)


def pick_port(preferred: int) -> int:
    for port in [preferred] + list(range(preferred + 1, preferred + 12)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main(argv=None):
    ap = argparse.ArgumentParser(description="%s %s 本地审稿服务" % (APP_NAME, APP_VERSION))
    ap.add_argument("--port", type=int, default=5731)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--log", action="store_true", help="打印每个请求，便于排查")
    args = ap.parse_args(argv)
    global LOG_REQUESTS
    LOG_REQUESTS = args.log
    port = pick_port(args.port)
    url = "http://127.0.0.1:%d" % port
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    print("%s v%s 已启动：%s" % (APP_NAME, APP_VERSION, url))
    print("（关闭此窗口即停止服务）")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
