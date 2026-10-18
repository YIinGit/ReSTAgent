# 打包成桌面应用

ReSTAgent 前端是一套静态页面 + 一个本地服务，因此有三种落地形态，按需要选一种即可。

---

## 方式一：独立窗口（现在就可用，零依赖）

双击仓库根目录的 **`ReSTAgent.bat`**（或先运行一次 `make_shortcut.bat` 在桌面建快捷方式，快捷方式直接指向 `pythonw.exe -m app.desktop`，不会闪控制台）。

它做的事：

1. 先实测本机可用的 Python（`py -3.12` … `python` … 逐个试），把选中的解释器、端口、浏览器探测结果打印出来；
2. 在当前进程里起本地服务（默认 5731，占用则自动往后退）；
3. 找到 Edge / Chrome，用 `--app` 模式打开一个**没有地址栏、没有标签页**的独立窗口；
4. 使用 `%LOCALAPPDATA%\ReSTAgent\browser` 作为专用浏览器配置，和日常浏览器互不干扰；
5. 关掉窗口，服务自动退出；再次双击会复用已在运行的实例，不会重复起服务。

找不到 Chromium 时退回默认浏览器 + 一个 tkinter 控制窗（打开/退出）。自检：

```bat
python -m app.desktop --selftest
ReSTAgent.bat            :: 走完整启动流程（自检 → 开窗）
```

> `ReSTAgent.pyw` 仍然保留，但它依赖 Windows 的 `.pyw` 关联；若 `py` 登记的 Python 已被卸载，双击会报 `Unable to create process`，此时请用 `ReSTAgent.bat`。

日志：`desktop.log`（源码运行时在仓库根目录，打包后在 `%LOCALAPPDATA%\ReSTAgent\`）。

---

## 方式二：单文件 exe（PyInstaller）

```bat
pip install pyinstaller
packaging\build_exe.bat
```

或者手动：

```bat
pyinstaller packaging\ReSTAgent.spec --noconfirm --clean
```

产物 `dist\ReSTAgent.exe`，双击即启动窗口，无控制台。

- 配置与数据写到 `%LOCALAPPDATA%\ReSTAgent\`（`config.json`、`data\uploads|exports|sessions`），升级 exe 不会丢配置。
- `web/`、`app/rules/`、`samples/` 作为资源打进包里（见 spec 的 `datas`），修改规则后需重新打包。
- 图标：`web/icon.ico`（由 `tools/make_icon.py` 生成）。
- 体积参考：约 12–16 MB（含 tkinter 兜底窗口）。若想去掉兜底窗口，可在 spec 的 `excludes` 里加 `"tkinter"`，体积约减 8 MB。

---

## 方式三：真原生壳（pywebview，可选）

想要系统原生 WebView2 窗口（无浏览器痕迹、可自定义标题栏）：

```bat
pip install pywebview
```

```python
# app/native.py（示意）
import threading
import webview
from http.server import ThreadingHTTPServer
from app.server import Handler, pick_port

port = pick_port(5731)
httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
httpd.daemon_threads = True
threading.Thread(target=httpd.serve_forever, daemon=True).start()

webview.create_window("ReSTAgent", "http://127.0.0.1:%d" % port, width=1380, height=920)
webview.start()
httpd.shutdown()
```

体积比重（WebView2 运行时约 100 MB+），但外观最接近原生桌面应用。

---

## 版本与命名

| 项 | 值 |
|---|---|
| 产品名 / 窗口标题 | `ReSTAgent` |
| exe | `ReSTAgent.exe` |
| 安装包 | `ReSTAgent-Setup-x.y.exe` |
| 用户数据目录 | `%LOCALAPPDATA%\ReSTAgent\` |
| 名称与版本号源 | `app/__init__.py` 的 `APP_NAME` / `APP_VERSION` |

改一处即可，服务、健康接口、界面、窗口标题都会跟随。
