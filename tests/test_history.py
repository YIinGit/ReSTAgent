"""新增功能回归测试：审稿历史、规则库、管理员、用量费用、SSE、流式降级。

用法：python tests/test_history.py
用临时 config / 临时 data 目录，不动用户真实配置与数据。
"""
from __future__ import annotations

import json
import pathlib
import secrets
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import admin as admin_mod  # noqa: E402
from app import config as config_mod  # noqa: E402
from app import jobs as jobs_mod  # noqa: E402
from app import rules_lib  # noqa: E402
from app import server as srv  # noqa: E402
from app.review import prompts  # noqa: E402
from app.review.llm import Client as LLMClient  # noqa: E402

SAMPLE = ROOT / "tests" / "sample.docx"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Api:
    def __init__(self, base: str):
        self.base = base

    def req(self, path, method="GET", body=None, headers=None, raw=None, ctype=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        r = urllib.request.Request(self.base + path, data=data, method=method)
        for k, v in (headers or {}).items():
            r.add_header(k, v)
        if ctype:
            r.add_header("Content-Type", ctype)
        elif data is not None:
            r.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(r, timeout=60) as resp:
            payload = resp.read()
            if resp.headers.get("Content-Type", "").startswith("application/json"):
                return json.loads(payload.decode("utf-8"))
            return payload

    def upload(self, path: pathlib.Path):
        boundary = "----ra" + uuid.uuid4().hex
        body = b""
        body += ("--%s\r\n" % boundary).encode()
        body += ('Content-Disposition: form-data; name="file"; filename="%s"\r\n' % path.name).encode()
        body += b"Content-Type: application/octet-stream\r\n\r\n"
        body += path.read_bytes()
        body += ("\r\n--%s--\r\n" % boundary).encode()
        return self.req("/api/upload", "POST", raw=body,
                        ctype="multipart/form-data; boundary=%s" % boundary)


def status_of(fn) -> int:
    try:
        fn()
    except urllib.error.HTTPError as e:
        return e.code
    return 200


def wait_job(c: Api, jid: str, timeout=60):
    t0 = time.time()
    while True:
        job = c.req("/api/jobs/" + jid)
        if job["status"] not in ("running", "queued"):
            return job
        if time.time() - t0 > timeout:
            raise SystemExit("任务超时：%s" % jid)
        time.sleep(0.2)


def check_history(c: Api, created: list):
    up = c.upload(SAMPLE)["files"][0]
    assert "error" not in up, up
    print("upload:", up["name"], up["chars"], "字")

    job = c.req("/api/jobs", "POST", {"file_ids": [up["file_id"]], "stage": "zero", "level": "standard"})
    job = wait_job(c, job["id"])
    jid = job["id"]
    created.append(jid)
    res = job["files"][0]["result"]
    print("zero job:", job["status"], "| pct:", job["progress"]["pct"],
          "| findings:", len(res["findings"]), "| cost:", job["cost"])
    assert job["progress"]["pct"] == 100
    assert "image_suggestions" not in res, "image_suggestions 应该已经被移除"
    assert res["verdict"] and res["verdict"].get("quality") in ("high", "medium", "low"), res["verdict"]
    assert "suggested_grade" not in res["verdict"], "旧的 suggested_grade 不应再输出"
    assert "estimated" in job["usage"], job["usage"]
    assert isinstance(job["cost"], float), job["cost"]

    items = c.req("/api/history")["items"]
    hit = [x for x in items if x["job_id"] == jid]
    assert hit, "历史列表里应有新任务"
    print("history list:", len(items), "条 |", hit[0]["name"], hit[0]["stage_label"],
          "| findings", hit[0]["findings"], "| updated", hit[0]["updated"])
    snap_path = jobs_mod.HISTORY / ("%s.json" % jid)
    assert snap_path.exists(), "history 快照没有落盘"
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    for key in ("job_id", "name", "stage", "level", "model", "files", "usage",
                "created", "updated", "exports", "pricing"):
        assert key in snap, "历史快照缺少字段：%s" % key
    assert snap["files"][0]["result"]["findings"], "历史快照应包含 findings"
    assert "status" in snap["files"][0]["result"]["findings"][0]

    # 模拟服务重启：内存里的任务、文件信息全部清空，历史载入后仍能继续处理并导出
    srv.MANAGER.jobs.clear()
    srv.MANAGER.docs.clear()
    srv.MANAGER.files.clear()
    detail = c.req("/api/history/" + jid)
    loaded = detail["job"]
    assert loaded["id"] == jid and loaded["files"][0]["result"]
    print("history detail after restart:", loaded["id"], loaded["status"],
          "| findings:", len(loaded["files"][0]["result"]["findings"]))

    ids = [f["id"] for f in loaded["files"][0]["result"]["findings"]]
    d = c.req("/api/jobs/%s/decide" % jid, "POST",
              {"file_id": up["file_id"], "ids": ids, "status": "accepted"})
    assert d["changed"] == len(ids), d
    print("decide on history job:", d["changed"])

    exp = c.req("/api/jobs/%s/export" % jid, "POST", {"file_id": up["file_id"]})
    assert pathlib.Path(exp["path"]).exists(), exp
    print("export from history:", exp["name"], exp["stats"])

    detail2 = c.req("/api/history/" + jid)["history"]
    assert detail2["exports"], "导出记录应写进历史快照"
    assert all(f["status"] == "accepted" for f in detail2["files"][0]["result"]["findings"])

    # 删除历史
    ok = c.req("/api/history/" + jid, "DELETE")
    assert ok.get("ok") is True, ok
    print("delete history:", ok)
    code = status_of(lambda: c.req("/api/history/" + jid))
    assert code == 404, code
    assert not snap_path.exists()
    assert jid not in [x["job_id"] for x in c.req("/api/history")["items"]]


def check_estimate(c: Api):
    r = c.req("/api/estimate", "POST", {"stage": "second", "level": "strict", "chars": 8000})
    for key in ("calls", "in_tokens", "out_tokens", "cost", "price_in", "price_out"):
        assert key in r, r
    print("estimate:", r)
    assert r["price_in"] == 10 and r["price_out"] == 30
    assert r["cost"] > 0
    base_cost = r["cost"]

    c.req("/api/config", "POST", {"pricing": {"input_per_m": 20, "output_per_m": 60}})
    r2 = c.req("/api/estimate", "POST", {"stage": "second", "level": "strict", "chars": 8000})
    assert abs(r2["cost"] - base_cost * 2) < 1e-3, (r2, base_cost)
    print("pricing changed ->", r2["price_in"], r2["price_out"], "cost", r2["cost"])
    c.req("/api/config", "POST", {"pricing": {"input_per_m": 10, "output_per_m": 30}})


def check_rules(c: Api):
    payload = c.req("/api/rules")
    groups = payload["groups"]
    assert len(groups) == 3, groups
    second = next(g for g in groups if g["stage"] == "second")
    sec = second["sections"][0]
    rid, title = sec["id"], sec["title"]
    assert sec["enabled"] is True
    print("rules:", [g["label"] + "×" + str(len(g["sections"])) for g in groups])

    before = prompts.system_prompt("second", "standard", config_mod.load())
    assert ("## " + title) in before, "启用时规则应进入 system prompt"

    r = c.req("/api/rules/toggle", "POST", {"id": rid, "enabled": False})
    after_sec = next(s for g in r["groups"] if g["stage"] == "second"
                     for s in g["sections"] if s["id"] == rid)
    assert after_sec["enabled"] is False
    assert config_mod.load()["rules"][rid] is False
    after = prompts.system_prompt("second", "standard", config_mod.load())
    assert ("## " + title) not in after, "关闭的规则不应再发送给模型"
    print("rule off ok:", title)
    c.req("/api/rules/toggle", "POST", {"id": rid, "enabled": True})
    assert ("## " + title) in prompts.system_prompt("second", "standard", config_mod.load())

    r = c.req("/api/rules/custom", "POST",
              {"title": "禁用第一人称", "body": "不要使用第一人称写作。", "stage": "second"})
    cid = r["added"]["id"]
    assert rules_lib.CUSTOM_PATH.exists(), "自定义规则应落盘到 data/rules_custom.json"
    data = json.loads(rules_lib.CUSTOM_PATH.read_text(encoding="utf-8"))
    assert any(x["id"] == cid for x in data)
    prompt = prompts.system_prompt("second", "standard", config_mod.load())
    assert "不要使用第一人称写作。" in prompt, "自定义规则应进入 system prompt"
    c.req("/api/rules/toggle", "POST", {"id": "custom:" + cid, "enabled": False})
    prompt2 = prompts.system_prompt("second", "standard", config_mod.load())
    assert "不要使用第一人称写作。" not in prompt2, "关闭的自定义规则不应发送"
    print("custom rule ok:", cid)


def check_admin(c: Api, old_pw: str, new_pw: str):
    code = status_of(lambda: c.req("/api/admin/login", "POST", {"password": "wrong-password"}))
    assert code == 401, code
    r = c.req("/api/admin/login", "POST", {"password": old_pw})
    token = r["token"]
    h = {"X-Admin-Token": token}
    print("admin login ok, token:", token[:8] + "…")

    code = status_of(lambda: c.req("/api/admin/save", "POST", {"notice": "x"}))
    assert code == 401, "未登录不能写管理员配置"
    state = c.req("/api/admin/state", "POST", {}, headers=h)
    assert state["admin"]["version"], state
    assert "password_hash" not in state["admin"] and "password_salt" not in state["admin"]

    r = c.req("/api/admin/save", "POST", {
        "notice": "本周五截稿",
        "version": "9.9.9",
        "staff": [{"name": "张三", "role": "主编"}, {"name": "李四", "role": "编辑"}],
    }, headers=h)
    assert r["health"]["notice"] == "本周五截稿"
    health = c.req("/api/health")
    assert health["version"] == "9.9.9", health
    assert [s["name"] for s in health["staff"]] == ["张三", "李四"]
    cfg = c.req("/api/config")
    assert "password_hash" not in cfg["admin"] and "password_salt" not in cfg["admin"]
    print("admin save -> version", health["version"], "| notice", health["notice"],
          "| staff", health["staff"])

    r = c.req("/api/admin/password", "POST", {"new_password": new_pw}, headers=h)
    assert r["ok"] is True
    code = status_of(lambda: c.req("/api/admin/login", "POST", {"password": old_pw}))
    assert code == 401, "旧密码应立即失效"
    r2 = c.req("/api/admin/login", "POST", {"password": new_pw})
    assert r2["ok"] is True

    raw = config_mod.CONFIG_PATH.read_text(encoding="utf-8")
    assert "password_hash" not in raw and "password_salt" not in raw, "密码不该再写进 config"
    assert old_pw not in raw and new_pw not in raw
    src = admin_mod.SECRET_PATH.read_text(encoding="utf-8")
    assert "SALT" in src and "HASH" in src
    assert old_pw not in src and new_pw not in src, "源码里只能有盐与哈希"
    salt, hashed = admin_mod.load_secret()
    assert len(salt) == 32 and len(hashed) == 64
    assert admin_mod.verify({}, new_pw) is True
    assert admin_mod.verify({}, old_pw) is False
    print("password stored in source secret file (salt/hash only) | salt len:", len(salt))


def check_sse(c: Api, created: list):
    up = c.upload(SAMPLE)["files"][0]
    job = c.req("/api/jobs", "POST", {"file_ids": [up["file_id"]], "stage": "second", "level": "standard"})
    jid = job["id"]
    created.append(jid)
    url = c.base + "/api/jobs/%s/stream" % jid
    with urllib.request.urlopen(urllib.request.Request(url), timeout=60) as resp:
        ctype = resp.headers.get("Content-Type", "")
        assert ctype.startswith("text/event-stream"), ctype
        chunks = []
        t0 = time.time()
        while time.time() - t0 < 60:
            line = resp.readline()
            if not line:
                break
            chunks.append(line.decode("utf-8", "replace"))
            if '"type": "done"' in chunks[-1] or '"type":"done"' in chunks[-1]:
                break
    body = "".join(chunks)
    assert '"type": "hello"' in body or '"type":"hello"' in body, body[:300]
    assert '"type": "done"' in body or '"type":"done"' in body, body[-300:]
    print("SSE ok:", len(body), "bytes |", ctype)
    wait_job(c, jid)


class FakeStream(BaseHTTPRequestHandler):
    """假 OpenAI 服务：stream 请求返回 SSE，非 stream 返回普通 JSON。"""
    protocol_version = "HTTP/1.1"
    mode = "stream"          # stream | reject

    def log_message(self, *a):
        pass

    def _send(self, code, body: bytes, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            req = {}
        if req.get("stream"):
            if self.mode == "reject":
                self._send(400, b'{"error":"stream_options not supported"}')
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            parts = [
                'data: {"choices":[{"delta":{"content":"{\\"verdict\\""}}]}\n\n',
                'data: {"choices":[{"delta":{"content":":{}"}}]}\n\n',
                'data: {"choices":[{"delta":{"content":"}"}}],'
                '"usage":{"prompt_tokens":12,"completion_tokens":5}}\n\n',
                "data: [DONE]\n\n",
            ]
            for p in parts:
                self.wfile.write(p.encode("utf-8"))
                self.wfile.flush()
            return
        content = "非流式内容"
        self._send(200, json.dumps({
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 30, "completion_tokens": 9},
        }, ensure_ascii=False).encode("utf-8"))


def read_sse(url: str, timeout: float = 60) -> list[dict]:
    events: list[dict] = []
    with urllib.request.urlopen(urllib.request.Request(url), timeout=timeout) as resp:
        ctype = resp.headers.get("Content-Type", "")
        assert ctype.startswith("text/event-stream"), ctype
        for rawline in resp:
            line = rawline.decode("utf-8", "replace").strip()
            if not line or line.startswith("id:") or line.startswith(":"):
                continue
            if line.startswith("data:"):
                try:
                    events.append(json.loads(line[len("data:"):].strip()))
                except json.JSONDecodeError:
                    continue
            if events and events[-1].get("type") == "done":
                break
    return events


def check_stream_job(c: Api):
    """真实链路：假模型 SSE → pipeline on_delta → jobs 事件队列 → /api/jobs/<id>/stream。"""
    port = free_port()
    handler = type("HStream", (FakeStream,), {"mode": "stream"})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        c.req("/api/config", "POST", {
            "debug": {"mock": False},
            "api": {"base_url": "http://127.0.0.1:%d/v1" % port, "api_key": "k",
                    "timeout": 20, "max_retries": 0},
        })
        up = c.upload(SAMPLE)["files"][0]
        job = c.req("/api/jobs", "POST",
                    {"file_ids": [up["file_id"]], "stage": "zero", "level": "standard"})
        events = read_sse(c.base + "/api/jobs/%s/stream" % job["id"])
        text = "".join(e.get("text", "") for e in events if e.get("type") == "delta")
        assert "verdict" in text, events
        done = wait_job(c, job["id"])
        assert done["usage"]["prompt_tokens"] == 12, done["usage"]
        assert done["usage"]["completion_tokens"] == 5, done["usage"]
        assert done["usage"]["estimated"] == 0, done["usage"]
        assert done["cost"] > 0, done["cost"]
        print("SSE + pipeline streaming ok | deltas:", len([e for e in events if e.get("type") == "delta"]),
              "| usage:", done["usage"], "| cost:", done["cost"])
    finally:
        httpd.shutdown()
        c.req("/api/config", "POST", {
            "debug": {"mock": True},
            "api": {"base_url": "", "api_key": ""},
        })


def check_stream_llm():
    port = free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), FakeStream)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        cfg = config_mod.load()
        cfg["api"] = {"base_url": "http://127.0.0.1:%d/v1" % port, "api_key": "k",
                      "timeout": 20, "max_retries": 0}
        deltas = []
        client = LLMClient(cfg)
        text = client.chat([{"role": "user", "content": "hi"}], model="m",
                           stream=True, on_delta=deltas.append)
        assert text == '{"verdict":{}}', text
        assert "".join(deltas) == text, deltas
        assert client.usage["prompt_tokens"] == 12 and client.usage["completion_tokens"] == 5
        assert client.usage["estimated"] == 0
        print("stream parse ok:", text, "| usage:", client.usage, "| deltas:", len(deltas))

        handler = type("H400", (FakeStream,), {"mode": "reject"})
        httpd2 = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        httpd2.daemon_threads = True
        threading.Thread(target=httpd2.serve_forever, daemon=True).start()
        try:
            cfg2 = dict(cfg)
            cfg2["api"] = dict(cfg["api"], base_url="http://127.0.0.1:%d/v1" % httpd2.server_address[1])
            c2 = LLMClient(cfg2)
            got = []
            text2 = c2.chat([{"role": "user", "content": "hi"}], model="m",
                            stream=True, on_delta=got.append)
            assert text2 == "非流式内容", text2
            assert c2.stream_fallback is True
            assert c2.usage["prompt_tokens"] == 30
            print("stream fallback ok:", text2, "| usage:", c2.usage)
        finally:
            httpd2.shutdown()
    finally:
        httpd.shutdown()


