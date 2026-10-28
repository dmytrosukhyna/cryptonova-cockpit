/* CRYPTO NOVΛ VIP — «Стратегии»: one card per strategy, one status set, stable groups (owner spec §9.1–9.3, §10 view).
   strategies.json (data agent) when published; until then the demo desks (paper.json) and the model spot portfolios (portfolio.json). */
(function () {
  'use strict';
  const V = window.VIP, esc = V.esc, A = V.arr;

  const ST = {
    Active: { ru: 'Активна', rank: 0 }, Approved: { ru: 'Допущена', rank: 1 }, Validation: { ru: 'Доп. проверка', rank: 2 },
    Testing: { ru: 'Тестирование', rank: 3 }, InDevelopment: { ru: 'В разработке', rank: 4 }, Paused: { ru: 'Приостановлена', rank: 5 },
    Rejected: { ru: 'Отклонена', rank: 6 }, Archived: { ru: 'Архив', rank: 7 },
  };
  const EN = { Active: 'Active', Approved: 'Approved', Validation: 'Validation', Testing: 'Testing', InDevelopment: 'In Development', Paused: 'Paused', Rejected: 'Rejected', Archived: 'Archived' };
  const normStatus = (s) => {
    const k = String(s || '').toLowerCase().replace(/[\s_-]/g, '');
    return { active: 'Active', approved: 'Approved', validation: 'Validation', testing: 'Testing', test: 'Testing', indevelopment: 'InDevelopment', development: 'InDevelopment', dev: 'InDevelopment', paused: 'Paused', rejected: 'Rejected', archived: 'Archived', archive: 'Archived' }[k] || 'Testing';
  };
  V.stRu = (s) => (ST[s] ? ST[s].ru : s);
  V.stChip = (s) => `<span class="chip st-${esc(s)}" title="${esc(EN[s] || s)}">${esc(V.stRu(s))}</span>`;
  const CAT = { spot: 'Спот', futures: 'Фьючерсы', other: 'Другое', perp: 'Фьючерсы', swap: 'Фьючерсы' };
  const catRu = (c) => CAT[String(c || '').toLowerCase()] || c || '—';
  const GROUPS = [
    { key: 'real', name: 'Реально используемые' },
    { key: 'test', name: 'Тестирование' },
    { key: 'valid', name: 'Дополнительная проверка' },
    { key: 'paused', name: 'Приостановлены' },
    { key: 'archive', name: 'Отклонены и архив' },
  ];
  const groupOf = (s) => (s.real ? 'real' : s.status === 'Paused' ? 'paused' : ['Rejected', 'Archived'].includes(s.status) ? 'archive'
    : ['Validation', 'Approved'].includes(s.status) ? 'valid' : 'test');
  const pctOf = (v) => (V.isNum(v) ? (Math.abs(+v) <= 1 ? +v * 100 : +v) : null);
  const isIvan = (x) => /ivan|иван/i.test(String(x.id || x.key || '') + ' ' + String(x.name || x.name_ru || '')) || x.owner_only === true;

  V.strategiesModel = async function () {
    const [S, P, PF, DS] = await V.feeds(['strategies.json', 'paper.json', 'portfolio.json', 'desk_select.json']);
    let items = [], src = 'strategies.json', asof = null;
    const list = S && (Array.isArray(S) ? S : S.strategies || S.items);
    if (A(list).length) {
      asof = V.asofOf(S);
      items = list.map((x) => {
        const m = x.metrics || x.stats || {};
        const it = {
          id: String(x.id || x.key || x.name), name: x.name_ru || x.name || x.id, status: normStatus(x.status), real: x.real_money === true,
          cat: catRu(x.category || x.type), desc: x.description_ru || x.about_ru || x.description || x.about || '',
          since: V.ms(x.since || x.test_since || x.started), updated: V.ms(x.updated || x.asof || m.asof),
          trades: V.n(m.trades != null ? m.trades : m.n != null ? m.n : m.closed_n), wr: pctOf(m.win_rate != null ? m.win_rate : m.winrate),
          net: V.n(m.net_pct != null ? m.net_pct : m.result_pct != null ? m.result_pct : m.pnl_pct), netUsd: V.n(m.net_usd != null ? m.net_usd : m.pnl_usd),
          dd: V.n(m.max_dd_pct), avg: V.n(m.avg_trade_pct != null ? m.avg_trade_pct : m.avg_pct), open: V.n(m.open != null ? m.open : m.open_n),
          sample: m.sample_note || x.sample_note || '', decision: x.decision_ru || x.admission_ru || x.decision || '', next: x.next_ru || x.next_stage_ru || '',
          criteria: x.criteria_ru || '', issues: A(x.issues_ru || x.issues), env: x.env_ru || x.env || '', owner: isIvan(x),
        };
        return it;
      });
    } else {
      src = 'fallback';
      asof = V.asofOf(P);
      const cur = DS && DS.current;
      const realKeys = new Set([cur, 'imp', 'imps'].filter(Boolean));
      const prof = (P && P.profiles) || {};
      const order = A(P && P.profile_list).map((p) => p.key);
      for (const k of Object.keys(prof)) if (!order.includes(k)) order.push(k);
      const about = {};
      for (const p of A(P && P.profile_list)) about[p.key] = p;
      for (const k of order) {
        const p = prof[k]; if (!p) continue;
        const s = p.stats || {};
        items.push({
          id: 'desk:' + k, key: k, name: p.name || k, status: realKeys.has(k) ? 'Active' : 'Testing', real: realKeys.has(k),
          cat: k === 'imps' ? 'Спот' : 'Фьючерсы', desc: (about[k] && about[k].about) || p.about || '', rules: p.rules || '',
          since: V.ms(p.since), updated: V.ms(p.asof), trades: V.n(s.trades), wr: pctOf(s.winrate), net: V.n(p.pnl_pct), netUsd: V.n(p.pnl_usd),
          dd: V.n(s.max_dd_pct), avg: null, open: A(p.open).length, sample: '', env: realKeys.has(k) ? 'статистика демо-стола; его сделки повторяет реальный счёт' : 'демо-счёт $10 000, виртуальные деньги',
          decision: realKeys.has(k) ? (k === cur ? 'Основной стол: за ним следует фьючерсная часть реального счёта.' : k === 'imps' ? 'За ним следует активный спот реального счёта (10%).' : 'За ним следует фьючерсная часть реального счёта вместе с основным столом.') : 'Тест на демо-счёте; к реальным деньгам не подключён.',
          next: k !== cur && DS ? (A(DS.candidates).find((c) => c.key === k) || {}).why || '' : '', err: p.error || null,
        });
      }
      const ports = (PF && PF.portfolios) || {};
      for (const [k, p] of Object.entries(ports)) {
        if (!p || !p.name) continue;
        const real = k === 'ideas';
        items.push({
          id: 'spot:' + k, key: k, name: p.name, status: real ? 'Active' : 'Testing', real, cat: 'Спот',
          desc: p.about || '', since: V.ms((p.reset && p.reset.time) || p.started), updated: V.ms(PF.asof),
          trades: null, wr: null, net: V.isNum(p.index) ? p.index - 100 : null, netUsd: null, dd: null, avg: null,
          open: A(p.positions).length, sample: '', env: real ? 'модель; её повторяет спот реального счёта' : 'модельный портфель, виртуальные деньги',
          decision: real ? 'Спот реального счёта (65%) повторяет этот портфель.' : 'Модельный портфель для сравнения; реальными деньгами не торгуется.', next: '',
        });
      }
    }
    // Ivan: a test strategy; its details are owner-private (spec §10) — never shown without CNAuth.state.isOwner
    if (!items.some((x) => x.owner)) items.push({ id: 'ivan', name: 'Ivan · внешняя платформа', status: 'Testing', real: false, cat: 'Фьючерсы', owner: true,
      desc: 'Сторонняя платформа, которая заявляет 100% win rate. Система только наблюдает за счётом в режиме чтения и считает честную статистику.',
      env: 'наблюдение, ключ только для чтения', decision: 'Тестовая: к реальному исполнению не подключается.', since: null, updated: null });
    for (const it of items) it.group = groupOf(it);
    const rank = (s) => (ST[s.status] ? ST[s.status].rank : 9);
    // stable: status → recently updated (by day, so a minute-by-minute refresh never reshuffles the list) → name
    const day = (t) => (t ? Math.floor(t / 86400000) : 0);
    items.sort((a, b) => rank(a) - rank(b) || day(b.updated) - day(a.updated) || String(a.name).localeCompare(String(b.name), 'ru'));
    const groups = GROUPS.map((g) => Object.assign({}, g, { items: items.filter((s) => s.group === g.key) }));
    return { items, groups, src, asof, DS };
  };

  /* «100% · 3 сделки» — the sample size always next to the win rate */
  V.wrText = (wr, n) => (V.isNum(wr) ? V.share(wr, 0) : '—') + (V.isNum(n) ? ` · ${V.int(n)} ${V.plural(n, 'сделка', 'сделки', 'сделок')}` : '');
  V.wrHtml = (wr, n) => (V.isNum(wr) ? V.share(wr, 0) : '—') + (V.isNum(n) ? ` <small>· ${V.int(n)} ${V.plural(n, 'сделка', 'сделки', 'сделок')}</small>` : '');

  let IVAN = { t: 0, d: null, err: null };
  async function ivanData() {
    if (!V.auth.owner()) return null;
    if (Date.now() - IVAN.t < 5 * 60000 && (IVAN.d || IVAN.err)) return IVAN;
    try { IVAN = { t: Date.now(), d: await V.ownerData('ivan'), err: null }; }
    catch (e) { IVAN = { t: Date.now(), d: null, err: e && e.message ? e.message : 'ошибка' }; }
    return IVAN;
  }
  function ivanMetrics(it, I) {
    const d = I && I.d, s = (d && d.stats) || {};
    if (!d) return;
    it.trades = V.n(s.n); it.wr = pctOf(s.winrate); it.netUsd = V.n(s.pnl_sum); it.dd = V.isNum(d.max_dd) ? +d.max_dd * 100 : null;
    it.open = A(d.positions).length; it.since = V.ms(d.since || s.first_trade); it.updated = V.ms(d.asof);
    it.sample = `Win rate считается только по закрытым сделкам (${V.int(s.wins)} в плюсе, ${V.int(s.losses)} в минусе из ${V.int(s.n)}). Убыточные позиции, которые держат открытыми и усредняют, в win rate не попадают — смотрите просадку и открытые позиции.`;
  }

  function card(it) {
    const locked = it.owner && !V.auth.owner();
    const m = (lbl, v, cls = '') => `<div><small>${esc(lbl)}</small><span class="${cls}">${v}</span></div>`;
    return `<button class="scard ${it.real ? 'realm' : ''} ${locked ? 'lockcard' : ''}" type="button" data-sid="${esc(it.id)}">
      <div class="scard-h"><b>${esc(it.name)}</b><span class="chips">${V.stChip(it.status)}${it.real ? V.chip('реальные деньги', 'real') : V.chip(it.owner ? 'тест · владелец' : 'тест', it.owner ? 'owner' : 'test')}</span></div>
      <div class="f">${esc(it.cat)}${it.since ? ' · с ' + esc(V.date(it.since)) : ''}${it.env ? ' · ' + esc(it.env) : ''}</div>
      ${locked ? `<div class="d">${V.ico('lock')} Статистика доступна владельцу после входа.</div>` : `
      <div class="m">${m('Win rate', V.wrHtml(it.wr, it.trades))}${m('Результат', it.net != null ? V.pct(it.net) : it.netUsd != null ? V.smoney(it.netUsd) : '—', V.cls(it.net != null ? it.net : it.netUsd))}${m('Макс. просадка', it.dd != null ? V.pct(-Math.abs(it.dd)) : '—')}</div>
      ${it.desc ? `<div class="d">${esc(it.desc)}</div>` : ''}`}
    </button>`;
  }

  V.views.strategies = {
    async html() {
      const SM = await V.strategiesModel();
      const I = await ivanData();
      for (const it of SM.items) if (it.owner && V.auth.owner()) ivanMetrics(it, I);
      V.ui.strat = SM;
      const DS = SM.DS;
      return `<div class="ph"><div><h2>Стратегии</h2><p>Все стратегии с едиными статусами. «Реальные деньги» — только те, за которыми следует реальный счёт; остальное — тесты.</p></div>
          <span class="sp"></span>${SM.asof ? V.stamp(SM.asof, 30) : ''}</div>
        ${SM.src === 'fallback' ? `<div class="card flat" style="margin-bottom:var(--gap)"><p class="note" style="margin:0">Сейчас список собран из демо-столов (paper.json) и модельных портфелей (portfolio.json): всё, к чему не подключены реальные деньги, — «Тестирование». Единые статусы со всеми стадиями появятся, когда будет опубликован strategies.json.</p></div>` : ''}
        ${SM.groups.map((g) => `<section class="sgroup" data-demo="st-${g.key}"><h3>${esc(g.name)} <span class="n">${V.int(g.items.length)}</span></h3>
          ${g.items.length ? `<div class="scards">${g.items.map(card).join('')}</div>` : V.empty('Нет стратегий в этой группе.')}</section>`).join('')}
        ${DS ? selCard(DS) : ''}`;
    },
    mount(el) {
      el.querySelectorAll('[data-sid]').forEach((b) => b.addEventListener('click', () => openStrat(b.dataset.sid)));
    },
  };

  function selCard(DS) {
    return `<section class="card" data-demo="st-select"><div class="card-h"><h3>Как выбирается основной стол</h3><span class="sp"></span>${V.stamp(DS.asof, 24 * 60)}</div>
      <p class="para">${esc(DS.why_ru || '')}</p>
      <div class="tbl-wrap"><table class="tbl"><thead><tr><th>Стол</th><th>Сделок</th><th>Результат</th><th class="hide-sm">Просадка</th><th class="hide-sm">Результат / просадка</th><th class="l">Решение</th></tr></thead><tbody>
        ${A(DS.candidates).map((c) => `<tr><td class="l"><span class="sym">${esc(c.name)}</span></td><td>${V.int(c.trades)}</td><td><span class="${V.cls(c.result_pct)}">${V.pct(c.result_pct)}</span></td>
          <td class="hide-sm">${V.pct(-Math.abs(c.dd_pct || 0))}</td><td class="hide-sm">${V.num(c.ratio, 2)}</td><td class="wrap">${c.key === DS.current ? V.chip('основной', 'real') + ' ' : ''}${esc(c.why || '')}</td></tr>`).join('')}
      </tbody></table></div>
      <p class="note">Стол меняется не чаще раза в 7 дней и только по доказательствам: нужно не меньше 20 закрытых сделок и отношение результата к просадке на 20% лучше текущего. Высокий win rate на 2–3 сделках не даёт допуска.</p></section>`;
  }

  async function openStrat(id) {
    const SM = V.ui.strat || (await V.strategiesModel());
    const it = SM.items.find((x) => x.id === id); if (!it) return;
    const locked = it.owner && !V.auth.owner();
    if (locked) {
      V.sheet(it.name, `<p class="para">${esc(it.desc)}</p>${V.lockBox('Только для владельца', V.auth.has() ? 'Войдите своим аккаунтом владельца — отдельный пароль для этой стратегии больше не нужен.' : 'Модуль входа ещё подключается; до этого статистика Ivan не показывается.')}`);
      return;
    }
    let extra = '';
    if (it.owner) {
      const I = await ivanData();
      if (I && I.err) extra = V.nodata('данные Ivan не загрузились: ' + I.err);
      else if (I && I.d) {
        const d = I.d, s = d.stats || {};
        extra = `<div class="sec-t"><h3>Счёт</h3>${V.stamp(d.asof, 60)}</div>
          <div class="kpis">${V.kpi('Капитал', V.money(d.equity), '')}${V.kpi('Нереализовано', `<span class="${V.cls(d.upnl)}">${V.smoney(d.upnl)}</span>`, `${A(d.positions).length} ${V.plural(A(d.positions).length, 'открытая', 'открытые', 'открытых')}`)}
          ${V.kpi('Закрыто', V.int(s.n), `в плюсе ${V.int(s.wins)} · в минусе ${V.int(s.losses)}`)}${V.kpi('Худшая открытая', V.isNum(d.worst_open) ? V.pct(d.worst_open * 100) : '—', 'от капитала')}</div>
          ${A(d.closed).length ? `<div class="sec-t"><h3>Закрытые сделки</h3></div><div class="tbl-wrap"><table class="tbl"><thead><tr><th>Закрыта</th><th class="l">Актив</th><th>Результат</th></tr></thead><tbody>
            ${d.closed.slice(0, 30).map((c) => `<tr><td>${esc(V.dt(c.close_time))}</td><td class="l">${esc(c.symbol || '—')} ${esc(c.side || '')}</td><td><span class="${V.cls(c.pnl)}">${V.smoney(c.pnl)}</span></td></tr>`).join('')}</tbody></table></div>` : ''}`;
      }
    }
    V.sheet(it.name, `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">${V.stChip(it.status)}${it.real ? V.chip('реальные деньги', 'real') : V.chip('без реальных денег', 'test')}${V.chip(it.cat, 'test')}</div>
      ${it.desc ? `<p class="para">${esc(it.desc)}</p>` : ''}
      <div class="kpis" style="margin-top:12px">${V.kpi('Сделок', V.int(it.trades), it.open != null ? `открыто ${V.int(it.open)}` : '')}${V.kpi('Win rate', V.isNum(it.wr) ? V.share(it.wr, 0) : '—', V.isNum(it.trades) ? `по ${V.int(it.trades)} ${V.plural(it.trades, 'закрытой сделке', 'закрытым сделкам', 'закрытым сделкам')}` : '')}
        ${V.kpi('Результат', it.net != null ? `<span class="${V.cls(it.net)}">${V.pct(it.net)}</span>` : `<span class="${V.cls(it.netUsd)}">${V.smoney(it.netUsd)}</span>`, it.net != null && it.netUsd != null ? V.smoney(it.netUsd) : '')}
        ${V.kpi('Макс. просадка', it.dd != null ? V.pct(-Math.abs(it.dd)) : '—', '')}</div>
      ${it.sample ? `<p class="note">${esc(it.sample)}</p>` : V.isNum(it.trades) && it.trades < 20 ? `<p class="note">Выборка маленькая (${V.int(it.trades)} ${V.plural(it.trades, 'сделка', 'сделки', 'сделок')}): win rate ещё ничего не доказывает.</p>` : ''}
      <div class="kv" style="margin-top:12px"><span>Среда</span><b>${esc(it.env || '—')}</b><span>Тест с</span><b>${it.since ? esc(V.date(it.since)) : '—'}</b>
        <span>Обновлено</span><b>${it.updated ? esc(V.dt(it.updated)) : '—'}</b></div>
      ${it.decision ? `<div class="sec-t"><h3>Допуск к реальной работе</h3></div><p class="para">${esc(it.decision)}</p>` : ''}
      ${it.criteria ? `<div class="sec-t"><h3>Критерии завершения проверки</h3></div><p class="para">${esc(it.criteria)}</p>` : ''}
      ${it.issues && it.issues.length ? `<div class="sec-t"><h3>Обнаруженные проблемы</h3></div><ul class="bul">${it.issues.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
      ${it.next ? `<div class="sec-t"><h3>Следующий этап</h3></div><p class="para">${esc(it.next)}</p>` : ''}
      ${it.rules ? `<details class="more"><summary>Правила</summary><p class="para">${esc(it.rules)}</p></details>` : ''}
      ${it.err ? `<p class="note">Последняя ошибка стола: ${esc(String(it.err).slice(0, 160))}</p>` : ''}
      ${extra}`);
  }
})();
