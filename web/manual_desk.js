/* CRYPTO NOVΛ — «Ручной стол (Дмитрий)», stage 1 (19.11.2026): the owner's semi-manual PAPER desk in «Управление» (admin.html).
   Data: GET/POST /v2/admin/manual* (cnq/api/manual_desk.py) — never a public file, never an exchange order.
   CNManual.html(D, {msg, preview, sym}) → HTML · CNManual.mount(el, api, rerender)
   api = {resolve(q), preview(sym, side), open(sym, side), close(id), stop(id, px), settings(form)} */
(function () {
  'use strict';
  if (window.CNManual) return;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const isN = (v) => v != null && v !== '' && isFinite(+v);
  const grp = (n, d) => { const [i, f] = (+n).toFixed(d).split('.'); return i.replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + (f ? ',' + f : ''); };   // groups the integer part only
  const usd = (v, sign = true) => (!isN(v) ? '—' : (+v < 0 ? '−' : sign && +v > 0 ? '+' : '') + '$' + grp(Math.abs(+v), 2));
  const px = (v) => (!isN(v) ? '—' : +v >= 1000 ? grp(v, 1) : +v >= 1 ? grp(v, 4) : (+v).toPrecision(4).replace('.', ','));
  const pct = (v) => (!isN(v) ? '—' : grp(v, 2) + '%');
  const dt = (t) => (t ? new Date(+t).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) + ' UTC' : '—');
  const cls = (v) => (+v > 0 ? 'pos' : +v < 0 ? 'neg' : '');
  const CSS = `.md .md-top{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:8px;align-items:end}.md label{display:grid;gap:3px;font-size:11.5px;color:var(--muted,#8a94a6)}
.md input,.md select{min-height:40px;padding:6px 10px;border-radius:8px;border:1px solid var(--line2,rgba(127,127,127,.4));background:var(--panel2,rgba(127,127,127,.08));color:var(--fg,#e8f0fa);font:inherit}
.md .md-b{min-height:48px;min-width:110px;border-radius:12px;border:0;font:700 16px inherit;letter-spacing:.06em;cursor:pointer;color:#06101e}.md .md-l{background:var(--up,#6fcf97)}.md .md-s{background:var(--dn,#ef7d6b)}
.md .md-m{color:var(--muted,#8a94a6);font-size:12px;line-height:1.5}.md .pos{color:var(--up,#6fcf97)}.md .neg{color:var(--dn,#ef7d6b)}
.md .md-box{border:1px solid var(--line,rgba(127,127,127,.25));border-radius:12px;padding:10px 12px;margin:10px 0;font-size:13px;line-height:1.55}
.md .md-pv{border-color:var(--amber,#f2c14e)}.md .md-k{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;margin:10px 0}.md .md-k>div{border:1px solid var(--line,rgba(127,127,127,.25));border-radius:10px;padding:8px 10px;display:grid;gap:2px}
.md .md-k small{color:var(--muted,#8a94a6);font-size:11.5px}.md .md-k b{font-size:16px}.md table{width:100%;border-collapse:collapse;font-size:12.5px}.md th,.md td{padding:5px 6px;border-bottom:1px solid var(--line,rgba(127,127,127,.18));text-align:left;vertical-align:top}
.md td.r,.md th.r{text-align:right}.md .md-w{overflow-x:auto}.md h4{margin:14px 0 4px;font-size:13px}.md .md-set{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-top:8px}
.md .md-strip{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}.md .md-strip span{border:1px solid var(--line,rgba(127,127,127,.25));border-radius:999px;padding:3px 10px;font-size:12px}
@media (max-width:640px){.md .md-top{grid-template-columns:1fr 1fr}.md .md-top label{grid-column:1/-1}.md .md-k,.md .md-set{grid-template-columns:1fr 1fr}}`;

  function previewBox(P) {
    if (!P) return '';
    const L = P.side === 'long';
    return `<div class="md-box md-pv"><b>${esc(P.sym)} ${L ? 'LONG' : 'SHORT'} — проверьте и подтвердите</b><br>
      Вход ≈ ${px(P.entry)} <span class="md-m">(цена ${px(P.last)} + проскальзывание 0,05%)</span><br>
      Стоп <b>${px(P.stop)}</b> <span class="md-m">−${pct(P.stop_dist_pct)} · ${esc(P.stop_why)}</span><br>
      Размер ${usd(P.notional, false)} · плечо ${grp(P.lev, 2)}× · риск <b>${usd(P.risk_usd, false)}</b> из ${usd(P.equity, false)} · комиссии ≈ ${usd(P.fees_est, false)}<br>
      Тейки: ${(P.tps || []).map((t, i) => `TP${i + 1} ${px(t.px)} (${grp(t.r, 1)}R, ${grp(t.pct, 0)}%)`).join(' · ')}<br>
      <span class="md-m">${P.settings && P.settings.be_after_tp1 ? 'После TP1 стоп в безубыток. ' : ''}${P.settings && P.settings.trail_after_tp2 ? 'После TP2 — трейлинг на расстоянии первого стопа. ' : ''}${esc(P.note_ru || '')}</span>
      <div style="margin-top:8px;display:flex;gap:8px"><button class="md-b ${L ? 'md-l' : 'md-s'}" type="button" data-md="confirm">Подтвердить</button><button class="ib" type="button" data-md="cancel">Отмена</button></div></div>`;
  }

  function html(D, o) {
    o = o || {};
    const S = (D && D.settings) || {}, st = (D && D.stats) || {};
    const top = `<div class="md-top"><label>Монета (OKX, бессрочный USDT)<input data-md="q" value="${esc(o.sym || '')}" placeholder="ADA или Cardano" maxlength="40" autocomplete="off"></label>
      <button class="md-b md-l" type="button" data-md="long">LONG</button><button class="md-b md-s" type="button" data-md="short">SHORT</button></div>`;
    const flash = o.msg ? `<div class="md-box" role="status">${esc(o.msg)}</div>` : '';
    if (!D) return `<style>${CSS}</style><div class="md">${top}${flash}${previewBox(o.preview)}<p class="md-m">${esc(o.err || 'Загружаю…')}</p></div>`;
    const op = D.open || [];
    const strip = op.length ? `<div class="md-strip">${op.map((p) => `<span>${esc(p.sym)} ${p.side === 'long' ? '▲' : '▼'} <b class="${cls(p.upnl)}">${usd(p.upnl)}</b></span>`).join('')}</div>` : '';
    const rows = op.map((p) => `<tr><td><b>${esc(p.sym)}</b> ${p.side === 'long' ? '▲ LONG' : '▼ SHORT'}<br><span class="md-m">${dt(p.t)} · ${grp(p.lev, 2)}×</span></td>
      <td>${px(p.entry)} → ${px(p.mark)}</td><td>стоп ${px(p.stop)}${p.trail ? ' <span class="md-m">трейлинг</span>' : ''}<br><span class="md-m">${(p.tps || []).map((t, i) => (t.done ? '✓' : '') + 'TP' + (i + 1) + ' ' + px(t.px)).join(' · ')}</span></td>
      <td class="r ${cls(p.upnl)}">${usd(p.upnl)}<br><span class="md-m">реализовано ${usd(p.realised - p.fees)}</span></td>
      <td class="r"><button class="ib" type="button" data-md="close" data-id="${esc(p.id)}">Закрыть</button> <button class="ib" type="button" data-md="stop" data-id="${esc(p.id)}" data-px="${esc(p.stop)}">Стоп…</button></td></tr>`).join('');
    const sysTxt = (c) => (c.system && c.system.direction ? esc(c.system.direction) : '<span class="md-m">нет данных</span>');
    const cl = (D.closed || []).slice(0, 30).map((c) => `<tr><td><b>${esc(c.sym)}</b> ${c.side === 'long' ? '▲' : '▼'} <span class="md-m">${dt(c.t)} → ${dt(c.closed_t)}</span></td>
      <td>${esc(c.why)}</td><td class="r ${cls(c.pnl_usd)}">${usd(c.pnl_usd)}</td><td class="r">${isN(c.r_mult) ? grp(c.r_mult, 2) + 'R' : '—'}</td><td>${sysTxt(c)}</td></tr>`).join('');
    const set = `<details class="md-box"><summary><b>Настройки</b> <span class="md-m">риск ${grp(S.risk_pct || 0, 1)}% · стоп ${esc(S.stop_rule)} · тейки ${(S.tp_r || []).join('/')}R · плечо до ${grp(S.max_lev || 0, 0)}×</span></summary>
      <form class="md-set" data-md="set"><label>Риск на сделку, %<input name="risk_pct" type="number" step="0.1" min="0.1" max="10" value="${esc(S.risk_pct)}"></label>
      <label>Правило стопа<select name="stop_rule">${[['swing_h1', 'за уровнем H1'], ['swing_h4', 'за уровнем H4'], ['pct', 'фиксированный %']].map(([k, v]) => `<option value="${k}"${S.stop_rule === k ? ' selected' : ''}>${v}</option>`).join('')}</select></label>
      <label>Стоп %, если без уровня<input name="stop_pct" type="number" step="0.1" min="0.3" max="16" value="${esc(S.stop_pct)}"></label>
      <label>Макс. стоп, %<input name="stop_max_pct" type="number" step="0.5" min="1" max="16" value="${esc(S.stop_max_pct)}"></label>
      <label>Тейки, R через запятую<input name="tp_r" value="${esc((S.tp_r || []).join(', '))}"></label>
      <label>Доли тейков, %<input name="tp_split" value="${esc((S.tp_split || []).join(', '))}"></label>
      <label>Макс. плечо<input name="max_lev" type="number" step="0.5" min="1" max="5" value="${esc(S.max_lev)}"></label>
      <label>После TP1 / TP2<select name="mgmt"><option value="both"${S.be_after_tp1 && S.trail_after_tp2 ? ' selected' : ''}>безубыток + трейлинг</option><option value="be"${S.be_after_tp1 && !S.trail_after_tp2 ? ' selected' : ''}>только безубыток</option><option value="none"${!S.be_after_tp1 && !S.trail_after_tp2 ? ' selected' : ''}>ничего</option></select></label>
      <div><button class="ib" type="submit">Сохранить</button></div></form></details>`;
    return `<style>${CSS}</style><div class="md"><p class="md-m"><b>${esc(D.label_ru || '')}</b></p>${top}${strip}${flash}${previewBox(o.preview)}
      <div class="md-k"><div><small>Демо-счёт</small><b>${usd(D.equity, false)}</b><small>старт ${usd(D.start, false)}</small></div>
        <div><small>Сделок</small><b>${st.trades || 0}</b><small>в плюсе ${st.win_pct == null ? '—' : grp(st.win_pct, 0) + '%'}</small></div>
        <div><small>Средний R</small><b>${isN(st.avg_r) ? grp(st.avg_r, 2) : '—'}</b><small>итог в рисках</small></div>
        <div><small>Итог</small><b class="${cls(st.pnl_usd)}">${usd(st.pnl_usd || 0)}</b><small>после комиссий</small></div>
        <div><small>Макс. просадка</small><b>${pct(st.max_dd_pct || 0)}</b><small>по закрытым</small></div></div>
      <h4>Открытые позиции</h4>${rows ? `<div class="md-w"><table><tr><th>Позиция</th><th>Вход → сейчас</th><th>Стоп и тейки</th><th class="r">Результат</th><th></th></tr>${rows}</table></div>` : '<p class="md-m">Открытых позиций нет.</p>'}
      <h4>Закрытые</h4>${cl ? `<div class="md-w"><table><tr><th>Сделка</th><th>Выход</th><th class="r">Итог</th><th class="r">R</th><th>Что говорила система</th></tr>${cl}</table></div>` : '<p class="md-m">Закрытых пока нет.</p>'}
      ${set}<p class="md-m">${esc(D.stage_ru || '')}</p></div>`;
  }

  function mount(el, api, rerender) {
    const q = el.querySelector('[data-md="q"]');
    const err = (e) => (e && e.data && e.data.message_ru) || 'Не получилось: ' + ((e && e.message) || e);
    el.querySelectorAll('[data-md="long"],[data-md="short"]').forEach((b) => b.addEventListener('click', async () => {
      const sym = (q.value || '').trim();
      if (!sym) { rerender(null, 'Впишите монету.', { sym }); return; }
      b.disabled = true;
      try { const P = await api.preview(sym, b.dataset.md); rerender(null, '', { preview: P, sym: P.sym }); }
      catch (e) { rerender(null, err(e), { sym }); }
      b.disabled = false; }));
    const c = el.querySelector('[data-md="confirm"]');
    if (c) c.addEventListener('click', async () => {
      const P = el.__preview; if (!P) return; c.disabled = true;
      try { const r = await api.open(P.sym, P.side); rerender(r, r.message_ru || '', { sym: P.sym }); }
      catch (e) { rerender(null, err(e), { sym: P.sym }); } });
    const x = el.querySelector('[data-md="cancel"]');
    if (x) x.addEventListener('click', () => rerender(null, '', { sym: el.__preview && el.__preview.sym }));
    el.querySelectorAll('[data-md="close"]').forEach((b) => b.addEventListener('click', async () => {
      if (!window.confirm('Закрыть позицию по рынку (на бумаге)?')) return;
      b.disabled = true; try { const r = await api.close(b.dataset.id); rerender(r, r.message_ru || ''); } catch (e) { rerender(null, err(e)); } }));
    el.querySelectorAll('[data-md="stop"]').forEach((b) => b.addEventListener('click', async () => {
      const v = window.prompt('Новый стоп (цена):', b.dataset.px); if (!v) return;
      try { const r = await api.stop(b.dataset.id, parseFloat(String(v).replace(',', '.'))); rerender(r, r.message_ru || ''); } catch (e) { rerender(null, err(e)); } }));
    const f = el.querySelector('[data-md="set"]');
    if (f) f.addEventListener('submit', async (e) => {
      e.preventDefault(); const d = new FormData(f), num = (s) => String(s || '').split(/[,;\s]+/).filter(Boolean).map((v) => parseFloat(v.replace(',', '.')));
      const m = d.get('mgmt');
      try { const r = await api.settings({ risk_pct: +d.get('risk_pct'), stop_rule: d.get('stop_rule'), stop_pct: +d.get('stop_pct'), stop_max_pct: +d.get('stop_max_pct'),
        tp_r: num(d.get('tp_r')), tp_split: num(d.get('tp_split')), max_lev: +d.get('max_lev'), be_after_tp1: m !== 'none', trail_after_tp2: m === 'both' });
        rerender(r, r.message_ru || ''); } catch (er) { rerender(null, err(er)); } });
  }
  window.CNManual = { html, mount };
})();
