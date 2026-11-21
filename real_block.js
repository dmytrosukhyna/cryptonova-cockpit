/* CRYPTO NOVΛ — «Реальный счёт» (owner 16.11.2026: «график страшный — одно огромное падение и потом ровно»).
   One renderer of the public real-account block. Data: app_home.json → public_portfolio (cnq/agents/app_home._real_portfolio) —
   the same numbers the app shows. Honest by construction: nothing is smoothed or hidden —
   · opens on «Фьючерсы · за сутки»; the switches change the part (фьючерсы / спот / всё) and the period (сутки / 7 дней / всё время);
   · «Всего с начала» with the split «фьючерсы · спот» is ALWAYS on the screen, exact, minus included;
   · the line is the result's change since the period's start: it starts at 0, the x axis is real time, the y axis is fitted to
     the shown series and the zero line is marked; the bars are the change of each day (00:00 UTC), they add up to the result. */
(function () {
  'use strict';
  if (window.CNRealBlock) return;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const usd = (v) => (v == null || !isFinite(v) ? '—' : (+v > 0 ? '+' : +v < 0 ? '−' : '') + '$' + Math.abs(+v).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const pct = (v) => (v == null || !isFinite(v) ? '' : (+v > 0 ? '+' : +v < 0 ? '−' : '') + Math.abs(+v).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + '%');
  const dm = (t) => new Date(t).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', timeZone: 'UTC' });
  const dmhm = (t) => new Date(t).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  const SEGS = [['futures', 'Фьючерсы'], ['spot', 'Спот'], ['all', 'Всё']];
  const PERS = [['day', 'Сутки', 'за сутки'], ['week', '7 дней', 'за 7 дней'], ['all', 'Всё время', 'за всё время']];
  const DAY = 86400000;
  const CSS = `.rb{display:grid;gap:10px}.rb-top{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;justify-content:space-between}
.rb-seg{display:inline-flex;gap:3px;padding:3px;border:1px solid var(--line,rgba(127,127,127,.25));border-radius:10px}
.rb-seg button{min-height:30px;padding:4px 12px;border-radius:7px;border:1px solid transparent;background:none;color:var(--muted,#8a94a6);font:600 12px inherit;cursor:pointer}
.rb-seg button[aria-pressed="true"]{color:var(--fg,#e8f0fa);border-color:var(--line2,rgba(127,127,127,.5));background:rgba(124,196,255,.12)}
.rb-h small,.rb-m{color:var(--muted,#8a94a6);font-size:12px;line-height:1.5}.rb-h b{display:block;font:600 26px var(--mono,monospace);color:var(--fg,#e8f0fa);margin-top:2px}
.rb-h b span{font-size:14px;color:var(--muted,#8a94a6);margin-left:8px}.rb-h i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px;vertical-align:middle}
.rb-tot{border-top:1px solid var(--line,rgba(127,127,127,.25));border-bottom:1px solid var(--line,rgba(127,127,127,.25));padding:8px 0;font-size:13px;line-height:1.6;display:flex;flex-wrap:wrap;gap:2px 14px;align-items:baseline}
.rb-tot b{font-family:var(--mono,monospace);font-weight:600;color:var(--fg,#e8f0fa)}.rb-tot span{color:var(--muted,#8a94a6)}
.rb-i{width:22px;height:22px;border-radius:50%;border:1px solid var(--line2,rgba(127,127,127,.5));background:none;color:var(--muted,#8a94a6);font:600 12px inherit;cursor:pointer;margin-left:4px;padding:0}
.rb-note{font-size:12.5px;line-height:1.55;color:var(--fg,#e8f0fa);border:1px solid var(--line,rgba(127,127,127,.25));border-radius:10px;padding:8px 10px}
.rb-plot{position:relative;display:grid;grid-template-columns:auto minmax(0,1fr);gap:0 6px}.rb-y{display:flex;flex-direction:column;justify-content:space-between;font:11px var(--mono,monospace);color:var(--muted,#8a94a6);text-align:right}
.rb-c{position:relative}.rb-c svg{display:block;width:100%;height:150px;overflow:visible}.rb-c svg.rb-bars{height:64px;margin-top:6px}
.rb-x{grid-column:2;display:flex;justify-content:space-between;font:11px var(--mono,monospace);color:var(--muted,#8a94a6);margin-top:3px}
.rb-z{position:absolute;right:0;font:10px var(--mono,monospace);color:var(--muted,#8a94a6);transform:translateY(-110%);pointer-events:none}
.rb-tip{position:absolute;top:0;pointer-events:none;font:11.5px var(--mono,monospace);color:var(--fg,#e8f0fa);background:var(--bg2,#0a1322);border:1px solid var(--line2,rgba(127,127,127,.5));border-radius:7px;padding:3px 7px;white-space:nowrap;transform:translate(-50%,-115%)}
.rb-cap{font-size:11.5px;color:var(--muted,#8a94a6);margin:6px 0 2px}`;

  /** the part of the account: {curve [[t, $ value]], start_usd, periods, daily, pnl_usd} */
  const part = (pp, seg) => (seg === 'all' ? pp : pp && pp[seg]) || null;

  /** the result's change since the period's start: [[t, $]] starting at 0, ending on the server's exact period result */
  function series(pp, seg, per) {
    const P = part(pp, seg), pr = P && P.periods && P.periods[per];
    if (!P || !pr || !pr.available || !(P.curve || []).length) return null;
    const base = +P.start_usd || 0, from = Math.max(+pr.from || 0, +P.curve[0][0]);
    let ref = 0;
    const pts = [];
    for (const [t, v] of P.curve) { if (t <= from) ref = v - base; else pts.push([t, v - base]); }
    if (from <= (pp.since || 0)) ref = 0;
    const out = [[from, 0]].concat(pts.map(([t, v]) => [t, Math.round((v - ref) * 100) / 100]));
    const end = Math.max(+pp.asof || 0, out[out.length - 1][0]);
    if (pr.usd != null) { if (out.length > 1 && out[out.length - 1][0] === end) out[out.length - 1][1] = +pr.usd; else out.push([end, +pr.usd]); }
    return { pts: out, from, to: end, usd: pr.usd, pct: pr.pct };
  }
  /** the days of the period: [[00:00 UTC, the day's change $]] */
  const days = (pp, seg, from) => ((part(pp, seg) || {}).daily || []).filter((d) => d[0] + DAY > from);

  function lineSvg(S) {
    const W = 600, H = 150, ys = S.pts.map((p) => p[1]);
    let lo = Math.min(0, ...ys), hi = Math.max(0, ...ys);
    if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
    const X = (t) => ((t - S.from) / Math.max(1, S.to - S.from)) * W, Y = (v) => H - ((v - lo) / (hi - lo)) * H;
    // the points are the real values on an even clock (one an hour); the axis runs exactly from the lowest to the highest of them
    const d = S.pts.map((p, i) => (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ' ' + Y(p[1]).toFixed(1)).join(' ');
    return { lo, hi, zero: Y(0) / H * 100, X, Y, W, H,
      svg: `<svg class="rb-line" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Изменение результата за период"><line x1="0" x2="${W}" y1="${Y(0).toFixed(1)}" y2="${Y(0).toFixed(1)}" stroke="var(--line2,rgba(127,127,127,.5))" stroke-dasharray="4 4" vector-effect="non-scaling-stroke"/><path d="${d}" fill="none" stroke="var(--acc,#7cc4ff)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/><line class="rb-cross" x1="0" x2="0" y1="0" y2="${H}" stroke="var(--line2,rgba(127,127,127,.5))" vector-effect="non-scaling-stroke" visibility="hidden"/></svg>` };
  }
  function barsSvg(D) {
    const W = 600, H = 64, m = Math.max(1e-9, ...D.map((d) => Math.abs(d[1]))), bw = W / D.length, w = Math.max(2, Math.min(22, bw - 2));
    const mid = H / 2, r = Math.min(3, w / 2);
    const bars = D.map((d, i) => {
      const h = Math.abs(d[1]) / m * (mid - 2), x = i * bw + (bw - w) / 2;
      if (h < 0.5) return `<rect x="${x.toFixed(1)}" y="${(mid - 0.5).toFixed(1)}" width="${w.toFixed(1)}" height="1" fill="var(--muted,#8a94a6)"><title>${dm(d[0])}: ${usd(d[1])}</title></rect>`;
      return `<rect x="${x.toFixed(1)}" y="${(d[1] >= 0 ? mid - h : mid).toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" rx="${r}" fill="var(--${d[1] >= 0 ? 'up,#6fcf97' : 'dn,#ef7d6b'})"><title>${dm(d[0])}: ${usd(d[1])}</title></rect>`;
    }).join('');
    return `<svg class="rb-bars" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Изменение по дням"><line x1="0" x2="${W}" y1="${mid}" y2="${mid}" stroke="var(--line2,rgba(127,127,127,.5))" vector-effect="non-scaling-stroke"/>${bars}</svg>`;
  }

  /** the always-visible total with its split — exact numbers, the minus included */
  function totalHtml(pp) {
    const f = pp.futures || {}, s = pp.spot || {};
    return `<div class="rb-tot"><span>${esc(pp.total_label || 'Всего с начала')}: <b>${usd(pp.pnl_usd)}</b>${pp.pnl_pct != null ? ' (' + pct(pp.pnl_pct) + ')' : ''}</span>`
      + `<span>фьючерсы <b>${usd(f.pnl_usd)}</b></span><span>спот <b>${usd(s.pnl_usd)}</b>${pp.spot_note ? '<button type="button" class="rb-i" data-rb-i aria-expanded="false" aria-label="Что такое спот в этом портфеле">i</button>' : ''}</span></div>`
      + (pp.spot_note ? `<div class="rb-note" data-rb-note hidden>${esc(pp.spot_note)}</div>` : '');
  }

  function html(pp, st) {
    if (!pp || pp.kind !== 'real') return '';
    const seg = st.seg, per = st.per, S = series(pp, seg, per);
    const segName = SEGS.find((x) => x[0] === seg)[1], perName = PERS.find((x) => x[0] === per)[2];
    const chips = (list, cur, attr) => `<div class="rb-seg" role="group">${list.map((x) => `<button type="button" data-rb-${attr}="${x[0]}" aria-pressed="${x[0] === cur}">${x[1]}</button>`).join('')}</div>`;
    let plot = '<div class="rb-m">За этот период данных пока нет.</div>';
    if (S && S.pts.length > 1) {
      const L = lineSvg(S), D = per === 'day' ? [] : days(pp, seg, S.from);
      const lab = per === 'day' ? (t) => new Date(t).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }) : dmhm;
      plot = `<div class="rb-cap">Изменение результата ${perName}, $ · начало периода = 0</div>`
        + `<div class="rb-plot"><div class="rb-y"><span>${usd(L.hi)}</span><span>${usd(L.lo)}</span></div><div class="rb-c" data-rb-plot>${L.svg}<span class="rb-z" style="top:${L.zero.toFixed(1)}%">0</span><div class="rb-tip" hidden></div></div>`
        + `<div class="rb-x"><span>${lab(S.from)}</span><span>${lab(S.to)}</span></div></div>`
        + (D.length > 1 ? `<div class="rb-cap">По дням (границы 00:00 UTC), $${per === 'all' ? ' — столбцы в сумме дают результат с начала' : ''}</div><div class="rb-plot"><div class="rb-y"></div><div class="rb-c">${barsSvg(D)}</div><div class="rb-x"><span>${dm(D[0][0])}</span><span>${dm(D[D.length - 1][0])}</span></div></div>` : '');
    }
    const v = S ? S.usd : null;
    return `<div class="rb"><div class="rb-top">${chips(SEGS, seg, 'seg')}${chips(PERS, per, 'per')}</div>`
      + `<div class="rb-h"><small>${esc(segName)} · ${perName} · реальный счёт на ${esc(pp.exchange || 'OKX')} с ${dm(pp.since)}</small>`
      + `<b><i style="background:var(--${v > 0 ? 'up,#6fcf97' : v < 0 ? 'dn,#ef7d6b' : 'muted,#8a94a6'})"></i>${usd(v)}<span>${S ? pct(S.pct) : ''}</span></b></div>`
      + totalHtml(pp) + plot
      + `<div class="rb-m">${esc(pp.note || '')}${pp.stale_note ? ' ' + esc(pp.stale_note) : ''} Обновлено ${dmhm(pp.asof)}.</div></div>`;
  }

  function mount(el, pp, st) {
    if (!document.getElementById('rbCss')) document.head.insertAdjacentHTML('beforeend', `<style id="rbCss">${CSS}</style>`);
    st = st || {};
    const v = (pp && pp.view) || {};
    st.seg = st.seg || (SEGS.some((x) => x[0] === v.segment) ? v.segment : 'futures');
    st.per = st.per || (PERS.some((x) => x[0] === v.period) ? v.period : 'day');
    const draw = () => {
      const keep = el.querySelector('[data-rb-note]'), open = keep && !keep.hidden;
      el.innerHTML = html(pp, st);
      const note = el.querySelector('[data-rb-note]'), ib = el.querySelector('[data-rb-i]');
      if (note && open) { note.hidden = false; ib.setAttribute('aria-expanded', 'true'); }
      const box = el.querySelector('[data-rb-plot]'), S = series(pp, st.seg, st.per);
      if (box && S) {
        const tip = box.querySelector('.rb-tip'), cross = box.querySelector('.rb-cross');
        const move = (e) => {
          const r = box.getBoundingClientRect(), x = Math.min(1, Math.max(0, ((e.touches ? e.touches[0].clientX : e.clientX) - r.left) / Math.max(1, r.width)));
          const t = S.from + x * (S.to - S.from);
          let p = S.pts[0];
          for (const q of S.pts) { if (q[0] <= t) p = q; else break; }
          tip.hidden = false; tip.style.left = (x * 100).toFixed(1) + '%'; tip.textContent = dmhm(p[0]) + ' · ' + usd(p[1]);
          cross.setAttribute('x1', x * 600); cross.setAttribute('x2', x * 600); cross.setAttribute('visibility', 'visible');
        };
        const out = () => { tip.hidden = true; cross.setAttribute('visibility', 'hidden'); };
        box.addEventListener('mousemove', move); box.addEventListener('touchstart', move, { passive: true }); box.addEventListener('touchmove', move, { passive: true });
        box.addEventListener('mouseleave', out); box.addEventListener('touchend', out);
      }
    };
    if (!el.dataset.rbBound) {
      el.dataset.rbBound = '1';
      el.addEventListener('click', (e) => {
        const b = e.target.closest && e.target.closest('button'); if (!b) return;
        if (b.dataset.rbSeg) { el._rb.st.seg = b.dataset.rbSeg; el._rb.draw(); }
        else if (b.dataset.rbPer) { el._rb.st.per = b.dataset.rbPer; el._rb.draw(); }
        else if ('rbI' in b.dataset) { const n = el.querySelector('[data-rb-note]'); if (n) { n.hidden = !n.hidden; b.setAttribute('aria-expanded', String(!n.hidden)); } }
      });
    }
    el._rb = { st, draw };
    draw();
    return st;
  }

  window.CNRealBlock = { html, mount, series, days, SEGS, PERS };
})();
