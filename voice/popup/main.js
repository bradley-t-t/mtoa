// Sunday's overlay. A transparent deck over the screen draws the reactor, the clock and what is
// being said; the conversation, the systems, the work in progress and the controls sit beside
// it as panels of real glass, each a window of its own. Focus mode folds all of it down to a
// small bar that shows only while Sunday is listening or answering. The deck page owns the
// connection to Sunday and opens the panels; this process places them and owns what the page
// cannot: the details sheet, the screen frame, the hotkeys, the tray, the audio meter, the Mac
// ear and the link to the brain.

const { spawn } = require("child_process");
const { app, BrowserWindow, screen, ipcMain, Tray, Menu, nativeImage, nativeTheme, globalShortcut, clipboard, net } = require("electron");
const fs = require("fs");
const os = require("os");
const path = require("path");

const TUNNEL_PORT = 18794;
// SUNDAY_EVENTS points a development copy at another events server. It keeps its own settings
// and leaves the tunnel, the ear, the meter, the game watch and the login item to the installed app.
const DEV = process.env.SUNDAY_EVENTS || "";
// Where the voice server is: `host` in the owner's profile (tools/mtoa_config.py), read
// from this machine's profile or, for a copy built elsewhere, the host.json `npm run host`
// bakes in beside this file.
function hostSettings() {
  const home = os.homedir();
  const files = process.env.MTOA_CONFIG ? [process.env.MTOA_CONFIG]
    : [path.join(home, ".claude", "profile", "mtoa.json"), path.join(home, ".config", "mtoa", "mtoa.json"),
       path.join(__dirname, "host.json")];
  for (const file of files) {
    try {
      const host = JSON.parse(fs.readFileSync(file, "utf8")).host;
      if (host && (host.ssh || host.lan_ip || host.tailnet_ip)) return host;
    } catch (_) { /* not there or not readable: try the next */ }
  }
  return {};
}
const HOST = hostSettings();
const VOICE_PORT = HOST.voice_port || 8794;
const SERVERS = DEV ? [DEV] : process.platform === "darwin"
  // On the Mac a new app needs Local Network permission to reach the LAN, and
  // this Mac's Tailscale is a localhost proxy. An SSH tunnel through the same
  // route `ssh <host.ssh>` uses avoids both, at home or away.
  ? [`ws://127.0.0.1:${TUNNEL_PORT}/events`]
  : [HOST.lan_ip, HOST.tailnet_ip].filter(Boolean).map((ip) => `ws://${ip}:${VOICE_PORT}/events`);
// The public status page's feed: the sites, their uptime and their incidents.
const STATUS_URL = HOST.url ? `${HOST.url.replace(/\/+$/, "")}/status.json` : "";
const STATUS_EVERY_MS = 60000;

const isMac = process.platform === "darwin";
const isWindows = process.platform === "win32";
let deck = null;
let tray = null;
let connected = false;
let audioPlaying = false;
let elevenlabs = null;   // unknown until the server says

if (DEV) app.setPath("userData", path.join(app.getPath("temp"), "sunday-dev"));
if (!app.requestSingleInstanceLock()) app.quit();
nativeTheme.themeSource = "dark";

// ---------------------------------------------------------------- settings

const settingsFile = () => path.join(app.getPath("userData"), "settings.json");
// mode: "deck" or "focus". pass: the panels let clicks through to what is under them.
let settings = { ear: true, mode: "deck", pass: false };

function loadSettings() {
  try { settings = { ...settings, ...JSON.parse(fs.readFileSync(settingsFile(), "utf8")) }; } catch (_) {}
}

function saveSettings() {
  try { fs.writeFileSync(settingsFile(), JSON.stringify(settings, null, 2)); } catch (_) {}
}

// ---------------------------------------------------------------- the deck and its panels

// The deck covers the work area of one screen, the one the cursor was on when it opened or was
// last called to. It only draws: every click goes through it. The panels are opened by the
// page, blank, and allowed here only by these names; each is real glass, vibrancy on the Mac
// and acrylic on Windows, placed where the page asks relative to the deck.
const PANELS = new Set(["conversation", "systems", "work", "incidents", "status", "dock", "head", "toasts"]);
// Pass Clicks lets the pointer through every panel but these, so it can always be turned off.
const ALWAYS_CLICKABLE = new Set(["status", "dock", "head"]);
const panels = new Map();       // name -> its window
const marks = new Map();        // name -> the page load that opened it
const wantedAt = new Map();     // name -> where the page wants it, relative to the deck; null hides it

const cursorDisplay = () => screen.getDisplayNearestPoint(screen.getCursorScreenPoint());
const isRect = (r) => r && ["x", "y", "width", "height"].every((k) => Number.isFinite(r[k])) && r.width > 0 && r.height > 0;

