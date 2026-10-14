// Chrome Login: the browser half of tools/chrome-login.py.
//
// The command line holds the vault and the owner's approval; this worker holds
// the tabs. It keeps one native-messaging port open to host.py, which relays
// requests from the command line, and answers three of them:
//
//   tabs    every open tab's id and URL, and which one is in front
//   probe   which sign-in fields a tab shows right now, on a host the login names
//   fill    put the values it is handed into those fields, and optionally submit
//
// Nothing here stores a value or sends one anywhere but the page it was meant
// for. Every probe and fill is checked twice against the hosts the login names:
// here, against the URL Chrome reports for the tab and for each frame, which a
// page cannot forge, and again inside the frame before a value is touched. The
// page code runs in the extension's isolated world, so the page's own scripts
// can neither read the values nor patch the functions that check the host, and
// it is pinned to the exact documents that passed, so a navigation in between
// lands it nowhere.

const HOST = 'com.mtoa.chrome_login';
const LOOPBACK = ['localhost', '127.0.0.1', '[::1]'];
let port = null;
let backoff = 1000;

function connect() {
  if (port) return;
  port = chrome.runtime.connectNative(HOST);
  port.onMessage.addListener((msg) => {
    backoff = 1000;
    handle(msg).then(
      (result) => reply(msg.id, { ok: true, result }),
      (err) => reply(msg.id, { ok: false, error: String((err && err.message) || err) }),
    );
  });
  // The host exits when Chrome closes the port and is missing until `install`
  // runs, so a lost port is retried on a widening interval rather than given up.
  port.onDisconnect.addListener(() => {
    void chrome.runtime.lastError;
    port = null;
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, 60000);
  });
}

function reply(id, body) {
  if (port) port.postMessage({ id, ...body });
}

async function handle(msg) {
  if (msg.op === 'tabs') return listTabs();
  if (msg.op === 'probe') return inFrames(msg.tabId, msg.hosts, 'probe', {}, false);
  if (msg.op === 'fill') return inFrames(msg.tabId, msg.hosts, 'fill', msg.values || {}, !!msg.submit);
  throw new Error(`unknown op ${msg.op}`);
}

async function listTabs() {
  const [tabs, focused] = await Promise.all([
    chrome.tabs.query({}),
    chrome.windows.getLastFocused().catch(() => null),
  ]);
  return tabs.map((t) => ({
    tabId: t.id,
    url: t.url || t.pendingUrl || '',
    title: t.title || '',
    active: !!t.active,
    front: !!(focused && t.active && t.windowId === focused.id),
  }));
}

function hostOf(url) {
  try {
    return new URL(url).hostname;
  } catch (e) {
    return '';
  }
}

// A sign-in is filled over https, or over http only on this machine's own
// loopback, where a local development server has no certificate to offer.
function allowed(url, hosts) {
  let u;
  try {
    u = new URL(url);
  } catch (e) {
    return false;
  }
  if (!hosts.includes(u.hostname)) return false;
  return u.protocol === 'https:' || (u.protocol === 'http:' && LOOPBACK.includes(u.hostname));
}

async function inFrames(tabId, hosts, mode, values, submit) {
  if (!Array.isArray(hosts) || hosts.length === 0) throw new Error('no hosts named');
  const tab = await chrome.tabs.get(tabId);
  const host = hostOf(tab.url || '');
  if (!allowed(tab.url || '', hosts)) return { host, refused: true, frames: [] };
  const frames = (await chrome.webNavigation.getAllFrames({ tabId })) || [];
  const top = frames.find((f) => f.frameId === 0);
  if (!top || !allowed(top.url, hosts)) return { host, refused: true, frames: [] };
  const documentIds = frames.filter((f) => allowed(f.url, hosts) && f.documentId).map((f) => f.documentId);
  const results = await chrome.scripting.executeScript({
    target: { tabId, documentIds },
    world: 'ISOLATED',
    func: signIn,
    args: [hosts, mode, values, submit],
  });
  return {
    host,
    refused: false,
    frames: results.map((r) => ({ frameId: r.frameId, ...(r.result || {}) })),
  };
}

