"""Experiment program (owner 22.11.2026 16:18: «continuous, bold search for a better strategy — experiments ON PAPER»). Backtest of
the variants of the whale copy stream on our stored whale history, apples to apples. RESEARCH ONLY — no real-account change.

Pre-registered (written before any result was seen, 22.11 ~17:00 Athens):
- Base stream = the decisions of the traders of the current follow lock (web/whales_top.json following + follow_lock.passing, the
  traders the real desk copies or may copy), sources bn + okx, opened in the last 60 days before the newest closed trip in ws_trips.
  Decision = one trader's entries in the same coin + side within 4 h (as every whale study). Closed trips only (bias: open losers
  are missing — the same for every variant, the comparison is what matters).
- Copy = entry at the open of the first Binance 1m bar ≥ whale open + 2 min, exit at the open of the first 1m bar ≥ whale close
  + 2 min, unless a stop / rule exits first; 0.11 % a round trip + funding paid; result = % of the copy's notional (no leverage).
  Stops are checked on 1m highs / lows; a gap through the stop fills at the bar's open. No look-ahead: every filter uses only data
  before the entry (BTC 4h bars completed before the entry, ATR of the 1h bars before the entry, other traders' trips opened before).
- Variants:
    base      stop −16 % (live today)
    a_regime  base, but no alt SHORT when the last completed BTC 4h close > its EMA20 and the EMA20 is rising; no alt LONG when
              the close < EMA20 and the EMA20 is falling (alt = any coin but BTC)
    c_be1r    stop −16 %, moved to the entry once the price went +16 % (1R) our way
    c_trail   stop −16 % plus a trailing stop 1.5 × ATR(14, 1h) under (over) the best price since the entry
    c_time48  stop −16 % plus exit after 48 h
    c_stop8   stop −8 %
    d_major2  base, only when at the entry at least one OTHER lock trader holds the same coin and side (≥ 2 whales)
    e_majors  base, only BTC / ETH / SOL;   e_btc  only BTC
  b (smart-money veto) has no stored history of the radar → forward on paper only (cnq/agents/exp_desks.py).
- Report per variant: n, win share, PF, mean, sum, max drawdown of the cumulative sum (equal notional per trade, in % points),
  the two halves of time (median open time of the base set): mean per half, and the paired difference vs base on common decisions.
- A variant is «promising» only if: PF > base PF, max dd ≤ base dd, mean > base mean in BOTH halves, n ≥ 30. Promising ≠ real: it
  goes to forward paper; the real account changes only after ≥ 30 forward trades better than the real source and the owner's yes.
Usage: exp_variants.py DB OUT.json"""
import bisect, json, math, os, sqlite3, statistics as st, sys, time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_delay as D  # noqa: E402

M1, H, DAY, COST = D.M1, D.H, D.DAY, D.COST
WINDOW, LAG = 60 * DAY, 2
H4 = 4 * H
MAJORS = {"BTC", "ETH", "SOL"}
VARIANTS = ["base", "a_regime", "c_be1r", "c_trail", "c_time48", "c_stop8", "d_major2", "e_majors", "e_btc"]


def load_lock():
    try:
        w = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "web", "whales_top.json")))
    except Exception:  # noqa: BLE001
        return set()
    fol = {f.get("addr") for f in w.get("following") or [] if isinstance(f, dict)}
    return fol | set((w.get("follow_lock") or {}).get("passing") or {})


def sim(S, side, t_in, t_out, stop=16.0, be_r=False, trail=None, tmax_h=None):
    """one copy on 1m bars: (result %, exit kind) or None"""
    T = S["t"]
    i, j = bisect.bisect_left(T, t_in), bisect.bisect_left(T, t_out)
    if i >= len(T) or j >= len(T) or j <= i or T[i] - t_in > 2 * M1 or T[j] - t_out > 2 * M1:
        return None
    s = 1 if side == "LONG" else -1
    a = S["k"][T[i]][0]
    lvl = a * (1 - s * stop / 100)
    best = a
    b, kind, t_end = S["k"][T[j]][0], "whale", T[j]
    if tmax_h:
        jt = bisect.bisect_left(T, T[i] + int(tmax_h * H))
        if jt < j:
            j, b, kind, t_end = jt, S["k"][T[jt]][0], "time", T[jt]
    for q in range(i, j):
        o, h, l = S["k"][T[q]]
        if (s > 0 and l <= lvl) or (s < 0 and h >= lvl):
            b = o if q > i and ((s > 0 and o < lvl) or (s < 0 and o > lvl)) else lvl
            kind, t_end = "stop", T[q]
            break
        best = max(best, h) if s > 0 else min(best, l)
        if be_r and s * (best / a - 1) * 100 >= stop:
            lvl = max(lvl, a) if s > 0 else min(lvl, a)
        if trail:
            tl = best - s * trail
            lvl = max(lvl, tl) if s > 0 else min(lvl, tl)
    f = sum(r for tt, r in S["fund"] if T[i] < tt <= t_end) * 100 * s
    return s * (b / a - 1) * 100 - COST - f, kind


