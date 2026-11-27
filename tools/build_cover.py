"""把 docs/proto 的封面原型接进软件：生成 web/cover.html + cover.js + 资源。

原型的 HTML/CSS/JS 保留其设计（可变字符 A、光晕跟随、套准线），只改三处：
  1. 资源路径 → /static/
  2. 两个入口 → 真实跳转（编辑部 → /app，活动部 → /campaign）
  3. 去掉原型里的 toast 演示逻辑，换成「淡出 + 跳转」的转场
"""
from __future__ import annotations

import pathlib
import re
import shutil

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROTO = ROOT / "docs" / "proto"
WEB = ROOT / "web"


def main() -> int:
    # 1) 静态资源：CSS 与品牌字标脚本原样搬过来（它们自带设计，不并入 tokens）
    shutil.copy2(PROTO / "cover.css", WEB / "cover.css")
    shutil.copy2(PROTO / "brandmark.js", WEB / "brandmark.js")

    # 2) 取原型 HTML 里 #cover 那一整块（到 </div>\n</div> 结束）
    src = (PROTO / "index.html").read_text(encoding="utf-8")
    i = src.index('<div id="cover"')
    j = src.index('</div>\n</div>', i) + len('</div>\n</div>')
    cover = src[i:j]

    # 3) 入口链接换成真实路由
    cover = cover.replace('href="#/editorial" data-go="editorial"', 'href="/app" data-go="app"')
    cover = cover.replace('href="#/campaign" data-go="campaign"', 'href="/campaign" data-go="campaign"')

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>ReSTAgent · 文稿审读工作台</title>
<script>try{{document.documentElement.dataset.theme="dark";}}catch(e){{}}</script>
<link rel="icon" href="/static/icon.png" />
<link rel="stylesheet" href="/static/cover.css" />
</head>
<body class="cover-body">

{cover}

<script src="/static/brandmark.js"></script>
<script src="/static/cover.js"></script>
</body>
</html>
'''
    (WEB / "cover.html").write_text(html, encoding="utf-8")
    print("已生成 web/cover.html:", len(html), "字节")
    print("  入口:", [m for m in re.findall(r'href="(/[^"]+)"', cover)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
