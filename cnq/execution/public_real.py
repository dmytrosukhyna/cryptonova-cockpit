"""«Реальный счёт CRYPTO NOVA на OKX» (22.10.2026, owner: «Публичный портфель» = the real OKX account since 22.10 on top, the system
since 17.10 (demo) below it). The mirror (scripts/mirror_mac.sh → mirror.py) publishes an UNENCRYPTED summary of the SUB-ACCOUNT
ONLY — the autopilot's account, read with the autopilot's key through myspot_sub (GET only) — to web/public_real.json, at most
every 15 minutes. The owner's personal (main) account never goes there: this module does not know the MYSPOT_* key.

  {"exchange": "OKX", "since": ms, "start_usd", "equity_usd", "pnl_usd", "pnl_pct" (percent), "today_usd", "today_pct",
   "spot": [{"sym", "share" (% of the account), "usd", "copy", "copy_reason", "copy_pct"}], "usdt_pct", "usdt_usd",
   "alloc": where the dollars belong (myspot_sub.allocate) {"spot_coins_usd", "spot_cash_usd", "fut_budget_usd", "fut_margin_usd",
            "fut_reserve_usd", "active_coins_usd", "active_wait_usd", "other_usd", "spot_pct", "fut_pct", "active_pct"},
   "futures": [{"inst", "side": "long" | "short", "entry", "stop", "target", "upnl_pct" (price move, %), "upnl_usd", "copy",
                "copy_reason"}],
   copy: true = copied by the followers (spot: the coin is held as lead positions; futures: the position has lead sub-positions);
   copy_reason when false — okx_lead's codes: "usdc_only" (bought through USDC: OKX copies only USDT pairs), "not_supported" (not in
   OKX's copy list), "legacy" (a normal coin bought before the lead mode), "ordinary" (refused as a lead instrument / not a lead
   position / not a lead account); copy_pct = the lead share of the coin, %,
   "spot_pnl_usd" / "fut_pnl_usd": the result since the start split — futures = OKX's realized P/L of the swaps closed since the
   start (positions history, fees and funding included) + the open positions' P/L; spot (portfolio + active spot) = the rest of the
   account's result. null when the history can not be read,
   "closed_n" (futures positions closed since the start, OKX's own history), "curve": [[ms, $ result since the start]] (≤ 1 point
   per 15 min, compact), "asof": ms, "stale": bool,
   "copiers": how many people copy the account on OKX (lead trader; null otherwise), "copy_url": the lead trader's public OKX
   profile https://www.okx.com/copy-trading/account/{uniqueCode} (public by design; null when not a lead trader)}
28.10 (build 46, owner: the futures tab must show the real account's own trades, closed ones with their stats, and the spot tab its
own result by period): "closed": the futures positions closed since the start, OKX's own positions history, newest first, at most
CLOSED_MAX — [{"inst", "side", "open_px", "close_px", "move_pct" (the price move in the trade's direction, %), "pnl_usd" (OKX's
realizedPnl: fees and funding included), "lev", "opened", "closed", "partial" (OKX type 1 / 4: part of it may still be open)}]; "fut_curve": [[ms, $ futures result since the start]] at the
curve's own timestamps (from 28.10 on: points before it have no split — nothing is back-filled), so spot = curve − fut_curve; each
open futures position also has "mark" (OKX mark price), "lev" (the leverage OKX applies), "opened" (OKX cTime) and "sig" (the desk
trade it mirrors, «bal:<id>» — public in web/paper.json; null when not ours).
"actions": what the autopilot really did on this account since the start (its own logs on the writer's machine: spot portfolio
orders, active spot buys / sells, futures opens / closes, positions OKX closed at the stop or target), newest first, at most
ACTIONS_MAX, only filled orders — [{"t", "kind": spot_buy | spot_sell | active_buy | active_sell | fut_open | fut_close |
fut_reduce | fut_gone, "sym", "side" (futures: long | short), "usd" (spot: the filled amount), "px" (average fill price), "sig"}].
28.10 (owner): deposits, withdrawals and transfers between the main account and this sub-account are NOT a result. "flows":
{"in_usd", "out_usd", "net_usd", "n", "last_t", "events": [[ms, ± $]], "by": {kind: [n, $]}, "checked_t" (the last time both bill
lists were read — null: «0» is not confirmed)} — read from OKX's own bills
(funding: deposits, withdrawals, cancelled withdrawals, master ↔ sub transfers; trading: transfers that are not the account's own
funding ↔ trading moves), each valued in USDT at its time (USDT / USDC = 1, other coins at the 1-minute close then). Remembered in
the local state (a bill hash, never published), so bills older than OKX's windows still count. With F(t) = the flows up to t:
  result(t) = equity(t) − start − F(t)        (the published "curve", "pnl_usd", "today_usd"; the state keeps the raw equity − start)
  % = result ÷ (start + Σ flow × (now − t_flow) ÷ (now − since))       (Modified Dietz: a flow counts for the part of the period it
       was in the account — honest when money comes in late, and = result ÷ start when there are no flows; today: the same over the day)
No id, address or hash leaves this module; a failed read keeps the flows already known.
USDT = trading + funding (no Earn: the account is the system 1:1). No key, IP, UID or order id is ever written. No fake numbers:
when the autopilot is not running (no live holder of its lease, or the global stop) the numbers are real but "stale": true; when
the account can not be read, the last published numbers stay, with "stale": true and their old asof (readers also treat an asof
older than 2 hours as stale). today_* are null until a day-start value exists. On by default when OKX_API_* exist; OKX_PUBLIC=off
turns it off. Local state (the curve): ~/.cryptonova_public_real.json. One writer only (writer()): the Mac mirror while the
autopilot runs on the Mac, the server's autopilot service (okx_autopilot --loop with OKX_NODE=server) while it runs there.
Not financial advice. Stdlib only."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time

from . import ivan_watch
from . import myspot_sub as sub
from . import myspot_watch as ms

PATH = "web/public_real.json"
KEYS = ("exchange", "since", "start_usd", "equity_usd", "pnl_usd", "pnl_pct", "today_usd", "today_pct", "spot", "usdt_pct", "usdt_usd",
        "alloc", "futures", "closed_n", "curve", "asof", "stale", "copiers", "copy_url", "spot_pnl_usd", "fut_pnl_usd", "closed", "fut_curve",
        "actions", "flows")
SPOT_KEYS = ("sym", "share", "usd", "copy", "copy_reason", "copy_pct")
FUT_KEYS = ("inst", "side", "entry", "stop", "target", "upnl_pct", "upnl_usd", "copy", "copy_reason", "mark", "lev", "opened", "sig")
CLOSED_KEYS = ("inst", "side", "open_px", "close_px", "move_pct", "pnl_usd", "lev", "opened", "closed", "partial")
CLOSED_MAX = 50
ACTION_KEYS = ("t", "kind", "sym", "side", "usd", "px", "sig")
ACTION_KINDS = ("spot_buy", "spot_sell", "active_buy", "active_sell", "fut_open", "fut_close", "fut_reduce", "fut_gone",
                "active_park", "active_unpark")        # 28.10 (owner's decision): «Импульс BTC» between its trades in BTC
ACTIONS_MAX = 30
TAIL_BYTES = 512 * 1024
SYM_RE = re.compile(r"^[A-Z0-9]{1,20}$")
INST_RE = re.compile(r"^[A-Z0-9]{1,20}-(USDT|USDC|USD)-SWAP$")
SIG_RE = re.compile(r"^[a-z]{2,8}:[A-Za-z0-9._-]{1,64}$")
REASONS = ("usdc_only", "not_supported", "legacy", "ordinary")
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
    return closed_stats(fetch, host, since, pages)[0]


def closed_stats(fetch, host: str, since: int, pages: int = 10) -> tuple[int | None, float | None]:
    """(closed swaps since the start, their realized P/L in $ — realizedPnl: fees and funding included). (None, None) unreadable."""
    return closed_history(fetch, host, since, pages)[:2]


def _closed_row(h: dict) -> dict:
    """one closed swap of OKX's positions history in public words — no position or order id"""
    op, cp = ms._f(h.get("openAvgPx")), ms._f(h.get("closeAvgPx"))
    d = str(h.get("direction") or h.get("posSide") or "").lower()
    side = d if d in ("long", "short") else None
    move = ((cp / op - 1) * (1 if side == "long" else -1) * 100) if op and cp and side else None
    v = ms._f(h.get("realizedPnl"))
    return {"inst": str(h.get("instId") or ""), "side": side, "open_px": op, "close_px": cp, "move_pct": _r(move),
            "pnl_usd": _r(v if v is not None else ms._f(h.get("pnl"))), "lev": ms._f(h.get("lever")),
            "opened": int(ms._f(h.get("cTime")) or 0) or None, "closed": int(ms._f(h.get("uTime")) or 0) or None,
            "partial": str(h.get("type") or "") in ("1", "4")}        # OKX: 1 partly closed, 4 partly liquidated — the rest may be open


