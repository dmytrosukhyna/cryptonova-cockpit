/* CRYPTO NOVΛ VIP — «Киты» (04.11.2026, owner: «Будем отслеживать топов»): a watchlist in two parts — crypto (every whale the
   whale study follows or studies: web/whale_watch.json) and the stock market (SEC 13F of 10 funds, ARK daily trades, Congress:
   web/stock_whales.json). Plus «Куда инвестируют киты» for the «Спот» section (web/smart_money.json, V.smartMoneyCard).
   Information only — every value comes from a feed; a missing one is «нет данных». */
(function () {
  'use strict';
  const V = window.VIP, esc = V.esc, A = V.arr;
  const ui = (V.ui.whales = V.ui.whales || { tab: 'crypto', st: 'all', src: 'all', all: false });
  const ST_CHIP = { followed: 'real', candidate: 'test', dropped: 'warn' };
  const SRC_NAME = { hl: 'Hyperliquid', okx: 'OKX', bn: 'Binance' };

  const usdK = (v) => {
    if (!V.isNum(v)) return '—';
    v = +v; const a = Math.abs(v), s = v < 0 ? '−' : '';
    if (a >= 1e9) return s + '$' + V.num(a / 1e9, 1) + ' млрд';
    if (a >= 1e6) return s + '$' + V.num(a / 1e6, 1) + ' млн';
    if (a >= 1e3) return s + '$' + V.num(a / 1e3, 0) + 'k';
    return s + '$' + V.num(a, 0);
  };
  const susdK = (v) => (V.isNum(v) ? (+v > 0 ? '+' : '') + usdK(v) : '—');
  V.usdK = usdK;
  const hold = (h) => (!V.isNum(h) ? '—' : +h < 1 ? V.int(h * 60) + ' мин' : +h < 48 ? V.num(h, 0) + ' ч' : V.num(h / 24, 0) + ' дн');
  const winTxt = (w) => (V.isNum(w) ? V.share(w * 100, 0) : '—');
  const pfTxt = (pf) => (!V.isNum(pf) ? '—' : +pf >= 99 ? 'без убытков' : V.num(pf, 2));
  const side = (s) => (String(s).toUpperCase() === 'LONG' ? 'лонг' : String(s).toUpperCase() === 'SHORT' ? 'шорт' : esc(s || '—'));

  V.views.whales = {
    async html(arg) {
      if (!V.auth.owner()) return `<div class="card">${V.nodata('раздел доступен только владельцу')}</div>`;   // 05.11: owner-only
      if (arg === 'stocks' || arg === 'crypto') ui.tab = arg;
      const [W, S] = await Promise.all([V.sealed('whale_watch'), V.feed('stock_whales.json')]);
      const nc = W ? A(W.whales).length : null, ns = S ? A(S.funds).length : null;
      return `<div class="ph"><div><h2>Киты</h2><p>Следим за топами: криптотрейдеры, которых изучает и копирует система, и крупнейшие фонды фондового рынка по официальным отчётам. Только информация, не финансовый совет.</p></div></div>
        <div class="seg wh-tabs" role="tablist" style="margin-bottom:var(--gap)">
          <button type="button" role="tab" data-wtab="crypto" aria-pressed="${ui.tab === 'crypto'}">Крипто${nc != null ? ' · ' + V.int(nc) : ''}</button>
          <button type="button" role="tab" data-wtab="stocks" aria-pressed="${ui.tab === 'stocks'}">Фондовый рынок${ns != null ? ' · ' + V.int(ns) : ''}</button>
        </div>
        ${ui.tab === 'stocks' ? stocks(S) : crypto(W)}`;
    },
    mount(el) {
      el.querySelectorAll('[data-wtab]').forEach((b) => b.addEventListener('click', () => { ui.tab = b.dataset.wtab; V.render({ keepScroll: true }); }));
      el.querySelectorAll('[data-wst]').forEach((b) => b.addEventListener('click', () => { ui.st = b.dataset.wst; ui.all = false; V.render({ keepScroll: true }); }));
      el.querySelectorAll('[data-wsrc]').forEach((b) => b.addEventListener('click', () => { ui.src = b.dataset.wsrc; ui.all = false; V.render({ keepScroll: true }); }));
      el.querySelectorAll('[data-wall]').forEach((b) => b.addEventListener('click', () => { ui.all = true; V.render({ keepScroll: true }); }));
      el.querySelectorAll('[data-whale]').forEach((b) => b.addEventListener('click', () => openWhale(b.dataset.whale)));
    },
  };

  /* ── crypto ─────────────────────────────────────────────────────────── */
  function crypto(W) {
    if (!W) return `<section class="card">${V.nodata('список временно недоступен — он появится после следующего прогона или когда сервер снова ответит')}</section>`;
    // 04.11 22:35 (owner): traders in a minus, without decisions or far from the bar are not candidates — folded «не прошли» below
    const every = A(W.whales), all = every.filter((w) => w.status !== 'failed'), failed = every.filter((w) => w.status === 'failed');
    const c = W.counts || {}, bs = W.by_src || {}, dk = W.desk;
    const list = all.filter((w) => (ui.st === 'all' || w.status === ui.st) && (ui.src === 'all' || w.src === ui.src));
    const shown = ui.all ? list : list.slice(0, 24);
    const seg = (attr, cur, opts) => `<div class="seg">${opts.map(([k, n]) => `<button type="button" data-${attr}="${k}" aria-pressed="${cur === k}">${esc(n)}</button>`).join('')}</div>`;
    return `<section class="card lead">
        <div class="card-h"><span class="eyebrow">Киты · крипто</span>${V.chip('демо-копирование', 'test')}<span class="sp"></span>${V.stamp(W.asof, 120)}</div>
        <div class="kpis k5">
          ${V.kpi('Копируем', V.int(c.followed), 'стол «Киты» повторяет их сделки')}
          ${V.kpi('Кандидаты', V.int(c.candidate), 'изучаем, в копирование не взяли')}
          ${V.kpi('Исключены', V.int(c.dropped), 'сняли с копирования')}
          ${V.kpi('Источники', Object.keys(bs).map((k) => `${esc(SRC_NAME[k] || k)} ${V.int(bs[k])}`).join(' · ') || '—', '', 'wrapv')}
          ${dk ? V.kpi('Стол «Киты» (демо)', `<span class="${V.cls(dk.pnl_usd)}">${V.smoney(dk.pnl_usd)}</span>`, `${V.pct(dk.pnl_pct)} · сделок ${V.int(dk.trades)}${V.isNum(dk.winrate) ? ' · в плюсе ' + winTxt(dk.winrate) : ''}`) : V.kpi('Стол «Киты»', '—', 'нет данных')}
        </div>
        <details class="more"><summary>Как выбираем, кого копировать</summary>
          <p class="para">${esc(W.rule_ru || '')}</p><p class="note">${esc(W.about_ru || '')}</p>
          ${W.not_covered_ru ? `<p class="note">${esc(W.not_covered_ru)}</p>` : ''}</details>
      </section>
      <div class="filters wh-filters" style="margin-top:var(--gap)">
        ${seg('wst', ui.st, [['all', `Все ${all.length}`], ['followed', `Копируем ${c.followed || 0}`], ['candidate', `Кандидаты ${c.candidate || 0}`], ['dropped', `Исключены ${c.dropped || 0}`]])}
        ${seg('wsrc', ui.src, [['all', 'Все источники'], ...Object.keys(bs).map((k) => [k, SRC_NAME[k] || k])])}
      </div>
      ${shown.length ? `<div class="scards wh-cards">${shown.map(card).join('')}</div>` : V.empty('Под этот фильтр китов нет.')}
      ${!ui.all && list.length > shown.length ? `<div style="margin-top:12px"><button class="btn" type="button" data-wall="1">Показать всех (${V.int(list.length)})</button></div>` : ''}
      ${A(W.bench).length ? `<details class="more" style="margin-top:12px"><summary>Скамейка — следующие за копируемыми (${V.int(W.bench.length)})</summary><div class="tbl-wrap"><table class="tbl"><tbody>
        ${W.bench.map((b) => `<tr><td><span class="sym">${esc(b.name)}<small>${esc(b.src_ru || b.src)}</small></span></td><td>${V.isNum(b.win) ? winTxt(b.win) : '—'} · PF ${V.isNum(b.pf) ? esc(String(b.pf)) : '—'}</td><td class="l">${esc(b.why_ru || '')}</td></tr>`).join('')}
      </tbody></table></div></details>` : ''}
      ${failed.length ? `<details class="more" style="margin-top:12px"><summary>Не прошли отбор (${V.int(failed.length)})</summary><div class="tbl-wrap"><table class="tbl"><tbody>
        ${failed.map((w) => `<tr><td><span class="sym">${esc(w.name)}<small>${esc(w.src_ru || w.src)}</small></span></td><td class="${V.cls(w.pnl_60d)}">${V.isNum(w.pnl_60d) ? V.smoney(w.pnl_60d) : '—'}</td><td class="l">${esc(A(w.why_ru).join('; ') || '—')}</td></tr>`).join('')}
      </tbody></table></div></details>` : ''}`;
  }

  function card(w) {
    const pos = A(w.positions), cp = w.copies || {};
    const upnl = pos.reduce((s, p) => s + (V.n(p.upnl) || 0), 0);
    return `<button class="scard wh-card ${w.status === 'followed' ? 'realm' : ''}" type="button" data-whale="${esc(w.addr)}">
      <div class="scard-h"><b class="wh-name">${esc(w.name)}</b><span class="chips">${V.chip(w.src_ru || w.src, 'test')}${V.chip(w.status_ru, ST_CHIP[w.status] || 'test')}</span></div>
      ${w.not_studied ? '<div class="d muted">В этом прогоне не изучался — показываем причину снятия.</div>' : `<div class="m wh-m">
        <div><small>В плюсе</small><span>${winTxt(w.win)}</span></div>
        <div><small>PF</small><span>${pfTxt(w.pf)}</span></div>
        <div><small>Решений, 60 дн</small><span>${V.int(w.decisions)}</span></div>
        <div><small>Держит</small><span>${hold(w.hold_med_h)}</span></div>
        <div><small>Макс. просадка</small><span>${V.isNum(w.max_dd_usd) ? usdK(-w.max_dd_usd) : '—'}</span></div>
        <div><small>P&amp;L 60 дн</small><span class="${V.cls(w.pnl_60d)}">${susdK(w.pnl_60d)}</span></div></div>`}
      <div class="f">${pos.length ? `Открыто ${V.int(pos.length)}${w.positions_hidden ? ` (скрыто ${V.int(w.positions_hidden)})` : ''} · <span class="${V.cls(upnl)}">${susdK(upnl)}</span>` : 'Открытых позиций нет'}
        · наши копии: ${cp.trades ? `${V.int(cp.trades)} сд. <span class="${V.cls(cp.pnl_usd)}">${V.smoney(cp.pnl_usd)}</span>` : ''}${cp.trades && A(cp.open).length ? ' + ' : ''}${A(cp.open).length ? `${A(cp.open).length} откр. <span class="${V.cls(cp.open_upnl)}">${V.smoney(cp.open_upnl)}</span>` : ''}${!cp.trades && !A(cp.open).length ? 'нет' : ''}</div>
      ${w.status !== 'followed' && A(w.why_ru).length ? `<div class="d">${esc(A(w.why_ru).join('; '))}</div>` : ''}
    </button>`;
  }

  async function openWhale(addr) {
    const W = await V.sealed('whale_watch');
    const w = A(W && W.whales).find((x) => x.addr === addr);
    if (!w) return;
    const pos = A(w.positions), rec = A(w.recent), cp = w.copies || {};
    const posT = pos.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Монета</th><th class="l">Сторона</th><th>Вход</th><th class="hide-sm">Плечо</th><th>P&amp;L</th></tr></thead><tbody>
        ${pos.map((p) => `<tr><td><span class="sym">${p.hidden ? '<span class="muted">скрыта</span>' : esc(p.coin)}</span></td><td class="l">${side(p.side)}</td><td>${p.hidden || !+p.entry ? '—' : V.price(p.entry)}</td><td class="hide-sm">${V.isNum(p.lev) ? V.num(p.lev, 0) + '×' : '—'}</td><td class="${V.cls(p.upnl)}">${V.smoney(p.upnl, 0)}</td></tr>`).join('')}</tbody></table></div>` : V.empty('Открытых позиций нет.');
    const recT = rec.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Монета</th><th class="l">Сторона</th><th class="hide-sm">Открыта</th><th>Держал</th><th>Ход цены</th><th>P&amp;L</th></tr></thead><tbody>
        ${rec.map((t) => `<tr><td><span class="sym">${esc(t.coin)}${t.open ? '<small>ещё открыта</small>' : ''}</span></td><td class="l">${side(t.side)}</td><td class="hide-sm">${esc(V.dt(t.open_t))}</td><td>${hold(t.hold_h)}</td><td class="${V.cls(t.ret_pct)}">${V.pct(t.ret_pct)}</td><td class="${V.cls(t.pnl)}">${susdK(t.pnl)}</td></tr>`).join('')}</tbody></table></div>` : V.empty('Сделок за 60 дней не видно.');
    const cps = A(cp.open).map((o) => ({ ...o, isOpen: true })).concat(A(cp.closed));
    const cpT = cps.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Монета</th><th class="l">Сторона</th><th>Вход</th><th class="hide-sm">Выход</th><th>Результат</th></tr></thead><tbody>
        ${cps.map((o) => `<tr><td><span class="sym">${esc(o.coin)}<small>${o.isOpen ? 'открыта ' + esc(V.dt(o.opened_t)) : esc(V.dt(o.closed_t)) + (o.why ? ' · ' + esc(o.why) : '')}</small></span></td><td class="l">${side(o.side)}</td><td>${V.price(o.entry)}</td><td class="hide-sm">${o.isOpen ? V.price(o.price) : V.price(o.exit)}</td><td class="${V.cls(o.isOpen ? o.upnl : o.pnl)}">${V.smoney(o.isOpen ? o.upnl : o.pnl)}</td></tr>`).join('')}</tbody></table></div>` : V.empty('Стол «Киты» этого кита не копировал.');
    V.sheet(w.name, `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">${V.chip(w.src_long_ru || w.src, 'test')}${V.chip(w.status_ru, ST_CHIP[w.status] || 'test')}${w.rank ? V.chip('место ' + w.rank + ' в отборе', 'test') : ''}</div>
      <div class="kv"><span>Адрес / код</span><b class="wh-addr">${esc(w.addr)}</b>
        ${w.link ? `<span>Профиль</span><b><a href="${esc(w.link)}" target="_blank" rel="noopener">открыть ${V.ico('ext')}</a></b>` : ''}
        ${V.isNum(w.followers) ? `<span>Копируют на бирже</span><b>${V.int(w.followers)}</b>` : ''}
        ${V.isNum(w.account) && +w.account > 0 ? `<span>Размер счёта</span><b>${usdK(w.account)}</b>` : ''}</div>
      <h4 class="wh-h4">Статус</h4><ul class="bul">${A(w.why_ru).map((x) => `<li>${esc(x)}</li>`).join('') || '<li>—</li>'}</ul>
      ${w.not_studied ? '' : `<h4 class="wh-h4">60 дней (с открытыми позициями)</h4><div class="kpis k3">
        ${V.kpi('В плюсе', winTxt(w.win), V.isNum(w.win_closed) ? 'по закрытым ' + winTxt(w.win_closed) : '')}
        ${V.kpi('PF', pfTxt(w.pf), V.isNum(w.pf_closed) ? 'по закрытым ' + pfTxt(w.pf_closed) : 'прибыль / убыток')}
        ${V.kpi('Решений', V.int(w.decisions), esc(w.style_ru || ''))}
        ${V.kpi('Держит (медиана)', hold(w.hold_med_h), V.isNum(w.lev_now) ? 'плечо ~' + V.num(w.lev_now, 0) + '×' : '')}
        ${V.kpi('Макс. просадка', V.isNum(w.max_dd_usd) ? usdK(-w.max_dd_usd) : '—', V.isNum(w.max_dd_all_usd) ? 'за всю историю ' + usdK(-w.max_dd_all_usd) : 'по сделкам за 60 дней')}
        ${V.kpi('P&L 60 дн', `<span class="${V.cls(w.pnl_60d)}">${susdK(w.pnl_60d)}</span>`, '30 дн ' + susdK(w.pnl_30d))}</div>`}
      ${w.desc_ru ? `<details class="more"><summary>Как торгует</summary><p class="para">${esc(w.desc_ru)}</p></details>` : ''}
      <h4 class="wh-h4">Открытые позиции</h4>${posT}
      <h4 class="wh-h4">Последние 10 сделок</h4>${recT}
      <h4 class="wh-h4">Наши копии</h4>
      <p class="para small">${cp.trades ? `Закрыто ${V.int(cp.trades)}, в плюсе ${V.int(cp.wins)}, итог <b class="${V.cls(cp.pnl_usd)}">${V.smoney(cp.pnl_usd)}</b>` : 'Закрытых копий нет'}${A(cp.open).length ? ` · открыто ${A(cp.open).length}, сейчас <b class="${V.cls(cp.open_upnl)}">${V.smoney(cp.open_upnl)}</b>` : ''}. Демо-счёт стола «Киты».</p>
      ${cpT}
      <p class="note">Прошлые результаты — не обещание. Сервер видит сделки китов с опозданием; киты могут хеджироваться на других биржах.</p>`);
  }

  /* ── stocks ─────────────────────────────────────────────────────────── */
  const tk = (r) => `<span class="sym">${esc(r.ticker || shortName(r.name))}${r.ticker ? `<small>${esc(r.name)}</small>` : r.cls && !/^COM/i.test(r.cls) ? `<small>${esc(r.cls)}</small>` : ''}</span>`;
  const shortName = (n) => String(n || '—').replace(/\s+(INC|CORP|CORPORATION|CO|LTD|PLC|HLDGS|HOLDINGS|GROUP)\.?$/i, '');
  const ACT = { new: 'новая', add: 'добавил', cut: 'сократил', exit: 'вышел' };

  function stocks(S) {
    if (!S) return `<section class="card">${V.nodata('stock_whales.json ещё не опубликован — данные SEC появятся после первого прогона')}</section>`;
    const src = S.sources || {};
    const srcLine = Object.values(src).map((s) => `${esc(s.name_ru)}: ${s.ok ? 'работает' : '<span class="neg">источник недоступен</span>'}`).join(' · ');
    return `<section class="card lead">
        <div class="card-h"><span class="eyebrow">Киты фондового рынка</span>${V.chip('задержка до 45 дней', 'warn')}<span class="sp"></span>${V.stamp(S.asof, 36 * 60)}</div>
        <p class="para">${esc(S.delay_ru || '')}</p>
        <details class="more"><summary>Что значат «новая», «добавил», «консенсус»</summary><p class="para">${esc(S.about_ru || '')}</p></details>
        <p class="note">${srcLine}</p>
      </section>
      ${A(S.consensus).map(consCard).join('') || `<section class="card" style="margin-top:var(--gap)"><div class="card-h"><h3>Консенсус</h3></div>${V.empty('Пока нет квартала, за который отчитались хотя бы 2 фонда.')}</section>`}
      <div class="sec-t"><h3>Фонды</h3><span class="sub">последний отчёт 13F каждого фонда</span></div>
      <div class="grid g2">${A(S.funds).map(fundCard).join('')}</div>
      ${arkCard(S.ark, src.ark)}
      ${congressCard(S.congress)}`;
  }

  function consCard(c) {
    const items = A(c.items);
    const row = (x) => `<tr><td>${tk(x)}</td><td>${V.int(x.n)}</td><td class="wrap">${A(x.funds).map((f) => `${esc(f.name)} <span class="muted">(${ACT[f.action] || f.action}${V.isNum(f.chg_pct) ? ' ' + V.pct(f.chg_pct, 0) : ''})</span>`).join(', ')}</td><td class="hide-sm">${usdK(x.usd)}</td></tr>`;
    return `<section class="card" style="margin-top:var(--gap)">
      <div class="card-h"><h3>Консенсус · ${esc(c.period_ru)}</h3><span class="sub">что купили 2 фонда и больше · отчитались ${V.int(c.n_funds)} из 10</span></div>
      ${items.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Акция</th><th>Фондов</th><th class="l">Кто и как</th><th class="hide-sm">Куплено на</th></tr></thead>
        <tbody>${items.slice(0, 8).map(row).join('')}</tbody></table></div>
        ${items.length > 8 ? `<details class="more"><summary>Ещё ${V.int(items.length - 8)}</summary><div class="tbl-wrap"><table class="tbl"><tbody>${items.slice(8).map(row).join('')}</tbody></table></div></details>` : ''}`
        : V.empty('В этом квартале нет акций, которые купили 2 фонда и больше.')}
      <p class="note">Фонды в расчёте: ${esc(A(c.funds).join(', '))}.</p></section>`;
  }

  function fundCard(f) {
    if (!f.period) return `<section class="card"><div class="card-h"><h3>${esc(f.name_ru)}</h3><span class="sub">${esc(f.who_ru)}</span></div>${V.empty('<b>Источник недоступен</b>' + (f.error_ru ? ' — ' + esc(f.error_ru.replace(/^источник недоступен:?\s*/, '')) : ''))}</section>`;
    const mv = f.moves || {}, mn = f.moves_n || {}, top = A(f.top);
    const list = (k) => A(mv[k]).length ? `<div class="wh-mv"><b>${ACT[k][0].toUpperCase() + ACT[k].slice(1)} · ${V.int(mn[k])}</b><ul class="bul">${A(mv[k]).map((r) => `<li>${esc(r.ticker || shortName(r.name))} <span class="muted">${k === 'exit' ? usdK(r.prev_value) : k === 'new' ? usdK(r.value) : V.pct(r.chg_pct, 0) + ' · ' + susdK(r.usd)}</span></li>`).join('')}</ul></div>` : '';
    return `<section class="card wh-fund">
      <div class="card-h"><h3>${esc(f.name_ru)}</h3><span class="sub">${esc(f.who_ru)}</span><span class="sp"></span>${f.error_ru ? V.chip('обновить не удалось', 'warn') : ''}${f.stale_ru ? V.chip('старый отчёт', 'warn') : ''}</div>
      ${f.stale_ru ? `<p class="note neg" style="margin:-4px 0 8px">${esc(f.stale_ru)}</p>` : ''}
      <div class="kpis k3">${V.kpi('Отчёт за', esc(f.period_ru), 'подан ' + esc(V.date(f.filed)))}${V.kpi('Портфель 13F', usdK(f.total_value), V.int(f.positions) + ' ' + V.plural(f.positions, 'позиция', 'позиции', 'позиций'))}
        ${V.kpi('За квартал', `${V.int(mn.new || 0)} / ${V.int(mn.add || 0)} / ${V.int(mn.cut || 0)} / ${V.int(mn.exit || 0)}`, f.prev_period ? 'новые / добавил / сократил / вышел' : 'нет прошлого отчёта', 'wrapv')}</div>
      <div class="tbl-wrap" style="margin-top:10px"><table class="tbl"><thead><tr><th>Крупнейшие</th><th>Доля</th><th class="hide-sm">Сумма</th><th>За квартал</th></tr></thead><tbody>
        ${top.slice(0, 5).map((r) => holdRow(r)).join('')}</tbody></table></div>
      ${top.length > 5 ? `<details class="more"><summary>Ещё ${V.int(top.length - 5)} крупнейших</summary><div class="tbl-wrap"><table class="tbl"><tbody>${top.slice(5).map((r) => holdRow(r)).join('')}</tbody></table></div></details>` : ''}
      ${f.prev_period ? `<details class="more"><summary>Изменения против ${esc(f.prev_period_ru)}</summary><div class="wh-mvs">${['new', 'add', 'cut', 'exit'].map(list).join('') || V.empty('Изменений нет.')}</div></details>` : ''}
      <p class="note"><a href="${esc(f.filing_link || f.link)}" target="_blank" rel="noopener">Отчёт в SEC EDGAR ${V.ico('ext')}</a>${f.options ? ` · опционных строк: ${V.int(f.options)} (не считаем)` : ''}</p>
    </section>`;
  }
  const holdRow = (r) => `<tr><td>${tk(r)}</td><td>${V.share(r.pct)}</td><td class="hide-sm">${usdK(r.value)}</td><td class="${V.cls(r.chg_pct)}">${r.is_new ? V.chip('новая', 'real') : V.isNum(r.chg_pct) ? V.pct(r.chg_pct, 0) : '—'}</td></tr>`;

  function arkCard(ark, s) {
    const mv = A(ark && ark.moves);
    const ACTA = { buy: 'купила', sell: 'продала', new: 'новая', exit: 'вышла' };
    const row = (m) => `<tr><td><span class="sym">${esc(m.ticker || m.company)}<small>${esc(m.fund)}</small></span></td><td class="l">${ACTA[m.action] || m.action}</td><td>${V.isNum(m.chg_pct) ? V.pct(m.chg_pct) : '—'}</td><td class="${V.cls(m.usd)}">${susdK(m.usd)}</td></tr>`;
    return `<section class="card" style="margin-top:var(--gap)">
      <div class="card-h"><h3>Кэти Вуд · ARK Invest</h3><span class="sub">сделки за день по файлам фондов</span><span class="sp"></span>${ark && ark.date ? V.chip('файл от ' + V.date(ark.date), 'test') : ''}</div>
      ${s && s.ok === false && !A(ark && ark.funds).length ? V.empty('<b>Источник недоступен</b> — файлы ARK не скачались.')
        : !A(ark && ark.funds).length ? V.empty('Нет данных.')
        : mv.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Акция</th><th class="l">Что сделала</th><th>Доля позиции</th><th>Сумма</th></tr></thead><tbody>${mv.slice(0, 8).map(row).join('')}</tbody></table></div>
          ${mv.length > 8 ? `<details class="more"><summary>Ещё ${V.int(mv.length - 8)}</summary><div class="tbl-wrap"><table class="tbl"><tbody>${mv.slice(8).map(row).join('')}</tbody></table></div></details>` : ''}`
        : V.empty(A(ark.funds).some((f) => f.prev_date) ? 'За последний день заметных сделок нет.' : 'Сравнение появится со следующим файлом ARK (нужны 2 дня).')}
      <p class="note">Изменение числа акций за день минус общий приток/отток денег фонда (медианное изменение всех позиций). Фонды: ${esc(A(ark && ark.funds).map((f) => f.fund).join(', ') || '—')}.</p></section>`;
  }

  function congressCard(cg) {
    if (!cg || !cg.available) return `<section class="card" style="margin-top:var(--gap)"><div class="card-h"><h3>Конгресс США</h3>${V.chip('источник недоступен', 'warn')}</div>
      <p class="para small">${esc((cg && cg.note_ru) || 'Бесплатный источник сделок Конгресса США не отвечает — блок пропущен.')}</p></section>`;
    const top = A(cg.top_buys), rec = A(cg.recent);
    return `<section class="card" style="margin-top:var(--gap)"><div class="card-h"><h3>Конгресс США</h3><span class="sub">раскрытые сделки за 45 дней</span></div>
      ${top.length ? `<p class="para small">Чаще всего покупали: ${top.slice(0, 10).map((b) => `<b>${esc(b.ticker)}</b> <span class="muted">(${V.int(b.who.length)})</span>`).join(', ')}</p>` : ''}
      <details class="more"><summary>Последние сделки (${V.int(rec.length)})</summary><div class="tbl-wrap"><table class="tbl"><tbody>${rec.map((r) => `<tr><td><span class="sym">${esc(r.ticker)}<small>${esc(r.who)}</small></span></td><td class="l">${esc(r.type)}</td><td class="hide-sm">${esc(r.amount)}</td><td>${esc(r.disclosed)}</td></tr>`).join('')}</tbody></table></div></details></section>`;
  }

  /* ── «Куда инвестируют киты» (for «Спот») ───────────────────────────── */
  V.smartMoneyCard = async function () {
    const D = await V.feed('smart_money.json');
    const head = `<div class="card-h"><h3>Куда инвестируют киты</h3>${V.chip('не финансовый совет, высокий риск', 'warn')}<span class="sp"></span>${D ? V.stamp(D.asof, 180) : ''}</div>`;
    if (!D) return `<section class="card" style="margin-top:var(--gap)" id="smart-money">${head}${V.nodata('smart_money.json ещё не опубликован')}</section>`;
    const sig = A(D.signals), hl = D.hl || {}, acc = A(hl.accum), fl = D.flows || {}, tr = D.track || {};
    const srcBad = Object.values(D.sources || {}).filter((s) => s.ok === false);
    return `<section class="card" style="margin-top:var(--gap)" id="smart-money">${head}
      <p class="para">Токены, которые за 7 дней набрали 2 и больше «умных» кошелька: фонды, маркетмейкеры, известные трейдеры и топ-трейдеры Hyperliquid. Так иногда находят ранние проекты — но многие такие токены потом падают.</p>
      <details class="more"><summary>Правило сигнала и риски</summary><p class="para">${esc(D.rule_ru || '')}</p><p class="para">${esc(D.risk_ru || '')}</p></details>
      ${srcBad.length ? `<p class="note neg">Источник недоступен: ${srcBad.map((s) => esc(s.name_ru)).join(', ')}.</p>` : ''}
      <div class="sec-t"><h3>Сигналы</h3><span class="sub">${V.int(sig.length)} в блокчейнах · ${V.int(acc.length)} на споте Hyperliquid</span></div>
      ${sig.length || acc.length ? `<div class="scards">${sig.map(tokCard).join('')}${acc.map(hlCard).join('')}</div>`
        : V.empty('За 7 дней ни один токен не набрал 2 и больше умных кошельков.' + (D.partial ? ' Прогон был неполный.' : ''))}
      ${A(D.singles).length ? `<details class="more"><summary>Одиночные крупные покупки (${V.int(D.singles.length)}) — для наблюдения</summary><div class="scards" style="margin-top:8px">${D.singles.map(tokCard).join('')}</div></details>` : ''}
      ${A(D.mm_only).length ? `<details class="more"><summary>Набрали только маркетмейкеры (${V.int(D.mm_only.length)})</summary><p class="note">Маркетмейкеры держат запас токенов для работы — это слабый сигнал.</p><div class="scards" style="margin-top:8px">${D.mm_only.map(tokCard).join('')}</div></details>` : ''}
      ${A(hl.held).length ? `<details class="more"><summary>Что держат топ-трейдеры на споте Hyperliquid (${V.int(hl.held.length)})</summary>${hlHeld(hl.held)}</details>` : ''}
      ${watchBlock(D.watch)}
      ${patternsBlock(D.watch)}
      <div class="sec-t"><h3>Биржевые кошельки</h3><span class="sub">приток (+) и отток (−) монет у известных кошельков бирж</span></div>
      ${flowTbl(fl)}
      <div class="sec-t"><h3>Работает ли это</h3><span class="sub">цена через 1, 7 и 30 дней после первого сигнала</span></div>
      ${trackBlock(tr)}
      <details class="more"><summary>За кем следим (${V.int(D.wallets_n)}) и источники</summary>${walletsBlock(D)}</details>
    </section>`;
  };

  function tokCard(g) {
    const b = A(g.buyers), fl = A(g.flags);
    return `<div class="scard wh-tok">
      <div class="scard-h"><b>${esc(g.symbol)}</b><span class="chips">${V.chip(g.chain_ru || g.chain, 'test')}${g.early ? V.chip('ранний', 'real') : ''}${V.chip(V.int(g.n) + ' ' + V.plural(g.n, 'кошелёк', 'кошелька', 'кошельков'), 'test')}</span></div>
      <div class="m wh-m">
        <div><small>Возраст</small><span>${V.isNum(g.age_days) ? V.int(g.age_days) + ' дн' : '—'}</span></div>
        <div><small>Ликвидность</small><span>${usdK(g.liq)}</span></div>
        <div><small>${V.isNum(g.mcap) ? 'Капитализация' : 'FDV'}</small><span>${usdK(V.isNum(g.mcap) ? g.mcap : g.fdv)}</span></div>
        <div><small>С 1-й покупки</small><span class="${V.cls(g.chg_since_pct)}">${V.pct(g.chg_since_pct)}</span></div>
        <div><small>Куплено</small><span>${usdK(g.usd)}</span></div>
        <div><small>Цена</small><span>${V.price(g.price)}</span></div></div>
      <div class="d wh-buyers">${b.slice(0, 3).map((x) => `${esc(x.label)} <span class="muted">${usdK(x.usd)}${x.from_exchange ? ', с биржи' : ''}</span>`).join(' · ')}${b.length > 3 ? ` <span class="muted">и ещё ${b.length - 3}</span>` : ''}</div>
      ${fl.length ? `<div class="chips">${fl.map((f) => V.chip(f, 'warn')).join('')}</div>` : `<div class="chips">${V.chip('явных рисков не найдено', 'test')}</div>`}
      <div class="f">${g.url ? `<a href="${esc(g.url)}" target="_blank" rel="noopener">график</a> · ` : ''}<a href="${esc(g.explorer || '#')}" target="_blank" rel="noopener">контракт</a>${g.security && V.isNum(g.security.top10_pct) ? ' · топ-10 держат ' + V.share(g.security.top10_pct, 0) : ''}</div>
    </div>`;
  }

  function hlCard(a) {
    const b = A(a.buyers);
    return `<div class="scard wh-tok"><div class="scard-h"><b>${esc(a.coin)}</b><span class="chips">${V.chip('Hyperliquid спот', 'test')}${V.chip(V.int(a.n) + ' ' + V.plural(a.n, 'трейдер', 'трейдера', 'трейдеров'), 'test')}</span></div>
      <div class="m wh-m"><div><small>Куплено</small><span>${usdK(a.usd)}</span></div><div><small>Цена</small><span>${V.price(a.price)}</span></div><div><small>Новых позиций</small><span>${V.int(b.filter((x) => x.new).length)}</span></div></div>
      <div class="d wh-buyers">${b.slice(0, 3).map((x) => `${esc(x.label)} <span class="muted">${usdK(x.usd)}${V.isNum(x.chg_pct) ? ', от входа ' + V.pct(x.chg_pct) : ''}</span>`).join(' · ')}</div></div>`;
  }

  function hlHeld(held) {
    return `<div class="tbl-wrap" style="margin-top:8px"><table class="tbl"><thead><tr><th>Монета</th><th>Трейдеров</th><th>Сумма</th><th class="hide-sm">Цена</th></tr></thead><tbody>
      ${held.map((h) => `<tr><td><span class="sym">${esc(h.coin)}<small>${esc(A(h.holders).slice(0, 3).map((x) => x.label).join(', '))}</small></span></td><td>${V.int(h.n)}</td><td>${usdK(h.usd)}</td><td class="hide-sm">${V.price(h.price)}</td></tr>`).join('')}</tbody></table></div>`;
  }

  function flowTbl(fl) {
    const rows = ['BTC', 'ETH', 'SOL'].filter((k) => fl[k]).map((k) => {
      const f = fl[k];
      if (f.error) return `<tr><td><span class="sym">${k}</span></td><td class="l" colspan="3"><span class="muted">${esc(f.error)}</span></td></tr>`;
      const part = (n) => (V.isNum(n) && n < f.wallets ? `<small class="muted"> по ${V.int(n)} из ${V.int(f.wallets)}</small>` : '');
      const cell = (v, u) => (V.isNum(v) ? `<span class="${V.cls(-v)}">${(+v > 0 ? '+' : '') + V.num(v, k === 'BTC' ? 1 : 0)} ${k}</span>${V.isNum(u) ? `<small class="muted"> ${susdK(u)}</small>` : ''}` : '<span class="muted">история копится</span>');
      return `<tr><td><span class="sym">${k}<small>${V.int(f.wallets)} ${V.plural(f.wallets, 'кошелёк', 'кошелька', 'кошельков')}</small></span></td><td class="hide-sm">${V.num(f.total, 0)} ${k}</td><td>${cell(f.d1, f.d1_usd)}${part(f.d1_wallets)}</td><td>${cell(f.d7, f.d7_usd)}${part(f.d7_wallets)}</td></tr>`;
    });
    return rows.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Монета</th><th class="hide-sm">Сейчас на кошельках</th><th>За 24 ч</th><th>За 7 дн</th></tr></thead><tbody>${rows.join('')}</tbody></table></div>
      <p class="note">Плюс — монеты пришли на биржу (часто к продаже), минус — ушли с биржи (часто в долгое хранение). Это несколько крупных известных кошельков, а не весь запас бирж.</p>` : V.empty('Нет данных.');
  }

  function trackBlock(tr) {
    const s = tr.stats || {};
    const k = (h) => { const x = s[h] || {}; return V.kpi(`Через ${h} ${V.plural(+h, 'день', 'дня', 'дней')}`, x.n ? `<span class="${V.cls(x.avg)}">${V.pct(x.avg)}</span>` : '—', x.n ? `${V.int(x.n)} сигн. · в плюсе ${winTxt(x.win)} · медиана ${V.pct(x.median)}` : 'история копится'); };
    const rec = A(tr.recent);
    return `<div class="kpis k3">${k('1')}${k('7')}${k('30')}</div>
      ${rec.length ? `<details class="more"><summary>Сигналы в журнале (${V.int(tr.signals_total)})</summary><div class="tbl-wrap"><table class="tbl"><thead><tr><th>Токен</th><th class="hide-sm">Дата</th><th>1 д</th><th>7 д</th><th>30 д</th></tr></thead><tbody>
        ${rec.map((r) => `<tr><td><span class="sym">${esc(r.symbol)}<small>${esc(r.chain || r.src || '')}</small></span></td><td class="hide-sm">${esc(V.date(r.t))}</td>${['1', '7', '30'].map((h) => `<td class="${V.cls((r.ret || {})[h])}">${V.pct((r.ret || {})[h])}</td>`).join('')}</tr>`).join('')}</tbody></table></div></details>` : ''}
      <p class="note">Каждый сигнал записывается в журнал в день появления; через 1, 7 и 30 дней записываем цену. Так через месяц будет видно, стоит ли следовать за китами.</p>`;
  }

  /* ── «Список токенов китов»: every token a tracked wallet bought, re-checked hourly (04.11 22:38) ── */
  const WST = { 'накопление': 'real', 'риски снялись': 'real', 'продают': 'warn', 'новая': 'test', 'под наблюдением': 'test', 'выбыла': 'test' };
  function watchBlock(W) {
    if (!W) return '';
    const toks = A(W.tokens), c = W.counts || {};
    const st = ['all', ...Object.keys(c).filter((k) => c[k])];
    const row = (t) => `<details class="wl-row" data-st="${esc(t.status)}"${t.status === 'выбыла' ? ' hidden' : ''}>
      <summary><span class="wl-sym"><b>${esc(t.symbol)}</b><small>${esc(t.chain)} · с ${esc(V.dshort(t.first_t))}</small></span>
        <span class="wl-st">${V.chip(t.status, WST[t.status] || 'test')}</span>
        <span class="wl-n" title="умных кошельков держат">${V.int(t.n)}${t.n_all > t.n ? `<small>/${V.int(t.n_all)}</small>` : ''}</span>
        <span class="wl-liq hide-xs">${t.liq != null ? usdK(t.liq) : '—'}</span>
        <span class="wl-chg ${V.cls(t.chg_pct)}">${V.pct(t.chg_pct)}</span>
        <span class="wl-fl">${A(t.flags).length ? V.chip(A(t.flags).length + ' ' + V.plural(A(t.flags).length, 'риск', 'риска', 'рисков'), 'warn') : V.chip('без рисков', 'test')}</span></summary>
      <div class="wl-body">
        ${A(t.flags).length ? `<div class="chips">${t.flags.map((f) => V.chip(f, 'warn')).join('')}</div>` : ''}
        <div class="kv" style="margin-top:8px"><span>Цена сейчас / при 1-й покупке</span><b>${V.price(t.price)} / ${V.price(t.first_price)}</b>
          <span>Капитализация / FDV</span><b>${usdK(t.mcap)} / ${usdK(t.fdv)}</b>
          <span>Возраст пула · ликвидность</span><b>${V.isNum(t.age_days) ? V.int(t.age_days) + ' дн' : '—'} · ${usdK(t.liq)}</b>
          ${V.isNum(t.top10_pct) ? `<span>Топ-10 держателей</span><b>${V.share(t.top10_pct, 0)}</b>` : ''}
          <span>С 1-й покупки: 1 / 7 / 30 дн</span><b>${['1', '7', '30'].map((h) => `<span class="${V.cls((t.ret || {})[h])}">${V.pct((t.ret || {})[h])}</span>`).join(' / ')}</b></div>
        <p class="para small" style="margin-top:8px">${A(t.buyers).map((b) => `${esc(b.label)}${b.auto ? ' <span class="muted">(найден авт.)</span>' : ''} <span class="muted">${b.usd != null ? usdK(b.usd) : ''}${b.sold ? ' · продал' : ''}</span>`).join(' · ')}</p>
        ${A(t.events).length ? `<ul class="bul wl-ev">${t.events.map((e) => `<li><span class="muted">${esc(V.dt(e.t))}</span> ${esc(e.text)}</li>`).join('')}</ul>` : ''}
        <p class="note">${t.url ? `<a href="${esc(t.url)}" target="_blank" rel="noopener">график</a> · ` : ''}<a href="${esc((t.chain === 'eth' ? 'https://etherscan.io' : t.chain === 'base' ? 'https://basescan.org' : 'https://arbiscan.io') + '/token/' + t.address)}" target="_blank" rel="noopener">контракт</a>${t.first_price_src ? ' · цена 1-й покупки: ' + esc(t.first_price_src) : ''}</p>
      </div></details>`;
    return `<div class="sec-t"><h3>Список токенов китов</h3><span class="sub">${V.int(W.n)} ${V.plural(W.n, 'токен', 'токена', 'токенов')} · всё, что купили умные кошельки, проверяем каждый час</span></div>
      <details class="more" style="margin-top:0;border-top:0;padding-top:0"><summary>Как устроен список</summary><p class="para">${esc(W.about_ru || '')}</p></details>
      <div class="seg wl-seg" style="margin:8px 0">${st.map((k) => `<button type="button" data-wlst="${esc(k)}" aria-pressed="${k === 'all'}">${k === 'all' ? 'Активные' : esc(k) + ' ' + V.int(c[k])}</button>`).join('')}</div>
      ${toks.length ? `<div class="wl-head"><span>Токен</span><span>Статус</span><span>Китов</span><span class="hide-xs">Ликв.</span><span>С 1-й пок.</span><span>Риски</span></div><div class="wl">${toks.map(row).join('')}</div>`
        : V.empty('Пока пусто: умные кошельки за 7 дней ничего не покупали.')}`;
  }
  function patternsBlock(W) {
    const P = A(W && W.patterns);
    const head = `<div class="sec-t"><h3>Что срабатывало</h3><span class="sub">доля токенов в плюсе и медиана от цены 1-й покупки, по шаблонам</span></div>`;
    if (!P.length) return head + V.empty('Данных пока нет: первые результаты через 1, 7 и 30 дней после первых покупок. Шаблоны: число умных кошельков, кошелёк, риски, возраст, ликвидность.');
    const cell = (x) => (x ? `<span class="${V.cls(x.median)}">${V.pct(x.median)}</span><small class="muted"> в плюсе ${winTxt(x.hit)} · n=${V.int(x.n)}${x.small ? ' · мало данных' : ''}</small>` : '<span class="muted">—</span>');
    const kinds = [...new Set(P.map((p) => p.kind))];
    return head + kinds.map((k, i) => {
      const rows = P.filter((p) => p.kind === k);
      const tbl = `<div class="tbl-wrap"><table class="tbl pat"><thead><tr><th class="l">${esc(k)}</th><th class="hide-sm">1 дн</th><th>7 дн</th><th class="hide-sm">30 дн</th></tr></thead><tbody>
        ${rows.map((p) => `<tr><td class="l">${esc(p.name)}</td><td class="hide-sm">${cell(p.h['1'])}</td><td>${cell(p.h['7'])}</td><td class="hide-sm">${cell(p.h['30'])}</td></tr>`).join('')}</tbody></table></div>`;
      return i === 0 ? tbl : `<details class="more"><summary>${esc(k)}</summary>${tbl}</details>`;
    }).join('') + `<p class="note">«мало» — меньше 10 токенов: выводы делать рано. Медиана — изменение цены от первой покупки умного кошелька; процент — доля токенов в плюсе.</p>`;
  }
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-wlst]'); if (!b) return;
    const k = b.dataset.wlst, box = b.closest('section');
    box.querySelectorAll('[data-wlst]').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    box.querySelectorAll('.wl-row').forEach((r) => { r.hidden = k === 'all' ? r.dataset.st === 'выбыла' : r.dataset.st !== k; });
  });

  function walletsBlock(D) {
    const ws = A(D.wallets);
    const cfg = ws.filter((w) => !w.auto && !w.auto_found), auto = ws.filter((w) => w.auto), found = ws.filter((w) => w.auto_found);
    const cand = A(D.watch && D.watch.wallets_auto);
    const row = (w) => `<li><b>${esc(w.label)}</b> <span class="muted">· ${esc(w.kind)}${w.confirmed === true ? ' · метка подтверждена' : w.confirmed === false ? ' · метка по источнику (в Blockscout её нет)' : ''}</span>
      ${w.source ? ` · <a href="${esc(w.source)}" target="_blank" rel="noopener">источник</a>` : ''}<br><span class="xs muted">${A(w.chains).map((c) => `${esc(c.chain)}: ${c.error ? esc(c.error) : 'видно ' + V.num(c.coverage_h, 0) + ' ч'}`).join(' · ')}</span></li>`;
    const src = Object.values(D.sources || {});
    return `<ul class="bul">${cfg.map(row).join('')}</ul>
      ${auto.length ? `<p class="para small" style="margin-top:8px">Топ-трейдеры Hyperliquid (${V.int(auto.length)}), смотрим их кошельки в Arbitrum и Ethereum:</p><ul class="bul">${auto.map(row).join('')}</ul>` : ''}
      <p class="para small" style="margin-top:8px"><b>Найдены автоматически</b> — кошельки, которые покупали токены раньше наших китов перед ростом на 50%+ (2 таких токена и больше). Это не публичные метки, а наблюдение системы.</p>
      ${found.length ? `<ul class="bul">${found.map((w) => row(w).replace('</span>', ` · ${esc(A(w.found_tokens).join(', '))}</span>`)).join('')}</ul>`
        : `<p class="note">Пока нет${cand.length ? ` — кандидатов с 1 токеном: ${V.int(cand.filter((c) => c.status !== 'кандидат').length)}` : ''}.</p>`}
      <p class="para small" style="margin-top:8px">Источники: ${src.map((s) => `${esc(s.name_ru)} — <span class="${s.ok === false ? 'neg' : ''}">${esc(s.status_ru)}</span>`).join('; ')}.</p>`;
  }
})();
