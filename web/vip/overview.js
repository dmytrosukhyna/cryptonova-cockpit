/* CRYPTO NOVΛ VIP — shared models (one source per number) + «Обзор». Owner spec §5, §6, §15. */
(function () {
  'use strict';
  const V = window.VIP, esc = V.esc, A = V.arr, N = V.n;
  const sum = (xs) => xs.reduce((s, x) => s + (V.isNum(x) ? +x : 0), 0);

  /* ── the public portfolio: the REAL CRYPTO NOVA account on OKX since 22.10.2026 (public_real.json; app_home only adds
        its own split / periods / action texts). Never mixed with demo desks or the owner's personal account. ── */
  V.real = async function () {
    const [R, H] = await V.feeds(['public_real.json', 'app_home.json']);
    const PP = H && H.public_portfolio && H.public_portfolio.kind === 'real' ? H.public_portfolio : null;
    if (!R && !PP) return null;
    const g = (k) => (R && R[k] != null ? R[k] : PP ? PP[k] : null);
    const since = V.ms(g('since')), asof = V.ms(g('asof'));
    const futures = A(R && R.futures);
    const closed = A(R && R.closed).length ? R.closed : A(PP && PP.futures && PP.futures.closed);
    const texts = {};
    for (const a of A(PP && PP.actions)) texts[a.t + ':' + a.sym] = a.text;
    const actions = A(R && R.actions).map((a) => Object.assign({}, a, { text: a.text || texts[a.t + ':' + a.sym] || null }));
    const alloc = (R && R.alloc) || {};
    const has = (k) => V.isNum(alloc[k]);
    const used = has('spot_coins_usd') ? sum([alloc.spot_coins_usd, alloc.active_coins_usd, alloc.fut_margin_usd]) : null;
    const free = V.isNum(g('usdt_usd')) ? +g('usdt_usd') : has('spot_cash_usd') ? sum([alloc.spot_cash_usd, alloc.active_wait_usd, alloc.fut_reserve_usd]) : null;
    const realizedFut = R && V.isNum(R.fut_realized_usd) ? +R.fut_realized_usd : closed.length ? sum(closed.map((c) => c.pnl_usd)) : 0;
    const openUpnl = sum(futures.map((f) => f.upnl_usd));
    const age = V.ageMin(asof);
    const startOk = since ? V.date(since) === V.PUBLIC_START_LABEL : false;
    return {
      R, PP, since, asof, startOk,
      stale: !!g('stale') || (age != null && age > 60),
      start: N(g('start_usd')), equity: N(R ? R.equity_usd : PP && PP.value_usd), pnl: N(g('pnl_usd')), pnlPct: N(g('pnl_pct')),
      today: N(g('today_usd')), todayPct: N(g('today_pct')),
      spot: A(R && R.spot), futures, closed, actions, alloc, used, free,
      realized: R && V.isNum(R.realized_usd) ? +R.realized_usd : null,        // when the feed gives a full split
      unrealized: R && V.isNum(R.unrealized_usd) ? +R.unrealized_usd : null,
      realizedFut, openUpnl,
      spotPnl: N(R && R.spot_pnl_usd), futPnl: N(R && R.fut_pnl_usd),
      curve: A(R && R.curve).length ? R.curve : A(PP && PP.curve), futCurve: A(R && R.fut_curve),
      flows: (R && R.flows) || (PP && PP.flows) || null,
      periods: PP && PP.periods, spotPart: PP && PP.spot, futPart: PP && PP.futures,
      copiers: g('copiers'), copyUrl: /^https:\/\/www\.okx\.com\/copy-trading\/account\/[A-Za-z0-9]+$/.test(g('copy_url') || '') ? g('copy_url') : null,
      note: PP && PP.note, closedN: N(R && R.closed_n),
    };
  };
  V.realStartWarn = (M) => (M && M.since && !M.startOk
    ? `<span class="chip warn" title="Публичный портфель ведётся с ${V.PUBLIC_START_LABEL}">дата начала в данных: ${esc(V.date(M.since))}</span>` : '');

  /* demo desk names (paper.json) — for «sig» = «<desk>:<id>» */
  V.deskNames = async function () {
    const P = await V.feed('paper.json');
    const m = {};
    for (const p of A(P && P.profile_list)) m[p.key] = p.name;
    for (const [k, p] of Object.entries((P && P.profiles) || {})) if (!m[k] && p && p.name) m[k] = p.name;
    return m;
  };

  const ACT_RU = {
    spot_buy: 'Покупка', spot_sell: 'Продажа', active_buy: 'Активный спот · покупка', active_sell: 'Активный спот · продажа',
    fut_open: 'Фьючерс · открыт', fut_close: 'Фьючерс · закрыт', fut_reduce: 'Фьючерс · частично закрыт', fut_gone: 'Фьючерс · закрыт биржей (стоп / цель)',
  };
  V.actRu = (k) => ACT_RU[k] || k || '—';
  V.actionLi = (a) => `<div class="li"><span class="t">${esc(V.dt(a.t))}</span>
      <span class="d"><b>${esc(V.actRu(a.kind))} · ${esc(a.sym || '—')}</b>${a.side ? ' · ' + esc(String(a.side).toUpperCase()) : ''}</span>
      <span class="v">${V.isNum(a.usd) ? V.money(a.usd) : ''}${V.isNum(a.px) ? `<span class="muted small"> по ${V.price(a.px)}</span>` : ''}</span>
      ${a.text ? `<span class="x">${esc(a.text)}</span>` : ''}</div>`;

  /* ── «Обзор» ─────────────────────────────────────────────────────────── */
  const ui = (V.ui.ov = V.ui.ov || { src: 'btc', per: '30d' });
  const REG = { up: 'рост', down: 'снижение', flat: 'без направления', side: 'без направления', on: 'рост', off: 'осторожно' };

  V.views.overview = {
    async html() {
      const [H, MK, CAL, IDEAS, PF, DS] = await V.feeds(['app_home.json', 'markets.json', 'calendar.json', 'ideas.json', 'portfolio.json', 'desk_select.json']);
      const [M, S, ST, OP] = await Promise.all([V.real(), V.sysStatus(), V.strategiesModel ? V.strategiesModel() : null, V.oppsModel ? V.oppsModel() : null]);
      const mv = (H && H.market_view) || null;
      const X = V.spotModel ? await V.spotModel() : null;
      const P = X && V.spotPipe ? V.spotPipe(M, X) : null;
      return `<div class="ph"><div><h2>Обзор</h2><p>Рынок, состояние системы и публичный портфель CRYPTO NOVA — на одном экране.</p></div></div>
      <div class="ov-grid">
        ${marketCard(mv, MK, CAL)}${systemCard(S)}${financeCard(M)}${chartCard()}${positionsCard(M)}${recoCard(IDEAS, PF, DS, P, X)}${stratCard(ST)}${oppsCard(OP)}
      </div>`;
    },
    async mount(el) {
      el.querySelectorAll('[data-ovsrc]').forEach((b) => b.addEventListener('click', () => { ui.src = b.dataset.ovsrc; ui.per = ui.src === 'btc' ? '30d' : 'all'; paintChart(el); }));
      el.querySelectorAll('[data-ovper]').forEach((b) => b.addEventListener('click', () => { if (!b.disabled) { ui.per = b.dataset.ovper; paintChart(el); } }));
      el.querySelectorAll('[data-showpf]').forEach((b) => b.addEventListener('click', () => { ui.src = 'pf'; ui.per = 'all'; paintChart(el); el.querySelector('[data-demo="ov-chart"]').scrollIntoView({ behavior: 'smooth', block: 'center' }); }));
      await paintChart(el);
    },
  };
  V.ovChart = (src, per) => { ui.src = src; ui.per = per; const el = document.getElementById('view'); if (el && el.querySelector('#ovChart')) return paintChart(el); };

  function marketCard(mv, MK, CAL) {
    if (!mv) return `<section class="card lead" data-demo="ov-market"><div class="card-h"><span class="eyebrow">Рынок</span></div>${V.nodata('сводка рынка (app_home.json → market_view) не загрузилась')}</section>`;
    const top = A(MK && MK.top), coin = (s) => top.find((c) => c.symbol === s);
    const c24 = (s) => { const c = coin(s); return c ? `<span class="${V.cls(c.chg24)}">${V.pct(c.chg24)}</span> за 24 ч` : 'нет данных'; };
    const btc = coin('BTC') || {};
    const fng = MK && MK.fng;
    const g = MK && MK.global;
    const reg = REG[mv.regime] || mv.regime || '—';
    const ev = A(CAL && CAL.events).filter((e) => V.ms(e.time) > Date.now() && (e.importance || 0) >= 2).slice(0, 2);
    const sys = A(mv.system), sysd = A(mv.system_demo);
    return `<section class="card lead" data-demo="ov-market">
      <div class="card-h"><span class="eyebrow">Рынок</span><span class="regime ${mv.regime === 'up' || mv.regime === 'on' ? 'up' : ''}">Режим системы: ${esc(reg)}</span><span class="sp"></span>${V.stamp(mv.asof, 90)}</div>
      <p class="lead-text">${esc(mv.now || '')} ${esc(mv.short || '')}</p>
      <div class="kpis k5" style="margin-top:12px">
        ${V.kpi('Bitcoin', V.price(btc.price || (mv.btc && mv.btc.price)), c24('BTC'))}
        ${V.kpi('Ethereum', V.price((coin('ETH') || {}).price), c24('ETH'))}
        ${V.kpi('Solana', V.price((coin('SOL') || {}).price), c24('SOL'))}
        ${V.kpi('Страх и жадность', fng && V.isNum(fng.value) ? V.int(fng.value) : '—', fng ? esc(fng.label || '') + (V.isNum(fng.week) ? ' · неделю назад ' + V.int(fng.week) : '') : 'нет данных')}
        ${V.kpi('Доля BTC', g && V.isNum(g.btc_dom) ? V.share(g.btc_dom) : '—', g && V.isNum(g.mcap_chg24) ? 'рынок ' + V.pct(g.mcap_chg24) + ' за 24 ч' : '')}
      </div>
      ${sys.length ? `<div style="margin-top:12px"><span class="eyebrow">Что делает система</span><ul class="bul">${sys.map((s) => `<li>${esc(s)}</li>`).join('')}</ul></div>` : ''}
      <details class="more"><summary>Подробнее: горизонты, факторы, риски, события</summary>
        <div class="grid g2" style="margin-top:10px">
          <div><span class="eyebrow">Горизонты</span><ul class="bul">
            ${mv.short ? `<li><b>Неделя.</b> ${esc(mv.short)}</li>` : ''}${mv.mid ? `<li><b>Месяц.</b> ${esc(mv.mid)}</li>` : ''}${mv.long ? `<li><b>Год.</b> ${esc(mv.long)}</li>` : ''}</ul></div>
          <div>${A(mv.factors).length ? `<span class="eyebrow">Факторы</span><ul class="bul">${mv.factors.map((s) => `<li>${esc(s)}</li>`).join('')}</ul>` : ''}
            ${A(mv.risks).length ? `<div style="margin-top:10px"><span class="eyebrow">Риски</span><ul class="bul">${mv.risks.map((s) => `<li>${esc(s)}</li>`).join('')}</ul></div>` : ''}</div>
        </div>
        ${ev.length ? `<div style="margin-top:12px"><span class="eyebrow">Ближайшие события</span><ul class="bul">${ev.map((e) => `<li><b>${esc(V.dt(e.time))} · ${esc(e.name)}.</b> ${esc(e.what || '')}</li>`).join('')}</ul></div>` : ''}
        ${sysd.length ? `<p class="note">Демо-счета (тест, виртуальные деньги): ${esc(sysd.join(' '))}</p>` : ''}
        ${mv.limits ? `<p class="note">${esc(mv.limits)}</p>` : ''}
      </details>
    </section>`;
  }

  function chartCard() {
    return `<section class="card" data-demo="ov-chart">
      <div class="card-h"><h3>Главный график</h3>
        <div class="seg" role="group" aria-label="Что показать">
          <button type="button" data-ovsrc="btc" aria-pressed="${ui.src === 'btc'}">Bitcoin</button>
          <button type="button" data-ovsrc="pf" aria-pressed="${ui.src === 'pf'}">Портфель с 22.10</button></div>
        <span class="sp"></span>
        <div class="seg" role="group" aria-label="Период" id="ovPer"></div></div>
      <div id="ovChart"><div class="chart-empty">Загрузка…</div></div>
      <p class="note" id="ovChartNote"></p>
    </section>`;
  }

  const DAY = 86400000;
  async function chartSeries(src, per) {
    const now = Date.now(), utcDay = now - (now % DAY);
    if (src === 'pf') {
      const M = await V.real();
      const c = A(M && M.curve);
      const first = c.length ? V.ms(c[0][0]) : null;
      const avail = { today: c.filter((p) => V.ms(p[0]) >= utcDay).length >= 2, '7d': !!first && first <= now - 7 * DAY, '30d': !!first && first <= now - 30 * DAY, all: c.length >= 2 };
      const from = { today: utcDay, '7d': now - 7 * DAY, '30d': now - 30 * DAY, all: 0 }[per];
      return { avail, pts: c.filter((p) => V.ms(p[0]) >= from), fmt: (v, ax) => V.smoney(v, ax ? 0 : 2), base: 0, signed: true,
        note: `Результат реального счёта CRYPTO NOVA на OKX с ${V.PUBLIC_START_LABEL} в долларах: стоимость счёта минус стартовый капитал; депозиты, выводы и переводы не считаются результатом. Источник: public_real.json.`
          + (avail['7d'] ? '' : ' Периоды длиннее истории счёта недоступны.') + (M && M.stale ? ' Данные устарели.' : '') };
    }
    const B = await V.feed('app_btc.json');
    const closes = A(B && B.closes), d0 = B && V.isNum(B.start_day) ? +B.start_day : null;
    const daily = d0 == null ? [] : closes.map((v, i) => [(d0 + i) * DAY, v]);
    let intraday = null;
    if (per === 'today' || per === '7d') {
      const T = await V.feed('terminal.json', 5 * 60000);
      const b = A(T && T.coins).find((c) => c.symbol === 'BTC-USDT');
      intraday = A(b && b.candles).map((k) => [k[0], k[4]]);
    }
    const ic = intraday || [];
    const avail = { today: true, '7d': true, '30d': daily.length >= 30, all: daily.length >= 2 };
    let pts;
    if (per === 'today') pts = ic.filter((p) => p[0] >= utcDay);
    else if (per === '7d') pts = ic.filter((p) => p[0] >= now - 7 * DAY);
    else if (per === '30d') pts = daily.slice(-31);
    else pts = daily;
    const tfmt = per === 'today' ? V.time : per === 'all' ? ((t) => V.date(t).slice(3)) : V.dshort;
    return { avail, pts, fmt: (v, ax) => (ax ? V.price(v) : V.price(v)), tfmt, tipT: per === 'today' || per === '7d' ? V.dt : V.date,
      note: per === 'today' || per === '7d' ? 'BTC/USDT, 15-минутные свечи (terminal.json); «Сегодня» — с 00:00 UTC.' : 'BTC, дневные закрытия UTC (app_btc.json)' + (per === 'all' && daily.length ? ` с ${V.date(daily[0][0])}.` : '.') };
  }

  async function paintChart(el) {
    const box = el.querySelector('#ovChart'); if (!box) return;
    el.querySelectorAll('[data-ovsrc]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.ovsrc === ui.src)));
    const s = await chartSeries(ui.src, ui.per);
    if (!s.avail[ui.per]) { ui.per = ['all', '30d', '7d', 'today'].find((p) => s.avail[p]) || 'all'; return paintChart(el); }
    const names = { today: 'Сегодня', '7d': '7 дней', '30d': '30 дней', all: 'Всё' };
    el.querySelector('#ovPer').innerHTML = Object.keys(names).map((p) =>
      `<button type="button" data-ovper="${p}" aria-pressed="${p === ui.per}" ${s.avail[p] ? '' : 'disabled title="История данных короче этого периода"'}>${names[p]}</button>`).join('');
    el.querySelectorAll('[data-ovper]').forEach((b) => b.addEventListener('click', () => { if (!b.disabled) { ui.per = b.dataset.ovper; paintChart(el); } }));
    V.chart(box, s.pts, { height: 230, fmt: s.fmt, base: s.base, signed: s.signed, tfmt: s.tfmt || (ui.per === 'today' ? V.time : V.dshort), tipT: s.tipT || V.dt,
      label: ui.src === 'btc' ? 'Цена биткоина' : 'Результат публичного портфеля', empty: 'Нет данных за этот период' });
    el.querySelector('#ovChartNote').textContent = s.note;
  }

  function systemCard(S) {
    const bad = S.comps.filter((c) => c.status !== 'ok');
    return `<section class="card" data-demo="ov-system">
      <div class="card-h"><span class="eyebrow">Состояние системы</span><span class="sp"></span>
        <button class="link" type="button" data-act="go" data-to="system">Подробнее</button></div>
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap"><span class="dot ${S.overall}" style="width:10px;height:10px"></span>
        <b style="font-size:17px">${esc(S.label)}</b>
        ${S.last ? `<span class="stamp">последнее успешное обновление ${esc(V.dt(S.last))} · ${esc(V.ago(S.last))}</span>` : ''}</div>
      ${bad.length ? `<ul class="bul">${bad.map((c) => `<li><b>${esc(c.name)}</b> — ${esc(V.statusRu(c.status))}${c.note ? ': ' + esc(c.note) : ''}</li>`).join('')}</ul>`
        : `<p class="note" style="margin-top:6px">Все ${S.comps.length} ${V.plural(S.comps.length, 'компонент обновляется', 'компонента обновляются', 'компонентов обновляются')} вовремя.</p>`}
      ${S.src === 'fallback' ? `<p class="note">Статус собран по времени обновления опубликованных файлов (system_status.json ещё не опубликован).</p>` : ''}
    </section>`;
  }

  function financeCard(M) {
    if (!M) return `<section class="card a-fin" data-demo="ov-finance"><div class="card-h"><span class="eyebrow">Публичный портфель</span></div>${V.nodata('public_real.json не загрузился')}</section>`;
    const eq = M.equity, pc = (v) => (V.isNum(v) && eq ? V.share((v / eq) * 100) + ' счёта' : '');
    const fl = M.flows;
    const flowsTxt = fl && V.isNum(fl.n) ? (fl.n ? `Депозиты, выводы и переводы с ${V.PUBLIC_START_LABEL}: ${V.int(fl.n)} на ${V.smoney(fl.net_usd)} — исключены из результата.` : `Депозитов, выводов и переводов с ${V.PUBLIC_START_LABEL} не было${fl.checked_t ? ' (проверено ' + V.dt(fl.checked_t) + ')' : ''}.`) : 'Данных о депозитах и выводах нет.';
    const realizedRow = M.realized != null
      ? V.kpi('Реализовано', `<span class="${V.cls(M.realized)}">${V.smoney(M.realized)}</span>`, 'закрытые сделки') + V.kpi('Нереализовано', `<span class="${V.cls(M.unrealized)}">${V.smoney(M.unrealized)}</span>`, 'открытые позиции')
      : V.kpi('Фьючерсы · реализовано', `<span class="${V.cls(M.realizedFut)}">${V.smoney(M.realizedFut)}</span>`, `${V.int(M.closed.length)} ${V.plural(M.closed.length, 'закрытая сделка', 'закрытые сделки', 'закрытых сделок')}`)
        + V.kpi('Фьючерсы · открытые', `<span class="${V.cls(M.openUpnl)}">${V.smoney(M.openUpnl)}</span>`, M.futures.length ? 'нереализовано' : 'позиций нет');
    return `<section class="card a-fin" data-demo="ov-finance">
      <div class="card-h"><span class="eyebrow">Публичный портфель CRYPTO NOVA</span>${V.chip('реальный счёт OKX', 'real')}${V.realStartWarn(M)}<span class="sp"></span>${V.stamp(M.asof, 60)}</div>
      <div class="kpis">
        ${V.kpi('Стоимость сейчас', V.money(eq), `старт ${V.money(M.start)} · ${esc(V.date(M.since))}`, '', 'big')}
        ${V.kpi('Результат с начала', `<span class="${V.cls(M.pnl)}">${V.smoney(M.pnl)}</span>`, `<span class="${V.cls(M.pnlPct)}">${V.pct(M.pnlPct)}</span>`)}
        ${V.kpi('Сегодня', `<span class="${V.cls(M.today)}">${V.smoney(M.today)}</span>`, `<span class="${V.cls(M.todayPct)}">${V.pct(M.todayPct)}</span> · с 00:00 UTC`)}
        ${V.kpi('Спот · результат', `<span class="${V.cls(M.spotPnl)}">${V.smoney(M.spotPnl)}</span>`, 'монеты, кэш и активный спот')}
        ${realizedRow}
        ${V.kpi('В работе', V.money(M.used), pc(M.used))}
        ${V.kpi('Свободно (USDT)', V.money(M.free), pc(M.free))}
      </div>
      <p class="note">${esc(flowsTxt)} Спот показан одной суммой: в данных нет разделения на реализованный и нереализованный результат по монетам.
        <button class="link" type="button" data-showpf="1">Показать на графике</button></p>
      ${M.copyUrl ? `<p class="note">Повторять сделки счёта можно через копитрейдинг OKX — функцию самой биржи: <a href="${esc(M.copyUrl)}" target="_blank" rel="noopener">профиль на OKX</a>${V.isNum(M.copiers) ? ` · копируют: ${V.int(M.copiers)}` : ''}.</p>` : ''}
    </section>`;
  }

  function positionsCard(M) {
    if (!M) return `<section class="card" data-demo="ov-positions"><div class="card-h"><span class="eyebrow">Позиции и действия</span></div>${V.nodata()}</section>`;
    const warn = [];
    if (M.stale) warn.push('Данные реального счёта устарели — зеркало давно не обновляло счёт.');
    if (M.since && !M.startOk) warn.push(`Дата начала в данных (${V.date(M.since)}) не совпадает с ${V.PUBLIC_START_LABEL}.`);
    return `<section class="card" data-demo="ov-positions">
      <div class="card-h"><span class="eyebrow">Позиции и последние действия</span><span class="sp"></span>${V.chip('реальный счёт', 'real')}</div>
      <div class="kpis k3">
        ${V.kpi('Спот-активов', V.int(M.spot.length), `<button class="link" type="button" data-act="go" data-to="spot">Спот</button>`)}
        ${V.kpi('Фьючерсных позиций', V.int(M.futures.length), `<button class="link" type="button" data-act="go" data-to="futures">Фьючерсы</button>`)}
        ${V.kpi('Действий с начала', V.int(M.actions.length), 'реальные исполненные ордера')}
      </div>
      ${warn.length ? `<div style="margin-top:10px">${warn.map((w) => `<div class="chip warn" style="white-space:normal;line-height:1.4;margin:3px 0">${esc(w)}</div>`).join('')}</div>` : ''}
      <div class="list" style="margin-top:8px">${M.actions.slice(0, 3).map(V.actionLi).join('') || V.empty('Действий на реальном счёте пока не было.')}</div>
    </section>`;
  }

  function recoCard(IDEAS, PF, DS, P, X) {
    const ideas = PF && PF.portfolios && PF.portfolios.ideas;
    // one source with «Спот»: the pipeline's recommended-but-not-bought coins (spot_pipeline.json, else the model's own list)
    const nb = P ? P.coins.filter((c) => c.reco && !c.held).map((c) => ({ symbol: c.sym, why_ru: shortWhy(c.why) })) : A(ideas && ideas.not_bought);
    const PL = X && X.PL;
    const cash = PL && V.isNum(PL.cash_pct_real) ? { now_pct: PL.cash_pct_real, target_pct: PL.cash_target_pct, real: true } : ideas && ideas.sleeves && ideas.sleeves.cash;
    return `<section class="card a-reco" data-demo="ov-reco">
      <div class="card-h"><span class="eyebrow">Ключевые рекомендации</span><span class="sp"></span>${IDEAS ? V.stamp(IDEAS.asof, 180) : ''}</div>
      ${IDEAS && IDEAS.summary_ru ? `<p class="para"><b>Спот, таблица отбора:</b> ${esc(IDEAS.summary_ru)}</p>` : V.nodata('таблица «Что брать на спот»')}
      ${nb.length ? `<p class="para"><b>Рекомендованы, но не куплены:</b> ${nb.slice(0, 4).map((x) => `${esc(x.symbol)} — ${esc(x.why_ru)}`).join('; ')}${nb.length > 4 ? '…' : ''}</p>` : ''}
      ${cash && V.isNum(cash.now_pct) ? `<p class="para"><b>${cash.real ? 'Кэш спот-портфеля на реальном счёте' : 'Кэш модели спота'}:</b> ${V.share(cash.now_pct)} при минимуме ${V.share(cash.target_pct, 0)} — <button class="link" type="button" data-act="go" data-to="spot" data-arg="cash">почему</button>.</p>` : ''}
      ${DS && DS.why_ru ? `<p class="para"><b>Фьючерсы, выбор стола:</b> ${esc(DS.why_ru)}</p>` : ''}
      <p class="note">${esc((IDEAS && IDEAS.disclaimer_ru) || 'Правила отбора, а не прогноз цены.')}</p>
    </section>`;
  }

  // «Таблица: … Портфель не покупает: <reason>» → the reason only
  const shortWhy = (w) => { w = String(w || 'причина не записана'); const i = w.indexOf('Портфель не покупает:'); return (i >= 0 ? w.slice(i + 21) : w).trim().replace(/\.$/, ''); };

  function stratCard(ST) {
    if (!ST) return `<section class="card" data-demo="ov-strats"><div class="card-h"><span class="eyebrow">Стратегии</span></div>${V.nodata()}</section>`;
    const real = ST.items.filter((s) => s.real);
    return `<section class="card" data-demo="ov-strats">
      <div class="card-h"><span class="eyebrow">Стратегии</span><span class="sp"></span><button class="link" type="button" data-act="go" data-to="strategies">Все стратегии</button></div>
      <div class="kpis k3">${ST.groups.filter((g) => g.items.length || g.key !== 'archive').slice(0, 3).map((g) => V.kpi(g.name, V.int(g.items.length), '')).join('')}</div>
      <p class="para" style="margin-top:10px"><b>Реальные деньги следуют за:</b> ${real.length ? real.map((s) => esc(s.name)).join(', ') : 'нет данных'}.</p>
      <p class="note">Win rate всегда показан вместе с числом сделок. Остальные стратегии — тесты на демо-счетах.${ST.src === 'fallback' ? ' Сейчас список собран из демо-столов (paper.json); единые статусы появятся с strategies.json.' : ''}</p>
    </section>`;
  }

  function oppsCard(OP) {
    if (!OP) return `<section class="card" data-demo="ov-opps"><div class="card-h"><span class="eyebrow">Другие возможности</span></div>${V.nodata()}</section>`;
    const realN = OP.items.filter((o) => o.real).length;
    return `<section class="card" data-demo="ov-opps">
      <div class="card-h"><span class="eyebrow">Другие возможности</span><span class="sp"></span><button class="link" type="button" data-act="go" data-to="opps">Подробнее</button></div>
      <p class="para">${V.int(OP.items.length)} ${V.plural(OP.items.length, 'направление', 'направления', 'направлений')} на проверке; на реальных деньгах — ${realN ? V.int(realN) : 'ни одного'}.</p>
      <div class="list">${OP.items.slice(0, 4).map((o) => `<div class="li"><span class="t">${o.real ? V.chip('реальные деньги', 'real') : V.chip(o.statusRu, 'test')}</span>
        <span class="d">${esc(o.name)}</span><span class="v small ${V.cls(o.testUsd)}">${V.isNum(o.testUsd) ? V.smoney(o.testUsd) + ' <span class="muted">тест</span>' : ''}</span></div>`).join('')}</div>
    </section>`;
  }
})();
