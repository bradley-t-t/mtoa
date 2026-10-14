// Finds a page's sign-in fields and fills them from the vault when asked.
//
// Two things ask: a trusted ctrl+alt+shift+L pressed in the page, and the
// toolbar popup's Fill Sign-In button. A page's own script can raise neither,
// since a keystroke it synthesizes is never trusted, so a page cannot start a
// fill on its own. The outcome is written to <html data-cryptofort-login> and
// shown in a toast, so whoever pressed the key can read what happened.
(() => {
  if (window.__cryptofortLogin) return;
  window.__cryptofortLogin = true;

  const TOP = window === window.top;
  const USERISH = /user|e-?mail|login|identifier|account|phone|handle/;
  const OTPISH = /one-time-code|otp|totp|2fa|mfa|two.?factor|verification|security.?code|auth\w*.?code|passcode|\bcode\b|token/;
  const SEARCHISH = /search|query/;
  const SUBMITISH = /^(sign ?in|log ?in|login|next|continue|submit|verify|confirm|done|go)\b/i;
  let busy = false;

  const visible = (el) => {
    if (!el || el.disabled || el.readOnly || el.type === 'hidden') return false;
    const rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return false;
    const style = getComputedStyle(el);
    return style.visibility !== 'hidden' && style.display !== 'none' && Number(style.opacity) > 0.05;
  };

  // Every element matching the selector, through open shadow roots as well.
  const collect = (root, selector, out = []) => {
    for (const el of root.querySelectorAll('*')) {
      if (el.matches(selector)) out.push(el);
      if (el.shadowRoot) collect(el.shadowRoot, selector, out);
    }
    return out;
  };

  const hint = (el) =>
    [el.name, el.id, el.autocomplete, el.placeholder, el.getAttribute('aria-label'), el.getAttribute('data-testid')]
      .join(' ')
      .toLowerCase();

  const precedes = (a, b) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

  function findFields() {
    const inputs = collect(document, 'input').filter(visible);
    const passwords = inputs.filter((el) => el.type === 'password');
    const password = passwords.find((el) => /current-password/.test(el.autocomplete)) || passwords[0] || null;
    const textual = inputs.filter(
      (el) => ['text', 'email', 'tel', 'number'].includes(el.type) && !SEARCHISH.test(hint(el)),
    );
    let username = null;
    let otp = null;
    let otpBoxes = null;
    if (password) {
      // The textual field nearest before the password, preferring one that
      // names itself a username, and one in the same form.
      const before = textual.filter((el) => precedes(el, password));
      const sameForm = before.filter((el) => el.form && el.form === password.form);
      const pool = (sameForm.length ? sameForm : before).reverse();
      username = pool.find((el) => el.type === 'email' || USERISH.test(hint(el))) || (sameForm.length ? pool[0] : null);
    } else {
      otp = textual.find((el) => el.autocomplete === 'one-time-code') || null;
      if (!otp) {
        username =
          textual.find((el) => /username|email/.test(el.autocomplete)) ||
          textual.find((el) => el.type === 'email') ||
          textual.find((el) => USERISH.test(hint(el)) && !OTPISH.test(hint(el))) ||
          null;
      }
      if (!otp && !username) {
        otp = textual.find((el) => OTPISH.test(hint(el))) || null;
        const boxes = textual.filter((el) => el.maxLength === 1);
        if (!otp && boxes.length >= 6 && boxes.length <= 8) otpBoxes = boxes;
      }
    }
    return { username, password, otp, otpBoxes };
  }

  // Through the element's own prototype setter, then the events a framework
  // listens for, so React, Vue and plain forms all see the new value.
  function setValue(el, value) {
    el.focus();
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
    el.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
  }

  const label = (el) => (el.innerText || el.value || el.getAttribute('aria-label') || '').trim();

  function submit(field) {
    const form = field.form || field.closest('form');
    const buttons = (root) =>
      collect(root, 'button, input[type="submit"], [role="button"]').filter(
        (el) => visible(el) && el.getAttribute('aria-disabled') !== 'true',
      );
    const inForm = form ? buttons(form) : [];
    const button =
      inForm.find((el) => el.type === 'submit' && SUBMITISH.test(label(el))) ||
      inForm.find((el) => SUBMITISH.test(label(el))) ||
      inForm.find((el) => el.type === 'submit') ||
      buttons(document).find((el) => SUBMITISH.test(label(el)));
    if (button) {
      button.click();
      return true;
    }
    if (form) {
      form.requestSubmit();
      return true;
    }
    return false;
  }

  let toastTimer = null;
  function show(state, text) {
    document.documentElement.setAttribute('data-cryptofort-login', state);
    document.documentElement.setAttribute('data-cryptofort-login-detail', text);
    let toast = document.getElementById('cryptofort-login-status');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'cryptofort-login-status';
      toast.setAttribute('role', 'status');
      toast.setAttribute('aria-live', 'polite');
      toast.style.cssText =
        'position:fixed;z-index:2147483647;right:16px;bottom:16px;max-width:360px;padding:10px 14px;' +
        'border-radius:8px;font:13px/1.4 -apple-system,BlinkMacSystemFont,sans-serif;color:#fff;' +
        'box-shadow:0 4px 16px rgba(0,0,0,.25);pointer-events:none';
      (document.body || document.documentElement).appendChild(toast);
    }
    toast.style.background = state === 'error' ? '#9b1c1c' : state === 'pending' ? '#1f3a8a' : '#14532d';
    toast.textContent = `CryptoFort: ${text}`;
    clearTimeout(toastTimer);
    if (state !== 'pending') toastTimer = setTimeout(() => toast.remove(), 8000);
  }

  function status(state, text) {
    if (TOP) show(state, text);
    else chrome.runtime.sendMessage({ type: 'status', state, text }).catch(() => {});
  }

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  async function run() {
    if (busy) return;
    busy = true;
    try {
      const found = findFields();
      if (!found.username && !found.password && !found.otp && !found.otpBoxes) {
        // A frame without a sign-in stays quiet; the frame that has one answers.
        if (TOP) status('error', `no sign-in field on ${location.host}`);
        return;
      }
      status('pending', `waiting for approval to sign in to ${location.host}`);
      const reply = await chrome.runtime.sendMessage({
        type: 'fill',
        account: document.documentElement.getAttribute('data-cryptofort-account') || undefined,
        need: { password: Boolean(found.password), code: Boolean(found.otp || found.otpBoxes) },
      });
      if (!reply || !reply.ok) {
        status('error', reply?.error || 'the extension gave no answer');
        return;
      }
      // The page may have re-rendered while the approval was waiting.
      const fields = findFields();
      let last = null;
      if (fields.username && reply.username) setValue((last = fields.username), reply.username);
      if (fields.password && reply.password) setValue((last = fields.password), reply.password);
      if (fields.otp && reply.code) setValue((last = fields.otp), reply.code);
      if (fields.otpBoxes && reply.code) {
        fields.otpBoxes.forEach((box, i) => setValue(box, reply.code[i] || ''));
        last = fields.otpBoxes[fields.otpBoxes.length - 1];
      }
      if (!last) {
        status('error', `the sign-in fields on ${location.host} changed before they could be filled`);
        return;
      }
      await sleep(250);
      const sent = submit(last);
      status('filled', `${sent ? 'signed in' : 'filled the sign-in'} to ${location.host} as ${reply.username}`);
    } catch (problem) {
      status('error', String(problem?.message || problem));
    } finally {
      busy = false;
    }
  }

  window.addEventListener(
    'keydown',
    (event) => {
      if (!event.isTrusted || !event.ctrlKey || !event.altKey || !event.shiftKey || event.metaKey) return;
      if (event.code !== 'KeyL' && event.keyCode !== 76 && (event.key || '').toLowerCase() !== 'l') return;
      event.preventDefault();
      event.stopPropagation();
      run();
    },
    true,
  );

  chrome.runtime.onMessage.addListener((message, sender) => {
    if (sender.id !== chrome.runtime.id || !message) return;
    if (message.type === 'run') run();
    if (message.type === 'show-status' && TOP) show(message.state, message.text);
  });
})();