def closed_history(fetch, host: str, since: int, pages: int = 10) -> tuple[int | None, float | None, list]:
    """closed_stats + the closed swaps themselves (newest first, ≤ CLOSED_MAX). (None, None, []) when unreadable."""
    n, pnl, after, rows = 0, 0.0, None, []
    try:
        for _ in range(pages):
            d = ms._call(fetch, host, HISTORY, {"instType": "SWAP", "limit": "100", **({"after": after} if after else {})})
            older = False
            for h in d:
                ut = int(h.get("uTime") or 0)
                if ut >= since:
                    n += 1
                    v = ms._f(h.get("realizedPnl"))
                    pnl += v if v is not None else (ms._f(h.get("pnl")) or 0.0)
                    if len(rows) < CLOSED_MAX:
                        rows.append(_closed_row(h))
                else:
                    older = True
            if len(d) < 100 or older:
                break
            after = str(min(int(h.get("uTime") or 0) for h in d))
    except Exception:  # noqa: BLE001
        return None, None, []
    rows.sort(key=lambda r: -(r["closed"] or 0))
    return n, round(pnl, 2), rows


def _tail(path: str, n: int = TAIL_BYTES) -> list[dict]:
    """the last events of a JSON-lines log (the end of the file only); [] when it is missing"""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - n))
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = raw.splitlines()[1:] if size > n else raw.splitlines()      # a cut first line is dropped
    out = []
    for ln in lines:
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        if isinstance(ev, dict):
            out.append(ev)
    return out


