// Bakes the voice host's address into host.json beside main.js, so a copy built here
// and installed on a machine that holds no profile still finds the server. Only the
// `host` block of the owner's profile (tools/mtoa_config.py) is copied.
const fs = require("fs");
const os = require("os");
const path = require("path");

const home = os.homedir();
const files = process.env.MTOA_CONFIG ? [process.env.MTOA_CONFIG]
  : [path.join(home, ".claude", "profile", "mtoa.json"), path.join(home, ".config", "mtoa", "mtoa.json")];
let host = {};
for (const file of files) {
  try { host = JSON.parse(fs.readFileSync(file, "utf8")).host || {}; break; } catch (_) { /* next */ }
}
const keep = {};
for (const key of ["ssh", "url", "lan_ip", "tailnet_ip", "voice_port"]) if (host[key]) keep[key] = host[key];
fs.writeFileSync(path.join(__dirname, "host.json"), JSON.stringify({ host: keep }, null, 2) + "\n");
console.log(Object.keys(keep).length ? `host.json: ${Object.keys(keep).join(", ")}` : "host.json: no host in the profile");
