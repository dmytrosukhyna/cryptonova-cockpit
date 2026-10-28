/* CRYPTO NOVΛ VIP — «Система»: components, data, background tasks, errors log (no raw traces, no secrets), settings, session,
   owner-only admin (owner spec §12). */
(function () {
  'use strict';
  const V = window.VIP, esc = V.esc, A = V.arr;
  const LS = { get: (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } }, set: (k, v) => { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) { /* private mode */ } } };
  const pref = (k) => LS.get('vip.' + k) === '1';
  const applyPrefs = () => document.body.classList.toggle('compact', pref('compact'));
  applyPrefs();

  // browser notification when the system status turns bad (only after the owner turned it on here)
  let lastOverall = null;
  V.notifyStatus = function (S) {
    if (!pref('notify') || !('Notification' in window) || Notification.permission !== 'granted') { lastOverall = S.overall; return; }
    if (lastOverall && lastOverall === 'ok' && S.overall !== 'ok') {
      try { new Notification('CRYPTO NOVΛ VIP', { body: 'Состояние системы: ' + S.label }); } catch (e) { /* ignore */ }
    }
    lastOverall = S.overall;
  };

  V.views.system = {
    async html() {
      const [S, E, ST] = await Promise.all([V.sysStatus(), V.feed('engine.json'), V.feed('status.json')]);
      V.notifyStatus(S);
      return `<div class="ph"><div><h2>Система</h2><p>Состояние API, данных и фоновых задач, журнал ошибок, настройки и сессия.</p></div></div>
        <div class="grid g-main" style="margin-bottom:var(--gap)">${compCard(S)}${sessionCard()}</div>
        <div class="grid g2" style="margin-bottom:var(--gap)">${tasksCard(E, ST)}${feedsCard()}</div>
        ${errorsCard(S)}
        <div class="grid g2" style="margin-top:var(--gap)">${settingsCard()}${adminCard()}</div>`;
    },
    async mount(el) {
      el.querySelectorAll('[data-pref]').forEach((i) => i.addEventListener('change', async () => {
        const k = i.dataset.pref;
        if (k === 'notify' && i.checked && 'Notification' in window && Notification.permission !== 'granted') {
          const p = await Notification.requestPermission().catch(() => 'denied');
          if (p !== 'granted') { i.checked = false; const m = el.querySelector('#prefMsg'); if (m) m.textContent = 'Браузер не разрешил уведомления.'; return; }
        }
        LS.set('vip.' + k, i.checked ? '1' : null); applyPrefs();
      }));
      el.querySelectorAll('[data-sys]').forEach((b) => b.addEventListener('click', async () => {
        const A2 = window.CNAuth, m = el.querySelector('#sessMsg');
        try {
          if (b.dataset.sys === 'logout') { await A2.signOut(); }
          if (b.dataset.sys === 'passkey') { await A2.passkeyRegister(); if (m) m.textContent = 'Готово: следующий вход — по Face ID / Touch ID.'; }
          if (b.dataset.sys === 'refresh') { V.invalidate(); V.paintPill(); V.render({ keepScroll: true }); }
        } catch (e) { if (m) m.textContent = 'Не получилось: ' + (e && e.message ? e.message : 'ошибка'); }
      }));
      const box = el.querySelector('#mySpot');
      if (box && V.auth.owner()) {
        try {
          const d = await V.ownerData('myspot');
          box.innerHTML = d ? mySpot(d) : V.nodata('личный счёт не опубликован');
        } catch (e) { box.innerHTML = V.nodata('личный счёт не загрузился: ' + (e && e.message ? e.message : 'ошибка')); }
      }
    },
  };

  function compCard(S) {
    return `<section class="card" data-demo="sys-comps">
      <div class="card-h"><h3>Компоненты</h3><span class="sp"></span><span class="dot ${S.overall}"></span><b>${esc(S.label)}</b>
        <button class="btn sm ghost" type="button" data-sys="refresh">Обновить</button></div>
      ${S.comps.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th class="l">Компонент</th><th class="l">Статус</th><th>Последний успех</th><th class="hide-sm">Возраст</th></tr></thead><tbody>
        ${S.comps.map((c) => `<tr><td class="l wrap">${esc(c.name)}${c.note ? `<br><span class="xs muted clamp2" title="${esc(c.note)}">${esc(c.note)}</span>` : ''}</td>
          <td class="l"><span class="dot ${c.status}"></span> ${esc(V.statusRu(c.status))}</td>
          <td>${c.last_ok ? esc(V.dt(c.last_ok)) : '—'}</td><td class="hide-sm">${V.isNum(c.age_min) ? esc(ageTxt(c.age_min)) : '—'}</td></tr>`).join('')}</tbody></table></div>` : V.nodata()}
      <p class="note">${S.src === 'fallback' ? 'Статус собран по времени обновления опубликованных файлов: «работает» только если файл обновился вовремя. system_status.json ещё не опубликован.' : 'Источник: system_status.json' + (S.asof ? ' · ' + esc(V.dt(S.asof)) : '') + '.'}</p>
    </section>`;
  }
  const ageTxt = (m) => (m < 1 ? '< 1 мин' : m < 60 ? Math.round(m) + ' мин' : m < 48 * 60 ? Math.round(m / 60) + ' ч' : Math.round(m / 1440) + ' дн');

  function tasksCard(E, ST) {
    const steps = Object.entries((E && E.steps) || {}).filter(([k]) => !k.startsWith('_'));
    const ls = E && E.last_sync;
    return `<section class="card" data-demo="sys-tasks">
      <div class="card-h"><h3>Фоновые задачи</h3><span class="sp"></span>${E ? V.stamp(E.asof, 10) : ''}</div>
      ${E ? `<div class="kv"><span>Цикл движка</span><b>№ ${V.int(E.cycle)} · каждые ${V.int(E.interval_sec)} с (последний ${V.num(E.cycle_sec, 1)} с)</b>
          <span>Запущен</span><b>${esc(V.dt(E.started))}</b>
          <span>Синхронизация с репозиторием</span><b>${ls ? esc(V.dt(ls.t)) + ' · ' + [ls.pulled ? 'получено' : '', ls.pushed ? 'отправлено' : ''].filter(Boolean).join(', ') : '—'}</b></div>
        <div class="tbl-wrap" style="margin-top:10px"><table class="tbl"><thead><tr><th class="l">Шаг</th><th class="l">Статус</th><th>Время</th></tr></thead><tbody>
          ${steps.map(([k, s]) => `<tr><td class="l">${esc(k)}</td><td class="l"><span class="dot ${s && s.ok ? 'ok' : 'error'}"></span> ${s && s.ok ? 'выполнен' : 'ошибка'}</td><td>${V.isNum(s && s.sec) ? V.num(s.sec, 1) + ' с' : '—'}</td></tr>`).join('')}</tbody></table></div>
        ${E.about_ru ? `<p class="note">${esc(E.about_ru)}</p>` : ''}` : V.nodata('engine.json')}
      ${ST ? `<p class="note">Свечи: ${V.int(Object.keys(ST.candles || {}).length)} рынков · устаревших: ${V.int(A(ST.stale).length)} · status.json ${esc(V.dt(ST.time))}.</p>` : ''}
    </section>`;
  }

  function feedsCard() {
    const recs = Object.values(V.feedLog).sort((a, b) => a.name.localeCompare(b.name));
    return `<section class="card" data-demo="sys-feeds">
      <div class="card-h"><h3>Данные этой страницы</h3><span class="sub">файлы, загруженные в браузере</span></div>
      <div class="tbl-wrap"><table class="tbl"><thead><tr><th class="l">Файл</th><th class="l">Состояние</th><th>Данные на</th></tr></thead><tbody>
        ${recs.map((r) => `<tr><td class="l">${esc(r.name)}</td><td class="l">${r.ok ? '<span class="dot ok"></span> загружен' : r.err === 'missing' ? (V.PENDING_FEEDS.includes(r.name) ? '<span class="dot idle"></span> ещё не опубликован' : '<span class="dot error"></span> не найден') : '<span class="dot error"></span> ошибка загрузки'}</td>
          <td>${r.asof ? esc(V.dt(r.asof)) : '—'}</td></tr>`).join('')}</tbody></table></div>
      <p class="note">Ключи бирж и пароли во фронтенд не попадают: страница читает только опубликованные файлы; закрытые данные — через сессию владельца.</p>
    </section>`;
  }

  function errorsCard(S) {
    const rows = S.errors.map((e) => ({ t: V.ms(e.t || e.time || e.ts), comp: e.component || e.comp || '—', type: e.type_ru || e.type || e.kind || '—', status: e.status_ru || e.status || 'open', text: e.text_ru || e.message_ru || e.text || '' }));
    for (const r of Object.values(V.feedLog)) if (!r.ok && !(r.err === 'missing' && V.PENDING_FEEDS.includes(r.name)))
      rows.push({ t: r.t, comp: 'панель · ' + r.name, type: r.err === 'missing' ? 'файл не найден' : r.err === 'parse' ? 'файл не читается' : 'ошибка загрузки', status: 'open', text: '' });
    rows.sort((a, b) => (b.t || 0) - (a.t || 0));
    const stRu = (s) => ({ open: 'не исправлено', fixed: 'исправлено', resolved: 'исправлено', ack: 'в работе', investigating: 'в работе' }[s] || s);
    return `<section class="card" data-demo="sys-errors">
      <div class="card-h"><h3>Журнал ошибок</h3><span class="sub">время, компонент, тип, статус — без трассировок и секретов</span></div>
      ${rows.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th class="l">Время</th><th class="l">Компонент</th><th class="l">Тип</th><th class="l">Статус</th></tr></thead><tbody>
        ${rows.slice(0, 50).map((r) => `<tr><td class="l">${esc(V.dt(r.t))}</td><td class="l">${esc(r.comp)}</td><td class="l wrap">${esc(r.type)}${r.text ? `<br><span class="xs muted">${esc(String(r.text).replace(/(key|secret|token|pass)[^\s]*/gi, '•••').slice(0, 140))}</span>` : ''}</td><td class="l">${esc(stRu(r.status))}</td></tr>`).join('')}</tbody></table></div>`
        : V.empty('Ошибок не зарегистрировано.')}
    </section>`;
  }

  function settingsCard() {
    const notif = 'Notification' in window;
    return `<section class="card" data-demo="sys-settings">
      <div class="card-h"><h3>Настройки</h3><span class="sub">хранятся в этом браузере</span></div>
      <label class="toggle"><div><b>Уведомления</b><small>Сообщить в браузере, если данные системы устарели или появилась ошибка.${notif ? '' : ' Браузер не поддерживает уведомления.'}</small></div>
        <input class="switch" type="checkbox" data-pref="notify" ${pref('notify') ? 'checked' : ''} ${notif ? '' : 'disabled'}></label>
      <label class="toggle"><div><b>Компактные таблицы</b><small>Меньше отступов в строках.</small></div><input class="switch" type="checkbox" data-pref="compact" ${pref('compact') ? 'checked' : ''}></label>
      <p class="note" id="prefMsg"></p>
    </section>`;
  }

  function sessionCard() {
    const st = V.auth.st();
    let body;
    if (!V.auth.has()) body = `<div class="lock">${V.ico('lock')}<div><b>Модуль входа ещё подключается</b><p>Когда он будет опубликован (vip/auth.js), здесь будут вход по email и паролю, восстановление пароля, Apple / Google, Face ID / Touch ID и выход. До этого закрытые данные скрыты.</p></div></div>`;
    else if (!st.signedIn) body = V.lockBox('Вы не вошли', 'Публичные разделы доступны и так; «Мой портфель», статистика Ivan и администрирование — после входа владельца.');
    else body = `<div class="kv"><span>Аккаунт</span><b>${esc(st.email || '—')}</b><span>Способ входа</span><b>${esc(V.methodRu(st.method))}</b><span>Права</span><b>${st.isOwner ? 'владелец' : 'просмотр'}</b></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">${window.CNAuth.passkeySupported && safe(() => window.CNAuth.passkeySupported()) ? '<button class="btn sm" type="button" data-sys="passkey">Включить Face ID / Touch ID</button>' : ''}
        <button class="btn sm" type="button" data-sys="logout">Выйти</button></div><p class="note" id="sessMsg"></p>`;
    return `<section class="card" data-demo="sys-session"><div class="card-h"><h3>Сессия</h3></div>${body}</section>`;
  }
  const safe = (f) => { try { return f(); } catch (e) { return false; } };

  function adminCard() {
    if (!V.auth.owner()) return `<section class="card" data-demo="sys-admin"><div class="card-h"><h3>Администрирование</h3>${V.chip('только владелец', 'owner')}</div>
      ${V.lockBox('Только для владельца', 'Административные функции и личный счёт видны только в сессии владельца.')}</section>`;
    return `<section class="card" data-demo="sys-admin"><div class="card-h"><h3>Администрирование</h3>${V.chip('владелец', 'owner')}</div>
      <div class="list">
        <div class="li"><span class="t">${V.ico('ext')}</span><span class="d"><a href="admin.html">Админ-панель приложения</a> — пользователи, рассылки, коннекторы; права проверяет сервер.</span><span class="v"></span></div>
        <div class="li"><span class="t">${V.ico('ext')}</span><span class="d"><a href="app.html">Текущая рабочая панель</a> — без изменений до перехода на новую.</span><span class="v"></span></div>
      </div>
      <p class="note">Просмотр тестовых стратегий не даёт права управлять реальным счётом. Торговые настройки, плечо, лимиты риска и стопы из этой панели не меняются.</p>
      <div class="sec-t"><h3>Мой портфель</h3>${V.chip('личный счёт владельца', 'owner')}</div>
      <div id="mySpot">${V.skel(3)}</div>
    </section>`;
  }

  function mySpot(d) {
    const p = d.plan || {}, rows = A(p.rows).slice().sort((a, b) => (b.usd || 0) - (a.usd || 0)).slice(0, 12);
    return `<p class="note" style="margin-top:0">Личный счёт OKX владельца — не публичный портфель и не демо. ${V.stamp(d.asof, 120)}</p>
      <div class="kpis k3">${V.kpi('Стоимость', V.money(p.total_usd), '')}${V.kpi('Доллары', p.cash ? V.share(p.cash.now_pct) : '—', p.cash ? 'цель ' + V.share(p.cash.target_pct, 0) : '')}${V.kpi('Позиций', V.int(A(p.rows).length), '')}</div>
      ${rows.length ? `<div class="tbl-wrap" style="margin-top:8px"><table class="tbl"><thead><tr><th class="l">Актив</th><th>Стоимость</th><th>Доля</th></tr></thead><tbody>
        ${rows.map((r) => `<tr><td class="l"><span class="sym">${esc(r.ccy || r.sym || '—')}</span></td><td>${V.money(r.usd)}</td><td>${V.share(V.isNum(r.share) ? r.share : p.total_usd ? (r.usd / p.total_usd) * 100 : null)}</td></tr>`).join('')}</tbody></table></div>` : ''}`;
  }
})();
