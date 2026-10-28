/* CRYPTO NOVΛ VIP — «Другие возможности»: arbitrage, funding and the rest in one place; test and real always apart (owner spec §11).
   opportunities.json (data agent) when published; until then the existing watch / demo feeds. */
(function () {
  'use strict';
  const V = window.VIP, esc = V.esc, A = V.arr;
  const ST = { Testing: 'Тестирование', Monitoring: 'Наблюдение', NotActive: 'Не активна', Approved: 'Допущена', Active: 'Активна', Paused: 'Приостановлена' };
  const norm = (s) => {
    const k = String(s || '').toLowerCase().replace(/[\s_-]/g, '');
    return { testing: 'Testing', test: 'Testing', paper: 'Testing', monitoring: 'Monitoring', watch: 'Monitoring', notactive: 'NotActive', inactive: 'NotActive', off: 'NotActive', approved: 'Approved', active: 'Active', live: 'Active', paused: 'Paused' }[k] || 'Testing';
  };
  const NOT_REAL = 'Реальный счёт CRYPTO NOVA торгует только спот, фьючерсы и активный спот — это направление к нему не подключено.';

  V.oppsModel = async function () {
    const [O] = await V.feeds(['opportunities.json']);
    const list = O && (Array.isArray(O) ? O : O.opportunities || O.items);
    if (A(list).length) {
      return { src: 'opportunities.json', asof: V.asofOf(O), items: list.map((x) => {
        const r = x.result || x.results || {};
        const status = norm(x.status);
        const realUsd = V.n(r.real_usd != null ? r.real_usd : x.real_usd);
        return {
          id: String(x.id || x.key || x.name), name: x.name_ru || x.name, desc: x.description_ru || x.about_ru || x.description || '', status, statusRu: ST[status] || status,
          real: x.real_money === true || (status === 'Active' && realUsd != null), realUsd, realPct: V.n(r.real_pct),
          testUsd: V.n(r.test_usd != null ? r.test_usd : x.test_usd), testPct: V.n(r.test_pct), testNote: r.test_note_ru || r.note_ru || '',
          ops: V.n(r.ops != null ? r.ops : r.n != null ? r.n : x.ops), risks: A(x.risks_ru || x.risks), why: x.why_not_used_ru || x.why_not_used || '',
          since: V.ms(x.since), asof: V.ms(x.asof || x.updated),
        };
      }) };
    }
    const [ARB, TRI, FUN, BAS, YLD] = await V.feeds(['arb.json', 'tri.json', 'funding.json', 'basis.json', 'yield.json']);
    const items = [];
    if (TRI) items.push({ id: 'tri', name: 'Треугольный арбитраж', status: 'Monitoring', desc: TRI.explain_ru || '', since: V.ms(TRI.since), asof: V.asofOf(TRI),
      testUsd: V.n(TRI.week && TRI.week.virtual_pnl_usd), testNote: 'виртуально за 7 дней', ops: V.n(TRI.week && TRI.week.windows),
      opsNote: 'окон за 7 дней', risks: [TRI.note || 'Окна живут секунды, в стакане по лучшей цене обычно почти нет объёма; застывшие треугольники — закрытый ввод/вывод или пустой стакан.'], why: NOT_REAL + ' Пока только наблюдение: ордера не ставятся.' });
    if (ARB) items.push({ id: 'arb', name: 'Межбиржевой арбитраж', status: 'Monitoring', desc: ARB.explain_ru || '', since: V.ms(ARB.since), asof: V.asofOf(ARB),
      testUsd: V.n(ARB.week && ARB.week.virtual_pnl_usd), testNote: 'виртуально за 7 дней' + (ARB.week && V.isNum(ARB.week.virtual_pnl_naive_usd) ? ` (без учёта стакана было бы ${V.smoney(ARB.week.virtual_pnl_naive_usd)})` : ''),
      ops: V.n(ARB.week && ARB.week.windows), opsNote: 'окон за 7 дней', risks: [ARB.note || ''].filter(Boolean), why: NOT_REAL + ' Пока только наблюдение: ордера не ставятся.' });
    const demo = (D, id, name) => D && items.push({ id, name: D.name || name, status: 'Testing', desc: D.about || D.about_ru || '', since: V.ms(D.since), asof: V.asofOf(D),
      testUsd: V.n(D.pnl_usd), testPct: V.n(D.pnl_pct), testNote: 'демо-счёт $10 000', ops: V.n(D.breakdown && D.breakdown.entries), opsNote: 'входов',
      risks: [D.note || ''].filter((x) => x && x.length < 400), why: NOT_REAL + ' Работает только на демо-счёте.' });
    demo(FUN, 'funding', 'Фандинг без направления');
    demo(BAS, 'basis', 'Фикс-ставка на фьючерсах');
    demo(YLD, 'yield', 'Доходность стейблкоинов');
    for (const it of items) { it.statusRu = ST[it.status]; it.real = false; it.realUsd = null; }
    return { src: 'fallback', asof: null, items };
  };

  V.views.opps = {
    async html() {
      const OP = await V.oppsModel();
      V.ui.opps = OP;
      const real = OP.items.filter((o) => o.real), test = OP.items.filter((o) => !o.real);
      return `<div class="ph"><div><h2>Другие возможности</h2><p>Арбитраж, фандинг и другие направления, которые система изучает. Тестовый результат — виртуальный и не входит в результат публичного портфеля.</p></div></div>
        <section class="sgroup" data-demo="op-real"><h3>На реальных деньгах <span class="n">${V.int(real.length)}</span></h3>
          ${real.length ? `<div class="scards">${real.map(card).join('')}</div>` : V.empty('Ни одно дополнительное направление сейчас не торгуется реальными деньгами.')}</section>
        <section class="sgroup" data-demo="op-test"><h3>Тест и наблюдение <span class="n">${V.int(test.length)}</span></h3>
          ${test.length ? `<div class="scards">${test.map(card).join('')}</div>` : V.empty('Нет данных.')}</section>
        ${OP.src === 'fallback' ? `<p class="note">Собрано из arb.json, tri.json, funding.json, basis.json и yield.json; opportunities.json с единым списком ещё не опубликован.</p>` : ''}`;
    },
    mount(el) { el.querySelectorAll('[data-oid]').forEach((b) => b.addEventListener('click', () => open(b.dataset.oid))); },
  };

  function card(o) {
    return `<button class="scard ${o.real ? 'realm' : ''}" type="button" data-oid="${esc(o.id)}">
      <div class="scard-h"><b>${esc(o.name)}</b><span class="chips">${V.chip(o.statusRu, 'test')}${o.real ? V.chip('реальные деньги', 'real') : V.chip('тест', 'test')}</span></div>
      <div class="m">
        <div><small>Реальный результат</small><span class="${V.cls(o.realUsd)}">${o.real && o.realUsd != null ? V.smoney(o.realUsd) : 'нет'}</span></div>
        <div><small>Тестовый результат</small><span class="${V.cls(o.testUsd)}">${o.testUsd != null ? V.smoney(o.testUsd) : '—'}</span></div>
        <div><small>Операций</small><span>${o.ops != null ? V.int(o.ops) : '—'}</span></div></div>
      ${o.why ? `<div class="d">${esc(o.why)}</div>` : ''}
      <div class="f">${o.since ? 'с ' + esc(V.date(o.since)) : ''}${o.asof ? ' · обновлено ' + esc(V.dt(o.asof)) : ''}</div>
    </button>`;
  }

  function open(id) {
    const o = (V.ui.opps && V.ui.opps.items.find((x) => x.id === id)); if (!o) return;
    V.sheet(o.name, `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">${V.chip(o.statusRu, 'test')}${o.real ? V.chip('реальные деньги', 'real') : V.chip('не реальные деньги', 'test')}</div>
      ${o.desc ? `<p class="para">${esc(o.desc)}</p>` : ''}
      <div class="kpis k3" style="margin-top:12px">${V.kpi('Реальный результат', o.real && o.realUsd != null ? `<span class="${V.cls(o.realUsd)}">${V.smoney(o.realUsd)}</span>` : 'нет', o.real ? '' : 'реальными деньгами не торгуется')}
        ${V.kpi('Тестовый результат', o.testUsd != null ? `<span class="${V.cls(o.testUsd)}">${V.smoney(o.testUsd)}</span>` : '—', esc(o.testNote || '') + (o.testPct != null ? ' · ' + V.pct(o.testPct) : ''))}
        ${V.kpi('Операций', o.ops != null ? V.int(o.ops) : '—', esc(o.opsNote || ''))}</div>
      ${o.risks.length ? `<div class="sec-t"><h3>Основные риски</h3></div><ul class="bul">${o.risks.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}
      <div class="sec-t"><h3>Почему не используется</h3></div><p class="para">${esc(o.why || 'Причина не записана.')}</p>
      <p class="note">Тестовый результат — виртуальный (демо-счёт или наблюдение) и не входит в результат публичного портфеля.</p>`);
  }
})();
