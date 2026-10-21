/* Copy of the CRYPTO NOVA brand sphere for the system panel. Source of truth: cryptonova-site/assets/sphere.js — change it there, then copy here.
   Panel copy adds: a canvas hidden when it mounted (chat, report window) resizes itself when shown; a 0-px canvas is skipped; one failing canvas does not stop the rest. */
/* CRYPTO NOVA — the NOVA AI sphere. One crisp glass sphere with a slowly turning light lattice inside.
   NovaSphere.mount(canvas, {ring:true}) animates it; NovaSphere.draw(ctx, cx, cy, R, t, opt) draws one frame (icons, exports).
   Brand rule: crisp rim, tight glow, smooth slow motion — never a blurry blob. */
(function (w) {
  "use strict";
  var TAU = Math.PI * 2;
  function rgba(c, a) { return "rgba(" + c + "," + a.toFixed(3) + ")"; }

  /* project a point on the unit sphere: spin around Y, then tilt toward the viewer around X */
  function proj(lat, lon, spin, tilt) {
    var cl = Math.cos(lat), x = cl * Math.cos(lon + spin), y = Math.sin(lat), z = cl * Math.sin(lon + spin);
    var ct = Math.cos(tilt), st = Math.sin(tilt);
    return { x: x, y: y * ct - z * st, z: y * st + z * ct };
  }

  /* a neural lattice: points spread evenly over the sphere, each wired to its nearest neighbours */
  var NET = {};
  function net(n) {
    if (NET[n]) return NET[n];
    var P = [], E = [], ga = Math.PI * (3 - Math.sqrt(5));
    for (var i = 0; i < n; i++) { var y = 1 - (i + 0.5) / n * 2, r = Math.sqrt(1 - y * y), th = ga * i; P.push({ lat: Math.asin(y), lon: Math.atan2(Math.sin(th) * r, Math.cos(th) * r) }); }
    function v(p) { return [Math.cos(p.lat) * Math.cos(p.lon), Math.sin(p.lat), Math.cos(p.lat) * Math.sin(p.lon)]; }
    var V = P.map(v), seen = {};
    V.forEach(function (a, i) {
      var d = V.map(function (b, j) { return [j, (a[0] - b[0]) * (a[0] - b[0]) + (a[1] - b[1]) * (a[1] - b[1]) + (a[2] - b[2]) * (a[2] - b[2])]; }).sort(function (x, y) { return x[1] - y[1]; });
      for (var k = 1; k <= 4; k++) { var j = d[k][0], key = Math.min(i, j) + "-" + Math.max(i, j); if (!seen[key]) { seen[key] = 1; E.push([i, j]); } }
    });
    return (NET[n] = { P: P, E: E });
  }

  function lattice(ctx, cx, cy, R, t, spin, tilt, small, still) {
    var nw = net(small ? 22 : 72), Q = nw.P.map(function (p) { return proj(p.lat, p.lon, spin, tilt); }), lw = Math.max(0.8, R / 110);
    ctx.lineCap = "round";
    nw.E.forEach(function (e) {
      var a = Q[e[0]], b = Q[e[1]], z = (a.z + b.z) / 2;
      ctx.strokeStyle = z > 0 ? rgba("205,240,255", 0.12 + 0.36 * z) : rgba("140,190,255", 0.06);
      ctx.lineWidth = z > 0 ? lw : lw * 0.8;
      ctx.beginPath(); ctx.moveTo(cx + a.x * R, cy - a.y * R); ctx.lineTo(cx + b.x * R, cy - b.y * R); ctx.stroke();
    });
    // signals running along the wires
    if (!still) {
      var m = small ? 3 : 14;
      for (var k = 0; k < m; k++) {
        var tk = Math.abs(t) * 0.7 + k * 0.31, e = nw.E[(k * 37 + Math.floor(tk)) % nw.E.length], ph = tk % 1;
        var a = Q[e[0]], b = Q[e[1]], x = a.x + (b.x - a.x) * ph, y = a.y + (b.y - a.y) * ph, z = a.z + (b.z - a.z) * ph;
        if (z <= 0) continue;
        var r = Math.max(1, R / 55) * (0.6 + 0.4 * z);
        var g = ctx.createRadialGradient(cx + x * R, cy - y * R, 0, cx + x * R, cy - y * R, r * 4);
        g.addColorStop(0, rgba("140,255,214", 0.55 * z)); g.addColorStop(1, "rgba(140,255,214,0)");
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx + x * R, cy - y * R, r * 4, 0, TAU); ctx.fill();
        ctx.fillStyle = rgba("230,255,246", 0.9 * z); ctx.beginPath(); ctx.arc(cx + x * R, cy - y * R, r, 0, TAU); ctx.fill();
      }
    }
    Q.forEach(function (q) {
      if (q.z <= -0.2) return;
      var r = Math.max(0.9, R / 70) * (0.55 + 0.45 * Math.max(0, q.z));
      ctx.fillStyle = rgba("235,250,255", 0.25 + 0.6 * Math.max(0, q.z)); ctx.beginPath(); ctx.arc(cx + q.x * R, cy - q.y * R, r, 0, TAU); ctx.fill();
    });
  }

  function draw(ctx, cx, cy, R, t, opt) {
    opt = opt || {};
    var small = R < 34, spin = t * 0.18, tilt = 0.42, breathe = 0.5 + 0.5 * Math.sin(t * 1.3);
    // tight halo — just enough to read as light, edge stays sharp
    var g = ctx.createRadialGradient(cx, cy, R * 0.92, cx, cy, R * (small ? 1.28 : 1.42));
    g.addColorStop(0, rgba("110,200,255", 0.30 + 0.08 * breathe)); g.addColorStop(1, "rgba(110,200,255,0)");
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, R * 1.45, 0, TAU); ctx.fill();
    if (opt.ring) {   // back half of the orbit ring
      ring(ctx, cx, cy, R, t, false);
    }
    ctx.save(); ctx.beginPath(); ctx.arc(cx, cy, R, 0, TAU); ctx.clip();
    // glass body: ice light from the upper left falling into deep navy
    var b = ctx.createRadialGradient(cx - R * 0.06, cy - R * 0.08, 0, cx, cy, R * 1.08);
    b.addColorStop(0, "#EFFBFF"); b.addColorStop(0.16, "#9BE2FF"); b.addColorStop(0.42, "#2F8BEA");
    b.addColorStop(0.7, "#14409E"); b.addColorStop(0.9, "#0A1D57"); b.addColorStop(1, "#050B22");
    ctx.fillStyle = b; ctx.fillRect(cx - R, cy - R, R * 2, R * 2);
    // a second, cooler light from below — gives the glass depth
    var d = ctx.createRadialGradient(cx + R * 0.38, cy + R * 0.62, 0, cx + R * 0.38, cy + R * 0.62, R * 0.95);
    d.addColorStop(0, "rgba(96,120,255,.42)"); d.addColorStop(1, "rgba(96,120,255,0)");
    ctx.fillStyle = d; ctx.fillRect(cx - R, cy - R, R * 2, R * 2);
    // the core breathing inside
    var c = ctx.createRadialGradient(cx - R * 0.06, cy - R * 0.08, 0, cx - R * 0.06, cy - R * 0.08, R * 0.5);
    c.addColorStop(0, rgba("255,255,255", 0.35 + 0.2 * breathe)); c.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = c; ctx.fillRect(cx - R, cy - R, R * 2, R * 2);
    lattice(ctx, cx, cy, R * 0.9, t, spin, tilt, small, opt.still);
    // edge shading → volume
    var e = ctx.createRadialGradient(cx, cy, R * 0.78, cx, cy, R);
    e.addColorStop(0, "rgba(2,6,22,0)"); e.addColorStop(1, "rgba(2,6,22,.55)");
    ctx.fillStyle = e; ctx.fillRect(cx - R, cy - R, R * 2, R * 2);
    // bottom caustic
    var f = ctx.createRadialGradient(cx + R * 0.05, cy + R * 1.02, 0, cx + R * 0.05, cy + R * 1.02, R * 0.62);
    f.addColorStop(0, "rgba(140,230,255,.38)"); f.addColorStop(1, "rgba(140,230,255,0)");
    ctx.fillStyle = f; ctx.fillRect(cx - R, cy - R, R * 2, R * 2);
    // specular highlight
    ctx.save(); ctx.translate(cx - R * 0.36, cy - R * 0.5); ctx.rotate(-0.55); ctx.scale(1, 0.52);
    var h = ctx.createRadialGradient(0, 0, 0, 0, 0, R * 0.34);
    h.addColorStop(0, "rgba(255,255,255,.72)"); h.addColorStop(0.55, "rgba(255,255,255,.18)"); h.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = h; ctx.beginPath(); ctx.arc(0, 0, R * 0.34, 0, TAU); ctx.fill(); ctx.restore();
    ctx.restore();
    // crisp rim
    var rg = ctx.createLinearGradient(cx - R, cy - R, cx + R, cy + R);
    rg.addColorStop(0, "rgba(225,247,255,.95)"); rg.addColorStop(0.5, "rgba(120,200,255,.55)"); rg.addColorStop(1, "rgba(80,120,255,.85)");
    ctx.strokeStyle = rg; ctx.lineWidth = Math.max(1, R / 55); ctx.beginPath(); ctx.arc(cx, cy, R - ctx.lineWidth / 2, 0, TAU); ctx.stroke();
    if (opt.ring) ring(ctx, cx, cy, R, t, true);
  }

  /* a thin tilted orbit with one satellite — the sphere is "working" */
  function ring(ctx, cx, cy, R, t, front) {
    var rx = R * 1.32, ry = R * 0.36, rot = -0.32, a0 = front ? 0 : Math.PI, a1 = front ? Math.PI : TAU;
    ctx.save(); ctx.translate(cx, cy); ctx.rotate(rot);
    ctx.strokeStyle = front ? "rgba(170,225,255,.55)" : "rgba(170,225,255,.18)"; ctx.lineWidth = Math.max(1, R / 80);
    ctx.beginPath(); ctx.ellipse(0, 0, rx, ry, 0, a0, a1); ctx.stroke();
    var s = (t * 0.5) % TAU, inFront = Math.sin(s) > 0;
    if (inFront === front) {
      var x = Math.cos(s) * rx, y = Math.sin(s) * ry, r = Math.max(1.6, R / 26);
      var g = ctx.createRadialGradient(x, y, 0, x, y, r * 3.2); g.addColorStop(0, "rgba(120,255,200,.75)"); g.addColorStop(1, "rgba(120,255,200,0)");
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, r * 3.2, 0, TAU); ctx.fill();
      ctx.fillStyle = "#C9FFE9"; ctx.beginPath(); ctx.arc(x, y, r, 0, TAU); ctx.fill();
    }
    ctx.restore();
  }

  /* animate on a canvas; the sphere fills the canvas (with room for the halo/ring) */
  function mount(cv, opt) {
    if (!cv || !cv.getContext) return null;
    opt = opt || {};
    var ctx = cv.getContext("2d"), dpr = 1, W = 0, H = 0, run = false, vis = true, t0 = performance.now();
    var reduce = w.matchMedia && w.matchMedia("(prefers-reduced-motion: reduce)").matches;
    function size() {
      dpr = Math.min(w.devicePixelRatio || 1, 2);
      W = cv.width = Math.max(1, Math.round(cv.clientWidth * dpr)); H = cv.height = Math.max(1, Math.round(cv.clientHeight * dpr));
    }
    function frame(now) {
      if (!run && now !== -1) return;
      if (Math.round(cv.clientWidth * Math.min(w.devicePixelRatio || 1, 2)) !== W) size();
      var t = Math.max(0, ((now === -1 ? t0 + 2600 : now) - t0) / 1000);
      ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, W, H);
      var R = Math.min(W, H) / 2 / (opt.ring ? 1.5 : 1.46);
      if (R >= 4) draw(ctx, W / 2, H / 2, R, t, { ring: opt.ring, still: now === -1 });   // hidden canvas (0 px): skip until it is shown
      if (opt.onFrame) opt.onFrame(t);
      if (now !== -1) requestAnimationFrame(frame);
    }
    function set() { var want = vis && !document.hidden && !reduce; if (want && !run) { run = true; requestAnimationFrame(frame); } else if (!want) { run = false; frame(-1); } }
    size(); w.addEventListener("resize", function () { size(); if (!run) frame(-1); });
    document.addEventListener("visibilitychange", set);
    if ("IntersectionObserver" in w) new IntersectionObserver(function (en) { vis = en[0].isIntersecting; set(); }).observe(cv);
    set(); frame(-1);
    return { size: size };
  }

  w.NovaSphere = { draw: draw, mount: mount };
  /* every <canvas data-sphere> on the page comes alive by itself */
  function auto() { [].forEach.call(document.querySelectorAll("canvas[data-sphere]"), function (c) { if (!c._sph) { try { c._sph = mount(c, { ring: c.getAttribute("data-sphere") === "ring" }); } catch (e) { /* one bad canvas must not stop the others */ } } }); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", auto); else auto();
  w.NovaSphere.auto = auto;
})(window);
