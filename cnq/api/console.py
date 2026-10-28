"""The owner's admin console API — /v2/admin/* (21.10.2026, owner: «админ-панель … CRM, масштабирование, Meta/Facebook, шлюзы,
коннекторы … продумай всё до мелочей»). The page is web/admin.html (https://cryptonova.vip/admin.html).

Access: only the owner. Every call carries `Authorization: Bearer <CNQ_ADMIN_TOKEN>` (28.10: or the owner session «cno_…» from
the panel's sign-in, cnq/api/owner_auth.py) — a long random string in /etc/cnq.env
(never in git, never in chat), compared in constant time. CNQ_ADMIN_TOKEN absent or shorter than 24 characters → every admin call
answers 403 admin_disabled. A wrong or missing token → 401; 20 wrong tokens from one address within 10 minutes → 429 for that
address. The token is never logged or echoed. Answers carry CORS for https://cryptonova.vip only (CNQ_ADMIN_ORIGIN overrides it
for a local test). Numbers are counted from data/api.sqlite as they are — nothing is estimated or padded.

Read:
  GET  /v2/admin/ping                    {"ok": true} — the page checks the token with it
  GET  /v2/admin/overview                users (total, new per day for 30 days, active 1 / 7 / 30 days, languages), push devices,
                                         NOVA questions per day, lesson progress, piggy (demo), BingX UID checks, the funnel
                                         (installs → sign-up → UID sent → UID confirmed; installs are the owner's own numbers
                                         from App Store Connect / Google Play, entered in the plan), referral checks
  GET  /v2/admin/users?q=&seg=&sort=&page=&size=   search (email, name, uid, BingX UID) + segment + paging (size ≤ 100)
  GET  /v2/admin/user/<uid>              one user's card: profile, prefs, piggy, lessons, NOVA, push devices (platform and
                                         date, never the token), UID checks, CRM notes and tags
  GET  /v2/admin/leads?q=&page=&size=    people who wrote to the Telegram bot (data/agents.sqlite, table bot_chats)
  GET  /v2/admin/connectors              which keys and services are set up — presence only, never a value
  GET  /v2/admin/push                    the last broadcast (live progress) and the history
  GET  /v2/admin/plan                    the growth plan the owner fills (installs, budgets, checklist, notes)
Write — each needs "confirm": true, else 400 confirm_required:
  POST /v2/admin/crm   {uid, note?, tags?: [...], confirm}
  POST /v2/admin/push  {segment, title, body, dry_run: true} → how many people and devices, nothing sent;
                       {segment, title, body, confirm: true} → sends in the background (GET /v2/admin/push shows the result)
  POST /v2/admin/plan  {data: {...}, confirm}
  POST /v2/admin/nova  {system, user, max_tokens} → the closed system's NOVA chat through the server's model key (handled in
                       server.py: Api._panel_nova, the same token check)
Segments: all · lang:<ru|en|uk|el|es> · verified (BingX UID confirmed) · uid_pending (UID sent, not confirmed) · inactive7 (no
visit for 7 days) · push_only · tag:<CRM tag>. A broadcast reaches only people whose «Новости» switch is on; it is refused during
the quiet hours 22:00–09:00 Kyiv (the engine's own pushes keep the same rule) and when the same text went to the same segment in
the last 30 minutes (a double click)."""
from __future__ import annotations

import collections
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
import time

try:
    from zoneinfo import ZoneInfo
    KYIV = ZoneInfo("Europe/Kyiv")
except Exception:  # noqa: BLE001
    KYIV = dt.timezone(dt.timedelta(hours=3))

ORIGIN = "https://cryptonova.vip"
MIN_TOKEN = 24
FAIL_MAX, FAIL_WINDOW = 20, 600
DAY_MS = 86_400_000
QUIET = (22, 9)
DUP_WINDOW_MS = 30 * 60_000
LANGS = ("ru", "en", "uk", "el", "es")
SEG_FIXED = ("all", "verified", "uid_pending", "inactive7", "push_only")
TITLE_MAX, BODY_MAX = 60, 180
NOTE_MAX, TAG_MAX, TAGS_MAX = 2000, 24, 12
PLAN_MAX = 16_000
META_APP_ID = "1635827251297802"     # public: it is inside every copy of the app (Android strings.xml, iOS Config.xcconfig)


def origin() -> str:
    return os.environ.get("CNQ_ADMIN_ORIGIN") or ORIGIN


