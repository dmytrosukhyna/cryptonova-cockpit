/* CRYPTO NOVΛ VIP — «Разворот (тест)» (11.11.2026): paper test desk, owner only (GET /v2/owner/reversal). Renderer: ../reversal.js */
(function () {
  'use strict';
  const V = window.VIP;
  V.views.reversal = {
    async html() {
      if (!V.auth.owner()) return `<div class="card">${V.nodata('раздел доступен только владельцу')}</div>`;
      let D = null, err = '';
      try { D = await V.ownerData('reversal'); } catch (e) { err = 'Стол ещё не запускался на сервере или не загрузился: ' + ((e && e.message) || e); }
      return `<div class="card">${window.CNReversal ? window.CNReversal.html(D, { err }) : V.nodata('reversal.js не загрузился')}</div>`;
    },
  };
})();
