/* 活动策划案初审 —— 独立页面脚本 */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const LEVEL = { must: "必改", suggest: "建议" };
  const VERDICT_CLASS = { "通过": "pass", "修改后通过": "revise", "退回修改": "return" };

  const state = { file: null, jobId: null, job: null, poll: null, sel: null };

  /* ---------- 主题 ---------- */
  function initTheme() {
    const btn = $("#btnTheme");
    const apply = (t) => {
      document.documentElement.dataset.theme = t;
      btn.textContent = t === "dark" ? "浅色" : "深色";
    };
    apply(document.documentElement.dataset.theme || "light");
    btn.addEventListener("click", () => {
      const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      apply(t);
      try { localStorage.setItem("restagent.theme", t); } catch (e) { }
    });
  }

  function showView(name) {
    ["campSetup", "campRunning", "campResult"].forEach((id) => {
      $("#" + id).classList.toggle("is-hidden", id !== "camp" + name);
    });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  /* ---------- 选文件 ---------- */
  function setFile(f) {
    if (!f) return;
    if (!/\.docx$/i.test(f.name)) {
      alert("只支持 .docx；旧版 .doc 请先用 Word「另存为」.docx");
      return;
    }
    state.file = f;
    const box = $("#campFileName");
    box.textContent = f.name + "（" + (f.size / 1024).toFixed(0) + " KB）";
    box.classList.remove("is-hidden");
    $("#btnCampStart").disabled = false;
    $("#campStartHint").textContent = "将按「" + $("#campSubmission").value + "」的要求审";
  }

  /* ---------- 启动 ---------- */
  async function start() {
    if (!state.file) return;
    $("#btnCampStart").disabled = true;
    const fd = new FormData();
    fd.append("file", state.file, state.file.name);
    fd.append("kind", $("#campKind").value || "");
    fd.append("submission", $("#campSubmission").value || "协内自用");
    try {
      const tk = (function () {
        try { return sessionStorage.getItem("ra_token") || new URLSearchParams(location.search).get("t") || ""; }
        catch (e) { return ""; }
      })();
      const res = await fetch("/api/campaign", { method: "POST", body: fd,
        headers: tk ? { "X-Auth-Token": tk } : {} });
      let job = {};
      try { job = await res.json(); } catch (e) { job = { error: "服务返回异常" }; }
      if (!res.ok) throw new Error(job.error || ("HTTP " + res.status));
      state.jobId = job.id;
      state.job = job;
      showView("Running");
      renderRunning();
      poll();
    } catch (e) {
      alert("启动失败：" + e.message);
      $("#btnCampStart").disabled = false;
    }
  }

  function poll() {
    stopPoll();
    state.poll = setInterval(async () => {
      if (!state.jobId) return;
      try {
        const res = await fetch("/api/campaign/" + state.jobId, { headers: (function(){try{const tk=sessionStorage.getItem("ra_token")||"";return tk?{"X-Auth-Token":tk}:{};}catch(e){return {};}})() });
        const job = await res.json();
        if (!res.ok) throw new Error(job.error || "读取失败");
        state.job = job;
        renderRunning();
        if (job.status !== "running") {
          stopPoll();
          if (job.status === "done") setTimeout(renderResult, 250);
          else {
            alert("初审失败：" + (job.error || "未知错误"));
            showView("Setup");
            $("#btnCampStart").disabled = false;
          }
        }
      } catch (e) {
        stopPoll();
        alert("获取进度失败：" + e.message);
      }
    }, 1200);
  }
  function stopPoll() { if (state.poll) { clearInterval(state.poll); state.poll = null; } }

  /* ---------- 进度 ---------- */
  function renderRunning() {
    const j = state.job || {};
    $("#campRunTitle").textContent = j.status === "done" ? "初审完成" : "正在初审";
    $("#campRunMsg").textContent = "《" + ((j.file || {}).name || "") + "》 · " +
      (j.kind || "类型待判") + " · " + (j.submission || "");
    const pct = Number(j.pct || 0);
    $("#campRunPct").textContent = pct + "%";
    $("#campProgressFill").style.width = pct + "%";
    const box = $("#campLog");
    const logs = j.log || [];
    const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
    box.innerHTML = logs.map((l) =>
      '<div class="' + (l.level === "error" ? "error" : "") + '"><span class="t">' +
      esc(l.t) + "</span>" + esc(l.msg) + "</div>").join("");
    if (atBottom) box.scrollTop = box.scrollHeight;
  }

  /* ---------- 结果 ---------- */
  async function renderResult() {
    const res = await fetch("/api/campaign/" + state.jobId, { headers: (function(){try{const tk=sessionStorage.getItem("ra_token")||"";return tk?{"X-Auth-Token":tk}:{};}catch(e){return {};}})() });
    const job = await res.json();
    state.job = job;
    const r = job.result || {};
    const fs = r.findings || [];
    const must = fs.filter((f) => f.level === "must").length;
    const sug = fs.length - must;

    const cls = VERDICT_CLASS[r.verdict] || "";
    $("#campVerdict").className = "camp-verdict " + cls;
    $("#campVerdict").innerHTML =
      "<h2>" + esc(r.verdict || "") + "</h2>" +
      "<p>" + esc(r.summary || "") + "</p>" +
      ((r.missing || []).length
        ? "<p style='margin-top:8px'>" + r.missing.map((m) =>
            "<span class='tag'>缺 " + esc(m) + "</span>").join("") + "</p>"
        : "");

    updateCounts(fs);
    $("#btnCampDocx").href = "/api/campaign/" + state.jobId + "/docx";
    $("#btnCampMd").href = "/api/campaign/" + state.jobId + "/report";

    state.sel = null;
    renderList(fs);
    $("#campDetail").innerHTML = '<div class="camp-empty">选择左侧一条意见查看详情</div>';
    showView("Result");
  }

  function renderList(fs) {
    const box = $("#campList");
    if (!fs.length) {
      box.innerHTML = '<div class="camp-empty">没有发现问题，可以交给下一环节。</div>';
      return;
    }
    let html = "";
    let last = null;
    fs.forEach((f, i) => {
      if (f.level !== last) {
        html += '<div class="camp-group">' + (LEVEL[f.level] || f.level) + "</div>";
        last = f.level;
      }
      const active = (state.sel === i ? " is-active" : "") +
        ((f.status || "pending") === "rejected" ? " rejected" : "");
      const where = f.paragraph >= 0 ? ("第 " + (f.paragraph + 1) + " 段") : "全文";
      const st = f.status || "pending";
      const stMark = st === "accepted" ? "✓ 已接受" : st === "rejected" ? "× 已忽略" : "";
      html += '<div class="camp-card' + active + '" data-i="' + i + '">' +
        '<div class="camp-card-top"><span class="camp-badge ' + esc(f.level) + '">' +
        (LEVEL[f.level] || f.level) + "</span><span>" + esc(f.rule || "") +
        "</span><span style='margin-left:auto'>" + esc(where) + "</span></div>" +
        '<div class="camp-card-problem">' + esc(f.problem || "") +
        (stMark ? '<span class="camp-status ' + st + '">' + stMark + "</span>" : "") +
        "</div></div>";
    });
    box.innerHTML = html;
    box._findings = fs;
  }

  function renderDetail(i) {
    const fs = ($("#campList")._findings) || [];
    const f = fs[i];
    if (!f) return;
    const rows = [];
    rows.push("<h3>" + esc(f.dimension || "") + (f.rule ? " · " + esc(f.rule) : "") + "</h3>");
    rows.push("<div class='meta'>" + (LEVEL[f.level] || "") + " · " +
      (f.paragraph >= 0 ? "第 " + (f.paragraph + 1) + " 段" : "全文") + "</div>");
    if (f.anchor) rows.push("<div class='camp-quote'>" + esc(f.anchor) + "</div>");
    rows.push("<div class='camp-block'><div class='k'>问题</div><div class='v'>" +
      esc(f.problem || "") + "</div></div>");
    if (f.rewrite) {
      rows.push("<div class='camp-block'><div class='k'>建议改为</div><div class='camp-rewrite'>" +
        esc(f.rewrite) + "</div></div>");
    }
    if (f.basis) {
      rows.push("<div class='camp-block'><div class='k'>教案依据</div><div class='v'>" +
        esc(f.basis) + "</div></div>");
    }
    const st = f.status || "pending";
    rows.push('<div class="camp-acts">' +
      '<button class="btn btn-primary btn-mini" data-act="accepted">接受</button>' +
      '<button class="btn btn-ghost btn-mini" data-act="rejected">忽略</button>' +
      (st !== "pending" ? '<button class="btn btn-ghost btn-mini" data-act="pending">还原</button>' : "") +
      '<span class="camp-acts-hint">' +
      (st === "accepted" ? "已接受：会写进批注版 Word" : st === "rejected" ? "已忽略：不写进 Word" : "待处理") +
      "</span></div>");
    $("#campDetail").innerHTML = rows.join("");
  }

  async function decideCamp(ids, status) {
    try {
      const res = await fetch("/api/campaign/" + state.jobId + "/decide", {
        method: "POST",
        headers: Object.assign({ "Content-Type": "application/json" },
          (function () {
            try { const tk = sessionStorage.getItem("ra_token"); return tk ? { "X-Auth-Token": tk } : {}; }
            catch (e) { return {}; }
          })()),
        body: JSON.stringify({ ids: ids, status: status }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "操作失败");
      state.job = data.job;
      const r = state.job.result || {};
      const fs = r.findings || [];
      renderList(fs);
      if (state.sel !== null && fs[state.sel]) renderDetail(state.sel);
      updateCounts(fs);
    } catch (e) {
      alert("操作失败：" + e.message);
    }
  }

  function updateCounts(fs) {
    const must = fs.filter((f) => f.level === "must").length;
    const sug = fs.length - must;
    const done = fs.filter((f) => (f.status || "pending") !== "pending").length;
    const el = $("#campCounts");
    if (el) el.textContent = "共 " + fs.length + " 条 · 必改 " + must +
      " · 建议 " + sug + " · 已处理 " + done;
  }

  /* ---------- 事件 ---------- */
  function bind() {
    initTheme();

    const drop = $("#campDrop");
    const input = $("#campFile");
    input.addEventListener("change", (e) => setFile(e.target.files[0]));
    ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => {
      e.preventDefault();
      drop.classList.add("is-over");
    }));
    ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => {
      e.preventDefault();
      drop.classList.remove("is-over");
    }));
    drop.addEventListener("drop", (e) => {
      if (e.dataTransfer.files && e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]);
    });

    $("#campSubmission").addEventListener("change", () => {
      if (state.file) {
        $("#campStartHint").textContent = "将按「" + $("#campSubmission").value + "」的要求审";
      }
    });
    $("#btnCampStart").addEventListener("click", start);

    $("#campList").addEventListener("click", (e) => {
      const card = e.target.closest("[data-i]");
      if (!card) return;
      state.sel = Number(card.dataset.i);
      $$("#campList .camp-card").forEach((c) => c.classList.toggle("is-active", c === card));
      renderDetail(state.sel);
    });

    $("#campDetail").addEventListener("click", (e) => {
      const b = e.target.closest("[data-act]");
      if (!b || state.sel === null) return;
      const fs = ((state.job || {}).result || {}).findings || [];
      const f = fs[state.sel];
      if (!f) return;
      decideCamp([f.id], b.dataset.act);
    });

    $("#btnCampAcceptMust")?.addEventListener("click", () => {
      const fs = ((state.job || {}).result || {}).findings || [];
      decideCamp(fs.filter((x) => x.level === "must").map((x) => x.id), "accepted");
    });
    $("#btnCampRejectSuggest")?.addEventListener("click", () => {
      const fs = ((state.job || {}).result || {}).findings || [];
      decideCamp(fs.filter((x) => x.level === "suggest").map((x) => x.id), "rejected");
    });
    $("#btnCampReset")?.addEventListener("click", () => {
      const fs = ((state.job || {}).result || {}).findings || [];
      decideCamp(fs.map((x) => x.id), "pending");
    });

    $("#btnCampNew").addEventListener("click", () => {
      stopPoll();
      state.file = null;
      state.jobId = null;
      state.job = null;
      state.sel = null;
      $("#campFileName").classList.add("is-hidden");
      $("#campFile").value = "";
      $("#btnCampStart").disabled = true;
      $("#campStartHint").textContent = "先选择一份 .docx 策划案";
      showView("Setup");
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind);
  else bind();
})();
