# -*- coding: utf-8 -*-
"""app.js 适配 v0.4 布局。

改动集中在四块，尽量不碰原有业务逻辑：
  1. 安全令牌：api() 带 token；启动失败不中断绑定
  2. 设置页：分段控件 + 预估卡 + 大主按钮
  3. 结果页：稿件纸白预览 + 安全区圆点 + 胶片条定位
  4. 键盘：isComposing 判断、j/k 跳转、去掉 ta.focus() 抢焦点
"""
from __future__ import annotations

import pathlib
import re

JS = pathlib.Path(__file__).resolve().parent.parent / "web" / "app.js"


def sub_once(s: str, old: str, new: str, tag: str) -> str:
    if old not in s:
        print("  [跳过] 未找到：%s" % tag)
        return s
    print("  [替换] %s" % tag)
    return s.replace(old, new, 1)


def main() -> int:
    s = JS.read_text(encoding="utf-8")

    # ---------------------------------------------------------- 1) 预估卡
    s = sub_once(s,
        '''    $("#estimate").textContent =
      `预计 ${r.calls} 轮调用 · 输入约 ${(r.in_tokens / 1000).toFixed(1)}k tokens · ` +
      `输出上限约 ${(r.out_tokens / 1000).toFixed(1)}k · 费用约 ¥${Number(r.cost || 0).toFixed(4)}`;''',
        '''    setText("#estCalls", `${r.calls} 次`);
    setText("#estIn", `${(r.in_tokens / 1000).toFixed(1)}k tokens`);
    setText("#estOut", `${(r.out_tokens / 1000).toFixed(1)}k tokens`);
    setText("#estCost", `¥${Number(r.cost || 0).toFixed(2)}`);''',
        "预估卡")

    s = sub_once(s,
        '''  if (!chars) { $("#estimate").textContent = "选择稿件后显示预估"; return; }''',
        '''  if (!chars) {
    ["#estCalls", "#estIn", "#estOut", "#estCost"].forEach((k) => setText(k, "—"));
    return;
  }''',
        "预估卡清空")

    # ---------------------------------------------------------- 2) 环节说明
    s = sub_once(s,
        '''    state.stage = b.dataset.stage;
    $$(".stage").forEach((x) => x.classList.toggle("is-active", x === b));
    updateEstimate();''',
        '''    state.stage = b.dataset.stage;
    $$(".stage").forEach((x) => x.classList.toggle("is-active", x === b));
    const desc = document.querySelector(`.stage[data-stage="${state.stage}"] .stage-desc`);
    state.stageDesc = STAGE_DESC[state.stage] || "";
    setText("#stageDetail", state.stageDesc || STAGE_DESC.second);
    updateEstimate();''',
        "环节说明")

    # ---------------------------------------------------------- 3) 启动容错
    s = sub_once(s,
        '''async function loadConfig() {
  try {
    state.cfg = await api("/api/config");
    renderApiState();
    fillSettings();
  } catch (e) {
    toast("读取配置失败：" + e.message, true);
  }
  await loadHealth();
}''',
        '''async function loadConfig() {
  try {
    state.cfg = await api("/api/config");
    renderApiState();
    fillSettings();
  } catch (e) {
    // 401 多半是没带访问令牌：不要中断后续初始化，给出可操作提示
    if (/401|令牌/.test(e.message)) {
      toast("没有访问权限：请用桌面启动器打开，或带上 ?t=令牌", true);
    } else {
      toast("读取配置失败：" + e.message, true);
    }
  }
  try { await loadHealth(); } catch (e) { /* 健康检查失败不影响使用 */ }
}''',
        "启动容错")

    # ---------------------------------------------------------- 4) 历史下拉
    s = sub_once(s,
        '''  renderFiles();
  loadHistory();
  loadConfig().then(() => {''',
        '''  renderFiles();
  renderRail();
  loadHistory();
  bindRecent();
  loadConfig().then(() => {''',
        "初始化挂载")

    # ---------------------------------------------------------- 5) 稿件预览 + 安全区
    rail_code = '''
/* ---------------- 稿件预览与安全区 ---------------- */
function docParagraphs(fileId) {
  const f = ((state.job || {}).files || []).find((x) => x.file_id === fileId);
  return (f && f.paragraphs) || (f && f.result && f.result.paragraphs) || [];
}

function renderDoc() {
  const box = $("#docBody");
  if (!box) return;
  const r = curResult();
  if (!r) { box.innerHTML = '<p class="doc-empty">还没有结果。</p>'; return; }
  const paras = r.paragraphs || docParagraphs(state.curFile);
  if (!paras.length) {
    box.innerHTML = '<p class="doc-empty">这份稿子的原文不在结果里，可用右侧详情查看上下文。</p>';
    return;
  }
  const sel = (r.findings || []).find((x) => x.id === state.sel);
  box.innerHTML = paras.map((p, i) => {
    const t = esc(p.text || "");
    if (!sel || !sel.pid || sel.pid !== p.pid) return `<p>${t || "&nbsp;"}</p>`;
    // 命中段落：把定位区间用 mark 标出来
    const a = Math.max(0, sel.start || 0), b = Math.min(t.length, sel.end || 0);
    if (b <= a) return `<p class="is-target">${t || "&nbsp;"}</p>`;
    return `<p class="is-target">${t.slice(0, a)}<mark class="doc-mark active">${
      t.slice(a, b)}</mark>${t.slice(b)}</p>`;
  }).join("");
  const mark = box.querySelector(".doc-mark.active");
  if (mark) {
    const top = mark.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop;
    box.scrollTo({ top: Math.max(0, top - box.clientHeight / 2), behavior: "smooth" });
  }
}

function renderRail() {
  const dots = $("#railDots");
  if (!dots) return;
  const all = (curResult() || {}).findings || [];
  const done = all.filter((f) => f.status !== "pending").length;
  dots.innerHTML = all.map((f) => {
    const cls = ["rail-dot"];
    if (f.status !== "pending") cls.push("done");
    if (f.severity === "must") cls.push("must");
    if (f.id === state.sel) cls.push("active");
    return `<button class="${cls.join(" ")}" data-rail="${esc(f.id)}" title="${esc(f.problem || "")}"></button>`;
  }).join("");
  setText("#railCount", all.length ? `${done}/${all.length} 已处理` : "—");
}

function setText(sel, text) {
  const el = $(sel);
  if (el) el.textContent = text;
}
'''
    # 插到「结果渲染」函数之前
    anchor = 'function renderResults() {'
    if anchor in s:
        s = s.replace(anchor, rail_code + "\n" + anchor, 1)
        print("  [插入] 稿件预览与安全区代码")

    # renderResults 末尾接上正文与安全区
    s = sub_once(s,
        '''  renderDetail();
  renderUsage(state.job);
  $("#btnExport").disabled = false;''',
        '''  renderDetail();
  renderDoc();
  renderRail();
  renderUsage(state.job);
  $("#btnExport").disabled = false;''',
        "renderResults 挂载")

    # 选中意见时刷新正文与安全区
    s = sub_once(s,
        '''    state.sel = c.dataset.id;
    renderList(); renderDetail();''',
        '''    state.sel = c.dataset.id;
    renderList(); renderDetail(); renderDoc(); renderRail();''',
        "点击意见联动")

    # ---------------------------------------------------------- 6) 键盘修复
    s = sub_once(s,
        '''  document.addEventListener("keydown", async (e) => {
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
    if ($("#viewResults").classList.contains("is-hidden")) return;
    const f = (curResult()?.findings || []).find((x) => x.id === state.sel);
    if (!f) return;
    if (e.key === "a" || e.key === "A") { e.preventDefault(); try { await decide(f.id, "accepted"); } catch (err) { toast(err.message, true); } }
    if (e.key === "x" || e.key === "X") { e.preventDefault(); try { await decide(f.id, "rejected"); } catch (err) { toast(err.message, true); } }
  });''',
        '''  document.addEventListener("keydown", async (e) => {
    if (e.isComposing) return;                       // 中文输入法组字时不抢键
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
    if ($("#viewResults").classList.contains("is-hidden")) return;
    const list = currentFindings();
    const f = (curResult()?.findings || []).find((x) => x.id === state.sel);
    if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); return step(1); }
    if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); return step(-1); }
    if (e.key === "Escape") { closeAllDrawers(); $("#sideList")?.classList.remove("is-open"); return; }
    if (!f) return;
    if (e.key === "a" || e.key === "A" || e.key === "Enter") { e.preventDefault(); try { await decide(f.id, "accepted"); } catch (err) { toast(err.message, true); } }
    if (e.key === "x" || e.key === "X") { e.preventDefault(); try { await decide(f.id, "rejected"); } catch (err) { toast(err.message, true); } }
  });

  // 上一条 / 下一条
  window.step = function (dir) {
    const list = currentFindings();
    if (!list.length) return;
    const i = list.findIndex((x) => x.id === state.sel);
    const j = Math.max(0, Math.min(list.length - 1, (i < 0 ? 0 : i) + dir));
    state.sel = list[j].id;
    renderList(); renderDetail(); renderDoc(); renderRail();
  };

  // 安全区圆点
  $("#railDots")?.addEventListener("click", (e) => {
    const b = e.target.closest("[data-rail]");
    if (!b) return;
    state.sel = b.dataset.rail;
    renderList(); renderDetail(); renderDoc(); renderRail();
  });

  // 清单浮层开关
  $("#btnToggleSide")?.addEventListener("click", () => {
    $("#sideList")?.classList.toggle("is-open");
  });

  // 最近记录下拉
  $("#btnRecent")?.addEventListener("click", (e) => {
    e.stopPropagation();
    const pop = $("#recentPop");
    if (!pop) return;
    pop.classList.toggle("is-hidden");
    if (!pop.classList.contains("is-hidden")) renderRecent();
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".recent")) $("#recentPop")?.classList.add("is-hidden");
  });''',
        "键盘与浮层")

    # 去掉抢焦点
    s = sub_once(s, "  if (ta) ta.focus();", "  // 不抢焦点：用户按 A/X 时要能连续处理下一条", "去掉 focus")

    # ---------------------------------------------------------- 7) 历史渲染到下拉
    recent_code = '''
/* ---------------- 最近记录（顶栏下拉） ---------------- */
const STAGE_DESC = {
  zero: "录用把关：属性、立论、内容密度、难度、体例",
  first: "内容与结构：学术错误、逻辑、文风、结构标注",
  second: "文字与语法：病句、错别字、用词、标点、中西混排",
  third: "排版终审：导入 IDML + PDF，查网格、字体字号、越界与混排",
  campaign: "活动策划案初审：依据《2026活动部教案》查板块、目标、流程与风险",
};
const STAGE_LABEL_CN = { zero: "零审", first: "一审", second: "二审", third: "三审", campaign: "策审" };

function bindRecent() {
  const btn = $("#btnRecent");
  if (!btn) return;
  setText("#stageDetail", STAGE_DESC[state.stage] || STAGE_DESC.second);
}

function renderRecent() {
  const pop = $("#recentPop");
  if (!pop) return;
  const items = state.history || [];
  if (!items.length) {
    pop.innerHTML = '<div class="recent-item"><span class="recent-name">还没有记录</span></div>';
    return;
  }
  pop.innerHTML = items.slice(0, 20).map((h) => `
    <div class="recent-item" data-hist="${esc(h.id)}">
      <span class="recent-name">${esc(h.file_name || h.name || "（未命名）")}</span>
      <span class="recent-meta">${esc(STAGE_LABEL_CN[h.stage] || h.stage || "")} · ${esc((h.updated || h.created || "").slice(5, 16))}</span>
    </div>`).join("");
  pop.querySelectorAll("[data-hist]").forEach((el) => {
    el.addEventListener("click", () => {
      pop.classList.add("is-hidden");
      openHistory(el.dataset.hist);
    });
  });
}
'''
    anchor2 = "/* ---------------- 稿子 ---------------- */"
    if anchor2 in s:
        s = s.replace(anchor2, recent_code + "\n" + anchor2, 1)
    else:
        s = s.replace("/* ---------------- 选稿 ---------------- */",
                      recent_code + "\n/* ---------------- 选稿 ---------------- */", 1)
    print("  [插入] 最近记录下拉")

    # loadHistory 后刷新下拉
    s = sub_once(s,
        '''    state.history = (data.items || []);''',
        '''    state.history = (data.items || []);
    if (window.renderRecent) renderRecent();''',
        "历史刷新下拉")

    JS.write_text(s, encoding="utf-8")
    print("app.js 已更新：", len(s), "字节")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
