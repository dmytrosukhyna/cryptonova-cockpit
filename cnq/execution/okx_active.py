"""«Автопилот OKX · активный спот» (22.10.2026) — the active-spot sleeve (OKX_ACT_SHARE, 0.10 of the account) follows the «Импульс
BTC» spot desk (web/paper.json → profiles.imps: when BTC jumps ≥ 2.5 % in an hour it buys the most liquid alts on spot, up to 5
positions × 20 % of its account, exits after 12 h or at its wide stop).
Between the desk's trades — owner's decision of 28.10 (real money, answered explicitly): the sleeve waits in BTC, not in USDT
(«парковка», state "park"). On the desk's exit signal (it closed a trade) the sleeve's BTC is sold to USDT and stays in USDT until
the desk's next entry signal or 3 days, whichever comes first (PAUSE_MS); then back to BTC. On an entry signal the BTC already held
pays for the buy (sold to USDT first, as much as the buy needs). Every move is logged in plain words ("text"). The parked BTC is
this sleeve's own: held() counts it, so the spot rebalance (okx_autopilot) and the extra core never count or sell it — nothing is
counted twice. The usual limits hold: the $10 minimum, limit IOC orders within OKX_SLIP, ≤ OKX_MAX_ORDER_USD an order, a daily cap
(OKX_MAX_DAY_USD); the parking is looked at every 15 minutes; a top-up / trim of the parked BTC only when it is more than
max($25, 1 % of the account) off the sleeve's share (the first parking: more than max($10, 0.3 %)). OKX_ACT_PARK=off → the old
behaviour (USDT between trades).

  • a NEW desk trade (≤ 45 min old, after this part started) → a spot buy of the same coin (limit IOC, at most OKX_SLIP above the
    best price) for the same share of the sleeve as the desk's position is of its account;
  • the desk closed it (time exit, stop, target) or cut it (qty_left) → the bought coins are sold (limit IOC, at most OKX_SLIP below
    the best price); a part left unsold is tried again a minute later.
Spot lead trader (22.10, owner: everything the account trades copyable; okx_lead.py): a buy on a {COIN}-USDT pair that OKX supports
for spot copy goes as a lead order (tdMode spot_isolated; the coin is added to the lead instruments first when missing) and its exit
sells the lead positions (spot_isolated); a full exit OKX refuses as a lead order closes this sleeve's own lead positions
(close-subposition, matched by the buy's order id). A coin OKX does not copy, or one through USDC, stays an ordinary trade.
A coin with no USDT pair but a USDC one goes through USDC (okx_autopilot.route / fill_via: USDT → USDC → COIN and back; unused USDC
returns to USDT at the end of the pass). Spot only: no leverage, no borrowing, nothing moved between accounts. The coins bought here are remembered in
~/.cryptonova_okx_active.json and the spot rebalance (okx_autopilot) never counts or sells them. Kill switch, live / demo and the
key checks are the spot autopilot's. Off with OKX_ACTIVE=off. Not financial advice. Stdlib only."""
from __future__ import annotations

import json
import os
import time
import urllib.request

from . import myspot_watch as ms
from . import okx_alloc as al
from . import okx_autopilot as ap
from . import okx_lead as ld

LOG = "data/okx_active.jsonl"
FRESH_MS = 45 * 60_000
MAX_POS = 5
PARK = "BTC"                       # owner's decision of 28.10: between the desk's trades the sleeve waits in BTC
PAUSE_MS = 3 * 86_400_000          # … USDT after the desk's exit until its next entry signal or 3 days, whichever comes first
PARK_EVERY_MS = 15 * 60_000
SRC = "https://raw.githubusercontent.com/dmytrosukhyna/cryptonova-cockpit/main/web/paper.json"


def _env(env=None) -> dict:
    return os.environ if env is None else env


def enabled(env=None) -> bool:
    return (_env(env).get("OKX_ACTIVE") or "on").strip().lower() not in ("off", "no", "0", "false")


def state_path(env=None) -> str:
    return os.path.expanduser(_env(env).get("OKX_ACT_STATE") or "~/.cryptonova_okx_active.json")


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
        json.dump(st, f, ensure_ascii=False, separators=(",", ":"))
    try:
        os.chmod(p + ".tmp", 0o600)
    except OSError:
        pass
    os.replace(p + ".tmp", p)


