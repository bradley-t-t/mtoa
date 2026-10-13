/*
 * Drives the parity probe across every route and width, so a capture is one command
 * rather than a session of navigating and pasting.
 *
 * It speaks the DevTools protocol directly over the WebSocket and fetch that Node
 * ships with, so it installs nothing and cannot fall out of step with a browser
 * driver's own release cycle. The browser it drives is whichever Chrome is already
 * on the machine, preferring the Chrome for Testing that Playwright caches.
 *
 *   node capture.js --url http://localhost:4173 --route / --route /pricing \
 *        --width 390 --width 1280 --out before.json [--built]
 *
 * Widths default to a phone and a desktop, because a layout that holds at one and
 * breaks at the other passes a single-width check. Routes default to whatever the
 * first page links to on the same origin.
 *
 * Pass --built when the URL is serving the production build. The after capture is
 * refused without it: a dev server sends unminified modules with no splitting, so it
 * renders a page the visitor never receives and hides every defect the build adds.
 */

const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const CHROME_CANDIDATES = [
  process.env.CHROME_PATH,
  path.join(os.homedir(), 'Library/Caches/ms-playwright/chromium-1234/chrome-mac-arm64/' +
    'Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'),
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Chromium.app/Contents/MacOS/Chromium',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
];

function findChrome() {
  for (const candidate of CHROME_CANDIDATES) {
    if (candidate && fs.existsSync(candidate)) return candidate;
  }
  const cache = path.join(os.homedir(), 'Library/Caches/ms-playwright');
  if (fs.existsSync(cache)) {
    for (const entry of fs.readdirSync(cache)) {
      const shell = path.join(cache, entry, 'chrome-headless-shell-mac-arm64',
        'chrome-headless-shell');
      if (fs.existsSync(shell)) return shell;
    }
  }
  return null;
}

function parseArgs(argv) {
  const out = { route: [], width: [], built: false, settle: 2500, port: 9333 };
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (token === '--built') out.built = true;
    else if (token.startsWith('--')) {
      const key = token.slice(2);
      const value = argv[i + 1];
      if (key === 'route' || key === 'width') out[key].push(value);
      else out[key] = value;
      i += 1;
    }
  }
  if (!out.route.length) out.route = null;
  out.width = (out.width.length ? out.width : ['390', '1280']).map(Number);
  out.settle = Number(out.settle);
  out.port = Number(out.port);
  return out;
}

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

async function waitForEndpoint(port, tries = 60) {
  for (let n = 0; n < tries; n += 1) {
    try {
      const answer = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (answer.ok) return await answer.json();
    } catch (ignored) { /* the browser has not opened the port yet */ }
    await sleep(250);
  }
  throw new Error('the browser never opened its debugging port');
}

/* One page target, addressed directly, so no session bookkeeping is needed. */
async function openPage(port) {
  for (const method of ['PUT', 'GET']) {
    const answer = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method });
    if (answer.ok) return await answer.json();
  }
  throw new Error('could not open a page in the browser');
}

/* The whole protocol client: replies are matched to requests by id, and anything
 * arriving without one is an event, kept aside for later reading. */
function connect(url) {
  const socket = new WebSocket(url);
  const pending = new Map();
  const events = [];
  let nextId = 1;
  const ready = new Promise((done, fail) => {
    socket.addEventListener('open', () => done());
    socket.addEventListener('error', () => fail(new Error('the debugging socket refused')));
  });
  socket.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const { done, fail } = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) fail(new Error(message.error.message));
      else done(message.result);
    } else if (message.method) {
      events.push(message);
    }
  });
  return {
    ready,
    events,
    send(method, params) {
      const id = nextId;
      nextId += 1;
      socket.send(JSON.stringify({ id, method, params: params || {} }));
      return new Promise((done, fail) => {
        pending.set(id, { done, fail });
        setTimeout(() => {
          if (pending.has(id)) {
            pending.delete(id);
            fail(new Error(`${method} did not answer`));
          }
        }, 60000);
      });
    },
    close() { socket.close(); },
  };
}

