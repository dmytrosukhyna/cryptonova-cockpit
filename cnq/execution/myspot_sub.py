"""«Мой портфель · OKX» + the CRYPTO NOVA sub-account (22.10.2026, owner: see the whole process — the total together and the
breakdown). The mirror (scripts/mirror_mac.sh → myspot_watch.run) adds this section to the SAME encrypted web/myspot.enc.json:
«Всего: $X = личный $A + CRYPTO NOVA $B», then the sub-account — spot coins vs the autopilot's targets, futures positions with
their stops and targets, active spot, USDT, the result since the start, and the autopilot's own «СОВПАДАЕТ С СИСТЕМОЙ» line.

READ ONLY. The autopilot's key (OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE) is used through myspot_watch.make_fetch —
a client that can only send GET requests. Nothing is placed, moved or cancelled here; tests/test_myspot_sub.py checks the
source and every request. The autopilot's own facts (its targets, its «совпадает» line, its futures trades, the active-spot
coins, the start) come from its local state files and log — no network for those.

Requests (all GET): /api/v5/account/balance, /api/v5/asset/balances, /api/v5/finance/savings/balance, /api/v5/market/tickers,
/api/v5/account/config, /api/v5/account/positions (SWAP), /api/v5/trade/orders-algo-pending (SWAP stops and targets), and for a
lead trader (okx_lead.py) /api/v5/copytrading/config, /api/v5/copytrading/instruments, /api/v5/copytrading/current-subpositions —
the lead spot positions are counted like ordinary coins; «lead» = the role line, the copiers and the public profile link.
«Результат с начала»: the start is the autopilot's first live run — its state (first_live) or its log (the first live «plan»
event in data/okx_autopilot.jsonl), whichever is earlier; else OKX_START_USD / OKX_START_AT; else $10 000.
Without OKX_API_* → None: the card works exactly as before. Not financial advice. Stdlib only."""
from __future__ import annotations

import datetime as dt
import json
import os
import time

from . import myspot_watch as ms
from . import okx_alloc as al
from . import okx_autopilot as ap
from . import okx_lead as ld

START_USD = 10_000.0
LIVE_MODE = "РЕАЛЬНЫЕ ДЕНЬГИ"
READS = ("/api/v5/account/balance", "/api/v5/asset/balances", "/api/v5/finance/savings/balance", "/api/v5/market/tickers",
         "/api/v5/account/config", "/api/v5/account/positions", "/api/v5/trade/orders-algo-pending") + ld.READS


def _env(env=None) -> dict:
    return os.environ if env is None else env


def creds(env=None):
    return ap.creds(env)


