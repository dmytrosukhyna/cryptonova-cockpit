# CRYPTO NOVA API v2 (server)

Small backend for the 2.0 app, next to the engine: `cnq/api/server.py` (stdlib HTTP + PyJWT), port 8090 on localhost,
published by nginx as `https://app.crypto-capital.app/v2/…`. The old Java backend is untouched.

Install once (root): `bash /opt/cnq/repo/deploy/api/install_api.sh`. Code updates arrive with the engine's git pull; the
API restarts itself within a minute when its code changes.

Settings in `/etc/cnq.env`:
- `CNQ_LLM_KEY` (+ optional `CNQ_LLM_PROVIDER`, `CNQ_LLM_MODEL`) — the AI agent NOVA (free providers: groq / gemini / openrouter).
- `CNQ_BINGX_AGENT_KEY`, `CNQ_BINGX_AGENT_SECRET` — BingX partner cabinet, read-only (VIP check). Without them VIP check answers 503.
- `CNQ_FIREBASE_PROJECT` — default `crypto-nova`.
- `CNQ_FCM_SA=/etc/cnq-fcm.json` — the Firebase service-account JSON (Firebase console → Project settings → Service accounts →
  Generate new private key; `chown cnq:cnq`, `chmod 600`). Used for pushes (engine) and to delete the Firebase login when a
  user deletes the account (`DELETE /v2/me`). Without it pushes are only logged and the app deletes the login on the phone.

Endpoints: see the docstring of `cnq/api/server.py`. Data: `/opt/cnq/run/data/api.sqlite` (not in git).

Legal pages for the stores (published with the site): https://cryptonova.vip/privacy.html · terms.html · delete.html
