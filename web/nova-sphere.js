/* Copied from cryptonova-site/assets/sphere.js — the single source of truth; change it there and copy here. */
/* CRYPTO NOVA — the NOVA AI sphere (owner 21.10 17:24: «вот такую сферу оживи… динамически, без обрывов»):
   a glass sphere with a small universe inside (stars, a warm glow, a cool highlight) and three glowing orbit ribbons
   with glass electrons. Everything moves continuously: electrons run their orbits, light flows along the ribbons,
   the whole atom turns slowly in the plane, the stars inside drift and twinkle. 100-unit design grid, scaled to any
   size at the screen's pixel density. NovaSphere.draw(ctx, cx, cy, half, t, {busy, still}); NovaSphere.mount(canvas,
   {busy: fn}); every <canvas data-sphere> mounts itself. */
(function (w) {
  "use strict";
  var TAU = Math.PI * 2;
  // owner 21.10 18:06: the live sphere stays simple — one electron per orbit, no sparks, no extra lines (the coins live on the icon)
  var ORB = [{ g: 1.62, b: 0.30, w: 0.78, e: [0] }, { g: 0.36, b: 0.27, w: -0.64, e: [2.2] }, { g: -0.30, b: 0.33, w: 0.71, e: [4.1] }];
  var STARS = [];
  (function () { var s = 7; function r() { s = (s * 16807) % 2147483647; return s / 2147483647; }
    for (var i = 0; i < 26; i++) { var u = r() * 2 - 1, a = r() * TAU, rr = Math.cbrt(r()) * 0.92, q = Math.sqrt(1 - u * u); STARS.push({ x: q * Math.cos(a) * rr, y: u * rr, z: q * Math.sin(a) * rr, s: 0.25 + r() * 0.55, ph: r() * TAU, warm: r() < 0.3 }); } })();
  var SPARK = [];
  (function () { for (var i = 0; i < 9; i++) SPARK.push({ o: i % 3, th: i * 0.73 + 0.3, sp: 0.05 + (i % 4) * 0.02, ph: i * 1.7 }); })();

  /* a point of orbit o at angle th: tilted circle (flat ellipse) turned by g around the view axis; z > 0 is in front */
  function op(o, th, rad, rot) {
    var x = Math.cos(th) * rad, y = Math.sin(th) * rad * Math.sin(o.b), z = Math.sin(th) * rad * Math.cos(o.b), g = o.g + rot;
    return { x: x * Math.cos(g) - y * Math.sin(g), y: x * Math.sin(g) + y * Math.cos(g), z: z };
  }
  /* depth shade: a conic gradient whose darkness follows how far behind the sphere each part of the orbit is.
     One continuous stroke per half orbit, so there are no seams, and the back fades smoothly into the front. */
  function shade(g, o, rot) {
    var st = [], p0 = op(o, 0, 1, rot), f0 = Math.atan2(p0.y, p0.x);
    for (var q = 0; q < 48; q++) {
      var th = q * TAU / 48, p = op(o, th, 1, rot), f = Math.atan2(p.y, p.x) - f0;
      f = ((f % TAU) + TAU) % TAU / TAU; st.push([f, 0.62 * (1 - (Math.sin(th) + 1) / 2)]);
    }
    st.sort(function (a, b) { return a[0] - b[0]; });
    var gr = g.createConicGradient(f0, 0, 0);
    st.forEach(function (x) { gr.addColorStop(Math.min(1, Math.max(0, x[0])), "rgba(4,10,34," + x[1].toFixed(3) + ")"); });
    gr.addColorStop(1, "rgba(4,10,34," + st[0][1].toFixed(3) + ")");
    return gr;
  }
  function ribbon(g, o, rad, rot, front, t, k, glow, small) {
    var a0 = front ? -0.015 : Math.PI - 0.015, a1 = front ? Math.PI + 0.015 : TAU + 0.015, n = small ? 36 : 80;
    function path(r) { g.beginPath(); for (var i = 0; i <= n; i++) { var p = op(o, a0 + (a1 - a0) * i / n, r, rot); if (i) g.lineTo(p.x, p.y); else g.moveTo(p.x, p.y); } }
    var grad = "rgba(150,205,255,.85)", conic = !!g.createConicGradient;
    if (conic) {
      grad = g.createConicGradient(t * 0.5 * (o.w > 0 ? 1 : -1) + o.g, 0, 0);
      grad.addColorStop(0, "rgba(90,170,255,.9)"); grad.addColorStop(0.18, "rgba(235,248,255,.95)"); grad.addColorStop(0.3, "rgba(255,205,140,.9)");
      grad.addColorStop(0.45, "rgba(90,165,255,.85)"); grad.addColorStop(0.7, "rgba(60,130,255,.8)"); grad.addColorStop(0.85, "rgba(225,244,255,.95)"); grad.addColorStop(1, "rgba(90,170,255,.9)");
    }
    g.lineCap = "butt"; g.lineJoin = "round";
    path(rad); g.strokeStyle = "rgba(70,150,255," + ((front ? 0.16 : 0.09) * glow).toFixed(3) + ")"; g.lineWidth = 4.6 * k; g.stroke();
    path(rad); g.strokeStyle = grad; g.lineWidth = 2.5 * k; g.stroke();
    path(rad); g.strokeStyle = "rgba(255,255,255,.75)"; g.lineWidth = 0.7 * k; g.stroke();
    if (conic) { var sh = shade(g, o, rot); path(rad); g.strokeStyle = sh; g.lineWidth = 2.7 * k; g.stroke(); }
    else if (!front) { path(rad); g.strokeStyle = "rgba(4,10,34,.45)"; g.lineWidth = 2.7 * k; g.stroke(); }
  }
  function bead(g, x, y, r, warm, dim) {
    var h = g.createRadialGradient(x, y, 0, x, y, r * 3.4);
    h.addColorStop(0, warm ? "rgba(255,210,150," + (0.45 * dim).toFixed(3) + ")" : "rgba(120,200,255," + (0.5 * dim).toFixed(3) + ")"); h.addColorStop(1, "rgba(120,200,255,0)");
    g.fillStyle = h; g.beginPath(); g.arc(x, y, r * 3.4, 0, TAU); g.fill();
    var b = g.createRadialGradient(x - r * 0.35, y - r * 0.4, r * 0.05, x, y, r);
    b.addColorStop(0, "rgba(255,255,255," + dim + ")"); b.addColorStop(0.35, warm ? "rgba(255,226,180," + dim + ")" : "rgba(150,225,255," + dim + ")");
    b.addColorStop(1, warm ? "rgba(200,140,70," + (0.9 * dim) + ")" : "rgba(30,95,220," + (0.9 * dim) + ")");
    g.fillStyle = b; g.beginPath(); g.arc(x, y, r, 0, TAU); g.fill();
  }

  function draw(g, cx0, cy0, half, t, opt) {
    opt = opt || {};
    var busy = !!opt.busy, k = 1, small = half < 22, glow = busy ? 1.35 : 1, rot = t * 0.07, RO = 43, RS = 17;
    g.save(); g.translate(cx0, cy0); g.scale(half / 50, half / 50);
    // bloom
    var hz = g.createRadialGradient(0, 0, RS * 0.6, 0, 0, 50);
    hz.addColorStop(0, "rgba(70,150,255," + (0.42 * glow).toFixed(3) + ")"); hz.addColorStop(0.45, "rgba(50,110,240," + (0.13 * glow).toFixed(3) + ")"); hz.addColorStop(1, "rgba(40,90,220,0)");
    g.fillStyle = hz; g.beginPath(); g.arc(0, 0, 50, 0, TAU); g.fill();
    // back halves of the orbits and the electrons behind the sphere
    var E = [];
    ORB.forEach(function (o, oi) {
      ribbon(g, o, RO, rot, false, t, k, glow, small);
      o.e.forEach(function (e0, ei) { var th = e0 + t * o.w * (busy ? 2.2 : 1); var p = op(o, th, RO, rot); E.push({ p: p, r: 2.7 * (small ? 1.2 : 1), warm: oi === 2 }); });
    });
    E.filter(function (e) { return e.p.z < 0; }).forEach(function (e) { bead(g, e.p.x, e.p.y, e.r * (0.85 + 0.15 * (1 + e.p.z / RO)), e.warm, 0.55); });
    // the sphere: a small universe in glass
    var sg = g.createRadialGradient(0, 0, RS * 0.9, 0, 0, RS * 1.35);
    sg.addColorStop(0, "rgba(110,190,255," + (0.5 * glow).toFixed(3) + ")"); sg.addColorStop(1, "rgba(110,190,255,0)");
    g.fillStyle = sg; g.beginPath(); g.arc(0, 0, RS * 1.35, 0, TAU); g.fill();
    g.save(); g.beginPath(); g.arc(0, 0, RS, 0, TAU); g.clip();
    var b = g.createRadialGradient(RS * 0.38, -RS * 0.2, 0, RS * 0.1, 0, RS * 1.45);
    b.addColorStop(0, "#FFF6E2"); b.addColorStop(0.07, "#C9F5FF"); b.addColorStop(0.24, "#4CCBFF"); b.addColorStop(0.5, "#1E58CF"); b.addColorStop(0.78, "#0C1758"); b.addColorStop(1, "#050928");
    g.fillStyle = b; g.fillRect(-RS, -RS, RS * 2, RS * 2);
    var wm = g.createRadialGradient(-RS * 0.25, RS * 0.55, 0, -RS * 0.25, RS * 0.55, RS * 0.75);
    wm.addColorStop(0, "rgba(255,190,110,.42)"); wm.addColorStop(1, "rgba(255,190,110,0)");
    g.fillStyle = wm; g.fillRect(-RS, -RS, RS * 2, RS * 2);
    var ya = t * 0.18, cy = Math.cos(ya), sy = Math.sin(ya);
    (small ? STARS.slice(0, 14) : STARS).forEach(function (s) {
      var x = s.x * cy + s.z * sy, z = -s.x * sy + s.z * cy, tw = 0.55 + 0.45 * Math.sin(t * 2.2 + s.ph), a = (0.35 + 0.65 * (z + 1) / 2) * tw;
      g.fillStyle = s.warm ? "rgba(255,215,160," + a.toFixed(3) + ")" : "rgba(220,240,255," + a.toFixed(3) + ")";
      g.beginPath(); g.arc(x * RS, s.y * RS, s.s * (small ? 1.6 : 1), 0, TAU); g.fill();
    });
    var hl = g.createRadialGradient(RS * 0.42, -RS * 0.22, 0, RS * 0.42, -RS * 0.22, RS * 0.55);
    hl.addColorStop(0, "rgba(255,255,255," + (0.85 + 0.15 * Math.sin(t * 1.4)).toFixed(3) + ")"); hl.addColorStop(1, "rgba(255,255,255,0)");
    g.fillStyle = hl; g.fillRect(-RS, -RS, RS * 2, RS * 2);
    var ed = g.createRadialGradient(0, 0, RS * 0.7, 0, 0, RS);
    ed.addColorStop(0, "rgba(4,8,30,0)"); ed.addColorStop(1, "rgba(4,8,30,.45)");
    g.fillStyle = ed; g.fillRect(-RS, -RS, RS * 2, RS * 2);
    g.restore();
    // glass rim and a reflection
    var rg = g.createLinearGradient(-RS, -RS, RS, RS);
    rg.addColorStop(0, "rgba(190,230,255,.9)"); rg.addColorStop(0.5, "rgba(90,160,255,.55)"); rg.addColorStop(1, "rgba(160,220,255,.85)");
    g.strokeStyle = rg; g.lineWidth = 0.8; g.beginPath(); g.arc(0, 0, RS - 0.4, 0, TAU); g.stroke();
    g.strokeStyle = "rgba(255,255,255,.45)"; g.lineWidth = 0.9; g.beginPath(); g.arc(0, 0, RS * 0.84, Math.PI * 1.08, Math.PI * 1.42); g.stroke();
    // front halves and electrons in front
    ORB.forEach(function (o) { ribbon(g, o, RO, rot, true, t, k, glow, small); });
    E.filter(function (e) { return e.p.z >= 0; }).forEach(function (e) { bead(g, e.p.x, e.p.y, e.r * (0.85 + 0.15 * (1 + e.p.z / RO)), e.warm, 1); });
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
    // a canvas that starts hidden (chat, report window) gets its real size when it is shown
    if ("ResizeObserver" in w) new ResizeObserver(function () { var d = Math.min(3, w.devicePixelRatio || 1); if (Math.round(cv.clientWidth * d) !== W || Math.round(cv.clientHeight * d) !== H) { size(); if (!run) frame(-1); } }).observe(cv);
    document.addEventListener("visibilitychange", set);
    if ("IntersectionObserver" in w) new IntersectionObserver(function (en) { vis = en[0].isIntersecting; set(); }).observe(cv);
    set(); frame(-1);
    return { size: size };
  }

  w.NovaSphere = { draw: draw, mount: mount };
  function auto() { [].forEach.call(document.querySelectorAll("canvas[data-sphere]"), function (c) { if (!c._sph) try { c._sph = mount(c, {}); } catch (e) { c._sph = true; } }); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", auto); else auto();
  w.NovaSphere.auto = auto;
})(window);