function glass() {
  return {
    width: 400,
    height: 200,
    show: false,
    frame: false,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    focusable: false,
    acceptFirstMouse: true,
    hasShadow: true,
    roundedCorners: true,
    ...(isMac
      ? { transparent: true, vibrancy: "hud", visualEffectState: "active" }
      : { backgroundMaterial: "acrylic", backgroundColor: "#00000000" }),
  };
}

function createDeck() {
  if (isMac) app.dock?.hide();
  deck = new BrowserWindow({
    ...cursorDisplay().workArea,
    show: false,
    frame: false,
    transparent: true,
    backgroundColor: "#00000000",
    hasShadow: false,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    focusable: false,
    enableLargerThanScreen: true,
    ...(isMac ? { type: "panel" } : {}),
    webPreferences: { preload: path.join(__dirname, "preload.js"), backgroundThrottling: false, spellcheck: false },
  });
  // Below the panels and the details on the Mac; on Windows the panels are raised over it.
  deck.setAlwaysOnTop(true, isMac ? "floating" : "screen-saver");
  deck.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  deck.setIgnoreMouseEvents(true);
  deck.webContents.on("will-navigate", (e) => e.preventDefault());
  // A panel's window name is its name, an @ and a mark for the page load that opened it. A load
  // opens each panel once; a later load's panel takes the place of the one before it.
  const nameOf = (frameName) => String(frameName).split("@");
  deck.webContents.setWindowOpenHandler(({ url, frameName }) => {
    const [name, mark] = nameOf(frameName);
    if (url !== "about:blank" || !PANELS.has(name) || (panels.has(name) && marks.get(name) === mark)) return { action: "deny" };
    return { action: "allow", overrideBrowserWindowOptions: glass() };
  });
  deck.webContents.on("did-create-window", (w, details) => {
    const [name, mark] = nameOf(details.frameName);
    const old = panels.get(name);
    if (old && old !== w && !old.isDestroyed()) old.destroy();
    panels.set(name, w);
    marks.set(name, mark);
    w.setAlwaysOnTop(true, "screen-saver");
    w.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
    if (settings.pass && !ALWAYS_CLICKABLE.has(name)) w.setIgnoreMouseEvents(true);
    w.webContents.on("will-navigate", (e) => e.preventDefault());
    w.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    w.on("closed", () => {
      if (panels.get(name) === w) { panels.delete(name); wantedAt.delete(name); }
    });
  });
  deck.webContents.on("render-process-gone", () => {
    closePanels();
    setTimeout(() => { if (deck && !deck.isDestroyed()) deck.reload(); }, 1000);
  });
  deck.webContents.on("did-finish-load", () => {
    sendAudio();
    sendDeck("overlay-state", overlayState());
    if (statusFeed) sendDeck("status-feed", statusFeed);
  });
  deck.loadFile("deck.html");
  deck.once("ready-to-show", () => { if (overlayShown()) { deck.showInactive(); raisePanels(); } });
}

function sendDeck(channel, ...data) {
  if (deck && !deck.isDestroyed()) deck.webContents.send(channel, ...data);
}

function closePanels() {
  for (const w of panels.values()) if (!w.isDestroyed()) w.destroy();
  panels.clear();
  marks.clear();
  wantedAt.clear();
}

function placePanel(name) {
  const w = panels.get(name), r = wantedAt.get(name);
  if (!w || w.isDestroyed()) return;
  if (!r || !overlayShown()) {
    if (w.isVisible()) w.hide();
    return;
  }
  const b = deck.getBounds();
  w.setBounds({ x: Math.round(b.x + r.x), y: Math.round(b.y + r.y), width: Math.round(r.width), height: Math.round(r.height) });
  if (!w.isVisible()) {
    w.showInactive();
    raise(w);
  }
}

function placePanels() {
  for (const name of panels.keys()) placePanel(name);
}

function raisePanels() {
  for (const w of panels.values()) if (!w.isDestroyed() && w.isVisible()) raise(w);
}

function moveDeck(display) {
  deck.setBounds(display.workArea);
  placePanels();
  fitCanvas();
}

function refitDeck() {
  // A display came, went or changed: the deck takes its screen's work area again.
  if (!deck || deck.isDestroyed()) return;
  moveDeck(screen.getDisplayMatching(deck.getBounds()));
}

function setPass(on) {
  settings.pass = on;
  saveSettings();
  for (const [name, w] of panels) if (!w.isDestroyed() && !ALWAYS_CLICKABLE.has(name)) w.setIgnoreMouseEvents(on);
}