async function evaluate(page, expression) {
  const result = await page.send('Runtime.evaluate', {
    expression, returnByValue: true, awaitPromise: true,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.exception?.description || 'evaluation failed');
  }
  return result.result.value;
}

/* readyState only says the document finished; the fixed wait that follows is for
 * content the page fetches and renders after that. The extra headroom on the
 * deadline bounds a page that never reaches complete. */
async function settle(page, ms) {
  const deadline = Date.now() + ms + 8000;
  while (Date.now() < deadline) {
    const state = await evaluate(page, 'document.readyState');
    if (state === 'complete') break;
    await sleep(200);
  }
  await sleep(ms);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.url) {
    console.error('capture.js needs --url. See the header of this file.');
    process.exit(2);
  }
  const chrome = findChrome();
  if (!chrome) {
    console.error('No Chrome on this machine. Set CHROME_PATH, or install one:\n' +
      '  npx playwright install chromium');
    process.exit(2);
  }
  const probe = fs.readFileSync(path.join(__dirname, 'parity-probe.js'), 'utf8');
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'parity-'));
  const browser = spawn(chrome, [
    `--remote-debugging-port=${args.port}`, '--headless=new', '--no-first-run',
    '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars',
    `--user-data-dir=${profile}`,
  ], { stdio: 'ignore' });

  const captures = [];
  let page = null;
  try {
    await waitForEndpoint(args.port);
    const target = await openPage(args.port);
    page = connect(target.webSocketDebuggerUrl);
    await page.ready;
    await page.send('Page.enable');
    await page.send('Runtime.enable');

    let routes = args.route;
    if (!routes) {
      await page.send('Emulation.setDeviceMetricsOverride',
        { width: args.width[0], height: 900, deviceScaleFactor: 1, mobile: args.width[0] < 768 });
      await page.send('Page.navigate', { url: args.url });
      await settle(page, args.settle);
      routes = await evaluate(page, `Array.from(document.querySelectorAll('a[href]'))
        .map(function (a) {
          try { var u = new URL(a.href); return u.origin === location.origin ? u.pathname : null; }
          catch (e) { return null; }
        })
        .filter(Boolean)
        .filter(function (v, i, all) { return all.indexOf(v) === i; })
        .sort()`);
      routes = (routes && routes.length ? routes : ['/']).slice(0, 12);
      console.log(`Discovered ${routes.length} route(s): ${routes.join(', ')}`);
    }

    for (const width of args.width) {
      for (const route of routes) {
        const url = new URL(route, args.url).href;
        await page.send('Emulation.setDeviceMetricsOverride', {
          width, height: width < 768 ? 844 : 900, deviceScaleFactor: 1, mobile: width < 768,
        });
        await page.send('Page.navigate', { url });
        await settle(page, args.settle);
        await evaluate(page, probe);
        const summary = await evaluate(page,
          `window.parityProbe.capture({ route: ${JSON.stringify(route)}, built: ${args.built} })`);
        const take = await evaluate(page, 'window.parityProbe.takes()[0]');
        captures.push(take);
        console.log(`  ${String(width).padEnd(5)} ${route.padEnd(24)} ` +
          `${String(summary.elements).padStart(4)} elements, ` +
          `${summary.controls} controls, ${summary.animations} animations, ` +
          `${summary.errors} error(s)`);
      }
    }
  } finally {
    if (page) page.close();
    browser.kill();
    fs.rmSync(profile, { recursive: true, force: true });
  }

  const out = args.out || 'capture.json';
  fs.writeFileSync(out, JSON.stringify({ captures }, null, 1));
  console.log(`\n${captures.length} capture(s) written to ${out}`);
  if (!args.built) {
    console.log('Taken against a server this run has not been told is the production build.\n' +
      'Pass --built once it is, or the after capture is refused.');
  }
}

main().catch((error) => {
  console.error(String(error.message || error));
  process.exit(1);
});