def parking_on(env=None) -> bool:
    return (_env(env).get("OKX_ACT_PARK") or "BTC").strip().lower() not in ("off", "no", "0", "false", "usdt")


def held(env=None, lead: bool | None = None) -> dict:
    """{COIN: quantity} bought by this sleeve and not sold yet — its trades and its parked BTC (owner's decision of 28.10); the
    spot rebalance leaves these alone. lead=True: only the ones held as lead positions (spot lead trader), lead=False: only the
    ordinary ones."""
    out = {}
    st = load_state(env)
    park = st.get("park") or {}
    for m in list((st.get("trades") or {}).values()) + ([park] if float(park.get("qty") or 0) > 0 else []):
        if lead is not None and bool(m.get("lead")) != lead:
            continue
        out[m["ccy"]] = out.get(m["ccy"], 0.0) + float(m.get("qty") or 0)
    return out


def fetch_desk(env=None) -> dict:
    src = _env(env).get("OKX_ACT_SRC") or _env(env).get("CNQ_MIRROR_SRC") or SRC
    req = urllib.request.Request(src + f"?t={int(time.time())}", headers={"Cache-Control": "no-cache", "User-Agent": "cnq-okx-active"})
    with urllib.request.urlopen(req, timeout=20) as r:
        p = json.loads(r.read().decode())
    return (p.get("profiles") or {}).get("imps") or {}


def _sell(cl: ap.Okx, ccy: str, quote: str, qty: float, slip: float, cl_id: str, sleep, env=None, log_path=None, m: dict | None = None,
          full: bool = False) -> dict:
    lead = bool((m or {}).get("lead"))
    rt = ap.route(cl, ccy, quote)
    inst_id, inst, tk = rt["inst"], rt["info"], rt["tk"]
    bid = ms._f((tk or {}).get("bidPx")) or ms._f((tk or {}).get("last"))
    if not inst or not bid:
        return {"filled": 0.0, "why": f"нет пары {rt['none']}"}
    have = ld.lead_avail(cl, inst_id) if lead and not rt["via"] else cl.avail(ccy)   # lead coins are not in the ordinary balance
    q = ap.floor_to(min(qty, have), float(inst["lotSz"]))
    if q < float(inst["minSz"]):
        return {"filled": qty, "why": "остаток меньше минимума OKX — считаю проданным", "dust": True}
    o = {"ccy": ccy, "inst": inst_id, "side": "sell", "sz": ap.fmt(q, inst["lotSz"]), "lot": inst["lotSz"], "min": inst["minSz"],
         "via": rt["via"], "px": ap.fmt(ap.floor_to(bid * (1 - slip), float(inst["tickSz"])), inst["tickSz"])}
    if lead and not rt["via"]:
        o.update(td=ld.TD, lead=True)
        try:
            return ap._fill(cl, o, cl_id, sleep)
        except ms.OkxError as e:
            if not full:
                raise
            return ld.close_out(cl, o, e, lambda ev: ap.log(ev, log_path), only=set((m or {}).get("ords") or []))
    return ap.fill_via(cl, o, cl_id, sleep, env, log_path) if rt["via"] else ap._fill(cl, o, cl_id, sleep)


