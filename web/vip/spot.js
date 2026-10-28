/* CRYPTO NOVΛ VIP — «Спот»: the public portfolio's spot part since 22.10.2026 (owner spec §7.1–7.3, + the pipeline and the cash
   question of §7.4–7.5). Real fills come from public_real.json; the model portfolio it mirrors (portfolio.json → ideas) only explains. */
(function () {
  'use strict';
  const V = window.VIP, esc = V.esc, A = V.arr;
  const ui = (V.ui.spot = V.ui.spot || { tab: 'real' });

  const MODEL_ACT = { BUY: 'Покупка', SELL: 'Продажа', TRIM: 'Частичная фиксация', ADD: 'Изменение размера (докупка)', REBALANCE: 'Ребалансировка', STOP: 'Продажа по стопу', TAKE: 'Частичная фиксация' };

  async function model() {
    const [PF, MK, IDEAS, PL] = await V.feeds(['portfolio.json', 'markets.json', 'ideas.json', 'spot_pipeline.json']);
    const ideas = PF && PF.portfolios && PF.portfolios.ideas;
    const px = {};
    for (const c of A(MK && MK.top)) if (c && c.symbol && px[c.symbol] == null) px[c.symbol] = { price: c.price, name: c.name, chg24: c.chg24 };
    const pos = {};
    for (const p of A(ideas && ideas.positions)) pos[p.symbol] = p;
    const acts = A(ideas && ideas.recent_actions);
    return { PF, MK, IDEAS, PL, ideas, px, pos, acts };
  }
  V.spotModel = model;

  V.views.spot = {
    async html(arg) {
      const [M, X] = await Promise.all([V.real(), model()]);
      return `<div class="ph"><div><h2>Спот</h2><p>Спотовая часть публичного портфеля CRYPTO NOVA на OKX с ${V.PUBLIC_START_LABEL}: что куплено, сколько вложено, что делает система и почему.</p></div></div>
        ${summary(M, X)}
        ${assets(M, X)}
        ${journal(M, X)}
        ${pipeline(M, X)}
        ${cashCard(M, X)}`;
    },
    mount(el, arg) {
      el.querySelectorAll('[data-asset]').forEach((tr) => {
        tr.addEventListener('click', () => openAsset(tr.dataset.asset));
        tr.addEventListener('keydown', (e) => { if (e.key === 'Enter') openAsset(tr.dataset.asset); });
      });
      el.querySelectorAll('[data-jtab]').forEach((b) => b.addEventListener('click', () => { ui.tab = b.dataset.jtab; V.render({ keepScroll: true }); }));
      if (arg === 'cash' || arg === 'pipeline') { const t = el.querySelector(`[data-demo="spot-${arg}"]`); if (t) setTimeout(() => t.scrollIntoView({ block: 'start' }), 50); }
      else if (arg) setTimeout(() => openAsset(arg), 0);
    },
  };

  function summary(M, X) {
    if (!M) return `<section class="card" data-demo="spot-summary">${V.nodata('public_real.json не загрузился')}</section>`;
    const S = M.spotPart || {}, a = M.alloc || {};
    const coins = V.n(a.spot_coins_usd), cash = V.n(a.spot_cash_usd), wait = V.n(a.active_wait_usd), act = V.n(a.active_coins_usd);
    const sleeve = (coins || 0) + (cash || 0);
    const cashPct = X.PL && V.isNum(X.PL.cash_pct_real) ? +X.PL.cash_pct_real : sleeve ? (cash / sleeve) * 100 : null;
    const value = V.isNum(S.value_usd) ? +S.value_usd : coins != null ? (coins || 0) + (cash || 0) + (wait || 0) + (act || 0) : null;
    const pnl = V.isNum(S.pnl_usd) ? +S.pnl_usd : M.spotPnl;
    return `<section class="card lead" data-demo="spot-summary">
      <div class="card-h"><span class="eyebrow">Сводка спота</span>${V.chip('реальный счёт OKX', 'real')}${V.realStartWarn(M)}<span class="sp"></span>${V.stamp(M.asof, 60)}</div>
      <div class="kpis">
        ${V.kpi('Стоимость спот-части', V.money(value), V.isNum(S.start_usd) ? `старт ${V.money(S.start_usd)} · ${esc(V.date(S.since || M.since))}` : `с ${V.PUBLIC_START_LABEL}`, '', 'big')}
        ${V.kpi('Результат спота', `<span class="${V.cls(pnl)}">${V.smoney(pnl)}</span>`, V.isNum(S.pct) ? `<span class="${V.cls(S.pct)}">${V.pct(S.pct)}</span>` : '')}
        ${V.kpi('Свободно (кэш спота)', V.money(cash), cashPct != null ? `${V.share(cashPct)} спот-портфеля` : '')}
        ${V.kpi('В монетах', V.money(coins), sleeve ? `${V.share((coins / sleeve) * 100)} спот-портфеля` : '')}
        ${V.kpi('Активов', V.int(M.spot.length), 'монет на счёте')}
        ${V.kpi('Активный спот', act ? V.money(act) : 'ждёт сигнала', V.isNum(wait) ? `${V.money(wait)} в USDT` : '')}
        ${V.kpi('Весь счёт', V.money(M.equity), `<span class="${V.cls(M.pnlPct)}">${V.pct(M.pnlPct)}</span> с начала`)}
        ${V.kpi('Начало публичного портфеля', V.PUBLIC_START_LABEL, 'старт ' + V.money(M.start))}
      </div>
      <p class="note">${esc(S.base_note || 'База спота — его доля стартового капитала.')} ${S.moved_note ? esc(S.moved_note) + ' ' : ''}Спот-портфель повторяет модель «CRYPTO NOVA портфель» (65% счёта), ещё 10% — дополнительное ядро BTC, ETH и SOL (30 / 10 / 5), активный спот — стол «Импульс BTC» (10%; между сигналами — в BTC).</p>
    </section>`;
  }

  function assetRows(M, X) {
    return M.spot.map((s) => {
      const p = X.px[s.sym] || {}, pos = X.pos[s.sym] || null;
      const price = V.isNum(s.price) ? +s.price : V.isNum(s.px) ? +s.px : V.n(p.price);
      const qty = V.isNum(s.qty) ? +s.qty : V.isNum(s.usd) && price ? s.usd / price : null;
      const avg = V.isNum(s.avg_px) ? +s.avg_px : V.isNum(s.entry) ? +s.entry : null;
      const pnl = V.isNum(s.pnl_usd) ? +s.pnl_usd : null, pnlPct = V.isNum(s.pnl_pct) ? +s.pnl_pct : avg && price ? (price / avg - 1) * 100 : null;
      const last = X.acts.find((a) => a.symbol === s.sym);
      return { s, p, pos, price, qty, qtyExact: V.isNum(s.qty), avg, pnl, pnlPct, last, name: p.name || (pos && pos.name) || '' };
    });
  }

  function assets(M, X) {
    if (!M) return '';
    const rows = assetRows(M, X);
    const anyAvg = rows.some((r) => r.avg != null);
    return `<section class="card" style="margin-top:var(--gap)" data-demo="spot-assets">
      <div class="card-h"><h3>Активы</h3><span class="sub">нажмите на строку — подробности, график и история</span></div>
      ${rows.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr>
          <th>Актив</th><th class="hide-sm">Кол-во</th><th class="hide-md">Ср. вход</th><th>Цена</th><th>Стоимость</th><th>Доля</th><th class="hide-sm">Результат</th><th class="l hide-md">Статус</th><th class="l hide-md">Последнее решение</th></tr></thead><tbody>
        ${rows.map((r) => `<tr class="click" tabindex="0" data-asset="${esc(r.s.sym)}">
          <td><span class="sym">${esc(r.s.sym)}<small>${esc(r.name)}</small></span></td>
          <td class="hide-sm">${r.qty != null ? (r.qtyExact ? '' : '≈') + V.qty(r.qty) : '—'}</td>
          <td class="hide-md">${r.avg != null ? V.price(r.avg) : '—'}</td>
          <td>${V.price(r.price)}</td>
          <td>${V.money(r.s.usd)}</td>
          <td>${V.share(r.s.share)}</td>
          <td class="hide-sm">${r.pnl != null ? `<span class="${V.cls(r.pnl)}">${V.smoney(r.pnl)}</span><br>` : ''}${r.pnlPct != null ? `<span class="${V.cls(r.pnlPct)}">${V.pct(r.pnlPct)}</span>` : '—'}</td>
          <td class="l hide-md">${r.pos ? esc(r.pos.kind_ru || 'в модели') : V.chip('нет в модели', 'test')}</td>
          <td class="wrap hide-md">${r.last ? `<b>${esc(V.dshort(r.last.time))} · ${esc(MODEL_ACT[r.last.action] || r.last.action)}.</b> ${esc(cut(r.last.reason, 90))}` : '<span class="muted">—</span>'}</td>
        </tr>`).join('')}</tbody></table></div>` : V.empty('На счёте нет монет — весь спот в долларах.')}
      <p class="note">Стоимость и доля — реальный счёт (public_real.json). Цена — CoinGecko (markets.json)${rows.some((r) => !r.qtyExact) ? ', количество ≈ стоимость / цена' : ''}.
        ${anyAvg ? '' : 'Средняя цена входа и результат по монете реального счёта пока не публикуются — поэтому «—», а не расчёт по модели.'}
        Статус и последнее решение — модель «CRYPTO NOVA портфель», которую повторяет счёт.</p>
    </section>`;
  }
  const cut = (s, n) => { s = String(s || ''); return s.length > n ? s.slice(0, n - 1) + '…' : s; };

  async function openAsset(sym) {
    const [M, X] = await Promise.all([V.real(), model()]);
    if (!M) return;
    const r = assetRows(M, X).find((x) => x.s.sym === sym);
    const pos = X.pos[sym], acts = X.acts.filter((a) => a.symbol === sym), real = M.actions.filter((a) => a.sym === sym);
    const lv = (pos && pos.levels) || {};
    V.sheet(sym + (r && r.name ? ' · ' + r.name : ''), `
      ${r ? `<div class="kpis">${V.kpi('Стоимость', V.money(r.s.usd), V.share(r.s.share) + ' счёта')}${V.kpi('Цена', V.price(r.price), r.p.chg24 != null ? `<span class="${V.cls(r.p.chg24)}">${V.pct(r.p.chg24)}</span> за 24 ч` : '')}
        ${V.kpi('Количество', r.qty != null ? (r.qtyExact ? '' : '≈') + V.qty(r.qty) : '—', r.qtyExact ? '' : 'стоимость / цена')}${V.kpi('Ср. вход', r.avg != null ? V.price(r.avg) : '—', r.avg != null ? '' : 'не публикуется')}</div>`
        : `<p class="para">${esc(sym)} сейчас нет на реальном счёте.</p>`}
      <div class="sec-t"><h3>График</h3><span class="sub">дневные свечи</span></div><div id="assetChart"><div class="chart-empty">Загрузка…</div></div>
      ${pos ? `<div class="sec-t"><h3>Почему держим</h3><span class="sub">модель «CRYPTO NOVA портфель»</span></div>
        <p class="para">${esc(pos.reason || '—')}</p>
        <div class="kv" style="margin-top:8px"><span>Роль</span><b>${esc(pos.kind_ru || '—')}${pos.role_ru ? ' · ' + esc(pos.role_ru) : ''}</b>
          <span>Горизонт</span><b>${esc(pos.horizon_ru || '—')}</b>
          <span>Стоп модели</span><b>${lv.stop ? V.price(lv.stop) : 'без стопа'}</b>
          ${lv.take_half ? `<span>Фиксация половины</span><b>${V.price(lv.take_half)}</b>` : ''}
          <span>Вход модели</span><b>${V.price(pos.entry)} · ${esc(V.date(pos.entry_time))}</b></div>
        <p class="note">Вход модели — цена её виртуальной покупки, не средняя цена реального счёта.</p>` : ''}
      <div class="sec-t"><h3>Действия на реальном счёте</h3></div>
      <div class="list">${real.map(V.actionLi).join('') || V.empty('С ' + V.PUBLIC_START_LABEL + ' по этой монете исполненных ордеров нет.')}</div>
      <div class="sec-t"><h3>Решения модели</h3></div>
      <div class="list">${acts.map(modelLi).join('') || V.empty('Нет записанных решений.')}</div>`, async (b) => {
      const T = await V.feed('terminal.json', 5 * 60000);
      const c = A(T && T.coins).find((x) => x.symbol === sym + '-USDT');
      const box = b.querySelector('#assetChart'); if (!box) return;
      if (c && A(c.candles_1d).length) V.chart(box, c.candles_1d.map((k) => [k[0], k[4]]), { height: 180, fmt: (v) => V.price(v), tfmt: V.dshort, tipT: V.date, label: 'Цена ' + sym });
      else box.innerHTML = `<div class="chart-empty">Для ${esc(sym)} в данных панели нет истории свечей</div>`;
    });
  }
  function modelLi(a) {
    return `<div class="li"><span class="t">${esc(V.dt(a.time))}</span><span class="d"><b>${esc(MODEL_ACT[a.action] || a.action)} · ${esc(a.symbol)}</b></span>
      <span class="v small">${V.isNum(a.price) ? 'по ' + V.price(a.price) : ''}</span>${a.reason ? `<span class="x">${esc(a.reason)}</span>` : ''}</div>`;
  }

  function journal(M, X) {
    const tabs = [['real', 'Реальный счёт'], ['model', 'Решения модели'], ['skip', 'Отказы и пропуски']];
    let body = '';
    if (ui.tab === 'real') {
      const list = M ? M.actions.filter((a) => /^(spot|active)_/.test(a.kind)) : [];
      body = list.map(V.actionLi).join('') || V.empty('С ' + V.PUBLIC_START_LABEL + ' спотовых ордеров на реальном счёте не было.');
      body += `<p class="note">Только исполненные ордера автопилота на OKX (public_real.json → actions). Причина — запись автопилота; если её нет, причина не дописывается задним числом.</p>`;
    } else if (ui.tab === 'model') {
      body = X.acts.filter((a) => V.ms(a.time) >= (M && M.since ? M.since : 0)).map(modelLi).join('') || V.empty('С ' + V.PUBLIC_START_LABEL + ' решений модели нет.');
      const older = X.acts.filter((a) => M && M.since && V.ms(a.time) < M.since).length;
      body += `<p class="note">Решения модели «CRYPTO NOVA портфель» с причинами из её журнала (portfolio.json). Реальный счёт повторяет их своими ордерами.${older ? ` Ещё ${older} ${V.plural(older, 'решение', 'решения', 'решений')} до ${V.PUBLIC_START_LABEL} — история модели, не публичного портфеля.` : ''}</p>`;
    } else {
      const P = pipeModel(M, X);
      const sk = P.coins.filter((c) => c.reco && !c.held);
      body = sk.map((c) => `<div class="li"><span class="t">${V.chip(c.statusRu || 'рекомендована', 'test')}</span><span class="d"><b>Отказ от покупки · ${esc(c.sym)}</b></span><span class="v"></span><span class="x">${esc(c.why || 'Причина не записана — неизвестно (нужна диагностика модуля портфеля).')}</span></div>`).join('')
        || V.empty('Сейчас нет рекомендованных монет, от покупки которых система отказалась.');
      body += `<p class="note">Причины — из записи модуля портфеля (${P.src}).</p>`;
    }
    return `<section class="card" style="margin-top:var(--gap)" data-demo="spot-journal">
      <div class="card-h"><h3>Журнал действий</h3><span class="sp"></span>
        <div class="seg" role="tablist">${tabs.map(([k, n]) => `<button type="button" role="tab" data-jtab="${k}" aria-pressed="${ui.tab === k}">${n}</button>`).join('')}</div></div>
      <div class="list">${body}</div></section>`;
  }

  /* the pipeline: found → passed → recommended → bought / waiting / rejected, with the reason per coin */
  function pipeModel(M, X) {
    const held = new Set((M ? M.spot : []).map((s) => s.sym)), held0 = held;
    const PL = X.PL;
    if (PL && (PL.stages || PL.coins || PL.items)) {
      const coins = A(PL.coins || PL.items || PL.rows).map((c) => {
        const sym = c.sym || c.symbol || c.ccy;
        const stage = String(c.stage || c.status || '');
        const held = stage === 'bought' || c.in_real_account === true || c.bought === true || held0.has(sym);
        return { sym, name: c.name && c.name !== sym ? c.name : '', stage, statusRu: c.stage_ru || c.status_ru || stage,
          why: c.why_not_ru || c.why_ru || c.reason_ru || c.reason || '', held, last: c.last_action || null,
          reco: ['all_filters', 'recommended', 'waiting', 'bought'].includes(stage) || ['buy', 'add', 'small'].includes(c.table_status) || c.recommended === true,
          rejected: stage === 'rejected' };
      });
      const st = Array.isArray(PL.stages) ? PL.stages : Object.entries(PL.stages || {}).map(([k, v]) => Object.assign({ key: k }, typeof v === 'object' ? v : { n: v }));
      return { src: 'spot_pipeline.json', asof: V.asofOf(PL), coins, stages: st.map((s) => {
        const n = V.n(s.reached != null ? s.reached : s.n != null ? s.n : s.count != null ? s.count : s.now);
        return { name: cap(s.name_ru || s.name || s.label || s.key), n, now: V.n(s.now), about: s.about_ru || '', rej: /reject|avoid/i.test(s.key || ''), side: s.reached == null && s.now != null };
      }) };
    }
    const I = X.IDEAS, ca = (I && (I.counts_all || I.counts)) || {};
    const nb = {};
    for (const x of A(X.ideas && X.ideas.not_bought)) nb[x.symbol] = x.why_ru;
    const coins = A(I && I.rows).map((r) => {
      const reco = ['buy', 'add', 'small'].includes(r.status);
      return { sym: r.symbol, name: r.name, stage: r.status, statusRu: r.status_ru, held: held.has(r.symbol), reco, rejected: r.status === 'avoid',
        why: reco ? (held.has(r.symbol) ? 'куплено' : nb[r.symbol] || '') : r.why_ru || '' };
    });
    const recoN = (ca.buy || 0) + (ca.add || 0) + (ca.small || 0);
    const recoHeld = coins.filter((c) => c.reco && c.held).length;
    return { src: 'ideas.json + portfolio.json', asof: I && I.asof, coins, stages: I ? [
      { name: 'Обнаружено и оценено', n: V.n(I.evaluated) },
      { name: 'Прошли первичную оценку', n: V.isNum(I.evaluated) ? I.evaluated - (ca.avoid || 0) : null },
      { name: 'Прошли все фильтры (рекомендованы)', n: recoN },
      { name: 'Куплены (из рекомендованных)', n: recoHeld },
      { name: 'Ждут подтверждения условий', n: V.n(ca.wait), side: 1 },
      { name: 'Отклонены («не брать»)', n: V.n(ca.avoid), rej: 1, side: 1 },
    ] : [] };
  }
  V.spotPipe = pipeModel;
  // the first sentences answer the question; the rest (every sale and candidate) opens on demand
  function splitWhy(t) {
    const parts = String(t).split(/(?<=[.!?»])\s+(?=[А-ЯЁA-Z])/);
    let head = '', i = 0;
    while (i < parts.length && (head.length < 260 || i < 2)) head += (head ? ' ' : '') + parts[i++];
    const rest = parts.slice(i).join(' ');
    return `<p class="para">${esc(head)}</p>` + (rest ? `<details class="more"><summary>Подробно: продажи и кандидаты</summary><p class="para">${esc(rest)}</p></details>` : '');
  }
  const cap = (t) => { t = String(t || ''); return t ? t.charAt(0).toUpperCase() + t.slice(1) : t; };

  function pipeline(M, X) {
    const P = pipeModel(M, X);
    const max = Math.max(1, ...P.stages.map((s) => s.n || 0));
    const reco = P.coins.filter((c) => c.reco).sort((a, b) => (a.held ? 1 : 0) - (b.held ? 1 : 0)), other = P.coins.filter((c) => !c.reco);
    const row = (c) => `<tr><td><span class="sym">${esc(c.sym)}${c.name ? `<small>${esc(c.name)}</small>` : ''}</span></td>
      <td class="l">${c.held ? V.chip('куплено', 'real') : c.rejected ? V.chip(c.statusRu || 'отклонена', 'test') : V.chip(c.statusRu || c.stage, 'test')}</td>
      <td class="wrap">${c.held ? '<span class="muted">в портфеле</span>' : esc(c.why || 'Причина не записана — неизвестно.')}</td></tr>`;
    return `<section class="card" style="margin-top:var(--gap)" data-demo="spot-pipeline">
      <div class="card-h"><h3>Воронка отбора монет</h3><span class="sub">от «найдено» до «куплено» или «отклонено»</span><span class="sp"></span>${V.stamp(P.asof, 180)}</div>
      ${P.stages.length ? `<div class="funnel">${P.stages.map((s) => `<div class="fstage" title="${esc(s.about || '')}"><span>${esc(s.name)}${s.side ? ' <span class="xs muted">сейчас</span>' : s.now != null && s.now !== s.n ? ` <span class="xs muted">· сейчас ${V.int(s.now)}</span>` : ''}</span><div class="fb"><i class="${s.rej ? 'rej' : ''}" style="width:${s.n ? Math.max(2, (s.n / max) * 100) : 0}%"></i></div><b>${s.n != null ? V.int(s.n) : '—'}</b></div>`).join('')}</div>` : V.nodata('воронка')}
      <div class="sec-t"><h3>Почему не куплено</h3><span class="sub">рекомендованные монеты и ответ системы по каждой</span></div>
      ${reco.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Монета</th><th class="l">Стадия</th><th class="l">Ответ системы</th></tr></thead><tbody>${reco.map(row).join('')}</tbody></table></div>` : V.empty('Рекомендованных монет сейчас нет.')}
      ${other.length ? `<details class="more"><summary>Остальные ${V.int(other.length)} из таблицы: ждём и «не брать»</summary>
        <div class="tbl-wrap" style="margin-top:8px"><table class="tbl"><thead><tr><th>Монета</th><th class="l">Стадия</th><th class="l">Почему</th></tr></thead><tbody>${other.map(row).join('')}</tbody></table></div></details>` : ''}
      <p class="note">Источник: ${esc(P.src)}. Рекомендация — это правило отбора, а не обязательная покупка: монета должна пройти фильтры риска и правила портфеля.</p>
    </section>`;
  }

  function cashCard(M, X) {
    const PL = X.PL, sl = X.ideas && X.ideas.sleeves;
    const a = (M && M.alloc) || {}, sleeve = (V.n(a.spot_coins_usd) || 0) + (V.n(a.spot_cash_usd) || 0);
    const real = PL && V.isNum(PL.cash_pct_real) ? +PL.cash_pct_real : sleeve ? (a.spot_cash_usd / sleeve) * 100 : null;
    const target = PL && V.isNum(PL.cash_target_pct) ? +PL.cash_target_pct : sl && sl.cash ? V.n(sl.cash.target_pct) : null;
    const nb = A(X.ideas && X.ideas.not_bought);
    const groups = {};
    for (const x of nb) (groups[x.why_ru] = groups[x.why_ru] || []).push(x.symbol);
    let why;
    if (PL && PL.cash_why_ru) why = splitWhy(PL.cash_why_ru) + `<p class="note">Источник: spot_pipeline.json${PL.real_stale ? ' · данные реального счёта устарели' : ''}. Защитные фильтры не ослабляются ради заполнения портфеля; доля кэша не меняется без решения владельца.</p>`;
    else {
      why = `<p class="para">Правило модели: кэш <b>не меньше</b> ${target != null ? V.share(target, 0) : '10%'} — это нижняя граница, а не обязанность потратить всё сверх неё. Свободные деньги идут только в монеты, которые прошли фильтры таблицы и правила портфеля.</p>`;
      if (sl && sl.buy) why += `<p class="para">Сейчас модель держит в монетах «можно брать» ${V.share(sl.buy.now_pct)} при плане ${V.share(sl.buy.target_pct)}${sl.small ? `, на небольшую сумму — ${V.share(sl.small.now_pct)} при плане ${V.share(sl.small.target_pct)}` : ''}. Недобор остаётся в кэше, потому что рекомендованные монеты не прошли правила покупки:</p>`;
      why += Object.keys(groups).length ? `<ul class="bul">${Object.entries(groups).map(([w, s]) => `<li><b>${esc(s.join(', '))}</b> — ${esc(w)}</li>`).join('')}</ul>` : V.empty('Отказов от покупки модель сейчас не записала.');
      why += `<p class="note">Источник: правила и отказы модели «CRYPTO NOVA портфель» (portfolio.json). Защитные фильтры не ослабляются ради заполнения портфеля; целевая доля кэша не меняется без решения владельца.</p>`;
    }
    return `<section class="card" style="margin-top:var(--gap)" data-demo="spot-cash">
      <div class="card-h"><h3>Почему система не тратит свободный капитал</h3></div>
      <div class="kpis k3">${V.kpi('Кэш спот-портфеля, реальный счёт', real != null ? V.share(real) : '—', M ? V.money(a.spot_cash_usd) : '')}
        ${V.kpi('Цель модели', target != null ? 'не меньше ' + V.share(target, 0) : '—', 'минимум кэша')}
        ${PL && PL.cash_free_slots ? V.kpi('Свободных мест', `${V.int(PL.cash_free_slots.buy)} из ${V.int(PL.cash_free_slots.buy_total)}`, `«можно брать»; на небольшую сумму ${V.int(PL.cash_free_slots.small)} из ${V.int(PL.cash_free_slots.small_total)}`)
          : V.kpi('Кэш модели', sl && sl.cash ? V.share(sl.cash.now_pct) : '—', 'portfolio.json')}</div>
      ${why}</section>`;
  }
})();
