"""端到端接口测试：起一个本地服务，走完 上传→审稿→决定→导出→下载。"""
from __future__ import annotations

import json
import pathlib
import socket
import sys
import threading
import time
import urllib.request
import uuid
from http.server import ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import server as srv  # noqa: E402

SAMPLE = ROOT / "tests" / "sample.docx"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Client:
    def __init__(self, base):
        self.base = base

    def req(self, path, method="GET", body=None, raw=None, ctype=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        r = urllib.request.Request(self.base + path, data=data, method=method)
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


def main():
    """用临时 config，绝不碰用户真实的 config.json；测试产出的导出文件事后删掉。"""
    import shutil

    from app import config as config_mod

    tmpdir = ROOT / "tests" / "_tmp_cfg"
    shutil.rmtree(tmpdir, ignore_errors=True)
    tmpdir.mkdir(parents=True, exist_ok=True)
    real_path = config_mod.CONFIG_PATH
    config_mod.CONFIG_PATH = tmpdir / "config.json"
    created: list[pathlib.Path] = []
    try:
        run(created)
    finally:
        config_mod.CONFIG_PATH = real_path
        shutil.rmtree(tmpdir, ignore_errors=True)
        for p in created:
            try:
                pathlib.Path(p).unlink()
            except OSError:
                pass


def run(created: list):
    port = free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), srv.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    c = Client("http://127.0.0.1:%d" % port)
    print("server on", port)

    html = c.req("/")
    print("GET / ->", len(html), "bytes; has title:", b"ReSTAgent" in html)
    css = c.req("/static/style.css")
    print("GET /static/style.css ->", len(css), "bytes")

    cfg = c.req("/api/config")
    print("config api_key_set:", cfg["api"]["api_key_set"], "| mock:", cfg["debug"]["mock"])
    cfg = c.req("/api/config", "POST", {"debug": {"mock": True}})
    print("set mock:", cfg["debug"]["mock"])

    up = c.upload(SAMPLE)
    info = up["files"][0]
    assert "error" not in info, info
    print("upload:", info["name"], info["chars"], "chars,", info["paragraphs"], "paras")

    demo = c.req("/api/demo", "POST", {})
    print("demo:", demo["name"], demo["chars"], "chars")

    est = c.req("/api/estimate", "POST", {"stage": "second", "level": "standard", "chars": info["chars"]})
    print("estimate:", est)

    job = c.req("/api/jobs", "POST", {"file_ids": [info["file_id"]], "stage": "second", "level": "standard"})
    jid = job["id"]
    from app import jobs as jobs_mod
    created.append(jobs_mod.HISTORY / ("%s.json" % jid))   # 审稿历史会落盘，测完清掉
    t0 = time.time()
    while True:
        job = c.req("/api/jobs/" + jid)
        if job["status"] not in ("running", "queued"):
            break
        time.sleep(0.3)
        if time.time() - t0 > 60:
            raise SystemExit("超时")
    print("job:", job["status"], "progress:", job["progress"], "log lines:", len(job["log"]))
    res = job["files"][0]["result"]
    print("findings:", len(res["findings"]), "mock:", res["mock"])
    for f in res["findings"][:4]:
        print("  ", f["id"], f["pid"], f["severity"], f["category"], repr(f["quote"]), "->", repr(f["after"]))

    must = [f["id"] for f in res["findings"] if f["severity"] == "must"]
    r = c.req("/api/jobs/%s/decide" % jid, "POST",
              {"file_id": info["file_id"], "ids": must, "status": "accepted"})
    print("decide:", r["changed"])

    exp = c.req("/api/jobs/%s/export" % jid, "POST", {"file_id": info["file_id"]})
    print("export:", exp["name"], exp["stats"])
    blob = c.req("/api/download?path=" + urllib.parse.quote(exp["path"]))
    assert blob[:2] == b"PK", "下载内容不是 zip"
    print("download ok:", len(blob), "bytes")

    # 校验导出的 docx
    from docx import Document
    out = pathlib.Path(exp["path"])
    created.append(out)
    Document(str(out))
    import zipfile
    with zipfile.ZipFile(out) as zf:
        assert "word/comments.xml" in zf.namelist()
        xml = zf.read("word/document.xml").decode("utf-8")
    print("docx ok: ins=%d del=%d refs=%d" % (xml.count("<w:ins "), xml.count("<w:del "),
                                              xml.count("<w:commentReference ")))

    allx = c.req("/api/jobs/%s/export_all" % jid, "POST", {})
    print("export_all:", allx["name"], allx["count"])
    if allx.get("path"):
        created.append(pathlib.Path(allx["path"]))
    httpd.shutdown()
    print("ALL OK")


if __name__ == "__main__":
    import urllib.parse  # noqa: F401
    main()
