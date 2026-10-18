import fs from "node:fs";

const html = fs.readFileSync("web/index.html", "utf8");
const js = fs.readFileSync("web/app.js", "utf8");

const idAttrs = new Set([...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]));
const used = new Set([...js.matchAll(/\$\$?\("#([A-Za-z0-9_-]+)"/g)].map((m) => m[1]));
const created = new Set(["verdictBox", "editAfter"]);
const missing = [...used].filter((x) => !idAttrs.has(x) && !created.has(x));

const clsUsed = new Set([...js.matchAll(/\$\$?\("\.([A-Za-z0-9_-]+)"/g)].map((m) => m[1]));
const clsInHtml = new Set([...html.matchAll(/class="([^"]+)"/g)].flatMap((m) => m[1].split(/\s+/)));
const missingCls = [...clsUsed].filter((c) => !clsInHtml.has(c));

// JS 里用到的类名（模板字符串中生成的）在 CSS 中是否存在
const css = fs.readFileSync("web/style.css", "utf8");
const cssClasses = new Set([...css.matchAll(/\.([A-Za-z][A-Za-z0-9_-]*)/g)].map((m) => m[1]));
const dyn = ["fcard", "badge", "badge-must", "badge-should", "badge-suggest", "badge-cat", "badge-ok",
  "sev-must", "sev-should", "sev-suggest", "status-tag", "status-accepted", "status-rejected",
  "status-pending", "chip", "done", "running", "error", "tab", "is-active", "is-rejected",
  "d-head", "d-quote", "d-context", "d-row", "d-label", "d-text", "diff", "box", "before", "after",
  "lbl", "d-actions", "spacer", "d-meta", "verdict", "vline", "notes", "banner", "path", "toast", "err",
  "issue-none"];
const missingCss = dyn.filter((c) => !cssClasses.has(c) && !css.includes("." + c));

console.log("html ids:", idAttrs.size, "| js id refs:", used.size, "| missing ids:", missing.length ? missing : "none");
console.log("js class refs:", [...clsUsed].join(","), "| missing in html:", missingCls.length ? missingCls : "none");
console.log("dynamic classes missing in css:", missingCss.length ? missingCss : "none");
if (missing.length || missingCls.length) process.exitCode = 1;