def step(env=None, opener=None, desk: dict | None = None, now: int | None = None, sleep=time.sleep, cl: ap.Okx | None = None,
         total: float | None = None, log_path: str | None = None, save: bool = True) -> dict:
    now = int(now or time.time() * 1000)
    lp = log_path or LOG
    res = {"bought": 0, "sold": 0, "errors": 0, "skipped": [], "why": None}
    if not enabled(env):
        res["why"] = "активный спот выключен (OKX_ACTIVE=off)"
        return res
    if ap.stopped(env):
        res["why"] = "включён стоп"
        return res
    g = ap.lease.gate(env, now)                 # 22.10: never two autopilots on one account (okx_lease.py)
    if not g["ok"]:
        res["why"] = g["why"]
        return res
    st = load_state(env)
    st.setdefault("started", now)
    st.setdefault("trades", {})
    st.setdefault("skip", {})
    d = desk if desk is not None else fetch_desk(env)
    want = {str(t["id"]): t for t in d.get("open") or [] if t.get("id") and t.get("symbol") and t.get("side") == "LONG"}
    # owner's decision of 28.10: the desk's exit signal (a trade it closed after we last looked) and the BTC parking
    park_on, parked = parking_on(env), float((st.get("park") or {}).get("qty") or 0.0)
    last_close = max([int(c.get("closed") or 0) for c in d.get("closed") or [] if isinstance(c, dict)] or [0])
    st.setdefault("exit_seen", last_close)                      # first sight: the desk's old exits never trigger anything
    desk_exit = last_close if last_close > int(st.get("exit_seen") or 0) else 0
    pause_end = bool(st.get("pause_until")) and now >= int(st["pause_until"])
    park_due = (park_on and (bool(desk_exit) or pause_end or now - int(st.get("park_t") or 0) >= PARK_EVERY_MS)) or (not park_on and parked > 0)
    if not want and not st["trades"] and not park_due:
        if save:
            save_state(st, env)
        return res
    cl = cl or ap.connect(env, opener, ap.load_state(env), sleep)
    cl.lease_deadline = ap.lease.deadline(g, now)
    cfg = cl.config()
    perm = ap.perm_of(cfg)
    lead = {}

    def lctx():                                  # the spot lead context, read once and only when a trade needs it
        if "c" not in lead:
            lead["c"] = ld.context(lambda p, q=None: cl.call("GET", p, q), cfg, env)
        return lead["c"]
    bad = ("ключ с правом ВЫВОДА" if "withdraw" in perm else "у ключа нет права «Торговля»" if "trade" not in perm else ap.world_ok(cl, env))
    if bad:
        res["why"] = bad
        return res
    quote = ap.pick_quote(cl, env) or "USDT"
    slip = ap.limits(env)["slip"]
    seq = 0
    via = False                                  # a USDC leg happened this pass → free USDC back to USDT at its end
    exited = False
    # exits and cuts
    for tid, m in list(st["trades"].items()):
        t = want.get(tid)
        if t is None and not m.get("exit_t"):          # the desk closed it: an exit signal — once per trade, even if a sell is retried
            m["exit_t"], exited = now, True
        keep_frac = 0.0 if t is None else ((float(t["qty_left"]) / float(t["qty"])) if t.get("qty_left") is not None and float(t.get("qty") or 0) > 0 else 1.0)
        sell_q = float(m["qty"]) - float(m["q0"]) * keep_frac
        if sell_q <= float(m["q0"]) * 0.001:
            continue
        seq += 1
        try:
            f = _sell(cl, m["ccy"], quote, sell_q, slip, f"cnqas{now // 1000}{seq:03d}", sleep, env, lp, m, t is None)
            via = via or bool(m.get("via")) or bool(f.get("half"))
        except Exception as e:  # noqa: BLE001
            res["errors"] += 1
            ap.log({"ev": "sell_error", "id": tid, "ccy": m["ccy"], "error": str(e)[:200]}, lp)
            continue
        m["qty"] = max(0.0, float(m["qty"]) - float(f.get("filled") or 0))
        ap.log({"ev": "sell", "id": tid, "ccy": m["ccy"], "want": sell_q, **{k: v for k, v in f.items() if k != "dust"}}, lp)
        if f.get("filled"):
            res["sold"] += 1
        if m["qty"] <= float(m["q0"]) * 0.001 or (t is None and f.get("dust")):
            st["trades"].pop(tid)
    # owner's decision of 28.10: the desk's exit signal → the sleeve's BTC to USDT, USDT until the next entry or 3 days
    if exited or desk_exit:
        t_exit = desk_exit or now
        st["exit_seen"] = max(int(st.get("exit_seen") or 0), desk_exit)
        if float((st.get("park") or {}).get("qty") or 0) > 0:
            seq += 1
            via = _unpark(cl, st, quote, None, slip, f"cnqap{now // 1000}{seq:03d}", sleep, env, lp, res,
                          "стол «Импульс BTC» закрыл сделку — BTC этой части продан в USDT") or via
        if park_on:
            st["pause_until"] = max(int(st.get("pause_until") or 0), t_exit + PAUSE_MS)
            ap.log({"ev": "pause", "until": st["pause_until"],
                    "text": f"«Импульс BTC»: после выхода стола эта часть в USDT до следующего сигнала или до {_when(st['pause_until'])}"}, lp)
    # entries
    st["skip"] = {k: v for k, v in st["skip"].items() if k in want}
    fresh = [t for tid, t in want.items() if tid not in st["trades"] and tid not in st["skip"]
             and now - int(t.get("opened") or 0) <= FRESH_MS and int(t.get("opened") or 0) >= int(st["started"]) - FRESH_MS]
    if fresh:
        if total is None:
            total = sum(r["usd"] for r in ms.assets(ms.read_raw(cl.fetch, cl.host)))
        bud = total * al.shares(env)["active"]
        deq = float(d.get("equity") or d.get("start_equity") or 10_000)
        if st.get("pause_until"):                     # owner's decision of 28.10: an entry signal ends the USDT pause
            st["pause_until"] = 0
            ap.log({"ev": "pause_end", "text": "«Импульс BTC»: сигнал на вход — пауза в USDT закончилась"}, lp)
        if float((st.get("park") or {}).get("qty") or 0) > 0:       # … and the BTC already held pays for the buy
            need = sum(float(t.get("qty_left") or t["qty"]) * float(t["entry"]) / deq * bud for t in fresh[:max(0, MAX_POS - len(st["trades"]))])
            seq += 1
            via = _unpark(cl, st, quote, need * 1.01, slip, f"cnqap{now // 1000}{seq:03d}", sleep, env, lp, res,
                          "сигнал «Импульс BTC» на вход — для покупки продан BTC этой части") or via
        for t in fresh:
            tid = str(t["id"])
            if len(st["trades"]) >= MAX_POS:
                res["skipped"].append({"id": tid, "why": "уже 5 позиций активного спота"})
                continue
            base, mult = al.okx_base(str(t["symbol"]))
            rt = ap.route(cl, base, quote)                       # {base}-{quote}, or {base}-USDC through USDC (22.10)
            inst_id, inst, tk = rt["inst"], rt["info"], rt["tk"]
            k = ap.via_rate(cl) if rt["via"] else 1.0
            ask = (ms._f((tk or {}).get("askPx")) or ms._f((tk or {}).get("last")) or 0.0) * k
            if not inst or not ask:
                st["skip"][tid] = f"на OKX нет спот-пары {rt['none']}"
                res["skipped"].append({"id": tid, "why": st["skip"][tid]})
                continue
            if abs(ask * mult / float(t["entry"]) - 1) > 0.15:
                st["skip"][tid] = f"цена {inst_id} отличается от входа сигнала больше чем на 15%"
                res["skipped"].append({"id": tid, "why": st["skip"][tid]})
                continue
            usd = float(t.get("qty_left") or t["qty"]) * float(t["entry"]) / deq * bud
            usd = min(usd, (cl.avail(quote) + (cl.avail(ap.VIA) * k if rt["via"] else 0.0)) * 0.98)
            q = ap.floor_to(usd / ask, float(inst["lotSz"]))
            if q < float(inst["minSz"]) or q * ask < ap.MIN_ORDER:
                st["skip"][tid] = f"меньше минимального ордера (≈ {ap.usd(q * ask)})"
                res["skipped"].append({"id": tid, "why": st["skip"][tid]})
                continue
            seq += 1
            o = {"ccy": base, "inst": inst_id, "side": "buy", "sz": ap.fmt(q, inst["lotSz"]), "lot": inst["lotSz"], "min": inst["minSz"],
                 "via": rt["via"], "px": ap.fmt(ap.ceil_to(ask / k * (1 + slip), float(inst["tickSz"])), inst["tickSz"])}
            via = via or bool(rt["via"])
            lc = lctx() if not rt["via"] and quote == "USDT" else None
            if lc and lc["active"]:                  # 22.10: a copyable coin → a lead buy (added to the lead instruments first)
                if inst_id not in lc["enabled"] and inst_id in lc["supported"] and lc["enabled"]:   # never from an unread list
                    lc["enabled"], _fails = ld.add_insts(cl, "SPOT", set(lc["enabled"]), {inst_id})
                if inst_id in lc["enabled"]:
                    o.update(td=ld.TD, lead=True)
            try:
                cid = f"cnqab{now // 1000}{seq:03d}"
                f = ap.fill_via(cl, o, cid, sleep, env, lp) if rt["via"] else ap._fill(cl, o, cid, sleep)
            except Exception as e:  # noqa: BLE001
                res["errors"] += 1
                ap.log({"ev": "buy_error", "id": tid, "inst": inst_id, "error": str(e)[:200]}, lp)
                continue
            ap.log({"ev": "buy", "id": tid, "inst": inst_id, **({"route": ap.route_text(o)} if rt["via"] else {}),
                    **({"copy": bool(o.get("lead"))} if lc and lc["active"] else {}), **f}, lp)
            if f["filled"] > 0:
                st["trades"][tid] = {"ccy": base, "q0": f["filled"], "qty": f["filled"], "opened": now, "px": f["avg_px"],
                                     **({"via": rt["via"]} if rt["via"] else {}),
                                     **({"lead": True, "ords": [f["ordId"]]} if o.get("lead") else {})}
                res["bought"] += 1
            elif f.get("half"):
                res["skipped"].append({"id": tid, "why": f["half"]})          # not «skip» for good: tried again next minute
    # owner's decision of 28.10: between the desk's trades the sleeve waits in BTC (not in a pass with an exit or an entry)
    if not park_on and float((st.get("park") or {}).get("qty") or 0) > 0:
        seq += 1
        via = _unpark(cl, st, quote, None, slip, f"cnqap{now // 1000}{seq:03d}", sleep, env, lp, res,
                      "парковка выключена (OKX_ACT_PARK=off) — BTC этой части продан в USDT") or via
    elif park_on and park_due and not (exited or desk_exit or fresh) and now >= int(st.get("pause_until") or 0):
        try:
            _park(cl, st, quote, slip, now, seq, sleep, env, lp, res, total, lctx)
        except Exception as e:  # noqa: BLE001 — the parking never breaks the sleeve's trades
            res["errors"] += 1
            ap.log({"ev": "park_error", "error": str(e)[:200]}, lp)
    if via:
        ap.sweep(cl, env, f"cnqau{now // 1000}", sleep, lp)                  # unused USDC back to USDT at the end of the pass
    if save:
        save_state(st, env)
    return res


