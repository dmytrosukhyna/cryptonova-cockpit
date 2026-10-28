/* CRYPTO NOVΛ VIP — «Фьючерсы»: the real account's futures part only (owner spec §8.1–8.4). Demo desk trades never appear here. */
(function () {
  'use strict';
  const V = window.VIP, esc = V.esc, A = V.arr;
  const ui = (V.ui.fut = V.ui.fut || { per: 'all', from: '', to: '', sym: '', strat: '', res: '' });
  const symOf = (inst) => String(inst || '').split('-')[0] || '—';
  const deskOf = (sig, names) => { if (!sig) return null; const k = String(sig).split(':')[0]; return names[k] || k; };

  V.views.futures = {
    async html() {
      const [M, names, DS, H] = await Promise.all([V.real(), V.deskNames(), V.feed('desk_select.json'), V.feed('app_home.json')]);
      if (!M) return `<div class="ph"><div><h2>Фьючерсы</h2></div></div><div class="card">${V.nodata('public_real.json не загрузился')}</div>`;
      return `<div class="ph"><div><h2>Фьючерсы</h2><p>Реальные фьючерсные позиции счёта CRYPTO NOVA на OKX с ${V.PUBLIC_START_LABEL}. Тестовые сделки демо-столов здесь не показываются.</p></div></div>
        ${summary(M)}
        <div class="grid g-main" style="margin-top:var(--gap)">${openCard(M, names)}${stateCard(M, names, DS, H)}</div>
        ${closedCard(M, names)}`;
    },
    mount(el) {
      el.querySelectorAll('[data-fper]').forEach((b) => b.addEventListener('click', () => { if (!b.disabled) { ui.per = b.dataset.fper; V.render({ keepScroll: true }); } }));
      el.querySelectorAll('[data-ff]').forEach((i) => i.addEventListener('change', () => { ui[i.dataset.ff] = i.value; V.render({ keepScroll: true }); }));
      const r = el.querySelector('[data-freset]'); if (r) r.addEventListener('click', () => { Object.assign(ui, { from: '', to: '', sym: '', strat: '', res: '' }); V.render({ keepScroll: true }); });
    },
  };

  function summary(M) {
    const a = M.alloc || {}, F = M.futPart || {};
    const budget = V.n(a.fut_budget_usd), margin = V.n(a.fut_margin_usd), reserve = V.n(a.fut_reserve_usd);
    const P = F.periods || {};
    const names = { day: 'Сегодня', week: '7 дней', month: '30 дней', all: 'Всё' };
    const keyOf = { today: 'day', '7d': 'week', '30d': 'month', all: 'all' };
    let per = keyOf[ui.per] || 'all';
    if (P[per] && P[per].available === false) per = 'all';
    const pp = P[per] || null;
    const total = pp && pp.available !== false ? pp.usd : M.futPnl;
    const totalPct = pp && pp.available !== false ? pp.pct : F.pct;
    return `<section class="card lead" data-demo="fut-summary">
      <div class="card-h"><span class="eyebrow">Сводка фьючерсов</span>${V.chip('реальный счёт OKX', 'real')}<span class="sp"></span>
        ${Object.keys(P).length ? `<div class="seg" role="group" aria-label="Период">${Object.entries(keyOf).map(([u, k]) => `<button type="button" data-fper="${u}" aria-pressed="${k === per}" ${P[k] && P[k].available === false ? 'disabled title="За этот период данных нет"' : ''}>${names[k]}</button>`).join('')}</div>` : ''}
        ${V.stamp(M.asof, 60)}</div>
      <div class="kpis">
        ${V.kpi('Открытых позиций', V.int(M.futures.length), M.futures.length ? '' : 'сейчас нет', '', 'big')}
        ${V.kpi('Итог за период', `<span class="${V.cls(total)}">${V.smoney(total)}</span>`, `${V.isNum(totalPct) ? `<span class="${V.cls(totalPct)}">${V.pct(totalPct)}</span> от базы фьючерсов` : ''}${pp && pp.clipped ? ' · с ' + V.PUBLIC_START_LABEL : ''}`)}
        ${V.kpi('Реализовано', `<span class="${V.cls(M.realizedFut)}">${V.smoney(M.realizedFut)}</span>`, `${V.int(M.closed.length)} ${V.plural(M.closed.length, 'закрытая', 'закрытые', 'закрытых')} · комиссии и фандинг учтены`)}
        ${V.kpi('Нереализовано', `<span class="${V.cls(M.openUpnl)}">${V.smoney(M.openUpnl)}</span>`, 'открытые позиции')}
        ${V.kpi('Бюджет фьючерсов', V.money(budget), V.isNum(a.fut_pct) ? V.share(a.fut_pct, 0) + ' счёта' : '')}
        ${V.kpi('Загрузка капитала', budget ? V.share(((margin || 0) / budget) * 100) : '—', 'маржа ' + V.money(margin))}
        ${V.kpi('Доступно', V.money(reserve), 'резерв для новых сделок')}
        ${V.kpi('Риск на сделку', '≤ 1%', 'бюджета фьючерсов · плечо ≤ 2')}
      </div>
      <p class="note">${esc(F.base_note || '')} Правила автопилота: только LONG, стоп на бирже у каждой позиции, риск не больше 1% фьючерсного бюджета на сделку, плечо не больше 2. Стратегию и риск интерфейс не меняет.</p>
    </section>`;
  }

  function openCard(M, names) {
    const rows = M.futures;
    return `<section class="card" data-demo="fut-open">
      <div class="card-h"><h3>Открытые позиции</h3><span class="sub">данные биржи OKX</span></div>
      ${rows.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Актив</th><th>Сторона</th><th>Вход</th><th>Марк.</th><th class="hide-sm">Размер</th><th class="hide-sm">Плечо</th><th>Нереализовано</th><th>SL</th><th class="hide-sm">TP</th><th class="hide-md">Открыта</th><th class="l hide-md">Стратегия</th><th class="l hide-md">Статус</th></tr></thead><tbody>
        ${rows.map((f) => {
          const size = V.isNum(f.notional_usd) ? V.money(f.notional_usd) : V.isNum(f.sz) ? V.qty(f.sz) : V.isNum(f.size) ? V.qty(f.size) : '—';
          return `<tr><td><span class="sym">${esc(symOf(f.inst))}<small>${esc(f.inst || '')}</small></span></td>
            <td>${esc(String(f.side || '—').toUpperCase())}</td><td>${V.price(f.entry)}</td><td>${V.price(f.mark)}</td>
            <td class="hide-sm">${size}</td><td class="hide-sm">${V.isNum(f.lev) ? '×' + V.num(f.lev, f.lev % 1 ? 1 : 0) : '—'}</td>
            <td><span class="${V.cls(f.upnl_usd)}">${V.smoney(f.upnl_usd)}</span><br><span class="${V.cls(f.upnl_pct)} small">${V.pct(f.upnl_pct)}</span></td>
            <td>${f.stop ? V.price(f.stop) : V.chip('нет стопа', 'warn')}</td><td class="hide-sm">${f.target ? V.price(f.target) : '—'}</td>
            <td class="hide-md">${esc(V.dt(f.opened))}</td><td class="l hide-md">${esc(deskOf(f.sig, names) || f.strategy || '—')}</td>
            <td class="l hide-md">${V.chip('открыта', 'real')}</td></tr>`;
        }).join('')}</tbody></table></div>
        <p class="note">Нереализовано в % — движение цены в сторону сделки. Размер позиции в фиде ${rows.some((f) => V.isNum(f.notional_usd) || V.isNum(f.sz) || V.isNum(f.size)) ? 'есть' : 'пока не публикуется — «—»'}.</p>`
        : V.empty('<b>Открытых позиций нет.</b> Реальный счёт открывает фьючерсную сделку только вслед за сигналом стола, за которым он следует.')}
    </section>`;
  }

  function stateCard(M, names, DS, H) {
    const F = M.futPart || {}, a = M.alloc || {};
    const lastFut = M.actions.find((x) => /^fut_/.test(x.kind));
    let st, why;
    if (M.stale) { st = 'Нет свежих данных'; why = 'Зеркало давно не обновляло счёт — состояние неизвестно.'; }
    else if (M.futures.length) { st = 'Управляет открытыми позициями'; why = `${M.futures.length} ${V.plural(M.futures.length, 'позиция', 'позиции', 'позиций')} со стопом на бирже.`; }
    else if (F.running === false) { st = 'Не открывает новые позиции'; why = 'Фьючерсная часть автопилота выключена.'; }
    else { st = 'Ждёт сигнала'; why = 'Открытых позиций нет: счёт входит только по сигналу стола, за которым следует.'; }
    const demoStatus = H && H.futures && H.futures.status_ru;
    return `<section class="card" data-demo="fut-state">
      <div class="card-h"><h3>Что делает система</h3></div>
      <div style="display:flex;align-items:center;gap:9px"><span class="dot ${M.stale ? 'stale' : 'ok'}" style="width:10px;height:10px"></span><b style="font-size:16px">${esc(st)}</b></div>
      <p class="para">${esc(why)}</p>
      <div class="kv" style="margin-top:10px">
        <span>Основной стол</span><b>${DS && DS.name ? esc(DS.name) : '—'}</b>
        <span>Выбран</span><b>${DS && DS.since ? esc(V.date(DS.since)) + (DS.by === 'owner' ? ' · владельцем' : '') : '—'}</b>
        <span>Последнее действие</span><b>${lastFut ? esc(V.dt(lastFut.t)) + ' · ' + esc(V.actRu(lastFut.kind)) + ' ' + esc(symOf(lastFut.sym)) : 'нет'}</b>
        <span>В позициях</span><b>${V.money(a.fut_margin_usd)}</b>
      </div>
      ${DS && DS.why_ru ? `<p class="note">${esc(DS.why_ru)}</p>` : ''}
      ${demoStatus ? `<details class="more"><summary>Состояние стола, за которым следует счёт (демо)</summary><p class="para">${esc(demoStatus)}</p></details>` : ''}
    </section>`;
  }

  function closedCard(M, names) {
    const all = M.closed.map((c) => Object.assign({}, c, { sym: symOf(c.inst), desk: deskOf(c.sig, names) || c.strategy || null }));
    const syms = [...new Set(all.map((c) => c.sym))].sort();
    const desks = [...new Set(all.map((c) => c.desk).filter(Boolean))].sort();
    const d0 = ui.from ? Date.parse(ui.from + 'T00:00:00') : null, d1 = ui.to ? Date.parse(ui.to + 'T23:59:59') : null;
    const rows = all.filter((c) => (!d0 || V.ms(c.closed) >= d0) && (!d1 || V.ms(c.closed) <= d1) && (!ui.sym || c.sym === ui.sym)
      && (!ui.strat || c.desk === ui.strat) && (!ui.res || (ui.res === 'win' ? c.pnl_usd > 0 : c.pnl_usd < 0)));
    const tot = rows.reduce((s, c) => s + (V.isNum(c.pnl_usd) ? +c.pnl_usd : 0), 0);
    const wins = rows.filter((c) => c.pnl_usd > 0).length;
    return `<section class="card" style="margin-top:var(--gap)" data-demo="fut-closed">
      <div class="card-h"><h3>Закрытые сделки</h3><span class="sub">${V.int(all.length)} с ${V.PUBLIC_START_LABEL}</span></div>
      <div class="filters">
        <label>С даты<input class="inp" type="date" data-ff="from" value="${esc(ui.from)}"></label>
        <label>По дату<input class="inp" type="date" data-ff="to" value="${esc(ui.to)}"></label>
        <label>Актив<select class="inp" data-ff="sym"><option value="">все</option>${syms.map((s) => `<option ${s === ui.sym ? 'selected' : ''}>${esc(s)}</option>`).join('')}</select></label>
        <label>Стратегия<select class="inp" data-ff="strat"><option value="">все</option>${desks.map((s) => `<option ${s === ui.strat ? 'selected' : ''}>${esc(s)}</option>`).join('')}</select></label>
        <label>Результат<select class="inp" data-ff="res"><option value="">любой</option><option value="win" ${ui.res === 'win' ? 'selected' : ''}>в плюсе</option><option value="loss" ${ui.res === 'loss' ? 'selected' : ''}>в минусе</option></select></label>
        ${ui.from || ui.to || ui.sym || ui.strat || ui.res ? '<button class="btn sm ghost" type="button" data-freset="1" style="align-self:flex-end">Сбросить</button>' : ''}
      </div>
      ${rows.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Открыта</th><th>Закрыта</th><th class="l">Актив</th><th class="hide-sm">Сторона</th><th class="l hide-md">Стратегия</th><th>Результат</th><th class="hide-sm">Движение цены</th><th class="l hide-md">Причина закрытия</th></tr></thead><tbody>
        ${rows.map((c) => `<tr><td>${esc(V.dt(c.opened))}</td><td>${esc(V.dt(c.closed))}</td><td class="l"><span class="sym">${esc(c.sym)}</span></td>
          <td class="hide-sm">${esc(String(c.side || '—').toUpperCase())}${V.isNum(c.lev) ? ' ×' + V.num(c.lev, c.lev % 1 ? 1 : 0) : ''}</td>
          <td class="l hide-md">${esc(c.desk || '—')}</td>
          <td><span class="${V.cls(c.pnl_usd)}">${V.smoney(c.pnl_usd)}</span></td><td class="hide-sm"><span class="${V.cls(c.move_pct)}">${V.pct(c.move_pct)}</span></td>
          <td class="l hide-md">${esc(c.reason_ru || c.close_reason || (c.partial ? 'частичное закрытие' : '—'))}</td></tr>`).join('')}</tbody>
        <tfoot><tr><td colspan="3">Показано ${V.int(rows.length)} · в плюсе ${V.int(wins)} из ${V.int(rows.length)}</td><td class="hide-sm"></td><td class="hide-md"></td><td><span class="${V.cls(tot)}">${V.smoney(tot)}</span></td><td class="hide-sm"></td><td class="hide-md"></td></tr></tfoot></table></div>`
        : V.empty(all.length ? 'Под фильтр не попала ни одна сделка.' : 'Закрытых сделок на реальном счёте пока нет.')}
      <p class="note">Результат — realizedPnl OKX (комиссии и фандинг учтены). ${all.some((c) => c.desk) ? '' : 'Стратегия у закрытых сделок в фиде пока не записывается — «—».'} Win rate по ${V.int(all.length)} ${V.plural(all.length, 'сделке', 'сделкам', 'сделкам')} — слишком маленькая выборка для выводов.</p>
    </section>`;
  }
})();
