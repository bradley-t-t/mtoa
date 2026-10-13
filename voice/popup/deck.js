"use strict";
// The deck. A transparent layer over the screen draws the clock, the reactor and what is being
// said; every panel beside it is its own window with the system's glass behind it, written into
// from here. This page owns the connection to Sunday and everything the panels show, so the
// panels carry no script of their own.
//
// Two modes. The deck: conversation on the left, systems and work on the right, the reactor in
// the middle and the dock under it. Focus: everything folds away but a small bar at the top
// while Sunday is listening or answering, and a few notes at the top right.

const api = window.sunday;
const isMac = !!api.isMac;
const $ = (id) => document.getElementById(id);
const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
if (!isMac) document.body.classList.add("win");

const KEYS = isMac
  ? { show: ["Control", "Option", "Shift", "O"], mode: ["Control", "Option", "Shift", "D"] }
  : { show: ["Ctrl", "Alt", "Shift", "O"], mode: ["Ctrl", "Alt", "Shift", "D"] };

const MAX_CARDS = 40;
const STATUS_MIN_MS = 700;          // a status line stays this long before the next replaces it
const ANSWER_LINGER_MS = 45000;     // an answer stays in the middle of the deck this long
const FOCUS_LINGER_MS = 9000;       // and in the focus bar this long
const TASK_DONE_MS = 4000;          // finished work stays in the list this long
const TOAST_MS = 9000;
const STALE_FEED_MS = 15 * 60000;
const SETTLE_MS = 3000;
const LOST_AFTER_MS = 20000;
const SILENT_AFTER_MS = 35000;
const UNREACHABLE_AFTER_MS = 4000;

// ---------------------------------------------------------------- small helpers

const ICONS = {
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21M8.5 21h7"/>',
  micOff: '<path d="M15 10V6a3 3 0 0 0-5.7-1.3M9 9v2a3 3 0 0 0 4.6 2.5M5.5 11a6.5 6.5 0 0 0 10.6 5M18.5 11c0 .9-.2 1.8-.5 2.6M12 17.5V21M8.5 21h7M4 4l16 16"/>',
  voice: '<path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 10.5v3"/>',
  details: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M3.5 9h17M9 9v10.5"/>',
  focus: '<path d="M4 8.5V6a2 2 0 0 1 2-2h2.5M15.5 4H18a2 2 0 0 1 2 2v2.5M20 15.5V18a2 2 0 0 1-2 2h-2.5M8.5 20H6a2 2 0 0 1-2-2v-2.5"/><circle cx="12" cy="12" r="2.5"/>',
  deck: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M8.5 4.5v15M15.5 4.5v15"/>',
  pointer: '<path d="M5.5 4.5l12.5 6-5.4 1.8-1.9 5.4z"/><path d="M12.6 12.3l4.6 4.6"/>',
  sliders: '<path d="M4 6.5h10M18 6.5h2M4 12h3M11 12h9M4 17.5h8M16 17.5h4"/><circle cx="16" cy="6.5" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="14" cy="17.5" r="2"/>',
};
const icon = (name) => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;

