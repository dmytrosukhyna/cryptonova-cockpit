"""CRYPTO NOVA API v2 — the 2.0 app's own small backend, next to the engine on the server (stdlib HTTP + PyJWT).

nginx: app.crypto-capital.app/v2/ → http://127.0.0.1:8090/v2/  (deploy/api/README.md). The old Java backend stays as it is.

Public (no login):  GET /v2/health · GET /v2/agent/selftest (one real NOVA call per 10 min, see _selftest) · GET /v2/home (= app_home.json, fresh every minute) · GET /v2/feed/<name>
                    (/v2/home also carries "news": «Главные новости рынка» from web/decision.json in the request's language — cnq/api/home_news.py)
                    (/v2/feed/nova = web/nova.json: NOVA's greeting, starter questions and topics — see cnq/api/nova.py)
                    ?lang=en|ru|uk|el|es (20.10, the app's language): web/<name>.<lang>.json when it exists, else <name>.json
                    (Russian); missing / unknown lang → ru. POST /v2/agent takes "lang" in its body the same way.
With the Firebase ID token («Authorization: Bearer …»):
  POST /v2/me        profile + settings + piggy + lesson progress + AI-agent allowance (creates the user on first call)
  PUT  /v2/prefs     notification switches (trades, news, piggy, brief)
  PUT  /v2/piggy     the free demo piggy bank: amount, how often, goal, mode, start day
  PUT  /v2/progress  lessons done, quiz scores, XP
  POST /v2/push      the phone's FCM token
  POST /v2/agent     a question to NOVA (free: 5 a day, real account connected: 100) — {"q", "ctx"?, "lang"?}; always 200 with
                     an answer in that language, see Api._agent
  POST /v2/vip/check BingX UID → invited by our link and deposited → VIP
  POST /v2/portfolio/parse   build 45 «Разбор портфеля»: the raw file (≤ 10 MB: CSV / XLSX / PDF / screenshot) → the coins found
                             (cnq/api/pf_parse.py); POST /v2/portfolio/review {"holdings", "lang"} → the review by the system's
                             rules (cnq/api/pf_review.py). In memory only: no file, no row, no log line with the content.
                             GET /v2/portfolio/caps (public) → which inputs work (screenshots only with a vision model)
  GET  /v2/spot/top          build 45 «Топ-10 монет на заметку» (public): web/ideas.json's first 10 «можно брать / добрать / на
                             небольшую сумму», a reason each in ?lang=, held by «CRYPTO NOVA портфель», on OKX / BingX spot (cnq/api/spot_top.py)
  DELETE /v2/me      delete the account: every row on our server + the Firebase login (Apple 5.1.1(v))
The owner's admin panel (web/admin.html), its own token CNQ_ADMIN_TOKEN, not Firebase: /v2/admin/* — see cnq/api/console.py.
Demo only until 2.1; nothing here places orders or holds money."""
from __future__ import annotations

import copy
import datetime as dt
import json
import os
import shutil
import re
import sys
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.etree import ElementTree as ET
from urllib.parse import parse_qs, urlsplit

from cnq.agents.i18n import DEFAULT, LANGS, file_name, norm_lang
from cnq.agents.llm import LLM, PROVIDERS

from . import access as access_codes, bingx, coin_verdict, console as admin_console, home_news, nova, pf_parse, pf_review, spot_top
from .auth import AuthError, FirebaseVerifier
from .store import Store

VERSION = "2.0.0"
FEEDS = {"app_home", "app_btc", "academy", "app_portfolio", "app_signals", "markets", "nova"}
LIMIT = {"free": 5, "vip": 100}
MAX_BODY = 16 * 1024
# 28.10 (build 46, owner: «в закрытой системе AI не отвечает»): the panel's chat (POST /v2/admin/nova) sends the panel's context —
# 12.7 KB measured on 28.10 (nova-panel-probe), so a busier market (more news, events, positions) crossed the 16 KB cap: the API
# answered 413 with the portfolio-file sentence «Файл больше 10 МБ». Its own cap now, under nginx's 32 KB (above that nginx answers
# an HTML 413 without CORS and the browser reports «нет связи»); the panel also trims its context to fit (web/app.html ctxForAI).
PANEL_MAX_BODY = 30 * 1024
# NOVA's model call (20.10: «Сколько я заработал?» failed — the reasoning model spent 506 of max_tokens=600 on hidden thinking,
# the answer came back cut or empty → 503 → the app said «проверьте интернет»). Low reasoning effort + room for the answer, and a
# time budget that fits the app (60 s) and nginx (proxy_read_timeout 60 s): ≤ 20 s per call, ≤ 6 s pause before one 429 retry.
AGENT_LLM = {"max_tokens": 800, "timeout": 20.0, "max_wait_429": 6.0, "reasoning_effort": "low"}
AGENT_TEMPERATURE = 0.6
# a feed the engine rewrites every minute: its translation is served only while it is as fresh as the Russian file (a language
# file left behind by a stopped writer must not show old trades); static feeds (academy, nova) are served whatever their age
LIVE_FEEDS = {"app_home"}
LANG_STALE_S = 600


