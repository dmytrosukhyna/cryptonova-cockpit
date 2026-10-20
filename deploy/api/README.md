# CRYPTO NOVA API v2 (server)

Small backend for the 2.0 app, next to the engine: `cnq/api/server.py` (stdlib HTTP + PyJWT), port 8090 on localhost,
published by nginx as `https://app.crypto-capital.app/v2/…`. The old Java backend is untouched.

Install once (root): `bash /opt/cnq/repo/deploy/api/install_api.sh`. Code updates arrive with the engine's git pull; the
API restarts itself within a minute when its code changes.

Settings in `/etc/cnq.env`:
- `CNQ_LLM_KEY` (+ optional `CNQ_LLM_PROVIDER`, `CNQ_LLM_MODEL`) — the AI agent NOVA (free providers: groq / gemini / openrouter).
- Optional `CNQ_NOVA_LLM_KEY` (+ `CNQ_NOVA_LLM_PROVIDER`, `CNQ_NOVA_LLM_MODEL`) — a separate key only for NOVA, e.g. a paid plan:
  one NOVA question is ≈ 2 000–2 500 tokens, and the free groq plan limits tokens per minute for the whole key.
- NOVA's knowledge: `data/nova_facts.json` (checked facts with dates and sources), `data/nova_faq.json` (Q&A), `web/nova.json`
  (greeting, starter questions, topics → `GET /v2/feed/nova`). Edit and push — no restart needed. Test the real model with the
  manual workflow `nova-probe` (notices show the time, tokens and each answer; `el: …` asks in Greek).
- Languages (20.10): the app sends `?lang=en|ru|uk|el|es` on every feed and `"lang"` to `POST /v2/agent` (missing / unknown →
  ru). Feeds: `web/<name>.<lang>.json` when it exists, else `<name>.json` (Russian) — `app_home.<lang>.json` is written by the
  engine every minute with `app_home.json` (texts: `cnq/agents/i18n.py`), `nova.<lang>.json` is in git, `academy.<lang>.json`
  can be added any time. NOVA: `data/nova_faq.<lang>.json` (same ids as nova_faq.json) and `data/nova_facts.<lang>.json`
  (fact texts by id). Change an answer or a fact in every language.
- `CNQ_BINGX_AGENT_KEY`, `CNQ_BINGX_AGENT_SECRET` — BingX partner cabinet, read-only (VIP check). Without them VIP check answers 503.
- `CNQ_FIREBASE_PROJECT` — default `crypto-nova`.
- `CNQ_FCM_SA=/etc/cnq-fcm.json` — the Firebase service-account JSON (Firebase console → Project settings → Service accounts →
  Generate new private key; `chown cnq:cnq`, `chmod 600`). Used for pushes (engine) and to delete the Firebase login when a
  user deletes the account (`DELETE /v2/me`). Without it pushes are only logged and the app deletes the login on the phone.

Endpoints: see the docstring of `cnq/api/server.py`. Data: `/opt/cnq/run/data/api.sqlite` (not in git).

Legal pages for the stores (published with the site): https://cryptonova.vip/privacy.html · terms.html · delete.html
