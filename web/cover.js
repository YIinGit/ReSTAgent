/* ReSTAgent 封面交互
 *
 * 设计约束（与 cover.css 一致）：
 *   · 只对 transform / opacity 做动画，不触发重排
 *   · 光晕跟随鼠标：只写 CSS 变量，走合成层
 *   · 进入时先淡出再跳转，不用白屏
 */
(function () {
  "use strict";

  var cover = document.getElementById("cover");
  if (!cover) return;

  /* ---------- 可变字符 A：自动换形 + 悬停加速 ---------- */
  var mark = null;
  try {
    if (window.BrandMark) {
      mark = BrandMark.mount(document.getElementById("wmGlyph"), {
        interval: 2800,
        hoverInterval: 950
      });
    }
  } catch (e) { /* 字标失败不影响进入 */ }

  /* ---------- 深浅色 ---------- */
  var THEME_KEY = "restagent.theme";
  var btn = document.getElementById("btnCoverTheme");
  var label = document.getElementById("coverThemeLabel");
  function applyTheme(theme, persist) {
    document.documentElement.dataset.theme = theme;
    if (label) label.textContent = theme === "dark" ? "浅色" : "深色";
    if (persist) { try { localStorage.setItem(THEME_KEY, theme); } catch (e) { } }
  }
  var saved = null;
  try { saved = localStorage.getItem(THEME_KEY); } catch (e) { saved = null; }
  applyTheme(saved === "light" ? "light" : "dark", false);
  btn?.addEventListener("click", function () {
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark", true);
  });

  /* ---------- 光晕跟随 ---------- */
  var raf = 0;
  cover.addEventListener("pointermove", function (e) {
    if (raf) return;
    raf = requestAnimationFrame(function () {
      raf = 0;
      cover.style.setProperty("--mx", e.clientX + "px");
      cover.style.setProperty("--my", e.clientY + "px");
    });
  });

  /* ---------- 进入：淡出 → 跳转 ---------- */
  var leaving = false;
  function enter(href) {
    if (leaving) return;
    leaving = true;
    cover.dataset.state = "leaving";
    cover.classList.add("is-leaving");
    if (mark && mark.stop) mark.stop();
    setTimeout(function () {
      cover.classList.add("is-done");
      location.href = href;
    }, 420);
  }

  cover.addEventListener("click", function (e) {
    var row = e.target.closest("[data-go]");
    if (!row) return;
    e.preventDefault();
    enter(row.getAttribute("href") || "/app");
  });

  /* 键盘：↑↓ 选择，Enter 进入 */
  var rows = Array.prototype.slice.call(document.querySelectorAll("[data-go]"));
  var cur = -1;
  function focusRow(i) {
    if (!rows.length) return;
    cur = (i + rows.length) % rows.length;
    rows.forEach(function (r, k) { r.classList.toggle("is-focus", k === cur); });
    rows[cur].focus({ preventScroll: true });
  }
  document.addEventListener("keydown", function (e) {
    if (e.isComposing || leaving) return;
    if (e.key === "ArrowDown" || e.key === "j") { e.preventDefault(); focusRow(cur + 1); }
    else if (e.key === "ArrowUp" || e.key === "k") { e.preventDefault(); focusRow(cur - 1); }
    else if (e.key === "Enter" && cur >= 0) {
      e.preventDefault();
      enter(rows[cur].getAttribute("href") || "/app");
    }
  });

  /* 首帧动画结束后标记 ready，供 CSS 使用 */
  cover.addEventListener("animationend", function (e) {
    if (e.animationName === "footIn") cover.dataset.state = "ready";
  });
  setTimeout(function () {
    if (!cover.dataset.state || cover.dataset.state === "idle") cover.dataset.state = "ready";
  }, 1600);
})();
