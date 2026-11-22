"""Global best-trader discovery (owner 22.11.2026 17:19: «пробуем всё»). RESEARCH ONLY — nothing is copied; the copyable ones become
PAPER sources of the experiment factory; the real follow list (whale_study, another agent's module) is not touched.

Pre-registered (22.11 ~19:20 Athens):
1. Reachability probe from the GitHub runner of every source the owner named (copy-trading leaderboards of Bybit, Bitget, BingX,
   Gate, MEXC; perp DEXes dYdX v4, GMX, Drift, Aevo, Vertex; Hyperliquid) — HTTP status and whether a leaderboard-like list came
   back. A source that does not answer is reported as such, never guessed.
2. Ranking where wallet histories are public (Hyperliquid today): leaderboard → accounts ≥ $100k with all-time P&L > 0 → the top
   400 by all-time P&L and 200 by month P&L → «portfolio» (all-time P&L and account-value history) → P&L over 3 / 6 / 12 months
   as % of the account value at the window start, max drawdown of the account value over 12 months (deposits / withdrawals are
   not separable here — stated as a limit) → for the 120 best: fills of the last 12 months (≤ 4 pages of 2 000) → closed trades
   (fills with closed P&L), share of the profit from the best 2 trades, last trade time, and current leverage.
   Consistency bar: profit in EACH of 3 / 6 / 12 m, ≥ 50 closed trades, best-2 share < 50 %, a trade in the last 14 days,
   current leverage ≤ 20×, 12-month drawdown < 40 %. Score = min(P3, P6 / 2, P12 / 4) / (DD + 5) — steady monthly-ish profit per
   unit of drawdown, not the biggest peak. Copyability: positions are public in real time on Hyperliquid (lag ≈ our 5-min pass).
Usage: global_traders.py OUT.json"""
import json, sys, time, urllib.request

UA = {"User-Agent": "Mozilla/5.0 (cnq-research)", "Accept": "application/json", "Content-Type": "application/json"}
HL, LB = "https://api.hyperliquid.xyz/info", "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
DAY = 86_400_000
PROBES = {
    "bybit": "https://api2.bybit.com/fapi/beehive/public/v1/common/dynamic-leader-list?timeStamp=0&pageNo=1&pageSize=10&dataDuration=DATA_DURATION_NINETY_DAY&sortField=LEADER_SORT_FIELD_SORT_ROI",
    "bitget": "https://www.bitget.com/v1/trigger/trace/public/traderRank?pageNo=1&pageSize=10&sortRule=2&model=1",
    "bingx": "https://bingx.com/api/strategy/api/v1/copy/trader/list?pageId=0&pageSize=10",
    "gate": "https://www.gate.io/apiw/v2/copy/leader/list?page=1&page_size=10&order_by=profit_rate&sort_by=desc&cycle=quarter",
    "mexc": "https://futures.mexc.com/copyFutures/api/v1/traders/v2?limit=10&page=1&orderBy=ROI",
    "dydx": "https://indexer.dydx.trade/v4/leaderboard/pnl?period=PERIOD_90D&limit=10",
    "gmx": "https://arbitrum-api.gmxinfra.io/leaderboard/accounts?period=total",
    "drift": "https://data.api.drift.trade/stats/leaderboard?page=1&limit=10&ordering=desc&sort=pnl",
    "aevo": "https://api.aevo.xyz/leaderboard?limit=10",
    "vertex": "https://archive.prod.vertexprotocol.com/v1",
}


def http(url, body=None, timeout=30):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:  # noqa: BLE001
        return type(e).__name__, b""


def post(body):
    for k in range(3):
        st, b = http(HL, body)
        if st == 200:
            try:
                return json.loads(b)
            except ValueError:
                return None
        time.sleep(1 + 2 * k)
    return None


def f(x):
    try:
        return float(x)
    except Exception:  # noqa: BLE001
        return 0.0


def at(series, t):
    """the value of a [[t, v]] series at time t (the last point ≤ t)"""
    v = None
    for ts, val in series:
        if ts <= t:
            v = f(val)
        else:
            break
    return v