def _date_ms(s) -> int | None:
    """OKX_START_AT: «2026-10-22» or «2026-10-22 14:05» (UTC) or epoch ms."""
    s = str(s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    for f in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(dt.datetime.strptime(s, f).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
        except ValueError:
            pass
    return None


def first_live_in_log(path: str | None = None, limit: int = 200_000) -> dict | None:
    """The autopilot's first live run in its own log: the first «plan» event in real-money mode (the account total before its
    first orders)."""
    try:
        with open(path or ap.LOG, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= limit:
                    break
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("ev") == "plan" and ev.get("mode") == LIVE_MODE and ev.get("t"):
                    return {"t": int(ev["t"]), "usd": ms._f(ev.get("total_usd")), "src": "log"}
    except OSError:
        return None
    return None


def start(env=None, st: dict | None = None, log_path: str | None = None, fut_st: dict | None = None) -> dict:
    """{"usd", "t", "src"} — where «результат с начала» counts from."""
    e = _env(env)
    st = ap.load_state(env) if st is None else st
    cands = []
    fl = st.get("first_live") or {}
    if fl.get("t"):
        cands.append({"t": int(fl["t"]), "usd": ms._f(fl.get("usd")), "src": "state"})
    lg = first_live_in_log(log_path)
    if lg:
        cands.append(lg)
    first = min(cands, key=lambda c: c["t"]) if cands else None
    usd_env, t_env = ms._f(e.get("OKX_START_USD")), _date_ms(e.get("OKX_START_AT"))
    if first and first.get("usd"):
        usd, src = first["usd"], first["src"]
    elif usd_env and usd_env > 0:
        usd, src = usd_env, "env"
    else:
        usd, src = START_USD, "default"
    fut_t = (fut_st or {}).get("first_live")
    t = (first or {}).get("t") or t_env or (int(fut_t) if fut_t else None)
    return {"usd": round(float(usd), 2), "t": t, "src": src}


def _positions(fetch, host: str) -> list:
    out = []
    for p in ms._call(fetch, host, "/api/v5/account/positions", {"instType": "SWAP"}):
        q = ms._f(p.get("pos")) or 0.0
        if not q:
            continue
        side = p.get("posSide") or "net"
        long = (q > 0) if side == "net" else side == "long"
        upr = ms._f(p.get("uplRatio"))
        out.append({"inst": str(p.get("instId") or ""), "side": "LONG" if long else "SHORT", "sz": abs(q),
                    "entry": ms._f(p.get("avgPx")), "mark": ms._f(p.get("markPx")) or ms._f(p.get("last")),
                    "upl": ms._f(p.get("upl")), "upl_pct": round(upr * 100, 2) if upr is not None else None,
                    "lev": ms._f(p.get("lever")), "notional": abs(ms._f(p.get("notionalUsd")) or 0.0),
                    "margin": ms._f(p.get("margin")) or ms._f(p.get("imr")), "liq": ms._f(p.get("liqPx")),
                    "ctime": int(ms._f(p.get("cTime")) or 0) or None})
    return out


def _algos(fetch, host: str) -> list:
    out = []
    for typ in ("conditional", "oco"):
        try:
            out += ms._call(fetch, host, "/api/v5/trade/orders-algo-pending", {"ordType": typ, "instType": "SWAP"})
        except ms.OkxError:
            pass
    return out


def _stop_tp(algos: list, inst: str, long: bool) -> tuple:
    sl = tp = None
    for a in algos:
        if a.get("instId") != inst or (a.get("side") and a.get("side") != ("sell" if long else "buy")):
            continue
        sl = sl if sl is not None else ms._f(a.get("slTriggerPx"))
        tp = tp if tp is not None else ms._f(a.get("tpTriggerPx"))
    return sl, tp


ALLOC_KEYS = ("spot_coins_usd", "spot_cash_usd", "fut_budget_usd", "fut_margin_usd", "fut_reserve_usd", "active_coins_usd",
              "active_wait_usd", "other_usd", "spot_pct", "fut_pct", "active_pct")


def allocate(total: float, usdt: float, spot_coins: float, active_coins: float, positions: list, split: dict, pf_cash: float | None,
             fut_budget: float | None, targets_pct: float = 0.0) -> dict:
    """22.10 (owner read «43% USDT» as idle cash): where the account's dollars belong, from the autopilot's own budgets —
    okx_alloc.shares (spot / futures / active), the portfolio's own cash share (okx_autopilot state pf_cash) × the spot budget,
    the futures budget (okx_futures.budget), the active budget = total × active share (okx_active). The USDT is handed out in
    this order: futures margin in positions (real, with its open result) → the spot portfolio's cash → active spot waiting for
    «Импульс BTC» → the futures reserve for new trades → «прочее» (fees, rounding, coins still to buy). The USDT parts add up to
    the USDT on the account."""
    sp, fu_, ac_ = (float(split.get(k) or 0.0) for k in ("spot", "futures", "active"))
    if pf_cash is None:                              # an older state: the spot share not in coin targets = the portfolio's cash
        pf_cash = max(0.0, 1 - targets_pct / 100 / sp) if sp else 0.0
    budget = fut_budget if fut_budget else total * fu_
    margin = 0.0
    for p in positions:
        m = p.get("margin")
        if m is None and p.get("notional"):
            m = p["notional"] / (p.get("lev") or 1.0)
        margin += (m or 0.0) + (p.get("upl") or 0.0)
    left = max(0.0, usdt)
    out = {}
    for k, want in (("fut_margin_usd", margin), ("spot_cash_usd", pf_cash * total * sp), ("active_wait_usd", total * ac_ - active_coins),
                    ("fut_reserve_usd", budget - margin)):
        v = min(max(0.0, want), left)
        out[k], left = round(v, 2), left - v
    out["other_usd"] = round(usdt - sum(out[k] for k in ("fut_margin_usd", "spot_cash_usd", "active_wait_usd", "fut_reserve_usd")), 2)
    out.update({"spot_coins_usd": round(spot_coins, 2), "fut_budget_usd": round(budget, 2), "active_coins_usd": round(active_coins, 2),
                "spot_pct": round(sp * 100, 2), "fut_pct": round(fu_ * 100, 2), "active_pct": round(ac_ * 100, 2)})
    return {k: out[k] for k in ALLOC_KEYS}


def match(env=None, st: dict | None = None) -> dict:
    """The autopilot's own «СОВПАДАЕТ С СИСТЕМОЙ CRYPTO NOVA» line and its differences (okx_autopilot.same_text), from its state."""
    from . import okx_active, okx_futures
    st = ap.load_state(env) if st is None else st
    if not st.get("last_run"):
        return {"line": "СОВПАДАЕТ С СИСТЕМОЙ CRYPTO NOVA: ещё не проверено — автопилот ещё не запускался", "same": None,
                "diffs": [], "note": "", "t": None}
    b = {"diffs": list(st.get("diffs") or []), "split": st.get("split") or al.shares(env), "quote": (st.get("last") or {}).get("quote") or "USDT"}
    lines = ap.same_text(b, okx_futures.enabled(env), okx_active.enabled(env)).split("\n")
    diffs = [x.strip()[len("отличие: "):] for x in lines[1:] if x.strip().startswith("отличие: ")]
    note = " ".join(x.strip() for x in lines[1:] if not x.strip().startswith("отличие: "))
    return {"line": lines[0], "same": not diffs, "diffs": diffs, "note": note, "t": st.get("drift_t") or st.get("last_run")}


def read(env=None, fetch=None, now: int | None = None, log_path: str | None = None) -> dict | None:
    """The sub-account section for the encrypted card. None without OKX_API_* (the card stays as before)."""
    from . import okx_active, okx_futures
    c = creds(env)
    if not c:
        return None
    now = int(now or time.time() * 1000)
    st = ap.load_state(env)
    fst = okx_futures.load_state(env)
    out = {"asof": now, "ok": False, "start": start(env, st, log_path, fst), "match": match(env, st),
           "split": st.get("split") or al.shares(env)}
    try:
        out["lease"] = ap.lease.status(env, now)
    except Exception:  # noqa: BLE001
        out["lease"] = None
    if fetch is None:
        ap.ipv4_only(env)                        # the key is bound to this machine's IPv4 address (okx_autopilot.ipv4_only)
        fetch = ms.make_fetch(*c)
    try:
        host, bal = ms.probe(fetch, st.get("host"))
        raw = ms.read_raw(fetch, host, bal)
        lctx = ld.context(lambda p, q=None: ms._call(fetch, host, p, q), raw.get("cfg") or {}, env, raw)
        if lctx.get("err"):
            raise RuntimeError(f"лидерские спот-позиции не прочитались: {lctx['err']}")
        if lctx.get("fresh") and lctx["held"]:
            raw["trading"] = ld.merge(raw.get("trading") or [], lctx["held"])
        out["lead"] = ld.card(lctx)
        pos = _positions(fetch, host)
        lead_swaps = set()                       # futures positions with lead sub-positions (copied); None = unreadable
        if lctx["role"]["fut"] and pos:
            try:
                lead_swaps = {str(x.get("instId")) for x in ms._call(fetch, host, ld.SUBS, {"instType": "SWAP"}) or []}
            except ms.OkxError:
                lead_swaps = None
        algos = _algos(fetch, host) if pos else []
    except Exception as e:  # noqa: BLE001
        out["error"] = ap.describe_error(e).replace("Автопилот OKX: ", "")
        return out
    raw["earn"] = []                             # 22.10 (owner): the account is the system 1:1 — trading + funding only, no Earn
    rows = ms.assets(raw)
    total = round(sum(r["usd"] for r in rows), 2)
    act = okx_active.held(env)
    keep = ap.keep_set(env)
    targets = {str(r.get("ccy")): float(r.get("target_pct") or 0) for r in st.get("targets") or [] if r.get("ccy")}
    band = max(ap.MIN_ORDER, ap.BAND * total)
    px = {r["ccy"]: r.get("px") for r in rows}
    # 22.10 (build 45): copied or not, and why — okx_lead's own codes (usdc_only / not_supported / legacy / ordinary), per coin
    pairs = {str(t.get("instId") or "") for t in raw.get("tickers") or []}
    cp = {r["ccy"]: ld.coin_copy(r["ccy"], lctx, r["qty"], pairs) for r in rows if not r["cash"]}
    nocp = {"copy": False, "copy_reason": "ordinary", "copy_pct": 0.0}

    def spot_row(ccy, qty, usd_, where=None):
        t = targets.get(ccy, 0.0)
        gap = round(t / 100 * total - usd_, 2)
        state = ("keep" if ccy in keep else "ok" if abs(gap) < band else "out" if not t else "under" if gap > 0 else "over")
        return {"ccy": ccy, "qty": qty, "usd": round(usd_, 2), "now_pct": round(usd_ / total * 100, 2) if total else 0.0,
                "target_pct": round(t, 2), "gap_usd": gap, "state": state, "where": where or [], **cp.get(ccy, nocp)}

    spot, seen = [], set()
    for r in rows:
        if r["cash"]:
            continue
        q = act.get(r["ccy"], 0.0)
        k = max(0.0, 1 - q / r["qty"]) if q and r["qty"] else 1.0
        if r["usd"] * k < 1.0 and r["ccy"] not in targets:
            continue
        spot.append(spot_row(r["ccy"], r["qty"] * k, r["usd"] * k, r.get("where")))
        seen.add(r["ccy"])
    spot += [spot_row(c, 0.0, 0.0) for c in targets if c not in seen]
    spot.sort(key=lambda x: -max(x["usd"], x["target_pct"] / 100 * total))
    active = [{"ccy": c, "qty": q, "usd": round(q * (px.get(c) or 0.0), 2) if px.get(c) else None, **cp.get(c, nocp)} for c, q in act.items()]
    ours = {m.get("inst") for m in (fst.get("trades") or {}).values()}
    sigs = {m.get("inst"): str(tid) for tid, m in (fst.get("trades") or {}).items() if m.get("inst")}   # 28.10: the desk trade behind it
    futures = []
    for p in pos:
        sl, tp = _stop_tp(algos, p["inst"], p["side"] == "LONG")
        futures.append({**p, "stop": sl, "tp": tp, "ours": p["inst"] in ours, "sig": sigs.get(p["inst"]),
                        **ld.swap_copy(p["inst"], lctx["role"]["fut"], lead_swaps)})
    quote = (st.get("last") or {}).get("quote") or "USDT"
    free = next((ms._f(d.get("availBal")) for d in raw.get("trading") or [] if str(d.get("ccy")) == quote), None)
    cash = [{"ccy": r["ccy"], "usd": r["usd"]} for r in rows if r["cash"]]
    fs = okx_futures.status(env)
    s0 = out["start"]
    out.update({
        "ok": True, "host": host.replace("https://", ""), "total_usd": total,
        "result": {"usd": round(total - s0["usd"], 2), "pct": round((total / s0["usd"] - 1) * 100, 2) if s0["usd"] else None},
        "spot": {"rows": spot[:30], "usd": round(sum(x["usd"] for x in spot), 2), "target_pct": round(sum(targets.values()), 2),
                 "share": (out["split"] or {}).get("spot")},
        "futures": {"positions": futures, "upl": round(sum(p["upl"] or 0.0 for p in futures), 2), "enabled": fs.get("enabled"),
                    "budget_usd": fs.get("budget_usd"), "day_pnl": fs.get("day_pnl"), "day_stop": fs.get("day_stop"),
                    "lev": fs.get("lev"), "risk_pct": fs.get("risk_pct"), "share": (out["split"] or {}).get("futures")},
        "active": {"rows": active, "usd": round(sum(x["usd"] or 0.0 for x in active), 2), "enabled": okx_active.enabled(env),
                   "share": (out["split"] or {}).get("active")},
        "cash": {"ccy": quote, "rows": cash, "usd": round(sum(x["usd"] for x in cash), 2), "free": free},
    })
    out["alloc"] = allocate(total, out["cash"]["usd"], out["spot"]["usd"], out["active"]["usd"], futures, out["split"] or {},
                            ms._f(st.get("pf_cash")), ms._f(fs.get("budget_usd")), sum(targets.values()))
    return out
