"use strict";
// The details sheet. Everything in it came from a model, so nothing in it is ever run: markdown
// is rendered with raw HTML off and then sanitized to a short list of tags, code is highlighted
// from its text, and charts are drawn from numbers alone.

const host = window.canvasHost;
const $ = (id) => document.getElementById(id);
const scrollEl = $("scroll");
const innerEl = $("inner");
const blocksEl = $("blocks");
const writingEl = $("writing");
const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");

// The categorical palette: the deck's own cyan, amber, mint and red first, then four more, in a
// fixed order. A series never gets a colour outside it.
const PALETTE = ["#35e0ff", "#ffb547", "#4df7c4", "#ff5c6c", "#9d8cff", "#ff8fd1", "#3d8bff", "#d7f36b"];
const INK = "rgba(232,251,255,0.96)", INK2 = "rgba(224,245,255,0.82)", INK3 = "rgba(224,245,255,0.6)";
const GRID = "rgba(255,255,255,0.08)", AXIS = "rgba(255,255,255,0.18)", SURFACE = "rgba(8,24,34,1)";
if (!host.isMac) document.body.classList.add("win");

const EASE_OUT = "cubic-bezier(0.2, 0.8, 0.2, 1)";

// ---------------------------------------------------------------- safe markdown

const md = window.markdownit({ html: false, linkify: false, typographer: true, breaks: false });
md.disable(["image"]);
md.renderer.rules.link_open = () => '<span class="link">';
md.renderer.rules.link_close = () => "</span>";
md.renderer.rules.fence = (tokens, i) => `<pre class="inline-code-block"><code>${highlight(tokens[i].content, tokens[i].info.trim().split(/\s+/)[0])}</code></pre>`;
md.renderer.rules.code_block = (tokens, i) => `<pre class="inline-code-block"><code>${escapeHTML(tokens[i].content)}</code></pre>`;

