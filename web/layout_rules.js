/* 《排版要求》规则库（三审）：动态读取 / 编辑 / 保存 / 恢复默认
 * 复用 app.js 的全局：state / $ / $$ / esc / api / toast
 */
(function () {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const GROUPS = [
    ["页面", ["page.width_mm", "page.height_mm", "page.bleed_mm", "page.tolerance_mm",
             "page.columns", "page.gutter_mm"]],
    ["版面网格", ["grid.font", "grid.point_size", "grid.char_aki", "grid.line_aki",
                "grid.char_width", "grid.line_height", "grid.chars_per_column",
                "grid.lines_per_page", "grid.tolerance"]],
    ["各角色字号", ["roles.body.size", "roles.body.leading", "roles.body.indent_mm",
                  "roles.heading.size", "roles.heading.leading",
                  "roles.subheading.size", "roles.subheading.leading",
                  "roles.author.size",
                  "roles.caption.size", "roles.caption.leading", "roles.caption.indent_mm",
                  "roles.editbar.size", "tolerance.size_pt"]],
  ];

  function getPath(obj, path) {
    return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
  }

  async function loadLayoutRules() {
    try {
      state.layout = await api("/api/layout/rules");
      renderLayoutRules();
    } catch (e) {
      const m = $("#layoutMsg");
      if (m) m.textContent = "读取排版要求失败：" + e.message;
    }
  }

  function renderLayoutRules() {
    const box = $("#layoutRules");
    if (!box) return;
    const L = state.layout || {};
    const cur = L.rules || {};
    const labels = L.labels || {};
    const units = L.units || {};
    const ruleLabels = L.rule_labels || {};
    let html = "";

    GROUPS.forEach(([title, keys]) => {
      html += '<div class="rule-group"><div class="rule-group-title">' + esc(title) + '</div><div class="lay-fields">';
      keys.forEach((k) => {
        const val = getPath(cur, k);
        const isText = typeof val === "string";
        html += '<label class="lay-row"><span>' + esc(labels[k] || k) + "</span>" +
          '<input class="input" data-path="' + esc(k) + '" type="' + (isText ? "text" : "number") +
          '" step="0.1" value="' + esc(val) + '" />' +
          "<em>" + esc(units[k] || "") + "</em></label>";
      });
      html += "</div></div>";
    });

    html += '<div class="rule-group"><div class="rule-group-title">规则开关</div>';
    Object.keys(ruleLabels).forEach((id) => {
      const on = !cur.rules || cur.rules[id] !== false;
      html += '<label class="rule-row"><input type="checkbox" data-lay-rule="' + esc(id) + '" ' +
        (on ? "checked" : "") + ' /><span>' + esc(ruleLabels[id]) + "</span></label>";
    });
    html += "</div>";

    box.innerHTML = html;
    const msg = $("#layoutMsg");
    if (msg && L.path) msg.textContent = "配置文件：" + L.path;
  }

  async function saveLayoutRules() {
    const patch = { page: {}, grid: {}, roles: { body: {}, heading: {}, subheading: {},
      author: {}, caption: {}, editbar: {} }, tolerance: {}, rules: {} };
    $$("#layoutRules [data-path]").forEach((inp) => {
      const parts = inp.dataset.path.split(".");
      let o = patch;
      for (let i = 0; i < parts.length - 1; i++) {
        o[parts[i]] = o[parts[i]] || {};
        o = o[parts[i]];
      }
      const last = parts[parts.length - 1];
      if (inp.type === "number") {
        const v = parseFloat(inp.value);
        if (Number.isNaN(v)) return;
        o[last] = v;
      } else {
        o[last] = inp.value;
      }
    });
    $$("#layoutRules [data-lay-rule]").forEach((cb) => {
      patch.rules[cb.dataset.layRule] = cb.checked;
    });
    const msg = $("#layoutMsg");
    try {
      state.layout = await api("/api/layout/rules", { method: "POST", body: { rules: patch } });
      renderLayoutRules();
      if (msg) msg.textContent = "已保存，下次三审生效";
      toast("排版要求已保存");
    } catch (e) {
      if (msg) msg.textContent = "保存失败：" + e.message;
    }
  }

  async function resetLayoutRules() {
    const msg = $("#layoutMsg");
    try {
      state.layout = await api("/api/layout/rules/reset", { method: "POST", body: {} });
      renderLayoutRules();
      if (msg) msg.textContent = "已恢复默认";
      toast("已恢复默认排版要求");
    } catch (e) {
      if (msg) msg.textContent = "恢复失败：" + e.message;
    }
  }

  function boot() {
    const save = $("#btnLayoutSave");
    if (save) save.addEventListener("click", saveLayoutRules);
    const reset = $("#btnLayoutReset");
    if (reset) reset.addEventListener("click", resetLayoutRules);
    // 打开规则库时顺带加载排版要求（app.js 里 openRules 是同一个按钮，用冒泡阶段补动作）
    const btn = $("#btnRules");
    if (btn) btn.addEventListener("click", () => setTimeout(loadLayoutRules, 50));
    // 深链：?layout=1 直接打开规则库面板便于排查
    try {
      if (new URLSearchParams(location.search).get("layout") === "1") {
        const drawer = $("#rulesDrawer");
        if (drawer) { drawer.classList.remove("is-hidden"); $("#drawerMask")?.classList.remove("is-hidden"); }
        loadLayoutRules();
      }
    } catch (e) { /* 忽略 */ }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
