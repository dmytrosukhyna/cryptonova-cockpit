/* CRYPTO NOVΛ VIP — «Стратегии сегодня» (owner 17.11 20:10: «на телефоне — сверху ОДИН список: каждая стратегия одной строкой,
   результат за сегодня $ и %, сколько открыто, тап — подробности»). Owner views only (vip.html «Фьючерсы»).
   CNStratToday.rows(src, now) → [{key, name, tag, today, open, note, lines, link}]   ·   CNStratToday.html(rows, opts) → HTML
   Honest by construction — every «сегодня» says what it is:
   · «с 00:00 UTC» = the change of the account / the desk's value since 00:00 UTC (open positions included) — from a curve that
     reaches back to 00:00; a curve that starts later says «с HH:MM»;
   · «закрыто сегодня» = the sum of the trades closed since 00:00 UTC (no curve to measure the open part's change);
   · «—» = there is no figure for today (no %, when there is no base for it). Nothing is estimated. */
(function () {
  'use strict';
  if (window.CNStratToday) return;
  const DAY = 86400000;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const isN = (v) => v != null && v !== '' && isFinite(+v);
  const grp = (n, d) => (+n).toFixed(d).replace('.', ',').replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  const usd = (v) => (!isN(v) ? '—' : (+v < 0 ? '−' : +v > 0 ? '+' : '') + '$' + grp(Math.abs(+v), Math.abs(+v) < 100 ? 2 : 0));
  const pct = (v) => (!isN(v) ? '' : (+v < 0 ? '−' : +v > 0 ? '+' : '') + grp(Math.abs(+v), 2) + '%');
  const hm = (t) => new Date(t).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' });
  const r2 = (v) => Math.round(v * 100) / 100;
  const sideRu = (s) => (String(s || '').toUpperCase() === 'SHORT' ? 'шорт' : 'лонг');

  /** the change of a value curve [[t, v]] since 00:00 UTC (from its first point when it starts later) → {usd, pct, label} */
  function sinceDay(curve, now) {
    const c = (curve || []).filter((p) => Array.isArray(p) && isN(p[0]) && isN(p[1])).map((p) => [+p[0], +p[1]]).sort((a, b) => a[0] - b[0]);
    if (c.length < 2) return null;
    const day0 = now - (now % DAY);
    let base = null, from = day0;
    for (const p of c) { if (p[0] <= day0) base = p[1]; else break; }
    if (base == null) { base = c[0][1]; from = c[0][0]; }
    const last = c[c.length - 1][1];
    return { usd: r2(last - base), pct: base ? r2((last - base) / base * 100) : null, label: from === day0 ? 'с 00:00 UTC' : 'с ' + hm(from) + ' UTC' };
  }
  /** the sum of trades closed since 00:00 UTC → {usd, n, label} */
  function closedToday(list, tKey, pKey, now) {
    const day0 = now - (now % DAY);
    const xs = (list || []).filter((c) => c && isN(c[tKey]) && +c[tKey] >= day0);
    return { usd: r2(xs.reduce((a, c) => a + (+c[pKey] || 0), 0)), pct: null, n: xs.length, label: 'закрыто сегодня' + (xs.length ? ' · ' + xs.length : '') };
  }

  function rows(S, now) {
    S = S || {};
    now = now || Date.now();
    const out = [], FO = S.fo || {}, day0 = now - (now % DAY);
    // 1. the real account's futures (app_home.public_portfolio.futures.periods.day — the same number as the app)
    const PF = ((S.home || {}).public_portfolio || {}).futures || {}, PD = (PF.periods || {}).day || {};
    const realOpen = (S.real || {}).futures || [];
    out.push({ key: 'real', name: 'Реальный счёт · фьючерсы', tag: 'реальные деньги',
      today: PD.available ? { usd: PD.usd, pct: PD.pct, label: 'с 00:00 UTC' } : null,
      open: { n: realOpen.length, usd: r2(realOpen.reduce((a, x) => a + (+x.upnl_usd || 0), 0)) },
      lines: realOpen.map((x) => `${esc(String(x.inst || '').split('-')[0])} ${sideRu(x.side)} · ${usd(x.upnl_usd)}`) });
    // 2. each followed trader: OUR copies (fut_owner.json trades by «whale»)
    const T = (FO.trades || []).filter((t) => t && t.whale);
    const byW = {};
    T.forEach((t) => { (byW[t.whale] = byW[t.whale] || []).push(t); });
    (FO.following || []).filter((f) => !/^ivan\d?:/.test(String(f.addr || ''))).forEach((f) => {
      const cs = byW[f.addr] || [], op = cs.filter((t) => !t.closed);
      const ct = closedToday(cs.filter((t) => t.closed), 'closed_t', 'pnl_usd', now);
      out.push({ key: 'w:' + f.addr, name: f.name, tag: 'трейдер · наши копии', today: cs.length ? ct : null,
        open: { n: op.length, usd: r2(op.reduce((a, t) => a + (+t.pnl_usd || 0), 0)) },
        note: cs.length ? `всего по нашим копиям ${usd((f.totals || {}).total_usd)}` : 'наших копий пока нет',
        lines: op.map((t) => `${esc(t.coin)} ${sideRu(t.side)} · ${usd(t.pnl_usd)}`) });
    });
    // 3. Ivan — HIS account (stats only: no daily figure) and OUR copies of him (ivan:)
    const IS = S.ivanStats || {}, ivC = T.filter((t) => /^ivan:/.test(String(t.whale))), ivO = ivC.filter((t) => !t.closed);
    out.push({ key: 'ivan', name: 'Иван', tag: 'его счёт Bybit · наши копии отдельно', today: null,
      open: { n: IS.open_n, usd: IS.open_upnl_usd },
      note: `его счёт: закрыто ${usd(IS.net_closed_usd)}, открыто ${usd(IS.open_upnl_usd)} · наши копии: открыто ${ivO.length} (${usd(ivO.reduce((a, t) => a + (+t.pnl_usd || 0), 0))}), закрыто сегодня ${usd(closedToday(ivC.filter((t) => t.closed), 'closed_t', 'pnl_usd', now).usd)}`,
      lines: ivO.map((t) => `наша копия: ${esc(t.coin)} ${sideRu(t.side)} · ${usd(t.pnl_usd)}`) });
    // 4. Ivan Test 2 / 3 / 4 — each test account's own value (equity history) and our real copies of it
    ((S.ivanTests || {}).accounts || []).forEach((a) => {
      const A = ((a.data || {}).account) || {}, R = a.real || {};
      out.push({ key: 'it:' + a.id, name: a.name, tag: `его тест-счёт · копия на реальный: ${a.copy_real ? 'ВКЛ' : 'выкл'}`,
        today: sinceDay(A.equity_hist, now), open: { n: (A.positions || []).length, usd: A.upnl },
        note: a.copy_real ? `наши реальные копии: открыто ${R.open_n || 0} (${usd(R.open_pnl_usd)}), закрыто ${R.closed_n || 0} (${usd(R.closed_pnl_usd)})` : '',
        lines: (A.positions || []).map((p) => `${esc(p.symbol)} ${sideRu(p.side)} · ${usd(p.upnl)}`), link: '#/ivan_tests' });
    });
    // 5. the demo desk «Киты» (fut_owner.json desk.history = its value curve)
    const D = FO.desk || {};
    if (D.history || D.equity != null) {
      out.push({ key: 'desk', name: 'Демо-стол «Киты»', tag: 'демо $10 000', today: sinceDay(D.history, now), open: { n: null, usd: null },
        note: `с начала ${pct(D.pnl_pct)} · ${usd(D.pnl_usd)}` });
    }
    // 6–8. the paper test desks (owner API) — closed today; their open part has no curve to measure today's change
    const paper = (key, name, d, tKey, pKey, link) => {
      if (!d) { out.push({ key, name, tag: 'бумажный тест', today: null, open: { n: null }, note: 'нет данных с сервера', link }); return; }
      const ct = closedToday(d.closed, tKey, pKey, now), op = d.open || [];
      out.push({ key, name, tag: 'бумажный тест', today: ct.n ? ct : { usd: 0, pct: null, label: 'закрыто сегодня · 0' },
        open: { n: op.length, usd: r2(op.reduce((a, o) => a + (+o.upnl || 0), 0)) },
        note: isN(d.pnl_usd) ? `с начала ${usd(d.pnl_usd)}` : '', lines: op.map((o) => `${esc(o.coin || o.symbol)} ${sideRu(o.side)} · ${usd(o.upnl)}`), link });
    };
    const rv = S.reversal && (S.reversal.data || S.reversal);
    paper('reversal', 'Разворот (тест)', rv && (rv.desk || rv), 'closed_t', 'pnl', '#/reversal');
    const sa = S.smartAlts && (S.smartAlts.data || S.smartAlts);
    paper('smart_alts', 'Альты от китов (тест)', sa && (sa.desk || sa), 'closed_t', 'pnl', '#/smart_alts');
    // Golden Donut: a notional $10 000 curve scaled by his % (peter.py) — its change since 00:00 UTC
    const P = S.peter && (S.peter.data || S.peter);
    out.push({ key: 'peter', name: 'Golden Donut', tag: 'наблюдение · условные $10 000',
      today: P ? sinceDay((P.curve || []).concat(isN(P.equity_notional) ? [[now, P.equity_notional]] : []), now) : null,
      open: { n: P ? (P.open || []).length : null }, note: P ? `с начала ${pct(P.pnl_pct)}` : 'нет данных с сервера',
      lines: P ? (P.open || []).map((o) => `${esc(o.symbol)} ${sideRu(o.side)}`) : [], link: '#/peter' });
    return out;
  }

  const CSS = `.st-l{display:grid}.st-r{border-bottom:1px solid var(--line,rgba(127,127,127,.2))}.st-r:last-child{border-bottom:0}
.st-r>summary{list-style:none;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2px 10px;align-items:baseline;padding:10px 2px;cursor:pointer;min-height:52px}
.st-r>summary::-webkit-details-marker{display:none}.st-n{font-weight:700;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.st-v{font:700 15px var(--mono,ui-monospace,monospace);text-align:right;white-space:nowrap}.st-v small{font-weight:500;font-size:12px;margin-left:6px}
.st-m{grid-column:1/-1;display:flex;justify-content:space-between;gap:8px;font-size:11.5px;color:var(--muted,#8a94a6)}
.st-r .pos{color:var(--up,#6fcf97)}.st-r .neg{color:var(--dn,#ef7d6b)}.st-d{padding:0 2px 10px;font-size:12.5px;line-height:1.55;color:var(--fg,#e8f0fa)}
.st-d a{color:var(--accent2,#7cc4ff)}.st-h{display:flex;justify-content:space-between;gap:8px;align-items:baseline;margin-bottom:4px}.st-h small{color:var(--muted,#8a94a6);font-size:11.5px}`;

  function html(R, opts) {
    opts = opts || {};
    const c = (v) => (+v > 0 ? 'pos' : +v < 0 ? 'neg' : '');
    const row = (r) => {
      const T = r.today;
      const val = T && isN(T.usd) ? `<span class="${c(T.usd)}">${usd(T.usd)}</span>${isN(T.pct) ? `<small class="${c(T.pct)}">${pct(T.pct)}</small>` : ''}` : '<span>—</span>';
      const op = isN((r.open || {}).n) ? `открыто ${r.open.n}${isN(r.open.usd) && r.open.n ? ' · сейчас ' + usd(r.open.usd) : ''}` : '';
      return `<details class="st-r" data-st="${esc(r.key)}"><summary><span class="st-n">${esc(r.name)}</span><span class="st-v">${val}</span>
        <span class="st-m"><span>${esc(r.tag || '')}${op ? ' · ' + op : ''}</span><span>${esc(T ? T.label : 'за сегодня нет данных')}</span></span></summary>
        <div class="st-d">${r.note ? esc(r.note) + '<br>' : ''}${(r.lines || []).join('<br>')}${r.link ? `<br><a href="${esc(r.link)}">Подробнее →</a>` : ''}</div></details>`;
    };
    return `<style>${CSS}</style><div class="st-h"><b>Стратегии сегодня</b><small>сегодня = с 00:00 UTC (02:00 Киев) · тап — подробности</small></div>
      <div class="st-l">${(R || []).map(row).join('')}</div>${opts.foot ? `<p class="st-m" style="display:block;margin-top:6px">${esc(opts.foot)}</p>` : ''}`;
  }
  window.CNStratToday = { rows, html, sinceDay, closedToday };
})();