def agent_llm() -> LLM:
    """NOVA's model. CNQ_NOVA_LLM_KEY (+ optional CNQ_NOVA_LLM_PROVIDER, CNQ_NOVA_LLM_MODEL) gives NOVA its own key and quota, e.g.
    a paid plan; without it NOVA shares the engine's CNQ_LLM_* key (the free groq plan limits tokens per minute)."""
    key = os.environ.get("CNQ_NOVA_LLM_KEY")
    if not key:
        return LLM(**AGENT_LLM)
    prov = (os.environ.get("CNQ_NOVA_LLM_PROVIDER") or os.environ.get("CNQ_LLM_PROVIDER") or "groq").lower()
    prov = prov if prov in PROVIDERS else "groq"
    return LLM(api_key=key, provider=prov, model=os.environ.get("CNQ_NOVA_LLM_MODEL") or PROVIDERS[prov]["model"], **AGENT_LLM)


def vision_config(llm=None) -> dict | None:
    """build 45: the model that reads a screenshot. CNQ_VISION_LLM_KEY (+ CNQ_VISION_LLM_PROVIDER, default gemini, and
    CNQ_VISION_LLM_MODEL) gives it its own key; without it NOVA's own model is used when it can see images (Gemini, Claude,
    Llama 4 …). NOVA's model on 22.10 is openai/gpt-oss-120b — text only, so screenshots are off until a key is set."""
    key = os.environ.get("CNQ_VISION_LLM_KEY")
    if key:
        prov = (os.environ.get("CNQ_VISION_LLM_PROVIDER") or "gemini").lower()
        if prov not in PROVIDERS:
            return None
        model = os.environ.get("CNQ_VISION_LLM_MODEL") or PROVIDERS[prov]["model"]
    elif isinstance(llm, LLM) and llm.key:
        prov, model, key = llm.provider, llm.model, llm.key
    else:
        return None
    if not pf_parse.VISION_RE.search(model or ""):
        return None
    return {"provider": prov, "model": model, "key": key, "url": PROVIDERS[prov]["url"], "style": PROVIDERS[prov]["style"]}


def _day() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


