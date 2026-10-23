// 23.10 (owner: «в закрытой системе NOVA AI не отвечает») — what the panel really sends to POST /v2/admin/nova.
// Opens the live panel (https://cryptonova.vip/app.html) in headless Chrome, waits for its data, then builds the chat request
// exactly as ask() does (ANALYST_SYS + «Контекст панели (JSON)» + the question) WITHOUT sending it, and prints its size and the
// biggest parts. Writes the body to panel_body.json for the model check that follows. No key, no token, nothing is sent.
// usage: node scripts/nova_panel_probe.js [url]   (needs puppeteer-core and Chrome: .github/workflows/nova-panel-probe.yml)
const puppeteer = require('puppeteer-core');
const fs = require('fs');
(async () => {
  const url = process.argv[2] || 'https://cryptonova.vip/app.html';
  const exe = ['/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium-browser'].find(p => fs.existsSync(p));
  const b = await puppeteer.launch({ executablePath: exe, headless: 'new', args: ['--no-sandbox'] });
  const p = await b.newPage();
  const failed = [], errors = [];
  p.on('requestfailed', r => failed.push(r.url().split('?')[0].slice(0, 90) + ' ' + ((r.failure() || {}).errorText || '')));
  const perr = [];
  p.on('pageerror', e => perr.push(String(e && e.message || e).slice(0, 300)));
  p.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text().slice(0, 160)); });
  await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await new Promise(r => setTimeout(r, 50000));
  const out = await p.evaluate(() => {
    const B = s => new TextEncoder().encode(s).length;
    try {
      const C = ctxForAI();
      const user = `Контекст панели (JSON):\n${JSON.stringify(C)}\n\nВопрос: Что сейчас с рынком и что делаем?`;
      const body = JSON.stringify({ system: ANALYST_SYS, user, max_tokens: 600 });
      const parts = {}, cur = {};
      for (const k in C) parts[k] = B(JSON.stringify(C[k]));
      for (const k in (C.current || {})) cur[k] = B(JSON.stringify(C.current[k]));
      return { bytes: B(body), chars: body.length, parts, current: cur, events: ((C.current || {}).events || []).length,
               coins: (C.coins || []).length, news: (C.news || []).length, route: typeof aiRoute === 'function' ? aiRoute() : null, body };
    } catch (e) { return { err: String(e), href: location.href, title: document.title, html: document.documentElement.outerHTML.length,
                           scripts: document.scripts.length, ask: typeof askLLM, ctx: typeof ctxForAI, pack: typeof ctxPack,
                           adm: typeof adminTok, sizes: [...document.scripts].map(x => (x.src ? 'src:' + x.src.split('/').pop() : x.text.length)) }; }
  });
  if (out.body) { fs.writeFileSync('panel_body.json', out.body); delete out.body; }
  console.log(JSON.stringify(out));
  console.log('uncaught page errors: ' + perr.length + (perr.length ? ' · ' + [...new Set(perr)].slice(0, 6).join(' | ') : ''));
  console.log('page errors: ' + errors.length + (errors.length ? ' · ' + errors.slice(0, 6).join(' | ') : ''));
  console.log('failed requests: ' + failed.length + (failed.length ? ' · ' + failed.slice(0, 8).join(' | ') : ''));
  await b.close();
})().catch(e => { console.log(JSON.stringify({ err: String(e) })); process.exit(0); });