ipcMain.on("place", (_e, name, r) => {
  if (!PANELS.has(name)) return;
  wantedAt.set(name, isRect(r) ? r : null);
  placePanel(name);
});
ipcMain.on("pass", (_e, on) => setPass(!!on));
ipcMain.on("ui", (_e, s) => {
  if (!s || (s.mode !== "deck" && s.mode !== "focus")) return;
  settings.mode = s.mode;
  settings.pass = !!s.pass;
  saveSettings();
  updateTray();
});
ipcMain.handle("ui-state", () => ({ mode: settings.mode, pass: !!settings.pass }));
ipcMain.on("settings-menu", () => {
  const owner = panels.get(settings.mode === "focus" ? "head" : "dock");
  Menu.buildFromTemplate(menuItems()).popup(owner && !owner.isDestroyed() ? { window: owner } : {});
});

// Windows lets the taskbar, once used, cover even a topmost window, so the panels and the
// frame take the top again whenever they appear.
function raise(w) {
  if (isWindows && !w.isDestroyed()) w.setAlwaysOnTop(true, "screen-saver");
}

// ---------------------------------------------------------------- the hotkeys and the overlay switch

// OVERLAY_KEY shows or hides the whole overlay. DECK_KEY switches between the deck and focus,
// and from another screen brings the deck to the screen the cursor is on.
//
// On Windows the overlay stays off while a game is running, unless he turns it on: by voice
// ("Sunday, overlay on"), with OVERLAY_KEY, or from the tray. Turned on during a game, it stays on
// until he turns it off or that game ends; then the default, on, comes back. Turned off with no
// game running, it stays off, across restarts, until he turns it on. Sunday still hears and
// answers him the whole time; only what is drawn on this screen waits. game-watch.ps1 says
// which game.
const OVERLAY_KEY = "Control+Alt+Shift+O";
const DECK_KEY = "Control+Alt+Shift+D";
let gaming = null;          // { game, pid, why } while a game is running
let gameOn = false;         // turned on during the game now running
let overlayWas = true;
let overlayLog = null;

function logOverlay(line) {
  if (!isWindows) return;
  try {
    if (!overlayLog) {
      const dir = app.getPath("logs");
      fs.mkdirSync(dir, { recursive: true });
      const file = path.join(dir, "overlay.log");
      try { if (fs.statSync(file).size > 1 << 20) fs.renameSync(file, file + ".1"); } catch (_) {}
      overlayLog = fs.createWriteStream(file, { flags: "a" });
    }
    overlayLog.write(`${new Date().toISOString()} ${line}\n`);
  } catch (_) {}
}

function overlayShown() {
  return gaming ? gameOn : !settings.overlayOff;
}

function overlayState() {
  return { shown: overlayShown(), game: gaming?.game || null, off: !!settings.overlayOff, gameOn };
}

function setOverlay(on, by) {
  if (gaming) gameOn = on;
  if (on) settings.overlayOff = false;
  else if (!gaming) settings.overlayOff = true;
  saveSettings();
  logOverlay(`overlay ${on ? "on" : "off"} by ${by}${gaming ? ` during ${gaming.game}` : ""}`);
  applyOverlay();
  if (on && (by === "hotkey" || by === "tray")) sendDeck("notice", gaming ? "Overlay on until the game ends" : "Overlay on");
}

function setGaming(g) {
  const was = gaming;
  if (was?.pid === g?.pid) { gaming = g; return; }
  gaming = g;
  gameOn = false;           // a new game starts hidden; an ended one takes its "on" with it
  logOverlay(g ? `game: ${g.game} (pid ${g.pid}, ${g.why})` : `game ended: ${was.game}`);
  applyOverlay();
}

function applyOverlay() {
  const on = overlayShown();
  if (on !== overlayWas) {
    overlayWas = on;
    if (!on) {
      if (deck && !deck.isDestroyed()) deck.hide();
      for (const w of panels.values()) if (!w.isDestroyed()) w.hide();
      for (const f of frames) if (!f.isDestroyed()) f.hide();
      holdCanvas();
    } else {
      if (deck && !deck.isDestroyed() && !deck.webContents.isLoading()) deck.showInactive();
      placePanels();
      raisePanels();
      if (lit) for (const f of frames) if (!f.isDestroyed() && !f.webContents.isLoading()) showFrame(f);
      if (canvasHeld && Date.now() - canvasHeld.at < CANVAS_HOLD_MS) openCanvas(canvasHeld.payload);
      canvasHeld = null;
    }
  }
  sendDeck("overlay-state", overlayState());
  updateTray();
}

function deckKey() {
  if (!overlayShown()) {
    setOverlay(true, "hotkey");
    sendDeck("toggle-mode", "deck");
    return;
  }
  const d = cursorDisplay();
  if (d.id !== screen.getDisplayMatching(deck.getBounds()).id) {
    moveDeck(d);
    sendDeck("toggle-mode", "deck");
  } else sendDeck("toggle-mode", null);
}

