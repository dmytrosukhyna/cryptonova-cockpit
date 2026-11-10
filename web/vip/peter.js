/* CRYPTO NOVΛ VIP — «Питер» (11.11.2026): MT5 gold trader under observation, owner only (GET /v2/owner/peter). Renderer: ../peter.js */
(function () {
  'use strict';
  const V = window.VIP;
  let LAST = null, ERR = '';
  V.views.peter = {
    async html() {
      if (!V.auth.owner()) return `<div class="card">${V.nodata('раздел доступен только владельцу')}</div>`;
      try { LAST = await V.ownerData('peter'); ERR = ''; } catch (e) { ERR = 'Не загрузилось: ' + (e && e.message || e); }
      return `<div class="card">${window.CNPeter ? window.CNPeter.html(LAST, { err: ERR }) : V.nodata('peter.js не загрузился')}</div>`;
    },
    mount(el) {
      if (window.CNPeter) window.CNPeter.mount(el, { fetchToken: () => window.CNAuth.fetchOwner('/v2/owner/peter/token'),
        rotateToken: () => window.CNAuth.fetchOwner('/v2/owner/peter/token/rotate', { method: 'POST' }) });
    },
  };
})();