def atr_1h(S, t, n=14):
    """ATR of the n 1h bars completed before t (from 1m bars)"""
    T = S["t"]
    t0 = (t // H) * H
    bars = []
    for k in range(n + 1, 0, -1):
        a, b = bisect.bisect_left(T, t0 - k * H), bisect.bisect_left(T, t0 - (k - 1) * H)
        if b - a < 30:
            continue
        hs = [S["k"][T[q]][1] for q in range(a, b)]
        ls = [S["k"][T[q]][2] for q in range(a, b)]
        bars.append((max(hs), min(ls), S["k"][T[b - 1]][0]))
    if len(bars) < n // 2:
        return None
    trs = [bars[0][0] - bars[0][1]] + [max(bars[q][0], bars[q - 1][2]) - min(bars[q][1], bars[q - 1][2]) for q in range(1, len(bars))]
    return sum(trs[-n:]) / len(trs[-n:])


def btc_regime(B):
    """{4h bar start: (close, ema20, ema20 of the bar before)} from BTCUSDT 1m bars"""
    T = B["t"]
    closes = {}
    for t in T:
        closes[(t // H4) * H4] = B["k"][t][0]                    # the last 1m open in the bar ≈ its close
    ks = sorted(closes)
    out, e, prev = {}, None, None
    for k in ks:
        c = closes[k]
        e = c if e is None else e + (c - e) * 2 / 21
        out[k] = (c, e, prev)
        prev = e
    return out


def regime_ok(R, coin, side, t):
    if coin == "BTC":
        return True
    k = (t // H4) * H4 - H4                                      # the last 4h bar COMPLETED before the entry
    v = R.get(k)
    if not v or v[2] is None:
        return True
    c, e, pe = v
    if side == "SHORT" and c > e and e > pe:
        return False
    if side == "LONG" and c < e and e < pe:
        return False
    return True


def stat(xs):
    if not xs:
        return {"n": 0}
    pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    cum, peak, dd = 0.0, 0.0, 0.0
    for x in xs:
        cum += x
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    o = {"n": len(xs), "win": round(sum(x > 0 for x in xs) / len(xs), 3), "pf": round(pos / neg, 2) if neg else None,
         "mean": round(st.mean(xs), 3), "sum": round(sum(xs), 1), "max_dd": round(dd, 1)}
    if len(xs) >= 2 and st.stdev(xs):
        o["t"] = round(st.mean(xs) / (st.stdev(xs) / math.sqrt(len(xs))), 2)
    return o


def main():
    db, out = sys.argv[1], sys.argv[2]
    lock = load_lock()
    c = sqlite3.connect(db)
    rows = c.execute("SELECT src, trader, coin, side, open_t, close_t, entry, exit, notional FROM ws_trips "
                     "WHERE src IN ('bn','okx') AND close_t > 0 AND coin IS NOT NULL AND coin != '' ORDER BY src, trader, open_t").fetchall()
    if not rows:
        json.dump({"error": "no rows"}, open(out, "w")); print("::error::no rows"); return
    t_end = max(r[5] for r in rows)
    since = t_end - WINDOW
    by = defaultdict(list)
    for r in rows:
        tid = r[1] if str(r[1]).startswith(r[0] + ":") else f"{r[0]}:{r[1]}"
        by[tid].append(r)
    eps = []
    for tid, lst in by.items():
        if tid not in lock:
            continue
        cur = {}
        for r in lst:
            k = (r[2].upper(), r[3]); e = cur.get(k)
            if e and r[4] - e["open_t"] <= 4 * H:
                e["g"].append(r); e["close_t"] = max(e["close_t"], r[5])
            else:
                e = {"trader": tid, "coin": r[2].upper(), "side": r[3], "open_t": r[4], "close_t": r[5], "g": [r]}; cur[k] = e
                if r[4] >= since:
                    eps.append(e)
    # other lock traders' open trips (for d: ≥ 2 whales on the same coin and side at the entry, opened before it)
    trips = defaultdict(list)
    for tid, lst in by.items():
        if tid in lock:
            for r in lst:
                trips[(r[2].upper(), r[3])].append((r[4], r[5], tid))
    print(f"rows {len(rows)}, lock traders {len(lock)}, decisions {len(eps)}, window "
          f"{time.strftime('%Y-%m-%d', time.gmtime(since / 1000))} → {time.strftime('%Y-%m-%d', time.gmtime(t_end / 1000))}", flush=True)
    syms = defaultdict(list)
    for e in eps:
        syms[e["coin"] + "USDT"].append((e["open_t"] - 20 * H, e["close_t"]))
    syms["BTCUSDT"].append((since - 10 * DAY, t_end))
    data = {}
    for n, (sym, spans) in enumerate(sorted(syms.items(), key=lambda kv: -len(kv[1]))):
        data[sym] = D.load_sym(sym, spans)
        if n % 10 == 0:
            print(f"candles {n + 1}/{len(syms)} {sym} {'ok' if data[sym] else 'none'}", flush=True)
    R = btc_regime(data["BTCUSDT"]) if data.get("BTCUSDT") else {}
    res, nodata = [], 0
    for e in sorted(eps, key=lambda x: x["open_t"]):
        S = data.get(e["coin"] + "USDT")
        if not S:
            nodata += 1; continue
        t_in, t_out = e["open_t"] + LAG * M1, e["close_t"] + LAG * M1
        base = sim(S, e["side"], t_in, t_out)
        if base is None:
            nodata += 1; continue
        r = {"t": e["open_t"], "coin": e["coin"], "side": e["side"], "trader": e["trader"], "base": base[0]}
        if regime_ok(R, e["coin"], e["side"], t_in):
            r["a_regime"] = base[0]
        r["c_be1r"] = (sim(S, e["side"], t_in, t_out, be_r=True) or (None,))[0]
        atr = atr_1h(S, t_in)
        r["c_trail"] = (sim(S, e["side"], t_in, t_out, trail=1.5 * atr) or (None,))[0] if atr else None
        r["c_time48"] = (sim(S, e["side"], t_in, t_out, tmax_h=48) or (None,))[0]
        r["c_stop8"] = (sim(S, e["side"], t_in, t_out, stop=8.0) or (None,))[0]
        if any(o_ < e["open_t"] < c_ and tid != e["trader"] for o_, c_, tid in trips[(e["coin"], e["side"])]):
            r["d_major2"] = base[0]
        if e["coin"] in MAJORS:
            r["e_majors"] = base[0]
        if e["coin"] == "BTC":
            r["e_btc"] = base[0]
        res.append(r)
    mid = st.median([r["t"] for r in res]) if res else 0
    O = {"window": [time.strftime("%Y-%m-%d", time.gmtime(since / 1000)), time.strftime("%Y-%m-%d", time.gmtime(t_end / 1000))],
         "lock_traders": len(lock), "decisions": len(res), "no_data": nodata, "variants": {}}
    B = stat([r["base"] for r in res])
    for v in VARIANTS:
        xs = [r[v] for r in res if r.get(v) is not None]
        h1 = [r[v] for r in res if r.get(v) is not None and r["t"] < mid]
        h2 = [r[v] for r in res if r.get(v) is not None and r["t"] >= mid]
        b1 = [r["base"] for r in res if r.get(v) is not None and r["t"] < mid]
        b2 = [r["base"] for r in res if r.get(v) is not None and r["t"] >= mid]
        s = stat(xs)
        s["half1_mean"], s["half2_mean"] = (round(st.mean(h1), 3) if h1 else None), (round(st.mean(h2), 3) if h2 else None)
        s["base_half1_mean"], s["base_half2_mean"] = (round(st.mean(b1), 3) if b1 else None), (round(st.mean(b2), 3) if b2 else None)
        s["paired_vs_base"] = stat([r[v] - r["base"] for r in res if r.get(v) is not None])
        bh1, bh2 = (round(st.mean([r["base"] for r in res if r["t"] < mid]), 3) if res else None,
                    round(st.mean([r["base"] for r in res if r["t"] >= mid]), 3) if res else None)
        s["promising"] = bool(v != "base" and s["n"] >= 30 and (s.get("pf") or 0) > (B.get("pf") or 0) and s["max_dd"] <= B.get("max_dd", 0)
                              and s["half1_mean"] is not None and s["half2_mean"] is not None and bh1 is not None
                              and s["half1_mean"] > bh1 and s["half2_mean"] > bh2)
        O["variants"][v] = s
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"::notice title=exp n::{O['decisions']} decisions, lock traders {len(lock)}, window {O['window']}, no data {nodata}")
    for v, s in O["variants"].items():
        print(f"::notice title=exp {v}::" + json.dumps({k: s.get(k) for k in ("n", "win", "pf", "mean", "sum", "max_dd", "t", "half1_mean",
                                                                              "half2_mean", "promising")}, ensure_ascii=False)
              + " paired " + json.dumps({k: s["paired_vs_base"].get(k) for k in ("n", "mean", "t")}))


if __name__ == "__main__":
    main()
