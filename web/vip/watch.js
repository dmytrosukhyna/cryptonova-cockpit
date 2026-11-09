/* CRYPTO NOVΛ VIP — «Под наблюдением владельца» (09.11.2026, owner 21:40): ADA, AVAX, LIT on top of «Спот», owner views only.
   Data: web/owner_watch.json (cnq/agents/owner_watch.py, every pipeline run). Information only — no buy/sell advice. */
(function () {
  'use strict';
  const V = window.VIP, esc = V.esc, A = V.arr;
  const px = (p) => (V.isNum(p) ? '$' + V.num(p, +p < 1 ? 4 : 2) : '—');
  const big = (u) => (V.isNum(u) ? (u >= 1e6 ? '$' + V.num(u / 1e6, 1) + 'M' : '$' + V.num(u / 1e3, 0) + 'K') : '—');
  const stood = (since, asof) => { const h = (asof - since) / 3600000; return h < 0.6 ? 'новая' : h < 24 ? 'стоит ' + Math.round(h) + ' ч' : 'стоит ' + V.num(h / 24, 1) + ' д'; };

  function wallRows(ws, asof) {
    return A(ws).slice(0, 3).map((w) => `<li><b>${big(w.usd)}</b> на ${px(w.price)} <span class="muted">(${V.pct(w.dist)} · ${esc(w.venue)} · ${stood(w.since, asof)})</span></li>`).join('')
      || '<li class="muted">нет данных стакана</li>';
  }

  function coinCard(c) {
    const lv = (n) => (V.isNum(c['sma' + n]) ? `${n}-дн. ${px(c['sma' + n])} <b class="${c['dist' + n] >= 0 ? 'pos' : 'neg'}">${V.pct(c['dist' + n])}</b>` : `${n}-дн. —`);
    const fund = Object.entries(c.funding || {}).map(([k, v]) => `${esc(k)} ${V.pct(v, 4)}`).join(' · ') || '—';
    const tr = A(c.traders).slice(0, 3).map((t) => `${esc(t.who)}: ${t.side === 'LONG' ? 'лонг' : 'шорт'} ${big(t.usd)}`).join('; ');
    const sm = A(c.smart).map((s) => `${esc(s.status || '—')}${s.last ? ' — ' + esc(s.last) : ''}`).join('; ');
    const un = A(c.unlocks).map((u) => `${esc(u.text_ru)} <a href="${esc(u.link)}" target="_blank" rel="noopener">${esc(u.source)}</a>`).join('<br>');
    const nw = A(c.news).map((n) => `<li>${esc(n.title)} — <a href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.source)}</a>${n.t ? ' <span class="muted">' + esc(V.dt(n.t)) + '</span>' : ''}</li>`).join('');
    return `<div class="ow-coin">
      <div class="ow-h"><b>${esc(c.coin)}</b> <span class="muted">${esc(c.name || '')}</span><span class="sp"></span><b>${px(c.price)}</b>
        <span class="${c.chg24 >= 0 ? 'pos' : 'neg'}">${V.pct(c.chg24)} за 24 ч</span> · <span class="${c.chg7 >= 0 ? 'pos' : 'neg'}">${V.pct(c.chg7)} за 7 д</span></div>
      <p class="para">До средних: ${lv(20)} · ${lv(50)} · ${lv(200)}</p>
      <div class="grid g2"><div><span class="eyebrow">Полки покупателя (±5%)</span><ul class="ow-l">${wallRows(c.bid_walls, c.asof)}</ul></div>
        <div><span class="eyebrow">Стены продавца (±5%)</span><ul class="ow-l">${wallRows(c.ask_walls, c.asof)}</ul></div></div>
      <p class="para">Фандинг (за 8 ч): ${fund}${V.isNum(c.oi_chg24) ? ` · открытый интерес OKX ${big(c.oi_usd)}, за сутки ${V.pct(c.oi_chg24)}` : ''}</p>
      ${tr ? `<p class="para">Крупные трейдеры в позиции: ${tr}</p>` : ''}
      ${sm ? `<p class="para">Умные кошельки: ${sm}</p>` : ''}
      ${un ? `<p class="para">Разблокировки: ${un}</p>` : ''}
      ${nw ? `<span class="eyebrow">Новости (с источником)</span><ul class="ow-l">${nw}</ul>` : ''}
      ${A(c.errors).length ? `<p class="note">Не загрузилось: ${esc(A(c.errors).join('; '))}</p>` : ''}
    </div>`;
  }

  V.ownerWatchCard = async function () {
    const [D] = await V.feeds(['owner_watch.json']);
    if (!D || !A(D.coins).length) return `<section class="card" style="margin-bottom:var(--gap)"><div class="card-h"><h3>Под наблюдением владельца</h3></div>${V.empty('Сводка ещё не собрана (web/owner_watch.json появится после ближайшего прогона данных).')}</section>`;
    const al = A(D.alerts).slice(0, 6).map((a) => `<li><span class="muted">${esc(V.dt(a.t))}</span> ${esc(a.text)}</li>`).join('');
    return `<section class="card" style="margin-bottom:var(--gap)" data-owner="watch">
      <div class="card-h"><h3>Под наблюдением владельца: ADA, AVAX, LIT</h3>${V.chip('только владелец', 'test')}<span class="sp"></span>${V.stamp(D.asof, 90)}</div>
      ${al ? `<span class="eyebrow">События</span><ul class="ow-l">${al}</ul>` : '<p class="note">Событий пока нет: полки от $1M, пробои средних, покупки крупных трейдеров и умных кошельков появятся здесь.</p>'}
      ${A(D.coins).map(coinCard).join('')}
      <p class="note">${esc(D.about_ru || '')}</p></section>`;
  };
})();