def actions(since: int, logs: dict | None = None) -> list[dict]:
    """28.10 (build 46 §12.4 / §14): the autopilot's own filled orders on this account since the start, newest first — from its
    logs (okx_autopilot / okx_active / okx_futures .LOG), never a guess. No order id leaves this function."""
    from . import okx_active, okx_autopilot, okx_futures
    logs = logs or {"spot": okx_autopilot.LOG, "active": okx_active.LOG, "fut": okx_futures.LOG}
    out = []

    def add(ev, kind, sym, side=None, usd=None, px=None):
        t = int(ev.get("t") or 0)
        sym = str(sym or "").split("-")[0].upper()
        if t >= since and SYM_RE.match(sym):
            out.append({"t": t, "kind": kind, "sym": sym, "side": side, "usd": _r(usd), "px": ms._f(px) or None, "sig": ev.get("id")})
    for ev in _tail(logs["spot"]):
        if ev.get("ev") == "order" and (ms._f(ev.get("filled")) or 0) > 0 and ev.get("side") in ("buy", "sell"):
            add(ev, "spot_" + ev["side"], ev.get("ccy"), usd=ms._f(ev.get("usd")), px=ev.get("avg_px"))
    for ev in _tail(logs["active"]):
        if ev.get("ev") in ("buy", "sell", "park", "unpark") and (ms._f(ev.get("filled")) or 0) > 0:
            add(ev, "active_" + {"buy": "buy", "sell": "sell", "park": "park", "unpark": "unpark"}[ev["ev"]], ev.get("ccy") or ev.get("inst"),
                usd=ms._f(ev.get("usd")), px=ev.get("avg_px"))
    for ev in _tail(logs["fut"]):
        e_ = ev.get("ev")
        side = {"LONG": "long", "SHORT": "short"}.get(str(ev.get("side") or ""))
        if e_ == "open" and (ms._f(ev.get("sz")) or 0) > 0:
            add(ev, "fut_open", ev.get("inst"), side, px=ev.get("avg_px"))
        elif e_ in ("close", "reduce") and ((ms._f(ev.get("filled")) or 0) > 0 or ev.get("lead")):
            add(ev, "fut_" + e_, ev.get("inst"), px=ev.get("avg_px"))
        elif e_ == "gone":
            add(ev, "fut_gone", ev.get("inst"))
    out.sort(key=lambda a: -a["t"])
    return out[:ACTIONS_MAX]


