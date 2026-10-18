"""桌面窗口启动器（零第三方依赖）。

双击 ReSTAgent.pyw：进程内起本地服务 → 用 Edge/Chrome 的 --app 模式打开一个
没有地址栏、没有标签页的独立窗口 → 窗口关闭时自动停服务。

找不到 Chromium 时退回默认浏览器 + 一个 tkinter 小控制窗。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from http.server import ThreadingHTTPServer  # noqa: E402

from app import APP_NAME, APP_TAGLINE, APP_VERSION  # noqa: E402
from app import paths  # noqa: E402
from app.server import HITS, Handler, pick_port  # noqa: E402

DATA_DIR = paths.user_dir()
LOG_PATH = DATA_DIR / "desktop.log"
LOCAL_PROFILE = pathlib.Path(os.environ.get("LOCALAPPDATA") or pathlib.Path.home()) / APP_NAME / "browser"


def browser_profile() -> pathlib.Path | None:
    """给窗口一个独立浏览器配置目录；用户目录不可写时退到临时目录。"""
    import tempfile
    for base in (
        LOCAL_PROFILE,
        pathlib.Path(tempfile.gettempdir()) / (APP_NAME + "-browser"),
    ):
        try:
            base.mkdir(parents=True, exist_ok=True)
            probe = base / ".w"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return base
        except Exception:
            continue
    return None


def log(msg: str):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def probe(url: str, timeout: float = 1.5):
    """已有实例在跑就返回它的 health，否则 None。"""
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data if data.get("name") == APP_NAME else None
    except Exception:
        return None


def find_browser():
    """返回可用的 Chromium 系浏览器路径（Edge 优先）。"""
    import winreg  # 仅 Windows 有

    def from_registry(exe):
        for root, sub in (
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\%s" % exe),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\%s" % exe),
        ):
            try:
                with winreg.OpenKey(root, sub) as k:
                    p = winreg.QueryValue(k, "")
                if p and pathlib.Path(p).exists():
                    return str(pathlib.Path(p))
            except OSError:
                continue
        return None

    for exe in ("msedge.exe", "chrome.exe"):
        p = from_registry(exe)
        if p:
            return p

    local = pathlib.Path(os.environ.get("LOCALAPPDATA", ""))
    pf = pathlib.Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    pf86 = pathlib.Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    for cand in (
        pf86 / "Microsoft/Edge/Application/msedge.exe",
        pf / "Microsoft/Edge/Application/msedge.exe",
        local / "Microsoft/Edge/Application/msedge.exe",
        pf / "Google/Chrome/Application/chrome.exe",
        pf86 / "Google/Chrome/Application/chrome.exe",
        local / "Google/Chrome/Application/chrome.exe",
    ):
        if cand.exists():
            return str(cand)
    return None


def open_app_window(browser: str, url: str) -> subprocess.Popen:
    profile = browser_profile()
    if profile is None:
        raise RuntimeError("无法创建浏览器配置目录")
    args = [
        browser,
        "--app=" + url,
        "--user-data-dir=" + str(profile),
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,msEdgeSidebarV2",
        "--window-size=1380,920",
    ]
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def control_window(url: str, on_quit):
    """没有 Edge/Chrome 时的兜底控制窗；连 tkinter 都没有就退化成控制台守候。"""
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        _console_keepalive(url, on_quit)
        return

    root = tk.Tk()
    root.title(APP_NAME)
    root.geometry("420x190")
    root.resizable(False, False)
    try:
        root.iconbitmap(default=str(paths.resource("web", "icon.ico")))
    except Exception:
        pass

    ttk.Label(root, text="%s %s" % (APP_NAME, APP_VERSION),
              font=("Microsoft YaHei UI", 13, "bold")).pack(pady=(22, 4))
    ttk.Label(root, text=APP_TAGLINE + " · 服务运行中",
              foreground="#555").pack()
    ttk.Label(root, text=url, foreground="#2b59d4").pack(pady=(6, 12))

    bar = ttk.Frame(root)
    bar.pack()
    ttk.Button(bar, text="打开界面", width=12,
               command=lambda: webbrowser.open(url)).pack(side="left", padx=6)
    ttk.Button(bar, text="退出", width=10,
               command=lambda: (root.destroy(), on_quit())).pack(side="left", padx=6)

    root.protocol("WM_DELETE_WINDOW", lambda: (root.destroy(), on_quit()))
    root.mainloop()


def _console_keepalive(url: str, on_quit):
    print("%s 正在运行：%s" % (APP_NAME, url))
    print("按回车退出。")
    try:
        input()
    except Exception:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    on_quit()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="%s %s 桌面窗口启动器" % (APP_NAME, APP_VERSION))
    ap.add_argument("--port", type=int, default=5731)
    ap.add_argument("--browser", default=None, help="指定浏览器可执行文件")
    ap.add_argument("--selftest", action="store_true", help="只验证服务与浏览器探测，不开窗")
    args = ap.parse_args(argv)

    # 已经开着一个就只把窗口叫出来
    for p in range(args.port, args.port + 20):
        info = probe("http://127.0.0.1:%d" % p, timeout=0.4)
        if info:
            url = "http://127.0.0.1:%d" % p
            log("已有实例：%s" % url)
            if args.selftest:
                print("已运行实例：", info)
                return 0
            browser = args.browser or find_browser()
            if browser:
                try:
                    open_app_window(browser, url).wait()
                except KeyboardInterrupt:
                    pass
            else:
                webbrowser.open(url)
                control_window(url, lambda: None)
            return 0

    port = pick_port(args.port)
    url = "http://127.0.0.1:%d" % port
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    log("服务启动：%s" % url)

    browser = args.browser or find_browser()

    if args.selftest:
        info = probe(url)
        with urllib.request.urlopen(url + "/", timeout=5) as r:
            html = r.read().decode("utf-8", "ignore")
        ok = bool(info) and APP_NAME in html
        print("Python：", sys.version.split()[0], "|", sys.executable)
        print("服务：", url)
        print("health：", info)
        print("首页含 ReSTAgent：", APP_NAME in html)
        print("浏览器：", browser or "（未找到 Edge/Chrome，将退回默认浏览器 + 控制窗）")
        print("数据目录：", DATA_DIR)
        print("自检结果：", "通过" if ok else "未通过")
        httpd.shutdown()
        httpd.server_close()
        return 0 if ok else 1

    try:
        if browser:
            hits0 = HITS["n"]
            proc = open_app_window(browser, url)
            log("已开窗口：%s" % browser)
            # 窗口进程秒退且服务端一次首页请求都没收到，说明窗口没起来
            for _ in range(30):
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
            if proc.poll() is not None and HITS["n"] == hits0:
                log("窗口未启动（进程已退出且无请求），退回默认浏览器")
                webbrowser.open(url)
                control_window(url, lambda: None)
            else:
                proc.wait()
                log("窗口已关闭")
        else:
            log("未找到 Edge/Chrome，退回默认浏览器")
            webbrowser.open(url)
            control_window(url, lambda: None)
    except KeyboardInterrupt:
        pass
    except Exception as e:  # noqa: BLE001
        log("启动窗口失败：%r" % (e,))
        webbrowser.open(url)
        try:
            control_window(url, lambda: None)
        except Exception as e2:  # noqa: BLE001
            log("兜底窗口也失败：%r" % (e2,))
            _console_keepalive(url, lambda: None)
    finally:
        httpd.shutdown()
        httpd.server_close()
        log("服务停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