let watcher = null;
function startGameWatch() {
  if (!isWindows || app.isQuitting) return;
  const res = app.isPackaged ? process.resourcesPath : path.join(__dirname, "native");
  watcher = spawn("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
    "-File", path.join(res, "game-watch.ps1")], { stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
  let buf = "";
  watcher.stdout.on("data", (d) => {
    buf += d.toString();
    const lines = buf.split(/\r?\n/);
    buf = lines.pop();
    for (const line of lines) {
      try {
        const g = JSON.parse(line);
        setGaming(g.gaming ? { game: String(g.game), pid: g.pid, why: String(g.why || "") } : null);
      } catch (_) {}
    }
  });
  watcher.stderr.on("data", (d) => logOverlay("game-watch: " + d.toString().trim()));
  watcher.on("exit", (code) => {
    watcher = null;
    logOverlay(`game-watch exited (${code})`);
    setTimeout(startGameWatch, 5000);
  });
}

function startKeys() {
  const shown = globalShortcut.register(OVERLAY_KEY, () => setOverlay(!overlayShown(), "hotkey"));
  logOverlay(shown ? `hotkey ${OVERLAY_KEY} registered` : `hotkey ${OVERLAY_KEY} is taken by another app`);
  const mode = globalShortcut.register(DECK_KEY, deckKey);
  logOverlay(mode ? `hotkey ${DECK_KEY} registered` : `hotkey ${DECK_KEY} is taken by another app`);
}

ipcMain.on("overlay", (_e, on, by) => setOverlay(!!on, String(by || "voice")));

// ---------------------------------------------------------------- the details sheet

// The detail of an answer: tables, steps, code and charts on a sheet of the same glass. On the
// deck it sits in the middle, under the live exchange and over the dock; in focus mode it comes
// down from the top of the screen. It is as tall as its content, up to the room it has, and it
// goes on Esc, its close button, the Details button, a dismissal said to Sunday, or his next
// question; never on a timer. It follows the overlay switch and the game rule: held back while
// the overlay is off, it comes up if he turns the overlay on within CANVAS_HOLD_MS.
const CANVAS_HOLD_MS = 120000;
const ESC = "Escape";
let canvasWin = null;
let canvasReady = false;
let canvasShown = null;       // the details on screen
let canvasLast = null;        // the last shown, which Show Details can bring back
let canvasHeld = null;        // { payload, at }: held back by the overlay switch or a game
let canvasPending = null;     // waiting on the page to load
let canvasFade = null;
let canvasHideTimer = null;
let canvasNext = null;        // arrived while the last one was still fading out
let detailsAt = null;         // where the deck page wants the details, relative to the deck
let detailsHeight = 0;        // how tall the details' own content is

function createCanvas() {
  canvasReady = false;
  canvasWin = new BrowserWindow({
    show: false,
    frame: false,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    focusable: false,           // never takes the keyboard or the foreground from what he is doing
    acceptFirstMouse: true,
    hasShadow: true,
    roundedCorners: true,
    ...(isMac
      ? { type: "panel", transparent: true, vibrancy: "hud", visualEffectState: "active" }
      : { backgroundMaterial: "acrylic", backgroundColor: "#00000000" }),
    webPreferences: { preload: path.join(__dirname, "canvas-preload.js"), spellcheck: false },
  });
  // Over the deck and under the panels on the Mac; on Windows all of them are topmost.
  canvasWin.setAlwaysOnTop(true, isMac ? "pop-up-menu" : "screen-saver");
  canvasWin.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  canvasWin.webContents.on("will-navigate", (e) => e.preventDefault());
  canvasWin.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  canvasWin.loadFile("canvas.html");
  canvasWin.on("closed", () => { canvasWin = null; canvasReady = false; });
}

function detailsBounds() {
  const b = deck.getBounds();
  const r = detailsAt || { x: 64, y: 52, width: Math.min(940, b.width - 128), height: b.height - 104 };
  const height = detailsHeight ? Math.min(r.height, Math.max(140, detailsHeight)) : r.height;
  return { x: Math.round(b.x + r.x), y: Math.round(b.y + r.y), width: Math.round(r.width), height: Math.round(height) };
}

function fitCanvas() {
  if (canvasShown && canvasWin && !canvasWin.isDestroyed()) canvasWin.setBounds(detailsBounds());
}

function report(id, shown, why = "") {
  sendDeck("canvas-report", { id, shown, why });
}

function fadeCanvas(to, ms, done) {
  // The glass is the window's own, so the window's opacity is what fades it.
  clearInterval(canvasFade);
  if (!isMac || !canvasWin) { done?.(); return; }
  const from = canvasWin.getOpacity(), t0 = Date.now();
  canvasFade = setInterval(() => {
    const k = Math.min(1, (Date.now() - t0) / ms);
    if (canvasWin && !canvasWin.isDestroyed()) canvasWin.setOpacity(from + (to - from) * (1 - (1 - k) ** 3));
    if (k >= 1) { clearInterval(canvasFade); done?.(); }
  }, 16);
}

function canvasArrived(payload) {
  if (canvasShown?.closing) { canvasNext = payload; return; }
  if (!overlayShown()) {
    const first = canvasHeld?.payload.id !== payload.id;
    canvasHeld = { payload, at: canvasHeld?.at && !first ? canvasHeld.at : Date.now() };
    if (first) {
      logOverlay(`canvas held back${gaming ? ` for ${gaming.game}` : ", overlay off"}: ${payload.title || "untitled"}`);
      report(payload.id, false, gaming ? `${gaming.game} is running` : "the overlay is off");
    }
    return;
  }
  if (canvasShown) {
    // The same details grown, or new ones over them: the page swaps the contents in place.
    const fresh = canvasShown.id !== payload.id;
    canvasShown = canvasLast = payload;
    canvasWin.webContents.send("update", payload);
    if (fresh) { report(payload.id, true); sendDeck("canvas-open", { id: payload.id, open: true }); }
    return;
  }
  openCanvas(payload);
}

function openCanvas(payload) {
  if (!canvasWin || canvasWin.isDestroyed()) createCanvas();
  clearTimeout(canvasHideTimer);
  canvasShown = canvasLast = payload;
  canvasHeld = null;
  detailsHeight = 0;
  // The deck makes room for the details first, then they come up where it made it.
  sendDeck("canvas-open", { id: payload.id, open: true });
  const go = () => {
    if (canvasShown !== payload && canvasShown?.id !== payload.id) return;
    canvasWin.setBounds(detailsBounds());
    if (isMac) canvasWin.setOpacity(0);
    canvasWin.webContents.send("show", canvasShown);
    canvasWin.showInactive();
    if (isWindows) {
      raisePanels();
      for (const f of frames) if (lit && !f.isDestroyed() && f.isVisible()) raise(f);
    }
    fadeCanvas(1, 200);
    if (!globalShortcut.isRegistered(ESC) && !globalShortcut.register(ESC, () => closeCanvas("esc", true))) {
      logOverlay("Esc is taken by another app; the details close by voice or their button");
    }
  };
  if (canvasReady) setTimeout(go, 60);
  else canvasPending = go;
  report(payload.id, true);
  logOverlay(`canvas shown: ${payload.title || "untitled"}`);
}

function hideCanvasNow() {
  clearInterval(canvasFade);
  clearTimeout(canvasHideTimer);
  if (globalShortcut.isRegistered(ESC)) globalShortcut.unregister(ESC);
  if (canvasWin && !canvasWin.isDestroyed() && canvasWin.isVisible()) canvasWin.hide();
}

function canvasGone() {
  const id = canvasShown?.id;
  canvasShown = null;
  hideCanvasNow();
  canvasHideTimer = null;
  if (id) sendDeck("canvas-open", { id, open: false });
  if (canvasNext) {
    const payload = canvasNext;
    canvasNext = null;
    canvasArrived(payload);
  }
}

function closeCanvas(by, here, id) {
  // here: he closed it on this screen, so the brain hears and takes it down everywhere else.
  if (!canvasShown || (id && canvasShown.id !== id)) {
    if (canvasHeld && (!id || canvasHeld.payload.id === id)) canvasHeld = null;
    return;
  }
  if (canvasShown.closing) return;
  canvasShown.closing = true;
  if (here) sendDeck("canvas-closed-here", { id: canvasShown.id, by });
  logOverlay(`canvas closed by ${by}`);
  if (globalShortcut.isRegistered(ESC)) globalShortcut.unregister(ESC);
  canvasWin.webContents.send("close", by);
  fadeCanvas(0, 200);
  // The page says when its own fade is done; if it never does, it goes anyway.
  clearTimeout(canvasHideTimer);
  canvasHideTimer = setTimeout(canvasGone, 700);
}

function holdCanvas() {
  // The overlay went off (a game started, or he turned it off) with the details up.
  if (!canvasShown) return;
  canvasHeld = { payload: canvasShown, at: Date.now() };
  logOverlay(`canvas held back${gaming ? ` for ${gaming.game}` : ", overlay off"}: ${canvasShown.title || "untitled"}`);
  canvasWin.webContents.send("close", "overlay off");
  canvasGone();
}

ipcMain.on("canvas", (_e, payload) => {
  if (payload && typeof payload === "object" && payload.id != null) canvasArrived(payload);
});
ipcMain.on("canvas-close", (_e, id, by) => closeCanvas(String(by || "brain"), false, id));
ipcMain.on("canvas-reopen", (_e, id) => {
  if (canvasLast && canvasLast.id === id && !canvasShown) canvasArrived(canvasLast);
});
ipcMain.on("details-close", () => closeCanvas("button", true));
ipcMain.on("details-rect", (_e, r) => {
  detailsAt = isRect(r) ? r : null;
  fitCanvas();
});
ipcMain.on("canvas-height", (_e, h) => {
  detailsHeight = Math.max(0, Math.ceil(Number(h) || 0));
  fitCanvas();
});
ipcMain.on("canvas-speaking", (_e, state) => {
  if (canvasWin && !canvasWin.isDestroyed()) canvasWin.webContents.send("state", state);
});
ipcMain.on("canvas-ready", () => {
  canvasReady = true;
  const go = canvasPending;
  canvasPending = null;
  go?.();
});
ipcMain.on("canvas-closing", (_e, by) => closeCanvas(by || "click", true));
ipcMain.on("canvas-closed", () => { if (!canvasShown || canvasShown.closing) canvasGone(); });
ipcMain.on("canvas-copy", (_e, text) => clipboard.writeText(String(text)));

// ---------------------------------------------------------------- from the page

ipcMain.on("connection", (_e, ok) => {
  connected = ok;
  if (!ok) setLight(false);
  updateTray();
});

ipcMain.on("light", (_e, on) => setLight(!!on));

ipcMain.on("voice", (_e, on) => {
  elevenlabs = !!on;
  updateTray();
});

ipcMain.handle("servers", () => SERVERS);
ipcMain.handle("machine", () => os.hostname().replace(/\.local$/, ""));

// ---------------------------------------------------------------- the status feed

// Read once a minute and handed to the page for the Systems and Incidents panels.
let statusFeed = null;
async function pollStatus() {
  if (!STATUS_URL) return;
  try {
    const res = await net.fetch(STATUS_URL, { cache: "no-store" });
    if (res.ok) {
      const feed = await res.json();
      statusFeed = { updated_at: feed.updated_at, outages_30d: feed.outages_30d, sites: feed.sites, incidents: feed.incidents };
      sendDeck("status-feed", statusFeed);
    }
  } catch (_) {}
  setTimeout(pollStatus, STATUS_EVERY_MS);
}

// ---------------------------------------------------------------- the screen frame

// While the cube's ring is lit, a cyan line runs around the edge of every screen.
// One click-through window per display, above everything, on every space.
const FRAME_HTML = `<!doctype html><html><head><style>
  html, body { margin: 0; height: 100%; background: transparent; overflow: hidden; }
  body::before { content: ""; position: fixed; inset: 0; border: 2px solid #35e0ff;
    box-shadow: inset 0 0 22px 2px rgba(53, 224, 255, 0.5), inset 0 0 3px 1px rgba(53, 224, 255, 0.9);
    opacity: 0; transition: opacity 0.25s ease; }
  body.on::before { opacity: 1; }
</style></head><body></body></html>`;
let frames = [];
let lit = false;
let unlitTimer = null;

function closeFrames() {
  for (const f of frames) if (!f.isDestroyed()) f.destroy();
  frames = [];
}

function makeFrames() {
  closeFrames();
  for (const d of screen.getAllDisplays()) {
    const f = new BrowserWindow({
      ...d.bounds,
      show: false,
      frame: false,
      transparent: true,
      backgroundColor: "#00000000",
      hasShadow: false,
      resizable: false,
      movable: false,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      focusable: false,
      skipTaskbar: true,
      enableLargerThanScreen: true,
      ...(isMac ? { type: "panel" } : {}),
    });
    // Windows shrinks a new window to the work area, which put the bottom line at
    // the top of the taskbar. Set the whole screen again so it runs along the edge.
    if (isWindows) f.setBounds(d.bounds);
    f.setIgnoreMouseEvents(true);
    f.setAlwaysOnTop(true, "screen-saver");
    f.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
    f.loadURL("data:text/html;charset=utf-8," + encodeURIComponent(FRAME_HTML));
    f.webContents.once("did-finish-load", () => { if (lit) showFrame(f); });
    frames.push(f);
  }
}

function showFrame(f) {
  if (f.isDestroyed() || !overlayShown()) return;
  f.showInactive();
  raise(f);
  f.webContents.executeJavaScript("document.body.classList.add('on')").catch(() => {});
}

function setLight(on) {
  if (on === lit) return;
  lit = on;
  clearTimeout(unlitTimer);
  if (on) {
    if (!frames.length) makeFrames();
    for (const f of frames) if (!f.webContents.isLoading()) showFrame(f);
  } else {
    for (const f of frames) if (!f.isDestroyed()) f.webContents.executeJavaScript("document.body.classList.remove('on')").catch(() => {});
    // Hidden once the fade has finished, so nothing sits over the screen while dark.
    unlitTimer = setTimeout(() => { for (const f of frames) if (!f.isDestroyed()) f.hide(); }, 300);
  }
}

function rebuildFrames() {
  // A display came, went or changed size: start the frames over on the new layout.
  if (!frames.length) return;
  makeFrames();
}

// ---------------------------------------------------------------- computer audio

// A small helper per platform prints 1 or 0 whenever this computer starts or stops
// playing sound; the page passes it to the server, which then won't take that sound
// for him. Mac: the level of a system audio process tap. Windows: the output
// endpoint's peak meter.
function sendAudio() {
  sendDeck("computer-audio", audioPlaying);
}

let meter = null;
function startMeter() {
  if (app.isQuitting) return;
  const res = app.isPackaged ? process.resourcesPath : path.join(__dirname, "native");
  const [cmd, args] = isMac
    ? [path.join(res, "audio-playing"), []]
    : ["powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
        "-File", path.join(res, "audio-playing.ps1")]];
  if (isMac && !fs.existsSync(cmd)) return;
  meter = spawn(cmd, args, { stdio: ["ignore", "pipe", "ignore"], windowsHide: true });
  let buf = "";
  meter.stdout.on("data", (d) => {
    buf += d.toString();
    const lines = buf.split(/\r?\n/);
    buf = lines.pop();
    for (const line of lines) {
      if (line.trim() === "1" || line.trim() === "0") {
        audioPlaying = line.trim() === "1";
        sendAudio();
      }
    }
  });
  meter.on("exit", () => { meter = null; audioPlaying = false; sendAudio(); setTimeout(startMeter, 5000); });
}

