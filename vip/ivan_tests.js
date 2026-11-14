/* CRYPTO NOVΛ VIP — «Иван · тест-стратегии» (13.11.2026): owner only (GET /v2/owner/ivan_tests). Renderer: ../ivan_tests.js */
(function () {
  'use strict';
  const V = window.VIP;
  const call = (p, body) => window.CNAuth.fetchOwner('/v2/owner/ivan_tests' + p, { method: 'POST', body });
  const api = { add: (b) => call('/add', b), check: (id) => call('/check', { id }), remove: (id) => call('/remove', { id }) };
  V.views.ivan_tests = {
    async html() {
      if (!V.auth.owner()) return `<div class="card">${V.nodata('раздел доступен только владельцу')}</div>`;
      let D = null, err = '';
      try { D = await window.CNAuth.fetchOwner('/v2/owner/ivan_tests'); } catch (e) { err = 'Не загрузилось: ' + ((e && e.message) || e); }
      return `<div class="card" data-it="root">${window.CNIvanTests ? window.CNIvanTests.html(D, { err }) : V.nodata('ivan_tests.js не загрузился')}</div>`;
    },
    mount(el) {
      const root = el.querySelector('[data-it="root"]') || el;
      const re = (D) => { root.innerHTML = window.CNIvanTests.html(D); window.CNIvanTests.mount(root, api, re); };
      if (window.CNIvanTests) window.CNIvanTests.mount(root, api, re);
    },
  };
})();
