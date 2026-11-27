/* ReSTAgent 前端：三步流程 + 历史 / 规则库 / 管理员 / 深色模式 / 实时输出 */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
const SEV = { must: "必改", should: "建议", suggest: "可选" };
const ST = { pending: "待处理", accepted: "已接受", edited: "已改写", rejected: "已忽略" };
const LEVEL_LABEL = { light: "轻", standard: "标准", strict: "严格", max: "极致" };
const STAGE_LABEL = { zero: "零审", first: "一审", second: "二审", third: "三审" };
const QUALITY_LABEL = { high: "高（选用）", medium: "中（需修改并提出意见）", low: "低（不选用）" };
const LEGACY_QUALITY = { "1级": "high", "2级": "medium", "3级": "low", "特策": "high" };
const THEME_KEY = "restagent.theme";

const state = {
  cfg: null, files: [], stage: "second", level: "standard",
  job: null, jobId: null, curFile: null, sel: null,
  filterSev: "all", filterStatus: "all", poll: null, exporting: false,
  history: [], rules: null, stream: null, adminToken: null,
};

/* ---------------- 基础 ---------------- */
function authToken() {
  // 桌面模式由 desktop.py 拼 ?t=token；首页会把它换成 cookie，这里做兜底
  try {
    const t = new URLSearchParams(location.search).get("t");
    if (t) { sessionStorage.setItem("ra_token", t); return t; }
    return sessionStorage.getItem("ra_token") || "";
  } catch (e) { return ""; }
}

async function api(path, { method = "GET", body = null, headers = null } = {}) {
  const opts = { method, headers: Object.assign({}, headers || {}) };
  const tk = authToken();
  if (tk) opts.headers["X-Auth-Token"] = tk;
  if (body) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const res = await fetch(path, opts);
  let data = {};
  try { data = await res.json(); } catch (e) { data = { error: "服务返回异常" }; }
  if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
  return data;
}

function toast(msg, isErr = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast" + (isErr ? " err" : "");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add("is-hidden"), 3200);
}