// ---------------------------------------------------------------- the Mac ear

// mac_ear.py listens for "sunday" on this Mac and defers to the cube: see popup/ear.
// It runs from its own Python environment, made once beside the settings, and
// reaches the brain through the same tunnel as the page.
const EAR_PACKAGES = ["pymicro-wakeword==2.5.0", "numpy", "websockets"];
let ear = null;
let earRetry = 5000;
let earLog = null;

function earLogStream() {
  if (earLog) return earLog;
  const dir = app.getPath("logs");
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, "ear.log");
  try { if (fs.statSync(file).size > 5 << 20) fs.renameSync(file, file + ".1"); } catch (_) {}
  earLog = fs.createWriteStream(file, { flags: "a" });
  return earLog;
}

function earPython(done) {
  const venv = path.join(app.getPath("userData"), "ear-venv");
  const py = path.join(venv, "bin", "python3");
  const marker = path.join(venv, "sunday-packages.json");
  let ready = false;
  try { ready = fs.readFileSync(marker, "utf8") === JSON.stringify(EAR_PACKAGES) && fs.existsSync(py); } catch (_) {}
  if (ready) return done(py);
  const base = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"].find((p) => fs.existsSync(p));
  if (!base) { earLogStream().write("no python3 found for the ear\n"); return; }
  earLogStream().write(`making the ear's Python environment with ${base}\n`);
  const setup = spawn("/bin/sh", ["-c",
    `"${base}" -m venv "${venv}" && "${py}" -m pip -q install --disable-pip-version-check ${EAR_PACKAGES.map((p) => `'${p}'`).join(" ")}`],
    { stdio: ["ignore", "pipe", "pipe"] });
  setup.stdout.pipe(earLogStream(), { end: false });
  setup.stderr.pipe(earLogStream(), { end: false });
  setup.on("exit", (code) => {
    if (code === 0) { fs.writeFileSync(marker, JSON.stringify(EAR_PACKAGES)); done(py); }
    else { earLogStream().write(`ear setup failed (${code}); trying again in a minute\n`); setTimeout(startEar, 60000); }
  });
}

