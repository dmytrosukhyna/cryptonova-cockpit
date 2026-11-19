/* CRYPTO NOVΛ — «Торговля от уровней · CRYPTO NOVA AI (тест)» (20.11.2026): the automatic PAPER desk's card in «Управление», under
   «Ручной стол (Дмитрий)». Data: GET /v2/admin/levels (cnq/api/levels.py, the owner's check) — never a public file, never an order.
   CNLevels.html(D, {err, starting}) → HTML · CNLevels.mount(el, {repeat(coin, side)}) · CNLevels.lines(D) → chart lines */
(function () {
  'use strict';
  if (window.CNLevels) return;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const isN = (v) => v != null && v !== '' && isFinite(+v);
  const grp = (n, d) => { const [i, f] = (+n).toFixed(d).split('.'); return i.replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + (f ? ',' + f : ''); };
  const usd = (v) => (!isN(v) ? '—' : (+v < 0 ? '−' : +v > 0 ? '+' : '') + '$' + grp(Math.abs(+v), 2));
  const px = (v) => (!isN(v) ? '—' : +v >= 1000 ? grp(v, 1) : +v >= 1 ? grp(v, 4) : (+v).toPrecision(4).replace('.', ','));
  const pct = (v) => (!isN(v) ? '—' : (+v > 0 ? '+' : +v < 0 ? '−' : '') + grp(Math.abs(+v), 2) + '%');
  const dt = (t) => (t ? new Date(+t).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) + ' UTC' : '—');
  const cls = (v) => (+v > 0 ? 'pos' : +v < 0 ? 'neg' : '');
  const SIDE = { L: 'лонг', S: 'шорт' };
  // the mode of the desk's variant — every trade of the desk is of that mode
  const MODE = { range: 'диапазон', bo_vol: 'пробой', bo_retest: 'пробой', crash: 'пробой', fbo1: 'ложный пробой', fbo3: 'ложный пробой' };

  /** the variant in plain words, e.g. «шорт после пробоя поддержки на ретесте, короткое удержание» */
  function plain(mode, side, exit) {
    const S = side === 'S', who = S ? 'шорт' : 'лонг';
    const what = {
      range: S ? 'от верхней границы диапазона' : 'от нижней границы диапазона',
      bo_vol: S ? 'на пробое поддержки с объёмом' : 'на пробое сопротивления с объёмом',
      bo_retest: S ? 'после пробоя поддержки на ретесте' : 'после пробоя сопротивления на ретесте',
      crash: 'на резком пробое вниз с большим объёмом',
      fbo1: S ? 'на ложном пробое сопротивления (возврат за 1 свечу)' : 'на ложном пробое поддержки (возврат за 1 свечу)',
      fbo3: S ? 'на ложном пробое сопротивления (возврат за 3 свечи)' : 'на ложном пробое поддержки (возврат за 3 свечи)',
    }[mode] || mode;
    return `${who} ${what}, ${exit === 'asym' ? 'короткое удержание' : 'удержание до цели'}`;
  }

  function curve(closed) {
    const cl = (closed || []).slice().sort((a, b) => a.closed_t - b.closed_t);
    if (cl.length < 2) return '';
    let s = 0; const pts = cl.map((c) => [c.closed_t, (s += +c.usd || 0)]);
    pts.unshift([cl[0].opened_t || cl[0].closed_t, 0]);
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), lo = Math.min(0, ...ys), hi = Math.max(0, ...ys), H = 70, W = 300;
    const X = (t) => ((t - x0) / Math.max(1, x1 - x0)) * W, Y = (v) => H - 4 - ((v - lo) / Math.max(1e-9, hi - lo)) * (H - 8);
    return `<svg class="lv-c" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Итог по закрытым сделкам">
      <line x1="0" x2="${W}" y1="${Y(0)}" y2="${Y(0)}" stroke="var(--line2,rgba(127,127,127,.4))" stroke-dasharray="4 4" vector-effect="non-scaling-stroke"/>
      <path d="${pts.map((p, i) => (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ' ' + Y(p[1]).toFixed(1)).join(' ')}" fill="none" stroke="var(--acc,#7cc4ff)" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
  }

  const CSS = `.lv .lv-m{color:var(--muted,#8a94a6);font-size:12px;line-height:1.5}.lv .pos{color:var(--up,#6fcf97)}.lv .neg{color:var(--dn,#ef7d6b)}
.lv .lv-box{border:1px solid var(--line,rgba(127,127,127,.25));border-radius:10px;padding:8px 10px;margin:8px 0;font-size:12.5px;line-height:1.5}.lv .lv-bad{border-color:var(--amber,#f2c14e)}
.lv .lv-k{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;margin:8px 0}.lv .lv-k>div{border:1px solid var(--line,rgba(127,127,127,.25));border-radius:10px;padding:8px 10px;display:grid;gap:2px}
.lv .lv-k small{color:var(--muted,#8a94a6);font-size:11.5px}.lv .lv-k b{font-size:16px}.lv table{width:100%;border-collapse:collapse;font-size:12.5px}.lv th,.lv td{padding:5px 6px;border-bottom:1px solid var(--line,rgba(127,127,127,.18));text-align:left;vertical-align:top}
.lv td.r,.lv th.r{text-align:right}.lv .lv-w{overflow-x:auto}.lv h4{margin:14px 0 4px;font-size:13px}.lv .lv-c{width:100%;height:70px;display:block}
@media (max-width:640px){.lv .lv-k{grid-template-columns:1fr 1fr}}`;

  function html(D, o) {
    o = o || {};
    if (!D) return `<style>${CSS}</style><div class="lv"><p class="lv-m">${esc(o.starting ? 'Стол запускается — первая проверка рынка раз в 10 минут, данные появятся после неё.' : (o.err || 'Загружаю…'))}</p></div>`;
    const B = D.backtest || {}, mode = MODE[D.mode] || D.mode;
    const cl = D.closed || [], wins = D.wins || 0, n = D.trades || 0;
    const btn = (c, s) => `<button class="ib" type="button" data-lv="repeat" data-coin="${esc(c)}" data-side="${s === 'S' ? 'short' : 'long'}">Повторить вручную</button>`;
    const open = (D.open || []).map((t) => `<tr><td><b>${esc(t.coin)}</b> ${SIDE[t.side] || esc(t.side)}<br><span class="lv-m">${esc(mode)} · ${dt(t.opened_t)}</span></td>
      <td>${px(t.entry)}<br><span class="lv-m">уровень ${px(t.level)}</span></td><td>стоп ${px(t.stop)}<br>цель ${px(t.tgt)}</td>
      <td class="r">$${grp(t.notional || 0, 0)}<br><span class="lv-m">риск $${grp(t.risk_usd || 0, 2)}</span></td><td class="r">${btn(t.coin, t.side)}</td></tr>`).join('');
    const pend = (D.pending || []).map((p) => `<tr><td><b>${esc(p.coin)}</b> ${SIDE[p.side] || esc(p.side)}</td><td>лимит ${px(p.px)}</td><td class="lv-m">ждёт ретеста с ${dt(p.t)} (до 6 ч)</td><td class="r">${btn(p.coin, p.side)}</td></tr>`).join('');
    const closed = cl.slice(0, 30).map((t) => `<tr><td><b>${esc(t.coin)}</b> ${SIDE[t.side] || esc(t.side)}<br><span class="lv-m">${esc(mode)} · ${dt(t.opened_t)} → ${dt(t.closed_t)}</span></td>
      <td>${px(t.entry)} → ${px(t.exit)}</td><td>${esc(t.why || '')}</td><td class="r ${cls(t.usd)}">${usd(t.usd)}<br><span class="lv-m">${pct(t.pct)}</span></td></tr>`).join('');
    const oos = B.oos || {}, is_ = B.is || {};
    return `<style>${CSS}</style><div class="lv"><p class="lv-m"><b>ТЕСТ · бумажный стол системы CRYPTO NOVA AI, $${grp(D.start || 10000, 0)}. Сделок на бирже нет, на реальный счёт ничего не попадает.</b></p>
      <div class="lv-box"><b>Вариант:</b> ${esc(plain(D.mode, D.side, D.exit))} <span class="lv-m">(${esc(D.variant)}, режим — ${esc(mode)})</span><br>${esc(D.rule_ru || '')}</div>
      ${B.verdict_ru ? `<div class="lv-box lv-bad"><b>Проверка на истории: не прошёл — работает как наблюдение.</b> ${esc(B.verdict_ru)}.<br>
        <span class="lv-m">Вторая половина: ${grp(oos.n || 0, 0)} сделок, в среднем ${pct(oos.mean)} на сделку, медиана ${pct(oos.median)}, в плюсе ${isN(oos.win) ? grp(oos.win * 100, 0) + '%' : '—'}, худшая ${pct(oos.worst)}. Первая половина: в среднем ${pct(is_.mean)}. ${esc(B.pass_rule_ru || '')}. Прошлое не гарантирует будущее.</span></div>` : ''}
      <div class="lv-k"><div><small>Бумажный счёт</small><b>$${grp(D.equity || 0, 2)}</b><small class="${cls(D.pnl_usd)}">${usd(D.pnl_usd)}</small></div>
        <div><small>Сделок</small><b>${n}</b><small>с ${dt(D.started).slice(0, 5)}</small></div><div><small>В плюсе</small><b>${n ? grp(wins / n * 100, 0) + '%' : '—'}</b><small>${wins} из ${n}</small></div>
        <div><small>Открыто</small><b>${(D.open || []).length}</b><small>ждут ретеста: ${(D.pending || []).length}</small></div><div><small>Данные</small><b>${dt(D.asof).slice(0, 12)}</b><small>раз в 10 мин</small></div></div>
      ${curve(cl)}${(D.errors || []).length ? `<p class="lv-m">Ошибки последней проверки: ${esc(D.errors.slice(0, 3).join('; '))}</p>` : ''}
      <h4>Открытые</h4>${open ? `<div class="lv-w"><table><tr><th>Сделка</th><th>Вход</th><th>Стоп / цель</th><th class="r">Размер</th><th></th></tr>${open}</table></div>` : '<p class="lv-m">Открытых нет.</p>'}
      ${pend ? `<h4>Ждут ретеста</h4><div class="lv-w"><table>${pend}</table></div>` : ''}
      <h4>Закрытые</h4>${closed ? `<div class="lv-w"><table><tr><th>Сделка</th><th>Цена</th><th>Выход</th><th class="r">Итог</th></tr>${closed}</table></div>` : '<p class="lv-m">Закрытых пока нет.</p>'}
      <p class="lv-m">«Повторить вручную» открывает в «Ручном столе» ту же монету и сторону с вашими правилами — решение и подтверждение остаются за вами.</p></div>`;
  }

  function mount(el, o) {
    el.querySelectorAll('[data-lv="repeat"]').forEach((b) => b.addEventListener('click', () => { if (o && o.repeat) o.repeat(b.dataset.coin, b.dataset.side); }));
  }
  /** the desk's open positions as chart lines for the shared chart of «Ручной стол» */
  function lines(D) {
    const out = [];
    ((D && D.open) || []).forEach((t) => { out.push({ sym: t.coin, px: t.entry, c: '#b8a7ff', t: 'уровни: вход ' + (SIDE[t.side] || '') }, { sym: t.coin, px: t.stop, c: '#b8a7ff', t: 'уровни: стоп', dash: true },
      { sym: t.coin, px: t.tgt, c: '#b8a7ff', t: 'уровни: цель' }); });
    return out;
  }
  window.CNLevels = { html, mount, lines, plain };
})();