def _when(ms_: int) -> str:
    import datetime as dt
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Kyiv")
    except Exception:  # noqa: BLE001
        tz = dt.timezone(dt.timedelta(hours=2))
    return dt.datetime.fromtimestamp(int(ms_) / 1000, tz).strftime("%d.%m %H:%M") + " по Киеву"


def _day_ok(st: dict, usd: float, now: int, env=None) -> bool:
    """the daily cap (OKX_MAX_DAY_USD) for the parking's own orders"""
    day = time.strftime("%Y-%m-%d", time.gmtime(now / 1000))
    if st.get("day") != day:
        st["day"], st["day_usd"] = day, 0.0
    return float(st.get("day_usd") or 0.0) + usd <= ap.limits(env)["max_day"]


def _unpark(cl, st: dict, quote: str, usd: float | None, slip: float, cl_id: str, sleep, env, lp, res: dict, why: str) -> bool:
    """sell the sleeve's parked BTC: all of it (usd None — an exit, the parking turned off) or about usd of it (an entry pays with
    it). Logged in plain words. Returns True when a USDC leg happened."""
    p = st.get("park") or {}
    q = float(p.get("qty") or 0.0)
    if q <= 0:
        return False
    full = usd is None
    if not full:
        tk = cl.ticker(f"{PARK}-{quote}") or {}
        bid = ms._f(tk.get("bidPx")) or ms._f(tk.get("last"))
        if not bid:
            return False
        q = min(q, usd / bid)
    try:
        f = _sell(cl, PARK, quote, q, slip, cl_id, sleep, env, lp, p, full)
    except Exception as e:  # noqa: BLE001
        res["errors"] += 1
        ap.log({"ev": "unpark_error", "ccy": PARK, "error": str(e)[:200]}, lp)
        return False
    got = float(f.get("filled") or 0.0)
    p["qty"] = max(0.0, float(p["qty"]) - got)
    if p["qty"] <= 1e-9 or (full and f.get("dust")):
        st["park"] = {}
    usd_ = float(f.get("usd") or 0.0)
    ap.log({"ev": "unpark", "ccy": PARK, "want": q, **{k: v for k, v in f.items() if k != "dust"},
            "text": f"«Импульс BTC»: {why}" + (f" (≈ {ap.usd(usd_)})" if usd_ else "")}, lp)
    if got:
        res["sold"] += 1
    return bool(p.get("via")) or bool(f.get("half"))


