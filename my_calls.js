/* CRYPTO NOVΛ — «Мои прогнозы (Дмитрий)» (19.11.2026): the owner's own paper calls next to what the system says. Owner-only:
   rendered in «Управление» (admin.html), data from GET /v2/admin/my_calls (cnq/api/my_calls.py) — never a public file.
   CNMyCalls.html(D, {msg, err}) → HTML · CNMyCalls.mount(el, api, rerender) — api = {add(form), del(id)} */
(function () {
  'use strict';
  if (window.CNMyCalls) return;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const isN = (v) => v != null && v !== '' && isFinite(+v);
  const grp = (n, d) => { const [i, f] = (+n).toFixed(d).split('.'); return i.replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + (f ? ',' + f : ''); };   // groups the integer part only
  const usd = (v) => (!isN(v) ? '—' : (+v < 0 ? '−' : +v > 0 ? '+' : '') + '$' + grp(Math.abs(+v), 2));
  const pct = (v, d = 2) => (!isN(v) ? '—' : (+v < 0 ? '−' : +v > 0 ? '+' : '') + grp(Math.abs(+v), d) + '%');
  const px = (v) => (!isN(v) ? '—' : +v >= 1000 ? grp(v, 0) : +v >= 1 ? grp(v, 3) : (+v).toPrecision(4).replace('.', ','));
  const dt = (t) => (t ? new Date(+t).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) + ' UTC' : '—');
  const cls = (v) => (+v > 0 ? 'pos' : +v < 0 ? 'neg' : '');
  const DIR = { up: '▲ вверх', down: '▼ вниз', flat: '◆ нейтрально' };
  const CSS = `.mc .mc-f{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;align-items:end;margin:8px 0}.mc label{display:grid;gap:3px;font-size:11.5px;color:var(--muted,#8a94a6)}
.mc input,.mc select{min-height:36px;padding:6px 10px;border-radius:8px;border:1px solid var(--line2,rgba(127,127,127,.4));background:var(--panel2,rgba(127,127,127,.08));color:var(--fg,#e8f0fa);font:inherit}
.mc .mc-c{grid-column:1/-1}.mc .mc-k{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:10px 0}.mc .mc-k>div{border:1px solid var(--line,rgba(127,127,127,.25));border-radius:10px;padding:8px 10px;display:grid;gap:2px}
.mc .mc-k small,.mc .mc-m{color:var(--muted,#8a94a6);font-size:12px;line-height:1.5}.mc .mc-k b{font-size:17px}.mc .pos{color:var(--up,#6fcf97)}.mc .neg{color:var(--dn,#ef7d6b)}
.mc table{width:100%;border-collapse:collapse;font-size:12.5px}.mc th,.mc td{padding:5px 6px;border-bottom:1px solid var(--line,rgba(127,127,127,.18));text-align:left;vertical-align:top}.mc td.r,.mc th.r{text-align:right}
.mc .mc-w{overflow-x:auto}.mc h4{margin:14px 0 4px;font-size:13px}.mc .mc-box{border:1px solid var(--line,rgba(127,127,127,.25));border-radius:10px;padding:8px 10px;margin:8px 0;font-size:12.5px;line-height:1.5}
@media (max-width:640px){.mc .mc-f,.mc .mc-k{grid-template-columns:1fr 1fr}}`;

  function sys(c) {
    const s = c.system;
    return s && s.direction ? `${esc(s.direction)}${s.horizon ? ' · ' + esc(s.horizon) : ''}${s.text ? `<br><span class="mc-m">${esc(s.text)}</span>` : ''}` : '<span class="mc-m">нет данных</span>';
  }
  function rateRows(obj, names) {
    return Object.entries(obj || {}).map(([k, r]) => `<tr><td>${esc((names || {})[k] || k)}</td><td class="r">${r.hits} из ${r.n}</td><td class="r">${r.pct == null ? '—' : grp(r.pct, 0) + '%'}</td><td class="r ${cls(r.pnl_usd)}">${usd(r.pnl_usd)}</td></tr>`).join('');
  }

  function html(D, opts) {
    opts = opts || {};
    const H = (D && D.horizons) || { today: 'до 00:00 UTC', '24h': '24 ч', '3d': '3 дня', '7d': '7 дней' };
    const form = `<form class="mc-f" data-mc="form" autocomplete="off">
      <label>Актив<input name="asset" value="BTC" maxlength="15" required></label>
      <label>Направление<select name="dir"><option value="up">вверх</option><option value="down">вниз</option><option value="flat">нейтрально</option></select></label>
      <label>Срок<select name="horizon">${Object.entries(H).map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join('')}</select></label>
      <div><button class="ib" type="submit">Записать</button></div>
      <label class="mc-c">Комментарий (не обязательно)<input name="comment" maxlength="500" placeholder="почему так думаю"></label></form>`;
    const flash = opts.msg ? `<div class="mc-box" role="status">${esc(opts.msg)}</div>` : '';
    if (!D) return `<style>${CSS}</style><div class="mc">${flash}${form}<p class="mc-m">${esc(opts.err || 'Загружаю…')}</p></div>`;
    const S = D.stats || {}, A = S.all || {}, last = (D.curve || []).slice(-1)[0];
    const open = (D.open || []).map((c) => `<tr><td><b>${esc(c.asset)}</b> ${DIR[c.dir] || esc(c.dir)}<br><span class="mc-m">${esc(H[c.horizon] || c.horizon)} · до ${dt(c.end_t)}</span></td>
      <td>${px(c.entry_px)}<br><span class="mc-m">${dt(c.t)} · ${esc(c.entry_src)}</span>${c.pending_note ? `<br><span class="mc-m">${esc(c.pending_note)}</span>` : ''}</td><td>${sys(c)}</td>
      <td>${c.comment ? esc(c.comment) : '<span class="mc-m">—</span>'}</td><td class="r"><button class="ib" type="button" data-mc="del" data-id="${esc(c.id)}">Удалить</button></td></tr>`).join('');
    const closed = (D.closed || []).map((c) => `<tr><td><b>${esc(c.asset)}</b> ${DIR[c.dir] || esc(c.dir)}<br><span class="mc-m">${esc(H[c.horizon] || c.horizon)} · ${dt(c.t)}</span></td>
      <td>${px(c.entry_px)} → ${px(c.exit_px)}<br><span class="mc-m">${pct(c.move_pct)} · ${esc(c.exit_src || '')}</span></td>
      <td><b class="${c.hit ? 'pos' : 'neg'}">${c.hit ? 'попал' : 'мимо'}</b>${c.band_pct ? `<br><span class="mc-m">коридор ±${grp(c.band_pct, 0)}%</span>` : ''}</td>
      <td class="r ${cls(c.pnl_usd)}">${usd(c.pnl_usd)}</td><td>${sys(c)}</td></tr>`).join('');
    const del = (D.deleted || []).map((c) => `<tr><td>${esc(c.asset)} ${DIR[c.dir] || esc(c.dir)} · ${esc(H[c.horizon] || c.horizon)}</td><td class="mc-m">записан ${dt(c.t)} · удалён ${dt(c.deleted_t)} — до срока</td></tr>`).join('');
    return `<style>${CSS}</style><div class="mc">${flash}<p class="mc-m"><b>${esc(D.label_ru || '')}</b></p>${form}
      <div class="mc-k"><div><small>Попадания</small><b>${A.n ? `${A.hits} из ${A.n}` : '—'}</b><small>${A.pct == null ? 'закрытых прогнозов нет' : grp(A.pct, 0) + '%'}</small></div>
        <div><small>Бумажный итог</small><b class="${cls(A.pnl_usd)}">${usd(A.pnl_usd || 0)}</b><small>$1 000 на прогноз, издержки 0,1%</small></div>
        <div><small>Открыто</small><b>${(D.open || []).length}</b><small>ждут конца срока</small></div>
        <div><small>Удалено до срока</small><b>${(D.deleted || []).length}</b><small>видно ниже</small></div></div>
      ${D.system_ru ? `<p class="mc-m">${esc(D.system_ru)}</p>` : ''}
      <h4>Открытые</h4>${open ? `<div class="mc-w"><table><tr><th>Прогноз</th><th>Цена входа</th><th>Система CRYPTO NOVA AI сказала</th><th>Дмитрий сказал</th><th></th></tr>${open}</table></div>` : '<p class="mc-m">Открытых прогнозов нет.</p>'}
      <h4>Закрытые</h4>${closed ? `<div class="mc-w"><table><tr><th>Прогноз</th><th>Цена</th><th>Итог</th><th class="r">Бумага</th><th>Система сказала</th></tr>${closed}</table></div>` : '<p class="mc-m">Закрытых пока нет.</p>'}
      ${A.n ? `<h4>По активам</h4><div class="mc-w"><table><tr><th>Актив</th><th class="r">Попадания</th><th class="r">%</th><th class="r">Бумага</th></tr>${rateRows(S.by_asset)}</table></div>
      <h4>По срокам</h4><div class="mc-w"><table><tr><th>Срок</th><th class="r">Попадания</th><th class="r">%</th><th class="r">Бумага</th></tr>${rateRows(S.by_horizon, H)}</table></div>
      <p class="mc-m">Накопленный бумажный итог: <b class="${cls(last && last[1])}">${usd(last ? last[1] : 0)}</b> за ${A.n} прогноз(а/ов).</p>` : ''}
      ${del ? `<h4>Удалённые до срока</h4><div class="mc-w"><table>${del}</table></div>` : ''}
      <div class="mc-box">${esc(D.rule_ru || '')}</div></div>`;
  }

  function mount(el, api, rerender) {
    const f = el.querySelector('[data-mc="form"]');
    if (f) f.addEventListener('submit', async (e) => {
      e.preventDefault();
      const b = f.querySelector('[type="submit"]'), d = new FormData(f);
      b.disabled = true;
      try { const r = await api.add({ asset: String(d.get('asset') || 'BTC').trim(), dir: d.get('dir'), horizon: d.get('horizon'), comment: String(d.get('comment') || '').trim() });
        rerender(r && r.open ? r : null, (r && r.message_ru) || ''); }
      catch (err) { rerender(null, (err && err.data && err.data.message_ru) || 'Не записалось: ' + ((err && err.message) || err)); }
      b.disabled = false;
    });
    el.querySelectorAll('[data-mc="del"]').forEach((b) => b.addEventListener('click', async () => {
      if (!window.confirm('Удалить прогноз до срока? Изменить его нельзя; удалённый останется в списке с пометкой.')) return;
      b.disabled = true;
      try { const r = await api.del(b.dataset.id); rerender(r && r.open ? r : null, (r && r.message_ru) || ''); }
      catch (err) { rerender(null, (err && err.data && err.data.message_ru) || 'Не удалилось: ' + ((err && err.message) || err)); }
    }));
  }
  window.CNMyCalls = { html, mount };
})();