# ── 28.10: deposits / withdrawals / master ↔ sub transfers (read-only bills) ──────────────────────────────────────────────
FUND_BILLS, TRADE_BILLS = "/api/v5/asset/bills", "/api/v5/account/bills"
FUND_KIND = {"1": "deposit", "2": "withdrawal", "13": "withdrawal", "20": "transfer", "21": "transfer", "22": "transfer", "23": "transfer"}
FUND_INTERNAL = {"130", "131"}               # the account's own funding ↔ trading moves — not a flow
STABLE = {"USDT", "USDC", "USD"}
FLOW_KINDS = ("deposit", "withdrawal", "transfer")
FLOW_EVENTS_MAX = 200


def _bills(fetch, host: str, path: str, since: int, cursor: str, params: dict | None = None, pages: int = 5) -> list:
    """bills since `since`, newest first, page by page (asset bills page by ts, account bills by billId)"""
    out, after = [], None
    for _ in range(pages):
        d = ms._call(fetch, host, path, {**(params or {}), "limit": "100", **({"after": after} if after else {})})
        out += d
        if len(d) < 100 or any(int(b.get("ts") or 0) < since for b in d):
            break
        last = d[-1]
        after = str(last.get(cursor) or last.get("ts") or "")
        if not after:
            break
    return [b for b in out if int(b.get("ts") or 0) >= since]


def _px_at(fetch, host: str, ccy: str, t: int) -> float | None:
    """the coin's USDT price at t: 1 for USDT / USDC; else the close of the 1-minute candle at t (the ticker when there is none)"""
    if ccy in STABLE:
        return 1.0
    try:
        d = ms._call(fetch, host, "/api/v5/market/history-candles", {"instId": f"{ccy}-USDT", "bar": "1m", "after": str(t + 60_000), "limit": "1"},
                     signed=False)
        if d and ms._f(d[0][4]):
            return ms._f(d[0][4])
        d = ms._call(fetch, host, "/api/v5/market/ticker", {"instId": f"{ccy}-USDT"}, signed=False)
        return ms._f(d[0].get("last")) if d else None
    except Exception:  # noqa: BLE001
        return None


def _hid(prefix: str, b: dict) -> str:
    raw = f"{prefix}:{b.get('billId') or ''}:{b.get('ts')}:{b.get('ccy')}:{b.get('balChg')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def read_flows(fetch, host: str, since: int, st: dict, now: int | None = None) -> None:
    """adds the new money flows since the start to st["flows"] {hash: [ms, ± $, kind]} (local state only). Never raises: a failed
    read leaves what is already known; a flow whose price can not be read now is tried again next time."""
    known = st.setdefault("flows", {})
    try:
        fund = _bills(fetch, host, FUND_BILLS, since, "ts")
    except Exception:  # noqa: BLE001
        fund = None
    try:
        trade = _bills(fetch, host, TRADE_BILLS, since, "billId", {"type": "1"})       # type 1 = transfer
    except Exception:  # noqa: BLE001
        trade = None
    if fund is not None and trade is not None:   # both bill lists read: «0 flows» is a fact, not a failed read
        st["flows_checked"] = int(now or time.time() * 1000)
    new = []
    internal = []
    for b in fund or []:
        typ, chg = str(b.get("type") or ""), ms._f(b.get("balChg"))
        if not chg:
            continue
        if typ in FUND_INTERNAL:
            internal.append([str(b.get("ccy") or ""), chg, int(b.get("ts") or 0)])
        elif typ in FUND_KIND:
            new.append((_hid("f", b), int(b.get("ts") or 0), str(b.get("ccy") or ""), chg, FUND_KIND[typ]))
    for b in trade or []:
        chg, ccy, t = ms._f(b.get("balChg")), str(b.get("ccy") or ""), int(b.get("ts") or 0)
        if not chg:
            continue
        twin = next((i for i in internal if i[0] == ccy and abs(i[1] + chg) <= 1e-9 + abs(chg) * 1e-6 and abs(i[2] - t) <= 120_000), None)
        if twin is not None:                       # the other half of a funding ↔ trading move inside this account
            internal.remove(twin)
            continue
        if fund is None:                           # without the funding bills an internal move can not be told apart — wait
            continue
        new.append((_hid("t", b), t, ccy, chg, "transfer"))
    for h, t, ccy, chg, kind in new:
        if h in known or not ccy:
            continue
        px = _px_at(fetch, host, ccy, t)
        if px is None:
            continue
        known[h] = [t, round(chg * px, 2), kind]


