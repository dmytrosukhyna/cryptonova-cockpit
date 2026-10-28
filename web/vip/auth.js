/* CRYPTONOVA VIP — the owner's sign-in in the browser: window.CNAuth (28.10.2026, owner spec §3, §12, §17).
 *
 * Plain JS, no build step. Include once:   <script src="vip/auth.js" defer></script>
 * Optional, BEFORE it:  <script>window.CN_AUTH_CONFIG = {api: 'https://app.crypto-capital.app', firebase: {...}}</script>
 *
 *   CNAuth.state                       {signedIn, email, isOwner, method, ready}   (method: email | apple | google | passkey)
 *   CNAuth.onChange(cb) → unsubscribe  cb(state) now and on every change (also from another tab)
 *   CNAuth.signInEmail(email, pass, {remember})   Firebase email + password → owner session on the server
 *   CNAuth.signInApple({remember}) · CNAuth.signInGoogle({remember})   Firebase popup (redirect when a popup is blocked)
 *   CNAuth.resetPassword(email)        Firebase's own reset email
 *   CNAuth.signOut({all})              revokes the session on the server (all: every device)
 *   CNAuth.passkeySupported()          WebAuthn usable here (Face ID / Touch ID / Windows Hello / Android)
 *   CNAuth.passkeyRegister({name})     add a passkey (needs a sign-in within the last 15 minutes)
 *   CNAuth.passkeyLogin({remember})    sign in with a passkey
 *   CNAuth.fetchOwner(path, {method, body})  → JSON; adds the session, refreshes it, throws CNAuth.Error on failure
 *   CNAuth.mountLogin(el, {onSignedIn})       a ready, accessible Russian sign-in sheet
 *   CNAuth.ready (Promise), CNAuth.configure({api, firebase}), CNAuth.Error, CNAuth.messages
 *
 * Security model (server: cnq/api/owner_auth.py): Firebase proves who the owner is; the server checks the token, the owner's
 * verified email and a fresh sign-in, then gives an access token (15 min, memory only) and a refresh token (rotated on every use;
 * localStorage when «Запомнить», else sessionStorage). The refresh is signed by a non-extractable device key (WebCrypto,
 * IndexedDB), so a copied refresh token is useless on another machine. Firebase's own login is dropped right after the exchange
 * (session persistence + signOut): the only long-lived credential on the device is the bound refresh token. No passwords, keys
 * or biometrics are stored by this file; Face ID / Touch ID run inside the operating system (WebAuthn passkeys).
 */
