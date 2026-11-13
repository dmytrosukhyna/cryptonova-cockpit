/* CRYPTO NOVΛ — «Альты от китов (тест)» (13.11.2026): a PAPER test desk, owner views only. One renderer for both panels.
   Data: GET /v2/owner/smart_alts (cnq/api/smart_alts.py) — never a public file. */
(function () {
  'use strict';
  if (window.CNSmartAlts) return;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (v, d) => (v == null || !isFinite(v) ? '—' : (+v < 0 ? '−' : '') + Math.abs(+v).toLocaleString('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d }));
  const usd = (v, d = 2) => (v == null || !isFinite(v) ? '—' : (+v > 0 ? '+' : +v < 0 ? '−' : '') + '$' + Math.abs(+v).toLocaleString('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d }));
  const pct = (v, d = 2) => (v == null || !isFinite(v) ? '—' : (+v > 0 ? '+' : +v < 0 ? '−' : '') + Math.abs(+v).toLocaleString('ru-RU', { maximumFractionDigits: d }) + '%');
  const px = (v) => (v == null || !isFinite(v) ? '—' : +v >= 1 ? num(v, 4) : (+v).toPrecision(4));
  const cls = (v) => (+v > 0 ? 'pos' : +v < 0 ? 'neg' : '');
  const dt = (t) => (t ? new Date(t).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—');
  const CSS = `.sa-k{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:8px 0 12px}.sa-k>div{border:1px solid var(--line,rgba(127,127,127,.25));border-radius:10px;padding:8px 10px;display:grid;gap:2px}
.sa-k small,.sa-m{color:var(--muted,#8a94a6);font-size:12px;line-height:1.5}.sa-k b{font-size:16px}.sa-t{width:100%;border-collapse:collapse;font-size:12.5px}.sa-t th,.sa-t td{padding:5px 6px;border-bottom:1px solid var(--line,rgba(127,127,127,.18));text-align:right;vertical-align:top}
.sa-t th:first-child,.sa-t td:first-child{text-align:left}.sa-w{overflow-x:auto}.sa-h{font-size:13px;margin:12px 0 4px}.sa-c{width:100%;height:90px;display:block}.sa .pos{color:var(--pos,#22c55e)}.sa .neg{color:var(--neg,#ef4444)}
.sa-box{border:1px solid var(--line,rgba(127,127,127,.25));border-radius:10px;padding:8px 10px;margin:8px 0;font-size:12.5px;line-height:1.5}
@media (max-width:760px){.sa-k{grid-template-columns:1fr 1fr}}`;

  function curve(D) {
    const cl = (D.closed || []).slice().sort((a, b) => (a.closed_t || 0) - (b.closed_t || 0));
    if (!cl.length) return '';
    let eq = +D.start || 10000;
    const pts = [[D.started || cl[0].opened_t, eq]];
    cl.forEach((c) => { eq += +c.pnl || 0; pts.push([c.closed_t, eq]); });
    if (D.asof && D.equity != null) pts.push([D.asof, +D.equity]);
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const d = pts.map((p, i) => (i ? 'L' : 'M') + (((p[0] - x0) / Math.max(1, x1 - x0)) * 300).toFixed(1) + ' ' + (86 - ((p[1] - y0) / Math.max(1e-9, y1 - y0)) * 80).toFixed(1)).join(' ');
    return `<svg class="sa-c" viewBox="0 0 300 90" preserveAspectRatio="none" aria-label="Бумажный счёт"><path d="${d}" fill="none" stroke="${pts[pts.length - 1][1] >= pts[0][1] ? 'var(--pos,#22c55e)' : 'var(--neg,#ef4444)'}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
  }

  const tok = (o) => `<b>${esc(o.symbol)}</b>${o.chain ? ` <span class="sa-m">${esc(o.chain)}</span>` : ''}${(o.buyers || []).length ? `<div class="sa-m">${esc(o.buyers.join(', '))}</div>` : ''}`;
  const vs = (mine, b, a) => `<span class="${cls(mine)}">${usd(mine)}</span><div class="sa-m">BTC ${usd(b)} · альты ${usd(a)}</div>`;

  function html(R, opts) {
    opts = opts || {};
    const head = `<h2>${opts.icon || ''} Альты от китов (тест) <span class="r muted">бумажный стол · тест · видно только тебе</span></h2>`;
    if (!R) return `<style>${CSS}</style><div class="sa">${head}<p class="sa-m">${esc(opts.err || 'Загружаю…')}</p></div>`;
    const D = R.data || R, BM = D.benchmarks || {}, B = D.backtest || null;
    const k = (lab, val, sub) => `<div><small>${lab}</small><b class="num">${val}</b>${sub ? `<span class="sa-m">${sub}</span>` : ''}</div>`;
    const kp = `<div class="sa-k">${k('Бумажный счёт', '$' + num(D.equity, 0), `<span class="${cls(D.pnl_usd)}">${usd(D.pnl_usd)}</span>`)}
      ${k('BTC на те же даты и суммы', `<span class="${cls(BM.btc_same_timing_usd)}">${usd(BM.btc_same_timing_usd)}</span>`, 'для сравнения')}
      ${k('20 крупных альтов на те же даты', `<span class="${cls(BM.top20_alts_same_timing_usd)}">${usd(BM.top20_alts_same_timing_usd)}</span>`, 'для сравнения')}
      ${k('Сделок', D.trades || 0, `в плюсе ${D.wins || 0} · открыто ${(D.open || []).length}`)}</div>`;
    const op = (D.open || []).map((o) => `<tr><td>${tok(o)}</td><td>${px(o.entry)} → ${px(o.price)}</td><td>$${num(o.notional, 0)}</td>
      <td>${vs(o.upnl, o.btc_same_time_usd, o.alts_same_time_usd)}</td><td>${dt(o.opened_t)}</td></tr>`).join('');
    const cl = (D.closed || []).slice(0, 25).map((c) => `<tr><td>${tok(c)}</td><td>${px(c.entry)} → ${px(c.exit)}</td>
      <td>${vs(c.pnl, c.btc_same_time_usd, c.alts_same_time_usd)}<div class="sa-m">${pct(c.pnl_pct)}</div></td><td>${dt(c.opened_t)}<div class="sa-m">${dt(c.closed_t)}</div></td>
      <td class="sa-m">${esc(c.why || '')}</td></tr>`).join('');
    const opens = {};
    (D.events || []).forEach((e) => { if (e.kind === 'open' && e.symbol) opens[e.symbol] = e.why; });
    const bt = B ? `<div class="sa-box"><b>Проверка на истории</b> (${esc(B.from || '')} – ${esc(B.to || '')}, ${num(B.days, 0)} дн.): закрыто ${num(B.trades_closed, 0)},
      открыто ${num(B.open_now, 0)}, итог ${usd(B.pnl_usd)}.${(B.closed || []).length ? ' ' + B.closed.map((c) => `${esc(c.symbol)} ${pct(c.pnl_pct)} (${esc(c.why)})`).join(' · ') + '.' : ''}
      <br><b>Оговорка:</b> ${esc(B.note_ru || '')}</div>` : '';
    return `<style>${CSS}</style><div class="sa">${head}
      <p class="sa-m">${esc(D.label_ru || '')} Обновлено ${dt(D.asof)}.</p>
      <div class="sa-box"><b>Правило.</b> ${esc(D.rule_ru || '')}</div>${bt}${kp}${curve(D)}
      <div class="sa-h"><b>Открытые бумажные позиции</b> <span class="sa-m">· результат и то же на BTC / 20 альтах</span></div>
      ${op ? `<div class="sa-w"><table class="sa-t"><tr><th>Токен · сеть · покупатели</th><th>Цена</th><th>Размер</th><th>Результат</th><th>Куплен</th></tr>${op}</table></div>` : '<p class="sa-m">Открытых позиций нет.</p>'}
      ${Object.keys(opens).length ? `<p class="sa-m">Почему куплены: ${Object.entries(opens).slice(-5).map(([s, w]) => `${esc(s)} — ${esc(w)}`).join('; ')}</p>` : ''}
      <div class="sa-h"><b>Закрытые сделки</b></div>
      ${cl ? `<div class="sa-w"><table class="sa-t"><tr><th>Токен · сеть · покупатели</th><th>Цена</th><th>Итог и сравнение</th><th>Куплен / продан</th><th>Почему продан</th></tr>${cl}</table></div>` : '<p class="sa-m">Закрытых сделок пока нет.</p>'}</div>`;
  }
  window.CNSmartAlts = { html };
})();
