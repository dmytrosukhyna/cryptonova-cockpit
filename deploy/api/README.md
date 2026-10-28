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

- `CNQ_ADMIN_TOKEN` — the owner's admin panel https://cryptonova.vip/admin.html (users, CRM notes and tags, Telegram bot leads,
  segment pushes, connector status, growth plan; API `/v2/admin/*`, `cnq/api/console.py`). A random string of at least 24
  characters, created on the server and never sent anywhere else:
  `grep -q '^CNQ_ADMIN_TOKEN=' /etc/cnq.env || echo "CNQ_ADMIN_TOKEN=$(openssl rand -hex 32)" >> /etc/cnq.env && systemctl restart cnq-api`.
  Without it every admin call answers 403. The system panel (app.html) shows «Админ-панель» only in owner mode.
  21.10: the same token also switches on NOVA in the closed system's chat (`POST /v2/admin/nova`, the server's model key) —
  sign in once in «Управление» with «Запомнить на этом устройстве»; Settings → «Проверить ИИ» shows the time or the exact error.

- Build 45 «Разбор портфеля» (`/v2/portfolio/*`, `cnq/api/pf_parse.py`, `cnq/api/pf_review.py`): CSV / XLSX / PDF work with
  no settings. Two one-time steps on the server (root):
  1. nginx must let a file of up to 10 MB through (the old snippet allows 32 KB):
     `install -m 644 /opt/cnq/repo/deploy/api/cnq-v2.nginx.conf /etc/nginx/snippets/cnq-v2.conf && nginx -t && systemctl reload nginx`
  2. Screenshots need a model that sees images. NOVA's model (openai/gpt-oss-120b) is text only, so they are off until
     `CNQ_VISION_LLM_KEY` (+ optional `CNQ_VISION_LLM_PROVIDER`, default `gemini`, and `CNQ_VISION_LLM_MODEL`) is in
     `/etc/cnq.env` and `systemctl restart cnq-api`; `GET /v2/portfolio/caps` then says `"image": true` and the app offers them.
  Optional: `/opt/cnq/venv/bin/pip install pypdf` reads unusual PDF fonts better (without it a small built-in reader is used).
  Nothing is stored: the file and the coins live only in the request's memory; `/v2/health` "portfolio" shows counts only.

- Build 46 NOVA AI's voice (`POST /v2/tts`, `cnq/api/tts.py`): the app's report and NOVA AI's answers in a male neural voice
  (edge-tts; the same male voices as the spoken greeting, never a female one). One-time step on the server (root):
  `/opt/cnq/venv/bin/pip install edge-tts && systemctl restart cnq-api`. Without it the route answers 503 `tts_unavailable` and the
  app falls back to the phone's male voice, then to text. Phrases are cached in `/opt/cnq/run/data/tts/` (≤ 500 files);
  `/v2/health` "tts_last" shows the last attempt (ok / the error, never the text).

Endpoints: see the docstring of `cnq/api/server.py`. Data: `/opt/cnq/run/data/api.sqlite` (not in git).

Legal pages for the stores (published with the site): https://cryptonova.vip/privacy.html · terms.html · delete.html
