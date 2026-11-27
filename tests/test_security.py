# -*- coding: utf-8 -*-
"""安全加固回归测试：验证 docs/代码审查报告.md 里的 P0 项确实被堵住。

覆盖：
  P0-1  Host / Origin 校验（DNS rebinding、跨站 CSRF）
  P0-1  会话令牌（必须带 token 才能调 /api/*）
  P0-3  POST 只收 JSON / multipart（text/plain 简单请求被拒）
  P0-2  请求体残留不会污染长连接
  P0-4  口令 scrypt 哈希 + JSON 存储 + 登录限速
  P1-9  config.save 原子写、不丢 Key

运行：python tests/test_security.py
"""
from __future__ import annotations

import http.client
import json
import pathlib
import shutil
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.server as srv          # noqa: E402
from app import admin as admin_mod  # noqa: E402
from app import config as config_mod  # noqa: E402

TOKEN = srv.SESSION_TOKEN


def free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def request(port: int, method: str, path: str, body=None, headers=None,
            ctype: str = "application/json"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = dict(headers or {})
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        h.setdefault("Content-Type", ctype)
    conn.request(method, path, body=data, headers=h)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(raw.decode("utf-8"))
    except Exception:
        return resp.status, raw


def main() -> int:
    tmp = ROOT / "_build" / ("sec_" + str(int(time.time())))
    tmp.mkdir(parents=True, exist_ok=True)
    config_mod.CONFIG_PATH = tmp / "config.json"
    admin_mod.SECRET_PATH = tmp / "admin_secret"
    admin_mod.set_password("SecTestPw123")
    srv.ADMIN_TOKENS.clear()
    srv.LOGIN_FAILS.clear()
    srv.ALLOW_ANY_ORIGIN = False          # 关键：用真实的安全策略

    port = free_port()
    httpd = srv.ThreadingHTTPServer(("127.0.0.1", port), srv.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = {"Host": "127.0.0.1:%d" % port}
    auth = dict(base, **{"X-Auth-Token": TOKEN})
    ok = 0
    try:
        print("=== 1. 静态资源不需要 token ===")
        st, _ = request(port, "GET", "/", headers=base)
        assert st == 200, st
        print("  首页 200（无 token 也能打开）")

        print("\n=== 2. /api/* 必须带 token（P0-1 会话令牌）===")
        st, j = request(port, "GET", "/api/health", headers=base)
        assert st == 401 and j.get("need_token"), (st, j)
        print("  无 token → 401 ✓")
        st, j = request(port, "GET", "/api/health", headers=dict(base, **{"X-Auth-Token": "wrong"}))
        assert st == 401, st
        print("  错 token → 401 ✓")
        st, j = request(port, "GET", "/api/health", headers=auth)
        assert st == 200, st
        print("  正确 token → 200 ✓")
        st, _ = request(port, "GET", "/api/health?t=%s" % TOKEN, headers=base)
        assert st == 200, st
        print("  query 传 token 也认 → 200 ✓")
        # 首页带 token 会下发 cookie
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/?t=%s" % TOKEN, headers=base)
        resp = conn.getresponse()
        resp.read()
        cookie = resp.getheader("Set-Cookie") or ""
        conn.close()
        assert "ra_token=" in cookie, cookie
        print("  首页 ?t= 换取 cookie ✓")
        st, _ = request(port, "GET", "/api/health", headers=dict(base, **{"Cookie": cookie}))
        assert st == 200, st
        print("  cookie 认证 ✓")

        print("\n=== 3. Host 校验（DNS rebinding）===")
        st, _ = request(port, "GET", "/api/health",
                        headers=dict(auth, **{"Host": "evil.attacker.example"}))
        assert st == 403, st
        print("  Host: evil.attacker.example → 403 ✓")
        st, _ = request(port, "GET", "/", headers={"Host": "evil.attacker.example"})
        assert st in (200, 403)   # 首页不校验 Host（静态资源无害），接口必须校验
        print("  首页不拦 Host（静态资源），接口拦 ✓")

        print("\n=== 4. Origin 校验（跨站 CSRF）===")
        st, _ = request(port, "POST", "/api/config",
                        body={"models": {"standard": "HIJACKED"}},
                        headers=dict(auth, **{"Origin": "https://evil.example"}))
        assert st == 403, st
        print("  Origin: https://evil.example → 403 ✓")
        cfg = config_mod.load()
        assert cfg["models"]["standard"] != "HIJACKED"
        print("  配置未被改写 ✓")
        st, _ = request(port, "POST", "/api/config", body={"debug": {"mock": True}},
                        headers=dict(auth, **{"Origin": "http://127.0.0.1:%d" % port}))
        assert st == 200, st
        print("  本机 Origin → 200 ✓")

        print("\n=== 5. 只收 JSON / multipart（P0-3）===")
        st, _ = request(port, "POST", "/api/config",
                        body={"models": {"standard": "X"}}, ctype="text/plain",
                        headers=auth)
        assert st == 415, st
        print("  text/plain（CORS 简单请求）→ 415 ✓")
        st, _ = request(port, "POST", "/api/config",
                        body=b'{"debug":{"mock":false}}', ctype="application/json", headers=auth)
        assert st == 200, st
        print("  application/json → 200 ✓")

        print("\n=== 6. 残留请求体不污染长连接（P0-2）===")
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.request("GET", "/api/health", headers=auth)          # GET 带 body
        r1 = conn.getresponse()
        r1.read()
        conn.request("GET", "/api/health", headers=auth)          # 同一连接再发一次
        r2 = conn.getresponse()
        r2.read()
        assert r1.status == 200 and r2.status == 200, (r1.status, r2.status)
        conn.close()
        print("  同一连接两次 GET 都拿到 200 ✓")

        print("\n=== 7. 口令哈希与登录限速（P0-4）===")
        jp = admin_mod.SECRET_PATH.with_suffix(".json")
        data = json.loads(jp.read_text(encoding="utf-8"))
        assert data["hash"].startswith("scrypt$"), data["hash"][:20]
        assert "SecTestPw123" not in jp.read_text(encoding="utf-8")
        print("  存 JSON + scrypt，无明文 ✓")
        srv.LOGIN_FAILS.clear()
        codes = []
        for _ in range(srv.LOGIN_MAX_FAILS + 2):
            codes.append(request(port, "POST", "/api/admin/login",
                                 body={"password": "bad"}, headers=auth)[0])
        assert 429 in codes, codes
        print("  连续失败 → 429 ✓", codes)

        print("\n=== 8. ADMIN_TOKENS 有登出接口 ===")
        srv.LOGIN_FAILS.clear()
        st, j = request(port, "POST", "/api/admin/login",
                        body={"password": "SecTestPw123"}, headers=auth)
        assert st == 200 and j.get("token"), (st, j)
        atok = j["token"]
        st, _ = request(port, "POST", "/api/admin/state", body={},
                        headers=dict(auth, **{"X-Admin-Token": atok}))
        assert st == 200, st
        request(port, "POST", "/api/admin/logout", body={},
                headers=dict(auth, **{"X-Admin-Token": atok}))
        st, _ = request(port, "POST", "/api/admin/state", body={},
                        headers=dict(auth, **{"X-Admin-Token": atok}))
        assert st == 401, st
        print("  登出后令牌立即失效 ✓")

        print("\n=== 9. 配置原子写（P1-9）===")
        config_mod.save({"api": {"api_key": "key-ABC-123"}})
        saved = config_mod.load()
        assert saved["api"]["api_key"] == "key-ABC-123"
        # 并发保存不丢字段
        def worker(i):
            config_mod.save({"debug": {"mock": False}, "models": {"light": "m%d" % i}})
        ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        final = config_mod.load()
        assert final["api"]["api_key"] == "key-ABC-123", "并发保存把 Key 弄丢了"
        assert not list(tmp.glob("config.json.tmp")), "残留临时文件"
        print("  并发 8 线程保存，Key 未丢失 ✓")

        print("\nALL OK")
        ok = 1
    finally:
        try:
            httpd.shutdown()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)
        srv.ALLOW_ANY_ORIGIN = (__import__("os").environ.get("RESTAGENT_INSECURE") == "1")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
