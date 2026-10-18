"""连接诊断：/api/check 背后的 probe() 要能说清「请求了哪个地址、返回了什么」。"""
from __future__ import annotations

import json
import pathlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.review.llm import alternate_bases, normalize_base, probe  # noqa: E402

NGINX_404 = b"<html>\r\n<head><title>404 Not Found</title></head>\r\n<body><center><h1>404 Not Found</h1></center><hr><center>nginx</center></body></html>\r\n"


class Fake(BaseHTTPRequestHandler):
    mode = "404"   # "404" | "ok"

    def log_message(self, *a):
        pass

    def _send(self, code, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.mode == "ok" and self.path.endswith("/models"):
            self._send(200, json.dumps({"data": [{"id": "m"}]}).encode())
        else:
            self._send(404, NGINX_404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        if self.mode == "ok" and self.path.endswith("/chat/completions"):
            body = {"choices": [{"message": {"content": "正常"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2}}
            self._send(200, json.dumps(body).encode())
        else:
            self._send(404, NGINX_404)


def serve(mode: str):
    handler = type("H", (Fake,), {"mode": mode})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def cfg_for(port: int) -> dict:
    return {
        "api": {"base_url": "http://127.0.0.1:%d/v1" % port, "api_key": "sk-test",
                "timeout": 5, "max_retries": 0},
        "models": {"standard": "demo-model"},
    }


def main():
    # URL 归一
    assert normalize_base("https://h/v1/") == "https://h/v1"
    assert normalize_base("https://h/v1/chat/completions") == "https://h/v1"
    assert normalize_base("h/v1") == "https://h/v1"
    assert normalize_base("https://h") == "https://h"
    assert alternate_bases("https://h/v1") == ["https://h"]
    assert "https://h/v1" in alternate_bases("https://h")
    print("URL 归一 OK")

    # 404：要说清地址和状态码，并给出建议与备选探测
    srv, port = serve("404")
    try:
        r = probe(cfg_for(port), "demo-model")
        assert r["ok"] is False
        assert r["status"] == 404, r
        assert r["chat_url"].endswith("/v1/chat/completions"), r["chat_url"]
        assert "404" in r["error"] and r["chat_url"] in r["error"], r["error"]
        assert r["hint"], "404 时应有建议"
        assert r["attempts"], "应有探测记录"
        print("404 诊断 OK |", r["hint"][:40], "…")
        print("   attempts:", [(a["name"], a["status"]) for a in r["attempts"]])
    finally:
        srv.shutdown()

    # 正常：ok=True，带回模型与耗时
    srv, port = serve("ok")
    try:
        r = probe(cfg_for(port), "demo-model")
        assert r["ok"] is True, r
        assert r["reply"] == "正常"
        assert r["ms"] >= 0
        print("正常连接 OK |", r["model"], r["reply"])
    finally:
        srv.shutdown()

    print("ALL OK")


if __name__ == "__main__":
    main()
