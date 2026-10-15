"use strict";
// The deck. A transparent layer over the screen draws the clock, the reactor and what is being
// said; every panel beside it is its own window with the system's glass behind it, written into
// from here. This page owns the connection to Sunday and everything the panels show, so the
// panels carry no script of their own.
//
// Two modes. The deck: the reactor in the middle with the work in progress orbiting it as chips,
// the live exchange as a caption under it, the last few answers fading at the bottom left and the
// controls in a rail down the right edge. Focus: everything folds away but a small bar at the top
// while Sunday is listening or answering, and a few notes at the top right.
//
// With the overlay off nothing is drawn but that small bar, and only while he is speaking to
// Sunday and it answers him: his words as they are heard, then the answer for a moment.

const api = window.sunday;
const isMac = !!api.isMac;
const $ = (id) => document.getElementById(id);
const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
if (!isMac) document.body.classList.add("win");

const KEYS = isMac
  ? { show: ["Option", "Space"], mode: ["Control", "Option", "Shift", "D"] }
  : { show: ["Alt", "Space"], mode: ["Ctrl", "Alt", "Shift", "D"] };

const MAX_CARDS = 40;
const STATUS_MIN_MS = 700;          // a status line stays this long before the next replaces it
const ANSWER_LINGER_MS = 45000;     // an answer stays in the middle of the deck this long
const FOCUS_LINGER_MS = 9000;       // and in the focus bar this long
const TASK_DONE_MS = 4000;          // finished work stays in the list this long
const TOAST_MS = 9000;
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