def flows_summary(st: dict, since: int) -> dict:
    ev = sorted((int(v[0]), float(v[1]), str(v[2])) for v in (st.get("flows") or {}).values()
                if isinstance(v, list) and len(v) == 3 and int(v[0]) >= since)
    by = {k: [0, 0.0] for k in FLOW_KINDS}
    for _t, u, k in ev:
        if k in by:
            by[k][0] += 1
            by[k][1] = round(by[k][1] + u, 2)
    inn = sum(u for _t, u, _k in ev if u > 0)
    out = -sum(u for _t, u, _k in ev if u < 0)
    return {"in_usd": _r(inn), "out_usd": _r(out), "net_usd": _r(inn - out), "n": len(ev), "last_t": ev[-1][0] if ev else None,
            "events": [[t, _r(u)] for t, u, _k in ev][-FLOW_EVENTS_MAX:], "by": by, "checked_t": st.get("flows_checked")}


def _by(v) -> list:
    if isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
        return [int(v[0]), round(float(v[1]), 2)]
    return [0, 0.0]


def _f_at(events: list, t: int) -> float:
    return sum(u for te, u in events if te <= t)


def dietz(base: float, events: list, t0: int, t1: int) -> float:
    """the capital a result is divided by: base + every flow in (t0, t1] weighted by the share of the period it was in"""
    span = max(1, t1 - t0)
    return base + sum(u * (t1 - te) / span for te, u in events if t0 < te <= t1)


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


def writer(env=None) -> bool:
    """22.10: exactly one writer of web/public_real.json — the machine the autopilot runs on (the lease's «node»): the Mac mirror
    while it is «mac», the server's autopilot service while it is «server». Unknown (no token, unreadable lease) → nobody."""
    from . import okx_lease
    s = okx_lease.status(env)
    return bool(s) and not s.get("error") and s.get("node") == okx_lease.node_of(env)