const PURIFY = {
  ALLOWED_TAGS: ["p", "br", "strong", "b", "em", "i", "code", "pre", "ul", "ol", "li", "blockquote", "table", "thead",
    "tbody", "tr", "th", "td", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "span", "del", "s", "sup", "sub"],
  ALLOWED_ATTR: ["class", "style", "start"],
  ALLOW_DATA_ATTR: false,
  ALLOW_ARIA_ATTR: false,
};
const CLASS_TOKEN = /^(?:link|inline-code-block|hljs(?:-[a-z_]+)?|[a-z]+_|language-[\w+#.-]+)$/;
window.DOMPurify.addHook("uponSanitizeAttribute", (_node, data) => {
  const v = String(data.attrValue || "").trim();
  if (data.attrName === "style") data.keepAttr = /^text-align:\s*(left|right|center);?$/i.test(v);
  else if (data.attrName === "class") data.keepAttr = v.split(/\s+/).every((t) => CLASS_TOKEN.test(t));
  else if (data.attrName === "start") data.keepAttr = /^\d{1,4}$/.test(v);
});
const clean = (html) => window.DOMPurify.sanitize(html, PURIFY);

function escapeHTML(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function highlight(text, lang) {
  const hl = window.hljs;
  try {
    if (lang && hl.getLanguage(lang)) return hl.highlight(text, { language: lang, ignoreIllegals: true }).value;
    if (!lang && text.length < 6000) return hl.highlightAuto(text).value;
  } catch (_) {}
  return escapeHTML(text);
}

const NUMERIC = /^[-+−]?[$€£]?\d[\d,]*(?:\.\d+)?\s?(?:%|[a-zA-Z]{1,3})?$/;

function finishProse(root) {
  // Tables scroll sideways in their own glass frame, and number columns line up on the right.
  for (const table of root.querySelectorAll("table")) {
    const wrap = document.createElement("div");
    wrap.className = "table-wrap";
    table.replaceWith(wrap);
    wrap.append(table);
    alignNumbers(table);
  }
  for (const ol of root.querySelectorAll("ol[start]")) ol.style.counterReset = `n ${parseInt(ol.getAttribute("start"), 10) - 1}`;
}

function alignNumbers(table) {
  const rows = [...table.querySelectorAll("tbody tr")];
  const head = [...table.querySelectorAll("thead th")];
  const cols = Math.max(head.length, ...rows.map((r) => r.children.length));
  for (let c = 0; c < cols; c++) {
    const cells = rows.map((r) => r.children[c]).filter(Boolean);
    const texts = cells.map((td) => td.textContent.trim()).filter((t) => t && t !== "-" && t !== "—");
    if (texts.length && texts.every((t) => NUMERIC.test(t)) && !cells.some((td) => td.style.textAlign)) {
      for (const td of cells) td.classList.add("num");
      head[c]?.classList.add("num");
    }
  }
}

function prose(src, inline) {
  const div = document.createElement("div");
  div.className = "prose";
  div.innerHTML = clean(inline ? md.renderInline(String(src)) : md.render(String(src)));
  finishProse(div);
  return div;
}

function inlineInto(el, src) {
  el.innerHTML = clean(md.renderInline(String(src ?? "")));
}

// ---------------------------------------------------------------- blocks

const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};

const LANG_NAMES = {
  py: "Python", python: "Python", js: "JavaScript", javascript: "JavaScript", mjs: "JavaScript", ts: "TypeScript",
  typescript: "TypeScript", tsx: "TSX", jsx: "JSX", sh: "Shell", bash: "Shell", zsh: "Shell", shell: "Shell",
  console: "Shell", json: "JSON", sql: "SQL", html: "HTML", xml: "XML", css: "CSS", go: "Go", rust: "Rust", rs: "Rust",
  swift: "Swift", ps1: "PowerShell", powershell: "PowerShell", yaml: "YAML", yml: "YAML", md: "Markdown",
  markdown: "Markdown", c: "C", cpp: "C++", "c++": "C++", cs: "C#", csharp: "C#", java: "Java", kotlin: "Kotlin",
  ruby: "Ruby", rb: "Ruby", php: "PHP", diff: "Diff", ini: "INI", toml: "TOML", dockerfile: "Dockerfile", lua: "Lua",
};

const ICONS = {
  info: '<svg viewBox="0 0 18 18" width="18" height="18" aria-hidden="true"><circle cx="9" cy="9" r="7.25" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M9 8.2v4.3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><circle cx="9" cy="5.6" r="1" fill="currentColor"/></svg>',
  good: '<svg viewBox="0 0 18 18" width="18" height="18" aria-hidden="true"><circle cx="9" cy="9" r="7.25" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M5.8 9.2l2.2 2.2 4.2-4.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  warn: '<svg viewBox="0 0 18 18" width="18" height="18" aria-hidden="true"><path d="M9 2.3l7 12.6H2z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M9 7.2v3.6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/><circle cx="9" cy="12.8" r="0.95" fill="currentColor"/></svg>',
  bad: '<svg viewBox="0 0 18 18" width="18" height="18" aria-hidden="true"><circle cx="9" cy="9" r="7.25" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M6.4 6.4l5.2 5.2M11.6 6.4l-5.2 5.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
};

function renderBlock(b) {
  switch (b.type) {
    case "heading": {
      const wrap = el("div", "b-heading");
      wrap.append(el(b.level === 3 ? "h3" : "h2", null, b.text));
      return wrap;
    }
    case "text":
      return prose(b.md);
    case "list": {
      const wrap = el("div", "prose");
      const list = el(b.ordered ? "ol" : "ul");
      for (const item of b.items || []) {
        const li = el("li");
        inlineInto(li, item);
        list.append(li);
      }
      wrap.append(list);
      return wrap;
    }
    case "table":
      return renderTable(b.columns || [], b.rows || [], b.align || []);
    case "code":
      return renderCode(b);
    case "chart":
      return renderChart(b);
    case "stats": {
      const wrap = el("div", "b-stats");
      for (const it of b.items || []) {
        const tile = el("div", "stat");
        tile.append(el("div", "value", it.value));
        if (it.label) tile.append(el("div", "label", it.label));
        if (it.note) tile.append(el("div", "note", it.note));
        wrap.append(tile);
      }
      return wrap;
    }
    case "callout": {
      const tone = ["info", "good", "warn", "bad"].includes(b.tone) ? b.tone : "info";
      const wrap = el("div", "b-callout " + tone);
      wrap.innerHTML = ICONS[tone];    // a fixed icon of ours, never model text
      wrap.append(prose(b.md));
      return wrap;
    }
    case "divider":
      return el("hr", "b-divider");
    default:
      return el("div");
  }
}

function renderTable(columns, rows, align) {
  const wrap = el("div", "table-wrap");
  const table = el("table");
  if (columns.length) {
    const tr = el("tr");
    columns.forEach((c, i) => {
      const th = el("th");
      inlineInto(th, c);
      if (align[i]) th.style.textAlign = align[i];
      tr.append(th);
    });
    const thead = el("thead");
    thead.append(tr);
    table.append(thead);
  }
  const tbody = el("tbody");
  for (const row of rows) {
    const tr = el("tr");
    row.forEach((cell, i) => {
      const td = el("td", "cell");
      inlineInto(td, cell);
      if (align[i]) td.style.textAlign = align[i];
      tr.append(td);
    });
    tbody.append(tr);
  }
  table.append(tbody);
  wrap.append(table);
  alignNumbers(table);
  return wrap;
}

function renderCode(b) {
  const wrap = el("div", "b-code");
  const head = el("div", "code-head");
  const lang = String(b.lang || "").toLowerCase();
  head.append(el("span", "lang", LANG_NAMES[lang] || (lang ? lang.toUpperCase() : "Code")));
  const copy = el("button", "chip-button", "Copy");
  copy.type = "button";
  copy.addEventListener("click", () => {
    host.copy(b.text);
    copy.textContent = "Copied";
    setTimeout(() => { copy.textContent = "Copy"; }, 1400);
  });
  head.append(copy);
  const pre = el("pre");
  const code = el("code", "hljs");
  code.innerHTML = clean(highlight(b.text || "", window.hljs.getLanguage(lang) ? lang : ""));
  pre.append(code);
  wrap.append(head, pre);
  return wrap;
}

// ---------------------------------------------------------------- charts

window.Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
window.Chart.defaults.font.size = 13;
window.Chart.defaults.color = INK3;

function compact(v) {
  if (v == null || !Number.isFinite(v)) return "";
  const a = Math.abs(v);
  const trim = (n) => n.toFixed(1).replace(/\.0$/, "");
  if (a >= 1e9) return trim(v / 1e9) + "B";
  if (a >= 1e6) return trim(v / 1e6) + "M";
  if (a >= 1e4) return trim(v / 1e3) + "K";
  return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function withUnit(v, unit) {
  const n = Number.isFinite(v) ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : "";
  if (!unit) return n;
  return unit === "%" || unit === "x" ? n + unit : unit === "$" ? "$" + n : `${n} ${unit}`;
}

function renderChart(b) {
  const wrap = el("div", "b-chart");
  const head = el("div", "chart-head");
  const title = el("div", "chart-title", b.title || "");
  if (b.unit) title.append(el("span", "unit", b.unit));
  head.append(title);
  const toggle = el("button", "chip-button", "Show Data");
  toggle.type = "button";
  head.append(toggle);
  const round = b.kind === "pie" || b.kind === "donut";
  const box = el("div", "chart-box" + (round ? " round" : ""));
  if (b.horizontal) box.style.height = `${Math.max(180, Math.min(640, b.labels.length * 30 + 60))}px`;
  const data = el("div", "chart-data");
  data.hidden = true;
  const names = round ? [b.series[0].name || "Value"] : b.series.map((s, i) => s.name || (b.series.length > 1 ? `Series ${i + 1}` : "Value"));
  data.append(renderTable(["", ...names], b.labels.map((label, i) =>
    [label, ...b.series.map((s) => (s.data[i] == null ? "" : withUnit(s.data[i], b.unit)))]), []));
  toggle.addEventListener("click", () => {
    data.hidden = !data.hidden;
    toggle.textContent = data.hidden ? "Show Data" : "Hide Data";
  });
  wrap.append(head, box, data);
  wrap._chartSpec = b;
  wrap._chartBox = box;
  return wrap;
}

function drawChart(wrap) {
  const b = wrap._chartSpec;
  if (!b || wrap._chart) return;
  const canvas = document.createElement("canvas");
  wrap._chartBox.append(canvas);
  const round = b.kind === "pie" || b.kind === "donut";
  const multi = b.series.length > 1;
  const name = (s, i) => s.name || (multi ? `Series ${i + 1}` : b.title || "Value");
  // Bars stay at most 24px thick, and the bars of one group sit together with a 4px gap
  // rather than spread across the whole slot.
  let groupShare = 0.6;
  if (b.kind === "bar" && !b.stacked) {
    const span = (b.horizontal ? wrap._chartBox.clientHeight : wrap._chartBox.clientWidth) - 56;
    const per = span / Math.max(1, b.labels.length);
    groupShare = Math.min(0.8, Math.max(0.1, (b.series.length * 28) / Math.max(per, 1)));
  }
  const datasets = round
    ? [{ label: name(b.series[0], 0), data: b.series[0].data, backgroundColor: b.labels.map((_, i) => PALETTE[i % PALETTE.length]),
      borderColor: SURFACE, borderWidth: 2, hoverOffset: 6 }]
    : b.series.map((s, i) => (b.kind === "line"
      ? { label: name(s, i), data: s.data, borderColor: PALETTE[i], backgroundColor: PALETTE[i], borderWidth: 2,
        pointRadius: 4, pointHoverRadius: 6, pointBorderWidth: 2, pointBorderColor: SURFACE, pointHoverBorderColor: SURFACE,
        tension: 0.32, spanGaps: true, borderCapStyle: "round", borderJoinStyle: "round" }
      : { label: name(s, i), data: s.data, backgroundColor: PALETTE[i], hoverBackgroundColor: PALETTE[i],
        borderRadius: 4, borderSkipped: "start", maxBarThickness: 24, categoryPercentage: groupShare, barPercentage: 24 / 28 }));
  const value = b.horizontal ? "x" : "y", category = b.horizontal ? "y" : "x";
  const scales = round ? {} : {
    [category]: { stacked: b.stacked, grid: { display: false }, border: { color: AXIS },
      ticks: { color: INK3, padding: 8, maxRotation: 0, autoSkip: true, font: { size: 12 } } },
    [value]: { stacked: b.stacked, beginAtZero: b.kind === "bar", grid: { color: GRID, drawTicks: false },
      border: { display: false }, ticks: { color: INK3, padding: 10, maxTicksLimit: 6, callback: (v) => compact(v) } },
  };
  wrap._chart = new window.Chart(canvas, {
    type: round ? (b.kind === "donut" ? "doughnut" : "pie") : b.kind,
    data: { labels: b.labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: b.horizontal ? "y" : "x",
      cutout: b.kind === "donut" ? "62%" : 0,
      animation: reduced.matches ? false : {
        duration: 950, easing: "easeOutQuart",
        delay: (ctx) => (ctx.type === "data" && ctx.mode === "default" ? ctx.dataIndex * 40 + ctx.datasetIndex * 90 : 0),
      },
      layout: { padding: { top: 2, right: 6, bottom: 0, left: 0 } },
      interaction: b.kind === "line" ? { mode: "index", intersect: false } : { mode: "nearest", intersect: true },
      plugins: {
        legend: {
          display: round || multi, position: "top", align: "start",
          labels: { color: INK2, usePointStyle: true, pointStyle: "circle", boxWidth: 8, boxHeight: 8, padding: 16, font: { size: 12.5 } },
        },
        tooltip: {
          backgroundColor: "rgba(16,18,26,0.94)", borderColor: "rgba(255,255,255,0.14)", borderWidth: 1,
          cornerRadius: 10, padding: 10, titleColor: INK, bodyColor: INK2, titleFont: { weight: "600" },
          usePointStyle: true, boxPadding: 5,
          callbacks: {
            label: (ctx) => {
              const v = round ? ctx.parsed : ctx.parsed[value];
              if (round) {
                const total = ctx.dataset.data.reduce((a, x) => a + (x || 0), 0);
                return ` ${ctx.label}: ${withUnit(v, b.unit)} (${Math.round((v / total) * 100)}%)`;
              }
              return ` ${ctx.dataset.label}: ${withUnit(v, b.unit)}`;
            },
          },
        },
      },
      scales,
    },
  });
}

function destroyCharts(node) {
  for (const w of [node, ...node.querySelectorAll?.(".b-chart") || []]) {
    if (w._chart) { w._chart.destroy(); w._chart = null; }
  }
}

// ---------------------------------------------------------------- filling the sheet

let current = null;        // { id }
let closing = false;
let entering = 0;          // blocks added in the current reveal, for the stagger

function reveal(node, animate) {
  const delay = animate ? 140 + Math.min(entering++, 10) * 55 : 0;
  if (animate && !reduced.matches) {
    node.animate([{ opacity: 0, transform: "translateY(14px)" }, { opacity: 1, transform: "none" }],
      { duration: 480, delay, easing: EASE_OUT, fill: "backwards" });
  }
  const charts = node.classList.contains("b-chart") ? [node] : [...node.querySelectorAll(".b-chart")];
  if (charts.length) setTimeout(() => charts.forEach(drawChart), reduced.matches ? 0 : delay + 120);
}

function setBlocks(blocks, animate) {
  const kids = [...blocksEl.children];
  entering = 0;
  blocks.forEach((b, i) => {
    const sig = JSON.stringify(b);
    const old = kids[i];
    if (old && old.dataset.sig === sig) return;
    const node = renderBlock(b);
    node.dataset.sig = sig;
    if (old) {
      // The same block, grown since: swapped in place, without moving anything around it.
      destroyCharts(old);
      old.replaceWith(node);
      reveal(node, false);
    } else {
      blocksEl.append(node);
      reveal(node, animate);
    }
  });
  for (const extra of kids.slice(blocks.length)) { destroyCharts(extra); extra.remove(); }
}

function fill(p, animate) {
  $("title").textContent = p.title || "";
  $("subtitle").textContent = p.subtitle || "";
  $("asked").textContent = p.question || "";
  $("said").textContent = p.said || "";
  writingEl.hidden = !p.partial;
  setBlocks(Array.isArray(p.blocks) ? p.blocks : [], animate);
}

function clear() {
  for (const a of document.getAnimations()) a.cancel();
  destroyCharts(blocksEl);
  for (const n of [...blocksEl.children]) destroyCharts(n);
  blocksEl.replaceChildren();
  document.body.classList.remove("speaking");
  $("state").textContent = "";
  current = null;
}

function openSheet(p) {
  clear();
  closing = false;
  current = { id: p.id };
  fill(p, true);
  scrollEl.scrollTop = 0;
  if (reduced.matches) return;
  // The glass is the window's and fades with it; the contents settle in under it.
  innerEl.animate([{ opacity: 0, transform: "translateY(10px)" }, { opacity: 1, transform: "none" }],
    { duration: 360, easing: EASE_OUT });
}

function update(p) {
  if (!current) return openSheet(p);
  if (p.id === current.id) return fill(p, true);
  // A new canvas over the one up: the contents cross over, the glass stays.
  current = { id: p.id };
  const out = reduced.matches ? null : scrollEl.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 140, fill: "forwards" });
  const swap = () => {
    for (const n of [...blocksEl.children]) destroyCharts(n);
    blocksEl.replaceChildren();
    fill(p, true);
    scrollEl.scrollTop = 0;
    out?.cancel();
    if (!reduced.matches) scrollEl.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 220, easing: "ease-out" });
  };
  out ? out.finished.then(swap) : swap();
}

function closeSheet(by, here) {
  if (closing || !current) return;
  closing = true;
  if (here) host.closing(by);
  const done = () => { clear(); host.closed(); };
  if (reduced.matches) return done();
  innerEl.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 180, easing: "ease-in", fill: "forwards" })
    .finished.then(done, done);
}

// ---------------------------------------------------------------- from the app

host.onShow((p) => openSheet(p));
host.onUpdate((p) => update(p));
host.onClose((by) => closeSheet(by, false));
host.onState((st) => {
  const speaking = !!(st && st.speaking && current && st.id === current.id);
  document.body.classList.toggle("speaking", speaking);
  $("state").textContent = speaking ? "Answering" : "";
});

$("close").addEventListener("click", () => closeSheet("button", true));
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet("esc", true); });

// The sheet is as tall as what is on it, up to the room the deck gives it.
new ResizeObserver(() => {
  const cs = getComputedStyle(scrollEl);
  host.height(innerEl.offsetHeight + parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom));
}).observe(innerEl);

host.ready();
