"""CRYPTO NOVA API v2 — the 2.0 app's own small backend, next to the engine on the server (stdlib HTTP + PyJWT).

nginx: app.crypto-capital.app/v2/ → http://127.0.0.1:8090/v2/  (deploy/api/README.md). The old Java backend stays as it is.

Public (no login):  GET /v2/health · GET /v2/home (= app_home.json, fresh every minute) · GET /v2/feed/<name>
With the Firebase ID token («Authorization: Bearer …»):
  POST /v2/me        profile + settings + piggy + lesson progress + AI-agent allowance (creates the user on first call)
  PUT  /v2/prefs     notification switches (trades, news, piggy, brief)
  PUT  /v2/piggy     the free demo piggy bank: amount, how often, goal, mode, start day
  PUT  /v2/progress  lessons done, quiz scores, XP
  POST /v2/push      the phone's FCM token
  POST /v2/agent     a question to NOVA (free: 5 a day, VIP: 100)
  POST /v2/vip/check BingX UID → invited by our link and deposited → VIP
  DELETE /v2/me      delete the account: every row on our server + the Firebase login (Apple 5.1.1(v))
Demo only until 2.1; nothing here places orders or holds money."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import bingx
from .auth import AuthError, FirebaseVerifier
from .store import Store

VERSION = "2.0.0"
FEEDS = {"app_home", "app_btc", "academy", "app_portfolio", "app_signals", "markets"}
LIMIT = {"free": 5, "vip": 100}
MAX_BODY = 16 * 1024
AGENT_SYSTEM = (
    "Ты — NOVA, AI-помощник приложения CRYPTO NOVA. Отвечай по-русски, очень простыми словами, как для человека без опыта: "
    "2–5 коротких предложений, цифры цифрами, пример из жизни, если помогает. Используй только числа из КОНТЕКСТА, ничего не "
    "придумывай. Никогда не обещай доход и не советуй купить или продать конкретную монету — объясняй, как это работает и какие "
    "риски. Если вопрос не про крипту, деньги или приложение — вежливо верни разговор к теме. В конце длинного ответа о рисках "
    "можно напомнить: это не финансовый совет.")


def _day() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


class Api:
    def __init__(self, store: Store, verifier: FirebaseVerifier, web_dir: str, llm=None, bingx_fetch=None, admin=None):
        self.store, self.verifier, self.web, self.llm, self.bingx_fetch = store, verifier, web_dir, llm, bingx_fetch
        self.admin = admin

    # ---------------------------------------------------------------- helpers
    def _feed(self, name: str) -> dict | None:
        try:
            with open(os.path.join(self.web, f"{name}.json"), encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return None

    def _uid(self, headers: dict) -> dict:
        h = headers.get("authorization") or ""
        if not h.lower().startswith("bearer "):
            raise AuthError("no token")
        return self.verifier.verify(h[7:].strip())

    def _allowance(self, uid: str, tier: str) -> dict:
        lim = LIMIT.get(tier or "free", 5)
        used = self.store.used_today(uid, _day())
        return {"limit": lim, "left": max(0, lim - used)}

    def _profile(self, uid: str) -> dict:
        u = self.store.user(uid)
        return {"user": {k: u.get(k) for k in ("uid", "email", "name", "tier", "vip_since", "bingx_uid", "created")},
                "prefs": self.store.prefs(uid), "piggy": self.store.piggy(uid), "progress": self.store.progress(uid),
                "agent": self._allowance(uid, u.get("tier") or "free")}

    # ---------------------------------------------------------------- the router
    def handle(self, method: str, path: str, headers: dict, body: bytes) -> tuple[int, dict]:
        path = path.split("?", 1)[0].rstrip("/")
        if method == "GET" and path == "/v2/health":
            home = self._feed("app_home") or {}
            age = None if not home.get("asof") else round(time.time() - home["asof"] / 1000)
            return 200, {"ok": True, "version": VERSION, "home_age_sec": age}
        if method == "GET" and path == "/v2/home":
            d = self._feed("app_home")
            return (200, d) if d else (503, {"error": "feed_unavailable"})
        m = re.fullmatch(r"/v2/feed/([a-z_]+)", path)
        if method == "GET" and m:
            if m.group(1) not in FEEDS:
                return 404, {"error": "unknown_feed"}
            d = self._feed(m.group(1))
            return (200, d) if d is not None else (503, {"error": "feed_unavailable"})
        # ------------------------------------------------------------ signed-in user
        try:
            who = self._uid(headers)
        except AuthError as e:
            return 401, {"error": "unauthorized", "detail": str(e)[:120]}
        uid = who["uid"]
        if method == "DELETE" and path == "/v2/me":
            rows = self.store.delete_user(uid)
            auth = self.admin.delete_user(uid) if self.admin is not None else "dry"
            print(f"account deleted: rows={rows} firebase={auth}", flush=True)
            return 200, {"deleted": True, "firebase": auth}
        try:
            data = json.loads(body.decode() or "{}") if body else {}
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            return 400, {"error": "bad_json"}
        if method == "POST" and path == "/v2/me":
            self.store.upsert_user(uid, who.get("email"), who.get("name"), str(data.get("lang") or "")[:5] or None)
            return 200, self._profile(uid)
        if not self.store.user(uid):
            self.store.upsert_user(uid, who.get("email"), who.get("name"))
        if method == "PUT" and path == "/v2/prefs":
            return 200, {"prefs": self.store.set_prefs(uid, data)}
        if method == "PUT" and path == "/v2/piggy":
            try:
                amount = float(data.get("amount"))
            except (TypeError, ValueError):
                return 400, {"error": "bad_amount"}
            if not 1 <= amount <= 10000 or data.get("freq") not in ("week", "month"):
                return 400, {"error": "bad_piggy"}
            p = {"amount": amount, "freq": data["freq"], "goal": data.get("goal") if data.get("goal") in ("pension", "kids", "dream") else "dream",
                 "mode": "active" if data.get("mode") == "active" else "save",
                 "start_day": int(data.get("start_day") or time.time() // 86400), "on": bool(data.get("on", True))}
            return 200, {"piggy": self.store.set_piggy(uid, p)}
        if method == "PUT" and path == "/v2/progress":
            lessons = data.get("lessons") if isinstance(data.get("lessons"), dict) else {}
            clean = {str(k)[:40]: {"done": bool((v or {}).get("done")), "score": int((v or {}).get("score") or 0)}
                     for k, v in list(lessons.items())[:200]}
            return 200, {"progress": self.store.set_progress(uid, {"lessons": clean, "xp": int(data.get("xp") or 0)})}
        if method == "POST" and path == "/v2/push":
            tok = str(data.get("token") or "")
            if not 20 <= len(tok) <= 4096:
                return 400, {"error": "bad_token"}
            self.store.add_push(uid, tok, "ios" if data.get("platform") == "ios" else "android")
            return 200, {"ok": True}
        if method == "POST" and path == "/v2/agent":
            return self._agent(uid, str(data.get("q") or "").strip())
        if method == "POST" and path == "/v2/vip/check":
            return self._vip(uid, str(data.get("bingx_uid") or "").strip())
        return 404, {"error": "not_found"}

    def _agent(self, uid: str, q: str) -> tuple[int, dict]:
        if not 2 <= len(q) <= 500:
            return 400, {"error": "bad_question"}
        u = self.store.user(uid)
        al = self._allowance(uid, u.get("tier") or "free")
        if al["left"] <= 0:
            return 429, {"error": "limit", "agent": al,
                         "answer": "На сегодня вопросы закончились. Завтра снова 5 бесплатных. VIP — без лимита."}
        home = self._feed("app_home") or {}
        ctx = {k: home.get(k) for k in ("total", "market")}
        fut = home.get("futures") or {}
        ctx["futures"] = {k: fut.get(k) for k in ("usd", "pct", "today_usd", "trades", "wins", "worst_dd_pct", "open")}
        ctx["spot"] = {k: (home.get("spot") or {}).get(k) for k in ("usd", "pct", "alloc", "last_action")}
        answer = None
        if self.llm is not None and getattr(self.llm, "available", lambda: False)():
            try:
                answer = self.llm.complete(AGENT_SYSTEM, "КОНТЕКСТ (демо-счета CRYPTO NOVA):\n"
                                           + json.dumps(ctx, ensure_ascii=False) + "\n\nВОПРОС:\n" + q, temperature=0.3).strip()
            except Exception:  # noqa: BLE001
                answer = None
        if not answer:
            return 503, {"error": "agent_unavailable", "agent": al,
                         "answer": "NOVA сейчас перегружена. Попробуйте через минуту — вопрос не списан."}
        self.store.use(uid, _day())
        return 200, {"answer": answer[:2000], "agent": self._allowance(uid, u.get("tier") or "free")}

    def _vip(self, uid: str, bx: str) -> tuple[int, dict]:
        owner = self.store.bingx_owner(bx) if bx else None
        if owner and owner != uid:
            return 409, {"error": "uid_taken"}
        res = bingx.check_referral(bx, fetch=self.bingx_fetch)
        if res.get("reason") in ("not_configured", "exchange_error"):
            return 503, {"error": res["reason"]}
        self.store.set_vip(uid, bx, bool(res.get("ok")), res)
        return 200, {"vip": bool(res.get("ok")), "reason": res.get("reason"), "user": self._profile(uid)["user"]}


def make_handler(api: Api):
    class H(BaseHTTPRequestHandler):
        server_version = "cnq-api/2"

        def _go(self, method: str) -> None:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._send(413, {"error": "too_large"})
            body = self.rfile.read(n) if n else b""
            hdrs = {k.lower(): v for k, v in self.headers.items()}
            try:
                code, doc = api.handle(method, self.path, hdrs, body)
            except Exception as e:  # noqa: BLE001
                print("api error:", repr(e), file=sys.stderr, flush=True)
                code, doc = 500, {"error": "server"}
            self._send(code, doc)

        def _send(self, code: int, doc: dict) -> None:
            raw = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store" if code != 200 or "/v2/feed" not in self.path else "public, max-age=30")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):  # noqa: N802
            self._go("GET")

        def do_POST(self):  # noqa: N802
            self._go("POST")

        def do_PUT(self):  # noqa: N802
            self._go("PUT")

        def do_DELETE(self):  # noqa: N802
            self._go("DELETE")

        def log_message(self, fmt, *args):  # quiet: nginx keeps the access log
            pass
    return H


def main() -> int:
    run = os.environ.get("CNQ_RUN_DIR", ".")
    os.makedirs(os.path.join(run, "data"), exist_ok=True)
    from cnq.agents.llm import LLM
    from .firebase_admin import FirebaseAdmin
    api = Api(Store(os.path.join(run, "data", "api.sqlite")), FirebaseVerifier(os.environ.get("CNQ_FIREBASE_PROJECT", "crypto-nova")),
              os.path.join(run, "web"), LLM(max_tokens=600), admin=FirebaseAdmin())
    port = int(os.environ.get("CNQ_API_PORT", "8090"))
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(api))
    print(f"cnq-api {VERSION} on 127.0.0.1:{port}", flush=True)

    def _watch() -> None:            # the engine's git pull updates the code → exit, systemd starts the new version
        here = os.path.dirname(os.path.realpath(__file__))
        stamp = lambda: max(os.path.getmtime(os.path.join(here, f)) for f in os.listdir(here) if f.endswith(".py"))
        first = stamp()
        while True:
            time.sleep(60)
            try:
                if stamp() != first:
                    print("api code updated — restarting", flush=True)
                    os._exit(0)
            except OSError:
                pass
    import threading
    threading.Thread(target=_watch, daemon=True).start()
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