def build(s: dict, st: dict, now: int, closed_n: int | None = None, fut_realized: float | None = None,
          closed_rows: list | None = None, acts: list | None = None) -> dict:
    """The public summary from myspot_sub.read() (an ok read). Updates st["curve"] and st["fut_curve"]."""
    start = s.get("start") or {}
    s0, since = float(start.get("usd") or sub.START_USD), int(start.get("t") or now)
    eq = float(s["total_usd"])
    fl = flows_summary(st, since)
    ev = [(t, u) for t, u in fl["events"]]
    raw = eq - s0                                  # the state's curve keeps equity − start; the flows come off at publishing
    pnl = raw - _f_at(ev, now)
    fpnl = None if fut_realized is None else fut_realized + sum(float(p.get("upl") or 0.0) for p in (s.get("futures") or {}).get("positions") or [])
    curve = [p for p in st.get("curve") or [] if isinstance(p, list) and len(p) == 2 and p[0] >= since]
    fcurve = [p for p in st.get("fut_curve") or [] if isinstance(p, list) and len(p) == 2 and p[0] >= since]
    if not curve:
        curve = [[since, 0.0]]
        if not fcurve:
            fcurve = [[since, 0.0]]            # at the start both results are 0 by definition
    if now - curve[-1][0] >= STEP_MS:
        curve.append([now, round(raw, 2)])
        if fpnl is not None:
            fcurve.append([now, round(fpnl, 2)])
    st["curve"] = curve = compact(curve)
    ts = {p[0] for p in curve}
    st["fut_curve"] = fcurve = [p for p in fcurve if p[0] in ts]       # only at the curve's own (kept) timestamps
    day0 = now - now % DAY_MS
    refp = (since, 0.0) if since >= day0 else next(((t, v) for t, v in reversed(curve) if t < day0), None)
    today = today_pct = None
    if refp is not None:
        t_ref, raw_ref = refp
        today = pnl - (raw_ref - _f_at(ev, t_ref))                    # the change of the result, not of the money in the account
        cap = dietz(s0 + raw_ref, ev, t_ref, now)                       # the equity at the day's start + the day's flows, weighted
        today_pct = today / cap * 100 if cap else None
    cap_all = dietz(s0, ev, since, now)
    spot, cps = {}, {}
    for r in list((s.get("spot") or {}).get("rows") or []) + list((s.get("active") or {}).get("rows") or []):
        if r.get("usd"):
            spot[r["ccy"]] = spot.get(r["ccy"], 0.0) + float(r["usd"])
            cps.setdefault(r["ccy"], {k: r.get(k) for k in ("copy", "copy_reason", "copy_pct")})
    cash = float((s.get("cash") or {}).get("usd") or 0.0)
    fut = []
    for p in (s.get("futures") or {}).get("positions") or []:
        long = p.get("side") == "LONG"
        mv = ((p["mark"] / p["entry"] - 1) * (1 if long else -1) * 100) if p.get("mark") and p.get("entry") else None
        fut.append({"inst": p.get("inst"), "side": "long" if long else "short", "entry": p.get("entry"), "stop": p.get("stop"),
                    "target": p.get("tp"), "upnl_pct": _r(mv), "upnl_usd": _r(p.get("upl")), "copy": p.get("copy"),
                    "copy_reason": p.get("copy_reason"), "mark": p.get("mark"), "lev": p.get("lev"), "opened": p.get("ctime"),
                    "sig": p.get("sig")})
    return {"exchange": "OKX", "since": since, "start_usd": _r(s0), "equity_usd": _r(eq), "pnl_usd": _r(pnl),
            "pnl_pct": _r(pnl / cap_all * 100 if cap_all else None), "today_usd": _r(today), "today_pct": _r(today_pct),
            "spot": [{"sym": c, "share": _r(u / eq * 100 if eq else 0.0), "usd": _r(u), **cps.get(c, {})}
                     for c, u in sorted(spot.items(), key=lambda x: -x[1])],
            "spot_pnl_usd": _r(pnl - fpnl) if fpnl is not None else None, "fut_pnl_usd": _r(fpnl),
            "usdt_pct": _r(cash / eq * 100 if eq else 0.0), "usdt_usd": _r(cash), "alloc": dict(s.get("alloc") or {}), "futures": fut,
            "closed_n": int(closed_n or 0), "curve": [[int(t), _r(v - _f_at(ev, t))] for t, v in curve], "asof": now,
            "stale": not running(s.get("lease")), "flows": fl,
            "copiers": (s.get("lead") or {}).get("copiers"), "copy_url": (s.get("lead") or {}).get("url"),
            "closed": list(closed_rows or [])[:CLOSED_MAX], "fut_curve": [[int(t), _r(v)] for t, v in fcurve],
            "actions": list(acts or [])[:ACTIONS_MAX]}


