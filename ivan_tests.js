/* CRYPTO NOVΛ — «Иван · тест-стратегии» (13.11.2026): Ivan's Bybit demo accounts, observation + a paper copy, owner views only.
   One renderer for both panels. Data: GET /v2/owner/ivan_tests (cnq/api/ivan_tests.py) — never a public file; keys never come back. */
(function () {
  'use strict';
  if (window.CNIvanTests) return;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (v, d) => (v == null || !isFinite(v) ? '—' : (+v < 0 ? '−' : '') + Math.abs(+v).toLocaleString('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d }));
  const usd = (v, d = 2) => (v == null || !isFinite(v) ? '—' : (+v > 0 ? '+' : +v < 0 ? '−' : '') + '$' + Math.abs(+v).toLocaleString('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d }));
  const pct = (v, d = 2) => (v == null || !isFinite(v) ? '—' : (+v > 0 ? '+' : +v < 0 ? '−' : '') + Math.abs(+v).toLocaleString('ru-RU', { maximumFractionDigits: d }) + '%');
  const cls = (v) => (+v > 0 ? 'pos' : +v < 0 ? 'neg' : '');
  const dt = (t) => (t ? new Date(t).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—');
  const side = (s) => (s === 'LONG' ? '▲ лонг' : s === 'SHORT' ? '▼ шорт' : '—');
  const CSS = `.it-t{width:100%;border-collapse:collapse;font-size:12.5px}.it-t th,.it-t td{padding:5px 6px;border-bottom:1px solid var(--line,rgba(127,127,127,.18));text-align:right;vertical-align:top}
.it-t th:first-child,.it-t td:first-child{text-align:left}.it-w{overflow-x:auto}.it-m{color:var(--muted,#8a94a6);font-size:12px;line-height:1.5}.it-h{font-size:13px;margin:12px 0 4px}
.it .pos{color:var(--pos,#22c55e)}.it .neg{color:var(--neg,#ef4444)}.it-box{border:1px solid var(--line,rgba(127,127,127,.25));border-radius:10px;padding:8px 10px;margin:8px 0;font-size:12.5px;line-height:1.5}
.it-c{width:100%;height:70px;display:block}.it-acc{border-top:1px solid var(--line,rgba(127,127,127,.25));padding-top:10px;margin-top:12px}`;

  function spark(pts) {
    pts = (pts || []).filter((p) => Array.isArray(p) && isFinite(p[1]));
    if (pts.length < 2) return '';
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const d = pts.map((p, i) => (i ? 'L' : 'M') + (((p[0] - x0) / Math.max(1, x1 - x0)) * 300).toFixed(1) + ' ' + (66 - ((p[1] - y0) / Math.max(1e-9, y1 - y0)) * 60).toFixed(1)).join(' ');
    return `<svg class="it-c" viewBox="0 0 300 70" preserveAspectRatio="none"><path d="${d}" fill="none" stroke="${ys[ys.length - 1] >= ys[0] ? 'var(--pos,#22c55e)' : 'var(--neg,#ef4444)'}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
  }

  function compare(accs) {
    const rows = accs.filter((a) => a.data).map((a) => { const s = a.data.summary || {};
      return `<tr><td><b>${esc(a.name)}</b> <span class="it-m">${a.host === 'demo' ? 'демо' : 'реальный'}</span></td><td class="${cls(s.pnl_pct)}">${pct(s.pnl_pct)}</td>
        <td>${s.winrate != null ? Math.round(s.winrate * 100) + '%' : '—'} <span class="it-m">из ${num(s.trades, 0)}</span></td><td class="${cls(s.avg_trade_pct)}">${pct(s.avg_trade_pct, 3)}</td>
        <td>${s.max_dd_pct != null ? '−' + num(s.max_dd_pct, 1) + '%' : '—'}</td><td>${num(s.open_n, 0)}</td>
        <td class="${cls(s.paper_pnl_usd)}">${usd(s.paper_pnl_usd)} <span class="it-m">${pct(s.paper_pnl_pct)} · DD −${num(s.paper_max_dd_pct, 1)}%</span></td></tr>`; }).join('');
    return rows ? `<div class="it-h"><b>Сравнение</b></div><div class="it-w"><table class="it-t"><tr><th>Счёт</th><th>Результат, % баланса</th><th>В плюсе</th><th>Средняя сделка</th>
      <th>Макс. просадка</th><th>Открыто</th><th>Наша копия на бумаге ($10 000)</th></tr>${rows}</table></div>` : '';
  }

  function account(a) {
    const D = a.data, l = a.last || {};
    const head = `<div class="it-h"><b>${esc(a.name)}</b> <span class="it-m">Bybit ${a.host === 'demo' ? 'демо' : 'реальный'} · ключ ${esc(a.key)}${l.t ? ` · ${dt(l.t)}: ${esc(l.msg || '')}` : ''}</span>
      <button class="ib sm" type="button" data-it="check" data-id="${esc(a.id)}">Проверить</button> <button class="ib sm" type="button" data-it="remove" data-id="${esc(a.id)}">Отключить</button></div>`;
    if (!D) return `<div class="it-acc">${head}<p class="it-m">Данных ещё нет — первое чтение идёт раз в 90 секунд.</p></div>`;
    const A = D.account || {}, P = D.paper || {};
    const pos = (A.positions || []).map((p) => `<tr><td><b>${esc(p.symbol)}</b> ${side(p.side)}</td><td>${num(p.entry, 4)} → ${num(p.mark, 4)}</td><td>×${num(p.leverage, 0)}</td>
      <td class="${cls(p.upnl)}">${usd(p.upnl)}</td><td>${p.stop ? num(p.stop, 4) : '—'} / ${p.tp ? num(p.tp, 4) : '—'}</td></tr>`).join('');
    const cl = (A.closed || []).slice(0, 12).map((t) => `<tr><td><b>${esc(t.symbol)}</b> ${side(t.side)}</td><td>${num(t.entry, 4)} → ${num(t.exit, 4)}</td>
      <td class="${cls(t.pnl)}">${usd(t.pnl)}</td><td>${dt(t.close_time)}</td></tr>`).join('');
    return `<div class="it-acc">${head}
      <p class="it-m">Баланс ${usd(A.equity).replace('+', '')} · в позициях ${usd(A.upnl)} · макс. просадка −${num((A.max_dd || 0) * 100, 1)}%</p>${spark(A.equity_hist)}
      ${pos ? `<div class="it-w"><table class="it-t"><tr><th>Открыто</th><th>Вход → сейчас</th><th>Плечо</th><th>Результат</th><th>Стоп / тейк</th></tr>${pos}</table></div>` : '<p class="it-m">Открытых позиций нет.</p>'}
      ${cl ? `<div class="it-w"><table class="it-t"><tr><th>Закрыто</th><th>Цена</th><th>Итог</th><th>Когда</th></tr>${cl}</table></div>` : '<p class="it-m">Закрытых сделок пока нет.</p>'}
      <div class="it-box"><b>Наша копия на бумаге:</b> $${num(P.equity, 0)} (<span class="${cls(P.pnl_usd)}">${usd(P.pnl_usd)}</span>), сделок ${num(P.trades, 0)},
        в плюсе ${P.winrate != null ? Math.round(P.winrate * 100) + '%' : '—'}, просадка −${num(P.max_dd_pct, 1)}%.${spark(P.curve)}</div></div>`;
  }

  function form() {
    return `<details class="edet" data-it="formbox"><summary>Подключить счёт Ивана (ключ только для чтения)</summary>
      <p class="it-m"><b>Только ключ «Read-Only».</b> Ключ с правом торговли или вывода сервер не сохранит. Ключ и секрет обратно не показываются.</p>
      <form data-it="form" autocomplete="off" style="display:grid;gap:6px;max-width:420px;margin:8px 0">
        <input name="name" placeholder="Имя, например «Иван A»" required>
        <input name="key" autocomplete="off" placeholder="API Key" required>
        <input name="secret" type="password" autocomplete="new-password" placeholder="API Secret" required>
        <label class="it-m"><input type="checkbox" name="demo" checked> Демо-счёт (Demo Trading)</label>
        <div><button class="ib sm" type="submit">Подключить</button></div></form><div class="it-m" data-it="msg"></div></details>`;
  }

  function html(R, opts) {
    opts = opts || {};
    const head = `<h2>${opts.icon || ''} Иван · тест-стратегии <span class="r muted">демо-счета Bybit · тест · видно только тебе</span></h2>`;
    if (!R) return `<style>${CSS}</style><div class="it">${head}<p class="it-m">${esc(opts.err || 'Загружаю…')}</p>${form()}</div>`;
    const accs = R.accounts || [];
    return `<style>${CSS}</style><div class="it">${head}<p class="it-m">${esc(R.label_ru || '')}</p>
      <div class="it-box">${esc(R.paper_rule_ru || '')}</div>
      ${accs.length ? compare(accs) + accs.map(account).join('') : '<p class="it-m">Счета ещё не подключены.</p>'}${form()}</div>`;
  }

  function mount(el, api, rerender) {
    if (!api) return;
    const msg = el.querySelector('[data-it="msg"]'), f = el.querySelector('[data-it="form"]');
    const done = (r) => { if (msg) msg.textContent = (r && r.message_ru) || ''; if (r && r.ok && rerender) rerender(r); };
    if (f) f.addEventListener('submit', async (e) => { e.preventDefault(); const b = f.querySelector('[type="submit"]'); b.disabled = true; msg.textContent = 'Проверяю ключ…';
      const d = new FormData(f);
      try { const r = await api.add({ name: String(d.get('name') || '').trim(), key: String(d.get('key') || '').trim(), secret: String(d.get('secret') || '').trim(), demo: !!d.get('demo') });
        f.reset(); done(r); } catch (err) { msg.textContent = 'Не получилось: ' + ((err && err.message) || err); }
      b.disabled = false; });
    el.querySelectorAll('[data-it="check"],[data-it="remove"]').forEach((b) => b.addEventListener('click', async () => {
      const rm = b.dataset.it === 'remove';
      if (rm && !window.confirm('Отключить счёт и удалить ключ с сервера?')) return;
      b.disabled = true;
      try { const r = await (rm ? api.remove : api.check)(b.dataset.id); if (msg) msg.textContent = (r && r.message_ru) || ''; if (rerender) rerender(r); }
      catch (err) { if (msg) msg.textContent = 'Не получилось: ' + ((err && err.message) || err); }
      b.disabled = false; }));
  }
  window.CNIvanTests = { html, mount };
})();
