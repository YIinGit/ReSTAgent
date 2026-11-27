/* ════════════════════════════════════════════════════════════════════════
   ReSTAgent · 可变字符 A（SVG 版）
   ────────────────────────────────────────────────────────────────────────
   为什么能用 SVG 了：
     上一版手描路径失败是因为**没有对齐字体度量**，不是 SVG 本身不行。
     现在所有几何量都来自无头浏览器实测（weight 600 / 词标字体栈）：

       capHeight = 0.703125 em
       A advance = 0.666016 em
       stem      = 0.117500 em      （"l" 的竖笔画实测）

   坐标约定（唯一不变量）
     viewBox 的 1 单位 = capHeight/100
     y =   0  → cap 顶
     y = 100  → 基线
     x 跨 0..94.72 → A 的 advance 宽度
   因此 SVG 盒子 = (0.666em × 0.703em)，底边贴基线，与右侧字母严丝合缝。

   形态：从「字」到「图」的 6 个阶段，每个都是对同一字母的一次重新构造。
   动效：每次换形用 stroke-dashoffset 把它**画出来**，而不是淡入淡出。
   ════════════════════════════════════════════════════════════════════════ */
var BrandMark = (function () {
  "use strict";

  /* ── 实测度量 → 绘图单位 ─────────────────────────────────────────── */
  var CAP_EM  = 0.703125;
  var ADV_EM  = 0.666016;
  var STEM_EM = 0.117500;

  var U       = 100 / CAP_EM;            // 每 em 多少绘图单位
  var VIEW_W  = ADV_EM * U;              // ≈ 94.72
  var STEM    = STEM_EM * U;             // ≈ 16.71
  var HALF    = STEM / 2;                // ≈ 8.36
  var CX      = VIEW_W / 2;              // ≈ 47.36
  var FOOT    = 100 - HALF;              // ≈ 91.64  基线侧收进半个笔画
  var APEX    = HALF;                    // ≈ 8.36   cap 侧收进半个笔画

  /* ── 6 种构造 ─────────────────────────────────────────────────────
     共用：foot y=91.6、apex y=8.4、x 0..94.7、圆头圆角
     差异：断口 / 曲率 / 是否正交 / 线宽 / 是否抽象
     ──────────────────────────────────────────────────────────────── */
  var VARIANTS = [
    {
      id: "bare", label: "刻线 A", w: STEM,
      d: "M8.4 91.6 L47.4 8.4 L86.4 91.6 M25.8 67.6 L68.9 67.6"
    },
    {
      id: "stencil", label: "断口 A", w: STEM,
      d: "M8.4 91.6 L47.4 8.4 L86.4 91.6 M23.4 67.6 L37.4 67.6 M57.4 67.6 L71.4 67.6"
    },
    {
      id: "arch", label: "拱形 A", w: STEM,
      d: "M8.4 91.6 L8.4 46 C8.4 22 26 8.4 47.4 8.4 C68.8 8.4 86.4 22 86.4 46 L86.4 91.6 M19.8 62 L75 62"
    },
    {
      id: "circuit", label: "正交 A", w: STEM,
      d: "M8.4 91.6 L8.4 30.6 L47.4 8.4 L86.4 30.6 L86.4 91.6 " +
         "M8.4 63.4 L31 63.4 M39 63.4 L56 63.4 M64 63.4 L86.4 63.4"
    },
    {
      id: "draft", label: "制图 A", w: STEM * 0.30,
      d: "M8.4 91.6 L47.4 8.4 L86.4 91.6 M8.4 91.6 L86.4 91.6 " +
         "M25.8 67.6 L68.9 67.6 M47.4 8.4 L47.4 100"
    },
    {
      id: "spark", label: "火花", w: STEM * 0.42,
      d: "M47.4 4 L47.4 96 M13 27.2 L81.8 72.8 M81.8 27.2 L13 72.8"
    }
  ];

  var MORPH_MS = 620;      // 画出来的时长
  var FADE_MS  = 210;      // 旧形态淡出

  var reduceMotion = false;
  try {
    reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch (e) { /* 老浏览器忽略 */ }

  var NS = "http://www.w3.org/2000/svg";

  function svgEl(tag, attrs) {
    var n = document.createElementNS(NS, tag);
    if (attrs) for (var k in attrs) if (attrs.hasOwnProperty(k)) n.setAttribute(k, attrs[k]);
    return n;
  }

  function mount(host, opts) {
    if (!host) return null;
    opts = opts || {};

    var interval = opts.interval || 3400;
    var hoverInterval = opts.hoverInterval || 1200;
    var cur = opts.startIndex || 0;

    host.textContent = "";

    /* 尺寸直接写进样式，保证与文字基线/字宽严丝合缝 */
    var svg = svgEl("svg", {
      viewBox: "0 0 " + VIEW_W.toFixed(3) + " 100",
      preserveAspectRatio: "xMidYMid meet",
      "aria-hidden": "true", focusable: "false",
      "class": "bm-svg"
    });
    svg.style.width = ADV_EM + "em";
    svg.style.height = CAP_EM + "em";
    svg.style.display = "block";
    svg.style.overflow = "visible";

    /* 两层 path 做交叉：a 是当前，b 是正在画出来的下一个 */
    var pa = svgEl("path", { "class": "bm-p bm-p-a", "pathLength": "1" });
    var pb = svgEl("path", { "class": "bm-p bm-p-b", "pathLength": "1" });

    /* apex 脉冲点：每次换形闪一下，作为"同步"信号 */
    /* apex 处的脉冲点。缩放用 SVG 自身 transform，基点就是圆心，
       这样无论父级是 em 还是 px，"圆"永远还是圆（之前用 CSS px 定位会退化成方块） */
    var DOT_R = STEM * 0.40;
    var dot = svgEl("circle", {
      "class": "bm-dot", cx: String(CX), cy: String(APEX),
      r: String(DOT_R), fill: "currentColor", opacity: "0"
    });

    svg.appendChild(pa);
    svg.appendChild(pb);
    svg.appendChild(dot);
    host.appendChild(svg);

    function apply(el, v) {
      el.setAttribute("d", v.d);
      el.style.strokeWidth = v.w;
    }
    function reset(el) {
      el.style.transition = "none";
      el.style.strokeDasharray = "none";
      el.style.strokeDashoffset = "0";
      el.style.opacity = "1";
    }

    apply(pa, VARIANTS[cur]);
    reset(pa);
    pb.style.opacity = "0";

    var busy = false, timer = null, done = null;

    function morphTo(i) {
      cur = ((i % VARIANTS.length) + VARIANTS.length) % VARIANTS.length;
      var v = VARIANTS[cur];

      if (reduceMotion) {
        apply(pa, v); reset(pa);
        pb.style.opacity = "0";
        return;
      }

      busy = true;
      apply(pb, v);

      /* 关键帧：先把 b 设为"完全未画出"，强制回流，再过渡到已画出 */
      pb.style.transition = "none";
      pb.style.strokeDasharray = "1";
      pb.style.strokeDashoffset = "1";
      pb.style.opacity = "1";
      void pb.getBoundingClientRect();

      /* eslint-disable no-unused-expressions */
      pb.style.transition =
        "stroke-dashoffset " + MORPH_MS + "ms cubic-bezier(.22,1,.36,1), " +
        "opacity 180ms linear";

      requestAnimationFrame(function () {
        pb.style.strokeDashoffset = "0";
      });

      /* 旧形态快速淡出 */
      pa.style.transition = "opacity " + FADE_MS + "ms linear";
      pa.style.opacity = "0";

      /* 脉冲：从小圆涨开再消散 —— 基点就是圆心，纯 SVG 变换 */
      dot.style.transition = "none";
      dot.setAttribute("opacity", "1");
      dot.setAttribute("transform", "translate(" + CX + " " + APEX + ") scale(.35) translate(" + (-CX) + " " + (-APEX) + ")");
      void dot.getBoundingClientRect();
      dot.style.transition = "transform 520ms cubic-bezier(.22,1,.36,1), opacity 520ms ease";
      dot.setAttribute("transform", "translate(" + CX + " " + APEX + ") scale(1.15) translate(" + (-CX) + " " + (-APEX) + ")");
      dot.style.opacity = "1";

      function finish() {
        if (!busy) return;
        busy = false;
        clearTimeout(done);
        done = null;
        /* b 的内容固化到 a，b 归零 */
        apply(pa, v); reset(pa);
        pb.style.opacity = "0";
        pb.style.strokeDasharray = "none";
        pb.style.strokeDashoffset = "0";
        dot.style.transition = "opacity 480ms ease, transform 480ms ease";
        dot.style.opacity = "0";
        dot.setAttribute("transform", "translate(" + CX + " " + APEX + ") scale(2.3) translate(" + (-CX) + " " + (-APEX) + ")");
      }
      done = setTimeout(finish, MORPH_MS + 40);
      svg.addEventListener("transitionend", function once(ev) {
        if (ev.propertyName === "stroke-dashoffset") {
          svg.removeEventListener("transitionend", once);
          finish();
        }
      });
    }

    function swap() {
      if (busy) return;
      morphTo(cur + 1);
    }

    function start(ms) {
      stop();
      if (reduceMotion) return;
      timer = setInterval(swap, ms || interval);
    }
    function stop() {
      if (timer) { clearInterval(timer); timer = null; }
    }

    var hoverHost = host.closest ? (host.closest(".wordmark") || host) : host;
    hoverHost.addEventListener("mouseenter", function () { if (!reduceMotion) start(hoverInterval); });
    hoverHost.addEventListener("mouseleave", function () { if (!reduceMotion) start(interval); });

    document.addEventListener("visibilitychange", function () {
      if (document.hidden) stop(); else start(interval);
    });

    start(interval);

    return {
      swap: swap,
      start: function () { start(interval); },
      stop: stop,
      setShape: function (i) {
        if (done) { clearTimeout(done); done = null; }
        busy = false;
        apply(pa, VARIANTS[((i % VARIANTS.length) + VARIANTS.length) % VARIANTS.length]);
        cur = ((i % VARIANTS.length) + VARIANTS.length) % VARIANTS.length;
        reset(pa);
        pb.style.opacity = "0";
        dot.style.opacity = "0";
        dot.setAttribute("opacity", "0");
      },
      current: function () { return VARIANTS[cur].id; },
      index: function () { return cur; }
    };
  }

  return { mount: mount, VARIANTS: VARIANTS, METRICS: { CAP_EM: CAP_EM, ADV_EM: ADV_EM, STEM_EM: STEM_EM, VIEW_W: VIEW_W, STEM: STEM } };
})();

if (typeof window !== "undefined") window.BrandMark = BrandMark;
