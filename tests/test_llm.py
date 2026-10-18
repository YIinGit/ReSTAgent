"""用本地假 OpenAI 服务验证：HTTP 客户端、JSON 解析、重试、定位、多块/多轮。"""
from __future__ import annotations

import json
import pathlib
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.docx_engine.reader import Doc  # noqa: E402
from app.review.pipeline import Reviewer  # noqa: E402

SAMPLE = ROOT / "tests" / "sample.docx"

CALLS = {"n": 0}
BROKEN_ONCE = {"left": 0}

GOOD = {
    "findings": [
        {"para_id": "P006", "quote": '"这就是自由基"', "category": "标点", "subcategory": "引号",
         "severity": "must", "action": "replace", "before": '"这就是自由基"', "after": "“这就是自由基”",
         "problem": "直引号", "suggestion": "改为中文引号", "rule_ref": "第十讲·引号", "confidence": 0.96},
        {"para_id": "p003", "quote": "那么，当我们给这个分子足够多的能量的时候", "category": "文风",
         "severity": "suggest", "action": "comment_only", "problem": "句子冗长", "suggestion": "拆分",
         "rule_ref": "第六讲·第三节", "confidence": 0.7},
        {"para_id": "P007", "quote": "高兴的说到", "category": "错别字", "severity": "must",
         "action": "replace", "before": "的", "after": "地", "problem": "的/地混用",
         "suggestion": "状语用“地”", "rule_ref": "第八讲·第二节", "confidence": 0.9},
        {"para_id": "P999", "quote": "不存在的段落", "category": "病句", "severity": "must",
         "action": "comment_only", "problem": "应被丢弃", "suggestion": "x"},
        {"para_id": "P004", "quote": "那么，根据共价键断裂的两种方式", "category": "病句", "severity": "should",
         "action": "comment_only", "problem": "关联词重复", "suggestion": "删一个"},
    ]
}


class Fake(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        CALLS["n"] += 1
        try:
            req = json.loads(body)
        except Exception:
            req = {}
        prompt = json.dumps(req, ensure_ascii=False)
        if "复核" in prompt or "尚未发现" in prompt:
            payload = {"findings": [
                {"para_id": "P008", "quote": "直径 318m 洞深 125m", "category": "学术错误",
                 "severity": "must", "action": "comment_only", "problem": "数据存疑",
                 "suggestion": "核实来源", "rule_ref": "第七讲·第三节", "needs_human_check": True,
                 "confidence": 0.6},
            ]}
        else:
            payload = GOOD
        content = json.dumps(payload, ensure_ascii=False)
        if BROKEN_ONCE["left"] > 0:
            BROKEN_ONCE["left"] -= 1
            content = "好的，以下是审稿意见：\n" + content + "\n希望有帮助！"   # 带前后缀，考验容错
        out = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
        }, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    port = free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Fake)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    cfg = config.load()
    cfg["api"] = {"base_url": "http://127.0.0.1:%d/v1" % port, "api_key": "test-key",
                  "timeout": 20, "max_retries": 2}
    cfg["debug"]["mock"] = False
    cfg["review"]["chunk_chars"] = 400
    BROKEN_ONCE["left"] = 1

    doc = Doc(SAMPLE)
    for level in ("standard", "strict"):
        CALLS["n"] = 0
        r = Reviewer(cfg, "second", level)
        res = r.review_file(doc, lambda m: None, threading.Event())
        print("\n[%s] calls=%d findings=%d usage=%s" % (level, CALLS["n"], len(res["findings"]), res["usage"]))
        for f in res["findings"]:
            print("   %s %s [%s/%s] %r -> %r" % (f["id"], f["pid"], f["severity"], f["category"],
                                                 f["quote"][:22], f["after"]))
        assert res["mock"] is False
        assert CALLS["n"] >= 2, "多块审稿应至少调用 2 次"
        pids = {f["pid"] for f in res["findings"]}
        assert "P999" not in pids, "越界定位应被丢弃"
        assert "P003" in pids, "小写段落号应被规范化"
    assert CALLS["n"] >= 2
    httpd.shutdown()
    print("\nALL OK")


if __name__ == "__main__":
    main()
