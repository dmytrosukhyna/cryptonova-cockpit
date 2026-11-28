/* CRYPTO NOVΛ — «Ручной стол (Дмитрий)», stage 1 (19.11.2026): the owner's semi-manual PAPER desk in «Управление» (admin.html).
   Data: GET/POST /v2/admin/manual* (cnq/api/manual_desk.py) — never a public file, never an exchange order.
   CNManual.html(D, {msg, preview, sym}) → HTML · CNManual.mount(el, api, rerender)
   api = {resolve(q), preview(sym, side), open(sym, side), close(id), stop(id, px), settings(form), candles(sym, bar), flow(sym), side(sym), level(form), walls(sym)}
   19.11 23:39: large limit orders («стенки») as bands on the chart and in «Крупные заявки» (age, grew / shrank, vanished — with the
   spoofing hint), the large-trades tape, and the open positions' entry / stop / TP lines on the chart; walls refresh every 30 s.
   Stage 2 (owner 19.11 23:13): ONE trading screen — the chart in the centre (1h / 4h / 1d, auto levels dashed, the owner's levels
   solid: tap the chart to add one), «Покупатели против продавцов» on the left, volume / liquidations / facts / news / calendar on
   the right; on a phone the three stack. The chart is drawn here on a canvas (no external library: «Управление» allows scripts from
   its own site only). */