def _has(name: str) -> bool:
    return bool((os.environ.get(name) or "").strip())


def _dkey(ms: int | None) -> str | None:
    if not ms:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).date().isoformat()


def _days(n: int, now_ms: int) -> list[str]:
    d0 = dt.datetime.fromtimestamp(now_ms / 1000, dt.timezone.utc).date()
    return [(d0 - dt.timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def quiet_now(now_ms: int | None = None) -> bool:
    h = dt.datetime.fromtimestamp((now_ms or time.time() * 1000) / 1000, KYIV).hour
    return h >= QUIET[0] or h < QUIET[1]


def _int(v, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _num(x) -> int | None:
    try:
        return None if x is None or str(x).strip() == "" else max(0, int(float(str(x).replace(" ", "").replace(",", "."))))
    except ValueError:
        return None


def _clean(s, n: int) -> str:
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(s or ""))
    return s.strip()[:n]


class Console:
    def __init__(self, store, web_dir: str, leads_db: str | None = None, fcm=None, background: bool = True):
        self.store, self.web = store, web_dir
        self.leads_db = leads_db
        self._fcm = fcm
        self.background = background            # tests send in the request thread
        self.fails: dict[str, list[float]] = {}
        self.lock = threading.Lock()
        self.sending = threading.Lock()
        self.last_push: dict | None = None
        self.access = None                      # 22.10: guest access codes (cnq/api/access.py), set by the API
        self.owner_auth = None                  # 28.10: the owner's sign-in (cnq/api/owner_auth.py), set by the API

    # ------------------------------------------------------------ access
    def check(self, headers: dict) -> tuple[int, dict] | None:
        """None when the caller is the owner; else (code, answer). Never prints or returns the token.
        28.10: an owner session from the panel's sign-in («Bearer cno_…», cnq/api/owner_auth.py) is accepted as well as
        CNQ_ADMIN_TOKEN — the token stays for scripts and as the fallback until the owner switches."""
        got = str(headers.get("authorization") or "")
        got = got[7:].strip() if got[:7].lower() == "bearer " else ""
        if self.owner_auth is not None and got.startswith("cno_"):
            ip = str(headers.get("x-real-ip") or headers.get("x-forwarded-for") or "local").split(",")[0].strip()[:64]
            now = time.time()
            with self.lock:
                recent = [t for t in self.fails.get(ip, []) if now - t < FAIL_WINDOW]
                self.fails[ip] = recent
                if len(recent) >= FAIL_MAX:
                    return 429, {"error": "too_many_attempts", "retry_after_sec": int(FAIL_WINDOW - (now - recent[0])) + 1}
            if self.owner_auth.session(headers):
                return None
            with self.lock:
                self.fails.setdefault(ip, []).append(now)
            return 401, {"error": "token_expired"}
        want = (os.environ.get("CNQ_ADMIN_TOKEN") or "").strip()
        if len(want) < MIN_TOKEN:
            return 403, {"error": "admin_disabled"}
        ip = str(headers.get("x-real-ip") or headers.get("x-forwarded-for") or "local").split(",")[0].strip()[:64]
        now = time.time()
        with self.lock:
            recent = [t for t in self.fails.get(ip, []) if now - t < FAIL_WINDOW]
            self.fails[ip] = recent
            if len(recent) >= FAIL_MAX:
                return 429, {"error": "too_many_attempts", "retry_after_sec": int(FAIL_WINDOW - (now - recent[0])) + 1}
        got = str(headers.get("authorization") or "")
        got = got[7:].strip() if got[:7].lower() == "bearer " else ""
        if got and hmac.compare_digest(hashlib.sha256(got.encode()).digest(), hashlib.sha256(want.encode()).digest()):
            return None
        with self.lock:
            self.fails.setdefault(ip, []).append(now)
        return 401, {"error": "unauthorized"}

    # ------------------------------------------------------------ router
    def handle(self, method: str, path: str, query: str, headers: dict, body: bytes) -> tuple[int, dict]:
        deny = self.check(headers)
        if deny:
            return deny
        qs = {k: v[-1] for k, v in _parse_qs(query).items()}
        if method == "GET":
            if path == "/v2/admin/ping":
                return 200, {"ok": True, "now": int(time.time() * 1000)}
            if path == "/v2/admin/overview":
                return 200, self.overview()
            if path == "/v2/admin/users":
                return 200, self.users(qs)
            m = re.fullmatch(r"/v2/admin/user/([^/]{1,128})", path)
            if m:
                card = self.user_card(_unquote(m.group(1)))
                return (200, card) if card else (404, {"error": "no_such_user"})
            if path == "/v2/admin/leads":
                return 200, self.leads(qs)
            if path == "/v2/admin/connectors":
                return 200, self.connectors()
            if path == "/v2/admin/push":
                return 200, self.push_state()
            if path == "/v2/admin/plan":
                return 200, {"plan": self._kv("plan") or {}}
            if path == "/v2/admin/access" and self.access is not None:
                return 200, {"codes": self.access.list()}
            return 404, {"error": "not_found"}
        if method != "POST":
            return 405, {"error": "method"}
        try:
            data = json.loads(body.decode() or "{}") if body else {}
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            return 400, {"error": "bad_json"}
        if path == "/v2/admin/push":
            return self.push(data)
        if data.get("confirm") is not True:
            return 400, {"error": "confirm_required"}
        if path == "/v2/admin/crm":
            return self.crm(data)
        if path == "/v2/admin/access" and self.access is not None:    # {op: add|remove, label, days?, level?, confirm}
            op, label = str(data.get("op") or ""), _clean(data.get("label"), 60)
            if op == "add":
                days = _num(data.get("days"))
                code = self.access.add(label or "гость", str(data.get("level") or "tests"), days or None)
                self._log("access_add", {"label": label, "days": days})
                return 200, {"code": code, "label": label or "гость", "days": days}      # shown once, never stored in plain text
            if op == "remove" and label:
                n = self.access.remove(label)
                self._log("access_remove", {"label": label, "n": n})
                return 200, {"removed": n}
            return 400, {"error": "bad_op"}
        if path == "/v2/admin/plan":
            plan = data.get("data")
            if not isinstance(plan, dict) or len(json.dumps(plan, ensure_ascii=False)) > PLAN_MAX:
                return 400, {"error": "bad_plan"}
            self._set_kv("plan", plan)
            self._log("plan", {"keys": sorted(plan)[:30]})
            return 200, {"plan": plan}
        return 404, {"error": "not_found"}

    # ------------------------------------------------------------ helpers
    def _q(self, sql: str, args: tuple = ()) -> list:
        return self.store._q(sql, args)

    def _kv(self, k: str):
        r = self._q("SELECT v FROM admin_kv WHERE k=?", (k,))
        try:
            return json.loads(r[0][0]) if r else None
        except ValueError:
            return None

    def _set_kv(self, k: str, v) -> None:
        self._q("INSERT OR REPLACE INTO admin_kv VALUES(?,?,?)", (k, json.dumps(v, ensure_ascii=False), int(time.time() * 1000)))

    def _log(self, action: str, detail: dict) -> None:
        self._q("INSERT INTO admin_log VALUES(?,?,?)", (int(time.time() * 1000), action, json.dumps(detail, ensure_ascii=False)))

    def _users(self) -> list[dict]:
        keys = ("uid", "email", "name", "created", "seen", "tier", "vip_since", "bingx_uid", "lang")
        return [dict(zip(keys, r)) for r in self._q(f"SELECT {', '.join(keys)} FROM users")]

    def _tags(self) -> dict[str, list[str]]:
        rows = self._q("SELECT uid, tags FROM crm c WHERE tags IS NOT NULL AND t = "
                       "(SELECT MAX(t) FROM crm WHERE uid = c.uid AND tags IS NOT NULL)")
        out = {}
        for uid, tags in rows:
            try:
                out[uid] = [str(x) for x in json.loads(tags)]
            except ValueError:
                pass
        return out

    def _devices(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = collections.defaultdict(list)
        for uid, plat in self._q("SELECT uid, platform FROM push"):
            out[uid].append(plat or "?")
        return out

    def _lessons(self) -> dict[str, tuple[int, int]]:
        out = {}
        for uid, data in self._q("SELECT uid, data FROM progress"):
            try:
                d = json.loads(data or "{}")
            except ValueError:
                continue
            les = d.get("lessons") if isinstance(d.get("lessons"), dict) else {}
            out[uid] = (sum(1 for v in les.values() if isinstance(v, dict) and v.get("done")), int(d.get("xp") or 0))
        return out

    def _piggies(self) -> dict[str, dict]:
        out = {}
        for uid, data in self._q("SELECT uid, data FROM piggy"):
            try:
                out[uid] = json.loads(data or "{}")
            except ValueError:
                pass
        return out

    def _nova(self, since_day: str) -> dict[str, tuple[int, int]]:
        out = {}
        for uid, total, recent in self._q("SELECT uid, SUM(n), SUM(CASE WHEN day >= ? THEN n ELSE 0 END) FROM usage GROUP BY uid",
                                          (since_day,)):
            out[uid] = (int(total or 0), int(recent or 0))
        return out

    def _checks(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for uid, bx, t, ok, detail in self._q("SELECT uid, bingx_uid, t, ok, detail FROM vip_checks ORDER BY t"):
            try:
                reason = (json.loads(detail or "{}") or {}).get("reason")
            except ValueError:
                reason = None
            prev = out.get(uid)
            out[uid] = {"t": t, "ok": bool(ok), "reason": reason, "bingx_uid": bx, "n": (prev["n"] + 1) if prev else 1}
        return out

    @staticmethod
    def _bingx_status(u: dict, chk: dict | None) -> str:
        if u.get("tier") == "vip":
            return "verified"
        if u.get("bingx_uid") or chk:
            return "pending"
        return "none"

    def _lessons_total(self) -> int | None:
        try:
            with open(os.path.join(self.web, "academy.json"), encoding="utf-8") as f:
                d = json.load(f)
            return sum(len(lv.get("lessons") or []) for lv in d.get("levels") or [])
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------ overview
    def overview(self) -> dict:
        now = int(time.time() * 1000)
        days = _days(30, now)
        users = self._users()
        new = collections.Counter(_dkey(u["created"]) for u in users)
        act = lambda d: sum(1 for u in users if (u.get("seen") or 0) >= now - d * DAY_MS)  # noqa: E731
        langs = collections.Counter((u.get("lang") or "—") for u in users)
        dev = self._devices()
        plats = collections.Counter(p for ps in dev.values() for p in ps)
        nova_rows = self._q("SELECT day, SUM(n), COUNT(DISTINCT uid) FROM usage WHERE day >= ? GROUP BY day", (days[0],))
        nova_day = {d: int(n or 0) for d, n, _ in nova_rows}
        nova_users = self._q("SELECT COUNT(DISTINCT uid) FROM usage WHERE day >= ?", (days[0],))[0][0] or 0
        lessons = self._lessons()
        total_lessons = self._lessons_total()
        buckets = [("0", 0, 0), ("1–4", 1, 4), ("5–9", 5, 9), ("10–19", 10, 19), ("20–49", 20, 49), ("50+", 50, 10 ** 6)]
        dist = []
        for label, lo, hi in buckets:
            n = sum(1 for u in users if lo <= (lessons.get(u["uid"], (0, 0))[0]) <= hi)
            dist.append({"label": label, "n": n})
        pig = self._piggies()
        pig_on = [p for p in pig.values() if p.get("on", True)]
        checks = self._checks()
        submitted = sum(1 for u in users if self._bingx_status(u, checks.get(u["uid"])) != "none")
        verified = sum(1 for u in users if u.get("tier") == "vip")
        plan = self._kv("plan") or {}
        inst = [n for n in (_num(plan.get("installs_ios")), _num(plan.get("installs_android"))) if n is not None]
        inst_n = sum(inst) if inst else None
        home = None
        try:
            with open(os.path.join(self.web, "app_home.json"), encoding="utf-8") as f:
                asof = json.load(f).get("asof")
            home = round(time.time() - asof / 1000) if asof else None
        except Exception:  # noqa: BLE001
            pass
        return {
            "now": now, "days": days,
            "users": {"total": len(users), "new_per_day": [new.get(d, 0) for d in days], "new_30d": sum(new.get(d, 0) for d in days),
                      "active_1d": act(1), "active_7d": act(7), "active_30d": act(30),
                      "by_lang": dict(langs.most_common())},
            "push": {"users": len(dev), "devices": sum(len(v) for v in dev.values()), "by_platform": dict(plats)},
            "nova": {"per_day": [nova_day.get(d, 0) for d in days], "total_30d": sum(nova_day.values()), "users_30d": int(nova_users)},
            "lessons": {"total": total_lessons, "dist": dist, "started": sum(1 for v in lessons.values() if v[0] > 0)},
            "piggy": {"users": len(pig), "on": len(pig_on),
                      "by_goal": dict(collections.Counter(str(p.get("goal") or "—") for p in pig_on)),
                      "by_freq": dict(collections.Counter(str(p.get("freq") or "—") for p in pig_on))},
            "bingx": {"submitted": submitted, "verified": verified},
            "funnel": [{"k": "install", "n": inst_n, "src": "owner"}, {"k": "signup", "n": len(users)},
                       {"k": "uid", "n": submitted}, {"k": "verified", "n": verified}],
            "referrals": self.referrals(now, days),
            "engine": {"home_age_sec": home},
        }

    def referrals(self, now: int, days: list[str]) -> dict:
        rows = self._q("SELECT uid, bingx_uid, t, ok, detail FROM vip_checks")
        reasons: collections.Counter = collections.Counter()
        per_day: collections.Counter = collections.Counter()
        ok_day: collections.Counter = collections.Counter()
        for _uid, _bx, t, ok, detail in rows:
            try:
                r = (json.loads(detail or "{}") or {}).get("reason") or ("ok" if ok else "other")
            except ValueError:
                r = "other"
            reasons[r] += 1
            per_day[_dkey(t)] += 1
            if ok:
                ok_day[_dkey(t)] += 1
        cfg = {}
        try:
            with open(os.path.join(self.web, "app_config.json"), encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:  # noqa: BLE001
            pass
        return {"checks": len(rows), "people": len({r[0] for r in rows}), "bingx_uids": len({r[1] for r in rows}),
                "confirmed": len({r[0] for r in rows if r[3]}), "reasons": dict(reasons),
                "per_day": [per_day.get(d, 0) for d in days], "ok_per_day": [ok_day.get(d, 0) for d in days],
                "exchange_ref_set": bool(cfg.get("exchange_ref")), "exchange_name": cfg.get("exchange_name")}

    # ------------------------------------------------------------ users
    def users(self, qs: dict) -> dict:
        now = int(time.time() * 1000)
        q = _clean(qs.get("q"), 80).lower()
        seg = _clean(qs.get("seg"), 40) or "all"
        sort = qs.get("sort") if qs.get("sort") in ("created", "seen", "nova", "lessons") else "created"
        page, size = _int(qs.get("page"), 1, 1, 10 ** 6), _int(qs.get("size"), 50, 1, 100)
        users = self._users()
        tags, dev, les, pig = self._tags(), self._devices(), self._lessons(), self._piggies()
        nova = self._nova(_days(30, now)[0])
        checks = self._checks()
        if q:
            users = [u for u in users if q in (u.get("email") or "").lower() or q in (u.get("name") or "").lower()
                     or q == (u.get("uid") or "").lower() or q in (u.get("bingx_uid") or "")]
        users = [u for u in users if self._in_segment(u, seg, now, tags, dev, checks)]
        key = {"created": lambda u: u.get("created") or 0, "seen": lambda u: u.get("seen") or 0,
               "nova": lambda u: nova.get(u["uid"], (0, 0))[0], "lessons": lambda u: les.get(u["uid"], (0, 0))[0]}[sort]
        users.sort(key=key, reverse=True)
        total = len(users)
        rows = []
        for u in users[(page - 1) * size: page * size]:
            uid = u["uid"]
            p = pig.get(uid)
            rows.append({**{k: u.get(k) for k in ("uid", "email", "name", "created", "seen", "lang", "tier", "bingx_uid")},
                         "lessons": les.get(uid, (0, 0))[0], "xp": les.get(uid, (0, 0))[1],
                         "piggy": ({"on": bool(p.get("on", True)), "amount": p.get("amount"), "freq": p.get("freq")} if p else None),
                         "nova_total": nova.get(uid, (0, 0))[0], "nova_30d": nova.get(uid, (0, 0))[1],
                         "push": len(dev.get(uid, [])), "bingx": self._bingx_status(u, checks.get(uid)),
                         "tags": tags.get(uid, [])})
        return {"total": total, "page": page, "size": size, "pages": max(1, -(-total // size)), "rows": rows,
                "seg": seg, "sort": sort, "q": q}

    def _in_segment(self, u: dict, seg: str, now: int, tags: dict, dev: dict, checks: dict) -> bool:
        if seg == "all":
            return True
        if seg.startswith("lang:"):
            return (u.get("lang") or "ru") == seg[5:]
        if seg == "verified":
            return u.get("tier") == "vip"
        if seg == "uid_pending":
            return self._bingx_status(u, checks.get(u["uid"])) == "pending"
        if seg == "inactive7":
            return (u.get("seen") or 0) < now - 7 * DAY_MS
        if seg == "push_only":
            return bool(dev.get(u["uid"]))
        if seg.startswith("tag:"):
            return seg[4:] in tags.get(u["uid"], [])
        return False

    def user_card(self, uid: str) -> dict | None:
        u = self.store.user(uid)
        if not u:
            return None
        now = int(time.time() * 1000)
        days = _days(30, now)
        prog = self.store.progress(uid) or {}
        les = prog.get("lessons") if isinstance(prog.get("lessons"), dict) else {}
        nova = {d: int(n) for d, n in self._q("SELECT day, n FROM usage WHERE uid=? AND day >= ?", (uid, days[0]))}
        nova_all = self._q("SELECT SUM(n) FROM usage WHERE uid=?", (uid,))[0][0] or 0
        devices = [{"platform": p, "updated": t} for p, t in self._q("SELECT platform, updated FROM push WHERE uid=?", (uid,))]
        checks = []
        for bx, t, ok, detail in self._q("SELECT bingx_uid, t, ok, detail FROM vip_checks WHERE uid=? ORDER BY t DESC", (uid,)):
            try:
                d = json.loads(detail or "{}") or {}
            except ValueError:
                d = {}
            checks.append({"bingx_uid": bx, "t": t, "ok": bool(ok), "reason": d.get("reason"),
                           "invited": d.get("invited"), "deposited": d.get("deposited")})
        notes = []
        for t, note, tags in self._q("SELECT t, note, tags FROM crm WHERE uid=? ORDER BY t DESC", (uid,)):
            notes.append({"t": t, "note": note, "tags": json.loads(tags) if tags else None})
        return {"user": u, "prefs": self.store.prefs(uid), "piggy": self.store.piggy(uid),
                "lessons": {"done": sorted(k for k, v in les.items() if isinstance(v, dict) and v.get("done")),
                            "total": self._lessons_total(), "xp": int(prog.get("xp") or 0)},
                "nova": {"days": days, "per_day": [nova.get(d, 0) for d in days], "total": int(nova_all)},
                "devices": devices, "checks": checks, "bingx": self._bingx_status(u, checks[0] if checks else None),
                "crm": {"tags": self._tags().get(uid, []), "notes": notes}}

    def crm(self, data: dict) -> tuple[int, dict]:
        uid = _clean(data.get("uid"), 128)
        if not uid or not self.store.user(uid):
            return 404, {"error": "no_such_user"}
        note = _clean(data.get("note"), NOTE_MAX) or None
        tags = None
        if data.get("tags") is not None:
            if not isinstance(data["tags"], list):
                return 400, {"error": "bad_tags"}
            seen: list[str] = []
            for t in data["tags"][:TAGS_MAX]:
                t = _clean(t, TAG_MAX)
                if t and t not in seen:
                    seen.append(t)
            tags = json.dumps(seen, ensure_ascii=False)
        if note is None and tags is None:
            return 400, {"error": "empty"}
        self._q("INSERT INTO crm VALUES(?,?,?,?)", (uid, int(time.time() * 1000), note, tags))
        self._log("crm", {"uid": uid, "note": bool(note), "tags": tags is not None})
        card = self.user_card(uid)
        return 200, {"crm": card["crm"] if card else None}

    # ------------------------------------------------------------ Telegram bot leads
    def _leads_path(self) -> str:
        return self.leads_db or os.path.join(os.environ.get("CNQ_RUN_DIR", "."), "data", "agents.sqlite")

    def leads(self, qs: dict) -> dict:
        path = self._leads_path()
        if not os.path.exists(path):
            return {"available": False, "total": 0, "rows": []}
        now = int(time.time() * 1000)
        days = _days(30, now)
        q = _clean(qs.get("q"), 64).lower().lstrip("@")
        page, size = _int(qs.get("page"), 1, 1, 10 ** 6), _int(qs.get("size"), 50, 1, 100)
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            try:
                rows = con.execute("SELECT chat_id, username, first_name, first_seen, last_seen, n_msgs FROM bot_chats").fetchall()
                try:
                    channel = bool(con.execute("SELECT v FROM bot_meta WHERE k='signals_chat'").fetchone())
                except sqlite3.Error:
                    channel = False
            finally:
                con.close()
        except sqlite3.Error:
            return {"available": False, "total": 0, "rows": [], "error": "db"}
        keys = ("chat_id", "username", "first_name", "first_seen", "last_seen", "n_msgs")
        all_rows = [dict(zip(keys, r)) for r in rows]
        new = collections.Counter(_dkey(r["first_seen"]) for r in all_rows)
        found = [r for r in all_rows if not q or q in (r["username"] or "").lower() or q in (r["first_name"] or "").lower()
                 or q == str(r["chat_id"])]
        found.sort(key=lambda r: r["last_seen"] or 0, reverse=True)
        return {"available": True, "total": len(all_rows), "found": len(found), "page": page, "size": size,
                "pages": max(1, -(-len(found) // size)), "rows": found[(page - 1) * size: page * size],
                "days": days, "new_per_day": [new.get(d, 0) for d in days],
                "active_7d": sum(1 for r in all_rows if (r["last_seen"] or 0) >= now - 7 * DAY_MS),
                "messages": sum(int(r["n_msgs"] or 0) for r in all_rows), "signals_channel": channel}

    # ------------------------------------------------------------ connectors (presence only)
    def connectors(self) -> dict:
        fcm = self._fcm_get()
        cfg = {}
        try:
            with open(os.path.join(self.web, "app_config.json"), encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:  # noqa: BLE001
            pass
        prov = (os.environ.get("CNQ_NOVA_LLM_PROVIDER") or os.environ.get("CNQ_LLM_PROVIDER") or "groq").lower()
        tokens = self._q("SELECT COUNT(*) FROM push")[0][0]
        leads_ok = os.path.exists(self._leads_path())
        ref = cfg.get("exchange_ref")
        items = [
            {"id": "admin", "ok": True, "env": ["CNQ_ADMIN_TOKEN"]},
            {"id": "firebase", "ok": bool(getattr(fcm, "ready", False)), "env": ["CNQ_FCM_SA"],
             "detail": {"project": os.environ.get("CNQ_FIREBASE_PROJECT", "crypto-nova")}},
            {"id": "push", "ok": bool(getattr(fcm, "ready", False)), "env": ["CNQ_FCM_SA"], "detail": {"devices": tokens}},
            {"id": "bingx_partner", "ok": _has("CNQ_BINGX_AGENT_KEY") and _has("CNQ_BINGX_AGENT_SECRET"),
             "env": ["CNQ_BINGX_AGENT_KEY", "CNQ_BINGX_AGENT_SECRET"]},
            {"id": "exchange_ref", "ok": bool(ref), "file": "web/app_config.json",
             "detail": {"exchange": cfg.get("exchange_name"), "link": ref if isinstance(ref, str) else None}},
            {"id": "nova", "ok": _has("CNQ_NOVA_LLM_KEY") or _has("CNQ_LLM_KEY"), "env": ["CNQ_NOVA_LLM_KEY", "CNQ_LLM_KEY"],
             "detail": {"own_key": _has("CNQ_NOVA_LLM_KEY"), "provider": prov}},
            {"id": "telegram", "ok": _has("CNQ_TG_TOKEN"), "env": ["CNQ_TG_TOKEN", "CNQ_TG_CHAT", "CNQ_SIGNALS_CHAT"],
             "detail": {"bot": _has("CNQ_TG_TOKEN"), "news_channel": _has("CNQ_TG_CHAT"), "signals_channel": _has("CNQ_SIGNALS_CHAT"),
                        "leads_db": leads_ok}},
            {"id": "meta", "ok": True, "detail": {
                "app_id": META_APP_ID,
                "sends": ["install", "app_open"],          # Meta SDK automatic events (FacebookAutoLogAppEventsEnabled = true)
                "missing": ["registration"]}},            # 2.0 sign-in (V2Store.signIn) does not call AppAnalytics.registration yet
            {"id": "appstore", "ok": None},
            {"id": "play", "ok": None},
        ]
        return {"items": items, "checked": int(time.time() * 1000)}

    # ------------------------------------------------------------ segment push
    def _fcm_get(self):
        if self._fcm is None:
            from .push import Fcm
            self._fcm = Fcm()
        return self._fcm

    def _segment_uids(self, seg: str) -> set[str] | None:
        if seg not in SEG_FIXED and not (seg.startswith("lang:") and seg[5:] in LANGS) \
                and not (seg.startswith("tag:") and 0 < len(seg) - 4 <= TAG_MAX):
            return None
        now = int(time.time() * 1000)
        tags, dev, checks = self._tags(), self._devices(), self._checks()
        return {u["uid"] for u in self._users() if self._in_segment(u, seg, now, tags, dev, checks)}

    def push(self, data: dict) -> tuple[int, dict]:
        seg = _clean(data.get("segment"), 40) or "all"
        title, body = _clean(data.get("title"), TITLE_MAX + 1), _clean(data.get("body"), BODY_MAX + 1)
        uids = self._segment_uids(seg)
        if uids is None:
            return 400, {"error": "bad_segment"}
        if not title or not body or len(title) > TITLE_MAX or len(body) > BODY_MAX:
            return 400, {"error": "bad_text", "title_max": TITLE_MAX, "body_max": BODY_MAX}
        targets = self.store.push_tokens(uids=uids, pref="news")
        fcm = self._fcm_get()
        info = {"segment": seg, "people_in_segment": len(uids), "people": len({u for u, _ in targets}), "devices": len(targets),
                "news_off": len(uids & {u for u, _ in self.store.push_tokens(uids=uids)}) - len({u for u, _ in targets}),
                "quiet_now": quiet_now(), "fcm_ready": bool(getattr(fcm, "ready", False))}
        if data.get("dry_run") is True and data.get("confirm") is not True:
            return 200, {"dry_run": True, **info}
        if data.get("confirm") is not True:
            return 400, {"error": "confirm_required", **info}
        if not info["fcm_ready"]:
            return 503, {"error": "push_not_configured", **info}
        if info["quiet_now"]:
            return 409, {"error": "quiet_hours", **info}
        if not targets:
            return 409, {"error": "nobody", **info}
        sig = hashlib.sha256(f"{seg}\n{title}\n{body}".encode()).hexdigest()[:16]
        since = int(time.time() * 1000) - DUP_WINDOW_MS
        for (detail,) in self._q("SELECT detail FROM admin_log WHERE action='push' AND t >= ?", (since,)):
            try:
                if json.loads(detail).get("sig") == sig:
                    return 409, {"error": "duplicate", **info}
            except ValueError:
                pass
        if not self.sending.acquire(blocking=False):
            return 409, {"error": "busy", **info}
        entry = {"t": int(time.time() * 1000), "segment": seg, "title": title, "body": body, "sig": sig,
                 "people": info["people"], "devices": info["devices"], "done": False, "results": {}}
        self.last_push = entry
        self._log("push", {k: entry[k] for k in ("segment", "title", "body", "sig", "people", "devices")})
        if self.background:
            threading.Thread(target=self._send_all, args=(fcm, targets, entry), daemon=True).start()
        else:
            self._send_all(fcm, targets, entry)
        return 200, {"started": True, **info, "push": entry}

    def _send_all(self, fcm, targets: list, entry: dict) -> None:
        res: collections.Counter = collections.Counter()
        try:
            for _uid, tok in targets:
                try:
                    r = fcm.send(tok, entry["title"], entry["body"], {"open": "home", "kind": "admin"})
                except Exception:  # noqa: BLE001
                    r = "error"
                res[r] += 1
                entry["results"] = dict(res)
                if r == "gone":
                    self.store.drop_push(tok)
        finally:
            entry["done"], entry["finished"] = True, int(time.time() * 1000)
            self._log("push_done", {"sig": entry["sig"], "results": dict(res)})
            self.sending.release()

    def push_state(self) -> dict:
        hist, done = [], {}
        for t, action, detail in self._q("SELECT t, action, detail FROM admin_log WHERE action IN ('push', 'push_done') "
                                         "ORDER BY t DESC LIMIT 100"):
            try:
                d = json.loads(detail)
            except ValueError:
                continue
            if action == "push_done":
                done[d.get("sig")] = d.get("results")
            else:
                hist.append({"t": t, **{k: d.get(k) for k in ("segment", "title", "body", "people", "devices")},
                             "results": done.get(d.get("sig"))})
        return {"last": self.last_push, "history": hist[:30], "quiet_now": quiet_now(),
                "fcm_ready": bool(getattr(self._fcm_get(), "ready", False))}


def _parse_qs(query: str) -> dict:
    from urllib.parse import parse_qs
    return parse_qs(query or "", keep_blank_values=False)


def _unquote(s: str) -> str:
    from urllib.parse import unquote
    return unquote(s)
