// RFC 6238 one-time codes, HMAC-SHA1, from a base32 seed.
const ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';

function base32(seed) {
  const clean = seed.toUpperCase().replace(/[\s=-]/g, '');
  const bytes = [];
  let bits = 0;
  let value = 0;
  for (const char of clean) {
    const index = ALPHABET.indexOf(char);
    if (index < 0) throw new Error('the one-time code seed is not base32');
    value = (value << 5) | index;
    bits += 5;
    if (bits >= 8) {
      bytes.push((value >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return new Uint8Array(bytes);
}

export async function totp(seed, { digits = 6, period = 30, now = Date.now() } = {}) {
  const key = await crypto.subtle.importKey('raw', base32(seed), { name: 'HMAC', hash: 'SHA-1' }, false, ['sign']);
  const counter = Math.floor(now / 1000 / period);
  const message = new DataView(new ArrayBuffer(8));
  message.setUint32(0, Math.floor(counter / 2 ** 32));
  message.setUint32(4, counter >>> 0);
  const mac = new Uint8Array(await crypto.subtle.sign('HMAC', key, message.buffer));
  const offset = mac[mac.length - 1] & 0x0f;
  const code =
    ((mac[offset] & 0x7f) << 24) | (mac[offset + 1] << 16) | (mac[offset + 2] << 8) | mac[offset + 3];
  return String(code % 10 ** digits).padStart(digits, '0');
}