def check_static_prompts():
    assert "image_suggestions" not in prompts.JSON_SPEC_FIRST
    assert "quality" in prompts.JSON_SPEC_ZERO and "suggested_grade" not in prompts.JSON_SPEC_ZERO
    first_rule = (ROOT / "app" / "rules" / "first_stage.md").read_text(encoding="utf-8")
    assert "image_suggestions" not in first_rule
    zero_rule = (ROOT / "app" / "rules" / "zero_stage.md").read_text(encoding="utf-8")
    assert "quality" in zero_rule and "1 级" not in zero_rule
    print("prompt specs ok: no image_suggestions, quality present")

    # 进度回调必须带 0–100 的 pct，且兼容只接收 message 的旧回调
    from app.review.pipeline import _Steps
    pcts = []
    steps = _Steps(lambda msg, pct=None: pcts.append(pct), 4)
    for _ in range(4):
        steps("x")
    assert pcts == [0, 25, 50, 75], pcts   # 百分比按已完成步数：宣布最后一步时仍是 75，100 由任务结束写入
    legacy = []
    steps2 = _Steps(lambda msg: legacy.append(msg), 2)
    steps2("a")
    steps2("b")
    assert legacy == ["a", "b"], legacy
    print("progress pct ok:", pcts)


def main() -> int:
    tmp = ROOT / "tests" / "_tmp_history"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    saved = {
        "config": config_mod.CONFIG_PATH,
        "uploads": jobs_mod.UPLOADS,
        "exports": jobs_mod.EXPORTS,
        "sessions": jobs_mod.SESSIONS,
        "history": jobs_mod.HISTORY,
        "custom": rules_lib.CUSTOM_PATH,
        "secret": admin_mod.SECRET_PATH,
    }
    for name in ("uploads", "exports", "sessions", "history"):
        p = tmp / name
        p.mkdir(parents=True, exist_ok=True)
        setattr(jobs_mod, name.upper(), p)
    config_mod.CONFIG_PATH = tmp / "config.json"
    rules_lib.CUSTOM_PATH = tmp / "rules_custom.json"
    # 管理员密码只存源代码文件；测试改到临时文件，绝不碰真实 app/admin_secret.py
    admin_mod.SECRET_PATH = tmp / "admin_secret.py"
    old_pw = secrets.token_urlsafe(9)
    new_pw = secrets.token_urlsafe(9)
    admin_mod.set_password(old_pw)
    srv.ADMIN_TOKENS.clear()
    created: list[str] = []

    port = free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), srv.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    c = Api("http://127.0.0.1:%d" % port)
    print("server on", port)
    try:
        check_static_prompts()
        cfg = c.req("/api/config")
        assert "pricing" in cfg and "rules" in cfg and "admin" in cfg
        assert "password_hash" not in cfg["admin"] and "password_salt" not in cfg["admin"], \
            "配置接口不能泄露密码"
        c.req("/api/config", "POST", {"debug": {"mock": True}})

        check_estimate(c)
        check_rules(c)
        check_history(c, created)
        check_admin(c, old_pw, new_pw)
        check_sse(c, created)
        check_stream_job(c)
        check_stream_llm()
        print("\nALL OK")
        return 0
    finally:
        httpd.shutdown()
        config_mod.CONFIG_PATH = saved["config"]
        jobs_mod.UPLOADS = saved["uploads"]
        jobs_mod.EXPORTS = saved["exports"]
        jobs_mod.SESSIONS = saved["sessions"]
        jobs_mod.HISTORY = saved["history"]
        rules_lib.CUSTOM_PATH = saved["custom"]
        admin_mod.SECRET_PATH = saved["secret"]
        srv.ADMIN_TOKENS.clear()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