(function () {
  'use strict';
  if (window.CNManual) return;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const isN = (v) => v != null && v !== '' && isFinite(+v);
  const grp = (n, d) => { const [i, f] = (+n).toFixed(d).split('.'); return i.replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + (f ? ',' + f : ''); };   // groups the integer part only
  const usd = (v, sign = true) => (!isN(v) ? '—' : (+v < 0 ? '−' : sign && +v > 0 ? '+' : '') + '$' + grp(Math.abs(+v), 2));
  const px = (v) => (!isN(v) ? '—' : +v >= 1000 ? grp(v, 1) : +v >= 1 ? grp(v, 4) : (+v).toPrecision(4).replace('.', ','));
  const pct = (v) => (!isN(v) ? '—' : grp(v, 2) + '%');
  const dt = (t) => (t ? new Date(+t).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) + ' UTC' : '—');
  const cls = (v) => (+v > 0 ? 'pos' : +v < 0 ? 'neg' : '');
  const CSS = `.md .md-top{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:8px;align-items:end}.md label{display:grid;gap:3px;font-size:11.5px;color:var(--muted,#8a94a6)}
.md input,.md select{min-height:40px;padding:6px 10px;border-radius:8px;border:1px solid var(--line2,rgba(127,127,127,.4));background:var(--panel2,rgba(127,127,127,.08));color:var(--fg,#e8f0fa);font:inherit}
.md .md-b{min-height:48px;min-width:110px;border-radius:12px;border:0;font:700 16px inherit;letter-spacing:.06em;cursor:pointer;color:#06101e}.md .md-l{background:var(--up,#6fcf97)}.md .md-s{background:var(--dn,#ef7d6b)}
.md .md-m{color:var(--muted,#8a94a6);font-size:12px;line-height:1.5}.md .pos{color:var(--up,#6fcf97)}.md .neg{color:var(--dn,#ef7d6b)}
.md .md-box{border:1px solid var(--line,rgba(127,127,127,.25));border-radius:12px;padding:10px 12px;margin:10px 0;font-size:13px;line-height:1.55}
.md .md-pv{border-color:var(--amber,#f2c14e)}.md .md-k{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;margin:10px 0}.md .md-k>div{border:1px solid var(--line,rgba(127,127,127,.25));border-radius:10px;padding:8px 10px;display:grid;gap:2px}
.md .md-k small{color:var(--muted,#8a94a6);font-size:11.5px}.md .md-k b{font-size:16px}.md table{width:100%;border-collapse:collapse;font-size:12.5px}.md th,.md td{padding:5px 6px;border-bottom:1px solid var(--line,rgba(127,127,127,.18));text-align:left;vertical-align:top}
.md td.r,.md th.r{text-align:right}.md .md-w{overflow-x:auto}.md h4{margin:14px 0 4px;font-size:13px}.md .md-set{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:8px}
.md .md-strip{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}.md .md-strip span{border:1px solid var(--line,rgba(127,127,127,.25));border-radius:999px;padding:3px 10px;font-size:12px}
.md .md-scr{display:grid;grid-template-columns:260px minmax(0,1fr) 280px;gap:10px;margin:10px 0}.md .md-pan{border:1px solid var(--line,rgba(127,127,127,.25));border-radius:12px;padding:10px 12px;font-size:12.5px;line-height:1.5;min-width:0}
.md .md-pan h5{margin:0 0 6px;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted,#8a94a6)}.md .md-row{display:flex;justify-content:space-between;gap:8px;padding:3px 0;border-bottom:1px solid var(--line,rgba(127,127,127,.12))}
.md .md-row:last-child{border-bottom:0}.md .md-bar{height:8px;border-radius:4px;background:var(--dn,#ef7d6b);overflow:hidden;margin:4px 0 8px}.md .md-bar i{display:block;height:100%;background:var(--up,#6fcf97)}
.md .md-cv{position:relative}.md canvas.md-ch{display:block;width:100%;height:380px;cursor:crosshair}.md .md-tf{display:flex;gap:4px;align-items:center;flex-wrap:wrap;margin-bottom:6px}.md .md-tf button[aria-pressed="true"]{border-color:var(--acc,#7cc4ff)}
.md .md-own{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}.md .md-own span{border:1px solid var(--amber,#f2c14e);border-radius:999px;padding:1px 8px;font-size:11.5px}.md .md-own button{background:none;border:0;color:inherit;cursor:pointer;padding:0 0 0 4px}
.md .md-news a{color:var(--acc,#7cc4ff)}@media (max-width:1100px){.md .md-scr{grid-template-columns:1fr}.md .md-scr .md-pc{order:-1}}
@media (max-width:640px){.md .md-top{grid-template-columns:1fr 1fr}.md .md-top label{grid-column:1/-1}.md .md-k,.md .md-set{grid-template-columns:1fr 1fr}}`;

  // ------------------------------------------------------------------ stage 2: the trading screen
  const SCR = { sym: '', bar: '1h', c: null, f: null, s: null, w: null, pos: [], extra: [], t: 0, err: '' };
  // ages are counted on the server's clock (the walls' «asof»), so a phone with a wrong clock does not age them
  const age = (t) => { const m = Math.max(0, Math.round((((SCR.w && SCR.w.asof) || Date.now()) - t) / 60000)); return m < 60 ? m + ' мин' : Math.floor(m / 60) + ' ч ' + (m % 60) + ' мин'; };
  function wallsHtml(W) {
    if (!W) return '';
    const row = (w) => `<div class="md-row"><span class="${w.side === 'bids' ? 'pos' : 'neg'}">${w.side === 'bids' ? 'покупка' : 'продажа'} ${px(w.price)}<br><span class="md-m">${w.dist_pct > 0 ? '+' : ''}${grp(w.dist_pct, 2)}% · стоит ${age(w.since)} · ${esc(w.trend)}${isN(w.chg_pct) && w.trend !== 'стоит' ? ' ' + (w.chg_pct > 0 ? '+' : '') + grp(w.chg_pct, 0) + '%' : ''}</span></span><b>${usd0(w.usd)}</b></div>`;
    const all = (W.asks || []).slice().reverse().concat(W.bids || []);
    const gone = (W.gone || []).slice(0, 4).map((g) => `<div class="md-row"><span class="md-m">${g.side === 'bids' ? 'покупка' : 'продажа'} ${px(g.price)} · ${usd0(g.usd)} · стояла ${age(((SCR.w && SCR.w.asof) || Date.now()) - (g.lived_ms || 0))}</span><span class="${g.note_ru.indexOf('подходе') >= 0 ? 'neg' : 'md-m'}">${esc(g.note_ru)}</span></div>`).join('');
    const tr = (W.trades || []).slice(0, 8).map((x) => `<div class="md-row"><span class="${x.side === 'покупка' ? 'pos' : 'neg'}">${esc(x.side)} ${px(x.px)}</span><span>${usd0(x.usd)} · ${new Date(x.t).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })}</span></div>`).join('');
    return `<h5 style="margin-top:12px">Крупные заявки</h5>${all.length ? all.map(row).join('') : '<span class="md-m">заметных стенок нет</span>'}
      ${gone ? `<div class="md-m" style="margin-top:6px">Исчезли (30 мин):</div>${gone}` : ''}
      <h5 style="margin-top:12px">Крупные сделки (от ${usd0(W.big_trade_usd)})</h5>${tr || '<span class="md-m">за 2 ч не было</span>'}
      <p class="md-m">${esc(W.note_ru || '')} Стенка — $ в полосе ${grp(W.settings.wall_band_pct, 2)}% в пределах ±${grp(W.settings.wall_range_pct, 0)}%, топ-${W.settings.wall_top} и не меньше ${grp(W.settings.wall_mult, 1)}× медианы. Обновление раз в 30 с.</p>`;
  }
  let LAST_O = {};
  const usd0 = (v) => (!isN(v) ? '—' : '$' + grp(Math.abs(+v) >= 1e6 ? +v / 1e6 : +v >= 1e3 ? +v / 1e3 : +v, Math.abs(+v) >= 1e6 ? 2 : Math.abs(+v) >= 1e3 ? 1 : 0) + (Math.abs(+v) >= 1e6 ? ' млн' : Math.abs(+v) >= 1e3 ? ' тыс.' : ''));
  const imb = (x) => (!x ? '<span class="md-m">нет данных</span>' : `<div class="md-row"><span>покупки ${usd0(x.bid_usd)}</span><span>продажи ${usd0(x.ask_usd)}</span></div>
      <div class="md-bar" title="перевес ${isN(x.imbalance) ? grp(x.imbalance, 1) + '%' : '—'}"><i style="width:${x.bid_usd + x.ask_usd ? (x.bid_usd / (x.bid_usd + x.ask_usd) * 100).toFixed(1) : 50}%"></i></div>`);
  function flowHtml(F) {
    if (!F) return '<p class="md-m">Загружаю…</p>';
    const D = F.depth || {}, T = F.taker || {};
    const tk = (x, lab) => (x ? `<div class="md-row"><span>${lab}</span><span><span class="pos">${usd0(x.buy_usd)}</span> / <span class="neg">${usd0(x.sell_usd)}</span></span></div>` : '');
    return `<h5>Покупатели против продавцов</h5>
      <div class="md-m">Стакан ±1% от цены</div>${imb(D['1pct'])}<div class="md-m">Стакан ±2%${D['2pct'] && !D['2pct'].complete ? ' (стакан OKX глубже не отдаёт)' : ''}</div>${imb(D['2pct'])}
      <div class="md-m">Рыночные покупки / продажи</div>${tk(T.h1, 'за 1 ч') || ''}${tk(T.h24, 'за ' + (T.h24_hours || 24) + ' ч') || (T.h1 ? '' : '<span class="md-m">нет данных</span>')}
      <div class="md-row"><span>Лонг / шорт (счета)</span><b>${F.long_short && isN(F.long_short.ratio) ? grp(F.long_short.ratio, 2) : '—'}</b></div>
      <div class="md-row"><span>Фандинг</span><b class="${F.funding ? (F.funding.rate_pct > 0 ? 'neg' : 'pos') : ''}">${F.funding ? grp(F.funding.rate_pct, 4) + '%' : '—'}</b></div>
      <div class="md-row"><span>Открытый интерес</span><b>${F.oi ? usd0(F.oi.usd) : '—'}</b></div>
      <div class="md-row"><span>ОИ за ${F.oi && F.oi.hours ? F.oi.hours + ' ч' : '24 ч'}</span><b class="${F.oi ? cls(F.oi.chg_24h_pct) : ''}">${F.oi && isN(F.oi.chg_24h_pct) ? (F.oi.chg_24h_pct > 0 ? '+' : '') + grp(F.oi.chg_24h_pct, 2) + '%' : '—'}</b></div>
      <p class="md-m">OKX, обновляется раз в 30 с.</p>`;
  }
  function sideHtml(S) {
    if (!S) return '<p class="md-m">Загружаю…</p>';
    const V = S.volume || {}, F = S.facts || {}, N = S.news || {};
    const news = (xs) => (xs || []).map((x) => `<div class="md-row md-news"><span>${x.direction === 'bull' ? '▲' : x.direction === 'bear' ? '▼' : '◆'} ${x.link ? `<a href="${esc(x.link)}" target="_blank" rel="noopener">${esc(x.title)}</a>` : esc(x.title)}<br><span class="md-m">${esc(x.source || '')} · ${dt(x.created)} · сила ${esc(x.magnitude)}</span></span></div>`).join('');
    return `<h5>Объём</h5><div class="md-row"><span>за 24 ч</span><b>${usd0(V.usd_24h)}</b></div><div class="md-row"><span>среднее за ${V.days || 7} дн.</span><b>${usd0(V.avg_7d_usd)}</b></div>
      <div class="md-row"><span>к среднему</span><b class="${cls(V.vs_avg_pct)}">${isN(V.vs_avg_pct) ? (V.vs_avg_pct > 0 ? '+' : '') + grp(V.vs_avg_pct, 1) + '%' : '—'}</b></div>
      <h5 style="margin-top:10px">Крупные ликвидации, 24 ч</h5>${S.liquidations ? (S.liquidations.length ? S.liquidations.slice(0, 5).map((x) => `<div class="md-row"><span>${esc(x.who)} · ${px(x.px)}</span><span>${usd0(x.usd)} · ${dt(x.t).slice(0, 11)}</span></div>`).join('') : '<span class="md-m">нет от $10 тыс.</span>') : '<span class="md-m">нет данных</span>'}
      ${F.name ? `<h5 style="margin-top:10px">Монета</h5><div>${esc(F.name)}${F.category ? ' · ' + esc(F.category) : ''}</div>${F.about_ru || F.desc_ru ? `<div class="md-m">${esc(F.about_ru || F.desc_ru)}</div>` : ''}` : ''}
      <h5 style="margin-top:10px">Новости ${esc(S.sym)}</h5>${news(N.coin) || '<span class="md-m">за 48 ч нет</span>'}
      ${(N.market || []).length ? `<h5 style="margin-top:10px">Рынок</h5>${news(N.market)}` : ''}
      <h5 style="margin-top:10px">Календарь, 48 ч</h5>${(S.calendar || []).length ? S.calendar.map((e) => `<div class="md-row"><span>${'!'.repeat(Math.max(1, Math.min(3, +e.importance || 1)))} ${esc(e.name)}</span><span>${dt(e.time).slice(0, 11)}</span></div>`).join('') : '<span class="md-m">событий нет</span>'}`;
  }
  function screenHtml() {
    if (!SCR.sym) return '<p class="md-m">Впишите монету — появится экран: график с уровнями, покупатели против продавцов, объём, новости и календарь.</p>';
    const C = SCR.c, own = (C && C.own) || [];
    return `<div class="md-scr"><div class="md-pan md-pl">${flowHtml(SCR.f)}${wallsHtml(SCR.w)}</div>
      <div class="md-pan md-pc"><div class="md-tf"><b>${esc(SCR.sym)}</b>${['1h', '4h', '1d'].map((b) => `<button class="ib" type="button" data-md="bar" data-bar="${b}" aria-pressed="${SCR.bar === b}">${b}</button>`).join('')}
        <span class="md-m">${C && C.last ? 'цена ' + px(C.last) : ''} · тап по графику — свой уровень</span></div>
        <div class="md-cv"><canvas class="md-ch" data-md="chart" aria-label="График ${esc(SCR.sym)}"></canvas></div>
        <div class="md-m">Пунктир — уровни системы (день, вчера, неделя, вершины и донья H1 / H4, круглые); сплошные жёлтые — ваши; полосы — крупные заявки (зелёные — покупка, красные — продажа); белая — вход, красная — стоп, зелёные — тейки открытой позиции; фиолетовые — сделка стола «Торговля от уровней».</div>
        <div class="md-own">${own.map((l) => `<span>${px(l.px)}<button type="button" data-md="lvdel" data-id="${esc(l.id)}" aria-label="Убрать уровень">×</button></span>`).join('')}</div>
        ${SCR.err ? `<p class="md-m">${esc(SCR.err)}</p>` : ''}</div>
      <div class="md-pan md-pr">${sideHtml(SCR.s)}</div></div>`;
  }
  const KC = { day: '#7cc4ff', prev: '#9aa7bd', week: '#b8a7ff', swing: '#7d93ad', round: '#5f6f86' };
  function drawChart(cv) {
    const C = SCR.c; if (!cv || !C || !(C.candles || []).length) return;
    const dpr = window.devicePixelRatio || 1, W = cv.clientWidth || 600, Hh = cv.clientHeight || 380;
    cv.width = W * dpr; cv.height = Hh * dpr;
    const g = cv.getContext('2d'); g.scale(dpr, dpr);
    const cs = C.candles.slice(-Math.max(40, Math.min(160, Math.floor(W / 5)))), padR = 64, padT = 8, padB = 18;
    let lo = Math.min(...cs.map((c) => c[3])), hi = Math.max(...cs.map((c) => c[2]));
    (C.own || []).forEach((l) => { if (C.last && Math.abs(l.px / C.last - 1) <= 0.15) { lo = Math.min(lo, l.px); hi = Math.max(hi, l.px); } });   // the owner's own levels stay in view
    const PL = [];                                      // the open positions of this coin: entry / stop / take-profits
    (SCR.pos || []).forEach((p) => { PL.push({ px: p.entry, c: '#e8f0fa', t: (p.side === 'long' ? 'LONG' : 'SHORT') + ' вход' }, { px: p.stop, c: '#ef7d6b', t: 'стоп' });
      (p.tps || []).forEach((t, i) => { if (!t.done) PL.push({ px: t.px, c: '#6fcf97', t: 'TP' + (i + 1) }); }); });
    (SCR.extra || []).filter((l) => l.sym === SCR.sym).forEach((l) => PL.push(l));   // 20.11: other desks' positions (CNLevels.lines)
    PL.forEach((l) => { if (C.last && Math.abs(l.px / C.last - 1) <= 0.25) { lo = Math.min(lo, l.px); hi = Math.max(hi, l.px); } });
    const span0 = hi - lo; lo -= span0 * 0.04; hi += span0 * 0.04;
    const lv = (C.levels || []).filter((l) => l.px >= lo && l.px <= hi), own = (C.own || []).filter((l) => l.px >= lo && l.px <= hi);
    const Y = (p) => padT + (hi - p) / (hi - lo) * (Hh - padT - padB), cw = (W - padR) / cs.length;
    cv.__map = { lo, hi, padT, padB, H: Hh };
    g.font = '10px ui-monospace,Menlo,monospace'; g.textBaseline = 'middle';
    let lastY = -99;                                   // a label only where it does not overlap the previous one (the line is always drawn)
    lv.slice().sort((a, b) => b.px - a.px).forEach((l) => { g.strokeStyle = KC[l.kind] || '#7d93ad'; g.setLineDash([4, 4]); g.lineWidth = 1; g.beginPath(); g.moveTo(0, Y(l.px)); g.lineTo(W - padR, Y(l.px)); g.stroke();
      g.setLineDash([]); if (Y(l.px) - lastY >= 12) { g.fillStyle = KC[l.kind] || '#7d93ad'; g.fillText(l.label, 4, Y(l.px) - 6); lastY = Y(l.px); } });
    const WB = SCR.w ? (SCR.w.bids || []).concat(SCR.w.asks || []) : [], wmax = Math.max(1, ...WB.map((w) => w.usd));
    const bh = Math.max(3, (Hh - padT - padB) * ((SCR.w && SCR.w.settings.wall_band_pct) || 0.25) / 100 * (C.last || 1) / (hi - lo));
    WB.filter((w) => w.price >= lo && w.price <= hi).forEach((w) => { const bid = w.side === 'bids', len = (W - padR) * (0.25 + 0.75 * w.usd / wmax);
      g.fillStyle = bid ? 'rgba(111,207,151,.22)' : 'rgba(239,125,107,.22)'; g.fillRect(W - padR - len, Y(w.price) - bh / 2, len, bh);
      g.fillStyle = bid ? '#6fcf97' : '#ef7d6b'; g.textAlign = 'right'; g.fillText(usd0(w.usd), W - padR - 4, Y(w.price)); g.textAlign = 'left'; });
    cs.forEach((c, i) => { const x = i * cw + cw / 2, up = c[4] >= c[1]; g.strokeStyle = g.fillStyle = up ? '#6fcf97' : '#ef7d6b';
      g.beginPath(); g.moveTo(x, Y(c[2])); g.lineTo(x, Y(c[3])); g.stroke();
      const y1 = Y(Math.max(c[1], c[4])), y2 = Y(Math.min(c[1], c[4])); g.fillRect(x - Math.max(1, cw * 0.35), y1, Math.max(2, cw * 0.7), Math.max(1, y2 - y1)); });
    own.forEach((l) => { g.strokeStyle = '#f2c14e'; g.lineWidth = 1.5; g.beginPath(); g.moveTo(0, Y(l.px)); g.lineTo(W - padR, Y(l.px)); g.stroke();
      g.fillStyle = '#f2c14e'; g.fillText('свой ' + px(l.px), 4, Y(l.px) - 6); });
    let plY = -99;                                     // labels of position lines never overlap (the lines are always drawn)
    PL.slice().sort((a, b) => b.px - a.px).forEach((l) => { g.strokeStyle = l.c; g.lineWidth = 1.5; g.setLineDash(l.t === 'стоп' || l.dash ? [6, 3] : []); g.beginPath(); g.moveTo(0, Y(l.px)); g.lineTo(W - padR, Y(l.px)); g.stroke(); g.setLineDash([]);
      if (Y(l.px) - plY >= 12) { g.fillStyle = l.c; g.fillText(l.t + ' ' + px(l.px), (W - padR) * 0.45, Y(l.px) - 6); plY = Y(l.px); } });
    g.fillStyle = '#7d93ad'; for (let k = 0; k <= 4; k++) { const p = hi - (hi - lo) * k / 4; g.fillText(px(p), W - padR + 4, Y(p)); }
    if (C.last) { g.fillStyle = '#e8f0fa'; g.fillRect(W - padR, Y(C.last) - 7, padR, 14); g.fillStyle = '#06101e'; g.fillText(px(C.last), W - padR + 4, Y(C.last)); }
  }
  async function loadScreen(api, sym, bar, rerender) {
    if (!api.candles || !sym || SCR.busy) return;
    SCR.busy = true;
    SCR.sym = sym; SCR.bar = bar || SCR.bar; SCR.err = '';
    const [c, f, s, w] = await Promise.all([api.candles(sym, SCR.bar).catch((e) => { SCR.err = (e.data && e.data.message_ru) || 'График не загрузился.'; return null; }),
      api.flow(sym).catch(() => null), api.side(sym).catch(() => null), api.walls ? api.walls(sym).catch(() => null) : null]);
    if (c) SCR.c = c; if (f) SCR.f = f; if (s) SCR.s = s; SCR.w = w || (SCR.w && SCR.w.sym === sym ? SCR.w : null);
    if (c && c.sym) SCR.sym = c.sym;
    SCR.t = Date.now();
    SCR.busy = false;
    rerender(null, LAST_O.msg || '', LAST_O);
  }

  function previewBox(P) {
    if (!P) return '';
    const L = P.side === 'long';
    return `<div class="md-box md-pv"><b>${esc(P.sym)} ${L ? 'LONG' : 'SHORT'} — проверьте и подтвердите</b><br>
      Вход ≈ ${px(P.entry)} <span class="md-m">(цена ${px(P.last)} + проскальзывание 0,05%)</span><br>
      Стоп <b>${px(P.stop)}</b> <span class="md-m">−${pct(P.stop_dist_pct)} · ${esc(P.stop_why)}</span><br>
      Размер ${usd(P.notional, false)} · плечо ${grp(P.lev, 2)}× · риск <b>${usd(P.risk_usd, false)}</b> из ${usd(P.equity, false)} · комиссии ≈ ${usd(P.fees_est, false)}<br>
      Тейки: ${(P.tps || []).map((t, i) => `TP${i + 1} ${px(t.px)} (${grp(t.r, 1)}R, ${grp(t.pct, 0)}%${t.why ? ' — ' + esc(t.why) : ''})`).join(' · ')}<br>
      <span class="md-m">${P.settings && P.settings.be_after_tp1 ? 'После TP1 стоп в безубыток. ' : ''}${P.settings && P.settings.trail_after_tp2 ? 'После TP2 — трейлинг на расстоянии первого стопа. ' : ''}${esc(P.note_ru || '')}</span>
      <div style="margin-top:8px;display:flex;gap:8px"><button class="md-b ${L ? 'md-l' : 'md-s'}" type="button" data-md="confirm">Подтвердить</button><button class="ib" type="button" data-md="cancel">Отмена</button></div></div>`;
  }

  function html(D, o) {
    o = o || {};
    LAST_O = o;
    SCR.pos = ((D && D.open) || []).filter((p) => p.sym === SCR.sym);
    const S = (D && D.settings) || {}, st = (D && D.stats) || {};
    const top = `<div class="md-top"><label>Монета (OKX, бессрочный USDT)<input data-md="q" value="${esc(o.sym || '')}" placeholder="ADA или Cardano" maxlength="40" autocomplete="off"></label>
      <button class="md-b md-l" type="button" data-md="long">LONG</button><button class="md-b md-s" type="button" data-md="short">SHORT</button></div>`;
    const flash = o.msg ? `<div class="md-box" role="status">${esc(o.msg)}</div>` : '';
    if (!D) return `<style>${CSS}</style><div class="md">${top}${flash}${previewBox(o.preview)}<p class="md-m">${esc(o.err || 'Загружаю…')}</p></div>`;
    const op = D.open || [];
    const strip = op.length ? `<div class="md-strip">${op.map((p) => `<span>${esc(p.sym)} ${p.side === 'long' ? '▲' : '▼'} <b class="${cls(p.upnl)}">${usd(p.upnl)}</b></span>`).join('')}</div>` : '';
    const rows = op.map((p) => `<tr><td><b>${esc(p.sym)}</b> ${p.side === 'long' ? '▲ LONG' : '▼ SHORT'}<br><span class="md-m">${dt(p.t)} · ${grp(p.lev, 2)}×</span></td>
      <td>${px(p.entry)} → ${px(p.mark)}</td><td>стоп ${px(p.stop)}${p.trail ? ' <span class="md-m">трейлинг</span>' : ''}<br><span class="md-m">${(p.tps || []).map((t, i) => (t.done ? '✓' : '') + 'TP' + (i + 1) + ' ' + px(t.px)).join(' · ')}</span></td>
      <td class="r ${cls(p.upnl)}">${usd(p.upnl)}<br><span class="md-m">реализовано ${usd(p.realised - p.fees)}</span></td>
      <td class="r"><button class="ib" type="button" data-md="close" data-id="${esc(p.id)}">Закрыть</button> <button class="ib" type="button" data-md="stop" data-id="${esc(p.id)}" data-px="${esc(p.stop)}">Стоп…</button></td></tr>`).join('');
    const sysTxt = (c) => (c.system && c.system.direction ? esc(c.system.direction) : '<span class="md-m">нет данных</span>');
    const cl = (D.closed || []).slice(0, 30).map((c) => `<tr><td><b>${esc(c.sym)}</b> ${c.side === 'long' ? '▲' : '▼'} <span class="md-m">${dt(c.t)} → ${dt(c.closed_t)}</span></td>
      <td>${esc(c.why)}</td><td class="r ${cls(c.pnl_usd)}">${usd(c.pnl_usd)}</td><td class="r">${isN(c.r_mult) ? grp(c.r_mult, 2) + 'R' : '—'}</td><td>${sysTxt(c)}</td></tr>`).join('');
    const set = `<details class="md-box"><summary><b>Настройки</b> <span class="md-m">риск ${grp(S.risk_pct || 0, 1)}% · стоп ${esc(S.stop_rule)} · тейки ${(S.tp_r || []).join('/')}R · плечо до ${grp(S.max_lev || 0, 0)}×</span></summary>
      <form class="md-set" data-md="set"><label>Риск на сделку, %<input name="risk_pct" type="number" step="0.1" min="0.1" max="10" value="${esc(S.risk_pct)}"></label>
      <label>Правило стопа<select name="stop_rule">${[['swing_h1', 'за уровнем H1'], ['swing_h4', 'за уровнем H4'], ['pct', 'фиксированный %']].map(([k, v]) => `<option value="${k}"${S.stop_rule === k ? ' selected' : ''}>${v}</option>`).join('')}</select></label>
      <label>Стоп %, если без уровня<input name="stop_pct" type="number" step="0.1" min="0.3" max="16" value="${esc(S.stop_pct)}"></label>
      <label>Макс. стоп, %<input name="stop_max_pct" type="number" step="0.5" min="1" max="16" value="${esc(S.stop_max_pct)}"></label>
      <label>Тейки<select name="tp_rule"><option value="r"${S.tp_rule !== 'levels' ? ' selected' : ''}>по R-множителям</option><option value="levels"${S.tp_rule === 'levels' ? ' selected' : ''}>по уровням (R, где уровня нет)</option></select></label>
      <label>Тейки, R через запятую<input name="tp_r" value="${esc((S.tp_r || []).join(', '))}"></label>
      <label>Доли тейков, %<input name="tp_split" value="${esc((S.tp_split || []).join(', '))}"></label>
      <label>Стенки: диапазон ±%<input name="wall_range_pct" type="number" step="0.5" min="1" max="10" value="${esc(S.wall_range_pct)}"></label>
      <label>Стенки: полоса, %<input name="wall_band_pct" type="number" step="0.05" min="0.05" max="1" value="${esc(S.wall_band_pct)}"></label>
      <label>Стенки: топ / ≥ × медианы<input name="wall_top_mult" value="${esc(S.wall_top)} / ${esc(S.wall_mult)}"></label>
      <label>Крупная сделка от, $ (0 = авто)<input name="big_trade_usd" type="number" step="1000" min="0" value="${esc(S.big_trade_usd)}"></label>
      <label>Макс. плечо<input name="max_lev" type="number" step="0.5" min="1" max="5" value="${esc(S.max_lev)}"></label>
      <label>После TP1 / TP2<select name="mgmt"><option value="both"${S.be_after_tp1 && S.trail_after_tp2 ? ' selected' : ''}>безубыток + трейлинг</option><option value="be"${S.be_after_tp1 && !S.trail_after_tp2 ? ' selected' : ''}>только безубыток</option><option value="none"${!S.be_after_tp1 && !S.trail_after_tp2 ? ' selected' : ''}>ничего</option></select></label>
      <div><button class="ib" type="submit">Сохранить</button></div></form></details>`;
    return `<style>${CSS}</style><div class="md"><p class="md-m"><b>${esc(D.label_ru || '')}</b></p>${top}${strip}${flash}${previewBox(o.preview)}${screenHtml()}
      <div class="md-k"><div><small>Демо-счёт</small><b>${usd(D.equity, false)}</b><small>старт ${usd(D.start, false)}</small></div>
        <div><small>Сделок</small><b>${st.trades || 0}</b><small>в плюсе ${st.win_pct == null ? '—' : grp(st.win_pct, 0) + '%'}</small></div>
        <div><small>Средний R</small><b>${isN(st.avg_r) ? grp(st.avg_r, 2) : '—'}</b><small>итог в рисках</small></div>
        <div><small>Итог</small><b class="${cls(st.pnl_usd)}">${usd(st.pnl_usd || 0)}</b><small>после комиссий</small></div>
        <div><small>Макс. просадка</small><b>${pct(st.max_dd_pct || 0)}</b><small>по закрытым</small></div></div>
      <h4>Открытые позиции</h4>${rows ? `<div class="md-w"><table><tr><th>Позиция</th><th>Вход → сейчас</th><th>Стоп и тейки</th><th class="r">Результат</th><th></th></tr>${rows}</table></div>` : '<p class="md-m">Открытых позиций нет.</p>'}
      <h4>Закрытые</h4>${cl ? `<div class="md-w"><table><tr><th>Сделка</th><th>Выход</th><th class="r">Итог</th><th class="r">R</th><th>Что говорила система</th></tr>${cl}</table></div>` : '<p class="md-m">Закрытых пока нет.</p>'}
      ${set}<p class="md-m">${esc(D.stage_ru || '')}</p></div>`;
  }

  function mount(el, api, rerender) {
    const q = el.querySelector('[data-md="q"]');
    // stage 2: the screen follows the chosen coin; a new coin (Enter / leaving the box / LONG / SHORT) loads chart and panels
    const want = ((LAST_O.preview && LAST_O.preview.sym) || LAST_O.sym || '').toUpperCase();
    const cv = el.querySelector('[data-md="chart"]');
    if (cv) { drawChart(cv); cv.addEventListener('click', async (e) => { const m = cv.__map; if (!m || !api.level) return;
      const r = cv.getBoundingClientRect(), p = m.hi - ((e.clientY - r.top) - m.padT) / (m.H - m.padT - m.padB) * (m.hi - m.lo);
      if (!(p > 0) || !window.confirm('Добавить свой уровень ' + px(p) + '?')) return;
      try { const R = await api.level({ sym: SCR.sym, px: +p.toPrecision(6) }); if (SCR.c) SCR.c.own = R.own; rerender(null, LAST_O.msg || '', LAST_O); } catch (er) { rerender(null, 'Уровень не сохранился.', LAST_O); } }); }
    el.querySelectorAll('[data-md="lvdel"]').forEach((b) => b.addEventListener('click', async () => {
      try { const R = await api.level({ sym: SCR.sym, remove: b.dataset.id }); if (SCR.c) SCR.c.own = R.own; rerender(null, LAST_O.msg || '', LAST_O); } catch (er) { /* keep */ } }));
    el.querySelectorAll('[data-md="bar"]').forEach((b) => b.addEventListener('click', () => loadScreen(api, SCR.sym, b.dataset.bar, rerender)));
    if (q) { const go = () => { const v = (q.value || '').trim(); if (v && v.toUpperCase() !== SCR.sym) { LAST_O = { ...LAST_O, sym: v }; loadScreen(api, v, SCR.bar, rerender); } };
      q.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); }); q.addEventListener('change', go); }
    TICK.api = api; TICK.rr = rerender;               // the walls refresh every 30 s while the screen is on the page
    if (!TICK.id && api.walls) TICK.id = setInterval(async () => {
      const cvs = document.querySelector('[data-md="chart"]'); if (!SCR.sym || !cvs || document.hidden || SCR.busy) return;
      try { SCR.w = await TICK.api.walls(SCR.sym); TICK.rr(null, LAST_O.msg || '', LAST_O); } catch (e) { /* keep the last */ } }, 30000);
    if (want && api.candles && !SCR.busy && ((want !== SCR.sym && want !== SCR.tried) || Date.now() - SCR.t > 60000)) { SCR.tried = want; loadScreen(api, want, SCR.bar, rerender); }
    const err = (e) => (e && e.data && e.data.message_ru) || 'Не получилось: ' + ((e && e.message) || e);
    el.querySelectorAll('[data-md="long"],[data-md="short"]').forEach((b) => b.addEventListener('click', async () => {
      const sym = (q.value || '').trim();
      if (!sym) { rerender(null, 'Впишите монету.', { sym }); return; }
      b.disabled = true;
      try { const P = await api.preview(sym, b.dataset.md); rerender(null, '', { preview: P, sym: P.sym }); }
      catch (e) { rerender(null, err(e), { sym }); }
      b.disabled = false; }));
    const c = el.querySelector('[data-md="confirm"]');
    if (c) c.addEventListener('click', async () => {
      const P = el.__preview; if (!P) return; c.disabled = true;
      try { const r = await api.open(P.sym, P.side); rerender(r, r.message_ru || '', { sym: P.sym }); }
      catch (e) { rerender(null, err(e), { sym: P.sym }); } });
    const x = el.querySelector('[data-md="cancel"]');
    if (x) x.addEventListener('click', () => rerender(null, '', { sym: el.__preview && el.__preview.sym }));
    el.querySelectorAll('[data-md="close"]').forEach((b) => b.addEventListener('click', async () => {
      if (!window.confirm('Закрыть позицию по рынку (на бумаге)?')) return;
      b.disabled = true; try { const r = await api.close(b.dataset.id); rerender(r, r.message_ru || ''); } catch (e) { rerender(null, err(e)); } }));
    el.querySelectorAll('[data-md="stop"]').forEach((b) => b.addEventListener('click', async () => {
      const v = window.prompt('Новый стоп (цена):', b.dataset.px); if (!v) return;
      try { const r = await api.stop(b.dataset.id, parseFloat(String(v).replace(',', '.'))); rerender(r, r.message_ru || ''); } catch (e) { rerender(null, err(e)); } }));
    const f = el.querySelector('[data-md="set"]');
    if (f) f.addEventListener('submit', async (e) => {
      e.preventDefault(); const d = new FormData(f), num = (s) => String(s || '').split(/[,;\s]+/).filter(Boolean).map((v) => parseFloat(v.replace(',', '.')));
      const m = d.get('mgmt');
      try { const r = await api.settings({ risk_pct: +d.get('risk_pct'), stop_rule: d.get('stop_rule'), stop_pct: +d.get('stop_pct'), stop_max_pct: +d.get('stop_max_pct'),
        tp_rule: d.get('tp_rule'), wall_range_pct: +d.get('wall_range_pct'), wall_band_pct: +d.get('wall_band_pct'),
        wall_top: num(d.get('wall_top_mult'))[0], wall_mult: num(String(d.get('wall_top_mult') || '').replace('/', ' '))[1], big_trade_usd: +d.get('big_trade_usd'),
        tp_r: num(d.get('tp_r')), tp_split: num(d.get('tp_split')), max_lev: +d.get('max_lev'), be_after_tp1: m !== 'none', trail_after_tp2: m === 'both' });
        rerender(r, r.message_ru || ''); } catch (er) { rerender(null, err(er)); } });
  }
  const TICK = { id: 0, api: null, rr: null };
  /** 20.11: lines of another desk to draw on this chart when its coin is selected ([{sym, px, c, t, dash}]) */
  const setExtra = (ls) => { SCR.extra = Array.isArray(ls) ? ls : []; };
  window.CNManual = { html, mount, setExtra, _scr: SCR, flowHtml, sideHtml, wallsHtml };
})();