def _park(cl, st: dict, quote: str, slip: float, now: int, seq: int, sleep, env, lp, res: dict, total: float | None, lctx) -> None:
    """the sleeve's money between the desk's trades → BTC (owner's decision of 28.10): target = the sleeve's share of the account
    minus what its open trades cost; a top-up or a trim only beyond max($25, 1 % of the account) once parked (the first parking:
    beyond max($10, 0.3 %)); limit IOC within OKX_SLIP, ≤ OKX_MAX_ORDER_USD an order, the daily cap. Logged in plain words."""
    pause_was = st.get("pause_until")
    st["park_t"] = now
    if pause_was:
        st["pause_until"] = 0
        ap.log({"ev": "pause_end", "text": "«Импульс BTC»: 3 дня после выхода прошли, сигнала не было — эта часть снова в BTC"}, lp)
    if total is None:
        total = sum(r["usd"] for r in ms.assets(ms.read_raw(cl.fetch, cl.host)))
    rt = ap.route(cl, PARK, quote)
    inst_id, inst, tk = rt["inst"], rt["info"], rt["tk"]
    bid = ms._f((tk or {}).get("bidPx")) or ms._f((tk or {}).get("last"))
    ask = ms._f((tk or {}).get("askPx")) or ms._f((tk or {}).get("last"))
    if not inst or not bid or not ask or rt["via"]:
        return
    p = st.setdefault("park", {})
    q0 = float(p.get("qty") or 0.0)
    alts = sum(float(m.get("qty") or 0) * float(m.get("px") or 0) for m in (st.get("trades") or {}).values())
    target = max(0.0, total * al.shares(env)["active"] - alts)
    gap = target - q0 * bid
    thr = max(ap.MIN_ORDER, ap.BAND * total) if q0 <= 0 else max(25.0, 0.01 * total)
    lim = ap.limits(env)
    lot, mn, tick = float(inst["lotSz"]), float(inst["minSz"]), float(inst["tickSz"])
    if gap >= thr:
        usd = min(gap, cl.avail(quote) * 0.98, lim["max_order"])
        q = ap.floor_to(usd / ask, lot)
        if q < mn or q * ask < ap.MIN_ORDER or not _day_ok(st, q * ask, now, env):
            return
        o = {"ccy": PARK, "inst": inst_id, "side": "buy", "sz": ap.fmt(q, inst["lotSz"]), "lot": inst["lotSz"], "min": inst["minSz"],
             "via": None, "px": ap.fmt(ap.ceil_to(ask * (1 + slip), tick), inst["tickSz"])}
        lc = lctx() if quote == "USDT" else None
        if lc and lc["active"]:                           # copyable like the sleeve's trades (okx_lead), when OKX lists it
            if inst_id not in lc["enabled"] and inst_id in lc["supported"] and lc["enabled"]:
                lc["enabled"], _fails = ld.add_insts(cl, "SPOT", set(lc["enabled"]), {inst_id})
            if inst_id in lc["enabled"]:
                o.update(td=ld.TD, lead=True)
        if q0 > 0 and bool(p.get("lead")) != bool(o.get("lead")):
            return                                          # one kind of BTC position per parking: never mix lead and ordinary
        f = ap._fill(cl, o, f"cnqap{now // 1000}{seq + 1:03d}", sleep)
        got = float(f.get("filled") or 0.0)
        if got > 0:
            p.update({"ccy": PARK, "qty": q0 + got, "q0": float(p.get("q0") or 0.0) + got, "t": now, "px": f.get("avg_px"),
                      **({"lead": True, "ords": list(p.get("ords") or []) + [f["ordId"]]} if o.get("lead") else {})})
            st["day_usd"] = round(float(st.get("day_usd") or 0.0) + float(f.get("usd") or 0.0), 2)
            res["bought"] += 1
        ap.log({"ev": "park", "ccy": PARK, "inst": inst_id, **({"copy": bool(o.get("lead"))} if lc and lc["active"] else {}), **f,
                "text": (f"«Импульс BTC»: сигнала нет — деньги этой части (≈ {ap.usd(f.get('usd') or 0)}) держим в BTC, а не в USDT"
                         if q0 <= 0 else f"«Импульс BTC»: BTC этой части добран до её доли (≈ {ap.usd(f.get('usd') or 0)})")}, lp)
    elif -gap >= thr and q0 > 0:
        q = ap.floor_to(min(q0, -gap / bid), lot)
        if q < mn or q * bid < ap.MIN_ORDER or not _day_ok(st, q * bid, now, env):
            return
        f = _sell(cl, PARK, quote, q, slip, f"cnqap{now // 1000}{seq + 1:03d}", sleep, env, lp, p, False)
        got = float(f.get("filled") or 0.0)
        p["qty"] = max(0.0, q0 - got)
        st["day_usd"] = round(float(st.get("day_usd") or 0.0) + float(f.get("usd") or 0.0), 2)
        if got:
            res["sold"] += 1
        ap.log({"ev": "unpark", "ccy": PARK, "want": q, **{k: v for k, v in f.items() if k != "dust"},
                "text": f"«Импульс BTC»: BTC этой части больше её доли — лишнее продано (≈ {ap.usd(f.get('usd') or 0)})"}, lp)


