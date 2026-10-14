// The toolbar popup: what the vault holds for this page, a fill on demand, and
// the one place a new login is typed in, by the person at the machine.
const $ = (id) => document.getElementById(id);
let tab = null;
let origin = null;

function note(text, bad = false) {
  $('note').textContent = text;
  $('note').className = bad ? 'note bad' : 'note';
}

async function refresh() {
  const reply = await chrome.runtime.sendMessage({ type: 'list', origin });
  const box = $('saved');
  box.textContent = '';
  if (!reply?.ok) {
    note(reply?.error || 'The vault could not be read', true);
    return;
  }
  if (!reply.logins.length) {
    box.textContent = 'The vault holds no login for this site.';
    box.style.marginBottom = '10px';
    return;
  }
  const list = document.createElement('ul');
  for (const login of reply.logins) {
    const item = document.createElement('li');
    item.textContent = login.usernames.join(', ') || login.entry;
    list.appendChild(item);
  }
  box.appendChild(list);
}

async function start() {
  [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  try {
    origin = new URL(tab?.url || '').origin;
  } catch {
    origin = null;
  }
  if (!origin || !/^https?:\/\//.test(origin)) {
    $('origin').textContent = 'Only an http or https page signs in.';
    return;
  }
  $('origin').textContent = origin;
  $('fill').disabled = false;
  $('toggle').disabled = false;
  refresh();
}

$('fill').addEventListener('click', async () => {
  // Every frame is asked; the one holding the sign-in answers.
  await chrome.tabs.sendMessage(tab.id, { type: 'run' }).catch(() => {});
  window.close();
});

$('toggle').addEventListener('click', () => {
  $('save').hidden = !$('save').hidden;
  if (!$('save').hidden) $('username').focus();
});

$('save').addEventListener('submit', async (event) => {
  event.preventDefault();
  note('Saving...');
  const reply = await chrome.runtime.sendMessage({
    type: 'save',
    origin,
    username: $('username').value,
    password: $('password').value,
    totp: $('totp').value,
    extraOrigins: $('extra').value.split(/[\s,]+/).filter(Boolean),
  });
  $('password').value = '';
  $('totp').value = '';
  if (!reply?.ok) {
    note(reply?.error || 'The vault did not answer', true);
    return;
  }
  $('save').hidden = true;
  note(`Saved ${reply.entry}`);
  refresh();
});

start();