(function () {
  'use strict';
  if (window.CNAuth) return;

  var FB_VERSION = '10.14.1';                                   // pinned Firebase JS SDK (gstatic, ES modules)
  var FB_BASE = 'https://www.gstatic.com/firebasejs/' + FB_VERSION + '/';
  var DEFAULT_API = 'https://app.crypto-capital.app';
  var STORE_KEY = 'cnv_auth', PK_FLAG = 'cnv_pk', REDIRECT_KEY = 'cnv_redirect';
  var IDB_NAME = 'cnv-auth', IDB_STORE = 'k';

  var MSG = {
    invalid: 'Неверная почта или пароль',
    throttled: function (n) { return 'Слишком много попыток — подождите ' + n + ' мин'; },
    offline: 'Нет связи',
    server: 'Сервер не ответил — попробуйте ещё раз',
    cancelled: 'Вход отменён',
    not_owner: 'Этот аккаунт не владелец панели',
    email_not_verified: 'Почта ещё не подтверждена — мы отправили письмо со ссылкой. Откройте его и войдите снова',
    stale_sign_in: 'Вход устарел — войдите ещё раз',
    bad_token: 'Вход не подтвердился — войдите ещё раз',
    not_configured: 'Вход по почте, Apple и Google ещё не настроен (нет веб-конфигурации Firebase)',
    owner_not_configured: 'На сервере не указан владелец панели',
    provider_off: 'Этот способ входа не включён в Firebase',
    domain: 'Этот адрес сайта не разрешён в Firebase (Authorized domains)',
    disabled: 'Аккаунт отключён',
    other_provider: 'Эта почта уже привязана к другому способу входа — войдите им',
    session_expired: 'Сессия закончилась — войдите снова',
    email_required: 'Введите почту',
    pass_required: 'Введите пароль',
    reset_sent: 'Если этот адрес зарегистрирован, письмо для смены пароля уже отправлено. Проверьте почту и папку «Спам»',
    passkey_unsupported: 'На этом устройстве или в этом браузере вход по биометрии недоступен',
    passkey_cancelled: 'Вход по биометрии отменён',
    passkey_unknown: 'Ключ входа не найден на сервере — войдите по почте и включите вход по биометрии снова',
    passkey_invalid: 'Устройство не подтвердило вход — попробуйте ещё раз',
    passkey_exists: 'Вход по биометрии на этом устройстве уже включён',
    reauth: 'Чтобы включить вход по биометрии, сначала войдите заново — по почте, через Apple или Google',
    too_many_passkeys: 'Слишком много ключей входа — удалите старые в настройках',
    confirm_required: 'Нужно подтверждение действия',
    unauthorized: 'Нужно войти'
  };

  function CNAuthError(code, extra) {
    var m = MSG[code];
    var e = new Error(typeof m === 'function' ? m((extra && extra.wait) || 1) : (m || MSG.server));
    e.name = 'CNAuthError';
    e.code = code;
    e.cnauth = true;
    if (extra) for (var k in extra) if (Object.prototype.hasOwnProperty.call(extra, k)) e[k] = extra[k];
    return e;
  }

  // ---------------------------------------------------------------------------------------------------------- settings
  var boot = window.CN_AUTH_CONFIG || {};
  var cfg = { api: boot.api || null, firebase: boot.firebase || null };
  try {
    var cs = document.currentScript;
    if (cs && cs.dataset && cs.dataset.api && !cfg.api) cfg.api = cs.dataset.api;
  } catch (e) { /* no currentScript */ }

  function apiBase() {
    if (cfg.api) return String(cfg.api).replace(/\/+$/, '');
    try {
      var q = new URLSearchParams(location.search).get('api');
      if (q && /^(localhost|127\.0\.0\.1)$/.test(location.hostname)) return q.replace(/\/+$/, '');
    } catch (e) { /* ignore */ }
    return DEFAULT_API;
  }

  // ---------------------------------------------------------------------------------------------------------- state
  var state = { signedIn: false, email: null, isOwner: false, method: null, ready: false };
  var listeners = [];
  var access = null, accessExp = 0, serverCfg = null, serverCfgP = null, fb = null, fbP = null, refreshP = null;

  function publicState() {
    return { signedIn: state.signedIn, email: state.email, isOwner: state.isOwner, method: state.method, ready: state.ready };
  }
  function emit() {
    var s = publicState();
    listeners.slice().forEach(function (cb) { try { cb(s); } catch (e) { setTimeout(function () { throw e; }); } });
  }
  function setState(p) { for (var k in p) state[k] = p[k]; emit(); }
  function onChange(cb) {
    if (typeof cb !== 'function') return function () {};
    listeners.push(cb);
    try { cb(publicState()); } catch (e) { setTimeout(function () { throw e; }); }
    return function () { listeners = listeners.filter(function (x) { return x !== cb; }); };
  }

  // ---------------------------------------------------------------------------------------------------------- storage
  function stores() { var s = []; try { s.push(localStorage); } catch (e) {} try { s.push(sessionStorage); } catch (e) {} return s; }
  function loadStored() {
    var s = stores();
    for (var i = 0; i < s.length; i++) {
      try { var v = JSON.parse(s[i].getItem(STORE_KEY) || 'null'); if (v && v.rt) return v; } catch (e) { /* ignore */ }
    }
    return null;
  }
  function clearStored() { stores().forEach(function (s) { try { s.removeItem(STORE_KEY); } catch (e) {} }); }
  function saveStored(v) {
    clearStored();
    try { (v.remember ? localStorage : sessionStorage).setItem(STORE_KEY, JSON.stringify(v)); } catch (e) { /* private mode */ }
  }
  function flag(k, v) {
    try { if (v === undefined) return localStorage.getItem(k); if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); }
    catch (e) { return null; }
    return v;
  }

  // ---------------------------------------------------------------------------------------------------------- bytes
  function b64u(buf) {
    var b = new Uint8Array(buf), s = '';
    for (var i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
    return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }
  function unb64u(s) {
    s = String(s || '').replace(/-/g, '+').replace(/_/g, '/');
    while (s.length % 4) s += '=';
    var bin = atob(s), out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out.buffer;
  }

  // ---------------------------------------------------------------------------------------------------------- device key
  function idb() {
    return new Promise(function (res, rej) {
      var r = indexedDB.open(IDB_NAME, 1);
      r.onupgradeneeded = function () { r.result.createObjectStore(IDB_STORE); };
      r.onsuccess = function () { res(r.result); };
      r.onerror = function () { rej(r.error); };
    });
  }
  function idbDo(mode, fn) {
    return idb().then(function (db) {
      return new Promise(function (res, rej) {
        var tx = db.transaction(IDB_STORE, mode), req = fn(tx.objectStore(IDB_STORE));
        tx.oncomplete = function () { res(req && req.result); db.close(); };
        tx.onerror = tx.onabort = function () { rej(tx.error); db.close(); };
      });
    });
  }
  function deviceKey(create) {
    if (!(window.crypto && crypto.subtle && window.indexedDB)) return Promise.resolve(null);
    return idbDo('readonly', function (s) { return s.get('device'); }).then(function (kp) {
      if (kp && kp.privateKey) return kp;
      if (!create) return null;
      return crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign', 'verify']).then(function (k) {
        return idbDo('readwrite', function (s) { return s.put(k, 'device'); }).then(function () { return k; });
      });
    }).catch(function () { return null; });
  }
  function devicePublic(kp) {
    if (!kp) return Promise.resolve(null);
    return crypto.subtle.exportKey('jwk', kp.publicKey).then(function (j) { return { kty: j.kty, crv: j.crv, x: j.x, y: j.y }; })
      .catch(function () { return null; });
  }
  function proof(kp, rt) {
    var t = Date.now();
    return crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, kp.privateKey, new TextEncoder().encode('cnq-refresh:' + t + ':' + rt))
      .then(function (sig) { return { t: t, sig: b64u(sig) }; });
  }

  // ---------------------------------------------------------------------------------------------------------- HTTP
  function call(path, o) {
    o = o || {};
    var h = { 'Accept': 'application/json' };
    if (o.body !== undefined) h['Content-Type'] = 'application/json';
    if (o.auth && access) h['Authorization'] = 'Bearer ' + access;
    var ctl = window.AbortController ? new AbortController() : null;
    var tm = setTimeout(function () { if (ctl) ctl.abort(); }, o.timeout || 20000);
    return fetch(apiBase() + path, {
      method: o.method || 'GET', headers: h, body: o.body === undefined ? undefined : JSON.stringify(o.body),
      cache: 'no-store', credentials: 'omit', mode: 'cors', signal: ctl ? ctl.signal : undefined
    }).then(function (r) {
      clearTimeout(tm);
      return r.json().catch(function () { return {}; }).then(function (d) { return { status: r.status, data: d || {} }; });
    }, function () { clearTimeout(tm); throw CNAuthError('offline'); });
  }

  function fromServer(status, d) {
    var e = (d && d.error) || '';
    if (status === 429) return CNAuthError('throttled', { wait: Math.max(1, Math.ceil(((d && d.retry_after_sec) || 60) / 60)), status: 429 });
    var map = {
      not_owner: 'not_owner', email_not_verified: 'email_not_verified', stale_sign_in: 'stale_sign_in', bad_token: 'bad_token',
      owner_not_configured: 'owner_not_configured', session_revoked: 'session_expired', expired: 'session_expired',
      invalid: 'session_expired', token_expired: 'session_expired', unauthorized: 'unauthorized', reauth_required: 'reauth',
      unknown_passkey: 'passkey_unknown', passkey_invalid: 'passkey_invalid', bad_challenge: 'passkey_invalid',
      bad_credential: 'passkey_invalid', already_registered: 'passkey_exists', too_many_passkeys: 'too_many_passkeys',
      confirm_required: 'confirm_required'
    };
    var x = CNAuthError(map[e] || 'server', { status: status, data: d });
    if (!map[e] && e) x.serverError = e;
    return x;
  }

  function serverConfig() {
    if (serverCfg) return Promise.resolve(serverCfg);
    if (!serverCfgP) {
      serverCfgP = call('/v2/auth/config').then(function (r) {
        if (r.status !== 200) throw fromServer(r.status, r.data);
        serverCfg = r.data;
        return serverCfg;
      }).catch(function (e) { serverCfgP = null; throw e; });
    }
    return serverCfgP;
  }

  // ---------------------------------------------------------------------------------------------------------- session
  function applySession(d, remember, bound) {
    var s = d.session || {};
    access = d.access_token;
    accessExp = Date.now() + ((d.expires_in || 900) - 30) * 1000;
    saveStored({ rt: d.refresh_token, email: s.email || null, method: s.method || null, remember: !!remember, bound: !!bound, sid: s.id || null });
    setState({ signedIn: true, email: s.email || null, isOwner: true, method: s.method || null });
  }
  function signOutLocal() {
    access = null; accessExp = 0;
    clearStored();
    if (state.signedIn || state.email) setState({ signedIn: false, email: null, isOwner: false, method: null });
  }

  function refreshOnce() {
    var st = loadStored();
    if (!st) return Promise.reject(CNAuthError('session_expired'));
    var body = { refresh_token: st.rt };
    var p = st.bound ? deviceKey(false).then(function (kp) { return kp ? proof(kp, st.rt) : null; }) : Promise.resolve(null);
    return p.then(function (pr) {
      if (pr) body.proof = pr;
      return call('/v2/auth/refresh', { method: 'POST', body: body });
    }).then(function (r) {
      if (r.status === 200) { applySession(r.data, st.remember, st.bound); return true; }
      if (r.status === 409) {                    // another tab rotated it a moment ago: use the token it saved
        return new Promise(function (res) { setTimeout(res, 400); }).then(function () {
          var st2 = loadStored();
          if (st2 && st2.rt !== st.rt) return refreshOnce();
          throw CNAuthError('session_expired');
        });
      }
      if (r.status === 401 || r.status === 403 || r.status === 400) { signOutLocal(); }
      throw fromServer(r.status, r.data);
    });
  }
  function refresh() {
    if (refreshP) return refreshP;
    var run = function () { return refreshOnce(); };
    refreshP = (navigator.locks && navigator.locks.request ? navigator.locks.request('cnv-auth-refresh', run) : run());
    return refreshP.then(function (v) { refreshP = null; return v; }, function (e) { refreshP = null; throw e; });
  }
  function ensureAccess() {
    if (access && Date.now() < accessExp) return Promise.resolve(access);
    return refresh().then(function () { return access; });
  }

  function fetchOwner(path, opts) {
    opts = opts || {};
    path = String(path || '');
    if (path.charAt(0) !== '/') return Promise.reject(new TypeError('fetchOwner: path must start with /'));
    if (!access && !loadStored()) return Promise.reject(CNAuthError('unauthorized', { status: 401 }));
    var go = function () { return call(path, { method: opts.method || (opts.body !== undefined ? 'POST' : 'GET'), body: opts.body, auth: true }); };
    return ensureAccess().then(go).then(function (r) {
      if (r.status !== 401) return r;
      return refresh().then(go);                // the access token ran out (or was revoked) between the check and the call
    }).then(function (r) {
      if (r.status === 401) { signOutLocal(); throw CNAuthError('session_expired', { status: 401 }); }
      if (r.status >= 400) throw fromServer(r.status, r.data);
      return r.data;
    });
  }

  // ---------------------------------------------------------------------------------------------------------- Firebase
  function firebaseConfig() {
    if (cfg.firebase && cfg.firebase.apiKey) return Promise.resolve(cfg.firebase);
    if (window.CN_FIREBASE_CONFIG && window.CN_FIREBASE_CONFIG.apiKey) return Promise.resolve(window.CN_FIREBASE_CONFIG);
    return serverConfig().then(function (c) { if (!c.firebase || !c.firebase.apiKey) throw CNAuthError('not_configured'); return c.firebase; });
  }
  function firebase() {
    if (fb) return Promise.resolve(fb);
    if (!fbP) {
      fbP = firebaseConfig().then(function (conf) {
        return Promise.all([import(FB_BASE + 'firebase-app.js'), import(FB_BASE + 'firebase-auth.js')]).catch(function () {
          throw CNAuthError('offline');
        }).then(function (m) {
          var appMod = m[0], A = m[1];
          var app = appMod.initializeApp(conf, 'cnv-auth');
          var auth = A.initializeAuth(app, { persistence: [A.browserSessionPersistence], popupRedirectResolver: A.browserPopupRedirectResolver });
          try { auth.languageCode = 'ru'; } catch (e) { /* ignore */ }
          fb = { A: A, auth: auth };
          return fb;
        });
      }).catch(function (e) { fbP = null; throw e; });
    }
    return fbP;
  }
  function fbErr(e) {
    if (e && e.cnauth) return e;
    var c = (e && e.code) || '';
    if (/invalid-credential|wrong-password|user-not-found|invalid-email|invalid-login-credentials|missing-password/.test(c)) return CNAuthError('invalid');
    if (/too-many-requests/.test(c)) return CNAuthError('throttled', { wait: 15 });
    if (/network-request-failed|timeout/.test(c) || navigator.onLine === false) return CNAuthError('offline');
    if (/popup-closed-by-user|cancelled-popup-request|user-cancelled|redirect-cancelled/.test(c)) return CNAuthError('cancelled');
    if (/operation-not-allowed|admin-restricted-operation/.test(c)) return CNAuthError('provider_off');
    if (/unauthorized-domain/.test(c)) return CNAuthError('domain');
    if (/user-disabled/.test(c)) return CNAuthError('disabled');
    if (/account-exists-with-different-credential|credential-already-in-use/.test(c)) return CNAuthError('other_provider');
    if (/api-key-not-valid|invalid-api-key|configuration-not-found/.test(c)) return CNAuthError('not_configured');
    return CNAuthError('server', { detail: c || String((e && e.message) || e) });
  }

  function exchange(user, remember) {
    var keep = remember !== false;
    return Promise.all([user.getIdToken(), deviceKey(true).then(devicePublic)]).catch(function (e) { throw fbErr(e); }).then(function (x) {
      return call('/v2/auth/session', { method: 'POST', body: { id_token: x[0], remember: keep, device_key: x[1] } }).then(function (r) {
        if (r.status === 200) { applySession(r.data, keep, !!x[1]); return publicState(); }
        if (r.status === 403 && r.data.error === 'email_not_verified' && fb) {
          return fb.A.sendEmailVerification(user).catch(function () {}).then(function () { throw fromServer(r.status, r.data); });
        }
        throw fromServer(r.status, r.data);
      });
    });
  }
  function dropFirebase() { if (fb) fb.A.signOut(fb.auth).catch(function () {}); }   // Firebase was only the proof of identity

  function signInEmail(email, pass, opts) {
    opts = opts || {};
    email = String(email || '').trim();
    if (!email) return Promise.reject(CNAuthError('email_required'));
    if (!pass) return Promise.reject(CNAuthError('pass_required'));
    return firebase().then(function (f) {
      return f.A.signInWithEmailAndPassword(f.auth, email, pass).catch(function (e) { throw fbErr(e); });
    }).then(function (cred) { return exchange(cred.user, opts.remember); })
      .then(function (s) { dropFirebase(); return s; }, function (e) { dropFirebase(); throw e; });
  }

  function provider(A, kind) {
    var p;
    if (kind === 'apple') { p = new A.OAuthProvider('apple.com'); p.addScope('email'); p.addScope('name'); p.setCustomParameters({ locale: 'ru_RU' }); }
    else { p = new A.GoogleAuthProvider(); p.setCustomParameters({ prompt: 'select_account' }); }
    return p;
  }
  function signInWith(kind, opts) {
    opts = opts || {};
    // the popup must open inside the tap: when Firebase is already loaded (mountLogin preloads it) nothing is awaited before it
    var start = function (f) {
      return f.A.signInWithPopup(f.auth, provider(f.A, kind)).catch(function (e) {
        var c = (e && e.code) || '';
        if (/popup-blocked|operation-not-supported-in-this-environment/.test(c)) {
          try { sessionStorage.setItem(REDIRECT_KEY, JSON.stringify({ kind: kind, remember: opts.remember !== false })); } catch (x) {}
          return f.A.signInWithRedirect(f.auth, provider(f.A, kind)).then(function () { return new Promise(function () {}); });
        }
        throw fbErr(e);
      });
    };
    var p = fb ? start(fb) : firebase().then(start);
    return p.then(function (cred) { return exchange(cred.user, opts.remember); })
      .then(function (s) { dropFirebase(); return s; }, function (e) { dropFirebase(); throw e; });
  }
  function completeRedirect() {
    var pending = null;
    try { pending = JSON.parse(sessionStorage.getItem(REDIRECT_KEY) || 'null'); sessionStorage.removeItem(REDIRECT_KEY); } catch (e) {}
    if (!pending) return Promise.resolve(null);
    return firebase().then(function (f) { return f.A.getRedirectResult(f.auth); }).then(function (cred) {
      if (!cred || !cred.user) return null;
      return exchange(cred.user, pending.remember);
    }).then(function (s) { dropFirebase(); return s; }, function (e) { dropFirebase(); throw fbErr(e); });
  }

  function resetPassword(email) {
    email = String(email || '').trim();
    if (!email) return Promise.reject(CNAuthError('email_required'));
    var back = location.href.split('#')[0].split('?')[0];
    return firebase().then(function (f) {
      return f.A.sendPasswordResetEmail(f.auth, email, { url: back }).catch(function (e) {
        var c = (e && e.code) || '';
        if (/user-not-found/.test(c)) return null;                                     // the same answer for any address
        if (/unauthorized-continue-uri|invalid-continue-uri|missing-continue-uri/.test(c)) return f.A.sendPasswordResetEmail(f.auth, email);
        throw fbErr(e);
      });
    }).then(function () { return { ok: true, message: MSG.reset_sent }; });
  }

  function signOut(opts) {
    opts = opts || {};
    var st = loadStored();
    var body = opts.all ? { all: true, confirm: true } : {};
    var p = (access || st) ? (opts.all ? ensureAccess().catch(function () { return null; }) : Promise.resolve()).then(function () {
      var cur = loadStored();                                      // a refresh above may have rotated it
      if (cur) body.refresh_token = cur.rt;
      return call('/v2/auth/logout', { method: 'POST', body: body, auth: true });
    }).catch(function () { return null; }) : Promise.resolve(null);
    return p.then(function () { signOutLocal(); dropFirebase(); return publicState(); });
  }

  // ---------------------------------------------------------------------------------------------------------- passkeys
  function passkeySupported() {
    var ok = !!(window.PublicKeyCredential && navigator.credentials && navigator.credentials.create && navigator.credentials.get &&
                window.isSecureContext);
    return ok && !(serverCfg && serverCfg.passkey && serverCfg.passkey.available === false);
  }
  function passkeyPlatform() {
    if (!passkeySupported() || !PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable) return Promise.resolve(false);
    return PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable().then(function (x) { return !!x; }, function () { return false; });
  }
  function waErr(e) {
    var n = e && e.name;
    if (n === 'InvalidStateError') return CNAuthError('passkey_exists');
    if (n === 'NotAllowedError' || n === 'AbortError') return CNAuthError('passkey_cancelled');
    if (n === 'SecurityError' || n === 'NotSupportedError') return CNAuthError('passkey_unsupported');
    return CNAuthError('passkey_invalid', { detail: String((e && e.message) || e) });
  }
  function passkeyRegister(opts) {
    opts = opts || {};
    if (!passkeySupported()) return Promise.reject(CNAuthError('passkey_unsupported'));
    return fetchOwner('/v2/auth/passkey/register/options', { method: 'POST', body: {} }).then(function (o) {
      var p = o.publicKey, pk = {};
      for (var k in p) pk[k] = p[k];
      pk.challenge = unb64u(p.challenge);
      pk.user = { id: unb64u(p.user.id), name: p.user.name, displayName: p.user.displayName };
      pk.excludeCredentials = (p.excludeCredentials || []).map(function (c) { var x = { type: c.type, id: unb64u(c.id) }; if (c.transports) x.transports = c.transports; return x; });
      return navigator.credentials.create({ publicKey: pk }).catch(function (e) { throw waErr(e); });
    }).then(function (cred) {
      if (!cred) throw CNAuthError('passkey_cancelled');
      var r = cred.response;
      return fetchOwner('/v2/auth/passkey/register/verify', { method: 'POST', body: { name: opts.name || undefined, credential: {
        id: cred.id, rawId: b64u(cred.rawId), type: cred.type, authenticatorAttachment: cred.authenticatorAttachment || null,
        response: { clientDataJSON: b64u(r.clientDataJSON), attestationObject: b64u(r.attestationObject),
                    transports: r.getTransports ? r.getTransports() : [] },
        clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {} } } });
    }).then(function (d) { flag(PK_FLAG, '1'); return d.passkey; });
  }
  function passkeyLogin(opts) {
    opts = opts || {};
    var keep = opts.remember !== false;
    if (!passkeySupported()) return Promise.reject(CNAuthError('passkey_unsupported'));
    return call('/v2/auth/passkey/login/options', { method: 'POST', body: {} }).then(function (r) {
      if (r.status !== 200) throw fromServer(r.status, r.data);
      var p = r.data.publicKey, pk = {};
      for (var k in p) pk[k] = p[k];
      pk.challenge = unb64u(p.challenge);
      pk.allowCredentials = (p.allowCredentials || []).map(function (c) { return { type: c.type, id: unb64u(c.id) }; });
      return navigator.credentials.get({ publicKey: pk, mediation: 'optional' }).catch(function (e) { throw waErr(e); });
    }).then(function (a) {
      if (!a) throw CNAuthError('passkey_cancelled');
      var r = a.response;
      return deviceKey(true).then(devicePublic).then(function (jwk) {
        return call('/v2/auth/passkey/login/verify', { method: 'POST', body: { remember: keep, device_key: jwk, credential: {
          id: a.id, rawId: b64u(a.rawId), type: a.type,
          response: { clientDataJSON: b64u(r.clientDataJSON), authenticatorData: b64u(r.authenticatorData), signature: b64u(r.signature),
                      userHandle: r.userHandle ? b64u(r.userHandle) : null },
          clientExtensionResults: a.getClientExtensionResults ? a.getClientExtensionResults() : {} } } }).then(function (x) {
          if (x.status !== 200) throw fromServer(x.status, x.data);
          applySession(x.data, keep, !!jwk);
          flag(PK_FLAG, '1');
          return publicState();
        });
      });
    });
  }

  // ---------------------------------------------------------------------------------------------------------- start
  function init() {
    var p = Promise.resolve();
    var pending = false;
    try { pending = !!sessionStorage.getItem(REDIRECT_KEY); } catch (e) {}
    if (pending) p = p.then(completeRedirect).catch(function (e) { lastError = e; });
    p = p.then(function () {
      if (state.signedIn || !loadStored()) return null;
      return refresh().catch(function (e) { lastError = e; });
    }).then(function () { setState({ ready: true }); return publicState(); });
    return p;
  }
  var lastError = null;

  window.addEventListener('storage', function (e) {
    if (e.key !== STORE_KEY) return;
    if (!e.newValue && state.signedIn) { access = null; accessExp = 0; setState({ signedIn: false, email: null, isOwner: false, method: null }); }
    else if (e.newValue && !state.signedIn) { refresh().catch(function () {}); }
  });

  // ---------------------------------------------------------------------------------------------------------- the sign-in sheet
  var CSS = [
    '.cna{--cna-bg:var(--cn-surface,#0d1631);--cna-fg:var(--cn-text,#eef2fb);--cna-mute:var(--cn-muted,#9aa6c2);',
    '--cna-line:var(--cn-line,rgba(255,255,255,.14));--cna-field:var(--cn-field,rgba(255,255,255,.06));--cna-acc:var(--cn-accent,#4f7cff);',
    '--cna-bad:var(--cn-red,#ff7272);--cna-ok:var(--cn-green,#3fcf8e);color:var(--cna-fg);background:var(--cna-bg);',
    'border:1px solid var(--cna-line);border-radius:var(--cn-radius,16px);padding:24px 20px;max-width:400px;margin:0 auto;box-sizing:border-box;',
    'font:15px/1.45 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Roboto,sans-serif}',
    '.cna *{box-sizing:border-box}.cna [hidden]{display:none!important}',
    '.cna h2{font-size:20px;line-height:1.25;margin:0 0 4px;font-weight:650;letter-spacing:.01em}',
    '.cna .cna-sub{color:var(--cna-mute);margin:0 0 16px;font-size:14px}',
    '.cna .cna-msg{color:var(--cna-bad);font-size:14px;margin:0 0 12px;min-height:0}.cna .cna-msg:empty,.cna .cna-note:empty{display:none}',
    '.cna .cna-note{color:var(--cna-mute);font-size:14px;margin:0 0 12px}',
    '.cna-btn{display:flex;align-items:center;justify-content:center;gap:10px;width:100%;min-height:48px;border-radius:12px;',
    'border:1px solid var(--cna-line);background:transparent;color:inherit;font:inherit;font-weight:600;cursor:pointer;margin:0 0 10px;padding:0 16px}',
    '.cna-btn svg{width:20px;height:20px;flex:none}.cna-btn:disabled{opacity:.55;cursor:default}',
    '.cna-btn:focus-visible,.cna input:focus-visible,.cna-link:focus-visible,.cna-eye:focus-visible{outline:2px solid var(--cna-acc);outline-offset:2px}',
    '.cna-primary{background:var(--cna-acc);border-color:var(--cna-acc);color:#fff}',
    '.cna-pk{background:rgba(79,124,255,.14);border-color:rgba(79,124,255,.45)}',
    '.cna-apple{background:#fff;color:#000;border-color:#fff}',
    '.cna-or{display:flex;align-items:center;gap:10px;color:var(--cna-mute);font-size:13px;margin:6px 0 14px}',
    '.cna-or:before,.cna-or:after{content:"";flex:1;border-top:1px solid var(--cna-line)}',
    '.cna label.cna-l{display:block;font-size:13px;color:var(--cna-mute);margin:0 0 6px}',
    '.cna input[type=email],.cna input[type=password],.cna input[type=text]{width:100%;min-height:48px;border-radius:12px;',
    'border:1px solid var(--cna-line);background:var(--cna-field);color:inherit;font:inherit;font-size:16px;padding:0 14px;margin:0 0 14px}',
    '.cna-pw{position:relative}.cna-pw input{padding-right:52px!important}',
    '.cna-eye{position:absolute;right:4px;top:4px;width:40px;height:40px;border:0;background:transparent;color:var(--cna-mute);cursor:pointer;border-radius:10px}',
    '.cna-eye svg{width:20px;height:20px}',
    '.cna-check{display:flex;align-items:center;gap:10px;font-size:14px;margin:0 0 16px;cursor:pointer;min-height:32px}',
    '.cna-check input{width:20px;height:20px;accent-color:var(--cna-acc)}',
    '.cna-link{display:block;margin:4px auto 0;background:none;border:0;color:var(--cna-acc);font:inherit;font-size:14px;cursor:pointer;padding:10px;min-height:44px}',
    '.cna-who{margin:0 0 16px}.cna-who b{word-break:break-all}',
    '.cna[aria-busy=true] .cna-btn{pointer-events:none}'
  ].join('');
  var ICON = {
    face: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2M9 9v1.5M15 9v1.5M12 9v4h-1M9.5 15.5a3.5 3.5 0 0 0 5 0"/></svg>',
    apple: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M16.37 12.6c-.02-2.2 1.8-3.27 1.88-3.32-1.03-1.5-2.62-1.7-3.18-1.73-1.35-.14-2.64.8-3.33.8-.69 0-1.74-.78-2.87-.76-1.47.02-2.83.86-3.59 2.18-1.53 2.66-.39 6.59 1.1 8.75.73 1.05 1.6 2.24 2.73 2.2 1.1-.05 1.51-.71 2.84-.71 1.32 0 1.7.71 2.86.69 1.18-.02 1.93-1.07 2.65-2.13.84-1.22 1.18-2.4 1.2-2.46-.03-.01-2.27-.87-2.29-3.46zM14.2 6.13c.6-.73 1.01-1.75.9-2.76-.87.04-1.92.58-2.54 1.31-.56.64-1.05 1.67-.92 2.66.97.08 1.96-.49 2.56-1.21z"/></svg>',
    google: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="#4285F4" d="M21.6 12.23c0-.71-.06-1.4-.18-2.05H12v3.88h5.39a4.6 4.6 0 0 1-2 3.02v2.5h3.23c1.9-1.74 2.98-4.3 2.98-7.35z"/><path fill="#34A853" d="M12 22c2.7 0 4.96-.9 6.62-2.42l-3.23-2.5c-.9.6-2.04.95-3.39.95-2.6 0-4.81-1.76-5.6-4.12H3.07v2.58A10 10 0 0 0 12 22z"/><path fill="#FBBC05" d="M6.4 13.9a6 6 0 0 1 0-3.8V7.52H3.07a10 10 0 0 0 0 8.96L6.4 13.9z"/><path fill="#EA4335" d="M12 5.98c1.47 0 2.79.5 3.83 1.5l2.87-2.87A9.6 9.6 0 0 0 12 2 10 10 0 0 0 3.07 7.52L6.4 10.1C7.2 7.74 9.4 5.98 12 5.98z"/></svg>',
    eye: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>'
  };
  function bioName() {
    var u = navigator.userAgent || '';
    if (/iPhone|iPad|iPod/.test(u)) return 'Face ID';
    if (/Macintosh/.test(u)) return navigator.maxTouchPoints > 1 ? 'Face ID' : 'Touch ID';
    if (/Android/.test(u)) return 'отпечатку';
    if (/Windows/.test(u)) return 'Windows Hello';
    return 'ключу входа';
  }
  var METHOD = { email: 'по почте', apple: 'через Apple', google: 'через Google', passkey: 'по биометрии' };
  var mounts = 0;

  function mountLogin(el, opts) {
    opts = opts || {};
    if (typeof el === 'string') el = document.querySelector(el);
    if (!el) throw new TypeError('mountLogin: element not found');
    if (!document.getElementById('cna-style')) {
      var st = document.createElement('style'); st.id = 'cna-style'; st.textContent = CSS; document.head.appendChild(st);
    }
    var n = ++mounts, bio = bioName();
    var bioLabel = /^(Face ID|Touch ID|Windows Hello)$/.test(bio) ? 'Войти с ' + bio : 'Войти по ' + bio;
    el.innerHTML =
      '<section class="cna" aria-labelledby="cna-t' + n + '">' +
      '<div data-view="signin">' +
      '<h2 id="cna-t' + n + '">Вход в CRYPTONOVA VIP</h2><p class="cna-sub">Закрытая панель владельца.</p>' +
      '<p class="cna-msg" role="alert"></p><p class="cna-note" role="status" aria-live="polite"></p>' +
      '<button type="button" class="cna-btn cna-pk" data-act="passkey" hidden>' + ICON.face + '<span>' + bioLabel + '</span></button>' +
      '<button type="button" class="cna-btn cna-apple" data-act="apple">' + ICON.apple + '<span>Войти через Apple</span></button>' +
      '<button type="button" class="cna-btn" data-act="google">' + ICON.google + '<span>Войти через Google</span></button>' +
      '<div class="cna-or" role="separator"><span>или по почте</span></div>' +
      '<form class="cna-form" novalidate method="post" action="#">' +
      '<label class="cna-l" for="cna-e' + n + '">Почта</label>' +
      '<input id="cna-e' + n + '" name="email" type="email" inputmode="email" autocomplete="username" autocapitalize="none" spellcheck="false" required>' +
      '<label class="cna-l" for="cna-p' + n + '">Пароль</label>' +
      '<div class="cna-pw"><input id="cna-p' + n + '" name="password" type="password" autocomplete="current-password" required>' +
      '<button type="button" class="cna-eye" aria-label="Показать пароль" aria-pressed="false">' + ICON.eye + '</button></div>' +
      '<label class="cna-check"><input type="checkbox" name="remember" checked> Запомнить это устройство</label>' +
      '<button type="submit" class="cna-btn cna-primary" data-act="email">Войти</button>' +
      '<button type="button" class="cna-link" data-act="reset">Забыли пароль?</button>' +
      '</form></div>' +
      '<div data-view="offer" hidden><h2 tabindex="-1">Входить по ' + bio + '?</h2>' +
      '<p class="cna-sub">В следующий раз панель откроется одним касанием. Сам отпечаток или лицо проверяет устройство — на сервер они не попадают.</p>' +
      '<p class="cna-msg" role="alert"></p>' +
      '<button type="button" class="cna-btn cna-primary" data-act="enable">' + ICON.face + '<span>Включить</span></button>' +
      '<button type="button" class="cna-btn" data-act="later">Не сейчас</button></div>' +
      '<div data-view="done" hidden><h2 tabindex="-1">Вы вошли</h2><p class="cna-who"></p>' +
      '<p class="cna-msg" role="alert"></p>' +
      '<button type="button" class="cna-btn" data-act="signout">Выйти</button></div>' +
      '</section>';
    var root = el.querySelector('.cna');
    var q = function (s) { return root.querySelector(s); };
    var views = { signin: q('[data-view=signin]'), offer: q('[data-view=offer]'), done: q('[data-view=done]') };
    var form = q('form'), email = form.elements.email, pass = form.elements.password, remember = form.elements.remember;
    var offering = false;

    function show(v) {
      for (var k in views) views[k].hidden = k !== v;
      var h = views[v].querySelector('h2[tabindex]');
      if (h && v !== 'signin') try { h.focus(); } catch (e) {}
    }
    function msg(t, view) { (view || views.signin).querySelector('.cna-msg').textContent = t || ''; }
    function note(t) { q('.cna-note').textContent = t || ''; }
    function busy(on) {
      root.setAttribute('aria-busy', on ? 'true' : 'false');
      Array.prototype.forEach.call(root.querySelectorAll('button'), function (b) { b.disabled = !!on; });
    }
    function render(s) {
      if (offering) return;
      if (s.signedIn) {
        q('.cna-who').innerHTML = '';
        q('.cna-who').appendChild(document.createTextNode('Аккаунт '));
        var b = document.createElement('b'); b.textContent = s.email || ''; q('.cna-who').appendChild(b);
        q('.cna-who').appendChild(document.createTextNode(s.method && METHOD[s.method] ? ', вход ' + METHOD[s.method] + '.' : '.'));
        show('done');
      } else show('signin');
    }
    function fail(e, view) { busy(false); note(''); msg((e && e.message) || MSG.server, view); }
    function after(s) {
      busy(false); msg(''); note('');
      if (typeof opts.onSignedIn === 'function') try { opts.onSignedIn(s); } catch (e) { setTimeout(function () { throw e; }); }
      if (s.method !== 'passkey' && flag(PK_FLAG) !== '1' && flag(PK_FLAG) !== 'no') {
        return passkeyPlatform().then(function (ok) {
          if (!ok) return render(publicState());
          offering = true; msg('', views.offer); show('offer');
        });
      }
      render(publicState());
    }

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      msg(''); note('');
      if (!email.value.trim()) { msg(MSG.email_required); email.focus(); return; }
      if (!pass.value) { msg(MSG.pass_required); pass.focus(); return; }
      busy(true); note('Проверяем…');
      signInEmail(email.value, pass.value, { remember: remember.checked }).then(function (s) { pass.value = ''; after(s); }, function (er) {
        fail(er); pass.value = ''; try { pass.focus(); } catch (x) {}
      });
    });
    root.addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('button[data-act]') : null;
      if (!b || !root.contains(b)) return;
      var act = b.getAttribute('data-act');
      if (act === 'email') return;                                     // the form's submit handles it
      if (act === 'apple' || act === 'google') {
        msg(''); busy(true); note('Открываем окно входа…');
        (act === 'apple' ? signInApple : signInGoogle)({ remember: remember.checked }).then(after, fail);
      } else if (act === 'passkey') {
        msg(''); busy(true); note('Подтвердите вход на устройстве…');
        passkeyLogin({ remember: remember.checked }).then(after, fail);
      } else if (act === 'reset') {
        msg(''); note('');
        if (!email.value.trim()) { msg('Введите почту — пришлём ссылку для нового пароля'); email.focus(); return; }
        busy(true);
        resetPassword(email.value).then(function (r) { busy(false); note(r.message); }, fail);
      } else if (act === 'enable') {
        busy(true); msg('', views.offer);
        passkeyRegister().then(function () { busy(false); offering = false; render(publicState()); },
          function (er) { if (er.code === 'passkey_exists') { flag(PK_FLAG, '1'); busy(false); offering = false; render(publicState()); } else fail(er, views.offer); });
      } else if (act === 'later') {
        flag(PK_FLAG, 'no'); offering = false; render(publicState());
      } else if (act === 'signout') {
        busy(true);
        signOut().then(function () { busy(false); offering = false; render(publicState()); try { email.focus(); } catch (x) {} });
      }
    });
    q('.cna-eye').addEventListener('click', function () {
      var on = pass.type === 'password';
      pass.type = on ? 'text' : 'password';
      this.setAttribute('aria-pressed', on ? 'true' : 'false');
      this.setAttribute('aria-label', on ? 'Скрыть пароль' : 'Показать пароль');
    });

    // which buttons to show: passkeys when this device can; Apple / Google / email when the server lists them
    passkeyPlatform().then(function (ok) { q('[data-act=passkey]').hidden = !ok; });
    serverConfig().then(function (c) {
      var pr = c.providers || [];
      q('[data-act=apple]').hidden = pr.indexOf('apple.com') < 0;
      q('[data-act=google]').hidden = pr.indexOf('google.com') < 0;
      form.hidden = pr.indexOf('password') < 0;
      if (!state.signedIn) firebase().catch(function (e) { if (e.code === 'not_configured') note(MSG.not_configured); });   // preload for the popup
    }, function (e) { if (e && e.code === 'offline' && !state.signedIn) msg(MSG.offline); });
    var off = onChange(render);
    if (lastError && !state.signedIn && lastError.code !== 'session_expired') msg(lastError.message);
    return { unmount: function () { off(); el.innerHTML = ''; }, element: root };
  }

  function signInApple(o) { return signInWith('apple', o); }
  function signInGoogle(o) { return signInWith('google', o); }

  var api = {
    onChange: onChange, signInEmail: signInEmail, signInApple: signInApple, signInGoogle: signInGoogle, resetPassword: resetPassword,
    signOut: signOut, passkeySupported: passkeySupported, passkeyPlatform: passkeyPlatform, passkeyRegister: passkeyRegister,
    passkeyLogin: passkeyLogin, fetchOwner: fetchOwner, mountLogin: mountLogin, Error: CNAuthError, messages: MSG,
    configure: function (o) {
      o = o || {};
      if (o.api) { cfg.api = o.api; serverCfg = null; serverCfgP = null; }
      if (o.firebase) cfg.firebase = o.firebase;
      if (!state.signedIn) api.ready = init();
      return api.ready;
    },
    get lastError() { return lastError; }
  };
  Object.defineProperty(api, 'state', { get: publicState, enumerable: true });
  api.ready = init();
  window.CNAuth = api;
})();