// At rest the spokes still breathe, slowly and low, so the reactor is never a still picture.
function frame(t) {
  const kind = live.kind;
  const shown = overlayOn && ui.mode === "deck" && !detailsOpen() && !reduced.matches;
  const on = (kind === "listening" || kind === "speaking") && shown;
  const amp = kind === "speaking" ? 30 : kind === "listening" ? 20 : kind === "thinking" ? 9 : 6;
  const pace = on ? 1 : 0.35;
  for (const b of spokes) {
    const r = 46 + (shown ? amp * level(b.phase, t * pace) : 2);
    b.el.setAttribute("x2", (180 + r * b.s).toFixed(1));
    b.el.setAttribute("y2", (180 - r * b.c).toFixed(1));
  }
  for (const b of waveBars) b.el.style.transform = `scaleY(${on ? (0.08 + 0.92 * level(b.phase, t) * b.env).toFixed(3) : 0.06})`;
  if (shown) requestAnimationFrame(frame);
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

function roundButton(doc, name, title, onClick) {
  const b = make(doc, "button", "round");
  b.type = "button";
  b.title = title;
  b.innerHTML = icon(name);
  b.addEventListener("click", onClick);
  return b;
}

// ---- the caption: the live exchange, on glass under the reactor

const caption = openPanel("caption", "caption");
const capText = make(caption.d, "div", "text");
const capLine = make(caption.d, "div", "line");
const capDot = make(caption.d, "span", "dot");
const capLabel = make(caption.d, "span", "state-label");
const capAsked = make(caption.d, "span", "asked");
const capMeta = make(caption.d, "span", "meta");
capLine.append(capDot, capLabel, capAsked, capMeta);
const capSaid = make(caption.d, "div", "said");
const capChips = make(caption.d, "div", "chips");
capText.append(capLine, capSaid, capChips);
const waveEl = make(caption.d, "div", "wave");
caption.root.append(capText, waveEl);
const waveBars = [];
for (let i = 0; i < 36; i++) {
  const bar = make(caption.d, "i");
  waveEl.append(bar);
  waveBars.push({ el: bar, phase: (i * 1.7) % (Math.PI * 2), env: 0.3 + 0.7 * Math.sin((Math.PI * i) / 35) });
}

// ---- the work in progress: a chip of glass for each task, on the reactor's orbit

const CHIPS = 4;
const chips = [];
for (let i = 0; i < CHIPS; i++) {
  const p = openPanel(`task${i}`, "taskchip hug");
  const top = make(p.d, "div", "head");
  p.$ = { what: make(p.d, "span", "what"), elapsed: make(p.d, "span", "elapsed"), detail: make(p.d, "div", "detail") };
  top.append(make(p.d, "span", "dot"), p.$.what, p.$.elapsed);
  p.root.append(top, p.$.detail, make(p.d, "div", "track"));
  p.task = null;
  chips.push(p);
}

// ---- status capsule

const status = openPanel("status", "status-capsule hug");
const statusItems = {};
for (const key of ["mic", "tasks"]) {
  const item = make(status.d, "div", "item");
  const dot = make(status.d, "span", "dot");
  const text = make(status.d, "span", "label");
  item.append(dot, text);
  status.root.append(item);
  statusItems[key] = { item, dot, text };
}

// ---- the rail of controls down the right edge

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
dockButton("pass", "pointer", "Clicks", () => setPass(!ui.pass));
dockButton("settings", "sliders", "Settings", () => api.settingsMenu());
dockButtons.voice.b.title = "ElevenLabs Voice";
dockButtons.pass.b.title = "Pass Clicks";

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

// ---------------------------------------------------------------- moving panels

// On the deck the status capsule and the rail can be dragged by their glass between the buttons.
// Where they end up is kept per screen size; Reset Panel Layout in Settings puts them back.
const MOVABLE = new Set(["status", "dock"]);
const screenKey = () => `o:${window.innerWidth}x${window.innerHeight}`;

function rectOf(p) {
  const [x, y, width, height] = String(p.placed || "").split(",").map(Number);
  return Number.isFinite(height) ? { x, y, width, height } : null;
}

function gestureFrom(p, el) {
  el.addEventListener("pointerdown", (e) => {
    const r = rectOf(p);
    if (e.target.closest("button") || !r || e.button !== 0 || ui.mode !== "deck") return;
    e.preventDefault();
    el.setPointerCapture(e.pointerId);
    const key = screenKey();
    const sx = e.screenX, sy = e.screenY;
    const move = (ev) => {
      const L = (layouts[key] ||= {});
      L[p.name] = { x: r.x + ev.screenX - sx, y: r.y + ev.screenY - sy };
      layout();
    };
    const end = () => {
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", end);
      el.removeEventListener("pointercancel", end);
      saveUi();
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
  });
}

for (const p of panels.values()) {
  if (!MOVABLE.has(p.name)) continue;
  p.root.classList.add("handle");
  gestureFrom(p, p.root);
}

function resetLayout() {
  delete layouts[screenKey()];
  saveUi();
  layout();
}
api.onResetLayout(resetLayout);

// ---------------------------------------------------------------- the deck's own drawing

const stage = $("stage");
const orbitEl = $("orbit");
const clockEl = document.querySelector(".clock");
const hintsEl = $("hints");
hintsEl.append(keyCaps(document, KEYS.show, "Show or Hide"), keyCaps(document, KEYS.mode, "Deck or Focus"));
const focusHint = $("focusHint");
focusHint.append(keyCaps(document, KEYS.mode, "Brings the deck back"));

function tickClock() {
  const now = new Date();
  const parts = new Intl.DateTimeFormat([], { hour: "numeric", minute: "2-digit" }).formatToParts(now);
  setText($("time"), parts.filter((p) => p.type !== "dayPeriod").map((p) => p.value).join("").trim());
  setText($("ampm"), parts.find((p) => p.type === "dayPeriod")?.value || "");
  const day = now.toLocaleDateString([], { weekday: "long" });
  const month = now.toLocaleDateString([], { month: "short" });
  setText($("date"), `${day} ${pad2(now.getDate())} ${month} ${now.getFullYear()}`);
}

// The devices the brain has connected, under the clock: each ear by its computer, each cube as a
// speaker, amber while its mute button is on.
const EARS = { mac: "Mac", pc: "PC", windows: "PC" };
let devicesKey = "";
function renderDevices() {
  const list = [];
  if (devices) {
    const ears = Array.isArray(devices.ears) ? devices.ears : Object.keys(devices.ears || {});
    for (const e of ears) list.push([EARS[String(e).toLowerCase()] || String(e).replace(/^\w/, (c) => c.toUpperCase()), "cyan"]);
    const cubes = Array.isArray(devices.cubes) ? devices.cubes : [];
    cubes.forEach((c, i) => list.push([cubes.length > 1 ? `Speaker ${i + 1}` : "Speaker", devices.hw_muted?.includes(c) ? "amber" : "mint"]));
  }
  const key = JSON.stringify(list);
  if (key === devicesKey) return;
  devicesKey = key;
  $("devices").replaceChildren(...list.map(([name, tone]) => {
    const d = make(document, "span", `device tone-${tone}`);
    d.append(make(document, "span", "dot"), make(document, "span", null, name));
    return d;
  }));
}

// The last few answers, fading as they age, at the bottom left. What the caption is showing is
// left out until it has gone quiet.
const recentEl = $("recent");
let recentKey = "";
function renderRecent() {
  const showing = live.kind === "idle" ? null : live.card;
  const done = [...cards.values()]
    .filter((c) => c.finished && !c.audible && c.reply && !c.filler && c !== showing)
    .sort((a, b) => b.at - a.at).slice(0, 3);
  const key = done.map((c) => `${c.id}:${c.reply}`).join("|");
  if (key === recentKey) return;
  recentKey = key;
  recentEl.replaceChildren();
  recentEl.hidden = !done.length;
  if (!done.length) return;
  recentEl.append(make(document, "div", "label", "Earlier"));
  for (const c of done) {
    const row = make(document, "div", "line");
    row.append(make(document, "span", "mono", timeOf(c.at)), make(document, "span", "text", c.reply));
    recentEl.append(row);
  }
}

// The orbit round the reactor: a ring of ticks turning slowly, the range ring beyond it, the
// guides out to the sides, and a lead from the ring to each chip of work.
let orbitKey = "";
function drawOrbit(g, leads) {
  const key = JSON.stringify([g, leads]);
  if (key === orbitKey) return;
  orbitKey = key;
  const { W, H, cx, cy, orbit, range } = g;
  const f = (n) => n.toFixed(1);
  const at = (r, deg) => [cx + r * Math.cos((deg * Math.PI) / 180), cy + r * Math.sin((deg * Math.PI) / 180)];
  let minor = "", major = "";
  for (let a = 0; a < 360; a += 5) {
    const [x0, y0] = at(orbit, a), [x1, y1] = at(orbit + (a % 30 ? 6 : 12), a);
    const d = `M${f(x0)} ${f(y0)}L${f(x1)} ${f(y1)}`;
    if (a % 30) minor += d; else major += d;
  }
  const g0 = orbit + 30;
  const guides = `M${cx - g0} ${cy}H${cx - orbit - 190}M${cx + g0} ${cy}H${cx + orbit + 190}M${cx} ${cy - g0}V${cy - orbit - 70}`;
  let lines = "";
  for (const l of leads) {
    lines += `<g class="o-lead tone-${l.tone}"><path d="M${f(l.px)} ${f(l.py)}H${f(l.ex)}"/>`
      + `<circle class="o-node" cx="${f(l.px)}" cy="${f(l.py)}" r="4"/><circle cx="${f(l.px)}" cy="${f(l.py)}" r="9"/></g>`;
  }
  orbitEl.setAttribute("viewBox", `0 0 ${W} ${H}`);
  orbitEl.innerHTML = `<circle class="o-range" cx="${cx}" cy="${cy}" r="${range}"/>`
    + `<path class="o-guides" d="${guides}"/>`
    + `<circle class="o-ring" cx="${cx}" cy="${cy}" r="${orbit}"/>`
    + `<g class="o-spin" style="transform-origin: ${cx}px ${cy}px"><path class="o-minor" d="${minor}"/><path class="o-major" d="${major}"/></g>`
    + lines;
}

// ---------------------------------------------------------------- mode, pass clicks and layout

let ui = { mode: "deck", pass: false };
let focusHintTimer = null;
// The panels he has moved, per screen size: { "o:WxH": { name: { x, y } } }.
let layouts = {};
const saveUi = () => api.ui({ ...ui, layouts });

function setMode(mode) {
  if (mode !== "deck" && mode !== "focus") return;
  const was = ui.mode;
  ui.mode = mode;
  saveUi();
  document.body.classList.toggle("focus", mode === "focus");
  clearTimeout(focusHintTimer);
  focusHint.classList.toggle("on", mode === "focus" && was !== "focus");
  if (mode === "focus") focusHintTimer = setTimeout(() => { focusHint.classList.remove("on"); }, 6000);
  render();
}

function setPass(on) {
  ui.pass = !!on;
  saveUi();
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

// Where the chips of work sit on the orbit, in degrees clockwise from three o'clock: two above
// the reactor, then one below on each side.
const CHIP_ANGLES = [-146, -34, 22, 158];
const CHIP_LEAD = 56;

let lastDetails = "";
function layout() {
  const W = window.innerWidth, H = window.innerHeight;
  if (!W || !H) return;
  const narrow = W < 1700;
  const m = narrow ? 32 : 40, top = narrow ? 84 : 98, gap = 16;
  document.documentElement.style.setProperty("--m", `${m}px`);
  document.body.classList.toggle("short", H < 900);
  for (const p of panels.values()) p.d.body.classList.toggle("narrow", narrow);
  const at = {};
  // The reactor in the middle, a little above centre, with the orbit round it and the caption
  // under it; the recent answers and the hints keep the bottom corners to either side.
  const lens = Math.round(Math.max(220, Math.min(440, H * 0.378, W * 0.26)));
  const orbit = Math.round(lens * 0.714), range = Math.round(lens * 0.976);
  const cx = Math.round(W / 2), cy = Math.round(Math.max(top + orbit + 12, H * 0.406));
  const side = Math.min(340, Math.round(W * 0.2));
  const sx = m + side, sw = W - 2 * sx;
  const headH = measure(head, sw).height;
  const leads = [];
  let details;

  if (ui.mode === "deck" && overlayOn) {
    const L = layouts[screenKey()] || {};
    const open = detailsOpen();
    const st = measure(status, null);
    at.status = { x: m, y: 22 + 56 + 8, width: st.width, height: st.height };
    const ds = measure(dock, null);
    const railTop = Math.ceil(clockEl.getBoundingClientRect().bottom) + 24;
    at.dock = { x: W - m - ds.width, y: Math.max(railTop, Math.min(H - m - ds.height, cy - ds.height / 2)), width: ds.width, height: ds.height };
    // Where he has put a panel wins over where it would go, kept inside the screen.
    for (const name of MOVABLE) {
      const r = at[name], saved = L[name];
      if (!r || !saved) continue;
      at[name] = {
        x: Math.max(0, Math.min(W - r.width, saved.x ?? r.x)),
        y: Math.max(0, Math.min(H - r.height, saved.y ?? r.y)),
        width: r.width, height: r.height,
      };
    }
    if (!open) {
      const capW = Math.min(980, sw);
      const capH = measure(caption, capW).height;
      at.caption = { x: cx - capW / 2, y: Math.min(cy + orbit + 44, H - m - capH), width: capW, height: capH };
      chips.forEach((p, i) => {
        if (!p.task) return;
        const a = (CHIP_ANGLES[i] * Math.PI) / 180;
        const px = cx + orbit * Math.cos(a), py = cy + orbit * Math.sin(a);
        const left = Math.cos(a) < 0;
        const ex = left ? px - CHIP_LEAD : px + CHIP_LEAD;
        const size = measure(p, null);
        at[p.name] = { x: left ? ex - size.width : ex, y: py - size.height / 2, width: size.width, height: size.height };
        leads.push({ px, py, ex, tone: p.task.ended ? "mint" : "amber" });
      });
    }
    if (open) at.head = { x: sx, y: top, width: sw, height: headH };
    const dy = top + headH + gap;
    details = { x: sx, y: dy, width: sw, height: H - m - dy };
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

  document.documentElement.style.setProperty("--cx", `${cx}px`);
  document.documentElement.style.setProperty("--cy", `${cy}px`);
  document.documentElement.style.setProperty("--lens", `${lens}px`);
  document.documentElement.style.setProperty("--side", `${side - 24}px`);
  drawOrbit({ W, H, cx, cy, orbit, range }, leads);
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
// holds one state at a time (Listening, Thinking, Answering...), his words as they were heard,
// one plain line of what is being done while it works, and the answer. The caption follows the
// newest; the answers before it are the recent ones at the bottom left.
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
    note: "", ear: false, heard: false,
  };
  cards.set(id, card);
  while (cards.size > MAX_CARDS) {
    const old = [...cards.values()].find((c) => c.finished && c !== current);
    if (!old) break;
    cards.delete(old.id);
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
  dirty();
}

function setState(card, state) {
  const [label, look] = STATES[state] || STATES.idle;
  card.state = state;
  card.look = look;
  card.label = label;
  dirty();
}

function setYou(card, text, partial, earlier) {
  card.you = text || "";
  card.partial = !!partial;
  card.earlier = !!earlier;
  if (!partial && text) card.heard = true;
  dirty();
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
    dirty();
  };
  const wait = card.statusAt + STATUS_MIN_MS - Date.now();
  if (!text || wait <= 0) apply();
  else card.statusTimer = setTimeout(apply, wait);
}

function setReply(card, text, filler) {
  card.reply = text || "";
  card.filler = !!filler;
  dirty();
}

function playingState(card) {
  return card.kind === "announce" ? "announcing" : card.kind === "late" ? "late" : "speaking";
}

function active(card) {
  card.finished = false;
  dirty();
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
  dirty();
}

let flushQueued = false;
function dirty() {
  if (flushQueued) return;
  flushQueued = true;
  queueMicrotask(() => {
    flushQueued = false;
    render();
  });
}

// ---------------------------------------------------------------- what is live right now

let live = { kind: "idle" };
let lingerTimer = null;
let overlayOn = true;

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
  const linger = ui.mode === "deck" && overlayOn ? ANSWER_LINGER_MS : FOCUS_LINGER_MS;
  const left = c.doneAt + linger - Date.now();
  if (left > 0 && (c.look === "answered" || c.look === "alert")) {
    clearTimeout(lingerTimer);
    lingerTimer = setTimeout(render, left + 30);
    return { kind: c.look === "alert" ? "error" : "answered", card: c };
  }
  return { kind: "idle", card: c };
}

// In focus mode the head is a strip that never goes away: his words as they are heard, and
// between questions the last thing he said.
function headWanted() {
  if (!overlayOn) return stripWanted();
  if (ui.mode === "deck") return detailsOpen();
  return true;
}

// The overlay is off: the bar shows only for an exchange he started, never for an announcement.
const EXCHANGE = new Set(["listening", "thinking", "speaking", "answered", "error"]);
function stripWanted() {
  return EXCHANGE.has(live.kind) && !!live.card && live.card.kind !== "announce" && live.card.kind !== "late";
}

function lastHeard() {
  let last = null;
  for (const c of cards.values()) if (c.you && c.kind !== "announce" && (!last || c.at >= last.at)) last = c;
  return last;
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
  capDot.className = `dot tone-${view.tone}`;
  setText(capLabel, view.label);
  capLabel.className = `state-label tone-${view.tone}`;
  setText(capAsked, view.asked || "");
  setText(capMeta, live.card && !view.quiet ? (live.card.ear ? (isMac ? "This Mac · " : "Mac · ") : "") + timeOf(live.card.at) : "");
  setText(capSaid, view.said || "");
  capSaid.classList.toggle("quiet", !!view.quiet);
  capSaid.classList.toggle("tail", !!view.partial);
  const len = (view.said || "").length;
  capSaid.style.setProperty("--said-size", view.quiet ? "20px" : len <= 90 ? "28px" : len <= 180 ? "24px" : len <= 320 ? "21px" : "19px");
  waveEl.className = `wave tone-${view.tone}${live.kind === "listening" || live.kind === "speaking" ? " on" : ""}`;
  capChips.replaceChildren();
  (view.steps || []).forEach((step, i, all) => {
    const chip = make(caption.d, "span", `chip ${i === all.length - 1 ? "tone-amber" : "tone-mint"}`);
    chip.append(make(caption.d, "span", "dot"), make(caption.d, "span", null, step));
    capChips.append(chip);
  });

  // The head: the same, in one line.
  headMini.className = `mini reactor ${look}`;
  setText(headLabel, view.label);
  headLabel.className = `label tone-${view.tone} t-${view.tone === "idle" ? "faint" : view.tone}`;
  const compactIdle = ui.mode === "deck" && live.kind === "idle" && current && current.reply;
  const heard = ui.mode === "focus" && live.kind === "idle" ? lastHeard() : null;
  if (heard) {
    setText(headLabel, "You Said");
    headLabel.className = "label t-cyan";
    setText(headSaid, `\u201c${heard.you}\u201d`);
    setText(headTime, timeOf(heard.at));
  } else if (compactIdle) {
    setText(headLabel, current.label);
    headLabel.className = "label t-mint";
    setText(headSaid, current.reply);
    setText(headTime, timeOf(current.at));
  } else {
    setText(headSaid, view.said || "");
    setText(headTime, live.card ? timeOf(live.card.at) : "");
  }
  headSaid.classList.toggle("t-faint", !!view.quiet && !compactIdle && !heard);
  headBack.hidden = ui.mode !== "focus";
  head.d.body.classList.toggle("wide", ui.mode === "deck" && overlayOn);
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

  const running = visibleTasks().filter((t) => !t.ended).length;
  if (running) setItem(statusItems.tasks, "amber", `${plural(running, "Task", "Tasks")} Running`);
  else statusItems.tasks.item.hidden = true;
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
  p.b.title = ui.pass ? "Stop Passing Clicks" : "Pass Clicks";
}

function render() {
  renderLive();
  renderStatus();
  renderDock();
  renderDevices();
  renderRecent();
  relayout();
}

// ---------------------------------------------------------------- work in progress

const tasks = new Map();            // id -> { id, label, status, started, ended }

function visibleTasks() {
  // A question's own lookup is already on its card, so it is not listed again here.
  return [...tasks.values()].filter((t) => !(t.id.startsWith("q-") && cards.has(t.id.slice(2))));
}

function startTask(e) {
  if (tasks.has(e.id)) { updateTask(e); return; }
  tasks.set(e.id, { id: e.id, label: e.label || e.kind || "Working", status: e.status || "", started: Date.now(), ended: 0 });
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
  setTimeout(() => { tasks.delete(id); renderWork(); }, TASK_DONE_MS);
}

// The first few take the chips on the orbit; the status capsule counts them all.
function renderWork() {
  const list = visibleTasks();
  chips.forEach((p, i) => {
    const t = list[i] || null;
    p.task = t;
    if (!t) return;
    p.root.className = `root tone-${t.ended ? "mint" : "amber"}${t.ended ? " done" : ""}`;
    setText(p.$.what, t.label);
    p.$.what.title = t.label;
    setText(p.$.elapsed, t.ended ? "Done" : lasted(Date.now() - t.started));
    setText(p.$.detail, t.status && t.status !== t.label ? t.status : "");
  });
  render();
}

function tickTasks() {
  for (const p of chips) if (p.task && !p.task.ended) setText(p.$.elapsed, lasted(Date.now() - p.task.started));
}

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

function onCanvas(e) {
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
  dirty();
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
  dirty();
}

function setVoice(on) {
  elevenlabs = !!on;
  api.voice(elevenlabs);
  dirty();
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
  dirty();
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
// The deck's part of opening and closing (main.js): the veil, and its own drawing rising or sinking.
// The panels' contents rise and sink with it.
api.onOverlayAnim((step) => {
  const body = document.body;
  if (step === "veil" || step === "sink") body.classList.add("away");
  if (step === "veil") body.classList.add("veiled");
  if (step === "rise") body.classList.remove("away", "veiled");
  if (step === "lift") body.classList.remove("veiled");
  for (const p of panels.values()) p.d.body.classList.toggle("away", body.classList.contains("away"));
});

api.onOverlayState((state) => {
  overlay = state;
  reportOverlay();
  if (!!state.shown === overlayOn) return;
  overlayOn = !!state.shown;
  // The switch hides or shows every panel itself, so each one is placed afresh.
  for (const p of panels.values()) p.placed = null;
  render();
});
api.onNotice((text) => { if (ui.mode === "focus") toast({ tone: "cyan", label: "Sunday", text }); });

function unreachable() {
  unreachableSaid = true;
  dirty();
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
      dirty();
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
      dirty();
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
reduced.addEventListener?.("change", () => dirty());

(async () => {
  const saved = await api.uiState();
  ui = { mode: saved?.mode === "focus" ? "focus" : "deck", pass: !!saved?.pass };
  // Only places kept for this layout; ones from an earlier layout no longer mean anything.
  const kept = saved?.layouts && typeof saved.layouts === "object" ? saved.layouts : {};
  layouts = Object.fromEntries(Object.entries(kept).filter(([k]) => k.startsWith("o:")));
  document.body.classList.toggle("focus", ui.mode === "focus");
  api.pass(ui.pass);
  tickClock();
  setInterval(() => { tickClock(); tickTasks(); }, 1000);
  renderWork();
  render();
  connect();
})();
