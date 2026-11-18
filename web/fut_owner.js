/* CRYPTO NOVΛ — the owner's Futures picture (06.11.2026, owner 17:30: «понятная картина по фьючерсам … от кого что мы получаем»).
 * One renderer for both panels (app.html and vip.html), owner views only. Data: web/fut_owner.json (cnq/agents/fut_owner.py).
 *   CNFutOwner.html(doc, {ivan, ivanStats})  → HTML string      CNFutOwner.mount(el)  → the chart's hover layer
 * Plain JS, no libraries. Internal wording on purpose («Копирование китов», trader names): never call it from a public view. */
(function () {
  'use strict';
  if (window.CNFutOwner) return;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const isN = (v) => v != null && v !== '' && isFinite(+v);
  const grp = (n, d) => (+n).toFixed(d).replace('.', ',').replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  const usd = (v, sign) => {
    if (!isN(v)) return '—';
    v = +v; const a = Math.abs(v), d = a < 10 ? 2 : 0;
    return (v < 0 ? '−' : sign && v > 0 ? '+' : '') + '\u2060$' + grp(a, d);
  };
  const pct = (v, d = 1, sign = true) => (!isN(v) ? '—' : (+v < 0 ? '−' : sign && +v > 0 ? '+' : '') + grp(Math.abs(+v), d) + '%');
  const px = (v) => { if (!isN(v)) return '—'; v = +v; const a = Math.abs(v); return grp(v, a >= 1000 ? 0 : a >= 10 ? 2 : a >= 1 ? 3 : a >= 0.01 ? 4 : 6); };
  // calm colours: a small amount stays in plain ink; only a real move gets the soft green / coral
  const tone = (v, small = 5) => (!isN(v) || Math.abs(+v) < small ? '' : +v > 0 ? 'p' : 'n');
  const KY = { timeZone: 'Europe/Kyiv' };
  const dd = (t) => (t ? new Date(+t).toLocaleDateString('ru-RU', { ...KY, day: '2-digit', month: '2-digit' }) : '—');
  const ddt = (t) => (t ? new Date(+t).toLocaleString('ru-RU', { ...KY, day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }).replace(',', '') : '—');
  const hold = (h) => (!isN(h) ? '—' : +h < 48 ? grp(h, 0) + ' ч' : grp(h / 24, 0) + ' дн');
  const sideRu = (s) => (String(s).toUpperCase() === 'SHORT' ? 'шорт' : 'лонг');
  const SRC = { bn: 'Binance', okx: 'OKX', hl: 'Hyperliquid' };

  const CSS = `
.fo{--fo-ink:#e8f0fa;--fo-ink2:#c3cee0;--fo-mute:#8a9ab4;--fo-faint:#62718b;--fo-line:rgba(160,190,230,.14);--fo-tile:rgba(16,28,50,.55);
  --fo-acc:#7cc4ff;--fo-p:#7fd1a8;--fo-n:#e8a39a;font-variant-numeric:tabular-nums;color:var(--fo-ink)}
.fo *{box-sizing:border-box}
.fo h3{margin:22px 0 10px;font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--fo-mute);font-weight:600;display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.fo h3 small{text-transform:none;letter-spacing:0;font-weight:500;color:var(--fo-faint)}
.fo .fo-head{padding-right:76px;display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap}
.fo .fo-head b{font-size:16px}.fo .fo-head span{font-size:12px;color:var(--fo-mute)}
.fo .p{color:var(--fo-p)}.fo .n{color:var(--fo-n)}.fo .m{color:var(--fo-mute)}
.fo .fo-chart{position:relative;margin-top:10px}.fo .fo-chart svg{display:block;width:100%;height:auto;overflow:visible}
.fo .fo-tip{position:absolute;pointer-events:none;background:#0b1528;border:1px solid var(--fo-line);border-radius:8px;padding:6px 9px;font-size:12px;white-space:nowrap;transform:translate(-50%,-110%);display:none}
.fo .fo-legend{display:flex;gap:6px 16px;align-items:center;flex-wrap:wrap;font-size:11.5px;color:var(--fo-mute);margin-top:6px}
.fo .fo-legend i{display:inline-block;width:14px;height:0;border-top:2px solid var(--fo-acc);vertical-align:middle;margin-right:6px}
.fo .fo-legend .fo-num{display:inline-grid;place-items:center;width:16px;height:16px;border-radius:50%;border:1px solid var(--fo-mute);font-size:10px;font-weight:600;color:var(--fo-ink);margin-right:6px}
.fo .fo-tiles{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-top:14px}
.fo .fo-tile{background:var(--fo-tile);border:1px solid var(--fo-line);border-radius:12px;padding:12px 14px;min-height:112px;display:flex;flex-direction:column;gap:4px}
.fo .fo-tile small{font-size:11.5px;color:var(--fo-mute)}.fo .fo-tile b{font-size:21px;font-weight:700;line-height:1.15}
.fo .fo-tile em{font-style:normal;font-size:12px;color:var(--fo-ink2);line-height:1.4}
.fo .fo-bar{height:6px;border-radius:3px;background:rgba(160,190,230,.14);overflow:hidden;margin:6px 0 2px}
.fo .fo-bar i{display:block;height:100%;background:var(--fo-acc);border-radius:3px}
.fo .fo-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.fo .fo-card{background:var(--fo-tile);border:1px solid var(--fo-line);border-radius:12px;padding:12px 14px;display:flex;flex-direction:column;gap:8px;min-width:0}
.fo .fo-card .t{display:flex;justify-content:space-between;align-items:baseline;gap:8px}.fo .fo-card .t b{font-size:15px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fo .fo-chip{font-size:11px;border:1px solid var(--fo-line);border-radius:999px;padding:2px 8px;color:var(--fo-ink2);white-space:nowrap}
.fo .fo-kv{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px 10px;font-size:12px}
.fo .fo-kv span{color:var(--fo-mute);display:block;font-size:11px}.fo .fo-kv b{font-weight:600}
.fo table{width:100%;border-collapse:collapse;font-size:12px}.fo th{font-weight:500;color:var(--fo-mute);text-align:left;padding:4px 6px 4px 0;border-bottom:1px solid var(--fo-line)}
.fo td{padding:5px 6px 5px 0;border-bottom:1px solid rgba(160,190,230,.07);white-space:nowrap}.fo td.r,.fo th.r{text-align:right;padding-right:0}
.fo .fo-tbl{overflow-x:auto}
.fo .fo-tot{display:flex;justify-content:space-between;gap:10px;font-size:12.5px;border-top:1px solid var(--fo-line);padding-top:8px;margin-top:auto}
.fo .fo-note{font-size:12px;color:var(--fo-mute);line-height:1.5;margin:6px 0 0}
.fo details{margin-top:10px;border:1px solid var(--fo-line);border-radius:12px;padding:10px 14px}.fo summary{cursor:pointer;font-size:13px;color:var(--fo-ink2)}
.fo details[open] summary{margin-bottom:10px}
.fo .fo-vs{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
@media (max-width:720px){.fo .fo-tiles,.fo .fo-vs{grid-template-columns:repeat(2,minmax(0,1fr))}.fo .fo-grid{grid-template-columns:1fr}
  .fo .fo-tile b{font-size:18px}.fo .fo-vs .fo-tile:last-child{grid-column:1/-1}}
@media (max-width:720px){.fo td{white-space:normal;word-break:break-word}.fo td.r{white-space:nowrap}.fo table{font-size:11.5px}.fo td,.fo th{padding-right:4px}}
@media (max-width:380px){.fo .fo-kv{grid-template-columns:repeat(2,minmax(0,1fr))}}`;
  function css() { if (document.getElementById('fo-css')) return; const s = document.createElement('style'); s.id = 'fo-css'; s.textContent = CSS; document.head.appendChild(s); }

  /* ── the chart: futures P&L since 22.10, one line, a zero line, the three marks; hover = crosshair + tooltip ── */
  function svgChart(doc, W) {
    const pts = (doc.curve || []).filter((p) => isN(p[0]) && isN(p[1]));
    W = Math.max(300, Math.round(W || 720));
    const H = W < 520 ? 190 : 230, L = 50, R = 14, T = 26, B = 26, fs = W < 520 ? 12 : 11;
    const t0 = pts[0][0], t1 = pts[pts.length - 1][0];
    let lo = Math.min(0, ...pts.map((p) => p[1])), hi = Math.max(0, ...pts.map((p) => p[1]));
    const pad = (hi - lo) * 0.12 || 10; lo -= pad; hi += pad;
    const x = (t) => L + (t - t0) / (t1 - t0 || 1) * (W - L - R), y = (v) => T + (hi - v) / (hi - lo) * (H - T - B);
    const line = pts.map((p, i) => (i ? 'L' : 'M') + x(p[0]).toFixed(1) + ' ' + y(p[1]).toFixed(1)).join('');
    const area = line + `L${x(t1).toFixed(1)} ${y(0).toFixed(1)}L${x(t0).toFixed(1)} ${y(0).toFixed(1)}Z`;
    const step = Math.pow(10, Math.floor(Math.log10((hi - lo) / 4 || 1))) * ([1, 2, 5].find((m) => (hi - lo) / 4 <= m * Math.pow(10, Math.floor(Math.log10((hi - lo) / 4 || 1)))) || 10);
    let grid = '';
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
      grid += `<line x1="${L}" x2="${W - R}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" stroke="rgba(160,190,230,${Math.abs(v) < 1e-9 ? '.35' : '.08'})"/>`
        + `<text x="${L - 8}" y="${(y(v) + 4).toFixed(1)}" text-anchor="end" font-size="${fs}" fill="#8a9ab4">${esc(usd(v))}</text>`;
    }
    let xt = '';
    for (let d = new Date(t0); d.getTime() <= t1; d.setDate(d.getDate() + 1)) {
      const t = d.getTime(), day = d.toLocaleDateString('ru-RU', { ...KY, day: 'numeric' });
      if ((+day - 1) % 4 === 0 || t === t0) xt += `<text x="${x(t).toFixed(1)}" y="${H - 6}" text-anchor="middle" font-size="${fs}" fill="#8a9ab4">${esc(dd(t))}</text>`;
    }
    // the marks: a dashed line and a numbered badge on top; the legend under the chart names them (no labels over the line)
    let prevX = -99;
    const mk = (doc.markers || []).filter((m) => m.t > t0 && m.t < t1).map((m, i) => {
      const X = x(m.t), bx = Math.max(X, prevX + 18); prevX = bx;
      return `<line x1="${X.toFixed(1)}" x2="${X.toFixed(1)}" y1="${T}" y2="${H - B}" stroke="#8a9ab4" stroke-dasharray="3 4" stroke-width="1"/>`
        + `<circle cx="${bx.toFixed(1)}" cy="${T - 12}" r="8" fill="#16243f" stroke="#8a9ab4"/><text x="${bx.toFixed(1)}" y="${T - 8}" text-anchor="middle" font-size="10.5" fill="#e8f0fa">${i + 1}</text>`;
    }).join('');
    const last = pts[pts.length - 1];
    const data = esc(JSON.stringify({ pts, t0, t1, lo, hi, W, H, L, R, T, B }));
    return { data, svg: `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Итог фьючерсов реального счёта с 22.10: ${esc(usd(last[1], true))}">
        ${grid}${xt}
        <path d="${area}" fill="rgba(124,196,255,.08)"/>
        <path d="${line}" fill="none" stroke="#7cc4ff" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
        ${mk}
        <circle cx="${x(last[0]).toFixed(1)}" cy="${y(last[1]).toFixed(1)}" r="4" fill="#7cc4ff" stroke="#0b1528" stroke-width="2"/>
        <g class="fo-x" style="display:none"><line y1="${T}" y2="${H - B}" stroke="rgba(232,240,250,.35)"/><circle r="4" fill="#7cc4ff" stroke="#0b1528" stroke-width="2"/></g>
        <rect x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent" class="fo-hit"/>
      </svg>` };
  }
  function chart(doc) {
    if ((doc.curve || []).length < 2) return '<p class="fo-note">Кривая фьючерсов ещё не набралась.</p>';
    const c = svgChart(doc, 720), M = (doc.markers || []).filter((m) => m.t > doc.curve[0][0] && m.t < doc.curve[doc.curve.length - 1][0]);
    return `<div class="fo-chart" data-fo-chart="${c.data}" data-fo-doc="${esc(JSON.stringify({ curve: doc.curve, markers: doc.markers }))}">${c.svg}<div class="fo-tip"></div></div>
      <div class="fo-legend"><span><i></i>итог фьючерсов реального счёта OKX, $ (с открытыми позициями)</span>${M.map((m, i) => `<span><b class="fo-num">${i + 1}</b>${esc(m.ru)}</span>`).join('')}</div>`;
  }
  function mount(el) {
    (el || document).querySelectorAll('[data-fo-chart]').forEach((box) => {
      if (box.__fo) return; box.__fo = 1;
      // redraw at the box's own width: 1 unit = 1 px, so the axis text keeps its size on a phone
      try {
        const w = box.clientWidth;
        if (w && Math.abs(w - 720) > 4) {
          const c = svgChart(JSON.parse(box.dataset.foDoc), w);
          box.querySelector('svg').outerHTML = c.svg; box.dataset.foChart = c.data.replace(/&quot;/g, '"').replace(/&amp;/g, '&');
        }
      } catch (e) { /* keep the first drawing */ }
      const D = JSON.parse(box.dataset.foChart), svg = box.querySelector('svg'), g = svg.querySelector('.fo-x'), tip = box.querySelector('.fo-tip');
      const x = (t) => D.L + (t - D.t0) / (D.t1 - D.t0 || 1) * (D.W - D.L - D.R), y = (v) => D.T + (D.hi - v) / (D.hi - D.lo) * (D.H - D.T - D.B);
      const move = (ev) => {
        const r = svg.getBoundingClientRect(), cx = ((ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left) / r.width * D.W;
        let best = D.pts[0];
        for (const p of D.pts) if (Math.abs(x(p[0]) - cx) < Math.abs(x(best[0]) - cx)) best = p;
        const X = x(best[0]), Y = y(best[1]);
        g.style.display = ''; g.querySelector('line').setAttribute('x1', X); g.querySelector('line').setAttribute('x2', X);
        g.querySelector('circle').setAttribute('cx', X); g.querySelector('circle').setAttribute('cy', Y);
        tip.style.display = 'block'; tip.style.left = (X / D.W * r.width) + 'px'; tip.style.top = (Y / D.H * r.height) + 'px';
        tip.innerHTML = `<b>${esc(usd(best[1], true))}</b> · ${esc(ddt(best[0]))}`;
      };
      const out = () => { g.style.display = 'none'; tip.style.display = 'none'; };
      const hit = svg.querySelector('.fo-hit');
      hit.addEventListener('mousemove', move); hit.addEventListener('touchmove', move, { passive: true });
      hit.addEventListener('mouseleave', out); hit.addEventListener('touchend', out);
    });
  }

  function tiles(K) {
    const c = K.closed || {}, used = isN(K.used_pct) ? Math.max(0, Math.min(100, +K.used_pct)) : 0;
    const t = (lab, val, sub, cls) => `<div class="fo-tile"><small>${lab}</small><b class="${cls || ''}">${val}</b><em>${sub}</em></div>`;
    return `<div class="fo-tiles">
      ${t('Вложено в фьючерсы', esc(usd(K.budget_usd)), `${esc(pct(K.budget_pct, 0, false))} счёта; было ${esc(pct(K.budget_start_pct, 0, false))} (${esc(usd(K.budget_start_usd))}) до ${esc(dd(K.budget_changed_t))}`)}
      ${t('Итог с 22.10', esc(usd(K.pnl_all_usd, true)), `с 04.11: <span class="${tone(K.pnl_switch_usd)}">${esc(usd(K.pnl_switch_usd, true))}</span>`, tone(K.pnl_all_usd))}
      <div class="fo-tile"><small>В работе / свободно</small><b>${esc(usd(K.margin_usd))}</b><div class="fo-bar"><i style="width:${used.toFixed(1)}%"></i></div><em>маржа ${esc(pct(K.used_pct, 1, false))} · свободно ${esc(usd(K.reserve_usd))}</em></div>
      ${t('Открыто позиций', esc(K.open_n != null ? K.open_n : '—'), `сейчас по ним <span class="${tone(K.open_usd)}">${esc(usd(K.open_usd, true))}</span>`)}
      ${t('Закрытые сделки', esc(c.n != null ? c.n : '—'), `в плюсе ${esc(pct(c.win_pct, 0, false))} · PF ${esc(isN(c.pf) ? grp(c.pf, 2) : '—')} · в среднем ${esc(usd(c.avg_usd, true))}<br>лучшая ${c.best ? esc(c.best.coin + ' ' + usd(c.best.pnl_usd, true)) : '—'} · худшая ${c.worst ? esc(c.worst.coin + ' ' + usd(c.worst.pnl_usd, true)) : '—'}`)}
      ${t('Сегодня', esc(usd(K.today_usd, true)), 'с полуночи по Киеву, с открытыми', tone(K.today_usd))}
    </div>`;
  }

  function copiesTable(cs) {
    if (!cs.length) return '<p class="fo-note">Копий на реальном счёте пока нет.</p>';
    return `<div class="fo-tbl"><table><thead><tr><th>Монета</th><th>Наш вход / его</th><th>Выход</th><th class="r">Итог</th></tr></thead><tbody>${cs.map((c) =>
      `<tr><td>${esc(c.coin)} ${sideRu(c.side)}<br><span class="m">${esc(dd(c.opened))}${c.match === 'time' ? ' · по времени' : ''}</span></td>
        <td>${esc(px(c.entry))}<br><span class="m">${esc(px(c.whale_entry))}${isN(c.entry_gap_pct) ? ' · ' + esc(pct(c.entry_gap_pct, 2)) : ''}</span></td>
        <td>${c.closed ? esc(px(c.exit)) + '<br><span class="m">' + esc(dd(c.closed_t)) + '</span>' : '<span class="m">открыта</span>'}</td>
        <td class="r ${tone(c.pnl_usd, 2)}">${esc(usd(c.pnl_usd, true))}</td></tr>`).join('')}</tbody></table></div>`;
  }
  function traderCard(f, lockNext) {
    const T = f.totals || {};
    const chip = f.status === 'following' ? `в списке${f.joined ? ' с ' + esc(dd(f.joined)) : ''} · пересмотр ${esc(dd(lockNext))}` : 'выбыл';
    return `<div class="fo-card"><div class="t"><b title="${esc(f.name)}">${esc(f.name)}</b><span class="fo-chip">${chip}</span></div>
      <div class="fo-kv"><div><span>в плюсе, 60 дн</span><b>${esc(pct(f.win_pct, 0, false))}</b></div><div><span>PF</span><b>${esc(isN(f.pf) ? (f.pf >= 99 ? '99+' : grp(f.pf, 1)) : '—')}</b></div>
        <div><span>решений</span><b>${esc(f.decisions != null ? f.decisions : '—')}</b></div><div><span>держит</span><b>${esc(hold(f.hold_med_h))}</b></div>
        <div><span>за 60 дн</span><b>${esc(usd(f.pnl_60d, true))}</b></div><div><span>плечо · биржа</span><b>${esc(isN(f.lev_med) ? grp(f.lev_med, 0) + '×' : '—')} · ${esc(SRC[f.src] || f.src || '—')}</b></div></div>
      ${f.why_ru ? `<p class="fo-note">${esc(f.why_ru)}</p>` : ''}
      ${copiesTable(f.copies || [])}
      <div class="fo-tot"><span>Нам дал: закрыто ${esc(T.n || 0)}${T.n ? ` (в плюсе ${esc(T.wins)})` : ''}${T.open_n ? `, открыто ${esc(T.open_n)}` : ''}</span><b class="${tone(T.total_usd, 2)}">${esc(usd(T.total_usd, true))}</b></div></div>`;
  }
  function strategy(doc) {
    const F = doc.following || [], lockNext = (doc.lock || {}).next_t;
    const all = F.concat(doc.dropped || []);
    const sum = (k) => all.reduce((a, f) => a + (+((f.totals || {})[k]) || 0), 0);
    const n = sum('n'), tot = sum('total_usd');
    const summary = `<div class="fo-card"><div class="t"><b>Итого по стратегии</b><span class="fo-chip">реальный счёт с 04.11</span></div>
      <div class="fo-kv"><div><span>копий закрыто</span><b>${esc(n)}</b></div><div><span>открыто</span><b>${esc(sum('open_n'))}</b></div><div><span>итог</span><b class="${tone(tot)}">${esc(usd(tot, true))}</b></div>
        <div><span>от текущих</span><b>${esc(usd(F.reduce((a, f) => a + (+(f.totals || {}).total_usd || 0), 0), true))}</b></div><div><span>от выбывших</span><b>${esc(usd((doc.dropped || []).reduce((a, f) => a + (+(f.totals || {}).total_usd || 0), 0), true))}</b></div>
        <div><span>без источника</span><b>${esc((doc.unattributed || {}).n || 0)} · ${esc(usd((doc.unattributed || {}).sum_usd, true))}</b></div></div>
      <p class="fo-note">${esc((doc.lock || {}).ru || '')}</p></div>`;
    const dropped = (doc.dropped || []).filter((f) => (f.copies || []).length);
    const bench = doc.bench || [];
    return `<h3>Стратегия «Копирование китов» <small>${esc(F.length)} трейдеров в списке · пересмотр ${esc(dd(lockNext))}</small></h3>
      <div class="fo-grid">${F.map((f) => traderCard(f, lockNext)).join('')}${summary}</div>
      <details><summary>Выбывшие трейдеры с нашими копиями · ${esc(dropped.length)}</summary><div class="fo-grid">${dropped.map((f) => traderCard(f, lockNext)).join('') || '<p class="fo-note">Нет.</p>'}</div></details>
      <details><summary>Скамейка запасных · ${esc(bench.length)}</summary><div class="fo-tbl"><table><thead><tr><th>Трейдер</th><th class="r">в плюсе</th><th class="r">PF</th><th class="r">решений</th><th class="r">60 дн</th></tr></thead><tbody>${bench.map((b) =>
        `<tr><td style="white-space:normal">${esc(b.name)}<br><span class="m">${esc(b.why_ru || '')}</span></td><td class="r">${esc(pct(b.win_pct, 0, false))}</td><td class="r">${esc(isN(b.pf) ? grp(b.pf, 1) : '—')}</td><td class="r">${esc(b.decisions != null ? b.decisions : '—')}</td><td class="r">${esc(usd(b.pnl_60d, true))}</td></tr>`).join('')}</tbody></table></div></details>`;
  }
  function desk(doc) {
    const D = doc.desk || {};
    const t = (lab, val, sub, cls) => `<div class="fo-tile"><small>${lab}</small><b class="${cls || ''}">${val}</b><em>${sub}</em></div>`;
    return `<h3>Демо-стол «Киты» для сравнения <small>те же сигналы на $10 000</small></h3><div class="fo-vs">
      ${t('Стол с ' + esc(dd(D.started)), esc(pct(D.pnl_pct, 1)), `${esc(usd(D.equity))} · сделок ${esc(D.trades)}, в плюсе ${esc(D.wins)} · просадка ${esc(pct(D.max_dd_pct, 1, false))}`, tone(D.pnl_pct, 0.5))}
      ${t('С 04.11: реальный счёт vs стол', `${esc(pct(D.real_switch_pct, 1))} <span class="m">/</span> ${esc(pct(D.switch_pct, 1))}`, 'реальные фьючерсы к их бюджету против стола к его счёту')}
      ${t('Качество копии', esc(pct(D.avg_entry_gap_pct, 2)), `средняя разница нашего входа с входом трейдера по ${esc(D.gap_n || 0)} сделкам («+» — хуже трейдера)`)}
    </div>`;
  }
  function ivanCard(st, iv, info) {
    const S = st || {};
    const pos = (iv && iv.positions) || [], cl = (iv && iv.closed) || [];
    const lastIn = Math.max(0, ...pos.map((p) => +p.seen || +p.created || 0), ...cl.map((c) => +c.open_time || 0));
    const rows = pos.map((p) => `<tr><td>${esc(p.symbol)} ${sideRu(p.side)}</td><td>${esc(px(p.entry))}</td><td>${p.stop ? esc(px(p.stop)) : '<span class="m">нет</span>'}</td><td class="r ${tone(p.upnl, 2)}">${esc(usd(p.upnl, true))}</td></tr>`).join('')
      + cl.slice(0, 8).map((c) => `<tr><td>${esc(c.symbol)} ${sideRu(c.side)}<br><span class="m">${esc(dd(c.close_time))}</span></td><td>${esc(px(c.entry))}</td><td>${esc(px(c.exit))}</td><td class="r ${tone(c.pnl, 2)}">${esc(usd(c.pnl, true))}</td></tr>`).join('');
    return `<h3>Иван <small>демо-счёт Bybit, только чтение</small></h3><div class="fo-vs">
      <div class="fo-tile"><small>С ${esc(dd(S.first_trade || S.since))}</small><b class="${tone((+S.net_closed_usd || 0) + (+S.open_upnl_usd || 0))}">${esc(usd((+S.net_closed_usd || 0) + (+S.open_upnl_usd || 0), true))}</b><em>закрыто ${esc(S.closed_n != null ? S.closed_n : '—')} (в плюсе ${esc(S.wins != null ? S.wins : '—')}) · просадка ${esc(pct(S.max_dd_pct, 1, false))}</em></div>
      <div class="fo-tile"><small>Открыто сейчас</small><b>${esc(S.open_n != null ? S.open_n : '—')}</b><em>по ним ${esc(usd(S.open_upnl_usd, true))}</em></div>
      <div class="fo-tile"><small>Последний вход</small><b>${iv ? esc(lastIn ? ddt(lastIn) : 'нет') : '—'}</b><em>${iv ? 'по данным счёта, точность ±15 мин' : 'подробности — после входа владельца (пароль «Иван тест»)'}</em></div></div>
      ${rows ? `<div class="fo-tbl" style="margin-top:10px"><table><thead><tr><th>Сделка</th><th>Вход</th><th>Стоп / выход</th><th class="r">Итог</th></tr></thead><tbody>${rows}</tbody></table></div>` : ''}
      <p class="fo-note">${esc((info || {}).source_ru || '')} ${esc((info || {}).copy_ru || '')}</p>`;
  }
  function tests(doc) {
    const T = doc.tests || [];
    const row = (x, main) => `<tr><td><b style="font-weight:${main ? 700 : 500}">${esc(x.name_ru)}</b>${x.about_ru ? `<br><span class="m" style="white-space:normal">${esc(x.about_ru)}</span>` : ''}</td>
        <td>${esc(x.status_ru || '—')}</td><td class="r">${esc(x.trades != null ? x.trades : '—')}</td><td class="r ${tone(x.net_usd)}">${esc(usd(x.net_usd, true))}${isN(x.net_pct) ? '<br><span class="m">' + esc(pct(x.net_pct, 1)) + '</span>' : ''}</td></tr>`;
    const head = '<thead><tr><th>Стратегия</th><th>Статус</th><th class="r">Сделок</th><th class="r">Итог</th></tr></thead>';
    const main = T.slice(0, 2), rest = T.slice(2);
    return `<h3>Тестирование стратегий</h3><div class="fo-tbl"><table>${head}<tbody>${main.map((x) => row(x, true)).join('')}</tbody></table></div>
      ${rest.length ? `<details><summary>Все остальные тесты · ${esc(rest.length)}</summary><div class="fo-tbl"><table>${head}<tbody>${rest.map((x) => row(x, false)).join('')}</tbody></table></div></details>` : ''}`;
  }

  function html(doc, opt) {
    css();
    opt = opt || {};
    if (!doc || !doc.kpi) return '<div class="fo"><p class="fo-note">Сводка фьючерсов ещё не собрана (web/fut_owner.json).</p></div>';
    return `<div class="fo"><div class="fo-head"><b>Фьючерсы · реальный счёт OKX с 22.10</b><span>видно только владельцу · данные ${esc(ddt(doc.asof))}</span></div>
      ${chart(doc)}${tiles(doc.kpi)}${strategy(doc)}${desk(doc)}${ivanCard(opt.ivanStats || (doc.ivan || {}).stats, opt.ivan, doc.ivan)}${tests(doc)}</div>`;
  }
  window.CNFutOwner = { html, mount };
})();
