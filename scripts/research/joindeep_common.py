"""Backlog 196 (17.11.2026 18:34, owner hypothesis: «у хороших трейдеров чем глубже просадка по позиции, тем лучше для нас в неё
входить»). RESEARCH + PAPER ONLY — no real-account change whatever the result. Extends backlog 164 (whale_joindepth) past the live
−3 % window. PRE-REGISTERED (this file is committed before any result is seen):

- A decision = one whale position (trips of the same coin + side opened within 4 h are one decision, as backlog 103 / 164).
- gap(t) = side-adjusted price / the whale's notional-weighted entry known at t − 1, in % (negative = the whale is in loss).
  Price = the close of each 15-minute bar after his first entry + 2 min, while the position is open (data end for open ones).
- Bands, entry at the FIRST 15-minute close where gap ≤ the band's upper bound (side-aware; one decision may enter several bands):
  B0 «0…−3 %» (the comparison: first close with −3 ≤ gap ≤ 0), D1 «−3…−6 %», D2 «−6…−10 %», D3 «−10…−15 %», D4 «< −15 %».
  An entry whose gap at that close is already past the band's lower bound (a gap through the band) is kept in the band it was
  reached for and counted in «gapped» (reported).
- Copy: enter at that close, exit 2 min after the whale's last close; still-open / never-closed positions marked at the last
  price of the data (survivorship: they are IN the set); liquidated positions are closed trips like any other.
  Stop variants measured from OUR entry: −16 % (primary), −8 %, no stop. Costs 0.11 % a round trip + funding paid.
- Result per copy = % of the copy's notional. Report per band: n, mean, median, win share, worst, share stopped, t.
- Survivorship-free set = OKX (closed + current positions) and Hyperliquid (fills + open positions) from lab-data-whales
  (data of 16.11.2026). Binance ws_trips holds CLOSED positions only — reported as a biased check, never used for the verdict.
- Primary population: decisions of traders passing the 60-day bar at entry (≥ 20 decisions in 60 days, ≥ 70 % wins, PF ≥ 2,
  net > 0 — the live bar). Splits reported: bar vs no bar; BTC regime at entry (BTC close above its 50-day SMA of daily closes =
  «рост», else «спад»); coin class (BTC/ETH vs alts); halves of time (by decision open time, median split of the primary set).
- PASS for a band Dk (k = 1..4), primary population, stop −16 %: mean(Dk) > mean(B0) in BOTH halves AND Welch
  t(Dk − B0) ≥ 2.2. Anything else: not passed. Only a passing band may become an owner-only PAPER desk «Глубокая просадка (тест)»."""
import math
import statistics as st

BANDS = [("B0", 0.0, -3.0), ("D1", -3.0, -6.0), ("D2", -6.0, -10.0), ("D3", -10.0, -15.0), ("D4", -15.0, None)]
STOPS = (16.0, 8.0, None)
T_PASS = 2.2


def stat(xs, stopped=None):
    if not xs:
        return {"n": 0}
    o = {"n": len(xs), "mean": round(st.mean(xs), 3), "median": round(st.median(xs), 3), "win": round(sum(x > 0 for x in xs) / len(xs), 3),
         "worst": round(min(xs), 2), "sum": round(sum(xs), 1)}
    if len(xs) >= 2:
        sd = st.stdev(xs)
        o["t"] = round(st.mean(xs) / (sd / math.sqrt(len(xs))), 2) if sd else None
    if stopped is not None:
        o["stopped"] = round(sum(stopped) / len(stopped), 3) if stopped else None
    return o


def welch(a, b):
    if len(a) < 3 or len(b) < 3:
        return None
    va, vb = st.variance(a) / len(a), st.variance(b) / len(b)
    return round((st.mean(a) - st.mean(b)) / math.sqrt(va + vb), 2) if va + vb > 0 else None


def block(rows, stop_key="s16"):
    """rows: [{band, t, ret: {s16, s8, none}, stopped: {...}}] → per band stats + t vs B0"""
    out = {}
    b0 = [r["ret"][stop_key] for r in rows if r["band"] == "B0"]
    for name, _, _ in BANDS:
        rs = [r for r in rows if r["band"] == name]
        xs = [r["ret"][stop_key] for r in rs]
        out[name] = {**stat(xs, [r["stopped"][stop_key] for r in rs]), "gapped": sum(1 for r in rs if r.get("gapped"))}
        if name != "B0":
            out[name]["t_vs_B0"] = welch(xs, b0)
    return out


def verdict(rows):
    """the pre-registered test on the primary set (stop −16 %)"""
    rows = sorted(rows, key=lambda r: r["t"])
    if not rows:
        return {"verdict": "нет данных"}
    decs = sorted({(r["t"], r["dec"]) for r in rows})
    mid = decs[len(decs) // 2][0]
    allb, h1, h2 = block(rows), block([r for r in rows if r["t"] < mid]), block([r for r in rows if r["t"] >= mid])
    res = {}
    for name, _, _ in BANDS[1:]:
        m = lambda b: b.get(name, {}).get("mean")       # noqa: E731
        both = all(m(b) is not None and b["B0"].get("mean") is not None and m(b) > b["B0"]["mean"] for b in (h1, h2))
        t = allb[name].get("t_vs_B0")
        res[name] = {"both_halves": both, "t": t, "pass": bool(both and t is not None and t >= T_PASS)}
    return {"mid": mid, "all": allb, "h1": h1, "h2": h2, "bands": res,
            "verdict": "прошло: " + ", ".join(k for k, v in res.items() if v["pass"]) if any(v["pass"] for v in res.values()) else "не прошло"}
