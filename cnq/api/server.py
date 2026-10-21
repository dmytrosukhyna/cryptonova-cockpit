"""CRYPTO NOVA API v2 — the 2.0 app's own small backend, next to the engine on the server (stdlib HTTP + PyJWT).

nginx: app.crypto-capital.app/v2/ → http://127.0.0.1:8090/v2/  (deploy/api/README.md). The old Java backend stays as it is.

Public (no login):  GET /v2/health · GET /v2/agent/selftest (one real NOVA call per 10 min, see _selftest) · GET /v2/home (= app_home.json, fresh every minute) · GET /v2/feed/<name>
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from cnq.agents.i18n import DEFAULT, file_name, norm_lang
from cnq.agents.llm import LLM, PROVIDERS

from . import bingx, console as admin_console, nova
from .auth import AuthError, FirebaseVerifier
from .store import Store

VERSION = "2.0.0"
FEEDS = {"app_home", "app_btc", "academy", "app_portfolio", "app_signals", "markets", "nova"}
LIMIT = {"free": 5, "vip": 100}
MAX_BODY = 16 * 1024
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


def _day() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


class Api:
    def __init__(self, store: Store, verifier: FirebaseVerifier, web_dir: str, llm=None, bingx_fetch=None, admin=None,
                 knowledge: nova.Knowledge | None = None, console: admin_console.Console | None = None):
        self.store, self.verifier, self.web, self.llm, self.bingx_fetch = store, verifier, web_dir, llm, bingx_fetch
        self.admin = admin                       # Firebase Admin (deletes the login with the account)
        self.console = console or admin_console.Console(store, web_dir)   # the owner's admin panel, /v2/admin/*
        self.nova = knowledge or nova.Knowledge()
        self.agent_last: dict | None = None      # the last NOVA call: result + time, shown in /v2/health
        self.selftest: dict | None = None        # GET /v2/agent/selftest: the last real model call on a fixed question
        self._selftest_lock = threading.Lock()

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

    def _profile(self, uid: str) -> dict:
        u = self.store.user(uid)
        return {"user": {k: u.get(k) for k in ("uid", "email", "name", "tier", "vip_since", "bingx_uid", "created")},
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
            return 200, {"ok": True, "version": VERSION, "home_age_sec": age, "ready": ready, "agent_last": self.agent_last}
        if method == "GET" and path == "/v2/diag":
            return 200, self._diag()
        if method == "GET" and path == "/v2/agent/selftest":
            return 200, self._selftest(lang)
        if method == "GET" and path == "/v2/home":
            d = self._feed("app_home", lang)
            return (200, d) if d else (503, {"error": "feed_unavailable"})
        m = re.fullmatch(r"/v2/feed/([a-z_]+)", path)
        if method == "GET" and m:
            if m.group(1) not in FEEDS:
                return 404, {"error": "unknown_feed"}
            d = self._feed(m.group(1), lang)
            return (200, d) if d is not None else (503, {"error": "feed_unavailable"})
        if path == "/v2/admin" or path.startswith("/v2/admin/"):          # the owner's admin panel: its own token, not Firebase
            return self.console.handle(method, path, url.query, headers, body)
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
        return 404, {"error": "not_found"}

    def _agent(self, uid: str, data: dict, lang: str = DEFAULT) -> tuple[int, dict]:
        """NOVA. Request {"q": question (≤ 500 chars, longer is cut), "ctx": optional plain-text summary of the user's own demo
        account and piggy bank that the app builds (≤ 600 chars kept, control characters removed; given to the model as the user's
        data, never as instructions), "lang": the app's language — en / ru / uk / el / es, anything else → ru}.
        Response: ALWAYS HTTP 200 with "answer" (text to show as NOVA's message, in that language) and "agent" {"limit", "left"};
        "error" says why it is not a model answer. The question is counted only when there is no "error" and no "source".
          (no error)      the model answered; agent.left is the new allowance
          source: "faq"   the model failed, but the question matched a FAQ item (data/nova_faq[.<lang>].json) — its answer, not
                          counted
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
            return 200, {"error": "limit", "answer": nova.limit(lang, vip=tier == "vip"), "agent": al}
        ctx = nova.clean_text(data.get("ctx"), nova.CTX_MAX)
        t0, answer, result = time.time(), "", "not_configured"
        llm = copy.copy(self.llm) if isinstance(self.llm, LLM) else self.llm   # its own last_finish per request (threads)
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
            if n > MAX_BODY:
                return self._send(413, {"error": "too_large"})
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

        def _is_admin(self) -> bool:
            p = self.path.split("?", 1)[0].rstrip("/")
            return p == "/v2/admin" or p.startswith("/v2/admin/")

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