function startEar() {
  if (!isMac || app.isQuitting || ear || !settings.ear) return;
  const res = app.isPackaged ? process.resourcesPath : __dirname;
  const helper = app.isPackaged ? path.join(res, "ear-audio") : path.join(__dirname, "native", "ear-audio");
  const script = path.join(res, "ear", "mac_ear.py");
  if (!fs.existsSync(helper) || !fs.existsSync(script)) return;
  earPython((py) => {
    if (ear || app.isQuitting || !settings.ear) return;
    const started = Date.now();
    ear = spawn(py, ["-u", script, helper, path.join(res, "ear", "wake")],
      { stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, SUNDAY_EAR_SERVER: `ws://127.0.0.1:${TUNNEL_PORT}` } });
    ear.stdout.pipe(earLogStream(), { end: false });
    ear.stderr.pipe(earLogStream(), { end: false });
    ear.on("exit", (code) => {
      ear = null;
      earLogStream().write(`ear exited (${code})\n`);
      // A crash right away backs off; one that ran a while comes straight back.
      earRetry = Date.now() - started > 60000 ? 2000 : Math.min(earRetry * 2, 120000);
      if (settings.ear) setTimeout(startEar, earRetry);
    });
  });
}

function setEar(on) {
  settings.ear = on;
  saveSettings();
  if (on) startEar();
  else ear?.kill();
  updateTray();
}

