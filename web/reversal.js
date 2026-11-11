/* CRYPTO NOVΛ — «Разворот (тест)» (11.11.2026): a PAPER test desk, owner views only. One renderer for both panels.
   Data: GET /v2/owner/reversal (cnq/api/reversal.py) — never a public file: its rule speaks about our own copies. */
(function () {
  'use strict';
  if (window.CNReversal) return;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (v, d) => (v == null || !isFinite(v) ? '—' : (+v < 0 ? '−' : '') + Math.abs(+v).toLocaleString('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d }));
  const usd = (v, d = 2) => (v == null || !isFinite(v) ? '—' : (+v > 0 ? '+' : +v < 0 ? '−' : '') + '$' + Math.abs(+v).toLocaleString('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d }));
  const pct = (v, d = 2) => (v == null || !isFinite(v) ? '—' : (+v > 0 ? '+' : +v < 0 ? '−' : '') + Math.abs(+v).toLocaleString('ru-RU', { maximumFractionDigits: d }) + '%');
  const cls = (v) => (+v > 0 ? 'pos' : +v < 0 ? 'neg' : '');
  const dt = (t) => (t ? new Date(t).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—');
  const side = (s) => (s === 'LONG' ? '▲ лонг' : s === 'SHORT' ? '▼ шорт' : '—');
  const CSS = `.rv-k{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:8px 0 12px}.rv-k>div{border:1px solid var(--line,rgba(127,127,127,.25));border-radius:10px;padding:8px 10px;display:grid;gap:2px}
.rv-k small,.rv-m{color:var(--muted,#8a94a6);font-size:12px;line-height:1.5}.rv-k b{font-size:16px}.rv-t{width:100%;border-collapse:collapse;font-size:12.5px}.rv-t th,.rv-t td{padding:5px 6px;border-bottom:1px solid var(--line,rgba(127,127,127,.18));text-align:right}
.rv-t th:first-child,.rv-t td:first-child{text-align:left}.rv-w{overflow-x:auto}.rv-h{font-size:13px;margin:12px 0 4px}.rv-c{width:100%;height:90px;display:block}.rv .pos{color:var(--pos,#22c55e)}.rv .neg{color:var(--neg,#ef4444)}
.rv-box{border:1px solid var(--line,rgba(127,127,127,.25));border-radius:10px;padding:8px 10px;margin:8px 0;font-size:12.5px;line-height:1.5}
@media (max-width:760px){.rv-k{grid-template-columns:1fr 1fr}}`;

  function curve(D) {
    const cl = (D.closed || []).slice().sort((a, b) => (a.closed_t || 0) - (b.closed_t || 0));
    if (!cl.length) return '';
    let eq = +D.start || 10000;
    const pts = [[D.started || cl[0].opened_t, eq]];
    cl.forEach((c) => { eq += +c.pnl || 0; pts.push([c.closed_t, eq]); });
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const d = pts.map((p, i) => (i ? 'L' : 'M') + (((p[0] - x0) / Math.max(1, x1 - x0)) * 300).toFixed(1) + ' ' + (86 - ((p[1] - y0) / Math.max(1e-9, y1 - y0)) * 80).toFixed(1)).join(' ');
    return `<svg class="rv-c" viewBox="0 0 300 90" preserveAspectRatio="none" aria-label="Бумажный счёт"><path d="${d}" fill="none" stroke="${eq >= pts[0][1] ? 'var(--pos,#22c55e)' : 'var(--neg,#ef4444)'}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
  }

  function html(R, opts) {
    opts = opts || {};
    const head = `<h2>${opts.icon || ''} Разворот (тест) <span class="r muted">бумажный стол · тест · видно только тебе</span></h2>`;
    if (!R) return `<style>${CSS}</style><div class="rv">${head}<p class="rv-m">${esc(opts.err || 'Загружаю…')}</p></div>`;
    const D = R.data || R, B = D.backtest || null;
    const k = (lab, val, sub) => `<div><small>${lab}</small><b class="num">${val}</b>${sub ? `<span class="rv-m">${sub}</span>` : ''}</div>`;
    const kp = `<div class="rv-k">${k('Бумажный счёт', '$' + num(D.equity, 0), `<span class="${cls(D.pnl_usd)}">${usd(D.pnl_usd)}</span>`)}
      ${k('Закрыто сделок', D.trades || 0, D.trades ? `в плюсе ${D.wins || 0}` : 'пока нет')}
      ${k('Открыто', (D.open || []).length, 'не больше 5')}${k('Ждут сигнала', (D.waiting || []).length, 'кандидаты на разворот')}</div>`;
    const bt = B ? `<div class="rv-box"><b>Проверка на истории</b> (${esc(B.period || '')}): ${num(B.n, 0)} сделок, в среднем ${pct(B.mean_pct)} на сделку,
      медиана ${pct(B.median_pct)}, в плюсе ${num(B.win_pct, 0)}%. Первая половина ${pct(B.first_half_pct)}, вторая ${pct(B.second_half_pct)}.
      <br><b>Оговорки:</b> ${esc(B.note_ru || '')}</div>` : '';
    const op = (D.open || []).map((o) => `<tr><td><b>${esc(o.coin)}</b> ${side(o.side)}</td><td>${num(o.entry, 4)}</td><td>${num(o.price, 4)}</td><td>$${num(o.notional, 0)}</td>
      <td class="${cls(o.upnl)}">${usd(o.upnl)}</td><td>${dt(o.opened_t)}</td></tr>`).join('');
    const cl = (D.closed || []).slice(0, 20).map((c) => `<tr><td><b>${esc(c.coin)}</b> ${side(c.side)}</td><td>${num(c.entry, 4)} → ${num(c.exit, 4)}</td>
      <td class="${cls(c.pnl)}">${usd(c.pnl)} (${pct(c.pnl_pct)})</td><td>${dt(c.closed_t)}</td><td class="rv-m">${esc(c.why || '')}</td></tr>`).join('');
    const wt = (D.waiting || []).map((w) => `${esc(w.coin)} ${side(w.side)}`).join(' · ');
    return `<style>${CSS}</style><div class="rv">${head}
      <p class="rv-m">${esc(D.label_ru || '')} Обновлено ${dt(D.asof)}.</p>
      <div class="rv-box"><b>Правило.</b> ${esc(D.rule_ru || '')}</div>${bt}${kp}${curve(D)}
      <div class="rv-h"><b>Открытые бумажные позиции</b></div>
      ${op ? `<div class="rv-w"><table class="rv-t"><tr><th>Позиция</th><th>Вход</th><th>Сейчас</th><th>Размер</th><th>Результат</th><th>Открыта</th></tr>${op}</table></div>` : '<p class="rv-m">Открытых позиций нет.</p>'}
      ${wt ? `<p class="rv-m">Ждут сигнала: ${wt}</p>` : ''}
      <div class="rv-h"><b>Закрытые сделки</b></div>
      ${cl ? `<div class="rv-w"><table class="rv-t"><tr><th>Сделка</th><th>Цена</th><th>Итог</th><th>Закрыта</th><th>Почему</th></tr>${cl}</table></div>` : '<p class="rv-m">Закрытых сделок пока нет.</p>'}</div>`;
  }
  window.CNReversal = { html };
})();
