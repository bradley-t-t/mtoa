// Copies the canvas's libraries out of node_modules into vendor/, which ships with the app,
// so the overlay draws everything offline and loads nothing from the network.
//   node vendor.js
const fs = require("fs");
const path = require("path");
const nm = (p) => path.join(__dirname, "node_modules", p);
const out = (p) => path.join(__dirname, "vendor", p);
const files = [
  ["markdown-it/dist/browser/markdown-it.umd.min.js", "markdown-it.min.js"],
  ["dompurify/dist/purify.min.js", "purify.min.js"],
  ["chart.js/dist/chart.umd.min.js", "chart.umd.min.js"],
  ["@highlightjs/cdn-assets/highlight.min.js", "highlight.min.js"],
  ["@fontsource-variable/jetbrains-mono/files/jetbrains-mono-latin-wght-normal.woff2", "fonts/jetbrains-mono-latin.woff2"],
  ["@fontsource-variable/orbitron/files/orbitron-latin-wght-normal.woff2", "fonts/orbitron-latin.woff2"],
  ["@fontsource/rajdhani/files/rajdhani-latin-500-normal.woff2", "fonts/rajdhani-latin-500.woff2"],
  ["@fontsource/rajdhani/files/rajdhani-latin-600-normal.woff2", "fonts/rajdhani-latin-600.woff2"],
  ["@fontsource/rajdhani/files/rajdhani-latin-700-normal.woff2", "fonts/rajdhani-latin-700.woff2"],
];
fs.mkdirSync(out("fonts"), { recursive: true });
for (const [from, to] of files) {
  fs.copyFileSync(nm(from), out(to));
  console.log(`${to}  ${fs.statSync(out(to)).size} bytes`);
}
for (const lic of ["markdown-it", "dompurify", "chart.js", "@highlightjs/cdn-assets", "@fontsource-variable/jetbrains-mono",
  "@fontsource-variable/orbitron", "@fontsource/rajdhani"]) {
  const src = ["LICENSE", "LICENSE.md", "LICENSE.txt"].map((f) => nm(path.join(lic, f))).find((f) => fs.existsSync(f));
  if (src) fs.copyFileSync(src, out(`LICENSE.${lic.replace(/[@/]/g, "_")}`));
}
