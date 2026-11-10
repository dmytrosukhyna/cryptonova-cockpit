/* CRYPTO NOVΛ — «Питер» (11.11.2026): an MT5 gold trader under observation, owner views only. One renderer for both panels
   (app.html owner session and vip.html isOwner). Data: GET /v2/owner/peter (cnq/api/peter.py) — never a public file.
   Observation only: no orders anywhere. */
(function () {
  'use strict';
  if (window.CNPeter) return;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (v, d) => (v == null || !isFinite(v) ? '—' : (+v < 0 ? '−' : '') + Math.abs(+v).toLocaleString('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d }));
  const usd = (v, d = 2) => (v == null || !isFinite(v) ? '—' : (+v < 0 ? '−' : '') + '$' + Math.abs(+v).toLocaleString('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d }));
  const pct = (v, d = 2) => (v == null || !isFinite(v) ? '—' : (+v > 0 ? '+' : +v < 0 ? '−' : '') + Math.abs(+v).toLocaleString('ru-RU', { maximumFractionDigits: d }) + '%');
  const cls = (v) => (+v > 0 ? 'pos' : +v < 0 ? 'neg' : '');
  const dt = (t) => (t ? new Date(t).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—');
  const hold = (m) => (m == null ? '—' : m < 60 ? m + ' мин' : m < 1440 ? (m / 60).toFixed(1).replace('.', ',') + ' ч' : (m / 1440).toFixed(1).replace('.', ',') + ' д');
  const side = (s) => (s === 'LONG' ? '▲ лонг' : s === 'SHORT' ? '▼ шорт' : '—');
  const CSS = `.pt-k{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;margin:8px 0 12px}.pt-k>div{border:1px solid var(--line,rgba(127,127,127,.25));border-radius:10px;padding:8px 10px;display:grid;gap:2px}
.pt-k small{color:var(--muted,#8a94a6);font-size:11px}.pt-k b{font-size:16px}.pt-t{width:100%;border-collapse:collapse;font-size:12.5px}.pt-t th,.pt-t td{padding:5px 6px;border-bottom:1px solid var(--line,rgba(127,127,127,.18));text-align:right;white-space:nowrap}
.pt-t th:first-child,.pt-t td:first-child{text-align:left}.pt-w{overflow-x:auto}.pt-h{font-size:13px;margin:12px 0 4px}.pt-d{display:flex;gap:6px;flex-wrap:wrap;font-size:12px}.pt-d span{border:1px solid var(--line,rgba(127,127,127,.25));border-radius:8px;padding:3px 8px}
.pt-c{width:100%;height:90px;display:block}.pt-m{color:var(--muted,#8a94a6);font-size:12px;line-height:1.5}.pt .pos{color:var(--pos,#22c55e)}.pt .neg{color:var(--neg,#ef4444)}
@media (max-width:760px){.pt-k{grid-template-columns:1fr 1fr}.pt-t td,.pt-t th{white-space:normal}}`;

  function curve(c) {
    const pts = (c || []).filter((p) => Array.isArray(p) && isFinite(p[1]));
    if (pts.length < 2) return '';
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const X = (x) => ((x - x0) / Math.max(1, x1 - x0)) * 300, Y = (y) => 86 - ((y - y0) / Math.max(1e-9, y1 - y0)) * 80;
    const d = pts.map((p, i) => (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ' ' + Y(p[1]).toFixed(1)).join(' ');
    const up = ys[ys.length - 1] >= ys[0];
    return `<svg class="pt-c" viewBox="0 0 300 90" preserveAspectRatio="none" aria-label="Условные $10 000"><path d="${d}" fill="none" stroke="${up ? 'var(--pos,#22c55e)' : 'var(--neg,#ef4444)'}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
  }

  function ctxLine(c) {
    if (!c) return '—';
    const bits = [(c.sessions || []).join(' / '), c.weekday];
    if (c.vs_sma200_D1_pct != null) bits.push('к SMA200 D1 ' + pct(c.vs_sma200_D1_pct));
    if (c.atr14_D1_pct != null) bits.push('ATR D1 ' + num(c.atr14_D1_pct, 2) + '%');
    return esc(bits.filter(Boolean).join(' · '));
  }

  function html(R, opts) {
    opts = opts || {};
    const head = `<h2>${opts.icon || ''} Питер <span class="r muted">MT5 · золото · наблюдение · видно только тебе</span></h2>`;
    if (!R) return `<style>${CSS}</style><div class="pt">${head}<p class="pt-m">${esc(opts.err || 'Загружаю…')}</p></div>`;
    const L = R.data || R, s = L.stats || {};
    const hb = L.last_hb, late = hb && Date.now() - hb > 30 * 60000;
    const k = (lab, val, sub) => `<div><small>${lab}</small><b class="num">${val}</b>${sub ? `<span class="pt-m">${sub}</span>` : ''}</div>`;
    const kp = `<div class="pt-k">${k('Условные $10 000', `<span class="${cls(L.pnl_notional)}">${usd(L.equity_notional, 0)}</span>`, pct(L.pnl_pct))}
      ${k('В плюсе', s.winrate != null ? Math.round(s.winrate * 100) + '%' : '—', `${s.wins || 0} из ${s.trades || 0}`)}
      ${k('Средняя сделка', `<span class="${cls(s.avg_pct)}">${pct(s.avg_pct, 3)}</span>`, s.avg_usd_notional != null ? usd(s.avg_usd_notional) + ' на $10 000' : '')}
      ${k('Макс. просадка', s.max_dd_pct != null ? '−' + num(s.max_dd_pct, 1) + '%' : '—', 'по условной кривой')}
      ${k('Сделок в неделю', num(s.trades_per_week, 1), s.hold_median_min != null ? 'держит ~' + hold(s.hold_median_min) : '')}</div>`;
    const op = (L.open || []).map((o) => `<tr><td><b>${esc(o.symbol)}</b> ${side(o.side)}</td><td>${num(o.volume, 2)}</td><td>${num(o.in_px, 2)}</td><td>${num(o.price, 2)}</td>
      <td>${num(o.sl, 2)} / ${num(o.tp, 2)}</td><td class="${cls(o.float_pct != null ? o.float_pct : o.float_usd)}">${o.float_pct != null ? pct(o.float_pct) : '—'}</td><td>${dt(o.open_t)}</td></tr>`).join('');
    const cl = (L.closed || []).slice(0, 15).map((t) => `<tr><td><b>${esc(t.symbol)}</b> ${side(t.side)}</td><td>${dt(t.open_t)}</td><td>${num(t.in_px, 2)} → ${num(t.out_px, 2)}</td>
      <td>${hold(t.hold_min)}</td><td class="${cls(t.pct)}">${pct(t.pct)}</td><td class="pt-m">${ctxLine(t.entry_ctx)}</td></tr>`).join('');
    const dist = (s.hold_dist || []).filter((x) => x.n).map((x) => `<span>${esc(x.label)}: <b>${x.n}</b></span>`).join('');
    const conn = `<details class="edet"><summary>Подключение терминала Питера</summary><p class="pt-m">Адрес для «Разрешить WebRequest»: <b>https://app.crypto-capital.app</b>.
      Инструкция для Питера — docs/peter-mt5-setup.md. Токен видно только здесь, в сессии владельца: передай его Питеру лично.</p>
      <button class="ib sm" type="button" data-pt="token">Показать токен</button> <code data-pt="tok"></code></details>`;
    return `<style>${CSS}</style><div class="pt">${head}
      ${!(R.configured != null ? R.configured : L.configured) ? '<p class="pt-m">⚠ Токен на сервере ещё не создан (CNQ_PETER_TOKEN) — см. «Подключение» ниже.</p>' : ''}
      ${!hb ? '<p class="pt-m">Терминал Питера ещё не подключался.</p>' : late ? `<p class="pt-m">⚠ Нет связи с терминалом с ${dt(hb)} — сделки придут из очереди, когда он снова включится.</p>` : `<p class="pt-m">Связь с терминалом: ${dt(hb)}.</p>`}
      ${kp}${curve(L.curve)}
      <div class="pt-h"><b>Открытые позиции${(L.open || []).length ? ' · ' + L.open.length : ''}</b></div>
      ${op ? `<div class="pt-w"><table class="pt-t"><tr><th>Позиция</th><th>Лот</th><th>Вход</th><th>Сейчас</th><th>Стоп / тейк</th><th>Сейчас, % счёта</th><th>Открыта</th></tr>${op}</table></div>` : '<p class="pt-m">Открытых позиций нет.</p>'}
      <div class="pt-h"><b>Закрытые сделки</b> <span class="pt-m">· % от баланса на момент сделки</span></div>
      ${cl ? `<div class="pt-w"><table class="pt-t"><tr><th>Сделка</th><th>Открыта</th><th>Цена</th><th>Держал</th><th>Итог</th><th>Момент входа</th></tr>${cl}</table></div>` : '<p class="pt-m">Закрытых сделок пока нет.</p>'}
      ${dist ? `<div class="pt-h"><b>Сколько держит сделку</b></div><div class="pt-d">${dist}</div>` : ''}
      ${conn}<p class="pt-m">${esc(L.about_ru || '')}</p></div>`;
  }

  function mount(el, opts) {
    opts = opts || {};
    const b = el.querySelector('[data-pt="token"]'), out = el.querySelector('[data-pt="tok"]');
    if (b && out && typeof opts.fetchToken === 'function') b.addEventListener('click', async () => {
      b.disabled = true;
      try { const r = await opts.fetchToken(); out.textContent = r && r.token ? r.token : (r && r.how_ru) || 'токен не настроен'; }
      catch (e) { out.textContent = 'не получилось: ' + (e && e.message || e); }
      b.disabled = false;
    });
  }
  window.CNPeter = { html, mount };
})();