def clean(doc: dict) -> dict:
    """Exactly the public keys, nothing else at any level (refuses anything that looks like an id, a key or an address)."""
    if set(doc) != set(KEYS):
        raise ValueError("public_real: wrong keys")
    out = {k: doc[k] for k in KEYS}
    out["spot"] = [{k: x.get(k) for k in SPOT_KEYS} for x in doc.get("spot") or []]
    out["futures"] = [{k: x.get(k) for k in FUT_KEYS} for x in doc.get("futures") or []]
    for x in out["spot"] + out["futures"]:            # only the known values: a bool (or null) and one of the codes
        x["copy"] = x["copy"] if isinstance(x.get("copy"), bool) else None
        x["copy_reason"] = x["copy_reason"] if x.get("copy_reason") in REASONS else None
    num = lambda v: v if isinstance(v, (int, float)) and not isinstance(v, bool) else None      # noqa: E731
    for x in out["futures"]:                           # 28.10: numbers only; «sig» only in the desk-trade shape
        x["mark"], x["lev"] = num(x.get("mark")), num(x.get("lev"))
        x["opened"] = int(x["opened"]) if num(x.get("opened")) else None
        x["sig"] = x["sig"] if isinstance(x.get("sig"), str) and SIG_RE.match(x["sig"]) else None
    out["closed"] = []
    for x in (doc.get("closed") or [])[:CLOSED_MAX]:
        if not isinstance(x, dict) or not INST_RE.match(str(x.get("inst") or "")):
            continue
        r = {k: num(x.get(k)) for k in CLOSED_KEYS}
        r["inst"], r["side"] = x["inst"], x.get("side") if x.get("side") in ("long", "short") else None
        r["opened"], r["closed"] = (int(r["opened"]) if r["opened"] else None), (int(r["closed"]) if r["closed"] else None)
        r["partial"] = x.get("partial") is True
        out["closed"].append(r)
    out["actions"] = []
    for x in (doc.get("actions") or [])[:ACTIONS_MAX]:
        if not isinstance(x, dict) or x.get("kind") not in ACTION_KINDS or not SYM_RE.match(str(x.get("sym") or "")) or not num(x.get("t")):
            continue
        out["actions"].append({"t": int(x["t"]), "kind": x["kind"], "sym": x["sym"],
                               "side": x.get("side") if x.get("side") in ("long", "short") else None,
                               "usd": num(x.get("usd")), "px": num(x.get("px")),
                               "sig": x["sig"] if isinstance(x.get("sig"), str) and SIG_RE.match(x["sig"]) else None})
    f = doc.get("flows") if isinstance(doc.get("flows"), dict) else {}
    out["flows"] = {"in_usd": num(f.get("in_usd")) or 0.0, "out_usd": num(f.get("out_usd")) or 0.0, "net_usd": num(f.get("net_usd")) or 0.0,
                    "n": int(num(f.get("n")) or 0), "last_t": int(f["last_t"]) if num(f.get("last_t")) else None,
                    "events": [[int(e[0]), num(e[1])] for e in f.get("events") or []
                               if isinstance(e, (list, tuple)) and len(e) == 2 and num(e[0]) and num(e[1]) is not None][-FLOW_EVENTS_MAX:],
                    "by": {k: _by((f.get("by") if isinstance(f.get("by"), dict) else {}).get(k)) for k in FLOW_KINDS},
                    "checked_t": int(f["checked_t"]) if num(f.get("checked_t")) else None}
    out["fut_curve"] = [[int(p[0]), num(p[1])] for p in doc.get("fut_curve") or []
                        if isinstance(p, (list, tuple)) and len(p) == 2 and num(p[0]) and num(p[1]) is not None]
    out["alloc"] = {k: (doc.get("alloc") or {}).get(k) for k in sub.ALLOC_KEYS}
    out["copiers"] = int(doc["copiers"]) if isinstance(doc.get("copiers"), (int, float)) else None
    out["copy_url"] = doc["copy_url"] if isinstance(doc.get("copy_url"), str) and doc["copy_url"].startswith(sub.ld.PROFILE) else None
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
    if not enabled(env) or not writer(env):
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
        since = int(s["start"].get("t") or now)
        try:
            acts = actions(since)
        except Exception:  # noqa: BLE001 — the summary never fails because of a log
            acts = []
        try:
            read_flows(fetch, "https://" + s["host"], since, state, now)
        except Exception:  # noqa: BLE001 — never fails because of the flows: the known ones stay
            pass
        doc = clean(build(s, state, now, *closed_history(fetch, "https://" + s["host"], since), acts=acts))
    elif state.get("last"):
        # the last real numbers, their own asof — never invented ones (a «last» from before 28.10 gets the new keys empty)
        doc = clean({**{k: None for k in KEYS}, "closed": [], "fut_curve": [], "actions": [], "flows": {}, **state["last"], "stale": True})
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
