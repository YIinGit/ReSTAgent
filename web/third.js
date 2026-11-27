/* 三审（IDML + PDF 版式检查）前端：上传 → 进度 → 问题清单 + 页面红框 → 导出报告
 *
 * 独立成文件，复用 app.js 里的全局：state / $ / $$ / esc / api / toast / showView / stopPolling。
 */
(function () {
  const LEVEL_TEXT = { major: "必改", minor: "建议改", info: "待核实" };

  function T() {
    if (!state.third) state.third = { idml: null, pdf: null, job: null, selPage: 1, selFinding: null };
    return state.third;
  }
  function isThird() { return state.stage === "third"; }
  function el(id) { return document.getElementById(id); }

  /* ---------------- 设置页：文件选择 ---------------- */
  function renderThirdSetup() {
    const t = T();
    const on = isThird();
    const box = el("thirdBox");
    const drop = el("drop");
    const demo = document.querySelector(".demo-row");
    if (box) box.classList.toggle("is-hidden", !on);
    if (drop) drop.classList.toggle("is-hidden", on);
    if (demo) demo.classList.toggle("is-hidden", on);
    if (!on) return;

    const n1 = el("idmlName"), n2 = el("pdfName");
    if (n1) {
      n1.textContent = t.idml ? t.idml.name : "点击选择 .idml";
      n1.classList.toggle("has-file", !!t.idml);
    }
    if (n2) {
      n2.textContent = t.pdf ? t.pdf.name : "点击选择 .pdf";
      n2.classList.toggle("has-file", !!t.pdf);
    }
    const picked = [t.idml, t.pdf].filter(Boolean);
    const info = el("startInfo");
    if (info) {
      info.textContent = picked.length
        ? "三审：" + picked.map((f) => f.name).join(" + ")
        : "先选择 IDML / PDF（至少一个）";
    }
    const start = el("btnStart");
    if (start) start.disabled = !picked.length;
    const est = el("estimate");
    if (est) est.textContent = "三审按页判定：每 2 页 1 次视觉调用；关掉视觉则纯本地规则、不花钱";
  }

  /* ---------------- 启动 ---------------- */
  async function startThird() {
    const t = T();
    if (!t.idml && !t.pdf) return;
    const fd = new FormData();
    if (t.idml) fd.append("idml", t.idml, t.idml.name);
    if (t.pdf) fd.append("pdf", t.pdf, t.pdf.name);
    const vision = (el("thirdVision") && el("thirdVision").checked) ? 1 : 0;
    const start = el("btnStart");
    if (start) start.disabled = true;
    try {
      toast("正在上传并开始三审…");
      const tk = (function () {
        try { return sessionStorage.getItem("ra_token") || new URLSearchParams(location.search).get("t") || ""; }
        catch (e) { return ""; }
      })();
      const res = await fetch("/api/third?vision=" + vision + "&per_sheet=2",
        { method: "POST", body: fd, headers: tk ? { "X-Auth-Token": tk } : {} });
      let job = {};
      try { job = await res.json(); } catch (e) { job = { error: "服务返回异常" }; }
      if (!res.ok) throw new Error(job.error || ("HTTP " + res.status));
      t.job = job;
      state.jobId = job.id;
      showView("Running");
      renderRunningThird();
      startThirdPolling();
    } catch (e) {
      toast("启动失败：" + e.message, true);
      if (start) start.disabled = false;
    }
  }

  function startThirdPolling() {
    stopPolling();
    state.poll = setInterval(async () => {
      if (!state.jobId) return;
      try {
        const j = await api("/api/third/" + state.jobId);
        T().job = j;
        if (j.status === "running" || j.status === "pending") { renderRunningThird(); return; }
        stopPolling();
        renderRunningThird();
        if (j.status === "done") {
          setTimeout(enterThirdResults, 300);
        } else {
          toast("三审失败：" + (j.error || "未知错误"), true);
          const start = el("btnStart");
          if (start) start.disabled = false;
          showView("Setup");
        }
      } catch (e) {
        stopPolling();
        toast("获取进度失败：" + e.message, true);
      }
    }, 1200);
  }

  /* ---------------- 运行中 ---------------- */
  function renderRunningThird() {
    const j = T().job || {};
    const title = el("runTitle"), msg = el("runMsg");
    if (title) title.textContent = "三审 · 版式终审";
    if (msg) msg.textContent = j.status === "error" ? (j.error || "") : "按页面判定文字角色，再对照排版标准";
    const pct = Number(j.pct || 0);
    const fill = el("progressFill"), pctEl = el("runPct");
    if (fill) fill.style.width = pct + "%";
    if (pctEl) pctEl.textContent = pct + "%";
    const f = j.file || {};
    const chips = [f.idml, f.pdf].filter(Boolean)
      .map((n) => '<span class="chip running">' + esc(n) + "</span>");
    chips.push('<span class="chip">视觉判定：' + (j.vision ? "开" : "关") + "</span>");
    const files = el("runFiles");
    if (files) files.innerHTML = chips.join("");
    const box = el("logBox");
    if (!box) return;
    const logs = (j.log || []).slice(-80);
    const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
    box.innerHTML = logs.map((l) =>
      '<div class="' + (l.level === "error" ? "error" : "") + '"><span class="t">' +
      esc(l.t || "") + "</span>" + esc(l.msg) + "</div>").join("");
    if (atBottom) box.scrollTop = box.scrollHeight;
  }

  /* ---------------- 结果 ---------------- */
  function enterThirdResults() {
    const j = T().job || {};
    const r = j.result;
    if (!r) {
      toast("没有拿到三审结果", true);
      showView("Setup");
      return;
    }
    const wb = document.querySelector(".workbench");
    if (wb) wb.classList.add("is-hidden");
    const box = el("thirdResults");
    if (box) box.classList.remove("is-hidden");
    const banner = el("exportBanner");
    if (banner) banner.classList.add("is-hidden");
    ["btnExport", "btnExportAll"].forEach((id) => { const e = el(id); if (e) e.classList.add("is-hidden"); });

    const counts = r.counts || {};
    const cEl = el("counts"), uEl = el("usageInfo");
    if (cEl) cEl.textContent = "";
    if (uEl) uEl.textContent = "";
    const tc = el("thirdCounts");
    if (tc) {
      tc.textContent = "共 " + (counts.total || 0) + " 条 · 必改 " + (counts.major || 0) +
        " · 建议改 " + (counts.minor || 0) + " · 待核实 " + (counts.info || 0);
    }
    const v = r.vision || {};
    const vi = el("thirdVisionInfo");
    if (vi) {
      vi.textContent = v.vision
        ? "视觉判定：" + (v.ok_pages || 0) + " 页 / " + (v.sheets || 0) + " 张拼版"
        : "未用视觉：按字号与字体族名启发式";
    }
    const rep = el("btnThirdReport");
    if (rep) rep.href = "/api/third/" + j.id + "/report";

    const f = j.file || {};
    const tabs = el("fileTabs");
    if (tabs) tabs.innerHTML = '<span class="tab is-active">' + esc(f.pdf || f.idml || "三审") + "</span>";

    T().selFinding = null;
    const pages = r.pages || [];
    // 先显示结果页再渲染页面图：容器隐藏时 clientWidth=0，算不出「适宽」
    showView("Results");
    renderThirdList();
    renderThirdPage(pages.length ? pages[0].n : 1);
    toast("三审完成");
  }

  function sortedFindings() {
    const r = (T().job || {}).result || {};
    const order = { major: 0, minor: 1, info: 2 };
    return (r.findings || []).slice().sort((a, b) =>
      (order[a.level] ?? 9) - (order[b.level] ?? 9) || (a.page || 0) - (b.page || 0));
  }

  function renderThirdList() {
    const list = el("thirdList");
    if (!list) return;
    const sorted = sortedFindings();
    list._sorted = sorted;
    if (!sorted.length) {
      list.innerHTML = '<div class="third-empty">没有发现问题：版面网格、字体字号、越界都符合标准。</div>';
      return;
    }
    const sel = T().selFinding;
    let html = "";
    let last = null;
    sorted.forEach((f, i) => {
      if (f.level !== last) {
        html += '<div class="third-group">' + (LEVEL_TEXT[f.level] || f.level) + "</div>";
        last = f.level;
      }
      const active = sel && sel.message === f.message && sel.page === f.page;
      html += '<div class="tcard' + (active ? " is-active" : "") + '" data-tf="' + i + '">' +
        '<div class="tcard-top"><span class="tcard-badge ' + esc(f.level) + '">' +
        (LEVEL_TEXT[f.level] || f.level) + "</span><span>" + esc(f.category || "") +
        '</span><span class="tcard-page">' + (f.page > 0 ? "第 " + f.page + " 页" : "全文") + "</span></div>" +
        '<div class="tcard-msg">' + esc(f.message || "") + "</div>" +
        (f.suggestion ? '<div class="tcard-std">建议：' + esc(f.suggestion) + "</div>" : "") +
        "</div>";
    });
    list.innerHTML = html;
  }

  function renderThirdPage(n) {
    const j = T().job || {};
    const r = j.result || {};
    const pages = r.pages || [];
    const wrap = el("thirdPageWrap"), nav = el("thirdNav");
    if (!wrap || !nav) return;
    if (!pages.length) {
      wrap.innerHTML = '<div class="third-empty">只提供了 IDML，没有 PDF 页面图。可以导出报告查看全部问题。</div>';
      nav.innerHTML = "";
      return;
    }
    const pg = pages.find((p) => p.n === n) || pages[0];
    T().selPage = pg.n;
    const byPage = r.by_page || {};
    const boxes = byPage[pg.n] || byPage[String(pg.n)] || [];
    const W = pg.width || 1, H = pg.height || 1;
    const sel = T().selFinding;
    const hl = T().hl !== false;

    // 原像素尺寸（后端按 scale 渲染，默认 2.2 倍 ≈ 158dpi），不再让浏览器压缩到容器宽度
    const pageScale = Number(r.scale || 1) || 1;
    const pxW = Number(pg.px_width) || Math.round(W * pageScale);
    const avail = Math.max(240, (wrap.clientWidth || 900) - 32);
    const fitZoom = Math.min(1, avail / Math.max(1, pxW));
    const zoom = T().fit === false ? Number(T().zoom || 1) : fitZoom;
    T().lastZoom = zoom;
    const dispW = Math.max(160, Math.round(pxW * zoom));

    const marks = !hl ? "" : boxes.map((b) => {
      const box = b.box || [0, 0, 0, 0];
      const left = (box[0] / W) * 100;
      const top = (box[1] / H) * 100;
      const w = Math.max(0.5, ((box[2] - box[0]) / W) * 100);
      const h = Math.max(0.5, ((box[3] - box[1]) / H) * 100);
      const active = sel && sel.message === b.message;
      return '<div class="pbox ' + esc(b.level) + (active ? " is-active" : "") + '" style="left:' +
        left.toFixed(2) + "%;top:" + top.toFixed(2) + "%;width:" + w.toFixed(2) + "%;height:" +
        h.toFixed(2) + '%" title="' + esc(b.message || "") + '"></div>';
    }).join("");
    wrap.innerHTML = '<div class="page-inner" style="width:' + dispW + 'px"><img src="/api/third/' +
      j.id + "/page/" + pg.n + '" alt="第 ' + pg.n + ' 页" style="width:' + dispW +
      'px;height:auto" />' + marks + "</div>";

    const label = el("thirdZoomLabel");
    if (label) label.textContent = (T().fit === false ? "" : "适宽 ") + Math.round(zoom * 100) + "%";

    nav.innerHTML = pages.map((p) => {
      const has = ((byPage[p.n] || byPage[String(p.n)] || []).length > 0);
      return '<button class="pnav' + (p.n === pg.n ? " is-active" : "") + (has ? " has-issue" : "") +
        '" data-page="' + p.n + '">' + p.n + "</button>";
    }).join("");

    // 选中的框滚到视野中间，解决「点了找不到高亮在哪」
    if (hl && sel) {
      const act = wrap.querySelector(".pbox.is-active");
      if (act) setTimeout(() => {
        try { act.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" }); } catch (e) { }
      }, 60);
    }
  }

  function zoomBy(factor) {
    const t = T();
    const cur = Number(t.lastZoom || 1);
    t.fit = false;
    t.zoom = Math.max(0.2, Math.min(4, cur * factor));
    renderThirdPage(t.selPage || 1);
  }

  /* ---------------- 复位 ---------------- */
  function resetThird() {
    const t = T();
    t.idml = t.pdf = t.job = null;
    t.selFinding = null;
    const i1 = el("idmlInput"), i2 = el("pdfInput");
    if (i1) i1.value = "";
    if (i2) i2.value = "";
    const wb = document.querySelector(".workbench");
    if (wb) wb.classList.remove("is-hidden");
    ["btnExport", "btnExportAll"].forEach((id) => { const e = el(id); if (e) e.classList.remove("is-hidden"); });
    const box = el("thirdResults");
    if (box) box.classList.add("is-hidden");
    renderThirdSetup();
  }

  /* ---------------- 事件 ---------------- */
  function boot() {
    const i1 = el("idmlInput"), i2 = el("pdfInput");
    if (i1) i1.addEventListener("change", (e) => { T().idml = e.target.files[0] || null; renderThirdSetup(); });
    if (i2) i2.addEventListener("change", (e) => { T().pdf = e.target.files[0] || null; renderThirdSetup(); });

    // 环节切换：app.js 的监听先执行，这里补三审的显隐
    const stages = el("stageList");
    if (stages) stages.addEventListener("click", () => setTimeout(renderThirdSetup, 0));

    // 开始 / 停止按钮：三审时在 document 捕获阶段拦截，避免 app.js 的 Word 流程抢先执行
    document.addEventListener("click", (e) => {
      const t = e.target;
      if (!t || !t.closest) return;
      if (t.closest("#btnStart") && isThird()) {
        e.stopPropagation();
        e.preventDefault();
        startThird();
        return;
      }
      if (t.closest("#btnCancel") && isThird()) {
        e.stopPropagation();
        e.preventDefault();
        toast("三审暂不支持中途停止（视觉判定请求已发出）");
      }
    }, true);
    const nw = el("btnNew");
    if (nw) nw.addEventListener("click", () => setTimeout(resetThird, 0));

    const list = el("thirdList");
    if (list) list.addEventListener("click", (e) => {
      const card = e.target.closest("[data-tf]");
      if (!card) return;
      const f = (list._sorted || [])[Number(card.dataset.tf)];
      if (!f) return;
      T().selFinding = f;
      renderThirdList();
      renderThirdPage(f.page > 0 ? f.page : (T().selPage || 1));
    });

    // 高亮开关 & 缩放
    const hlBox = el("thirdHl");
    if (hlBox) hlBox.addEventListener("change", () => {
      T().hl = hlBox.checked;
      renderThirdPage(T().selPage || 1);
    });
    const zin = el("thirdZoomIn"), zout = el("thirdZoomOut"), zfit = el("thirdFit");
    if (zin) zin.addEventListener("click", () => zoomBy(1.25));
    if (zout) zout.addEventListener("click", () => zoomBy(0.8));
    if (zfit) zfit.addEventListener("click", () => {
      T().fit = true;
      renderThirdPage(T().selPage || 1);
    });
    window.addEventListener("resize", () => {
      if (T().job && (T().job.result || {}).pages && !document.getElementById("thirdResults").classList.contains("is-hidden")) {
        renderThirdPage(T().selPage || 1);
      }
    });
    const nav = el("thirdNav");
    if (nav) nav.addEventListener("click", (e) => {
      const b = e.target.closest("[data-page]");
      if (!b) return;
      renderThirdPage(Number(b.dataset.page));
    });

    renderThirdSetup();

    // 深链：?stage=third 直接进入三审；?third=<任务ID> 直接打开已有三审结果
    try {
      const q = new URLSearchParams(location.search);
      if (q.get("third")) {
        state.stage = "third";
        api("/api/third/" + q.get("third")).then((j) => {
          T().job = j;
          state.jobId = j.id;
          if (j.result) enterThirdResults();
          else { showView("Running"); renderRunningThird(); startThirdPolling(); }
        }).catch((e) => toast("打开三审记录失败：" + e.message, true));
      } else if (q.get("stage") === "third") {
        state.stage = "third";
        document.querySelectorAll(".stage").forEach((x) =>
          x.classList.toggle("is-active", x.dataset.stage === "third"));
        renderThirdSetup();
      }
    } catch (e) { /* 忽略 */ }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