function make(doc, tag, cls, text) {
  const e = doc.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function setText(el, text) {
  text = text ?? "";
  if (el.textContent !== text) el.textContent = text;
}

const pad2 = (n) => String(n).padStart(2, "0");
const timeOf = (ms) => new Date(ms).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

function ago(ms) {
  const s = Math.max(0, (Date.now() - ms) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function lasted(ms) {
  const s = Math.floor(Math.max(0, ms) / 1000);
  const hh = Math.floor(s / 3600), mm = Math.floor(s / 60) % 60;
  return (hh ? `${hh}:${pad2(mm)}` : String(mm)) + ":" + pad2(s % 60);
}

function keyCaps(doc, keys, text) {
  const hint = make(doc, "span", "hint");
  for (const k of keys) hint.append(make(doc, "span", "key", k));
  hint.append(make(doc, "span", null, text));
  return hint;
}

// ---------------------------------------------------------------- the reactor

const BARS = 72;

function reactorSVG(mini) {
  const C = 180, f = (n) => n.toFixed(2);
  const pt = (r, a) => [C + r * Math.sin((a * Math.PI) / 180), C - r * Math.cos((a * Math.PI) / 180)];
  const arc = (r, a0, a1) => {
    const [x0, y0] = pt(r, a0), [x1, y1] = pt(r, a1);
    return `M${f(x0)} ${f(y0)}A${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${f(x1)} ${f(y1)}`;
  };
  const spoke = (r0, r1, a) => {
    const [x0, y0] = pt(r0, a), [x1, y1] = pt(r1, a);
    return `M${f(x0)} ${f(y0)}L${f(x1)} ${f(y1)}`;
  };
  const tri = (a) => {
    const [x0, y0] = pt(158, a - 3.2), [x1, y1] = pt(158, a + 3.2), [x2, y2] = pt(147, a);
    return `M${f(x0)} ${f(y0)}L${f(x1)} ${f(y1)}L${f(x2)} ${f(y2)}Z`;
  };
  const k = mini ? 2.4 : 1;
  let segs = "";
  for (const [a0, a1] of [[10, 80], [100, 170], [190, 260], [280, 350]]) segs += arc(144, a0, a1);
  let dashes = "";
  for (let a = 0; a < 360; a += 30) dashes += arc(102, a + 4, a + 22);
  if (mini) {
    return `<svg viewBox="0 0 360 360" fill="none" stroke="currentColor" stroke-linecap="round" aria-hidden="true">
      <circle cx="180" cy="180" r="172" stroke-opacity=".35" stroke-width="${k * 1.2}"/>
      <g class="r-seg"><path d="${segs}" stroke-width="${k * 5}" stroke-linecap="butt"/></g>
      <g class="r-inner"><path d="${dashes}" stroke-width="${k * 2.4}" stroke-opacity=".85"/></g>
      <g class="r-orbit"><circle cx="180" cy="42" r="12" fill="currentColor" stroke="none"/></g>
      <circle cx="180" cy="180" r="34" stroke-width="${k * 2.4}"/>
      <circle class="r-core" cx="180" cy="180" r="16" stroke="none"/>
      <path class="r-slash" d="M110 250L250 110" stroke-width="${k * 5}"/>
    </svg>`;
  }
  let ticks = "";
  for (let a = 0; a < 360; a += 6) ticks += spoke(a % 30 ? 162 : 155, 168, a);
  let inner = "";
  for (let a = 0; a < 360; a += 15) inner += spoke(120, a % 45 ? 124 : 129, a);
  let bars = "";
  for (let i = 0; i < BARS; i++) {
    const [x0, y0] = pt(36, (i * 360) / BARS), [x1, y1] = pt(46, (i * 360) / BARS);
    bars += `<line x1="${f(x0)}" y1="${f(y0)}" x2="${f(x1)}" y2="${f(y1)}"/>`;
  }
  return `<svg viewBox="0 0 360 360" fill="none" stroke="currentColor" stroke-linecap="round" aria-hidden="true">
    <circle cx="180" cy="180" r="174" stroke-opacity=".24" stroke-width="1"/>
    <g class="r-spin"><path d="${ticks}" stroke-opacity=".55" stroke-width="1.2"/></g>
    <g class="r-seg"><path d="${segs}" stroke-width="5" stroke-linecap="butt"/><path d="${tri(0) + tri(120) + tri(240)}" fill="currentColor" stroke="none"/></g>
    <circle cx="180" cy="180" r="133" stroke-opacity=".3" stroke-width="1"/>
    <g class="r-inner"><path d="${inner}" stroke-opacity=".6" stroke-width="1.2"/><path d="${dashes}" stroke-width="2.4" stroke-opacity=".85"/></g>
    <path class="r-acc" d="${arc(124, 228, 262)}" stroke-width="2.4"/>
    <g class="r-orbit"><circle cx="180" cy="44" r="4" fill="currentColor" stroke="none"/></g>
    <g class="r-bars" stroke-width="2" stroke-opacity=".9">${bars}</g>
    <circle cx="180" cy="180" r="27" stroke-width="2"/>
    <circle class="r-core" cx="180" cy="180" r="11" stroke="none"/>
    <path class="r-slash" d="M118 242L242 118" stroke-width="5"/>
  </svg>`;
}

const reactorEl = $("reactor");
reactorEl.innerHTML = reactorSVG(false);
const spokes = [...reactorEl.querySelectorAll(".r-bars line")].map((el, i) => {
  const a = (i * 2 * Math.PI) / BARS;
  return { el, s: Math.sin(a), c: Math.cos(a), phase: (i * 2.399) % (Math.PI * 2) };
});
const waveEl = $("wave");
const waveBars = [];
for (let i = 0; i < 96; i++) {
  const bar = document.createElement("i");
  waveEl.append(bar);
  waveBars.push({ el: bar, phase: (i * 1.7) % (Math.PI * 2), env: 0.3 + 0.7 * Math.sin((Math.PI * i) / 95) });
}

// A level that moves like speech: two slow waves against each other per bar, so neighbours
// rise and fall together and the whole thing breathes rather than flickers. Drawn only while
// something is being heard or said; nothing on screen claims a level it was never sent.
const level = (phase, t) => 0.5 + 0.5 * Math.sin(t * 0.0061 + phase) * Math.cos(t * 0.0023 + phase * 0.6);

let animating = false;
function animate() {
  if (animating) return;
  animating = true;
  requestAnimationFrame(frame);
}

function frame(t) {
  const kind = live.kind;
  const on = (kind === "listening" || kind === "speaking") && ui.mode === "deck" && !detailsOpen() && !reduced.matches;
  const amp = kind === "speaking" ? 30 : 20;
  for (const b of spokes) {
    const r = 46 + (on ? amp * level(b.phase, t) : 2);
    b.el.setAttribute("x2", (180 + r * b.s).toFixed(1));
    b.el.setAttribute("y2", (180 - r * b.c).toFixed(1));
  }
  for (const b of waveBars) b.el.style.transform = `scaleY(${on ? (0.08 + 0.92 * level(b.phase, t) * b.env).toFixed(3) : 0.06})`;
  if (on) requestAnimationFrame(frame);
  else animating = false;
}

// ---------------------------------------------------------------- the glass panels

// Each panel is a window of its own, opened blank from this page and written into from here.
// Its height follows its content; this page measures it and tells the app where it goes.
const panels = new Map();
const loadMark = Date.now().toString(36);

function openPanel(name, cls) {
  const w = window.open("", `${name}@${loadMark}`);
  if (!w) return null;
  const d = w.document;
  d.open();
  d.write(`<!doctype html><html><head><meta charset="utf-8"><base href="${document.baseURI}">`
    + `<link rel="stylesheet" href="deck.css"><title>Sunday</title></head>`
    + `<body class="glass ${cls}${isMac ? "" : " win"}"><div class="rim"></div><div class="root"></div></body></html>`);
  d.close();
  const p = { name, w, d, root: d.querySelector(".root"), placed: undefined };
  new w.ResizeObserver(relayout).observe(p.root);
  d.fonts.ready.then(relayout);
  panels.set(name, p);
  return p;
}

function header(p, idx, title) {
  const ph = make(p.d, "div", "ph");
  const meta = make(p.d, "span", "meta");
  ph.append(make(p.d, "span", "idx", idx), make(p.d, "span", "title", title), make(p.d, "span", "rule"), meta);
  p.root.append(ph);
  return meta;
}

function roundButton(doc, name, title, onClick) {
  const b = make(doc, "button", "round");
  b.type = "button";
  b.title = title;
  b.innerHTML = icon(name);
  b.addEventListener("click", onClick);
  return b;
}

// ---- conversation

const conv = openPanel("conversation", "conversation");
const convMeta = header(conv, "01", "Conversation");
const cardsEl = make(conv.d, "div", "cards");
const convEmpty = make(conv.d, "div", "empty", "Your exchanges with Sunday show here as you talk.");
conv.root.append(cardsEl, convEmpty);

// ---- systems

const sys = openPanel("systems", "systems");
const sysMeta = header(sys, "02", "Systems");
const statsEl = make(sys.d, "div", "stats");
const sitesEl = make(sys.d, "div", "sites");
const sysFoot = make(sys.d, "div", "foot");
const sysEmpty = make(sys.d, "div", "empty", "Waiting for the first reading.");
sys.root.append(statsEl, sitesEl, sysFoot, sysEmpty);

// ---- work in progress

const work = openPanel("work", "work");
const workMeta = header(work, "03", "Work in Progress");
const tasksEl = make(work.d, "div", "tasks");
const workEmpty = make(work.d, "div", "empty", "Nothing running right now.");
const workFoot = make(work.d, "div", "foot");
work.root.append(tasksEl, workEmpty, workFoot);

// ---- incidents

const inc = openPanel("incidents", "incidents");
const incMeta = header(inc, "04", "Incidents");
const incEl = make(inc.d, "div", "list");
const incEmpty = make(inc.d, "div", "empty", "No incidents on record.");
inc.root.append(incEl, incEmpty);

// ---- status capsule

const status = openPanel("status", "status-capsule hug");
const statusItems = {};
for (const key of ["mic", "systems", "tasks"]) {
  const item = make(status.d, "div", "item");
  const dot = make(status.d, "span", "dot");
  const text = make(status.d, "span", "label");
  item.append(dot, text);
  status.root.append(item);
  statusItems[key] = { item, dot, text };
}
const statusButtons = make(status.d, "div", "buttons");
const statusFocus = roundButton(status.d, "focus", "Focus", () => setMode("focus"));
const statusPass = roundButton(status.d, "pointer", "Pass Clicks", () => setPass(!ui.pass));
const statusSettings = roundButton(status.d, "sliders", "Settings", () => api.settingsMenu());
statusButtons.append(statusFocus, statusPass, statusSettings);
status.root.append(statusButtons);

// ---- dock

const dock = openPanel("dock", "dock hug");
const dockButtons = {};
function dockButton(key, iconName, label, onClick) {
  const b = make(dock.d, "button", "dock-button");
  b.type = "button";
  const round = make(dock.d, "span", "round");
  round.innerHTML = icon(iconName);
  const text = make(dock.d, "span", "label", label);
  b.append(round, text);
  b.addEventListener("click", () => { if (!b.disabled) onClick(); });
  dock.root.append(b);
  dockButtons[key] = { b, round, text };
}
dockButton("mute", "mic", "Mute", () => send({ type: muted ? "unmute" : "mute" }));
dockButton("voice", "voice", "Voice", toggleVoice);
dockButton("details", "details", "Details", toggleDetails);
dockButton("focus", "focus", "Focus", () => setMode("focus"));
dockButton("pass", "pointer", "Pass Clicks", () => setPass(!ui.pass));
dockButton("settings", "sliders", "Settings", () => api.settingsMenu());
dockButtons.voice.b.title = "ElevenLabs Voice";

// ---- the head: the live exchange in one line, over the details or as the focus bar

const head = openPanel("head", "minibar");
const headMini = make(head.d, "div", "mini reactor idle");
headMini.innerHTML = reactorSVG(true);
const headText = make(head.d, "div", "text");
const headLine = make(head.d, "div", "line");
const headLabel = make(head.d, "span", "label");
const headTime = make(head.d, "span", "mono t-faint");
headLine.append(headLabel, headTime);
const headSaid = make(head.d, "div", "said");
headText.append(headLine, headSaid);
const headBack = roundButton(head.d, "deck", "Open the Deck", () => setMode("deck"));
head.root.append(headMini, headText, headBack);

// ---- toasts, in focus mode

const toastPanel = openPanel("toasts", "toasts");
const toasts = [];

// ---------------------------------------------------------------- the deck's own drawing

const hud = $("hud");
const stage = $("stage");
const hintsEl = $("hints");
hintsEl.append(keyCaps(document, KEYS.show, "Show or Hide"), keyCaps(document, KEYS.mode, "Deck or Focus"));
const focusHint = $("focusHint");
focusHint.append(keyCaps(document, KEYS.mode, "Brings the deck back"));

function tickClock() {
  const now = new Date();
  setText($("time"), timeOf(now));
  const day = now.toLocaleDateString([], { weekday: "long" });
  const month = now.toLocaleDateString([], { month: "short" });
  setText($("date"), `${day} ${pad2(now.getDate())} ${month} ${now.getFullYear()}`);
}

// ---------------------------------------------------------------- mode, pass clicks and layout

let ui = { mode: "deck", pass: false };
let focusHintTimer = null;

function setMode(mode) {
  if (mode !== "deck" && mode !== "focus") return;
  const was = ui.mode;
  ui.mode = mode;
  api.ui(ui);
  hud.classList.toggle("focus", mode === "focus");
  clearTimeout(focusHintTimer);
  focusHint.classList.toggle("on", mode === "focus" && was !== "focus");
  if (mode === "focus") focusHintTimer = setTimeout(() => { focusHint.classList.remove("on"); }, 6000);
  render();
}

function setPass(on) {
  ui.pass = !!on;
  api.ui(ui);
  api.pass(ui.pass);
  render();
}

let layoutQueued = false;
function relayout() {
  if (layoutQueued) return;
  layoutQueued = true;
  setTimeout(() => { layoutQueued = false; layout(); }, 0);
}

function measure(p, width) {
  p.root.style.width = width == null ? "" : `${width}px`;
  const r = p.root.getBoundingClientRect();
  return { width: Math.ceil(r.width), height: Math.ceil(r.height) };
}

function place(p, rect) {
  const key = rect ? `${Math.round(rect.x)},${Math.round(rect.y)},${Math.round(rect.width)},${Math.round(rect.height)}` : "";
  if (p.placed === key) return;
  p.placed = key;
  api.place(p.name, rect ? { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) } : null);
}

let lastDetails = "";
function layout() {
  const W = window.innerWidth, H = window.innerHeight;
  if (!W || !H) return;
  const narrow = W < 1700;
  const m = narrow ? 32 : 40, col = narrow ? 340 : 400, top = narrow ? 84 : 98, gap = 16;
  document.documentElement.style.setProperty("--m", `${m}px`);
  document.body.classList.toggle("short", H < 900);
  for (const p of panels.values()) p.d.body.classList.toggle("narrow", narrow);
  const at = {};
  const sx = m + col + 24, sw = W - 2 * sx;
  const dockSize = measure(dock, null);
  const dockY = H - 24 - dockSize.height;
  let details;

  if (ui.mode === "deck") {
    const incH = measure(inc, col).height;
    at.incidents = { x: m, y: H - m - incH, width: col, height: incH };
    const convH = H - m - incH - gap - top;
    conv.root.style.height = `${convH}px`;
    at.conversation = { x: m, y: top, width: col, height: convH };
    const sysH = measure(sys, col).height;
    at.systems = { x: W - m - col, y: top, width: col, height: sysH };
    const workTop = top + sysH + gap;
    const workH = Math.min(measure(work, col).height, H - m - workTop);
    at.work = workH > 80 ? { x: W - m - col, y: workTop, width: col, height: workH } : null;
    const st = measure(status, null);
    at.status = { x: W - m - st.width, y: 22 + (56 - st.height) / 2, width: st.width, height: st.height };
    at.dock = { x: sx + (sw - dockSize.width) / 2, y: dockY, width: dockSize.width, height: dockSize.height };
    const headH = measure(head, sw).height;
    if (detailsOpen()) at.head = { x: sx, y: top, width: sw, height: headH };
    const dy = top + headH + gap;
    details = { x: sx, y: dy, width: sw, height: dockY - gap - dy };
    at.toasts = null;
  } else {
    const hs = measure(head, null);
    const headOn = headWanted();
    if (headOn) at.head = { x: (W - hs.width) / 2, y: 22, width: hs.width, height: hs.height };
    const ts = measure(toastPanel, 360);
    at.toasts = toasts.length ? { x: W - m - 360, y: 22, width: 360, height: ts.height } : null;
    const dw = Math.min(940, W - 64);
    const dy = headOn ? 22 + hs.height + gap : 52;
    details = { x: (W - dw) / 2, y: dy, width: dw, height: H - 40 - dy };
    focusHint.style.top = `${headOn ? 22 + hs.height + 14 : 30}px`;
  }

  // The stage: the reactor's lens and what is being said, between the columns and above the dock.
  const hintsTop = dockY - 44;
  const room = hintsTop - top;
  const lens = Math.round(Math.max(200, Math.min(420, sw * 0.62, room * 0.5)));
  const lensTop = Math.round(Math.min(56, room * 0.08));
  stage.style.left = `${sx}px`;
  stage.style.width = `${sw}px`;
  stage.style.top = `${top}px`;
  stage.style.height = `${H - top}px`;
  stage.style.setProperty("--lens", `${lens}px`);
  stage.style.setProperty("--lens-top", `${lensTop}px`);
  stage.style.setProperty("--live-top", `${lensTop + lens + 34}px`);
  stage.style.setProperty("--live-bottom", `${H - top - (hintsTop - 18)}px`);
  hintsEl.style.top = `${hintsTop - top}px`;
  stage.classList.toggle("compact", detailsOpen());

  for (const p of panels.values()) {
    const rect = at[p.name];
    if (rect && p.name !== "dock" && p.name !== "status" && p.name !== "head") {
      p.root.style.width = `${rect.width}px`;
    }
    place(p, rect || null);
  }
  const key = JSON.stringify(details);
  if (key !== lastDetails) { lastDetails = key; api.detailsRect(details); }
}

window.addEventListener("resize", relayout);

// ---------------------------------------------------------------- the exchange cards

// Every query gets its own card, keyed by the id the server puts on each event about it. A card
// shows one state at a time (Listening, Thinking, Answering...), his words as they were heard,
// one plain line of what is being done while it works, and the answer. Cards stay as the
// conversation's history, newest on top; the middle of the deck follows the newest.
const STATES = {
  listening: ["Listening", "listening"],
  thinking: ["Thinking", "thinking"],
  speaking: ["Answering", "speaking"],
  answered: ["Answered", "answered"],
  announcing: ["Announcement", "speaking"],
  announced: ["Announcement", "answered"],
  late: ["Follow-Up", "speaking"],
  lateDone: ["Follow-Up", "answered"],
  idle: ["Sunday", "quiet"],
  stopped: ["Stopped", "quiet"],
  skipped: ["Skipped", "quiet"],
  closed: ["Closed", "quiet"],
  error: ["Couldn't Answer", "alert"],
};
const TONE = { listening: "cyan", thinking: "amber", speaking: "mint", answered: "mint", quiet: "idle", alert: "red" };

const cards = new Map();            // id -> card, oldest first
let current = null;                 // the card the middle of the deck follows
let lastId = null;                  // for events from a server that sends no id
let seq = 0;

function makeCard(id, kind = "question") {
  const card = {
    id, kind, state: "idle", look: "quiet", label: "Sunday", finished: false, audible: false, endState: "idle",
    at: Date.now(), touched: Date.now(), doneAt: 0, you: "", partial: false, earlier: false,
    status: "", statusWant: "", statusAt: 0, statusTimer: null, steps: [], reply: "", filler: false,
    note: "", ear: false, hasCanvas: false, heard: false, el: null,
  };
  cards.set(id, card);
  while (cards.size > MAX_CARDS) {
    const old = [...cards.values()].find((c) => c.finished && c !== current);
    if (!old) break;
    cards.delete(old.id);
    old.el?.remove();
  }
  return card;
}

function cardFor(id, create, kind) {
  let card = cards.get(id);
  if (!card && create) card = makeCard(id, kind);
  if (card) card.touched = Date.now();
  return card;
}

function follow(card) {
  current = card;
  dirty(card);
}

function setState(card, state) {
  const [label, look] = STATES[state] || STATES.idle;
  card.state = state;
  card.look = look;
  card.label = label;
  dirty(card);
}

function setYou(card, text, partial, earlier) {
  card.you = text || "";
  card.partial = !!partial;
  card.earlier = !!earlier;
  if (!partial && text) card.heard = true;
  dirty(card);
}

function setStatus(card, text) {
  // One plain line of what is being done, each held long enough to read, so a quick run of
  // lookups never flickers past.
  card.statusWant = text || "";
  clearTimeout(card.statusTimer);
  const apply = () => {
    if (card.status === card.statusWant) return;
    card.status = card.statusWant;
    card.statusAt = Date.now();
    if (card.status && card.steps[card.steps.length - 1] !== card.status) card.steps.push(card.status);
    dirty(card);
  };
  const wait = card.statusAt + STATUS_MIN_MS - Date.now();
  if (!text || wait <= 0) apply();
  else card.statusTimer = setTimeout(apply, wait);
}

function setReply(card, text, filler) {
  card.reply = text || "";
  card.filler = !!filler;
  dirty(card);
}

function playingState(card) {
  return card.kind === "announce" ? "announcing" : card.kind === "late" ? "late" : "speaking";
}

function active(card) {
  card.finished = false;
  dirty(card);
}

function finish(card, state) {
  if (!card) return;
  if (!state) {
    state = card.reply && !card.filler
      ? (card.kind === "announce" ? "announced" : card.kind === "late" ? "lateDone" : "answered")
      : "idle";
  }
  card.endState = state;
  setStatus(card, "");
  setState(card, card.audible ? playingState(card) : state);
  card.finished = true;
  card.doneAt = Date.now();
  dirty(card);
}

let pending = new Set(), flushQueued = false;
function dirty(card) {
  if (card) pending.add(card);
  if (flushQueued) return;
  flushQueued = true;
  queueMicrotask(() => {
    flushQueued = false;
    const list = pending;
    pending = new Set();
    for (const c of list) if (cards.get(c.id) === c) renderCard(c);
    render();
  });
}

function renderCard(card) {
  const d = conv.d;
  if (!card.el) {
    const el = make(d, "div", "card");
    const top = make(d, "div", "head");
    card.$ = {
      label: make(d, "span", "label"),
      time: make(d, "span", "time"),
      you: make(d, "div", "you"),
      status: make(d, "div", "status"),
      reply: make(d, "div", "reply"),
      note: make(d, "div", "note"),
      details: make(d, "div", "details"),
    };
    top.append(card.$.label, make(d, "span", "grow"), card.$.time);
    el.append(top, card.$.you, card.$.status, card.$.reply, card.$.note, card.$.details);
    const show = make(d, "button", "chip-button", "Show Details");
    show.type = "button";
    card.$.details.append(show);
    el.addEventListener("click", () => { if (canShowDetails(card)) reopenDetails(card.id); });
    card.el = el;
    cardsEl.prepend(el);
    cardsEl.scrollTop = 0;
    if (!reduced.matches) {
      el.animate([{ opacity: 0, transform: "translateY(-6px)" }, { opacity: 1, transform: "none" }],
        { duration: 320, easing: "cubic-bezier(0.2, 0.8, 0.2, 1)" });
    }
  }
  const tone = TONE[card.look] || "idle";
  card.el.className = `card tone-${tone}${card.finished && !card.audible ? " done" : ""}${canShowDetails(card) ? " has-details" : ""}`;
  setText(card.$.label, card.label);
  setText(card.$.time, (card.ear ? (isMac ? "This Mac · " : "Mac · ") : "") + timeOf(card.at));
  setText(card.$.you, card.you);
  card.$.you.classList.toggle("partial", card.partial);
  setText(card.$.status, card.finished ? "" : card.status);
  setText(card.$.reply, card.reply);
  card.$.reply.classList.toggle("filler", card.filler);
  setText(card.$.note, card.note);
  card.$.details.hidden = !canShowDetails(card);
}

// ---------------------------------------------------------------- what is live right now

let live = { kind: "idle" };
let lingerTimer = null;

function liveState() {
  if (!connected && unreachableSaid) return { kind: "offline" };
  if (muted) return { kind: "muted" };
  const c = current;
  if (!c) return { kind: "idle" };
  if (!c.finished) {
    const kind = c.look === "thinking" ? "thinking" : c.look === "speaking" ? "speaking"
      : c.look === "listening" ? "listening" : c.reply ? "speaking" : "listening";
    return { kind, card: c };
  }
  if (c.audible) return { kind: "speaking", card: c };
  const linger = ui.mode === "deck" ? ANSWER_LINGER_MS : FOCUS_LINGER_MS;
  const left = c.doneAt + linger - Date.now();
  if (left > 0 && (c.look === "answered" || c.look === "alert")) {
    clearTimeout(lingerTimer);
    lingerTimer = setTimeout(render, left + 30);
    return { kind: c.look === "alert" ? "error" : "answered", card: c };
  }
  return { kind: "idle", card: c };
}

function headWanted() {
  if (ui.mode === "deck") return detailsOpen();
  return live.kind !== "idle";
}

const QUIET = {
  idle: "Say “Sunday” to ask anything.",
  muted: "Sunday listens only for “unmute” until you turn the microphone back on.",
  offline: "Reconnecting",
};

function describe(state) {
  // What the stage and the head both show: a label and its tone, the question and the line.
  const c = state.card;
  switch (state.kind) {
    case "listening":
      return { label: "Listening", tone: "cyan", asked: "", said: c.you, partial: c.partial };
    case "thinking":
      return { label: "Thinking", tone: "amber", asked: "", said: c.you, steps: c.steps.slice(-3) };
    case "speaking":
      return { label: c.label === "Sunday" ? "Answering" : c.label, tone: "mint", asked: c.you, said: c.reply || c.you };
    case "answered":
      return { label: c.label, tone: "mint", asked: c.you, said: c.reply };
    case "error":
      return { label: "Couldn't Answer", tone: "red", asked: c.you, said: c.note, quiet: true };
    case "muted":
      return { label: "Mic Off", tone: "red", said: QUIET.muted, quiet: true };
    case "offline":
      return { label: "Can't Reach Sunday", tone: "red", said: QUIET.offline, quiet: true };
    default:
      return { label: "Standing By", tone: "idle", said: QUIET.idle, quiet: true };
  }
}

const REACTOR_LOOK = { listening: "listening", thinking: "thinking", speaking: "speaking", answered: "answered", error: "offline", muted: "muted", offline: "offline", idle: "idle" };

function renderLive() {
  live = liveState();
  const look = REACTOR_LOOK[live.kind] || "idle";
  const view = describe(live);
  reactorEl.className = `reactor ${look}`;
  const labelEl = $("stateLabel");
  setText(labelEl, view.label);
  labelEl.className = `state-label tone-${view.tone}`;
  setText($("asked"), view.asked || "");
  const said = $("said");
  setText(said, view.said || "");
  said.classList.toggle("quiet", !!view.quiet);
  said.classList.toggle("tail", !!view.partial);
  const len = (view.said || "").length;
  said.style.setProperty("--said-size", view.quiet ? "20px" : len <= 90 ? "34px" : len <= 180 ? "28px" : len <= 320 ? "23px" : "20px");
  const wave = $("wave");
  wave.className = `wave tone-${view.tone}${live.kind === "listening" || live.kind === "speaking" ? " on" : ""}`;
  const chips = $("chips");
  chips.replaceChildren();
  (view.steps || []).forEach((step, i, all) => {
    const chip = make(document, "span", `chip ${i === all.length - 1 ? "tone-amber" : "tone-mint"}`);
    chip.append(make(document, "span", "dot"), make(document, "span", null, step));
    chips.append(chip);
  });

  // The head: the same, in one line.
  headMini.className = `mini reactor ${look}`;
  setText(headLabel, view.label);
  headLabel.className = `label tone-${view.tone} t-${view.tone === "idle" ? "faint" : view.tone}`;
  const compactIdle = ui.mode === "deck" && live.kind === "idle" && current && current.reply;
  if (compactIdle) {
    setText(headLabel, current.label);
    headLabel.className = "label t-mint";
    setText(headSaid, current.reply);
    setText(headTime, timeOf(current.at));
  } else {
    setText(headSaid, view.said || "");
    setText(headTime, live.card ? timeOf(live.card.at) : "");
  }
  headSaid.classList.toggle("t-faint", !!view.quiet && !compactIdle);
  headBack.hidden = ui.mode !== "focus";
  head.d.body.classList.toggle("wide", ui.mode === "deck");
  animate();
}

// ---------------------------------------------------------------- the status capsule and the dock

function renderStatus() {
  let mic;
  if (!connected && unreachableSaid) mic = ["red", "Can't Reach Sunday"];
  else if (muted) mic = ["red", "Mic Off"];
  else if (devices && Array.isArray(devices.cubes) && !devices.cubes.length) mic = ["amber", "Speaker Offline"];
  else if (devices?.hw_muted?.length) mic = ["amber", "Speaker Mic Off"];
  else mic = ["cyan", "Mic Live"];
  setItem(statusItems.mic, mic[0], mic[1]);

  if (feed && Array.isArray(feed.sites)) {
    const bad = feed.sites.filter((s) => s.status !== "operational");
    const down = bad.some((s) => s.status !== "degraded");
    setItem(statusItems.systems, bad.length ? (down ? "red" : "amber") : "mint",
      bad.length ? `${plural(bad.length, "Site Needs", "Sites Need")} Attention` : "All Systems Normal");
  } else statusItems.systems.item.hidden = true;

  const running = visibleTasks().filter((t) => !t.ended).length;
  if (running) setItem(statusItems.tasks, "amber", `${plural(running, "Task", "Tasks")} Running`);
  else statusItems.tasks.item.hidden = true;

  statusPass.classList.toggle("on", ui.pass);
  statusPass.title = ui.pass ? "Stop Passing Clicks" : "Pass Clicks";
}

function setItem(it, tone, text) {
  it.item.hidden = false;
  it.dot.className = `dot tone-${tone}`;
  it.text.className = `label t-${tone}`;
  setText(it.text, text);
}

function renderDock() {
  const m = dockButtons.mute;
  m.b.disabled = !connected;
  m.b.classList.toggle("alert", muted);
  m.round.innerHTML = icon(muted ? "micOff" : "mic");
  setText(m.text, muted ? "Unmute" : "Mute");
  const v = dockButtons.voice;
  v.b.disabled = !connected || elevenlabs == null;
  v.round.classList.toggle("on", !!elevenlabs);
  v.b.classList.toggle("on", !!elevenlabs);
  const dt = dockButtons.details;
  dt.b.disabled = !detailsOpen() && !lastShownId;
  dt.round.classList.toggle("on", detailsOpen());
  dt.b.classList.toggle("on", detailsOpen());
  const p = dockButtons.pass;
  p.round.classList.toggle("on", ui.pass);
  p.b.classList.toggle("on", ui.pass);
}

function render() {
  renderLive();
  renderStatus();
  renderDock();
  const n = cards.size;
  setText(convMeta, n ? `Today · ${n}` : "Today");
  convEmpty.hidden = n > 0;
  cardsEl.hidden = n === 0;
  relayout();
}

// ---------------------------------------------------------------- work in progress

const tasks = new Map();            // id -> { id, label, status, started, ended, el }

function visibleTasks() {
  // A question's own lookup is already on its card, so it is not listed again here.
  return [...tasks.values()].filter((t) => !(t.id.startsWith("q-") && cards.has(t.id.slice(2))));
}

function startTask(e) {
  if (tasks.has(e.id)) { updateTask(e); return; }
  tasks.set(e.id, { id: e.id, label: e.label || e.kind || "Working", status: e.status || "", started: Date.now(), ended: 0, el: null });
  renderWork();
}

function updateTask(e) {
  const t = tasks.get(e.id);
  if (t && e.status && t.status !== e.status) { t.status = e.status; renderWork(); }
}

function endTask(id) {
  const t = tasks.get(id);
  if (!t || t.ended) return;
  t.ended = Date.now();
  renderWork();
  if (ui.mode === "focus" && !id.startsWith("q-")) toast({ tone: "mint", label: "Done", text: t.label, detail: t.status });
  setTimeout(() => {
    if (t.el) t.el.classList.add("leaving");
    setTimeout(() => { t.el?.remove(); tasks.delete(id); renderWork(); }, 300);
  }, TASK_DONE_MS);
}

const MAX_TASK_ROWS = 5;
function renderWork() {
  const list = visibleTasks();
  const shown = list.slice(0, MAX_TASK_ROWS);
  for (const t of tasks.values()) if (t.el && !shown.includes(t)) { t.el.remove(); t.el = null; }
  for (const t of shown) {
    if (!t.el) {
      const d = work.d;
      t.el = make(d, "div", "task");
      const top = make(d, "div", "head");
      t.$ = { what: make(d, "span", "what"), state: make(d, "span", "label state"), elapsed: make(d, "span", "elapsed"), detail: make(d, "div", "detail") };
      top.append(make(d, "span", "dot"), t.$.what, t.$.state, t.$.elapsed);
      t.el.append(top, t.$.detail, make(d, "div", "track"));
      tasksEl.append(t.el);
    }
    t.el.className = `task tone-${t.ended ? "mint" : "amber"}${t.ended ? " done" : ""}${t.el.classList.contains("leaving") ? " leaving" : ""}`;
    setText(t.$.what, t.label);
    t.$.what.title = t.label;
    setText(t.$.state, t.ended ? "Done" : "Running");
    setText(t.$.elapsed, lasted((t.ended || Date.now()) - t.started));
    setText(t.$.detail, t.status && t.status !== t.label ? t.status : "");
  }
  const running = list.filter((t) => !t.ended).length;
  setText(workMeta, running ? `${running} active` : "Idle");
  workEmpty.hidden = list.length > 0;
  setText(workFoot, list.length > MAX_TASK_ROWS ? `${list.length - MAX_TASK_ROWS} more running` : "");
  workFoot.hidden = list.length <= MAX_TASK_ROWS;
  render();
}

function tickTasks() {
  for (const t of tasks.values()) if (t.el && !t.ended) setText(t.$.elapsed, lasted(Date.now() - t.started));
}

// ---------------------------------------------------------------- systems and incidents

let feed = null;
let knownIncidents = null;

const siteTone = (s) => (s.status === "operational" ? "mint" : s.status === "degraded" ? "amber" : "red");
const siteWord = (s) => (s.status === "operational" ? "Online" : s.status === "degraded" ? "Degraded" : "Down");
const siteName = (s) => String(s.domain || s.name || s.id || "").replace(/^www\./, "");
const RANK = { red: 0, amber: 1, mint: 2 };

function spark(doc, days, tone) {
  // The last two weeks of uptime as a line, flat when every day was whole.
  const pts = (days || []).filter((d) => d.measured !== false && Number.isFinite(d.uptime)).slice(-14);
  const svgNS = "http://www.w3.org/2000/svg";
  const svg = doc.createElementNS(svgNS, "svg");
  svg.setAttribute("width", "56");
  svg.setAttribute("height", "14");
  svg.setAttribute("viewBox", "0 0 56 14");
  if (pts.length < 2) return svg;
  const lo = Math.min(99, ...pts.map((d) => d.uptime));
  const y = (u) => 12 - ((u - lo) / Math.max(0.01, 100 - lo)) * 10;
  const line = doc.createElementNS(svgNS, "polyline");
  line.setAttribute("points", pts.map((d, i) => `${((i / (pts.length - 1)) * 54 + 1).toFixed(1)},${y(d.uptime).toFixed(1)}`).join(" "));
  line.setAttribute("fill", "none");
  line.setAttribute("stroke", `var(--${tone})`);
  line.setAttribute("stroke-width", "1.4");
  line.setAttribute("stroke-linejoin", "round");
  line.setAttribute("stroke-linecap", "round");
  svg.append(line);
  return svg;
}

function stat(doc, value, label, delta, deltaTone) {
  const tile = make(doc, "div", "stat");
  tile.append(make(doc, "div", "value", value), make(doc, "div", "label", label));
  const dl = make(doc, "div", `delta t-${deltaTone}`, delta);
  tile.append(dl);
  return tile;
}

function renderSystems() {
  const d = sys.d;
  const sites = Array.isArray(feed?.sites) ? feed.sites : [];
  sysEmpty.hidden = sites.length > 0;
  statsEl.hidden = sitesEl.hidden = sysFoot.hidden = !sites.length;
  if (!sites.length) { setText(sysMeta, ""); render(); return; }
  const updated = Date.parse(feed.updated_at || "");
  const stale = !Number.isFinite(updated) || Date.now() - updated > STALE_FEED_MS;
  setText(sysMeta, stale ? "Stale" : "Live");
  sysMeta.classList.toggle("warn", stale);

  const online = sites.filter((s) => s.status === "operational").length;
  const degraded = sites.filter((s) => s.status === "degraded").length;
  const down = sites.length - online - degraded;
  const issues = sites.reduce((n, s) => n + (Number(s.open_issues) || 0), 0);
  const withIssues = sites.filter((s) => Number(s.open_issues) > 0).length;
  const ups = sites.map((s) => Number(s.uptime_30d)).filter(Number.isFinite);
  const uptime = ups.length ? Math.floor((ups.reduce((a, b) => a + b, 0) / ups.length) * 10) / 10 : null;
  const outages = Number(feed.outages_30d) || 0;
  statsEl.replaceChildren(
    stat(d, pad2(issues), "Open Issues", issues ? `on ${plural(withIssues, "site", "sites")}` : "none open", issues ? "amber" : "mint"),
    stat(d, `${online}/${sites.length}`, "Sites Online", down ? `${down} down` : degraded ? `${degraded} degraded` : "all online",
      down ? "red" : degraded ? "amber" : "mint"),
    stat(d, uptime == null ? "--" : `${uptime}%`, "30-Day Uptime", outages ? plural(outages, "outage", "outages") : "no outages",
      outages ? "amber" : "mint"),
  );

  const rows = [...sites].sort((a, b) => RANK[siteTone(a)] - RANK[siteTone(b)] || siteName(a).localeCompare(siteName(b)));
  const max = document.body.classList.contains("short") ? 4 : 6;
  sitesEl.replaceChildren(...rows.slice(0, max).map((s) => {
    const tone = siteTone(s);
    const row = make(d, "div", `site tone-${tone}${tone === "amber" ? " slow" : tone === "red" ? " down" : ""}`);
    row.append(make(d, "span", "dot"), make(d, "span", "name", siteName(s)), spark(d, s.days, tone), make(d, "span", "note", siteWord(s)));
    row.title = s.name || siteName(s);
    return row;
  }));
  const more = rows.length - max;
  setText(sysFoot, `${more > 0 ? `${plural(more, "more site", "more sites")} · ` : ""}updated ${Number.isFinite(updated) ? ago(updated) : "a while ago"}`);
  render();
}

function renderIncidents() {
  const d = inc.d;
  const all = Array.isArray(feed?.incidents) ? feed.incidents : [];
  const open = all.filter((i) => i.status !== "resolved");
  const ids = new Set(all.map((i) => i.id));
  if (knownIncidents) {
    for (const i of open) {
      if (!knownIncidents.has(i.id) && ui.mode === "focus") toast({ tone: "amber", label: "Incident", text: i.title, detail: i.site });
    }
  }
  knownIncidents = ids;
  const latest = (i) => Date.parse(i.opened_at || "") || 0;
  const pick = [...open.sort((a, b) => latest(b) - latest(a)), ...all.filter((i) => i.status === "resolved").sort((a, b) => latest(b) - latest(a))].slice(0, 3);
  incEmpty.hidden = pick.length > 0;
  setText(incMeta, open.length ? `${open.length} open` : all.length ? "All Clear" : "");
  incMeta.classList.toggle("warn", open.length > 0);
  incEl.replaceChildren(...pick.map((i) => {
    const resolved = i.status === "resolved";
    const row = make(d, "div", `incident tone-${resolved ? "mint" : "amber"}`);
    const body = make(d, "div", "body");
    const when = resolved
      ? `Resolved ${ago(Date.parse(i.resolved_at || i.updated_at || "") || Date.now())}`
      : `Opened ${ago(latest(i) || Date.now())}`;
    body.append(make(d, "div", "title", i.title || "Incident"), make(d, "div", "where", `${i.site || ""} · ${when}`));
    row.append(make(d, "span", `dot${resolved ? " quiet" : ""}`), body);
    row.title = i.summary || "";
    return row;
  }));
  render();
}

api.onStatusFeed((f) => {
  if (!f || typeof f !== "object") return;
  feed = f;
  renderSystems();
  renderIncidents();
});

// ---------------------------------------------------------------- toasts

function toast(t) {
  const item = { ...t, at: Date.now() };
  toasts.unshift(item);
  if (toasts.length > 3) toasts.length = 3;
  renderToasts();
  setTimeout(() => {
    const i = toasts.indexOf(item);
    if (i >= 0) { toasts.splice(i, 1); renderToasts(); }
  }, TOAST_MS);
}

function renderToasts() {
  const d = toastPanel.d;
  toastPanel.root.replaceChildren(...toasts.map((t) => {
    const el = make(d, "div", `toast tone-${t.tone}`);
    const top = make(d, "div", "head");
    top.append(make(d, "span", "dot"), make(d, "span", `label t-${t.tone}`, t.label), make(d, "span", "grow"), make(d, "span", "mono t-faint", timeOf(t.at)));
    el.append(top, make(d, "div", "text", t.text));
    if (t.detail) el.append(make(d, "div", "mono t-faint", t.detail));
    return el;
  }));
  relayout();
}

// ---------------------------------------------------------------- the details sheet

let canvasOpenId = null;            // the details up on screen
let lastShownId = null;             // the last details shown, which Show Details brings back
const detailsOpen = () => canvasOpenId != null;
const canShowDetails = (card) => card.hasCanvas && card.id === lastShownId && canvasOpenId !== card.id;

function onCanvas(e) {
  const card = cards.get(e.id);
  if (card) { card.hasCanvas = true; dirty(card); }
  api.canvas(e, null);
}

function reopenDetails(id) {
  api.canvasReopen(id, null);
  send({ type: "canvas_state", id, machine, shown: true, reopened: true });
}

function toggleDetails() {
  if (canvasOpenId) api.detailsClose();
  else if (lastShownId) reopenDetails(lastShownId);
}

api.onCanvasOpen(({ id, open }) => {
  if (open) { canvasOpenId = id; lastShownId = id; }
  else if (canvasOpenId === id) canvasOpenId = null;
  for (const c of cards.values()) if (c.hasCanvas) dirty(c);
  dirty(null);
});
// Where the details landed, or why they were held back, and when he closed them here: the
// brain takes them down on his other screens and stops listening for "close that".
api.onCanvasReport((r) => send({ type: "canvas_state", id: r.id, machine, shown: r.shown, why: r.why, reopened: !!r.reopened }));
api.onCanvasClosedHere((r) => send({ type: "canvas_closed", id: r.id, by: r.by }));

// ---------------------------------------------------------------- muting, the voice, the devices

let muted = false;
let elevenlabs = null;              // unknown until the server says
let devices = null;                 // the devices the brain has connected, from "hello" and every "sync"

function setMuted(on) {
  muted = !!on;
  dirty(null);
}

function setVoice(on) {
  elevenlabs = !!on;
  api.voice(elevenlabs);
  dirty(null);
}

function toggleVoice() {
  if (elevenlabs != null) send({ type: "voice", elevenlabs: !elevenlabs });
}
api.onToggleVoice(toggleVoice);

// ---------------------------------------------------------------- reconciling with the server

// On every (re)connect and every few seconds after, the server sends what is really in
// progress. Anything here that it no longer lists is let go, so a "done" or an "end" lost to a
// restart, a dropped connection or a turn that died can't leave anything stuck. Something heard
// from in the last moment is left alone, since its own events may still be on the way.
function reconcile(e) {
  if (e.devices) devices = e.devices;
  if (Array.isArray(e.active)) {
    const listed = new Set(e.active.map((t) => t.id));
    for (const id of [...tasks.keys()]) if (!listed.has(id)) endTask(id);
    for (const t of e.active) startTask(t);
  }
  if (Array.isArray(e.live)) {
    const alive = new Set(e.live), playing = new Set(e.playing || []);
    const now = Date.now();
    for (const card of cards.values()) {
      if (now - card.touched < SETTLE_MS || alive.has(card.id)) continue;
      if (!card.finished) finish(card, card.endState === "idle" ? null : card.endState);
      if (card.audible && !playing.has(card.id)) {
        card.audible = false;
        setState(card, card.endState);
      }
    }
  }
  dirty(null);
}

function letGoOfEverything() {
  // With no server to ask, nothing shown can be trusted to still be going.
  for (const id of [...tasks.keys()]) endTask(id);
  for (const card of cards.values()) {
    if (!card.finished) finish(card, card.endState === "idle" ? null : card.endState);
    if (card.audible) { card.audible = false; setState(card, card.endState); }
  }
}

// ---------------------------------------------------------------- server events

function handle(e) {
  if (e.type === "hello") { reconcile(e); setMuted(e.muted); setVoice(e.elevenlabs); api.light(!!e.light); return; }
  if (e.type === "sync") { reconcile(e); return; }
  if (e.type === "light") { api.light(!!e.on); return; }
  if (e.type === "voice") { setVoice(e.elevenlabs); return; }
  if (e.type === "mute") { setMuted(e.muted); return; }
  if (e.type === "overlay") { api.overlay(!!e.on, e.by || "voice"); return; }
  if (e.type === "canvas_close") { api.canvasClose(e.id, e.by); return; }
  if (e.type === "task") {
    if (e.state === "start") startTask(e);
    else if (e.state === "update") updateTask(e);
    else if (e.state === "end") endTask(e.id);
    return;
  }
  if (e.type === "canvas") { onCanvas(e); return; }
  const id = e.id ?? (e.type === "listening" || e.type === "announce" ? "local-" + ++seq : lastId);
  if (id != null) lastId = id;
  let card;
  switch (e.type) {
    case "listening":
      card = cardFor(id, true);
      card.ear = !!e.ear;
      setState(card, "listening");
      active(card);
      follow(card);
      break;
    case "partial":
      card = cardFor(id, true);
      if (!card.heard) setYou(card, e.text, true);
      follow(card);
      break;
    case "heard":
      card = cardFor(id, true);
      setYou(card, e.text, false);
      follow(card);
      break;
    case "status":
      card = cardFor(id, true);
      if (e.kind === "filler") {
        // A line said while the answer is worked out: shown as said, until the answer replaces it.
        if (!card.reply || card.filler) setReply(card, e.text, true);
      } else {
        if (card.look !== "speaking") setState(card, "thinking");
        setStatus(card, e.kind === "thinking" || e.text === "Thinking" ? "" : e.text);
      }
      active(card);
      follow(card);
      break;
    case "reply":
      card = cardFor(id, true);
      setStatus(card, "");
      setReply(card, (card.filler ? "" : card.reply) + (card.filler ? String(e.text || "").trimStart() : e.text || ""), false);
      break;
    case "speaking":
      card = cardFor(id, true);
      if (e.ear != null) card.ear = !!e.ear;
      card.audible = true;
      setStatus(card, "");
      setState(card, playingState(card));
      active(card);
      follow(card);
      api.canvasSpeaking({ id, speaking: true });
      break;
    case "spoken":
      card = cardFor(id, false);
      api.canvasSpeaking({ id, speaking: false });
      if (card) {
        card.audible = false;
        if (card.finished) { setState(card, card.endState); card.doneAt = Date.now(); }
      }
      break;
    case "announce":
      card = cardFor(id, true, e.source === "late" ? "late" : "announce");
      card.kind = e.source === "late" ? "late" : "announce";
      setYou(card, e.source === "late" ? e.about || "" : "", false, e.source === "late");
      setReply(card, e.text);
      card.audible = true;
      setState(card, playingState(card));
      active(card);
      follow(card);
      break;
    case "done":
      card = cardFor(id, false);
      api.canvasSpeaking({ id, speaking: false });
      if (!card) break;
      if (e.command === "close") finish(card, "closed");
      else if (e.command === "stop" || e.command === "mute") finish(card, "stopped");
      else if (e.interrupted) finish(card, e.cause === "superseded" || e.cause === "redirect" ? "skipped" : "stopped");
      else if (e.error) {
        finish(card, "error");
        card.note = "Sunday ran into a problem with that one. Ask again.";
      } else finish(card);
      break;
    case "idle":
      if (e.id != null) finish(cardFor(e.id, false));
      // An idle with no id means nothing is in progress anywhere.
      else for (const c of cards.values()) if (!c.finished) finish(c);
      break;
  }
}

// ---------------------------------------------------------------- the connection

let socket = null, machine = "computer", connected = false;
let audioPlaying = false, overlay = null;
let unreachableTimer = null, unreachableSaid = false, everConnected = false;

function send(msg) {
  if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(msg));
}

// Whether this computer is playing sound, passed on so the server can tell the computer's audio
// apart from his voice; and whether the overlay is drawing, so the brain can answer "overlay on"
// truthfully.
function reportAudio() { send({ type: "audio", machine, playing: audioPlaying }); }
function reportOverlay() { if (overlay) send({ type: "overlay", machine, ...overlay }); }
api.onComputerAudio((playing) => { audioPlaying = playing; reportAudio(); });
api.onOverlayState((state) => { overlay = state; reportOverlay(); });
api.onNotice((text) => { if (ui.mode === "focus") toast({ tone: "cyan", label: "Sunday", text }); });

function unreachable() {
  unreachableSaid = true;
  dirty(null);
}

function reachable() {
  clearTimeout(unreachableTimer);
  unreachableTimer = null;
  unreachableSaid = false;
}

async function connect() {
  const servers = await api.servers();
  machine = await api.machine();
  let i = 0, delay = 1000, heardAt = Date.now(), lostTimer = null;
  const attempt = () => {
    const ws = new WebSocket(servers[i % servers.length]);
    let opened = false;
    ws.onopen = () => {
      opened = true; delay = 1000; socket = ws; heardAt = Date.now();
      clearTimeout(lostTimer); lostTimer = null;
      connected = true;
      reachable();
      everConnected = true;
      api.connection(true);
      reportAudio();
      reportOverlay();
      dirty(null);
    };
    ws.onmessage = (m) => { heardAt = Date.now(); try { handle(JSON.parse(m.data)); } catch (_) {} };
    ws.onclose = () => {
      if (socket === ws) socket = null;
      connected = false;
      api.connection(false);
      if (!lostTimer) lostTimer = setTimeout(letGoOfEverything, LOST_AFTER_MS);
      // At start the link takes a few seconds to come up, which is not worth a word.
      if (!unreachableTimer && !unreachableSaid) {
        unreachableTimer = setTimeout(unreachable, everConnected ? UNREACHABLE_AFTER_MS : 20000);
      }
      if (!opened) i++;
      dirty(null);
      setTimeout(attempt, delay);
      delay = Math.min(delay * 2, 15000);
    };
  };
  attempt();
  // The server sends its list every ten seconds; a connection that has gone quiet for much
  // longer is dead even if it hasn't closed, so start it over.
  setInterval(() => {
    if (socket && socket.readyState === WebSocket.OPEN && Date.now() - heardAt > SILENT_AFTER_MS) socket.close();
  }, 5000);
}

// ---------------------------------------------------------------- start

api.onToggleMode((to) => setMode(to || (ui.mode === "deck" ? "focus" : "deck")));
reduced.addEventListener?.("change", () => dirty(null));

(async () => {
  const saved = await api.uiState();
  ui = { mode: saved?.mode === "focus" ? "focus" : "deck", pass: !!saved?.pass };
  hud.classList.toggle("focus", ui.mode === "focus");
  api.pass(ui.pass);
  tickClock();
  setInterval(() => { tickClock(); tickTasks(); }, 1000);
  setInterval(() => { if (feed) { renderSystems(); renderIncidents(); } }, 60000);
  renderWork();
  renderSystems();
  renderIncidents();
  render();
  connect();
})();