function showView(name) {
  ["viewSetup", "viewRunning", "viewResults"].forEach((id) => {
    $("#" + id).classList.toggle("is-hidden", id !== "view" + name);
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ---------------- 深色模式 ---------------- */
function currentTheme() {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}
function applyTheme(theme, persist) {
  document.documentElement.dataset.theme = theme;
  if (persist) { try { localStorage.setItem(THEME_KEY, theme); } catch (e) { /* 隐私模式 */ } }
  const btn = $("#btnTheme");
  if (btn) btn.textContent = theme === "dark" ? "浅色" : "深色";
}
function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem(THEME_KEY); } catch (e) { saved = null; }
  if (!saved && window.matchMedia) {
    saved = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  applyTheme(saved || "light", false);
  $("#btnTheme").addEventListener("click", () => {
    applyTheme(currentTheme() === "dark" ? "light" : "dark", true);
  });
  if (window.matchMedia) {
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      let stored = null;
      try { stored = localStorage.getItem(THEME_KEY); } catch (e) { stored = null; }
      if (!stored) applyTheme(mq.matches ? "dark" : "light", false);
    };
    if (mq.addEventListener) mq.addEventListener("change", onChange);
  }
}

/* ---------------- 配置 / 公告 ---------------- */
async function loadConfig() {
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
}

async function loadHealth() {
  try {
    const h = await api("/api/health");
    if (h && h.version) $("#appVersion").textContent = "v" + h.version;
    if (h && h.name) document.title = h.name + " · " + (h.tagline || "文稿审读");
    renderNotice(h || {});
    renderStaff((h && h.staff) || []);
  } catch (e) { /* 版本号拿不到不影响使用 */ }
}

function renderNotice(h) {
  const bar = $("#noticeBar");
  const text = (h && h.notice) || "";
  bar.textContent = text;
  bar.classList.toggle("is-hidden", !text);
  bar.title = text;
}

function renderStaff(staff) {
  const box = $("#staffList");
  if (!box) return;
  if (!staff || !staff.length) {
    box.innerHTML = `<div class="hint">尚未设置</div>`;
    return;
  }
  box.innerHTML = staff.map((s) =>
    `<div class="staff-item"><b>${esc(s.name || "")}</b>${s.role ? " · " + esc(s.role) : ""}</div>`).join("");
}

function renderApiState() {
  const c = state.cfg || {};
  const el = $("#apiState");
  const mock = c.debug && c.debug.mock;
  const keySet = c.api && c.api.api_key_set;
  if (mock) {
    el.className = "pill pill-warn";
    el.textContent = "离线演示模式";
  } else if (keySet) {
    el.className = "pill pill-ok";
    el.textContent = "API 已配置 · " + ((c.models || {}).standard || "");
  } else {
    el.className = "pill pill-warn";
    el.textContent = "未配置 API Key（将用本地预检）";
  }
}

function fillSettings() {
  const c = state.cfg || {};
  $("#cfgBase").value = (c.api || {}).base_url || "";
  const mt = (c.api || {}).max_tokens;
  $("#cfgMaxTokens").value = (mt === undefined || mt === null) ? 32768 : mt;
  $("#cfgKey").value = "";
  $("#keyHint").textContent = (c.api || {}).api_key_set ? "已保存：" + (c.api.api_key_hint || "") : "（未设置）";
  const m = c.models || {};
  $("#cfgModelLight").value = m.light || "";
  $("#cfgModelStandard").value = m.standard || "";
  $("#cfgModelStrict").value = m.strict || "";
  $("#cfgModelMax").value = m.max || "";
  const v = c.vision_api || {};
  if ($("#cfgVisionBase")) {
    $("#cfgVisionBase").value = v.base_url || "";
    $("#cfgVisionKey").value = "";
    $("#visionKeyHint").textContent = v.api_key_set ? "已保存：" + (v.api_key_hint || "") : "（沿用主 Key）";
    $("#cfgVisionModel").value = v.model || "";
    $("#cfgVisionPerSheet").value = v.per_sheet || 2;
  }
  $("#cfgChunk").value = ((c.review || {}).chunk_chars) || 2800;
  $("#cfgFont").value = ((c.export || {}).font) || "";
  $("#cfgMock").checked = !!(c.debug && c.debug.mock);
  const price = c.pricing || {};
  $("#cfgPriceIn").value = price.input_per_m === undefined ? 10 : price.input_per_m;
  $("#cfgPriceOut").value = price.output_per_m === undefined ? 30 : price.output_per_m;
}


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

/* ---------------- 选稿 ---------------- */
function renderFiles() {
  const box = $("#fileList");
  if (!state.files.length) { box.innerHTML = ""; }
  else {
    box.innerHTML = state.files.map((f, i) => `
      <div class="file-row ${f.error ? "is-error" : ""}" data-i="${i}">
        <div class="fi">
          <div class="fname">${esc(f.name)}</div>
          <div class="fmeta">${f.error ? esc(f.error)
        : `${f.chars} 字 · ${f.paragraphs} 段 · ${f.tables} 表 · ${f.images} 图`}</div>
        </div>
        <button class="fx" data-del="${i}" title="移除">×</button>
      </div>`).join("");
  }
  const ok = state.files.filter((f) => !f.error);
  $("#startInfo").textContent = ok.length
    ? `已选 ${ok.length} 份稿件，共 ${ok.reduce((a, f) => a + f.chars, 0)} 字`
    : "请先选择稿件";
  $("#btnStart").disabled = !ok.length;
  updateEstimate();
}

async function uploadFiles(fileList) {
  const files = Array.from(fileList || []).filter((f) => /\.docx$/i.test(f.name));
  if (!files.length) { toast("请选择 .docx 文件", true); return; }
  toast(`正在读取 ${files.length} 份稿件…`);
  for (const f of files) {
    const fd = new FormData();
    fd.append("file", f, f.name);
    try {
      const res = await fetch("/api/upload", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "上传失败");
      (data.files || []).forEach((info) => state.files.push(info));
    } catch (e) {
      state.files.push({ name: f.name, error: e.message, chars: 0 });
    }
  }
  renderFiles();
}

async function updateEstimate() {
  const mech = !!(document.querySelector("#chkMechanic") || {}).checked;
  if (mech) {
    setText("#estCalls", "0 次");
    setText("#estIn", "不调用模型");
    setText("#estOut", "—");
    setText("#estCost", "¥0");
    return;
  }
  const ok = state.files.filter((f) => !f.error);
  const chars = ok.reduce((a, f) => a + f.chars, 0);
  if (!chars) {
    ["#estCalls", "#estIn", "#estOut", "#estCost"].forEach((k) => setText(k, "—"));
    return;
  }
  try {
    const r = await api("/api/estimate", { method: "POST", body: { stage: state.stage, level: state.level, chars } });
    setText("#estCalls", `${r.calls} 次`);
    setText("#estIn", `${(r.in_tokens / 1000).toFixed(1)}k tokens`);
    setText("#estOut", `${(r.out_tokens / 1000).toFixed(1)}k tokens`);
    setText("#estCost", `¥${Number(r.cost || 0).toFixed(2)}`);
  } catch (e) {
    setText("#estCalls", "预估失败");
  }
}

/* ---------------- 开始 ---------------- */
async function startReview() {
  const ok = state.files.filter((f) => !f.error);
  if (!ok.length) return;
  $("#btnStart").disabled = true;
  try {
    const job = await api("/api/jobs", {
      method: "POST",
      body: {
        file_ids: ok.map((f) => f.file_id), stage: state.stage, level: state.level,
        mechanic: !!(document.querySelector("#chkMechanic") || {}).checked,
      },
    });
    state.job = job; state.jobId = job.id; state.curFile = null; state.sel = null;
    showView("Running");
    renderRunning();
    openStream(job.id);
    startPolling();
  } catch (e) {
    toast("启动失败：" + e.message, true);
    $("#btnStart").disabled = false;
  }
}

function startPolling() {
  stopPolling();
  state.poll = setInterval(async () => {
    if (!state.jobId) return;
    try {
      const job = await api("/api/jobs/" + state.jobId);
      state.job = job;
      if (["running", "queued"].includes(job.status)) {
        renderRunning();
      } else {
        stopPolling();
        renderRunning();
        setTimeout(() => enterResults(), 350);
      }
    } catch (e) {
      stopPolling();
      toast("获取进度失败：" + e.message, true);
    }
  }, 900);
}
function stopPolling() { if (state.poll) { clearInterval(state.poll); state.poll = null; } }

function renderRunning() {
  const job = state.job;
  if (!job) return;
  $("#runTitle").textContent = `${job.stage_label} · 正在审读`;
  let msg = job.message || "";
  if (job.model) msg += msg ? ` · ${job.model}` : job.model;
  $("#runMsg").textContent = msg;
  const p = job.progress || {};
  const pct = (p.pct !== undefined && p.pct !== null)
    ? p.pct
    : Math.max(4, Math.min(100, Math.round((p.done / Math.max(1, p.total)) * 100)));
  $("#progressFill").style.width = pct + "%";
  $("#runPct").textContent = pct + "%";
  $("#runFiles").innerHTML = (job.files || []).map((f) => {
    const cls = f.status === "done" ? "done" : f.status === "error" ? "error" : f.status === "running" ? "running" : "";
    const tail = f.status === "done" ? ` · ${(f.result?.findings || []).length} 条` : "";
    return `<span class="chip ${cls}">${esc(f.name)}${tail}</span>`;
  }).join("");
  renderUsage(job);
  const logs = (job.log || []).slice(-60);
  const box = $("#logBox");
  const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
  box.innerHTML = logs.map((l) =>
    `<div class="${l.level === "error" ? "error" : ""}"><span class="t">${esc((l.t || "").slice(11))}</span>${esc(l.msg)}</div>`
  ).join("");
  if (atBottom) box.scrollTop = box.scrollHeight;
}

function usageText(job) {
  const u = (job && job.usage) || {};
  const pin = u.prompt_tokens || 0;
  const cout = u.completion_tokens || 0;
  if (!pin && !cout) return "";
  const est = u.estimated ? "（含流式估算）" : "";
  return `本次用量：输入 ${pin} · 输出 ${cout} tokens${est} · 费用 ¥${Number((job && job.cost) || 0).toFixed(4)}`;
}

function renderUsage(job) {
  const text = usageText(job);
  const a = $("#usageInfo");
  const b = $("#runUsage");
  if (a) a.textContent = text;
  if (b) b.textContent = text;
}

/* ---------------- AI 实时输出（SSE） ---------------- */
function openStream(jobId) {
  closeStream();
  const panel = $("#aiPanel");
  const out = $("#aiOut");
  out.textContent = "";
  panel.classList.remove("is-hidden");
  if (!window.EventSource) return;
  let es = null;
  try {
    es = new EventSource(`/api/jobs/${jobId}/stream`);
  } catch (e) { return; }
  state.stream = es;
  es.onmessage = (ev) => {
    let d = null;
    try { d = JSON.parse(ev.data); } catch (e) { return; }
    if (!d) return;
    if (d.type === "delta" && d.text) {
      out.textContent += d.text;
      if (out.textContent.length > 24000) out.textContent = out.textContent.slice(-16000);
      out.scrollTop = out.scrollHeight;
    } else if (d.type === "status") {
      if (d.pct !== undefined && d.pct !== null) {
        $("#progressFill").style.width = d.pct + "%";
        $("#runPct").textContent = d.pct + "%";
        if (state.job) {
          state.job.progress = state.job.progress || {};
          state.job.progress.pct = d.pct;
        }
      }
      if (d.message) $("#runMsg").textContent = d.message;
    } else if (d.type === "done") {
      closeStream();
    }
  };
  es.onerror = () => { /* 服务端在任务结束后主动关闭；结果页会收起面板 */ };
}

function closeStream() {
  if (state.stream) {
    try { state.stream.close(); } catch (e) { /* 忽略 */ }
    state.stream = null;
  }
}

/* ---------------- 结果 ---------------- */
function enterResults() {
  const job = state.job;
  if (!job) return;
  const done = (job.files || []).filter((f) => f.result);
  if (!done.length) {
    const err = (job.files || []).find((f) => f.error);
    toast("审稿失败：" + (err ? err.error : "没有结果"), true);
    showView("Setup");
    $("#btnStart").disabled = false;
    closeStream();
    $("#aiPanel").classList.add("is-hidden");
    return;
  }
  closeStream();
  $("#aiPanel").classList.add("is-hidden");
  state.curFile = done[0].file_id;
  const first = ((done[0].result || {}).findings || [])[0];
  state.sel = first ? first.id : null;
  showView("Results");
  renderResults();
  loadHistory();
}

async function loadDemo() {
  try {
    toast("正在载入示例稿件…");
    const info = await api("/api/demo", { method: "POST", body: {} });
    state.files = [info];
    renderFiles();
    await startReview();
  } catch (e) {
    toast("示例载入失败：" + e.message, true);
  }
}

function curEntry() {
  return ((state.job || {}).files || []).find((f) => f.file_id === state.curFile) || null;
}
function curResult() { const e = curEntry(); return e && e.result ? e.result : null; }

function currentFindings() {
  const r = curResult();
  if (!r) return [];
  return (r.findings || []).filter((f) =>
    (state.filterSev === "all" || f.severity === state.filterSev) &&
    (state.filterStatus === "all" || f.status === state.filterStatus ||
      (state.filterStatus === "accepted" && f.status === "edited"))
  );
}


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

function renderResults() {
  const r = curResult();
  if (!r) return;
  renderTabs();
  renderVerdict(r);
  renderList();
  renderCounts();
  renderDetail();
  renderDoc();
  renderRail();
  renderUsage(state.job);
  $("#btnExport").disabled = false;
}

function renderTabs() {
  const files = ((state.job || {}).files || []).filter((f) => f.result);
  $("#fileTabs").innerHTML = files.map((f) => `
    <button class="tab ${f.file_id === state.curFile ? "is-active" : ""}" data-file="${f.file_id}">
      ${esc(f.name)}
    </button>`).join("");
}

function renderVerdict(r) {
  let box = $("#verdictBox");
  if (!box) {
    box = document.createElement("div");
    box.id = "verdictBox";
    $(".workbench").before(box);
  }
  const v = r.verdict || null;
  const struct = r.structure_suggestions || [];
  const notes = (r.notes || []).map((n) => `<div class="notes">· ${esc(n)}</div>`).join("");
  if (!v && !struct.length && !r.mock && !notes) { box.innerHTML = ""; return; }

  const dec = { accept: "可录用", revise: "修改后录用", reject: "退稿" }[v && v.decision] || "";
  const quality = v ? (v.quality || LEGACY_QUALITY[v.suggested_grade] || "") : "";
  const qLabel = QUALITY_LABEL[quality] || "";
  let html = "";
  if (v) {
    html += `<div class="verdict">
      <h3>${dec ? "零审结论：" + dec : "审稿结论"}</h3>
      ${v.summary ? `<div class="vline">${esc(v.summary)}</div>` : ""}
      ${(qLabel || v.suggested_section || v.word_count_est) ? `<div class="vline">${qLabel ? `文章质量：${esc(qLabel)}　` : ""}建议板块：${esc(v.suggested_section || "—")}　估算字数：${esc(v.word_count_est || "—")}</div>` : ""}
      ${(v.reasons || []).length ? `<ul>${v.reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
    </div>`;
  }
  if (struct.length) {
    html += `<div class="advice"><h3>结构建议</h3><ul>${struct.map((s) =>
      `<li><b>${esc(s.issue || "")}</b>${s.advice ? "：" + esc(s.advice) : ""}</li>`).join("")}</ul></div>`;
  }
  if (r.mock || notes) {
    html += `<div class="advice">${r.mock ? `<div class="notes">· 当前为本地规则预检（未调用模型），只覆盖硬性文字规则。</div>` : ""}${notes}</div>`;
  }
  box.innerHTML = html;
}

function renderCounts() {
  const r = curResult() || { findings: [] };
  const all = r.findings || [];
  const c = { pending: 0, accepted: 0, rejected: 0 };
  all.forEach((f) => { c[f.status] = (c[f.status] || 0) + 1; });
  const dirty = c.accepted;
  $("#counts").textContent = `共 ${all.length} 条 · 待处理 ${c.pending} · 已接受 ${c.accepted} · 已忽略 ${c.rejected}`;
  $("#btnExport").textContent = dirty ? `导出 Word（${dirty}）` : "导出 Word";
}

function renderList() {
  const list = currentFindings();
  const box = $("#findingList");
  const keep = box.scrollTop;
  if (!list.length) {
    box.innerHTML = `<div class="empty" style="height:180px">没有符合筛选条件的意见</div>`;
    return;
  }
  box.innerHTML = list.map((f) => {
    const cls = `sev-${f.severity}` + (f.id === state.sel ? " is-active" : "") + (f.status === "rejected" ? " is-rejected" : "");
    const st = f.status !== "pending" ? `<span class="badge ${f.status === "accepted" ? "badge-ok" : ""}">${ST[f.status]}</span>` : "";
    return `<div class="fcard ${cls}" data-id="${f.id}">
      <div class="fcard-top">
        <span class="badge badge-${f.severity}">${SEV[f.severity]}</span>
        <span class="badge badge-cat">${esc(f.category)}${f.subcategory ? "·" + esc(f.subcategory) : ""}</span>
        ${st}
        <span class="pid">${esc(f.pid)}</span>
      </div>
      <div class="quote">${esc(f.quote || "")}</div>
      <div class="prob">${esc(f.problem || f.suggestion || "")}</div>
    </div>`;
  }).join("");
  box.scrollTop = keep;
}

function detailHTML(f) {
  const st = `<span class="status-tag status-${f.status}">${ST[f.status]}</span>`;
  const ctxEsc = esc(f.context || "");
  const qEsc = esc(f.quote || "");
  const ctx = qEsc ? ctxEsc.split(qEsc).join(`<mark>${qEsc}</mark>`) : ctxEsc;
  const diff = (f.action === "replace" || f.action === "insert" || f.action === "delete") ? `
    <div class="d-row">
      <div class="d-label">修改</div>
      <div class="diff">
        <div class="box before"><div class="lbl">原文</div>${esc(f.before || f.quote || "（空）")}</div>
        <div class="box after"><div class="lbl">改为</div>${esc(f.after || "（删除）")}</div>
      </div>
    </div>
    <div class="d-row">
      <div class="d-label">接受时的替换文本（可编辑）</div>
      <textarea id="editAfter" class="input">${esc(f.final_text ?? f.after ?? "")}</textarea>
    </div>` : "";
  const actionBtns = f.action === "comment_only" || !f.after
    ? `<button class="btn btn-primary" data-act="accept">接受批注</button>`
    : `<button class="btn btn-primary" data-act="accept">接受修改</button>
       <button class="btn btn-ghost" data-act="comment_only">仅批注不改文</button>`;
  return `
    <div class="d-head">
      <span class="badge badge-${f.severity}">${SEV[f.severity]}</span>
      <span class="badge badge-cat">${esc(f.category)}${f.subcategory ? "·" + esc(f.subcategory) : ""}</span>
      <span class="pid">${esc(f.pid)}</span>
      ${st}
      ${f.needs_human_check ? `<span class="badge badge-should">需人工核实</span>` : ""}
    </div>
    <div class="d-quote">原文片段：${esc(f.quote || "")}　｜　置信度 ${Math.round((f.confidence || 0) * 100)}%</div>
    <div class="d-context">${ctx}</div>
    <div class="d-row"><div class="d-label">问题</div><div class="d-text">${esc(f.problem || "—")}</div></div>
    <div class="d-row"><div class="d-label">建议</div><div class="d-text">${esc(f.suggestion || "—")}</div></div>
    ${diff}
    <div class="d-row"><div class="d-label">依据</div><div class="d-text">${esc(f.rule_ref || "—")}</div></div>
    <div class="d-actions">
      ${actionBtns}
      <button class="btn btn-ghost" data-act="reject">忽略</button>
      <span class="spacer"></span>
      <button class="btn btn-mini" data-act="prev">上一条</button>
      <button class="btn btn-mini" data-act="next">下一条</button>
    </div>
    <div class="d-meta">快捷键：A 接受 · X 忽略 · ↑↓ 切换</div>`;
}

function renderDetail() {
  const box = $("#detail");
  const r = curResult();
  const f = r && (r.findings || []).find((x) => x.id === state.sel);
  if (!f) { box.innerHTML = `<div class="empty">选择左侧一条意见查看详情</div>`; return; }
  box.innerHTML = detailHTML(f);
  const ta = $("#editAfter");
  // 不抢焦点：按 A/X 要能连续处理下一条（原为 ta.focus）
}

/* ---------------- 决定 ---------------- */
async function decide(id, status, finalText) {
  const f = (curResult()?.findings || []).find((x) => x.id === id);
  if (!f) return;
  const body = { file_id: state.curFile, finding_id: id, status };
  if (finalText !== undefined) body.final_text = finalText;
  const r = await api(`/api/jobs/${state.jobId}/decide`, { method: "POST", body });
  state.job = r.job;
  autoAdvance(id);
  renderResults();
}

async function decideMany(ids, status) {
  if (!ids.length) { toast("没有符合条件的意见"); return; }
  const r = await api(`/api/jobs/${state.jobId}/decide`, {
    method: "POST", body: { file_id: state.curFile, ids, status },
  });
  state.job = r.job;
  renderResults();
  toast(`已处理 ${r.changed} 条`);
}

function autoAdvance(id) {
  const list = currentFindings();
  const i = list.findIndex((x) => x.id === id);
  if (i < 0) return;
  const next = list.slice(i + 1).find((x) => x.status === "pending");
  state.sel = next ? next.id : id;
}

/* ---------------- 导出 ---------------- */
async function exportCurrent() {
  if (state.exporting) return;
  state.exporting = true;
  $("#btnExport").disabled = true;
  try {
    const info = await api(`/api/jobs/${state.jobId}/export`, {
      method: "POST", body: { file_id: state.curFile },
    });
    renderExportBanner([info]);
    loadHistory();
  } catch (e) {
    toast("导出失败：" + e.message, true);
  } finally {
    state.exporting = false;
    $("#btnExport").disabled = false;
  }
}

async function exportAll() {
  try {
    const info = await api(`/api/jobs/${state.jobId}/export_all`, { method: "POST", body: {} });
    renderExportBanner([{ path: info.path, name: info.name, count: info.count }]);
    loadHistory();
  } catch (e) {
    toast("导出失败：" + e.message, true);
  }
}

function renderExportBanner(items) {
  const box = $("#exportBanner");
  box.classList.remove("is-hidden");
  box.innerHTML = items.map((it) => `
    <div class="banner">
      <div>
        <div>已导出：${esc(it.name)}</div>
        <div class="path">${esc(it.path)}</div>
      </div>
      <div style="display:flex;gap:8px;flex-shrink:0">
        <button class="btn btn-mini" data-open="${esc(it.path)}">用 Word 打开</button>
        <a class="btn btn-mini" href="/api/download?path=${encodeURIComponent(it.path)}">下载</a>
      </div>
    </div>`).join("");
}

/* ---------------- 历史 ---------------- */
async function loadHistory() {
  try {
    const r = await api("/api/history");
    state.history = r.items || [];
    if (window.renderRecent) renderRecent();
    renderHistory();
  } catch (e) {
    console.warn("历史读取失败", e);
  }
}

function renderHistory() {
  if (!document.querySelector("#historyList")) return;
  const box = $("#historyList");
  if (!state.history.length) {
    box.innerHTML = `<div class="hint">暂无历史记录。完成一次审稿后会自动保存。</div>`;
    return;
  }
  box.innerHTML = state.history.map((h) => `
    <div class="hist-item" data-hist="${esc(h.job_id)}">
      <div class="hist-top">
        <span class="hist-name">${esc(h.name || "（未命名）")}</span>
        <button class="hist-del" data-hist-del="${esc(h.job_id)}" title="删除这条历史">×</button>
      </div>
      <div class="hist-meta">${esc(h.stage_label || h.stage || "")} · ${esc(LEVEL_LABEL[h.level] || h.level || "")} · ${esc(h.updated || h.created || "")}</div>
      <div class="hist-meta">意见 ${h.findings || 0} 条 · 已处理 ${h.decided || 0} 条${h.exports ? ` · 已导出 ${h.exports}` : ""}</div>
    </div>`).join("");
}

async function openHistory(id) {
  try {
    stopPolling();
    closeStream();
    const r = await api("/api/history/" + encodeURIComponent(id));
    const job = r.job;
    if (!job) throw new Error("历史快照为空");
    const done = (job.files || []).filter((f) => f.result);
    if (!done.length) { toast("这条记录没有可用的审稿结果", true); return; }
    state.job = job;
    state.jobId = job.id;
    state.curFile = done[0].file_id;
    state.sel = ((done[0].result || {}).findings || [])[0]?.id || null;
    $("#exportBanner").classList.add("is-hidden");
    showView("Results");
    renderResults();
    toast("已载入历史记录，可继续处理或重新导出");
  } catch (e) {
    toast("载入失败：" + e.message, true);
  }
}

async function deleteHistory(id) {
  if (!window.confirm("确定删除这条历史记录？")) return;
  try {
    await api("/api/history/" + encodeURIComponent(id), { method: "DELETE" });
    await loadHistory();
    toast("已删除");
  } catch (e) {
    toast("删除失败：" + e.message, true);
  }
}

/* ---------------- 设置 ---------------- */
function closeAllDrawers() {
  ["#drawer", "#rulesDrawer", "#adminDrawer"].forEach((s) => {
    const el = $(s);
    if (el) el.classList.add("is-hidden");
  });
  $("#drawerMask").classList.add("is-hidden");
}

function openDrawer() {
  closeAllDrawers();
  $("#drawer").classList.remove("is-hidden");
  $("#drawerMask").classList.remove("is-hidden");
}
function closeDrawer() { closeAllDrawers(); }

async function saveSettings() {
  const patch = {
    api: { base_url: $("#cfgBase").value.trim(),
           max_tokens: parseInt($("#cfgMaxTokens").value, 10) || 0 },
    models: {
      light: $("#cfgModelLight").value.trim(),
      standard: $("#cfgModelStandard").value.trim(),
      strict: $("#cfgModelStrict").value.trim(),
      max: $("#cfgModelMax").value.trim(),
    },
    review: { chunk_chars: parseInt($("#cfgChunk").value, 10) || 2800 },
    vision_api: {
      base_url: ($("#cfgVisionBase") || {}).value ? $("#cfgVisionBase").value.trim() : "",
      model: ($("#cfgVisionModel") || {}).value ? $("#cfgVisionModel").value.trim() : "",
      per_sheet: parseInt(($("#cfgVisionPerSheet") || {}).value, 10) || 2,
    },
    export: { font: $("#cfgFont").value },
    pricing: {
      input_per_m: parseFloat($("#cfgPriceIn").value) || 0,
      output_per_m: parseFloat($("#cfgPriceOut").value) || 0,
    },
    debug: { mock: $("#cfgMock").checked },
  };
  const key = $("#cfgKey").value.trim();
  if (key) patch.api.api_key = key;
  const vkey = ($("#cfgVisionKey") || {}).value ? $("#cfgVisionKey").value.trim() : "";
  if (vkey) patch.vision_api.api_key = vkey;
  try {
    state.cfg = await api("/api/config", { method: "POST", body: patch });
    renderApiState();
    fillSettings();
    updateEstimate();
    toast("已保存");
    closeDrawer();
  } catch (e) {
    toast("保存失败：" + e.message, true);
  }
}

async function testConnection() {
  const box = $("#testResult");
  box.className = "hint test-result";
  box.textContent = "测试中…（会真实请求一次模型）";
  try {
    const r = await api("/api/check", { method: "POST", body: {} });
    if (r.ok) {
      box.className = "hint test-result ok";
      box.innerHTML = `连接正常：${esc(r.model)} · ${r.ms}ms · 回复「${esc(r.reply || "")}」`;
      return;
    }
    const lines = [];
    lines.push(`<b>失败</b>：${esc(r.error || r.hint || "未知原因")}`);
    if (r.chat_url) lines.push(`请求地址：<code>${esc(r.chat_url)}</code>`);
    if (r.hint && r.hint !== r.error) lines.push(`建议：${esc(r.hint)}`);
    for (const a of r.attempts || []) {
      lines.push(`· ${esc(a.name)} <code>${esc(a.url)}</code> → ${a.status || "无响应"}`);
    }
    box.className = "hint test-result bad";
    box.innerHTML = lines.join("<br>");
  } catch (e) {
    box.className = "hint test-result bad";
    box.textContent = "失败：" + e.message;
  }
}

async function testVision() {
  const box = $("#visionTestMsg");
  if (box) { box.className = "hint"; box.textContent = "测试中…（会真实发一张图给视觉模型）"; }
  try {
    const r = await api("/api/check", { method: "POST", body: { vision: true } });
    if (r.ok) {
      if (box) {
        box.className = "hint test-result ok";
        box.innerHTML = `视觉模型可用：${esc(r.model)} · ${r.ms}ms · 回复「${esc(r.reply || "")}」`;
      }
      return;
    }
    if (box) {
      box.className = "hint test-result bad";
      box.innerHTML = `<b>不可用</b>：${esc(r.error || r.hint || "未知原因")}` +
        (r.model ? `<br>模型：<code>${esc(r.model)}</code>` : "") +
        (r.base ? `<br>Base：<code>${esc(r.base)}</code>` : "");
    }
  } catch (e) {
    if (box) { box.className = "hint test-result bad"; box.textContent = "失败：" + e.message; }
  }
}

/* ---------------- 规则库 ---------------- */
async function openRules() {
  closeAllDrawers();
  $("#rulesDrawer").classList.remove("is-hidden");
  $("#drawerMask").classList.remove("is-hidden");
  await refreshRules();
}

async function refreshRules() {
  try {
    state.rules = await api("/api/rules");
    renderRules();
  } catch (e) {
    $("#rulesMsg").textContent = "读取规则失败：" + e.message;
  }
}

function renderRules() {
  const r = state.rules || { groups: [], custom: [] };
  const box = $("#rulesList");
  let html = "";
  (r.groups || []).forEach((g) => {
    html += `<div class="rule-group">
      <div class="rule-group-title">${esc(g.label)} · ${esc(g.title || "")}</div>
      ${(g.sections || []).map((s) => `
        <label class="rule-row">
          <input type="checkbox" data-rule-id="${esc(s.id)}" ${s.enabled ? "checked" : ""} />
          <span>${esc(s.title)}</span>
        </label>`).join("")}
    </div>`;
  });
  if ((r.custom || []).length) {
    html += `<div class="rule-group">
      <div class="rule-group-title">自定义规则</div>
      ${r.custom.map((c) => `
        <label class="rule-row">
          <input type="checkbox" data-rule-id="${esc(c.id)}" ${c.enabled ? "checked" : ""} />
          <span>${esc(c.stage_label || "")} · ${esc(c.title)}</span>
        </label>`).join("")}
    </div>`;
  }
  box.innerHTML = html || `<div class="hint">暂无规则</div>`;
}

async function toggleRule(id, enabled) {
  try {
    state.rules = await api("/api/rules/toggle", { method: "POST", body: { id, enabled } });
    renderRules();
    toast(enabled ? "已启用该规则" : "已关闭该规则（不再发送给模型）");
  } catch (e) {
    toast("操作失败：" + e.message, true);
    await refreshRules();
  }
}

async function addCustomRule() {
  const title = $("#ruleTitle").value.trim();
  const body = $("#ruleBody").value.trim();
  const stage = $("#ruleStage").value;
  if (!title || !body) { toast("请填写自定义规则的标题与正文", true); return; }
  try {
    state.rules = await api("/api/rules/custom", { method: "POST", body: { title, body, stage } });
    $("#ruleTitle").value = "";
    $("#ruleBody").value = "";
    renderRules();
    $("#rulesMsg").textContent = "已追加自定义规则：" + title;
    toast("已追加自定义规则");
  } catch (e) {
    $("#rulesMsg").textContent = "追加失败：" + e.message;
    toast("追加失败：" + e.message, true);
  }
}

/* ---------------- 管理员 ---------------- */
function openAdmin() {
  closeAllDrawers();
  $("#adminDrawer").classList.remove("is-hidden");
  $("#drawerMask").classList.remove("is-hidden");
  const logged = !!state.adminToken;
  $("#adminLogin").classList.toggle("is-hidden", logged);
  $("#adminPanel").classList.toggle("is-hidden", !logged);
  if (logged) loadAdminState();
}

function adminHeaders() {
  return state.adminToken ? { "X-Admin-Token": state.adminToken } : {};
}

async function adminLogin() {
  const pwd = $("#adminPwd").value;
  try {
    const r = await api("/api/admin/login", { method: "POST", body: { password: pwd } });
    state.adminToken = r.token;
    $("#adminPwd").value = "";
    $("#adminLoginMsg").textContent = "";
    $("#adminLogin").classList.add("is-hidden");
    $("#adminPanel").classList.remove("is-hidden");
    await loadAdminState();
    toast("已进入管理员模式");
  } catch (e) {
    $("#adminLoginMsg").textContent = e.message;
    toast("进入失败：" + e.message, true);
  }
}

async function loadAdminState() {
  try {
    const r = await api("/api/admin/state", { method: "POST", body: {}, headers: adminHeaders() });
    const a = r.admin || {};
    const ad = a.api_defaults || {};
        $("#adminBase").value = ad.base_url || "";
    const m = ad.models || {};
    $("#adminModelLight").value = m.light || "";
    $("#adminModelStandard").value = m.standard || "";
    $("#adminModelStrict").value = m.strict || "";
    $("#adminModelMax").value = m.max || "";
    $("#adminSendKey").checked = !!ad.send_key;
    $("#adminKey").value = "";
    $("#adminKey").placeholder = ad.api_key_set ? "已保存：" + (ad.api_key_hint || "") + "（留空则沿用）" : "可选";
    $("#adminMsg").textContent = "";
  } catch (e) {
    $("#adminMsg").textContent = "读取管理员配置失败：" + e.message;
  }
}

async function saveAdmin() {
  const body = {
    api_defaults: {
      base_url: $("#adminBase").value.trim(),
      models: {
        light: $("#adminModelLight").value.trim(),
        standard: $("#adminModelStandard").value.trim(),
        strict: $("#adminModelStrict").value.trim(),
        max: $("#adminModelMax").value.trim(),
      },
      api_key: $("#adminKey").value.trim(),
      send_key: $("#adminSendKey").checked,
    },
  };
  try {
    const r = await api("/api/admin/save", { method: "POST", body, headers: adminHeaders() });
      $("#adminKey").value = "";
    $("#adminMsg").textContent = "已保存管理员设置";
    await loadConfig();
    toast("管理员设置已保存");
  } catch (e) {
    $("#adminMsg").textContent = "保存失败：" + e.message;
    toast("保存失败：" + e.message, true);
  }
}

async function changeAdminPassword() {
  const pwd = $("#adminNewPwd").value;
  if (pwd.length < 4) { toast("新密码至少 4 位", true); return; }
  try {
    await api("/api/admin/password", { method: "POST", body: { new_password: pwd }, headers: adminHeaders() });
    $("#adminNewPwd").value = "";
    $("#adminMsg").textContent = "密码已修改（旧密码立即失效）";
    toast("密码已修改");
  } catch (e) {
    $("#adminMsg").textContent = "改密码失败：" + e.message;
    toast("改密码失败：" + e.message, true);
  }
}

/* ---------------- 事件绑定 ---------------- */
function bind() {
  const drop = $("#drop");
  $("#fileInput").addEventListener("change", (e) => { uploadFiles(e.target.files); e.target.value = ""; });
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => {
    e.preventDefault(); drop.classList.add("is-over");
  }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => {
    e.preventDefault(); drop.classList.remove("is-over");
  }));
  drop.addEventListener("drop", (e) => uploadFiles(e.dataTransfer.files));

  $("#fileList").addEventListener("click", (e) => {
    const b = e.target.closest("[data-del]");
    if (b) { state.files.splice(+b.dataset.del, 1); renderFiles(); }
  });

  $("#stageList").addEventListener("click", (e) => {
    const b = e.target.closest(".stage:not(.is-disabled)");
    if (!b) return;
    state.stage = b.dataset.stage;
    $$(".stage").forEach((x) => x.classList.toggle("is-active", x === b));
    state.stageDesc = STAGE_DESC[state.stage] || "";
    setText("#stageDetail", state.stageDesc || STAGE_DESC.second);
    if (state.stage !== "third") {
      // 从三审切回文稿环节：恢复稿件状态文案与按钮可用性
      renderFiles();
    }
    updateEstimate();
  });
  $("#levelList").addEventListener("click", (e) => {
    const b = e.target.closest(".level");
    if (!b) return;
    state.level = b.dataset.level;
    $$(".level").forEach((x) => x.classList.toggle("is-active", x === b));
    updateEstimate();
  });

  document.querySelector("#chkMechanic")?.addEventListener("change", () => {
    const on = document.querySelector("#chkMechanic").checked;
    setText("#startInfo", on
      ? "机械审查：只用本地规则，不调用模型，零成本"
      : (state.files.filter((f) => !f.error).length ? "可以开始审稿" : "请先放入稿件"));
    updateEstimate();
  });
  $("#btnStart").addEventListener("click", startReview);
  $("#btnDemo").addEventListener("click", loadDemo);
  $("#btnCancel").addEventListener("click", async () => {
    try { await api(`/api/jobs/${state.jobId}/cancel`, { method: "POST", body: {} }); } catch (e) { }
  });
  $("#btnNew").addEventListener("click", () => {
    stopPolling();
    closeStream();
    $("#aiPanel").classList.add("is-hidden");
    state.files = []; state.job = null; state.jobId = null; state.curFile = null; state.sel = null;
    renderFiles();
    showView("Setup");
    $("#btnStart").disabled = true;
    loadHistory();
  });

  $("#fileTabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-file]");
    if (!b) return;
    state.curFile = b.dataset.file; state.sel = null;
    $("#exportBanner").classList.add("is-hidden");
    renderResults();
  });

  $("#filterSev").addEventListener("change", (e) => { state.filterSev = e.target.value; renderList(); });
  $("#filterStatus").addEventListener("change", (e) => { state.filterStatus = e.target.value; renderList(); });

  $("#findingList").addEventListener("click", (e) => {
    const c = e.target.closest("[data-id]");
    if (!c) return;
    state.sel = c.dataset.id;
    renderList(); renderDetail(); renderDoc(); renderRail();
  });

  $("#detail").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const act = b.dataset.act;
    const f = (curResult()?.findings || []).find((x) => x.id === state.sel);
    if (!f) return;
    if (act === "prev" || act === "next") {
      const list = currentFindings();
      const i = list.findIndex((x) => x.id === state.sel);
      const j = act === "next" ? Math.min(list.length - 1, i + 1) : Math.max(0, i - 1);
      if (list[j]) { state.sel = list[j].id; renderList(); renderDetail(); }
      return;
    }
    try {
      if (act === "reject") await decide(f.id, "rejected");
      else if (act === "comment_only") await decide(f.id, "comment_only");
      else {
        const ta = $("#editAfter");
        const text = ta ? ta.value : (f.final_text ?? f.after);
        const status = ta && ta.value !== (f.after || "") ? "edited" : "accepted";
        await decide(f.id, status, text);
      }
    } catch (err) { toast("操作失败：" + err.message, true); }
  });

  $("#btnAcceptMust").addEventListener("click", () => {
    const ids = (curResult()?.findings || []).filter((f) => f.severity === "must" && f.status === "pending").map((f) => f.id);
    decideMany(ids, "accepted");
  });
  $("#btnRejectSuggest").addEventListener("click", () => {
    const ids = (curResult()?.findings || []).filter((f) => f.severity === "suggest" && f.status === "pending").map((f) => f.id);
    decideMany(ids, "rejected");
  });

  $("#btnExport").addEventListener("click", exportCurrent);
  $("#btnExportAll").addEventListener("click", exportAll);
  $("#exportBanner").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-open]");
    if (!b) return;
    try { await api("/api/open", { method: "POST", body: { path: b.dataset.open } }); }
    catch (err) { toast("打开失败：" + err.message, true); }
  });

  document.querySelector("#historyList")?.addEventListener("click", (e) => {
    const del = e.target.closest("[data-hist-del]");
    if (del) { e.stopPropagation(); deleteHistory(del.dataset.histDel); return; }
    const item = e.target.closest("[data-hist]");
    if (item) openHistory(item.dataset.hist);
  });
  document.querySelector("#btnRefreshHistory")?.addEventListener("click", loadHistory);

  $("#btnSettings").addEventListener("click", openDrawer);
  $("#btnCloseDrawer").addEventListener("click", closeDrawer);
  $("#drawerMask").addEventListener("click", closeAllDrawers);
  $("#btnSaveCfg").addEventListener("click", saveSettings);
  $("#btnTest").addEventListener("click", testConnection);
  if ($("#btnTestVision")) $("#btnTestVision").addEventListener("click", testVision);

  $("#btnRules").addEventListener("click", openRules);
  $("#btnCloseRules").addEventListener("click", closeAllDrawers);
  $("#btnAddRule").addEventListener("click", addCustomRule);
  $("#rulesList").addEventListener("change", (e) => {
    const cb = e.target.closest("[data-rule-id]");
    if (cb) toggleRule(cb.dataset.ruleId, cb.checked);
  });

  $("#btnAdmin").addEventListener("click", openAdmin);
  $("#btnCloseAdmin").addEventListener("click", closeAllDrawers);
  $("#btnAdminLogin").addEventListener("click", adminLogin);
  $("#btnAdminSave").addEventListener("click", saveAdmin);
  $("#btnAdminPwd").addEventListener("click", changeAdminPassword);

  $("#btnAiClose").addEventListener("click", () => {
    closeStream();
    $("#aiPanel").classList.add("is-hidden");
  });

  document.addEventListener("keydown", async (e) => {
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
  });
}

/* ---------------- 启动 ---------------- */
(function init() {
  bind();
  initTheme();
  $$(".stage").forEach((x) => x.classList.remove("is-active"));
  $('.stage[data-stage="second"]').classList.add("is-active");
  $('.level[data-level="standard"]').classList.add("is-active");
  renderFiles();
  renderRail();
  loadHistory();
  bindRecent();
  loadConfig().then(() => {
    const q = new URLSearchParams(location.search);
    if (q.get("demo") === "1") loadDemo();
    if (q.get("settings") === "1") openDrawer();
  });
})();
