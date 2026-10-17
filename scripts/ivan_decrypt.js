// «Иван тест»: decrypt web/ivan.enc.json in the browser. WebCrypto (crypto.subtle) + atob only, no libraries.
// ivanDecrypt is self-contained and can be pasted into the panel as is. Format (cnq/execution/ivan_watch.py):
//   km = PBKDF2-SHA256(utf8(pass), salt, 200000 iterations, 512 bits); encKey = km[0:32], macKey = km[32:64]
//   tag = HMAC-SHA256(macKey, salt || nonce || ct)  is checked first (wrong pass → Error('Неверный пароль'))
//   keystream block i = HMAC-SHA256(encKey, nonce || uint32_be(i)); plaintext = ct XOR keystream (UTF-8 JSON)
async function ivanDecrypt(blob, pass) {
  if (typeof blob === 'string') blob = JSON.parse(blob);
  if (!blob || blob.v !== 1) throw new Error('Неизвестный формат файла');
  const S = crypto.subtle;
  const u8 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
  const salt = u8(blob.salt), nonce = u8(blob.nonce), ct = u8(blob.ct), tag = u8(blob.tag);
  const base = await S.importKey('raw', new TextEncoder().encode(pass), 'PBKDF2', false, ['deriveBits']);
  const km = new Uint8Array(await S.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt, iterations: 200000 }, base, 512));
  const hk = raw => S.importKey('raw', raw, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const encKey = await hk(km.slice(0, 32)), macKey = await hk(km.slice(32, 64));
  const msg = new Uint8Array(salt.length + nonce.length + ct.length);
  msg.set(salt, 0); msg.set(nonce, salt.length); msg.set(ct, salt.length + nonce.length);
  const t = new Uint8Array(await S.sign('HMAC', macKey, msg));
  let diff = t.length ^ tag.length;
  for (let i = 0; i < t.length; i++) diff |= t[i] ^ (tag[i] | 0);
  if (diff) throw new Error('Неверный пароль');
  const blocks = await Promise.all(Array.from({ length: Math.ceil(ct.length / 32) }, (_, i) => {
    const m = new Uint8Array(nonce.length + 4);
    m.set(nonce, 0);
    new DataView(m.buffer).setUint32(nonce.length, i, false);
    return S.sign('HMAC', encKey, m).then(b => new Uint8Array(b));
  }));
  const pt = new Uint8Array(ct.length);
  for (let i = 0; i < ct.length; i++) pt[i] = ct[i] ^ blocks[i >> 5][i & 31];
  return JSON.parse(new TextDecoder().decode(pt));
}
if (typeof module !== 'undefined' && module.exports) module.exports = { ivanDecrypt };