def main():
    out = sys.argv[1]
    probe = {}
    for k, u in PROBES.items():
        st, b = http(u, {"type": "x"} if k == "vertex" else None, 20)
        listy = False
        try:
            j = json.loads(b) if b else None
            listy = bool(j) and ("list" in json.dumps(j)[:4000].lower() or isinstance(j, list) or "rank" in json.dumps(j)[:4000].lower())
        except ValueError:
            pass
        probe[k] = {"status": st, "bytes": len(b), "list_like": listy}
        time.sleep(0.5)
    print("::notice title=gt probe::" + json.dumps(probe))
    now = int(time.time() * 1000)
    st, b = http(LB, None, 120)
    rows = []
    for x in (json.loads(b).get("leaderboardRows") if st == 200 else []) or []:
        m = {k: v for k, v in (x.get("windowPerformances") or [])}
        av = f(x.get("accountValue"))
        if av < 1e5 or f((m.get("allTime") or {}).get("pnl")) <= 0:
            continue
        rows.append({"addr": (x.get("ethAddress") or "").lower(), "name": x.get("displayName") or "", "account": av,
                     "pnl_all": f((m.get("allTime") or {}).get("pnl")), "pnl_m": f((m.get("month") or {}).get("pnl")),
                     "vlm_m": f((m.get("month") or {}).get("vlm"))})
    pick = {r["addr"]: r for r in sorted(rows, key=lambda r: -r["pnl_all"])[:400]}
    pick.update({r["addr"]: r for r in sorted(rows, key=lambda r: -r["pnl_m"])[:200]})
    print(f"hl leaderboard {len(rows)} eligible, portfolio for {len(pick)}", flush=True)
    stage = []
    for n, (a, r) in enumerate(pick.items()):
        p = post({"type": "portfolio", "user": a})
        time.sleep(0.2)
        w = {k: v for k, v in (p or [])} if isinstance(p, list) else {}
        al = w.get("allTime") or {}
        pnl = sorted((int(t), f(v)) for t, v in al.get("pnlHistory") or [])
        av = sorted((int(t), f(v)) for t, v in al.get("accountValueHistory") or [])
        if len(pnl) < 5 or not av:
            continue
        res = {}
        for lab, d in (("p3", 90), ("p6", 180), ("p12", 365)):
            t0 = now - d * DAY
            p0, a0 = at(pnl, t0), at(av, t0)
            res[lab] = round((pnl[-1][1] - p0) / a0 * 100, 1) if p0 is not None and a0 and a0 > 0 else None
        a12 = [v for t, v in av if t >= now - 365 * DAY]
        peak, dd = 0.0, 0.0
        for v in a12:
            peak = max(peak, v)
            if peak > 0:
                dd = max(dd, (peak - v) / peak * 100)
        stage.append({**r, **res, "dd12": round(dd, 1)})
        if n % 100 == 0:
            print(f"portfolio {n + 1}/{len(pick)} kept {len(stage)}", flush=True)
    good = [s for s in stage if all((s.get(k) or 0) > 0 for k in ("p3", "p6", "p12"))]
    for s in good:
        s["score0"] = min(s["p3"], s["p6"] / 2, s["p12"] / 4) / (s["dd12"] + 5)
    good.sort(key=lambda s: -s["score0"])
    final = []
    for s in good[:120]:
        fills, t = [], now - 365 * DAY
        for _ in range(4):
            fl = post({"type": "userFillsByTime", "user": s["addr"], "startTime": t, "aggregateByTime": True}) or []
            time.sleep(0.2)
            if not fl:
                break
            fills += fl
            if len(fl) < 2000:
                break
            t = int(fl[-1]["time"]) + 1
        closed = [f(x.get("closedPnl")) for x in fills if f(x.get("closedPnl"))]
        prof = sorted((c for c in closed if c > 0), reverse=True)
        top2 = sum(prof[:2]) / sum(prof) if prof else 1.0
        last_t = max((int(x["time"]) for x in fills), default=0)
        ch = post({"type": "clearinghouseState", "user": s["addr"]}) or {}
        time.sleep(0.2)
        levs = [f((p.get("position") or {}).get("leverage", {}).get("value")) for p in ch.get("assetPositions") or []]
        coins = sorted({(p.get("position") or {}).get("coin") for p in ch.get("assetPositions") or []} - {None})
        s.update({"trades": len(closed), "top2_share": round(top2, 2), "last_trade_d": round((now - last_t) / DAY, 1) if last_t else None,
                  "lev_max": max(levs) if levs else 0, "open_coins": coins[:8], "win": round(sum(c > 0 for c in closed) / len(closed), 3) if closed else None,
                  "style": ("scalper" if len(closed) > 3000 else "swing" if len(closed) > 200 else "position")})
        s["passes"] = bool(s["trades"] >= 50 and s["top2_share"] < 0.5 and s["last_trade_d"] is not None and s["last_trade_d"] <= 14
                           and s["lev_max"] <= 20 and s["dd12"] < 40)
        final.append(s)
    top = sorted([s for s in final if s["passes"]], key=lambda s: -s["score0"])[:30]
    O = {"t": now, "probe": probe, "hl": {"eligible": len(rows), "portfolio": len(stage), "profit_3_6_12": len(good), "checked": len(final),
                                          "pass": sum(1 for s in final if s["passes"])}, "top": top}
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    print("::notice title=gt hl::" + json.dumps(O["hl"]))
    for i in range(0, len(top), 10):
        print(f"::notice title=gt top {i // 10 + 1}::" + json.dumps([{k: s.get(k) for k in ("addr", "name", "account", "p3", "p6", "p12", "dd12", "trades",
                                                                                          "win", "top2_share", "last_trade_d", "lev_max", "style",
                                                                                          "score0")} for s in top[i:i + 10]], ensure_ascii=False)[:4000])


if __name__ == "__main__":
    main()
