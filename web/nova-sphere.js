/* Copied from cryptonova-site/assets/sphere.js — the single source of truth; change it there and copy here. */
/* CRYPTO NOVA — the NOVA AI sphere: the white plasma sphere the system shows while NOVA gives its report
   (owner 21.10 16:31: «красивая, беленькая, светилась при докладе» — that one, not a planet).
   Drawn on a 64-unit design grid and scaled to any size at the screen's pixel density, so the edge stays crisp.
   NovaSphere.draw(ctx, cx, cy, half, t, {busy, still}) — one frame in a box of 2·half;
   NovaSphere.mount(canvas, {busy: fn}) — animates a canvas; every <canvas data-sphere> mounts itself. */
(function (w) {
  "use strict";
  var TAU = Math.PI * 2;
  var SPARKS = [];
  for (var i = 0; i < 8; i++) SPARKS.push({ a: i * 0.8, r: 0.6 + 0.32 * Math.sin(i * 2.1), sp: 0.8 + i * 0.15, tilt: 0.45 + i * 0.37, sz: 1.1 + (i % 3) * 0.55 });

  function draw(g, cx0, cy0, half, t, opt) {
    opt = opt || {};
    var busy = !!opt.busy, k = half / 32, cx = 32, cy = 32;
    g.save(); g.translate(cx0 - half, cy0 - half); g.scale(k, k);
    var R = 9.5 + 1.1 * Math.sin(t * 2) + (busy ? 1.6 * Math.abs(Math.sin(t * 7)) : 0);
    // soft light around the sphere
    var hz = g.createRadialGradient(cx, cy, R * 0.3, cx, cy, 31);
    hz.addColorStop(0, "rgba(150,215,255,.55)"); hz.addColorStop(0.45, "rgba(90,170,255,.22)"); hz.addColorStop(1, "rgba(90,170,255,0)");
    g.fillStyle = hz; g.beginPath(); g.arc(cx, cy, 31.5, 0, TAU); g.fill();
    var pts = SPARKS.map(function (s) { var a = s.a + t * s.sp, x = Math.cos(a) * 22 * s.r, z = Math.sin(a) * 22 * s.r; return { a: a, s: s, x: cx + x, y: cy + z * Math.sin(s.tilt) * 0.6, z: Math.cos(s.tilt) * z }; });
    function spark(p) {
      var q = (p.z + 22) / 44;
      for (var j = 3; j >= 1; j--) {
        var a2 = p.a - j * 0.09 * p.s.sp, x2 = cx + Math.cos(a2) * 22 * p.s.r, z2 = Math.sin(a2) * 22 * p.s.r;
        g.fillStyle = "rgba(150,215,255," + (0.10 * (4 - j) * (0.4 + q)).toFixed(3) + ")"; g.beginPath(); g.arc(x2, cy + z2 * Math.sin(p.s.tilt) * 0.6, p.s.sz * (0.5 + 0.6 * q), 0, TAU); g.fill();
      }
      g.fillStyle = "rgba(" + (q > 0.5 ? "245,251,255" : "160,220,255") + "," + (0.5 + 0.5 * q).toFixed(3) + ")"; g.shadowColor = "rgba(140,210,255,1)"; g.shadowBlur = (5 + 8 * q) * k;
      g.beginPath(); g.arc(p.x, p.y, p.s.sz * (0.65 + 0.85 * q), 0, TAU); g.fill(); g.shadowBlur = 0;
    }
    if (!opt.still) pts.filter(function (p) { return p.z < 0; }).forEach(spark);
    // rings behind the core (back halves dimmer)
    for (var i = 0; i < 3; i++) { var rx = 17 + i * 5, ry = rx * Math.abs(Math.sin(t * (0.6 + i * 0.35) + i * 1.3)), rot = t * (0.45 - i * 0.27); g.strokeStyle = "rgba(190,232,255," + (0.22 + 0.12 * i) + ")"; g.lineWidth = 0.75; g.beginPath(); g.ellipse(cx, cy, rx, Math.max(0.6, ry), rot, Math.PI, TAU); g.stroke(); }
    // plasma core: white centre, ice body, glowing rim
    var cg = g.createRadialGradient(cx - R * 0.35, cy - R * 0.4, 0.5, cx, cy, R);
    cg.addColorStop(0, "#ffffff"); cg.addColorStop(0.35, "#d6f1ff"); cg.addColorStop(0.7, "#6cc2ff"); cg.addColorStop(1, "rgba(60,150,255,.55)");
    g.fillStyle = cg; g.shadowColor = "#8fd3ff"; g.shadowBlur = (18 + (busy ? 10 : 0)) * k; g.beginPath(); g.arc(cx, cy, R, 0, TAU); g.fill(); g.shadowBlur = 0;
    // a slow swirl inside
    g.save(); g.beginPath(); g.arc(cx, cy, R * 0.92, 0, TAU); g.clip();
    for (var m = 0; m < 3; m++) { g.strokeStyle = "rgba(255,255,255," + (0.35 - 0.08 * m) + ")"; g.lineWidth = 0.9; g.beginPath(); g.arc(cx + Math.cos(t * 1.3 + m * 2.1) * R * 0.25, cy + Math.sin(t * 1.1 + m * 2.1) * R * 0.25, R * (0.45 + 0.15 * m), t * (1.6 + m * 0.4) + m, t * (1.6 + m * 0.4) + m + 2.2); g.stroke(); }
    g.restore();
    g.fillStyle = "rgba(255,255,255,.85)"; g.beginPath(); g.ellipse(cx - R * 0.38, cy - R * 0.45, R * 0.22, R * 0.13, -0.6, 0, TAU); g.fill();
    // rings in front
    for (var n = 0; n < 3; n++) { var rx2 = 17 + n * 5, ry2 = rx2 * Math.abs(Math.sin(t * (0.6 + n * 0.35) + n * 1.3)), rot2 = t * (0.45 - n * 0.27); g.strokeStyle = "rgba(225,245,255," + (0.45 + 0.15 * n) + ")"; g.lineWidth = 0.85; g.shadowColor = "rgba(140,210,255,.9)"; g.shadowBlur = 3 * k; g.beginPath(); g.ellipse(cx, cy, rx2, Math.max(0.6, ry2), rot2, 0, Math.PI); g.stroke(); g.shadowBlur = 0; }
    if (!opt.still) pts.filter(function (p) { return p.z >= 0; }).forEach(spark);
    g.restore();
  }

  function mount(cv, opt) {
    if (!cv || !cv.getContext) return null;
    opt = opt || {};
    var ctx = cv.getContext("2d"), W = 0, H = 0, run = false, vis = true, t = 0, last = 0;
    var reduce = w.matchMedia && w.matchMedia("(prefers-reduced-motion: reduce)").matches;
    function size() { var d = Math.min(3, w.devicePixelRatio || 1); W = cv.width = Math.max(1, Math.round(cv.clientWidth * d)); H = cv.height = Math.max(1, Math.round(cv.clientHeight * d)); }
    function frame(now) {
      if (!run && now !== -1) return;
      var busy = typeof opt.busy === "function" ? opt.busy() : false;
      if (now !== -1) { var dt = last ? Math.min(0.05, (now - last) / 1000) : 0.016; last = now; t += dt * (busy ? 3 : 1); } else t = t || 1.3;
      ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, W, H);
      draw(ctx, W / 2, H / 2, Math.min(W, H) / 2, t, { busy: busy, still: now === -1 && reduce });
      if (now !== -1) requestAnimationFrame(frame);
    }
    function set() { var want = vis && !document.hidden && !reduce; if (want && !run) { run = true; last = 0; requestAnimationFrame(frame); } else if (!want) { run = false; frame(-1); } }
    size(); w.addEventListener("resize", function () { size(); if (!run) frame(-1); });
    document.addEventListener("visibilitychange", set);
    if ("IntersectionObserver" in w) new IntersectionObserver(function (en) { vis = en[0].isIntersecting; set(); }).observe(cv);
    set(); frame(-1);
    return { size: size };
  }

  w.NovaSphere = { draw: draw, mount: mount };
  function auto() { [].forEach.call(document.querySelectorAll("canvas[data-sphere]"), function (c) { if (!c._sph) c._sph = mount(c, {}); }); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", auto); else auto();
  w.NovaSphere.auto = auto;
})(window);