class Api:
    def __init__(self, store: Store, verifier: FirebaseVerifier, web_dir: str, llm=None, bingx_fetch=None, admin=None,
                 knowledge: nova.Knowledge | None = None, console: admin_console.Console | None = None,
                 news: home_news.HomeNews | None = None):
        self.store, self.verifier, self.web, self.llm, self.bingx_fetch = store, verifier, web_dir, llm, bingx_fetch
        # build 44: «Главные новости рынка» on Home — the desk's top stories, translated once by NOVA's model and kept on disk
        # next to the API's other data (<run>/data/news_i18n.json, git-ignored)
        self.news = news or home_news.HomeNews(web_dir, os.path.join(os.path.dirname(os.path.abspath(web_dir)), "data", "news_i18n.json"), llm)
        self.admin = admin                       # Firebase Admin (deletes the login with the account)
        self.console = console or admin_console.Console(store, web_dir)   # the owner's admin panel, /v2/admin/*
        self.access = access_codes.AccessCodes(store)          # 22.10: guest access codes, checked here (no public hash file)
        self.console.access = self.access
        self.nova = knowledge or nova.Knowledge()
        self.agent_last: dict | None = None      # the last NOVA call: result + time, shown in /v2/health
        self.selftest: dict | None = None        # GET /v2/agent/selftest: the last real model call on a fixed question
        self.panel_last: dict | None = None      # the last closed-system NOVA call (POST /v2/admin/nova): result + time
        self._selftest_lock = threading.Lock()
        # build 45 «Разбор портфеля»: the system's data, per-user limits (memory only), the screenshot model, the last call's pulse
        self.pf_data = pf_review.Data(web_dir)
        self.pf_parse_limit = pf_review.Limiter(per_window=6, window_s=600, per_day=30)
        self.pf_review_limit = pf_review.Limiter(per_window=10, window_s=600, per_day=40)
        self.vision = vision_config(llm)
        self.vision_reader = pf_parse.vision_holdings    # tests put a fake here
        self.pf_last: dict | None = None         # counts and timing only — never a coin or an amount
        self.listings = spot_top.Listings()      # build 45 «Топ-10 монет на заметку»: OKX / BingX spot coins (background, 12 h)

    # ---------------------------------------------------------------- helpers
    def _read(self, path: str) -> dict | None:
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return None

    def _feed(self, name: str, lang: str = DEFAULT) -> dict | None:
        """web/<name>.json, or web/<name>.<lang>.json for another language when it exists (else the Russian one)"""
        base = os.path.join(self.web, f"{name}.json")
        if lang != DEFAULT:
            p = os.path.join(self.web, file_name(name, lang))
            try:
                fresh = name not in LIVE_FEEDS or os.path.getmtime(p) >= os.path.getmtime(base) - LANG_STALE_S
            except OSError:
                fresh = os.path.exists(p)
            if fresh:
                d = self._read(p)
                if d is not None:
                    return d
        return self._read(base)

    def _llm_ready(self) -> bool:
        """cnq.agents.llm.LLM has `available` as a property; test fakes may have it as a method"""
        a = getattr(self.llm, "available", False) if self.llm is not None else False
        try:
            return bool(a() if callable(a) else a)
        except Exception:  # noqa: BLE001
            return False

    def _diag(self) -> dict:
        """20.10: the engine went quiet and nobody could see the server — a small public view of its pulse (no secrets): disk
        space, the age of the engine's status and of the app home, the steps that failed in the last cycle, the last git sync."""
        out: dict = {"now": int(time.time())}
        try:
            du = shutil.disk_usage(self.web)
            out["disk_free_gb"] = round(du.free / 1e9, 2)
            out["disk_used_pct"] = round(du.used / du.total * 100, 1)
        except OSError:
            pass
        try:                                        # the machine's memory and the engine service's own memory account (cgroup v2)
            mi = dict(ln.split(":", 1) for ln in open("/proc/meminfo", encoding="utf-8").read().splitlines() if ":" in ln)
            out["mem_total_mb"] = int(mi["MemTotal"].split()[0]) // 1024
            out["mem_avail_mb"] = int(mi["MemAvailable"].split()[0]) // 1024
        except Exception:  # noqa: BLE001
            pass
        cg = "/sys/fs/cgroup/system.slice/cnq-engine.service"
        for f, key in (("memory.current", "engine_mem_mb"), ("memory.peak", "engine_mem_peak_mb"), ("memory.max", "engine_mem_max_mb")):
            try:
                v = open(os.path.join(cg, f), encoding="utf-8").read().strip()
                out[key] = int(v) // (1024 * 1024) if v.isdigit() else v
            except OSError:
                pass
        try:                                        # every program in the engine service right now, with its memory
            procs = []
            for pid in open(os.path.join(cg, "cgroup.procs"), encoding="utf-8").read().split():
                try:
                    rss = next((int(ln.split()[1]) // 1024 for ln in open(f"/proc/{pid}/status", encoding="utf-8") if ln.startswith("VmRSS:")), 0)
                    cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
                    procs.append({"pid": int(pid), "rss_mb": rss, "cmd": cmd[-90:]})
                except OSError:
                    pass
            out["engine_procs"] = procs
        except Exception:  # noqa: BLE001
            pass
        try:                                        # what fills the account: programs (anon) or the file cache (file, dirty, writeback)
            st = dict(ln.split() for ln in open(os.path.join(cg, "memory.stat"), encoding="utf-8").read().splitlines() if ln.strip())
            out["engine_mem_stat_mb"] = {k: int(st[k]) // (1024 * 1024) for k in ("anon", "file", "file_dirty", "file_writeback", "shmem",
                                                                                  "kernel", "sock") if k in st}
        except Exception:  # noqa: BLE001
            pass
        try:
            ev = dict(ln.split() for ln in open(os.path.join(cg, "memory.events"), encoding="utf-8").read().splitlines() if ln.strip())
            out["engine_oom_kills"] = int(ev.get("oom_kill", 0))
            out["engine_mem_max_hits"] = int(ev.get("max", 0))
        except Exception:  # noqa: BLE001
            pass
        eng = self._read(os.path.join(self.web, "engine.json")) or {}
        if eng.get("asof"):
            out["engine_age_sec"] = round(time.time() - eng["asof"] / 1000)
        out["engine_cycle"] = eng.get("cycle")
        out["engine_cycle_sec"] = eng.get("cycle_sec")
        out["engine_errors"] = eng.get("errors")
        out["engine_err_tail"] = {k: str(v.get("err", ""))[-160:] for k, v in (eng.get("steps") or {}).items() if not v.get("ok")}
        ls = eng.get("last_sync") or {}
        out["last_sync"] = {k: ls.get(k) for k in ("t", "pulled", "pushed", "committed")}
        if ls.get("t"):
            out["last_sync_age_sec"] = round(time.time() - ls["t"] / 1000)
        now = self._read(os.path.join(self.web, "engine_now.json")) or {}
        if now.get("since"):
            out["engine_step"] = now.get("step")
            out["engine_step_sec"] = round(time.time() - now["since"] / 1000)
            out["engine_step_timeout"] = now.get("timeout")
            out["engine_started_age_sec"] = round(time.time() - (now.get("started") or 0) / 1000)
            out["engine_step_rss_mb"] = now.get("rss_mb")
            if now.get("phase"):
                out["engine_phase"] = now.get("phase")
                out["engine_phase_age_sec"] = round(time.time() - (now.get("phase_t") or 0) / 1000)
        crash = self._read(os.path.join(self.web, "engine_crash.json")) or {}
        if crash.get("t"):
            out["engine_crash_age_sec"] = round(time.time() - crash["t"] / 1000)
            out["engine_crash"] = str(crash.get("trace", ""))[-400:]
        for name in ("app_home.json", "paper.json"):
            try:
                out[name + "_age_sec"] = round(time.time() - os.path.getmtime(os.path.join(self.web, name)))
            except OSError:
                pass
        return out

    def _uid(self, headers: dict) -> dict:
        h = headers.get("authorization") or ""
        if not h.lower().startswith("bearer "):
            raise AuthError("no token")
        return self.verifier.verify(h[7:].strip())

    def _allowance(self, uid: str, tier: str) -> dict:
        lim = LIMIT.get(tier or "free", 5)
        used = self.store.used_today(uid, _day())
        return {"limit": lim, "left": max(0, lim - used)}

    @staticmethod
    def _is_owner(email: str | None) -> bool:
        """build 45 (owner 22.10): the signed-in user is the owner when their email equals CNQ_OWNER_EMAIL (/etc/cnq.env).
        Empty / unset = nobody is the owner. The email itself is never in the repo or the app."""
        owner = (os.environ.get("CNQ_OWNER_EMAIL") or "").strip().lower()
        return bool(owner) and (email or "").strip().lower() == owner

    def _profile(self, uid: str) -> dict:
        u = self.store.user(uid)
        return {"user": {**{k: u.get(k) for k in ("uid", "email", "name", "tier", "vip_since", "bingx_uid", "created")},
                         "is_owner": self._is_owner(u.get("email"))},
                "prefs": self.store.prefs(uid), "piggy": self.store.piggy(uid), "progress": self.store.progress(uid),
                "agent": self._allowance(uid, u.get("tier") or "free")}

    # ---------------------------------------------------------------- the router
    def handle(self, method: str, path: str, headers: dict, body: bytes) -> tuple[int, dict]:
        url = urlsplit(path)
        path = url.path.rstrip("/")
        lang = norm_lang((parse_qs(url.query).get("lang") or [""])[0])
        if method == "GET" and path == "/v2/health":
            home = self._feed("app_home") or {}
            age = None if not home.get("asof") else round(time.time() - home["asof"] / 1000)
            ready = {"llm": self._llm_ready(),
                     "firebase_admin": bool(getattr(self.admin, "ready", False)),
                     "bingx_partner": bool(os.environ.get("CNQ_BINGX_AGENT_KEY") and os.environ.get("CNQ_BINGX_AGENT_SECRET"))}
            return 200, {"ok": True, "version": VERSION, "home_age_sec": age, "ready": ready, "agent_last": self.agent_last,
                         "panel_last": self.panel_last, "news_last": self.news.last,
                         "portfolio": {"vision": bool(self.vision), "last": self.pf_last}}
        if method == "GET" and path == "/v2/diag":
            return 200, self._diag()
        if method == "GET" and path == "/v2/agent/selftest":
            return 200, self._selftest(lang)
        if method == "GET" and path == "/v2/home":
            d = self._feed("app_home", lang)
            if not d:
                return 503, {"error": "feed_unavailable"}
            # build 44: the caller's country when the proxy in front knows it (Cloudflare CF-IPCountry or nginx geoip → X-Country);
            # the app uses it only to choose which exchange to name (never a BingX link in the EU/EEA). Nothing is stored.
            cc = str(headers.get("cf-ipcountry") or headers.get("x-country") or "").strip().upper()
            if re.fullmatch(r"[A-Z]{2}", cc) and cc not in ("XX", "T1"):
                d = {**d, "geo": {"country": cc}}
            # build 44: «Главные новости рынка» (≤ 5 stories in this language); no stories → no key, the app hides the block
            try:
                news = self.news.feed(lang)
            except Exception as e:  # noqa: BLE001 — the home feed never fails because of the news
                print("home news error:", repr(e)[:200], file=sys.stderr, flush=True)
                news = []
            if news:
                d = {**d, "news": news}
            return 200, d
        m = re.fullmatch(r"/v2/feed/([a-z_]+)", path)
        if method == "GET" and m:
            if m.group(1) not in FEEDS:
                return 404, {"error": "unknown_feed"}
            d = self._feed(m.group(1), lang)
            return (200, d) if d is not None else (503, {"error": "feed_unavailable"})
        if method == "GET" and path == "/v2/portfolio/caps":            # build 45: which files «Разбор портфеля» reads now
            return 200, {"image": bool(self.vision), "csv": True, "xlsx": True, "pdf": True,
                         "max_mb": pf_parse.MAX_FILE // (1024 * 1024), "exchanges": ["okx", "bingx", "binance"]}
        if method == "GET" and path == "/v2/spot/top":                  # build 45: «Топ-10 монет на заметку» (Spot tab)
            d = spot_top.top(self.pf_data, lang, self.listings.get())
            return (200, d) if d["rows"] or self.pf_data.get("ideas") else (503, {"error": "feed_unavailable"})
        if method == "POST" and path == "/v2/access/verify":             # 22.10: a guest's access code for cryptonova.vip
            try:
                data = json.loads(body.decode() or "{}") if body else {}
            except ValueError:
                return 400, {"error": "bad_json"}
            ip = str(headers.get("x-real-ip") or headers.get("x-forwarded-for") or "local").split(",")[0].strip()[:64]
            return self.access.verify(str((data or {}).get("code") or "")[:64], ip)
        if method == "POST" and path == "/v2/admin/nova":                # the closed system's NOVA chat through the server's key
            deny = self.console.check(headers)
            return deny if deny else self._panel_nova(body)
        if path == "/v2/admin" or path.startswith("/v2/admin/"):          # the owner's admin panel: its own token, not Firebase
            return self.console.handle(method, path, url.query, headers, body)
        # ------------------------------------------------------------ signed-in user
        try:
            who = self._uid(headers)
        except AuthError as e:
            return 401, {"error": "unauthorized", "detail": str(e)[:120]}
        uid = who["uid"]
        if method == "POST" and path == "/v2/portfolio/parse":          # the raw file, not JSON
            return self._pf_guard("parse", lang, lambda: self._pf_parse(uid, body, lang))
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
                 "start_day": int(data.get("start_day") or time.time() // 86400), "on": bool(data.get("on", True)),
                 "years": max(0, min(40, int(data.get("years") or 0)))}
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
            lang = norm_lang(data.get("lang") or lang)
            try:
                return self._agent(uid, data, lang)
            except Exception as e:  # noqa: BLE001 — NOVA never answers with a bare 500
                print("agent error:", repr(e)[:300], file=sys.stderr, flush=True)
                self.agent_last = {"result": "server_error", "ms": None, "at": int(time.time()), "lang": lang}
                return 200, {"error": "busy", "answer": nova.busy(lang)}
        if method == "POST" and path == "/v2/vip/check":
            return self._vip(uid, str(data.get("bingx_uid") or "").strip())
        if method == "POST" and path == "/v2/portfolio/review":
            lang = norm_lang(data.get("lang") or lang)
            return self._pf_guard("review", lang, lambda: self._pf_review(uid, data, lang))
        return 404, {"error": "not_found"}

    # ---------------------------------------------------------------- build 45: «Разбор портфеля»
    def _pf_guard(self, what: str, lang: str, fn) -> tuple[int, dict]:
        """never a bare 500, and never the file's content in a log line: only the error's type is printed"""
        t0 = time.time()
        try:
            code, doc = fn()
        except Exception as e:  # noqa: BLE001
            print(f"portfolio {what}: {type(e).__name__}", file=sys.stderr, flush=True)
            code, doc = 200, {"error": "server", "message": pf_review.message("server", lang)}
        self.pf_last = {"what": what, "at": int(t0), "ms": round((time.time() - t0) * 1000), "code": code,
                        "result": doc.get("error") or "ok", "kind": doc.get("kind"), "coins": len(doc.get("holdings") or doc.get("rows") or [])}
        return code, doc

    def _pf_parse(self, uid: str, data: bytes, lang: str) -> tuple[int, dict]:
        """POST /v2/portfolio/parse?lang=… — the body is the file itself (≤ 10 MB). → 200 {"holdings": [{"sym", "amount",
        "value_usd"?}], "kind": csv|xlsx|pdf|image, "exchange", "mode", "note"} or {"error", "message"} (the sentence to show).
        413 too_large · 429 rate (per user: 6 in 10 min, 30 a day). The file lives only in this call's memory."""
        def err(code, http=200, **kw):
            return http, {"error": code, "message": pf_review.message(code, lang, **kw)}
        if len(data) > pf_parse.MAX_FILE:
            return err("too_large", 413)
        wait = self.pf_parse_limit.hit(uid)
        if wait:
            return err("rate", 429, m=wait)
        kind = pf_parse.sniff(data)
        if kind is None:
            return err("bad_type")
        if kind == "xls":
            return err("xls")
        known = self.pf_data.known()
        note = None
        try:
            if kind == "csv":
                res = pf_parse.parse_table(pf_parse.read_csv(data), known)
            elif kind == "xlsx":
                res = pf_parse.best_table(pf_parse.read_xlsx(data), known)
            elif kind == "pdf":
                text = pf_parse.pdf_text(data)
                res = pf_parse.parse_text(text, known)
                if not res["holdings"] and text.strip() and self._llm_ready():
                    llm = copy.copy(self.llm) if isinstance(self.llm, LLM) else self.llm
                    if isinstance(llm, LLM):
                        llm.max_tokens, llm.timeout, llm.max_wait_429 = 2000, 25.0, 3.0
                    res = {"holdings": pf_parse.llm_text_holdings(llm, text, known), "mode": "model", "exchange": res.get("exchange")}
                    note = "note_model"
                del text
            else:
                if not self.vision:
                    return err("image_unsupported")
                res = {"holdings": self.vision_reader(self.vision, data, known), "mode": "model", "exchange": None}
                note = "note_model"
        except pf_parse.ParseError as e:
            return err(str(e) if str(e) in pf_review.MSG else "read_failed")
        except (ET.ParseError, ValueError, KeyError, IndexError, zlib.error, EOFError):
            return err("read_failed")
        if not res["holdings"]:
            return err("no_rows")
        if note is None:
            note = "note_ledger" if res.get("mode") == "ledger" else "note_check"
        return 200, {"holdings": res["holdings"], "kind": kind, "exchange": res.get("exchange"), "mode": res.get("mode"),
                     "note": pf_review.message(note, lang), "max": pf_parse.MAX_HOLDINGS}

    def _pf_review(self, uid: str, data: dict, lang: str) -> tuple[int, dict]:
        """POST /v2/portfolio/review {"holdings": [{"sym", "amount", "value_usd"?}] (≤ 100, as the user confirmed them), "lang"}
        → the review (cnq/api/pf_review.review) or {"error", "message"}. 429 rate (10 in 10 min, 40 a day)."""
        hs = pf_review.clean_holdings(data.get("holdings"), pf_parse.MAX_HOLDINGS)
        if not hs:
            return 200, {"error": "bad_holdings", "message": pf_review.message("bad_holdings", lang)}
        wait = self.pf_review_limit.hit(uid)
        if wait:
            return 429, {"error": "rate", "message": pf_review.message("rate", lang, m=wait)}
        out = pf_review.review(hs, self.pf_data, lang)
        return 200, out

    def _agent(self, uid: str, data: dict, lang: str = DEFAULT) -> tuple[int, dict]:
        """NOVA. Request {"q": question (≤ 500 chars, longer is cut), "ctx": optional plain-text summary of the user's own demo
        account and piggy bank that the app builds (≤ 600 chars kept, control characters removed; given to the model as the user's
        data, never as instructions), "lang": the app's language — en / ru / uk / el / es, anything else → ru}.
        Response: ALWAYS HTTP 200 with "answer" (text to show as NOVA's message, in that language) and "agent" {"limit", "left"};
        "error" says why it is not a model answer. The question is counted only when there is no "error" and no "source".
          (no error)      the model answered; agent.left is the new allowance
          source: "faq"   the model failed, but the question matched a FAQ item (data/nova_faq[.<lang>].json) — its answer, not
                          counted
          source: "coin"  22.10: a question about a coin («что думаешь о монете Саи», «стоит ли брать биток») — the system's own
                          verdict built from its data (cnq/api/coin_verdict.py) without the model's words, not counted; when the
                          model phrased it (every number checked against the data) there is no "source" and it is counted
          source: "clarify"  two coins it could be — one short question back («Вы про WLD (Worldcoin) или про WLFI …?»), not counted
          error: "busy"   the model failed, timed out, answered empty or is not configured, or any server error — not counted
          error: "limit"  no questions left today (free 5, VIP 100) — not counted
          error: "bad_question"  empty or unreadable question — not counted
        Other codes of this route: 400 bad_json (the body is not a JSON object), 401 unauthorized (no or invalid Firebase token),
        413 too_large (body > 16 KB)."""
        u = self.store.user(uid) or {}
        tier = u.get("tier") or "free"
        al = self._allowance(uid, tier)
        q = nova.clean_text(data.get("q"), nova.Q_MAX)
        if len(q) < 2:
            return 200, {"error": "bad_question", "answer": nova.bad_question(lang), "agent": al}
        if al["left"] <= 0:
            # 28.10 (build 46 §11.4): after the limit the app's own ready-made questions still get their FAQ answer (not counted)
            nq = nova._norm(q)
            if any(self.nova._same(nq, it) for lg in LANGS for it in self.nova.faq(lg)):
                hit = self.nova.match(q, lang)
                if hit:
                    return 200, {"answer": hit["a"], "source": "faq", "agent": al}
            return 200, {"error": "limit", "answer": nova.limit(lang, vip=tier == "vip"), "agent": al}
        ctx = nova.clean_text(data.get("ctx"), nova.CTX_MAX)
        t0, answer, result = time.time(), "", "not_configured"
        llm = copy.copy(self.llm) if isinstance(self.llm, LLM) else self.llm   # its own last_finish per request (threads)
        cv = self._coin(q, lang, llm)          # 22.10: «что думаешь о монете Саи» → the system's own verdict on SUI
        if cv:
            self.agent_last = {"result": "coin_llm" if cv["llm"] else "coin_" + cv["source"], "ms": round((time.time() - t0) * 1000),
                               "at": int(time.time()), "lang": lang, "coins": cv["coins"]}
            if cv["llm"]:                      # the model phrased it: counted like any model answer
                self.store.use(uid, _day())
                return 200, {"answer": cv["answer"], "agent": self._allowance(uid, tier)}
            return 200, {"answer": cv["answer"], "source": cv["source"], "agent": al}
        if self._llm_ready():
            try:
                raw = llm.complete(self.nova.system(lang), self.nova.user_message(q, self._feed("app_home", lang) or {}, ctx, lang),
                                   temperature=AGENT_TEMPERATURE)
                cut = getattr(llm, "last_finish", None) == "length"
                answer = nova.tidy(raw if isinstance(raw, str) else "", cut=cut)
                result = "empty" if not answer else ("cut" if cut else "ok")
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                result = "timeout" if "timed out" in msg.lower() else "rate_limit" if "429" in msg else "llm_error"
                print(f"agent: {result}: {msg[:200]}", file=sys.stderr, flush=True)
        use = getattr(llm, "last_usage", None) or {}
        self.agent_last = {"result": result, "ms": round((time.time() - t0) * 1000), "at": int(time.time()), "lang": lang,
                           "finish": getattr(llm, "last_finish", None), "prompt_tokens": use.get("prompt_tokens"),
                           "reasoning_tokens": (use.get("completion_tokens_details") or {}).get("reasoning_tokens")}
        if answer:
            self.store.use(uid, _day())
            return 200, {"answer": answer, "agent": self._allowance(uid, tier)}
        hit = self.nova.match(q, lang)
        if hit:
            return 200, {"answer": hit["a"], "source": "faq", "agent": al}
        return 200, {"error": "busy", "answer": nova.busy(lang), "agent": al}

    def _coin(self, q: str, lang: str, llm) -> dict | None:
        """22.10 (owner: «что думаешь о монете Саи» — a vague answer): a question about a coin gets the system's own verdict
        (cnq/api/coin_verdict.py): {"answer", "source": coin | clarify, "llm": phrased by the model?, "coins"}; None — not a
        coin question. Never raises: a failure here leaves NOVA's normal answer."""
        try:
            nq = nova._norm(q)
            if any(self.nova._same(nq, it) for lg in LANGS for it in self.nova.faq(lg)):
                return None                                # the app's own chip («Не поздно ли покупать биткоин?»): its FAQ answer
            with self.listings._lock:
                lists = dict(self.listings.lists)          # the OKX / BingX lists already cached — never a fetch from here
            return coin_verdict.answer(q, lang, self.pf_data, self._feed("app_home", lang) or {}, lists,
                                       llm if self._llm_ready() else None)
        except Exception as e:  # noqa: BLE001
            print(f"agent coin: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr, flush=True)
            return None

    PANEL_SYS_MAX, PANEL_USER_MAX = 4000, 40000
    PANEL_Q = re.compile(r"\n\nВопрос(?: зрителя)?:\s*(.+)\Z", re.S)

    def _panel_nova(self, body: bytes) -> tuple[int, dict]:
        """POST /v2/admin/nova (owner only, CNQ_ADMIN_TOKEN) — 21.10, owner: «в закрытой системе AI-помощник не отвечает».
        The panel's chat asked the model from the browser with a key kept in that browser; without one it only had rule-based
        answers. With the owner's admin token it now asks through the server's own NOVA key. Request {"system", "user",
        "max_tokens"}; answer always 200: {"answer", "ms", "finish"} or {"error": timeout / rate_limit / llm_error / empty /
        not_configured, "detail", "ms"}. Nothing is stored; the last result (no text) is in /v2/health as panel_last."""
        try:
            data = json.loads(body.decode() or "{}") if body else {}
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            return 400, {"error": "bad_json"}
        system = str(data.get("system") or "")[: self.PANEL_SYS_MAX]
        user = str(data.get("user") or "")[: self.PANEL_USER_MAX]
        if len(user) < 2:
            return 400, {"error": "bad_question"}
        t0, out = time.time(), {}
        m = self.PANEL_Q.search(user)                   # the panel's chat: «Контекст панели (JSON): … Вопрос: <q>»
        cv = None
        if m:
            llm = copy.copy(self.llm) if isinstance(self.llm, LLM) else self.llm
            cv = self._coin(nova.clean_text(m.group(1), nova.Q_MAX), DEFAULT, llm)
        if cv:                                          # 22.10: a coin question → the system's own verdict, as in the app
            out = {"answer": cv["answer"], "source": cv["source"]}
        elif not self._llm_ready():
            out = {"error": "not_configured"}
        else:
            llm = copy.copy(self.llm) if isinstance(self.llm, LLM) else self.llm
            try:
                llm.max_tokens = max(200, min(1200, int(data.get("max_tokens") or 800)))
            except (TypeError, ValueError):
                pass
            try:
                raw = llm.complete(system, user, temperature=0.3)
                txt = (raw if isinstance(raw, str) else "").strip()
                out = {"answer": txt, "finish": getattr(llm, "last_finish", None)} if txt else {"error": "empty", "finish": getattr(llm, "last_finish", None)}
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                out = {"error": "timeout" if "timed out" in msg.lower() else "rate_limit" if "429" in msg else "llm_error", "detail": msg[:160]}
                print(f"panel nova: {out['error']}: {msg[:200]}", file=sys.stderr, flush=True)
        out["ms"] = round((time.time() - t0) * 1000)
        self.panel_last = {"result": out.get("error") or "ok", "ms": out["ms"], "at": int(time.time())}
        return 200, out

    SELFTEST_EVERY_S = 600
    SELFTEST_Q = {"ru": "Что такое CRYPTO NOVA и как он работает?", "en": "What is CRYPTO NOVA and how does it work?"}

    def _selftest(self, lang: str = DEFAULT) -> dict:
        """21.10 (owner: «проверить, работает ли AI»): the same model call as /v2/agent — NOVA's system prompt, the live home
        feed, the same limits — on one fixed question, without a login, so a health check (api-health workflow) proves the
        whole chain to the model and back. At most one real call per 10 minutes for everyone; in between the last result is
        returned with its age. Shows the result, the time and the first 200 characters — never a key."""
        now = time.time()
        with self._selftest_lock:
            last = self.selftest
            if last and now - last["at"] < self.SELFTEST_EVERY_S:
                return {**last, "cached": True, "age_sec": round(now - last["at"])}
            lang = lang if lang in self.SELFTEST_Q else DEFAULT
            out: dict = {"at": int(now), "lang": lang, "model": getattr(self.llm, "model", None),
                         "provider": getattr(self.llm, "provider", None), "timeout_s": AGENT_LLM["timeout"]}
            if not self._llm_ready():
                out.update(result="not_configured", ms=None)
            else:
                llm = copy.copy(self.llm) if isinstance(self.llm, LLM) else self.llm
                t0 = time.time()
                try:
                    raw = llm.complete(self.nova.system(lang), self.nova.user_message(self.SELFTEST_Q[lang], self._feed("app_home", lang) or {}, "", lang),
                                       temperature=AGENT_TEMPERATURE)
                    cut = getattr(llm, "last_finish", None) == "length"
                    answer = nova.tidy(raw if isinstance(raw, str) else "", cut=cut)
                    out.update(result="empty" if not answer else ("cut" if cut else "ok"), answer_head=answer[:200])
                except Exception as e:  # noqa: BLE001
                    msg = str(e)
                    out.update(result="timeout" if "timed out" in msg.lower() else "rate_limit" if "429" in msg else "llm_error",
                               error=msg[:200])
                    print(f"agent selftest: {out['result']}: {msg[:200]}", file=sys.stderr, flush=True)
                out.update(ms=round((time.time() - t0) * 1000), finish=getattr(llm, "last_finish", None))
            self.selftest = out
            return {**out, "cached": False, "age_sec": 0}

    def _vip(self, uid: str, bx: str) -> tuple[int, dict]:
        owner = self.store.bingx_owner(bx) if bx else None
        if owner and owner != uid:
            return 409, {"error": "uid_taken"}
        res = bingx.check_referral(bx, fetch=self.bingx_fetch)
        if res.get("reason") in ("not_configured", "exchange_error"):
            return 503, {"error": res["reason"]}
        self.store.set_vip(uid, bx, bool(res.get("ok")), res)
        return 200, {"vip": bool(res.get("ok")), "reason": res.get("reason"), "user": self._profile(uid)["user"]}


def _body_lang(body: bytes) -> str:
    try:
        d = json.loads(body.decode() or "{}")
        return norm_lang(d.get("lang") if isinstance(d, dict) else None)
    except Exception:  # noqa: BLE001
        return DEFAULT


def make_handler(api: Api):
    class H(BaseHTTPRequestHandler):
        server_version = "cnq-api/2"

        def _go(self, method: str) -> None:
            n = int(self.headers.get("Content-Length") or 0)
            p = self.path.split("?", 1)[0].rstrip("/")
            limit = (pf_parse.MAX_FILE if p == "/v2/portfolio/parse" else          # build 45: a portfolio file ≤ 10 MB
                     PANEL_MAX_BODY if p == "/v2/admin/nova" else MAX_BODY)
            if n > limit:
                if p != "/v2/portfolio/parse":            # 28.10: the file sentence only for the file route
                    return self._send(413, {"error": "too_large", "limit_kb": limit // 1024})
                q = parse_qs(urlsplit(self.path).query)
                return self._send(413, {"error": "too_large", "message": pf_review.message("too_large", norm_lang((q.get("lang") or [""])[0]))})
            body = self.rfile.read(n) if n else b""
            hdrs = {k.lower(): v for k, v in self.headers.items()}
            try:
                code, doc = api.handle(method, self.path, hdrs, body)
            except Exception as e:  # noqa: BLE001
                print("api error:", repr(e), file=sys.stderr, flush=True)
                code, doc = 500, {"error": "server"}
                if self.path.split("?", 1)[0].rstrip("/") == "/v2/agent":      # NOVA: the app shows the answer, never «нет интернета»
                    code, doc = 200, {"error": "busy", "answer": nova.busy(_body_lang(body))}
            self._send(code, doc)

        def _is_admin(self) -> bool:          # the pages on cryptonova.vip call these from the browser → CORS for that origin only
            p = self.path.split("?", 1)[0].rstrip("/")
            return p == "/v2/admin" or p.startswith("/v2/admin/") or p == "/v2/access/verify"

        def _cors(self) -> None:          # the admin page lives on cryptonova.vip and calls this API from the owner's browser
            self.send_header("Access-Control-Allow-Origin", admin_console.origin())
            self.send_header("Vary", "Origin")

        def _send(self, code: int, doc: dict) -> None:
            raw = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store" if code != 200 or "/v2/feed" not in self.path else "public, max-age=30")
            if self._is_admin():
                self._cors()
            self.end_headers()
            self.wfile.write(raw)

        def do_OPTIONS(self):  # noqa: N802 — CORS preflight, only for the admin panel
            if not self._is_admin():
                return self._send(404, {"error": "not_found"})
            self.send_response(204)
            self._cors()
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()

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
    from .firebase_admin import FirebaseAdmin
    api = Api(Store(os.path.join(run, "data", "api.sqlite")), FirebaseVerifier(os.environ.get("CNQ_FIREBASE_PROJECT", "crypto-nova")),
              os.path.join(run, "web"), agent_llm(), admin=FirebaseAdmin())
    port = int(os.environ.get("CNQ_API_PORT", "8090"))
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(api))
    print(f"cnq-api {VERSION} on 127.0.0.1:{port}", flush=True)

    def _watch() -> None:            # the engine's git pull updates the code → exit, systemd starts the new version
        here = os.path.dirname(os.path.realpath(__file__))
        agents = os.path.join(os.path.dirname(here), "agents")            # what the API imports from the engine's package
        files = lambda: [os.path.join(here, f) for f in os.listdir(here) if f.endswith(".py")] + \
            [os.path.join(agents, f) for f in ("i18n.py", "llm.py") if os.path.exists(os.path.join(agents, f))]
        stamp = lambda: max(os.path.getmtime(p) for p in files())
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
