"""桌面启动器测试：目录可写、自检、窗口起不来时的兜底、单实例复用。"""
from __future__ import annotations

import pathlib
import socket
import sys
import threading
import unittest.mock as mock
from http.server import ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import desktop  # noqa: E402
from app.server import Handler  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeProc:
    def __init__(self, code=1):
        self.code = code
        self.pid = 4242

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        return self.code


def main():
    print("browser:", desktop.find_browser())
    prof = desktop.browser_profile()
    print("profile:", prof)
    assert prof and prof.is_dir() and (prof / ".w").exists() is False

    # 1) 自检
    rc = desktop.main(["--selftest", "--port", str(free_port())])
    print("selftest rc:", rc)
    assert rc == 0

    # 2) 窗口进程秒退且没请求 -> 退回默认浏览器
    opened = []
    with mock.patch.object(desktop, "find_browser", return_value="fake.exe"), \
         mock.patch.object(desktop, "open_app_window", return_value=FakeProc(1)), \
         mock.patch.object(desktop.webbrowser, "open", side_effect=lambda u: opened.append(u)), \
         mock.patch.object(desktop, "control_window", return_value=None):
        rc = desktop.main(["--port", str(free_port())])
    print("fallback rc:", rc, "opened:", opened)
    assert rc == 0 and len(opened) == 1, "窗口没起来时应退回默认浏览器"

    # 3) 已有实例在跑 -> 直接复用，不再起服务
    port = free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        opened.clear()
        with mock.patch.object(desktop, "find_browser", return_value="fake.exe"), \
             mock.patch.object(desktop, "open_app_window") as oa:
            rc = desktop.main(["--selftest", "--port", str(port)])
        print("reuse rc:", rc)
        assert rc == 0
        with mock.patch.object(desktop, "find_browser", return_value="fake.exe"), \
             mock.patch.object(desktop, "open_app_window", return_value=FakeProc(0)) as oa:
            rc = desktop.main(["--port", str(port)])
        print("reuse window rc:", rc, "called:", oa.called)
        assert rc == 0 and oa.called, "已有实例时应直接开窗口指向它"
    finally:
        httpd.shutdown()
    print("ALL OK")


if __name__ == "__main__":
    main()
