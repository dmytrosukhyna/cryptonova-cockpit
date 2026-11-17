/* CRYPTO NOVΛ — «Иван · тест-стратегии» (13.11.2026): Ivan's Bybit demo accounts, observation + a paper copy, owner views only.
   One renderer for both panels. Data: GET /v2/owner/ivan_tests (cnq/api/ivan_tests.py) — never a public file; keys never come back.
   17.11: the per-account switch «Копировать на реальный счёт» (POST /v2/owner/ivan_tests/copy_real {id, on}; DEFAULT OFF — only the
   owner's own click with a plain confirm turns it on), the study block (the held loss and the drawdown as large as the win rate)
   and the result of OUR real copies of each account. */
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
.it-k{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:8px 0}.it-k>div{border:1px solid var(--line,rgba(127,127,127,.25));border-radius:10px;padding:8px 10px;display:grid;gap:2px}
.it-k small{color:var(--muted,#8a94a6);font-size:11.5px;line-height:1.4}.it-k b{font-size:17px;font-family:var(--mono,monospace)}.it-k .warn{border-color:var(--neg,#ef4444)}
.it-copy{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;justify-content:space-between}.it-copy.on{border-color:var(--amber,#f2c14e)}
.it-sw{min-height:34px;padding:6px 14px;border-radius:999px;border:1px solid var(--line2,rgba(127,127,127,.5));background:none;color:var(--fg,#e8f0fa);font:600 12.5px inherit;cursor:pointer}
.it-sw[aria-checked="true"]{border-color:var(--amber,#f2c14e);background:rgba(242,193,78,.14)}.it-flash{border:1px solid var(--line2,rgba(127,127,127,.5));border-radius:10px;padding:8px 10px;margin:8px 0;font-size:12.5px;line-height:1.5}
.it-links{display:flex;flex-wrap:wrap;gap:8px;align-items:center;border-top:1px solid var(--line,rgba(127,127,127,.25));padding-top:10px;margin-top:12px}.it-links .it-m{flex-basis:100%}
.it-lnk{display:inline-flex;align-items:center;min-height:38px;padding:6px 14px;border-radius:10px;border:1px solid var(--line2,rgba(127,127,127,.5));color:var(--fg,#e8f0fa);text-decoration:none;font-size:13px;font-weight:600}
.it-conn input:not([type=checkbox]){min-height:38px;padding:6px 10px;border-radius:8px;border:1px solid var(--line2,rgba(127,127,127,.5));background:rgba(127,127,127,.08);color:inherit;font:inherit;width:100%;box-sizing:border-box}
@media (max-width:760px){.it-k{grid-template-columns:1fr 1fr}}
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

  // 17.11: what the owner reads before real money starts to follow an account — said plainly, nothing softened
  const confirmOn = (name, slot) => `Включить копирование «${name}» на РЕАЛЬНЫЙ счёт OKX?\n\n`
    + `Это реальные деньги: система будет открывать фьючерсные сделки на реальном счёте вслед за этим тест-счётом Ивана${slot ? ` (как «Иван · тест ${slot}»)` : ''}.\n\n`
    + `Лимиты:\n· все счета Ивана вместе — не больше 50% бюджета фьючерсов;\n· защитный стоп −16% от цены входа (или собственный стоп Ивана, если он стоит в 0,2–15% от входа);\n`
    + `· дневной стоп счёта;\n· не больше 15 копий одновременно, одна копия на монету, без усреднений.\n\n`
    + `Выключение останавливает только НОВЫЕ копии. Уже открытые остаются и закрываются вместе с его позицией или по нашему стопу.\n\n`
    + `Прошлый результат тест-счёта не гарантирует результат копии.`;
  const confirmOff = (name) => `Выключить копирование «${name}» на реальный счёт?\n\nНовых копий не будет. Уже открытые копии останутся и закроются вместе с его позицией или по нашему стопу.`;

  /** 17.11: the switch of ONE account and what our real copies of it gave */
  function copyBox(a) {
    const on = a.copy_real === true, R = a.real, tag = a.slot ? `«Иван · тест ${esc(a.slot)}»` : '';
    const st = on ? `<b>ВКЛЮЧЕНО</b> с ${dt(a.copy_since)} · на реальном счёте это ${tag} (id ${esc(a.copy_id || '')}:)` : `<b>выключено</b>${a.slot ? ` · место ${tag} закреплено` : ''}`;
    const real = R ? `<div class="it-m" style="flex-basis:100%">Наши реальные копии этого счёта: открыто ${num(R.open_n, 0)} (<span class="${cls(R.open_pnl_usd)}">${usd(R.open_pnl_usd)}</span> сейчас) ·
      закрыто ${num(R.closed_n, 0)} (<span class="${cls(R.closed_pnl_usd)}">${usd(R.closed_pnl_usd)}</span>, в плюсе ${num(R.wins, 0)}) · всего <b class="${cls(R.total_pnl_usd)}">${usd(R.total_pnl_usd)}</b>${R.asof ? ` · на ${dt(R.asof)}` : ''}
      ${(R.open || []).map((x) => `<br>${esc(String(x.inst || '').split('-')[0])} ${side(String(x.side || '').toUpperCase())} <span class="${cls(x.upnl_usd)}">${usd(x.upnl_usd)}</span> · с ${dt(x.opened)}`).join('')}</div>`
      : (on ? '<div class="it-m" style="flex-basis:100%">Наших реальных копий этого счёта пока нет.</div>' : '');
    return `<div class="it-box it-copy${on ? ' on' : ''}"><span>Копировать на реальный счёт: ${st}</span>
      <button class="it-sw" type="button" role="switch" aria-checked="${on}" data-it="copy" data-id="${esc(a.id)}" data-on="${on ? '0' : '1'}" data-name="${esc(a.name)}" data-slot="${esc(a.slot || '')}">${on ? 'Выключить' : 'Включить…'}</button>${real}</div>`;
  }

  const EV = { entry: 'вход', add: 'доливка', reduce: 'сокращение', exit: 'выход' };
  /** 17.11: the study — the held loss and the drawdown are as large as the win rate (a win rate counts closed trades only) */
  function studyBox(S) {
    if (!S) return '';
    const wr = S.winrate != null ? Math.round(S.winrate * 100) + '%' : '—';
    const tile = (t, v, sub, warn) => `<div${warn ? ' class="warn"' : ''}><small>${t}</small><b>${v}</b>${sub ? `<small>${sub}</small>` : ''}</div>`;
    const lad = (S.ladders || []).map((L) => { const k = String(L.key || '').split('|');
      return `<tr><td><b>${esc(k[0])}</b> ${side(k[1])}</td><td>${num(L.steps, 0)}</td><td>${num(L.adds, 0)} / ${num(L.avg_downs, 0)}</td><td>${num(L.first_px, 4)} → ${num(L.avg, 4)}</td>
        <td class="${cls(L.worst_upnl)}">${usd(L.worst_upnl)}</td><td>${dt(L.t0)}</td></tr>`; }).join('');
    const ev = (S.last_events || []).slice(0, 12).map((e) => `<tr><td>${dt(e.t)}</td><td><b>${esc(e.symbol)}</b> ${side(e.side)}</td>
      <td>${e.kind === 'add' && e.averaging ? 'усреднение' : (EV[e.kind] || esc(e.kind))}${e.step > 1 ? ` · шаг ${num(e.step, 0)}` : ''}${e.baseline ? ' · уже было открыто' : ''}</td>
      <td>${num(e.fill_px || e.px, 4)}</td><td class="${cls(e.kind === 'exit' ? e.worst_upnl : e.upnl)}">${e.kind === 'exit' ? 'худшее ' + usd(e.worst_upnl) : usd(e.upnl)}</td></tr>`).join('');
    return `<div class="it-h"><b>Изучение стратегии</b> <span class="it-m">с ${dt(S.since)}</span></div>
      <div class="it-k">${tile('Открытый результат (не закрыт)', `<span class="${cls(S.open_pnl_usd)}">${usd(S.open_pnl_usd)}</span>`, `открыто позиций: ${num(S.open_n, 0)}`, +S.open_pnl_usd < 0)}
        ${tile('Худший открытый убыток', `<span class="${cls(S.worst_open_usd)}">${usd(S.worst_open_usd)}</span>`, 'самый глубокий минус по открытым за время наблюдения', +S.worst_open_usd < 0)}
        ${tile('Макс. просадка', S.max_dd_usd ? `<span class="neg">−$${num(S.max_dd_usd, 2)}</span>` : '$0,00', `счёта: −${num(S.max_dd_pct != null ? S.max_dd_pct : S.max_dd_account_pct, 2)}% от пика`, +S.max_dd_usd > 0)}
        ${tile('В плюсе (только закрытые)', wr, `закрытых сделок: ${num(S.closed_n, 0)}`)}
        ${tile('Закрытый результат', `<span class="${cls(S.closed_pnl_usd)}">${usd(S.closed_pnl_usd)}</span>`, `всего с открытыми: ${usd(S.total_pnl_usd)}`)}
        ${tile('Доливки / усреднения', `${num(S.adds, 0)} / ${num(S.averagings, 0)}`, `входов ${num(S.entries, 0)} · сокращений ${num(S.reduces, 0)} · выходов ${num(S.exits, 0)}`)}
        ${tile('Открытые лесенки', `${num(S.open_averaged_n, 0)} из ${num(S.open_n, 0)}`, `самая длинная: шагов ${num(S.open_max_steps, 0)}`)}</div>
      <p class="it-m">${esc(S.note_ru || '')}</p>
      ${lad ? `<div class="it-w"><table class="it-t"><tr><th>Открытая лесенка</th><th>Шагов</th><th>Доливок / против цены</th><th>Первый вход → средняя</th><th>Худший минус</th><th>С</th></tr>${lad}</table></div>` : ''}
      ${ev ? `<div class="it-w"><table class="it-t"><tr><th>Последние события</th><th>Монета</th><th>Что</th><th>Цена</th><th>Результат позиции</th></tr>${ev}</table></div>` : ''}`;
  }

  function account(a) {
    const D = a.data, l = a.last || {};
    const head = `<div class="it-h"><b>${esc(a.name)}</b> <span class="it-m">Bybit ${a.host === 'demo' ? 'демо' : 'реальный'} · ключ ${esc(a.key)}${l.t ? ` · ${dt(l.t)}: ${esc(l.msg || '')}` : ''}</span>
      <button class="ib sm" type="button" data-it="check" data-id="${esc(a.id)}">Проверить</button> <button class="ib sm" type="button" data-it="remove" data-id="${esc(a.id)}">Отключить</button></div>`;
    if (!D) return `<div class="it-acc">${head}${copyBox(a)}<p class="it-m">Данных ещё нет — первое чтение идёт раз в 90 секунд.</p></div>`;
    const A = D.account || {}, P = D.paper || {};
    const pos = (A.positions || []).map((p) => `<tr><td><b>${esc(p.symbol)}</b> ${side(p.side)}</td><td>${num(p.entry, 4)} → ${num(p.mark, 4)}</td><td>×${num(p.leverage, 0)}</td>
      <td class="${cls(p.upnl)}">${usd(p.upnl)}</td><td>${p.stop ? num(p.stop, 4) : '—'} / ${p.tp ? num(p.tp, 4) : '—'}</td></tr>`).join('');
    const cl = (A.closed || []).slice(0, 12).map((t) => `<tr><td><b>${esc(t.symbol)}</b> ${side(t.side)}</td><td>${num(t.entry, 4)} → ${num(t.exit, 4)}</td>
      <td class="${cls(t.pnl)}">${usd(t.pnl)}</td><td>${dt(t.close_time)}</td></tr>`).join('');
    return `<div class="it-acc">${head}${copyBox(a)}${studyBox(a.study || D.study)}
      <p class="it-m">Баланс ${usd(A.equity).replace('+', '')} · в позициях ${usd(A.upnl)} · макс. просадка −${num((A.max_dd || 0) * 100, 1)}%</p>${spark(A.equity_hist)}
      ${pos ? `<div class="it-w"><table class="it-t"><tr><th>Открыто</th><th>Вход → сейчас</th><th>Плечо</th><th>Результат</th><th>Стоп / тейк</th></tr>${pos}</table></div>` : '<p class="it-m">Открытых позиций нет.</p>'}
      ${cl ? `<div class="it-w"><table class="it-t"><tr><th>Закрыто</th><th>Цена</th><th>Итог</th><th>Когда</th></tr>${cl}</table></div>` : '<p class="it-m">Закрытых сделок пока нет.</p>'}
      <div class="it-box"><b>Наша копия на бумаге:</b> $${num(P.equity, 0)} (<span class="${cls(P.pnl_usd)}">${usd(P.pnl_usd)}</span>), сделок ${num(P.trades, 0)},
        в плюсе ${P.winrate != null ? Math.round(P.winrate * 100) + '%' : '—'}, просадка −${num(P.max_dd_pct, 1)}%.${spark(P.curve)}</div></div>`;
  }

  function form(open) {
    return `<details class="edet" data-it="formbox"${open ? ' open' : ''}><summary>Подключить счёт Ивана (ключ только для чтения)</summary>
      <p class="it-m"><b>Только ключ «Read-Only».</b> Ключ с правом торговли или вывода сервер не сохранит. Ключ и секрет обратно не показываются.</p>
      <form data-it="form" autocomplete="off" style="display:grid;gap:6px;max-width:420px;margin:8px 0">
        <input name="name" placeholder="Имя: «Иван Тест 2», «Иван Тест 3» или «Иван Тест 4»" required>
        <span class="it-m">Назовите счёт «Иван Тест 2 / 3 / 4»: цифра в имени закрепляет за ним место для копии на реальный счёт (не больше трёх счетов).</span>
        <input name="key" autocomplete="off" placeholder="API Key" required>
        <input name="secret" type="password" autocomplete="new-password" placeholder="API Secret" required>
        <label class="it-m"><input type="checkbox" name="demo" checked> Демо-счёт (Demo Trading)</label>
        <div><button class="ib sm" type="submit">Подключить</button></div></form><div class="it-m" data-it="msg"></div></details>`;
  }

  function html(R, opts) {
    opts = opts || {};
    const head = `<h2>${opts.icon || ''} Иван · тест-стратегии <span class="r muted">демо-счета Bybit · тест · видно только тебе</span></h2>`;
    const flash = opts.msg ? `<div class="it-flash" role="status">${esc(opts.msg)}</div>` : '';
    if (!R) return `<style>${CSS}</style><div class="it">${head}${flash}<p class="it-m">${esc(opts.err || 'Загружаю…')}</p>${form()}</div>`;
    const accs = R.accounts || [];
    return `<style>${CSS}</style><div class="it">${head}${flash}<p class="it-m">${R.copy_real_any ? '<b>' + esc(R.label_ru || '') + '</b>' : esc(R.label_ru || '')}</p>
      <div class="it-box">${esc(R.paper_rule_ru || '')}</div>${R.copy_rule_ru ? `<div class="it-box"><b>Копия на реальный счёт:</b> ${esc(R.copy_rule_ru)}</div>` : ''}
      ${accs.length ? compare(accs) + accs.map(account).join('') : '<p class="it-m">Счета ещё не подключены.</p>'}${form()}</div>`;
  }

  /** 17.11 (owner 18:31: «не могу найти, куда вставить ключи»): the SAME form and switches as the tab, as one box for the
   *  «Фьючерсы» page — the form is open, every connected account shows its state and its «Копировать на реальный счёт» switch,
   *  and a row of links leads to the test desks. opts.links = [[name, href]]. Owner views only. */
  function connectHtml(R, opts) {
    opts = opts || {};
    const accs = (R && R.accounts) || [];
    const rows = accs.map((a) => { const l = a.last || {};
      return `<div class="it-acc"><div class="it-h"><b>${esc(a.name)}</b> <span class="it-m">Bybit ${a.host === 'demo' ? 'демо' : 'реальный'} · ключ ${esc(a.key)}${l.t ? ` · ${dt(l.t)}: ${esc(l.msg || '')}` : ''}</span>
        <button class="ib sm" type="button" data-it="check" data-id="${esc(a.id)}">Проверить</button> <button class="ib sm" type="button" data-it="remove" data-id="${esc(a.id)}">Отключить</button></div>${copyBox(a)}</div>`; }).join('');
    const links = (opts.links || []).map((x) => `<a class="it-lnk" href="${esc(x[1])}">${esc(x[0])}</a>`).join('');
    return `<style>${CSS}</style><div class="it it-conn"><h2>Подключить стратегии Ивана (Тест 2 / 3 / 4) <span class="r muted">видно только тебе</span></h2>
      ${opts.msg ? `<div class="it-flash" role="status">${esc(opts.msg)}</div>` : ''}
      <p class="it-m">Сюда вставляется ключ «Read-Only» каждого тест-счёта Ивана на Bybit. После подключения у счёта появится переключатель «Копировать на реальный счёт» — он выключен, пока вы сами его не включите.</p>
      ${opts.err ? `<p class="it-m">${esc(opts.err)}</p>` : ''}${rows || (R ? '<p class="it-m">Счета ещё не подключены.</p>' : '')}${form(true)}
      ${links ? `<div class="it-links"><span class="it-m">Тестовые столы (только для владельца):</span>${links}</div>` : ''}</div>`;
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
    // 17.11: the real-copy switch — a plain confirm before real money; the server's answer (or its refusal) is shown as it came
    el.querySelectorAll('[data-it="copy"]').forEach((b) => b.addEventListener('click', async () => {
      if (!api.copyReal) return;
      const on = b.dataset.on === '1';
      if (!window.confirm(on ? confirmOn(b.dataset.name, b.dataset.slot) : confirmOff(b.dataset.name))) return;
      b.disabled = true;
      try { const r = await api.copyReal(b.dataset.id, on);
        if (rerender && r && r.accounts) rerender(r, r.message_ru || ''); else if (msg) msg.textContent = (r && r.message_ru) || ''; }
      catch (err) { const m = (err && err.data && err.data.message_ru) || 'Не получилось: ' + ((err && err.message) || err);
        if (rerender) rerender(null, m); else if (msg) msg.textContent = m; }
      b.disabled = false; }));
  }
  window.CNIvanTests = { html, connectHtml, mount, confirmOn, confirmOff };
})();
