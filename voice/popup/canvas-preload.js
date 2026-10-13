const { contextBridge, ipcRenderer } = require("electron");

// The details sheet: what the main process hands it, and what it says back.
contextBridge.exposeInMainWorld("canvasHost", {
  onShow: (fn) => ipcRenderer.on("show", (_e, payload) => fn(payload)),
  onUpdate: (fn) => ipcRenderer.on("update", (_e, payload) => fn(payload)),
  onClose: (fn) => ipcRenderer.on("close", (_e, by) => fn(by)),
  onState: (fn) => ipcRenderer.on("state", (_e, state) => fn(state)),
  closing: (by) => ipcRenderer.send("canvas-closing", String(by || "")),
  closed: () => ipcRenderer.send("canvas-closed"),
  height: (h) => ipcRenderer.send("canvas-height", Number(h) || 0),
  copy: (text) => ipcRenderer.send("canvas-copy", String(text).slice(0, 200000)),
  ready: () => ipcRenderer.send("canvas-ready"),
  isMac: process.platform === "darwin",
});