def plan_lines(env=None, desk: dict | None = None, now: int | None = None, total: float = 0.0) -> list[str]:
    now = int(now or time.time() * 1000)
    bud = total * al.shares(env)["active"]
    L = [f"АКТИВНЫЙ СПОТ · Импульс BTC — {al._p(al.shares(env)['active'])} счёта ≈ {ap.usd(bud)}"
         + ("" if enabled(env) else " — ВЫКЛЮЧЕН (OKX_ACTIVE=off), деньги в USDT")]
    try:
        d = desk if desk is not None else fetch_desk(env)
    except Exception as e:  # noqa: BLE001
        return L + [f"  сигналы не загрузились: {str(e)[:100]}"]
    st = load_state(env)
    mine = st.get("trades") or {}
    opened = [t for t in d.get("open") or [] if t.get("side") == "LONG"]
    park, pause = st.get("park") or {}, int(st.get("pause_until") or 0)
    if not parking_on(env):
        idle = "эта часть лежит в USDT (парковка выключена: OKX_ACT_PARK=off)"
    elif pause > now:
        idle = f"после выхода стола эта часть в USDT до следующего сигнала или до {_when(pause)}"
    elif float(park.get("qty") or 0) > 0:
        idle = f"эта часть ждёт в BTC ({float(park['qty']):g} BTC), а не в USDT (решение владельца 28.10)"
    else:
        idle = "эта часть переходит в BTC на ближайшей проверке (решение владельца 28.10: между сделками — в BTC, а не в USDT)"
    if not opened and not mine:
        return L + [f"  ждёт сигнала: у стола «Импульс BTC» нет открытых сделок — {idle}."]
    deq = float(d.get("equity") or 10_000)
    started = int(st.get("started") or now)
    for t in opened:
        tid = str(t["id"])
        if tid in mine:
            L.append(f"  {t['symbol']}: уже куплено {mine[tid]['qty']:g}")
        elif now - int(t.get("opened") or 0) > FRESH_MS or int(t.get("opened") or 0) < started - FRESH_MS:
            L.append(f"  {t['symbol']}: сигнал старше 45 минут — не догоняем")
        else:
            usd = float(t.get("qty_left") or t["qty"]) * float(t["entry"]) / deq * bud
            L.append(f"  {t['symbol']}: КУПИТЬ на ≈ {ap.usd(usd)} (лимит IOC) — сигнал «Импульс BTC», выход вместе со столом")
    for tid, m in mine.items():
        if tid not in {str(t["id"]) for t in opened}:
            L.append(f"  {m['ccy']}: ПРОДАТЬ {m['qty']:g} — стол закрыл сделку")
    return L


def status(env=None) -> dict:
    st = load_state(env)
    park = st.get("park") or {}
    return {"enabled": enabled(env), "open": [{"ccy": m["ccy"], "qty": m["qty"]} for m in (st.get("trades") or {}).values()],
            "park": {"ccy": park.get("ccy"), "qty": park.get("qty")} if float(park.get("qty") or 0) > 0 else None,
            "pause_until": st.get("pause_until") or None}