// ---------------------------------------------------------------- tunnel, tray and settings menu

let tunnel = null;
function openTunnel() {
  if (!isMac || app.isQuitting || !HOST.ssh) return;
  tunnel = spawn("/usr/bin/ssh", [
    "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
    "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
    "-L", `127.0.0.1:${TUNNEL_PORT}:127.0.0.1:${VOICE_PORT}`, HOST.ssh,
  ], { stdio: "ignore" });
  tunnel.on("exit", () => { tunnel = null; setTimeout(openTunnel, 5000); });
}

function trayIcon() {
  // A plain ring, drawn here so the app ships no image files.
  const size = 32;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}">
    <circle cx="16" cy="16" r="9" fill="none" stroke="#000" stroke-width="3" ${connected ? "" : 'stroke-dasharray="4 4"'}/></svg>`;
  const img = nativeImage.createFromDataURL("data:image/svg+xml;base64," + Buffer.from(svg).toString("base64"));
  const sized = img.resize({ width: 18, height: 18 });
  if (isMac) sized.setTemplateImage(true);
  return sized;
}

// The same choices in the tray and behind the Settings button.
function menuItems() {
  return [
    ...(isMac ? [{ label: "Listen on This Mac", type: "checkbox", checked: !!settings.ear,
      click: (item) => setEar(item.checked) }] : []),
    { label: "ElevenLabs Voice", type: "checkbox", checked: !!elevenlabs, enabled: connected && elevenlabs !== null,
      click: () => { sendDeck("toggle-voice"); updateTray(); } },
    { type: "separator" },
    { label: "Show Overlay", type: "checkbox", checked: overlayShown(), accelerator: OVERLAY_KEY,
      registerAccelerator: false, click: (item) => setOverlay(item.checked, "tray") },
    { label: "Focus Mode", type: "checkbox", checked: settings.mode === "focus", accelerator: DECK_KEY,
      registerAccelerator: false, click: (item) => {
        if (!overlayShown()) setOverlay(true, "tray");
        sendDeck("toggle-mode", item.checked ? "focus" : "deck");
      } },
    ...(gaming ? [{ label: `Game Running: ${gaming.game}`, enabled: false }] : []),
    { type: "separator" },
    { label: "Open at Login", type: "checkbox", checked: app.getLoginItemSettings().openAtLogin,
      click: (item) => app.setLoginItemSettings({ openAtLogin: item.checked }) },
    { type: "separator" },
    { label: "Quit Sunday", click: () => app.quit() },
  ];
}

function updateTray() {
  if (!tray) return;
  tray.setImage(trayIcon());
  tray.setToolTip((connected ? "Sunday: Connected" : "Sunday: Not Connected") + (!overlayShown() ? ", Overlay Off" : ""));
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: connected ? "Connected to Sunday" : "Not Connected", enabled: false },
    { type: "separator" },
    ...menuItems(),
  ]));
}

app.on("before-quit", () => {
  app.isQuitting = true; tunnel?.kill(); meter?.kill(); ear?.kill(); watcher?.kill();
  globalShortcut.unregisterAll();
});

app.whenReady().then(() => {
  loadSettings();
  overlayWas = overlayShown();
  if (!DEV) {
    openTunnel();
    startMeter();
    startEar();
    startGameWatch();
  }
  createDeck();
  createCanvas();
  startKeys();
  tray = new Tray(trayIcon());
  updateTray();
  makeFrames();
  pollStatus();
  for (const ev of ["display-added", "display-removed", "display-metrics-changed"]) {
    screen.on(ev, () => {
      rebuildFrames();
      refitDeck();
    });
  }
  if (!DEV && !app.getLoginItemSettings().openAtLogin && !process.argv.includes("--no-login-item")) {
    app.setLoginItemSettings({ openAtLogin: true });
    updateTray();
  }
});

app.on("window-all-closed", (e) => e?.preventDefault?.());
