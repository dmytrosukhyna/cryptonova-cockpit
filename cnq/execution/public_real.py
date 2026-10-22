"""«Реальный счёт CRYPTO NOVA на OKX» (22.10.2026, owner: «Публичный портфель» = the real OKX account since 22.10 on top, the system
since 17.10 (demo) below it). The mirror (scripts/mirror_mac.sh → mirror.py) publishes an UNENCRYPTED summary of the SUB-ACCOUNT
ONLY — the autopilot's account, read with the autopilot's key through myspot_sub (GET only) — to web/public_real.json, at most
every 15 minutes. The owner's personal (main) account never goes there: this module does not know the MYSPOT_* key.

  {"exchange": "OKX", "since": ms, "start_usd", "equity_usd", "pnl_usd", "pnl_pct" (percent), "today_usd", "today_pct",
   "spot": [{"sym", "share" (% of the account), "usd"}], "usdt_pct", "usdt_usd",
   "futures": [{"inst", "side": "long" | "short", "entry", "stop", "target", "upnl_pct" (price move, %), "upnl_usd"}],
   "closed_n" (futures positions closed since the start, OKX's own history), "curve": [[ms, $ result since the start]] (≤ 1 point
   per 15 min, compact), "asof": ms, "stale": bool}
USDT = trading + funding (no Earn: the account is the system 1:1). No key, IP, UID or order id is ever written. No fake numbers:
when the autopilot is not running (no live holder of its lease, or the global stop) the numbers are real but "stale": true; when
the account can not be read, the last published numbers stay, with "stale": true and their old asof (readers also treat an asof
older than 2 hours as stale). today_* are null until a day-start value exists. On by default when OKX_API_* exist; OKX_PUBLIC=off
turns it off. Local state (the curve): ~/.cryptonova_public_real.json. Not financial advice. Stdlib only."""
from __future__ import annotations

import base64
import json
import os
import time

from . import ivan_watch
from . import myspot_sub as sub
from . import myspot_watch as ms

PATH = "web/public_real.json"
KEYS = ("exchange", "since", "start_usd", "equity_usd", "pnl_usd", "pnl_pct", "today_usd", "today_pct", "spot", "usdt_pct", "usdt_usd",
        "futures", "closed_n", "curve", "asof", "stale")
SPOT_KEYS, FUT_KEYS = ("sym", "share", "usd"), ("inst", "side", "entry", "stop", "target", "upnl_pct", "upnl_usd")
EVERY_S = 15 * 60
STEP_MS = 15 * 60_000
STALE_MS = 2 * 3_600_000
DAY_MS = 86_400_000
CURVE_MAX, CURVE_RECENT = 480, 192            # ≤ 480 points: the last 2 days every 15 min, older ones one per 6 hours
HISTORY = "/api/v5/account/positions-history"


def _env(env=None) -> dict:
    return os.environ if env is None else env


def enabled(env=None) -> bool:
    return bool(sub.creds(env)) and (_env(env).get("OKX_PUBLIC") or "on").strip().lower() not in ("off", "no", "0", "false")


def state_path(env=None) -> str:
    return os.path.expanduser(_env(env).get("OKX_PUBLIC_STATE") or "~/.cryptonova_public_real.json")


