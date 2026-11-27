# -*- coding: utf-8 -*-
"""把 index.html 改造成 v0.4 布局（设置页两栏、结果页稿件+安全区、顶栏「最近」）。

做法：按结构切分替换，不整文件重写——抽屉/设置面板等大块原样保留，
只改 <main> 内部与顶栏，减少样式断层风险。
"""
from __future__ import annotations

import pathlib
import re

HTML = pathlib.Path(__file__).resolve().parent.parent / "web" / "index.html"


def replace_block(s: str, start_mark: str, end_mark: str, new: str) -> str:
    i = s.index(start_mark)
    j = s.index(end_mark, i) + len(end_mark)
    return s[:i] + new + s[j:]


def main() -> int:
    s = HTML.read_text(encoding="utf-8")

    # ---------------------------------------------------------- 1) 顶栏
    topbar = '''<header class="topbar">
  <div class="brand">
    <div class="logo">R</div>
    <div>
      <h1>ReSTAgent <span id="appVersion" class="ver">v0.3</span></h1>
      <p class="tagline">本机运行 · 数据不外传</p>
    </div>
  </div>
  <div class="topbar-right">
    <span id="apiState" class="pill pill-muted">检测中…</span>
    <div class="recent">
      <button id="btnRecent" class="btn btn-ghost" title="最近审过的稿子">最近</button>
      <div id="recentPop" class="recent-pop is-hidden"></div>
    </div>
    <button id="btnRules" class="btn btn-ghost">规则库</button>
    <button id="btnAdmin" class="btn btn-ghost">管理员</button>
    <button id="btnSettings" class="btn btn-ghost">设置</button>
    <button id="btnTheme" class="btn btn-ghost" title="切换浅色 / 深色">浅色</button>
  </div>
</header>'''
    s = replace_block(s, '<header class="topbar">', '</header>', topbar)

    # ---------------------------------------------------------- 2) 设置页
    setup = '''  <div class="main-col">
      <section id="viewSetup" class="view">
        <div class="setup-grid">
          <!-- 左：放入稿件 -->
          <div class="setup-col setup-drop">
            <h2 class="step"><span class="step-no">01</span>放入稿件
              <span class="step-sub">不改原文件 · 结果另存</span>
            </h2>
            <label id="drop" class="drop">
              <input id="fileInput" type="file" accept=".docx" multiple hidden />
              <div class="drop-icon">＋</div>
              <div class="drop-main">拖入 Word 文件，或点击选择</div>
              <div class="drop-sub">可一次选多份 .docx</div>
            </label>
            <div class="demo-row">
              <button id="btnDemo" class="btn btn-mini">用示例稿试一次</button>
            </div>

            <!-- 三审：IDML + PDF -->
            <div id="thirdBox" class="third-box is-hidden">
              <div class="third-grid">
                <label class="third-file">
                  <span class="third-tag">① IDML · 结构</span>
                  <input id="idmlInput" type="file" accept=".idml" hidden />
                  <div id="idmlName" class="third-name">点击选择 .idml</div>
                  <span class="third-sub">页面 / 网格 / 文本框</span>
                </label>
                <label class="third-file">
                  <span class="third-tag">② PDF · 成品</span>
                  <input id="pdfInput" type="file" accept=".pdf" hidden />
                  <div id="pdfName" class="third-name">点击选择 .pdf</div>
                  <span class="third-sub">字体 / 字号 / 位置</span>
                </label>
              </div>
              <label class="check third-check">
                <input id="thirdVision" type="checkbox" checked />
                <span>先用视觉模型判定文字角色（2 页拼一张，约 1 分钟）</span>
              </label>
              <p class="hint">InDesign：文件 → 导出 → InDesign 标记语言(.idml)，再导出 PDF（勾选出血）</p>
            </div>
            <div id="fileList" class="file-list"></div>
          </div>

          <!-- 右：怎么审 -->
          <div class="setup-col setup-params">
            <h2 class="step"><span class="step-no">02</span>怎么审</h2>
            <div class="segmented" id="stageList" role="tablist">
              <button class="stage" data-stage="zero">零审</button>
              <button class="stage" data-stage="first">一审</button>
              <button class="stage is-active" data-stage="second">二审</button>
              <button class="stage" data-stage="third">三审</button>
              <a class="stage stage-link" href="/campaign" title="依据《2026活动部教案》初审活动策划案">策审</a>
            </div>
            <p class="stage-detail" id="stageDetail">文字与语法：病句、错别字、用词、标点、中西混排</p>

            <div class="levels" id="levelList" role="radiogroup">
              <button class="level" data-level="light">轻</button>
              <button class="level is-active" data-level="standard">标准</button>
              <button class="level" data-level="strict">严格</button>
              <button class="level" data-level="max">极致</button>
            </div>

            <dl class="estimate-card" id="estimateCard">
              <dt>预计调用</dt><dd id="estCalls">—</dd>
              <dt>输入 tokens</dt><dd id="estIn">—</dd>
              <dt>输出上限</dt><dd id="estOut">—</dd>
              <dt>预计费用</dt><dd id="estCost" class="cost">—</dd>
            </dl>
          </div>
        </div>

        <div class="startbar">
          <button id="btnStart" class="btn-start" disabled>开始审稿 →</button>
          <p class="hint" id="startInfo">请先放入稿件</p>
        </div>
      </section>

      <!-- ============ 审稿中 ============ -->
      <section id="viewRunning" class="view is-hidden">
        <div class="panel running">
          <div class="running-head">
            <div>
              <h2 id="runTitle">正在审稿</h2>
              <p class="hint" id="runMsg">准备中…</p>
            </div>
            <div class="running-right">
              <span id="runPct" class="run-pct">0%</span>
              <button id="btnCancel" class="btn btn-ghost">停止</button>
            </div>
          </div>
          <div class="progress"><div id="progressFill" class="progress-fill"></div></div>
          <div class="run-files" id="runFiles"></div>
          <div id="runUsage" class="run-usage hint"></div>
          <div class="logbox" id="logBox"></div>
        </div>
      </section>

      <!-- ============ 结果 ============ -->
      <section id="viewResults" class="view is-hidden">
        <div class="resultbar">
          <div class="resultbar-left">
            <button id="btnNew" class="btn btn-ghost">← 新建</button>
            <button id="btnToggleSide" class="btn btn-ghost" title="显示 / 隐藏意见清单">≡ 清单</button>
            <div class="tabs" id="fileTabs"></div>
          </div>
          <div class="resultbar-right">
            <span class="counts" id="counts"></span>
            <span class="usage" id="usageInfo"></span>
            <button id="btnExportAll" class="btn btn-ghost">全部导出</button>
            <button id="btnExport" class="btn btn-primary">导出 Word</button>
          </div>
        </div>
        <div id="exportBanner" class="banner is-hidden"></div>

        <!-- 三审结果 -->
        <div id="thirdResults" class="third-results is-hidden">
          <div class="third-toolbar">
            <div id="thirdCounts" class="counts"></div>
            <div class="third-toolbar-right">
              <label class="third-hl"><input id="thirdHl" type="checkbox" checked /> 显示红框</label>
              <div class="third-zoom">
                <button class="btn btn-mini" id="thirdZoomOut" title="缩小">−</button>
                <span id="thirdZoomLabel" class="third-zoom-label">适宽</span>
                <button class="btn btn-mini" id="thirdZoomIn" title="放大">＋</button>
                <button class="btn btn-mini" id="thirdFit" title="适合宽度">适宽</button>
              </div>
              <span id="thirdVisionInfo" class="hint"></span>
              <a id="btnThirdReport" class="btn btn-primary" href="#" download>导出报告</a>
            </div>
          </div>
          <div class="third-split">
            <aside class="third-list" id="thirdList"></aside>
            <section class="third-view">
              <div class="third-nav" id="thirdNav"></div>
              <div class="third-page-wrap" id="thirdPageWrap"></div>
            </section>
          </div>
        </div>

        <!-- 文稿结果：正文 + 详情 + 安全区 -->
        <div id="docResults">
          <div class="workbench">
            <section class="document" id="docBody">
              <p class="doc-empty">选择右侧一条意见，正文会自动定位到对应位置。</p>
            </section>
            <section class="detail" id="detail">
              <div class="empty">选择一条意见查看详情</div>
            </section>
          </div>
          <div class="rail" id="rail">
            <div class="rail-dots" id="railDots"></div>
            <span class="rail-count" id="railCount">—</span>
          </div>
        </div>

        <!-- 意见清单：改为可滑出的浮层 -->
        <aside class="side" id="sideList">
          <div class="filters">
            <select id="filterSev" class="select">
              <option value="all">全部级别</option>
              <option value="must">仅必改</option>
              <option value="should">建议</option>
              <option value="suggest">可选</option>
            </select>
            <select id="filterStatus" class="select">
              <option value="all">全部状态</option>
              <option value="pending">待处理</option>
              <option value="accepted">已接受</option>
              <option value="rejected">已忽略</option>
            </select>
          </div>
          <div class="bulk">
            <button id="btnAcceptMust" class="btn btn-mini">接受全部必改</button>
            <button id="btnRejectSuggest" class="btn btn-mini">忽略全部可选</button>
          </div>
          <div id="findingList" class="finding-list"></div>
        </aside>
      </section>
    </div>'''

    old_start = '  <div class="main-col">'
    old_end = '''        </div>
      </section>
    </div>'''
    # 用「viewResults 结束」定位：main-col 到 </main> 之前
    main_i = s.index('<main>')
    main_j = s.index('</main>')
    body = s[main_i:main_j]
    # 保留 drawer 等不在 main 内的部分，整体替换 main 内容
    s = s[:main_i] + '<main>\n' + setup + '\n' + s[main_j:]

    # 3) 引入 layout.css
    if 'layout.css' not in s:
        s = s.replace('<link rel="stylesheet" href="/static/style.css" />',
                      '<link rel="stylesheet" href="/static/style.css" />\n'
                      '<link rel="stylesheet" href="/static/layout.css" />')

    # 4) 去掉旧的历史侧栏（已收进顶栏）
    s = re.sub(r'    <!-- ============ 历史 ============ -->.*?</aside>\n', '', s, flags=re.S)

    HTML.write_text(s, encoding="utf-8")
    print("index.html 已改造：", len(s), "字节")
    for need in ('setup-grid', 'segmented', 'estimate-card', 'btn-start', 'docBody', 'railDots', 'sideList', 'recentPop'):
        print("  %-14s %s" % (need, need in s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
