/* CRYPTO NOVΛ VIP — «Альты от китов (тест)» (13.11.2026): paper test desk, owner only (GET /v2/owner/smart_alts). Renderer: ../smart_alts.js */
(function () {
  'use strict';
  const V = window.VIP;
  V.views.smart_alts = {
    async html() {
      if (!V.auth.owner()) return `<div class="card">${V.nodata('раздел доступен только владельцу')}</div>`;
      let D = null, err = '';
      try { D = await V.ownerData('smart_alts'); } catch (e) { err = 'Стол ещё не запускался на сервере или не загрузился: ' + ((e && e.message) || e); }
      return `<div class="card">${window.CNSmartAlts ? window.CNSmartAlts.html(D, { err }) : V.nodata('smart_alts.js не загрузился')}</div>`;
    },
  };
})();
