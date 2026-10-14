// Carries a fill request from a page to the CryptoFort host and back.
//
// The page's origin is taken from Chrome (sender.origin), never from anything
// the page says, and it is the only origin the host is asked about. Once a
// sign-in is approved, the login is held in session storage for two minutes for
// that tab and that origin alone, so a sign-in split across pages (the username
// on one, the password on the next, a code after that) needs one approval.
// Session storage lives in memory, is gone when Chrome closes, and is out of
// reach of every page and every content script.
import { totp } from './totp.js';

const HOST = 'cryptofort.chrome_login';
const GRANT_MS = 2 * 60 * 1000;

const grantKey = (tabId, origin) => `grant:${tabId}:${origin}`;

async function heldGrant(tabId, origin, account) {
  const key = grantKey(tabId, origin);
  const { [key]: grant } = await chrome.storage.session.get(key);
  if (!grant) return null;
  if (Date.now() > grant.expires || (account && grant.username !== account)) {
    await chrome.storage.session.remove(key);
    return null;
  }
  return grant;
}

async function native(request) {
  try {
    const reply = await chrome.runtime.sendNativeMessage(HOST, request);
    return reply || { ok: false, error: 'The CryptoFort host gave no answer' };
  } catch (problem) {
    return { ok: false, error: `The CryptoFort host is not reachable: ${problem.message}` };
  }
}

async function fill(message, sender) {
  const origin = sender.origin && sender.origin !== 'null' ? sender.origin : new URL(sender.url).origin;
  if (!/^https?:\/\//.test(origin)) return { ok: false, error: 'Only an http or https page signs in' };
  const tabId = sender.tab.id;
  const account = typeof message.account === 'string' && message.account ? message.account : undefined;
  let grant = await heldGrant(tabId, origin, account);
  if (!grant) {
    const reply = await native({ action: 'fill', origin, account });
    if (!reply.ok) return reply;
    grant = {
      username: reply.username,
      password: reply.password,
      totp: reply.totp || null,
      expires: Date.now() + GRANT_MS,
    };
    const key = grantKey(tabId, origin);
    await chrome.storage.session.set({ [key]: grant });
    chrome.alarms.create(key, { when: grant.expires });
  }
  // A page is handed only what its fields ask for.
  const need = message.need || {};
  const out = { ok: true, username: grant.username };
  if (need.password) out.password = grant.password;
  if (need.code) {
    if (!grant.totp) return { ok: false, error: 'This login carries no one-time code seed' };
    out.code = await totp(grant.totp.seed, { digits: grant.totp.digits, period: grant.totp.period });
  }
  return out;
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name.startsWith('grant:')) chrome.storage.session.remove(alarm.name);
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const held = await chrome.storage.session.get(null);
  const stale = Object.keys(held).filter((key) => key.startsWith(`grant:${tabId}:`));
  if (stale.length) await chrome.storage.session.remove(stale);
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || !message) return false;
  const answer = (promise) => {
    promise.then(sendResponse, (problem) => sendResponse({ ok: false, error: String(problem?.message || problem) }));
    return true;
  };
  if (sender.tab) {
    if (message.type === 'fill') return answer(fill(message, sender));
    if (message.type === 'status') {
      // A sign-in inside a frame reports through the top page, where it can be read.
      chrome.tabs
        .sendMessage(sender.tab.id, { type: 'show-status', state: message.state, text: message.text }, { frameId: 0 })
        .catch(() => {});
    }
    return false;
  }
  // Saving and listing come from the toolbar popup alone.
  if (sender.url !== chrome.runtime.getURL('popup.html')) return false;
  if (message.type === 'list') return answer(native({ action: 'list', origin: message.origin }));
  if (message.type === 'save') {
    return answer(
      native({
        action: 'save',
        origin: message.origin,
        username: message.username,
        password: message.password,
        totp: message.totp,
        extraOrigins: message.extraOrigins,
      }),
    );
  }
  return false;
});