def load_state(env=None) -> dict:
    try:
        with open(state_path(env), encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save_state(st: dict, env=None) -> None:
    p = state_path(env)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(st, f, separators=(",", ":"))
    os.replace(p + ".tmp", p)


def _r(v, n: int = 2):
    return None if v is None else round(float(v), n)


def closed_count(fetch, host: str, since: int, pages: int = 10) -> int | None:
    """Futures positions closed since the start — OKX's own positions history (GET, 100 a page). None when unreadable."""
    n, after = 0, None
    try:
        for _ in range(pages):
            d = ms._call(fetch, host, HISTORY, {"instType": "SWAP", "limit": "100", **({"after": after} if after else {})})
            older = False
            for h in d:
                ut = int(h.get("uTime") or 0)
                if ut >= since:
                    n += 1
                else:
                    older = True
            if len(d) < 100 or older:
                break
            after = str(min(int(h.get("uTime") or 0) for h in d))
    except Exception:  # noqa: BLE001
        return None
    return n


def compact(curve: list, max_n: int = CURVE_MAX, recent: int = CURVE_RECENT) -> list:
    if len(curve) <= max_n:
        return curve
    old, new = curve[:-recent], curve[-recent:]
    out, last = [], None
    for p in old:
        if last is None or p[0] - last >= 6 * 3_600_000:
            out.append(p)
            last = p[0]
    return (out + new)[-max_n:]


def running(lease: dict | None) -> bool:
    """The autopilot is trading this account right now: a live holder of its lease and no global stop. Unknown → False."""
    return bool(lease) and not lease.get("error") and bool(lease.get("holder")) and not lease.get("stop")


def build(s: dict, st: dict, now: int, closed_n: int | None = None) -> dict:
    """The public summary from myspot_sub.read() (an ok read). Updates st["curve"]."""
    start = s.get("start") or {}
    s0, since = float(start.get("usd") or sub.START_USD), int(start.get("t") or now)
    eq = float(s["total_usd"])
    pnl = eq - s0
    curve = [p for p in st.get("curve") or [] if isinstance(p, list) and len(p) == 2 and p[0] >= since]
    if not curve:
        curve = [[since, 0.0]]
    if now - curve[-1][0] >= STEP_MS:
        curve.append([now, round(pnl, 2)])
    st["curve"] = curve = compact(curve)
    day0 = now - now % DAY_MS
    ref = 0.0 if since >= day0 else next((v for t, v in reversed(curve) if t < day0), None)
    today = None if ref is None else pnl - ref
    spot = {}
    for r in list((s.get("spot") or {}).get("rows") or []) + list((s.get("active") or {}).get("rows") or []):
        if r.get("usd"):
            spot[r["ccy"]] = spot.get(r["ccy"], 0.0) + float(r["usd"])
    cash = float((s.get("cash") or {}).get("usd") or 0.0)
    fut = []
    for p in (s.get("futures") or {}).get("positions") or []:
        long = p.get("side") == "LONG"
        mv = ((p["mark"] / p["entry"] - 1) * (1 if long else -1) * 100) if p.get("mark") and p.get("entry") else None
        fut.append({"inst": p.get("inst"), "side": "long" if long else "short", "entry": p.get("entry"), "stop": p.get("stop"),
                    "target": p.get("tp"), "upnl_pct": _r(mv), "upnl_usd": _r(p.get("upl"))})
    return {"exchange": "OKX", "since": since, "start_usd": _r(s0), "equity_usd": _r(eq), "pnl_usd": _r(pnl),
            "pnl_pct": _r(pnl / s0 * 100 if s0 else None), "today_usd": _r(today),
            "today_pct": _r(today / (s0 + ref) * 100) if today is not None and (s0 + ref) else None,
            "spot": [{"sym": c, "share": _r(u / eq * 100 if eq else 0.0), "usd": _r(u)} for c, u in sorted(spot.items(), key=lambda x: -x[1])],
            "usdt_pct": _r(cash / eq * 100 if eq else 0.0), "usdt_usd": _r(cash), "futures": fut,
            "closed_n": int(closed_n or 0), "curve": [[int(t), _r(v)] for t, v in curve], "asof": now, "stale": not running(s.get("lease"))}


def clean(doc: dict) -> dict:
    """Exactly the public keys, nothing else at any level (refuses anything that looks like an id, a key or an address)."""
    if set(doc) != set(KEYS):
        raise ValueError("public_real: wrong keys")
    out = {k: doc[k] for k in KEYS}
    out["spot"] = [{k: x.get(k) for k in SPOT_KEYS} for x in doc.get("spot") or []]
    out["futures"] = [{k: x.get(k) for k in FUT_KEYS} for x in doc.get("futures") or []]
    return out


def publish(doc: dict, token: str, repo: str = ivan_watch.DEFAULT_REPO, http=None) -> bool:
    if not token:
        return False
    url = f"https://api.github.com/repos/{repo}/contents/{PATH}"
    hdr = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "User-Agent": "cnq-public-real"}
    body = json.dumps(clean(doc), ensure_ascii=False, separators=(",", ":"))
    return ivan_watch.put_contents(url, hdr, base64.b64encode(body.encode()).decode(), "public real: update", http=http)


def run(env=None, fetch=None, now: int | None = None, put=None, state: dict | None = None, save=None) -> dict | None:
    """read (GET only) → summary → publish when it changed. None when off. A failed read republishes the last numbers as stale."""
    if not enabled(env):
        return None
    e = _env(env)
    now = int(now or time.time() * 1000)
    if state is None:
        state = load_state(env)
        save = save or (lambda s_: save_state(s_, env))
    s = sub.read(env, fetch, now)
    if s and s.get("ok"):
        if fetch is None:
            fetch = ms.make_fetch(*sub.creds(env))
        doc = clean(build(s, state, now, closed_count(fetch, "https://" + s["host"], int(s["start"].get("t") or now))))
    elif state.get("last"):
        doc = {**state["last"], "stale": True}           # the last real numbers, their own asof — never invented ones
    else:
        return None
    if put is None:
        tok, repo = e.get("CNQ_GH_TOKEN", ""), e.get("CNQ_GH_REPO") or ivan_watch.DEFAULT_REPO
        put = (lambda d: publish(d, tok, repo)) if tok else None
    if doc != state.get("last") and put is not None:
        if not put(doc):
            raise RuntimeError("GitHub не принял web/public_real.json")
        state["last"] = doc
    if save:
        save(state)
    return doc
