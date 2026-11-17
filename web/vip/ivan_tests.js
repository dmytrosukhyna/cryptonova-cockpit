/* CRYPTO NOVΛ VIP — «Иван · тест-стратегии» (13.11.2026): owner only (GET /v2/owner/ivan_tests). Renderer: ../ivan_tests.js */
(function () {
  'use strict';
  const V = window.VIP;
  const call = (p, body) => window.CNAuth.fetchOwner('/v2/owner/ivan_tests' + p, { method: 'POST', body });
  const api = { add: (b) => call('/add', b), check: (id) => call('/check', { id }), remove: (id) => call('/remove', { id }),
    copyReal: (id, on) => call('/copy_real', { id, on }) };          // 17.11: the owner's own switch, default off
  V.views.ivan_tests = {
    async html() {
      if (!V.auth.owner()) return `<div class="card">${V.nodata('раздел доступен только владельцу')}</div>`;
      let D = null, err = '';
      try { D = await window.CNAuth.fetchOwner('/v2/owner/ivan_tests'); } catch (e) { err = 'Не загрузилось: ' + ((e && e.message) || e); }
      V.views.ivan_tests._doc = D;
      return `<div class="card" data-it="root">${window.CNIvanTests ? window.CNIvanTests.html(D, { err }) : V.nodata('ivan_tests.js не загрузился')}</div>`;
    },
    mount(el) {
      const root = el.querySelector('[data-it="root"]') || el;
      let last = V.views.ivan_tests._doc || null;
      const re = (D, msg) => { if (D && D.accounts) last = D; if (!last) return;
        root.innerHTML = window.CNIvanTests.html(last, { msg }); window.CNIvanTests.mount(root, api, re); };
      if (window.CNIvanTests) window.CNIvanTests.mount(root, api, re);
    },
  };
})();
