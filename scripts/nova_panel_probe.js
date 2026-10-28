// 23.10 (owner: «в закрытой системе NOVA AI не отвечает») — what the panel really sends to POST /v2/admin/nova.
// Opens the live panel (https://cryptonova.vip/app.html) in headless Chrome with a dummy «Управление» token in sessionStorage
// (so the chat takes the server route), asks one question through the chat form, and catches the request to /v2/admin/nova
// BEFORE it leaves the browser (answered locally with a fake answer — the real API and the model are never called from here).
// Prints its size and the biggest parts; writes the body to panel_body.json for the checks that follow. No real token, no key.
// usage: node scripts/nova_panel_probe.js [url]   (needs puppeteer-core and Chrome: .github/workflows/nova-panel-probe.yml)
const puppeteer = require('puppeteer-core');
const fs = require('fs');
(async () => {
  const url = process.argv[2] || 'https://cryptonova.vip/app.html';
  const exe = ['/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium-browser'].find(p => fs.existsSync(p));
  const b = await puppeteer.launch({ executablePath: exe, headless: 'new', args: ['--no-sandbox'] });
  const p = await b.newPage();
  const perr = [];
  p.on('pageerror', e => perr.push(String(e && e.message || e).slice(0, 300)));
  await p.evaluateOnNewDocument(() => { try { sessionStorage.setItem('cnq_admin_token', 'probe-dummy-token'); } catch (e) {} });
  let caught = null;
  await p.setRequestInterception(true);
  p.on('request', r => {
    if (r.url().includes('/v2/admin/nova') && r.method() === 'POST') {
      caught = r.postData() || '';
      return r.respond({ status: 200, contentType: 'application/json', headers: { 'access-control-allow-origin': '*' },
                         body: JSON.stringify({ answer: 'probe: перехвачено', ms: 1 }) });
    }
    r.continue();
  });
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await new Promise(r => setTimeout(r, 50000));
  const ui = await p.evaluate(() => {
    const f = document.getElementById('chatForm'), i = document.getElementById('chatIn');
    if (!f || !i) return 'no chat form';
    i.value = 'Что сейчас с рынком и что делаем?';
    try { f.requestSubmit(); } catch (e) { f.dispatchEvent(new Event('submit', { cancelable: true })); }
    return 'asked';
  });
  await new Promise(r => setTimeout(r, 6000));
  const shown = await p.evaluate(() => { const m = document.querySelectorAll('#chatMsgs .a'); return m.length ? m[m.length - 1].textContent.slice(0, 160) : ''; });
  const out = { ui, shown, caught: caught !== null };
  if (caught !== null) {
    const B = s => Buffer.byteLength(s, 'utf8');
    out.body_bytes = B(caught);
    try {
      const d = JSON.parse(caught);
      out.system_bytes = B(d.system || ''); out.user_bytes = B(d.user || ''); out.max_tokens = d.max_tokens;
      const m = /^Контекст панели \(JSON\):\n([\s\S]*)\n\nВопрос: /.exec(d.user || '');
      if (m) {
        const C = JSON.parse(m[1]);
        out.parts = Object.fromEntries(Object.keys(C).map(k => [k, B(JSON.stringify(C[k]))]));
        out.current = Object.fromEntries(Object.keys(C.current || {}).map(k => [k, B(JSON.stringify(C.current[k]))]));
        out.counts = { coins: (C.coins || []).length, news: (C.news || []).length, events: ((C.current || {}).events || []).length,
                       positions: ((C.portfolio || {}).positions || []).length };
      }
    } catch (e) { out.parse = String(e); }
    fs.writeFileSync('panel_body.json', caught);
  }
  console.log(JSON.stringify(out));
  console.log('uncaught page errors: ' + perr.length + (perr.length ? ' · ' + [...new Set(perr)].slice(0, 6).join(' | ') : ''));
  await b.close();
})().catch(e => { console.log(JSON.stringify({ err: String(e) })); process.exit(0); });