// Runs inside the page, in the extension's isolated world. Chrome serializes
// this function alone, so everything it needs is declared inside it.
async function signIn(hosts, mode, values, submit) {
  const loopback = ['localhost', '127.0.0.1', '[::1]'];
  const secure =
    location.protocol === 'https:' ||
    (location.protocol === 'http:' && loopback.includes(location.hostname));
  if (!hosts.includes(location.hostname) || !secure) return { refused: true };

  const visible = (el) => {
    if (el.disabled || el.readOnly) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2 || r.right <= 0 || r.bottom <= 0) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && Number(s.opacity) > 0.05;
  };
  const found = [];
  const walk = (root) => {
    for (const el of root.querySelectorAll('input')) found.push(el);
    for (const el of root.querySelectorAll('*')) if (el.shadowRoot) walk(el.shadowRoot);
  };
  walk(document);
  const inputs = found.filter(visible);
  const kind = (el) => (el.getAttribute('type') || 'text').toLowerCase();
  const textual = (el) => ['text', 'email', 'tel', 'number'].includes(kind(el));
  const words = (el) =>
    [el.name, el.id, el.getAttribute('autocomplete'), el.placeholder, el.getAttribute('aria-label')]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
  const OTP = /one-time-code|otp|totp|2fa|mfa|two.?factor|verification|verify|security.?code|auth.*code|passcode|\bcode\b|token/;
  const NOT_OTP = /postal|zip|promo|coupon|captcha|country|area/;
  const USER = /user|email|e-mail|login|account|identifier|phone|mobile|handle/;

  const passwords = inputs.filter((el) => kind(el) === 'password');
  const password =
    passwords.find((el) => el.getAttribute('autocomplete') !== 'new-password') || passwords[0] || null;
  const boxes = inputs.filter((el) => textual(el) && el.maxLength === 1);
  const totp =
    inputs.find(
      (el) =>
        textual(el) &&
        el.maxLength !== 1 &&
        (el.getAttribute('autocomplete') === 'one-time-code' ||
          (OTP.test(words(el)) && !NOT_OTP.test(words(el)))),
    ) || null;
  const otpBoxes = !totp && boxes.length >= 6 ? boxes.slice(0, 8) : [];
  const users = inputs.filter(
    (el) =>
      textual(el) &&
      el !== totp &&
      !otpBoxes.includes(el) &&
      !/search/.test(words(el)) &&
      (['username', 'email'].includes(el.getAttribute('autocomplete')) ||
        kind(el) === 'email' ||
        USER.test(words(el))),
  );
  const username =
    (password &&
      users.find(
        (el) => el.form === password.form && el.compareDocumentPosition(password) & Node.DOCUMENT_POSITION_FOLLOWING,
      )) ||
    users[0] ||
    null;

  const state = {
    refused: false,
    username: !!username,
    password: !!password,
    totp: !!(totp || otpBoxes.length),
  };
  if (mode !== 'fill') return state;

  // Typed the way a person's keystrokes arrive first, so frameworks that only
  // trust input events see real ones; the value setter is the fallback where
  // the page will not take focus.
  const put = (el, v) => {
    el.focus();
    let done = false;
    try {
      el.select();
      done = document.execCommand('insertText', false, v) && el.value === v;
    } catch (e) {
      done = false;
    }
    if (!done) {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, v);
      el.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true, inputType: 'insertText', data: v }));
    }
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return el.value === v;
  };

  const filled = [];
  let last = null;
  if (values.username && username && put(username, values.username)) {
    filled.push('username');
    last = username;
  }
  if (values.password && password && put(password, values.password)) {
    filled.push('password');
    last = password;
  }
  if (values.totp && totp && put(totp, values.totp)) {
    filled.push('totp');
    last = totp;
  } else if (values.totp && otpBoxes.length) {
    const digits = String(values.totp).split('');
    if (digits.length <= otpBoxes.length && digits.every((d, i) => put(otpBoxes[i], d))) {
      filled.push('totp');
      last = otpBoxes[digits.length - 1];
    }
  }

  let submitted = null;
  if (submit && last) {
    await new Promise((r) => setTimeout(r, 250));
    const usable = (el) => {
      const r = el.getBoundingClientRect();
      return !el.disabled && r.width > 1 && r.height > 1 && getComputedStyle(el).visibility !== 'hidden';
    };
    const form = last.form;
    const LABEL = /^(next|continue|sign ?in|log ?in|login|submit|verify|confirm|done)$/i;
    let button = form
      ? [...form.querySelectorAll('button, input[type=submit], input[type=image]')].find(
          (b) => usable(b) && (b.type || '').toLowerCase() === 'submit',
        )
      : null;
    if (!button) {
      button = [...document.querySelectorAll('button, [role=button], input[type=submit]')].find(
        (b) => usable(b) && LABEL.test((b.innerText || b.value || b.getAttribute('aria-label') || '').trim()),
      );
    }
    if (button) {
      button.click();
      submitted = 'clicked';
    } else if (form) {
      form.requestSubmit();
      submitted = 'submitted';
    }
  }
  return { ...state, filled, submitted };
}

chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
connect();
