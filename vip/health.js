/* CRYPTO NOVΛ VIP — «Состояние системы» for the owner (09.11.2026, owner 21:40: «я сам ничего не понимаю, привести в порядок все
   сервисы»): one row per service in plain Russian — what it does, when it last ran, 🟢 / 🟡 / 🔴 and what to do when red.
   Data: web/owner_health.json (cnq/agents/owner_health.py). Owner views only, at the very top of «Обзор». */
(function () {
  'use strict';
  const V = window.VIP, esc = V.esc, A = V.arr;
  V.ownerHealthCard = async function () {
    const D = await V.sealed('owner_health');
    // the API is down or the file is not there: one calm public-safe line, no details
    if (!D || !A(D.rows).length) return `<section class="card" style="margin-bottom:var(--gap)" data-owner="health"><div class="card-h"><h3>Состояние системы</h3></div><p class="note">Данные временно недоступны.</p></section>`;
    const rows = A(D.rows).map((r) => `<tr class="${r.status === 'fail' ? 'bad' : ''}">
        <td class="hl-dot">${esc(r.icon)} ${esc(r.word)}</td>
        <td class="l"><b>${esc(r.name)}</b><div class="muted">${esc(r.what)}</div>${r.note ? `<div>${esc(r.note)}</div>` : ''}${r.todo ? `<div><b>Что делать:</b> ${esc(r.todo)}</div>` : ''}</td>
        <td class="hide-sm">${r.last ? esc(V.dt(r.last)) + '<div class="muted">' + esc(V.ago(r.last)) + '</div>' : '—'}</td></tr>`).join('');
    return `<section class="card" style="margin-bottom:var(--gap)" data-owner="health">
      <div class="card-h"><h3>${esc(D.icon || '')} Состояние системы</h3>${V.chip('только владелец', 'test')}<span class="sp"></span>${V.stamp(D.asof, 90)}</div>
      <div class="tbl-wrap"><table class="tbl hl-tbl"><thead><tr><th>Статус</th><th class="l">Сервис</th><th class="hide-sm">Последний запуск</th></tr></thead>
      <tbody>${rows}</tbody></table></div><p class="note">${esc(D.about_ru || '')}</p></section>`;
  };
})();
