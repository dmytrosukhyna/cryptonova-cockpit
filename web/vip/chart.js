/* CRYPTO NOVΛ VIP — one small SVG line chart (no library): responsive, hover / touch tooltip, optional baseline. */
(function () {
  'use strict';
  const V = window.VIP;

  V.chart = function (el, pts, opts = {}) {
    pts = (pts || []).filter((p) => p && V.isNum(p[0]) && V.isNum(p[1])).map((p) => [V.ms(p[0]), +p[1]]).sort((a, b) => a[0] - b[0]);
    if (el._ro) { el._ro.disconnect(); el._ro = null; }
    if (pts.length < 2) { el.innerHTML = `<div class="chart-empty">${V.esc(opts.empty || 'Недостаточно точек для графика')}</div>`; return; }
    const H = opts.height || 220, fmt = opts.fmt || ((v) => V.num(v)), base = opts.base;
    const tfmt = opts.tfmt || ((t) => V.dshort(t));
    const tipT = opts.tipT || ((t) => V.dt(t));
    el.classList.add('chart');

    function draw() {
      const W = Math.max(260, el.clientWidth || 600);
      const padL = 6, padR = 64, padT = 12, padB = 24;
      const t0 = pts[0][0], t1 = pts[pts.length - 1][0];
      let lo = Math.min(...pts.map((p) => p[1])), hi = Math.max(...pts.map((p) => p[1]));
      if (V.isNum(base)) { lo = Math.min(lo, base); hi = Math.max(hi, base); }
      if (hi === lo) { hi += Math.abs(hi) * 0.01 || 1; lo -= Math.abs(lo) * 0.01 || 1; }
      const span = hi - lo; hi += span * 0.06; lo -= span * 0.06;
      const x = (t) => padL + ((t - t0) / Math.max(1, t1 - t0)) * (W - padL - padR);
      const y = (v) => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB);
      const step = Math.max(1, Math.floor(pts.length / (W * 1.5)));
      const use = pts.filter((_, i) => i % step === 0 || i === pts.length - 1);
      const d = use.map((p, i) => (i ? 'L' : 'M') + x(p[0]).toFixed(1) + ' ' + y(p[1]).toFixed(1)).join('');
      const area = d + `L${x(t1).toFixed(1)} ${H - padB}L${x(t0).toFixed(1)} ${H - padB}Z`;
      const ticks = [hi - (hi - lo) * 0.06, (hi + lo) / 2, lo + (hi - lo) * 0.06];
      const gid = 'g' + Math.random().toString(36).slice(2, 8);
      const xl = [t0, t0 + (t1 - t0) / 2, t1];
      el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="${V.esc(opts.label || 'график')}">
        <defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#6ea6f5" stop-opacity=".22"/><stop offset="1" stop-color="#6ea6f5" stop-opacity="0"/></linearGradient></defs>
        ${ticks.map((v) => `<line x1="${padL}" x2="${W - padR}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" stroke="rgba(148,172,214,.09)"/>
          <text class="ax" x="${W - padR + 8}" y="${(y(v) + 4).toFixed(1)}">${V.esc(fmt(v, true))}</text>`).join('')}
        ${V.isNum(base) ? `<line x1="${padL}" x2="${W - padR}" y1="${y(base).toFixed(1)}" y2="${y(base).toFixed(1)}" stroke="rgba(230,236,246,.35)" stroke-dasharray="3 4"/>` : ''}
        <path d="${area}" fill="url(#${gid})"/>
        <path d="${d}" fill="none" stroke="#6ea6f5" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round"/>
        ${xl.map((t, i) => `<text class="ax" x="${x(t).toFixed(1)}" y="${H - 6}" text-anchor="${i === 0 ? 'start' : i === 2 ? 'end' : 'middle'}">${V.esc(tfmt(t))}</text>`).join('')}
        <line class="cx" x1="0" x2="0" y1="${padT}" y2="${H - padB}" stroke="rgba(230,236,246,.35)" visibility="hidden"/>
        <circle class="cd" r="4" fill="#9cc2fa" stroke="#070d1a" stroke-width="2" visibility="hidden"/>
      </svg><div class="tip" hidden></div>`;
      const svg = el.querySelector('svg'), cx = svg.querySelector('.cx'), cd = svg.querySelector('.cd'), tip = el.querySelector('.tip');
      const move = (ev) => {
        const r = svg.getBoundingClientRect();
        const px = ((ev.clientX - r.left) / r.width) * W;
        const tt = t0 + ((px - padL) / (W - padL - padR)) * (t1 - t0);
        let lo2 = 0, hi2 = pts.length - 1;
        while (hi2 - lo2 > 1) { const m = (lo2 + hi2) >> 1; if (pts[m][0] < tt) lo2 = m; else hi2 = m; }
        const p = Math.abs(pts[lo2][0] - tt) < Math.abs(pts[hi2][0] - tt) ? pts[lo2] : pts[hi2];
        const X = x(p[0]), Y = y(p[1]);
        cx.setAttribute('x1', X); cx.setAttribute('x2', X); cx.setAttribute('visibility', 'visible');
        cd.setAttribute('cx', X); cd.setAttribute('cy', Y); cd.setAttribute('visibility', 'visible');
        tip.hidden = false;
        tip.innerHTML = `<b class="${opts.signed ? V.cls(p[1]) : ''}">${V.esc(fmt(p[1]))}</b><small>${V.esc(tipT(p[0]))}</small>`;
        const left = Math.min(Math.max((X / W) * r.width, 60), r.width - 60);
        tip.style.left = left + 'px';
      };
      const leave = () => { tip.hidden = true; cx.setAttribute('visibility', 'hidden'); cd.setAttribute('visibility', 'hidden'); };
      svg.addEventListener('pointermove', move);
      svg.addEventListener('pointerdown', move);
      svg.addEventListener('pointerleave', leave);
    }
    draw();
    let lastW = el.clientWidth;
    if (window.ResizeObserver) {
      el._ro = new ResizeObserver(() => { if (Math.abs(el.clientWidth - lastW) > 8) { lastW = el.clientWidth; draw(); } });
      el._ro.observe(el);
    }
  };
})();
