/* CRYPTO NOVΛ VIP — core: number format, feeds, router, auth glue, sheet.
   Data truth: every value comes from a published feed; a missing value is «нет данных», never a made-up number. */
(function () {
  'use strict';
  const V = (window.VIP = window.VIP || {});
  V.views = V.views || {};
  V.ui = V.ui || {};          // per-view UI state that survives re-renders (filters, chart period …)

  /* ── public portfolio start (owner spec §6) ─────────────────────────── */
  V.PUBLIC_START_LABEL = '22.10.2026';

  /* ── owner-only data through CNAuth.fetchOwner(path) — the paths in one place ── */
  V.OWNER_PATHS = { ivan: 'ivan', myspot: 'myspot', admin: 'admin' };

  /* ── number format: ru, one style everywhere («$9 923,43», «−1,3%») ─── */
  const MINUS = '−';
  const NF = {};
  const nf = (d) => NF[d] || (NF[d] = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d }));
  const isNum = (v) => v !== null && v !== undefined && v !== '' && isFinite(+v);
  V.isNum = isNum;
  V.n = (v) => (isNum(v) ? +v : null);
  V.num = (v, d = 2) => (isNum(v) ? (+v < 0 ? MINUS : '') + nf(d).format(Math.abs(+v)) : '—');
  V.money = (v, d = 2) => (isNum(v) ? (+v < 0 ? MINUS : '') + '$' + nf(d).format(Math.abs(+v)) : '—');
  V.smoney = (v, d = 2) => {
    if (!isNum(v)) return '—';
    v = +v; const r = +Math.abs(v).toFixed(d);
    return (r === 0 ? '' : v > 0 ? '+' : MINUS) + '$' + nf(d).format(r);
  };
  V.pct = (v, d) => {
    if (!isNum(v)) return '—';
    v = +v; if (d == null) d = Math.abs(v) < 0.1 && v !== 0 ? 2 : 1;
    const r = +Math.abs(v).toFixed(d);
    return (r === 0 ? '' : v > 0 ? '+' : MINUS) + nf(d).format(r) + '%';
  };
  V.share = (v, d = 1) => (isNum(v) ? nf(d).format(+v) + '%' : '—');
  V.price = (p) => {
    if (!isNum(p)) return '—'; p = +p;
    if (p >= 1000) return '$' + nf(0).format(p);
    if (p >= 1) return '$' + nf(2).format(p);
    if (p >= 0.01) return '$' + nf(4).format(p);
    return '$' + (+p.toPrecision(3)).toString().replace('.', ',');
  };
  V.qty = (q) => {
    if (!isNum(q)) return '—'; q = +q;
    const d = q >= 1000 ? 0 : q >= 10 ? 2 : q >= 1 ? 4 : 6;
    return nf(d).format(q);
  };
  V.cls = (v) => (isNum(v) ? (+v > 0 ? 'pos' : +v < 0 ? 'neg' : '') : '');
  V.int = (v) => (isNum(v) ? nf(0).format(Math.round(+v)) : '—');

  /* ── time ───────────────────────────────────────────────────────────── */
  const ms = (t) => {
    if (t == null || t === '') return null;
    if (typeof t === 'string') { const p = Date.parse(t); return isNaN(p) ? null : p; }
    t = +t; if (!isFinite(t)) return null;
    return t < 1e12 ? t * 1000 : t;
  };
  V.ms = ms;
  const pad = (x) => String(x).padStart(2, '0');
  V.date = (t) => { t = ms(t); if (!t) return '—'; const d = new Date(t); return pad(d.getDate()) + '.' + pad(d.getMonth() + 1) + '.' + d.getFullYear(); };
  V.dshort = (t) => { t = ms(t); if (!t) return '—'; const d = new Date(t); return pad(d.getDate()) + '.' + pad(d.getMonth() + 1); };
  V.time = (t) => { t = ms(t); if (!t) return '—'; const d = new Date(t); return pad(d.getHours()) + ':' + pad(d.getMinutes()); };
  V.dt = (t) => { t = ms(t); if (!t) return '—'; return V.dshort(t) + ' ' + V.time(t); };
  V.dtf = (t) => { t = ms(t); if (!t) return '—'; return V.date(t) + ', ' + V.time(t); };
  V.ago = (t) => {
    t = ms(t); if (!t) return 'нет данных';
    const m = Math.round((Date.now() - t) / 60000);
    if (m < 1) return 'только что';
    if (m < 60) return m + ' мин назад';
    const h = Math.round(m / 60);
    if (h < 48) return h + ' ч назад';
    return Math.round(h / 24) + ' дн назад';
  };
  V.ageMin = (t) => { t = ms(t); return t ? (Date.now() - t) / 60000 : null; };
  V.plural = (n, a, b, c) => { n = Math.abs(Math.round(n)); return n % 10 === 1 && n % 100 !== 11 ? a : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? b : c; };

  /* ── html helpers ───────────────────────────────────────────────────── */
  V.esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const esc = V.esc;
  V.ico = (n) => `<svg class="ico" aria-hidden="true"><use href="#i-${n}"/></svg>`;
  V.chip = (t, c = '') => `<span class="chip ${c}">${esc(t)}</span>`;
  V.kpi = (label, value, sub, cls = '', extra = '') =>
    `<div class="kpi ${extra}"><small>${esc(label)}</small><b class="${cls}">${value}</b>${sub ? `<em>${sub}</em>` : ''}</div>`;
  V.stamp = (t, staleMin, prefix = 'обновлено') => {
    t = ms(t);
    if (!t) return `<span class="stamp stale">нет времени обновления</span>`;
    const old = staleMin != null && V.ageMin(t) > staleMin;
    return `<span class="stamp ${old ? 'stale' : ''}" title="${esc(V.dtf(t))}">${prefix} ${esc(V.dt(t))} · ${esc(V.ago(t))}${old ? ' · устарело' : ''}</span>`;
  };
  V.empty = (t) => `<div class="empty">${t}</div>`;
  V.nodata = (what) => V.empty(`<b>Нет данных</b>${what ? ' — ' + esc(what) : ''}`);
  V.skel = (n = 3) => Array.from({ length: n }, (_, i) => `<div class="skel" style="width:${92 - i * 14}%;margin:10px 0"></div>`).join('');
  V.get = (o, path, d = null) => {
    let x = o;
    for (const k of String(path).split('.')) { if (x == null) return d; x = x[k]; }
    return x == null ? d : x;
  };
  V.arr = (x) => (Array.isArray(x) ? x : []);

  /* ── feeds ──────────────────────────────────────────────────────────── */
  // pending: published by the data agent; until they exist the views fall back to the existing feeds.
  V.PENDING_FEEDS = ['spot_pipeline.json', 'strategies.json', 'opportunities.json', 'system_status.json'];
  V.feedLog = {};
  const cache = {};
  const asofOf = (d) => (d && typeof d === 'object' && !Array.isArray(d) ? ms(d.asof || d.time || d.generated || d.updated || d.ts) : null);
  V.asofOf = asofOf;
  V.feed = function (name, maxAge = 60000) {
    const c = cache[name];
    if (c && Date.now() - c.t < maxAge) return c.p;
    const p = fetch('./' + name + '?t=' + Math.floor(Date.now() / 30000), { cache: 'no-store' })
      .then(async (r) => {
        const rec = { name, http: r.status, t: Date.now() };
        if (!r.ok) { rec.ok = false; rec.err = r.status === 404 ? 'missing' : 'http'; V.feedLog[name] = rec; return null; }
        let d;
        try { d = await r.json(); } catch (e) { rec.ok = false; rec.err = 'parse'; V.feedLog[name] = rec; return null; }
        rec.ok = true; rec.asof = asofOf(d); V.feedLog[name] = rec; return d;
      })
      .catch((e) => { V.feedLog[name] = { name, ok: false, err: 'net', t: Date.now() }; return null; });
    cache[name] = { t: Date.now(), p };
    return p;
  };
  V.feeds = (names) => Promise.all(names.map((n) => V.feed(n)));
  V.invalidate = () => { for (const k in cache) delete cache[k]; };

  /* ── auth glue (window.CNAuth from vip/auth.js; until it is there: a placeholder, owner parts locked) ── */
  const NOAUTH = { signedIn: false, email: null, isOwner: false, method: null };
  V.auth = {
    has: () => !!(window.CNAuth && typeof window.CNAuth === 'object' && window.CNAuth.state),
    st: () => (V.auth.has() ? window.CNAuth.state || NOAUTH : NOAUTH),
    owner: () => !!(V.auth.has() && window.CNAuth.state && window.CNAuth.state.isOwner === true && window.CNAuth.state.signedIn !== false),
  };
  V.ownerData = async function (kind) {
    if (!V.auth.owner() || typeof window.CNAuth.fetchOwner !== 'function') return null;
    let r = await window.CNAuth.fetchOwner(V.OWNER_PATHS[kind] || kind);
    if (r && typeof r.json === 'function') {
      if (r.ok === false) throw new Error('HTTP ' + r.status);
      r = await r.json();
    }
    return r || null;
  };
  V.lockBox = (title, text) => `<div class="lock">${V.ico('lock')}<div><b>${esc(title)}</b><p>${text}</p>
      <div style="margin-top:9px"><button class="btn sm" type="button" data-act="login">${V.auth.has() ? 'Войти' : 'Вход'}</button></div></div></div>`;

  /* ── sheet (details drawer) ─────────────────────────────────────────── */
  let sheetOnClose = null;
  V.sheet = function (title, html, onMount, onClose) {
    const s = document.getElementById('sheet'), bg = document.getElementById('sheetBg');
    document.getElementById('sheetTitle').textContent = title;
    const b = document.getElementById('sheetBody');
    b.innerHTML = html; b.scrollTop = 0;
    s.hidden = false; bg.hidden = false;
    sheetOnClose = onClose || null;
    if (onMount) onMount(b);
    setTimeout(() => document.getElementById('sheetClose').focus(), 0);
  };
  V.sheetClose = function () {
    const s = document.getElementById('sheet');
    if (s.hidden) return;
    s.hidden = true; document.getElementById('sheetBg').hidden = true;
    document.getElementById('sheetBody').innerHTML = '';
    const f = sheetOnClose; sheetOnClose = null; if (f) f();
  };

  /* ── account / sign-in ──────────────────────────────────────────────── */
  V.openAccount = function () {
    const st = V.auth.st();
    if (!V.auth.has()) {
      V.sheet('Вход в CRYPTO NOVΛ VIP', `<div class="lock">${V.ico('lock')}<div><b>Модуль входа ещё подключается</b>
        <p>Здесь будет обычный вход: email и пароль, восстановление пароля через почту, Apple / Google и Face ID / Touch ID (passkey).
        Пока модуль не опубликован, закрытые разделы («Мой портфель», стратегия Ivan, администрирование) скрыты, публичные данные доступны.</p></div></div>`);
      return;
    }
    if (st.signedIn) {
      const A = window.CNAuth;
      V.sheet('Аккаунт', `<div class="kv">
          <span>Вход выполнен</span><b>${esc(st.email || '—')}</b>
          <span>Способ</span><b>${esc(methodRu(st.method))}</b>
          <span>Права</span><b>${st.isOwner ? 'владелец' : 'просмотр'}</b></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:16px">
          ${A.passkeySupported && safe(() => A.passkeySupported()) ? '<button class="btn" type="button" data-act="passkey">Включить Face ID / Touch ID</button>' : ''}
          <button class="btn" type="button" data-act="logout">Выйти</button></div>
        <p class="note" id="acctMsg"></p>`, (b) => {
        b.addEventListener('click', async (e) => {
          const t = e.target.closest('[data-act]'); if (!t) return;
          const msg = b.querySelector('#acctMsg');
          try {
            if (t.dataset.act === 'logout') { await A.signOut(); V.sheetClose(); }
            if (t.dataset.act === 'passkey') { await A.passkeyRegister(); msg.textContent = 'Готово: следующий вход — по Face ID / Touch ID.'; }
          } catch (err) { msg.textContent = 'Не получилось: ' + (err && err.message ? err.message : 'ошибка'); }
        });
      });
      return;
    }
    V.sheet('Вход в CRYPTO NOVΛ VIP', '<div id="loginMount"></div>', (b) => {
      try { window.CNAuth.mountLogin(b.querySelector('#loginMount')); }
      catch (e) { b.innerHTML = V.nodata('форма входа не загрузилась'); }
    });
  };
  const safe = (f) => { try { return f(); } catch (e) { return false; } };
  const methodRu = (m) => ({ email: 'email и пароль', password: 'email и пароль', apple: 'Apple', google: 'Google', passkey: 'Face ID / Touch ID (passkey)' }[m] || m || '—');
  V.methodRu = methodRu;

  function paintAccount() {
    const st = V.auth.st(), btn = document.getElementById('acctBtn'), lab = document.getElementById('acctLabel');
    if (!btn) return;
    btn.classList.toggle('on', !!st.signedIn);
    lab.textContent = st.signedIn ? (st.email || 'Аккаунт') : 'Войти';
    btn.title = st.signedIn ? 'Аккаунт: ' + (st.email || '') : 'Вход';
  }

  /* ── system status (shared by the header pill, Обзор and Система) ───── */
  const COMP_RU = {
    engine: 'Движок: демо-столы, каждую минуту', public_real: 'Реальный счёт OKX: зеркало', app_home: 'Сводка приложения',
    markets: 'Рыночные данные', ideas: 'Таблица «Что брать на спот»', portfolio: 'Портфели и решения', paper: 'Демо-столы',
    candles: 'Свечи', desk_select: 'Выбор основного стола', autopilot: 'Автопилот OKX', mirror: 'Зеркало на Mac',
  };
  V.compName = (k, c) => (c && (c.name_ru || c.name)) || COMP_RU[k] || k;
  // the fallback: component → [feed, field, stale after N minutes]
  const FALLBACK = [
    ['engine', 'engine.json', 10], ['public_real', 'public_real.json', 60], ['app_home', 'app_home.json', 30],
    ['markets', 'markets.json', 60], ['paper', 'paper.json', 15], ['ideas', 'ideas.json', 120],
    ['portfolio', 'portfolio.json', 120], ['desk_select', 'desk_select.json', 24 * 60],
  ];
  V.sysStatus = async function () {
    const [SS, R, E, ST] = await V.feeds(['system_status.json', 'public_real.json', 'engine.json', 'status.json']);
    let comps = [], errors = [], src = 'system_status.json';
    const list = SS && (SS.components || SS.items || null);
    if (SS && list) {
      const entries = Array.isArray(list) ? list.map((c) => [c.key || c.id || c.name, c]) : Object.entries(list);
      comps = entries.filter(([, c]) => c && typeof c === 'object').map(([k, c]) => ({
        key: k, name: V.compName(k, c), status: ['ok', 'stale', 'error', 'stopped'].includes(c.status) ? c.status : 'unknown',
        last_ok: ms(c.last_ok), age_min: isNum(c.age_min) ? +c.age_min : V.ageMin(c.last_ok), note: c.note_ru || c.note || c.why_ru || '',
      }));
      errors = V.arr(SS.errors || SS.error_log);
    } else {
      src = 'fallback';
      await V.feeds(FALLBACK.map((f) => f[1]));
      for (const [k, f, lim] of FALLBACK) {
        const rec = V.feedLog[f];
        if (!rec) continue;
        let status = 'ok', note = '';
        const last = rec.asof;
        const age = V.ageMin(last);
        if (!rec.ok) { status = 'error'; note = rec.err === 'missing' ? 'файл не найден' : 'файл не читается'; }
        else if (age == null) { status = 'unknown'; note = 'в файле нет времени'; }
        else if (age > lim) { status = 'stale'; note = 'нет обновления ' + V.ago(last).replace(' назад', ''); }
        if (k === 'public_real' && R && R.stale) { status = 'stale'; note = 'зеркало пометило данные как устаревшие'; }
        if (k === 'engine' && E && V.arr(E.errors).length) { status = 'error'; note = 'ошибки в шагах движка'; }
        comps.push({ key: k, name: V.compName(k), status, last_ok: rec.ok ? last : null, age_min: rec.ok ? age : null, note });
      }
      if (E) {
        for (const er of V.arr(E.errors)) errors.push({ t: ms(er.t || er.time) || V.asofOf(E), component: 'engine', type: String(er.step || er.kind || 'step'), status: 'open', text: String(er.msg || er.error || '').slice(0, 140) });
        for (const [k, s] of Object.entries(E.steps || {})) if (s && s.ok === false) errors.push({ t: V.asofOf(E), component: 'engine:' + k, type: 'шаг не выполнен', status: 'open' });
      }
      if (ST) for (const s of V.arr(ST.stale)) errors.push({ t: ms(ST.time), component: String(s.name || s.file || s), type: 'устаревшие данные', status: 'open' });
    }
    const has = (s) => comps.some((c) => c.status === s);
    const overall = !comps.length ? 'unknown' : has('error') ? 'error' : has('stale') ? 'stale' : has('stopped') ? 'stopped' : has('unknown') ? 'unknown' : 'ok';
    const last = Math.max(0, ...comps.map((c) => c.last_ok || 0)) || null;
    const label = { ok: 'Работает', stale: 'Данные устарели', error: 'Ошибка данных', stopped: 'Остановлена', unknown: 'Статус неизвестен' }[overall];
    return { overall, label, comps, errors, last, src, asof: SS ? V.asofOf(SS) : null };
  };
  V.statusRu = (s) => ({ ok: 'работает', stale: 'устарело', error: 'ошибка', stopped: 'остановлено', unknown: 'неизвестно' }[s] || s);

  async function paintPill() {
    const p = document.getElementById('sysPill'); if (!p) return;
    try {
      const s = await V.sysStatus();
      const bad = s.comps.filter((c) => c.status !== 'ok');
      if (V.notifyStatus) V.notifyStatus(s);
      p.innerHTML = `<span class="dot ${s.overall}"></span>${esc(s.label)}${s.last ? ' · ' + esc(V.time(s.last)) : ''}`;
      p.title = s.overall === 'ok' ? 'Все компоненты обновляются' : 'Проблемы: ' + bad.map((c) => c.name + ' — ' + V.statusRu(c.status)).join('; ');
    } catch (e) { p.innerHTML = '<span class="dot"></span>Статус неизвестен'; }
  }
  V.paintPill = paintPill;

  /* ── router ─────────────────────────────────────────────────────────── */
  V.ROUTES = [
    { id: 'overview', name: 'Обзор', short: 'Обзор', icon: 'overview' },
    { id: 'spot', name: 'Спот', short: 'Спот', icon: 'spot' },
    { id: 'futures', name: 'Фьючерсы', short: 'Фьючерсы', icon: 'futures' },
    { id: 'strategies', name: 'Стратегии', short: 'Стратегии', icon: 'strategies' },
    { id: 'opps', name: 'Другие возможности', short: 'Другое', icon: 'opps' },
    { id: 'demo', name: 'Live Demo', short: 'Live', icon: 'demo' },
    { id: 'system', name: 'Система', short: 'Система', icon: 'system' },
  ];
  V.parse = () => {
    const h = (location.hash || '').replace(/^#\/?/, '');
    const [id, ...rest] = h.split('/');
    const r = V.ROUTES.find((x) => x.id === id) ? id : 'overview';
    return { id: r, arg: rest.length ? decodeURIComponent(rest.join('/')) : null };
  };
  V.go = (id, arg) => { const h = '#/' + id + (arg ? '/' + encodeURIComponent(arg) : ''); if (location.hash === h) V.render(); else location.hash = h; };

  function paintNav(cur) {
    const html = (short) => V.ROUTES.map((r) =>
      `<a href="#/${r.id}" ${r.id === cur ? 'aria-current="page"' : ''}>${V.ico(r.icon)}<span>${esc(short ? r.short : r.name)}</span></a>`).join('');
    document.getElementById('navSide').innerHTML = html(false);
    document.getElementById('navBottom').innerHTML = html(true);
    const r = V.ROUTES.find((x) => x.id === cur);
    document.getElementById('topTitle').textContent = r ? r.name : '';
    document.title = (r ? r.name + ' · ' : '') + 'CRYPTO NOVΛ VIP';
  }

  let tok = 0, lastId = null;
  V.render = async function (opts = {}) {
    const { id, arg } = V.parse();
    const my = ++tok;
    const el = document.getElementById('view');
    const same = id === lastId;
    const y = same && opts.keepScroll ? window.scrollY : 0;
    paintNav(id);
    if (!same || !opts.keepScroll) el.innerHTML = `<div class="card">${V.skel(4)}</div>`;
    lastId = id;
    const view = V.views[id];
    try {
      const html = await view.html(arg);
      if (my !== tok) return;
      el.innerHTML = html;
      if (view.mount) await view.mount(el, arg);
      if (my !== tok) return;
      if (opts.keepScroll) window.scrollTo(0, y); else if (!same) window.scrollTo(0, 0);
    } catch (e) {
      if (my !== tok) return;
      el.innerHTML = `<div class="card">${V.nodata('раздел не собрался')}<p class="note">${esc(e && e.message ? e.message : e)}</p></div>`;
      if (window.console) console.warn('[vip] view', id, e);
    }
    document.dispatchEvent(new CustomEvent('vip:rendered', { detail: { id, arg } }));
  };

  /* ── global clicks ──────────────────────────────────────────────────── */
  document.addEventListener('click', (e) => {
    const a = e.target.closest('[data-act]');
    if (!a) return;
    const act = a.dataset.act;
    if (act === 'login') { e.preventDefault(); V.openAccount(); }
    else if (act === 'go') { e.preventDefault(); V.go(a.dataset.to, a.dataset.arg || null); }
  });

  V.start = function () {
    document.getElementById('acctBtn').addEventListener('click', V.openAccount);
    document.getElementById('sysPill').addEventListener('click', () => V.go('system'));
    document.getElementById('sheetClose').addEventListener('click', V.sheetClose);
    document.getElementById('sheetBg').addEventListener('click', V.sheetClose);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') V.sheetClose(); });
    window.addEventListener('hashchange', () => { V.sheetClose(); V.render(); });
    if (V.auth.has() && typeof window.CNAuth.onChange === 'function') {
      try { window.CNAuth.onChange(() => { paintAccount(); V.render({ keepScroll: true }); }); } catch (e) { /* ignore */ }
    }
    paintAccount();
    paintPill();
    V.render();
    if (V.demo && V.demo.resume) V.demo.resume();
    // refresh: every 5 minutes while the tab is visible; the presenter is never interrupted by a re-render
    setInterval(() => {
      if (document.hidden) return;
      V.invalidate(); paintPill();
      if (!(V.demo && V.demo.active && V.demo.active()) && document.getElementById('sheet').hidden) V.render({ keepScroll: true });
    }, 5 * 60000);
  };
})();
