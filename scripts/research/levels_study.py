"""Backlog (owner 19.11.2026 23:42): «Торговля от уровней» — backtest of the 21 pre-registered variants (cnq/agents/levels_desk.py)
and the owner's asymmetry question («падения резкие, рост долгий»). Research only. PRE-REGISTERED with levels_desk.py, before any run:

Data: lab-data-core18 (Binance USDT-M perps, 15m → H1, 2022-11-20 → 2026-11-04): BTC, ETH, SOL + 15 liquid alts (LINK, ARB, LTC,
OP, DOT, BNB, NEAR, XRP, BCH, DOGE, TRX, ADA, SUI, AVAX, APT). Binance stands in for OKX (same coins; OKX history is not stored).
Halves: IS = before 2024-11-12 00:00 UTC (the middle), OOS = from then on.
Per variant: n, mean / median % per trade (after 0.10 % + funding 0.01 %/8 h), win share, worst, share stopped, t per trade and
t of daily means (trades grouped by exit day), for IS / OOS / all.
Random-entry null (OOS): 200 repetitions; the same number of trades on the same coins, random OOS bars, same side, the variant's
median stop and target distances in ATR, same max bars and trailing → the distribution of mean % per trade.
PASS (21 variants, Bonferroni 0.05 / 21 two-sided → |z| ≥ 3.04): IS mean > 0 AND OOS mean > 0 AND OOS daily t ≥ 3.04 AND the OOS
mean above ALL 200 null means AND OOS n ≥ 100.
The desk's variant (owner: launch regardless of the result): the best IS mean per trade among variants with IS n ≥ 100 —
chosen on IS only, reported on OOS.
Asymmetry: per coin and pooled, both halves — skewness of H1 / H4 / D1 log returns; zigzag legs on H1 closes (5 % and 10 %
reversal): count, median hours, median speed %/h, up vs down; first passage: from every 24th H1 bar, hours to −X % vs +X %
(X = 5, 10) within 30 days.
Portfolio of the desk's variant on OOS: $10 000, 1 % risk per trade, ≤ 5 positions, one per coin.
Usage: levels_study.py core18.npz OUT.json"""
import json, math, os, random, statistics as st, sys
from collections import defaultdict
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from cnq.agents import levels_desk as LD

H, DAY = LD.H, LD.DAY
MID = 1_731_369_600_000          # 2024-11-12 00:00 UTC
Z = 3.04
RNG = random.Random(20261119)


def load(path):
    d = np.load(path)
    T = d["times"].astype(np.int64)
    out = {}
    for k, s in enumerate(d["syms"]):
        o, h, l, c, v = (d[x][k].astype(float) for x in ("open", "high", "low", "close", "volume_quote"))
        ok = np.isfinite(c) & (c > 0)
        hb = T - T % H
        rows = {}
        for i in np.flatnonzero(ok):
            b = int(hb[i])
            if b not in rows:
                rows[b] = [b, o[i], h[i], l[i], c[i], v[i], 1]
            else:
                r = rows[b]; r[2] = max(r[2], h[i]); r[3] = min(r[3], l[i]); r[4] = c[i]; r[5] += v[i]; r[6] += 1
        ks = sorted(b for b, r in rows.items() if r[6] == 4)
        out[str(s)] = [rows[b][:6] for b in ks]
    return out


def tstat(xs):
    if len(xs) < 3:
        return None
    sd = st.stdev(xs)
    return round(st.mean(xs) / (sd / math.sqrt(len(xs))), 2) if sd else None


def summ(tr):
    if not tr:
        return {"n": 0}
    xs = [x["ret"] for x in tr]
    days = defaultdict(list)
    for x in tr:
        days[x["exit_t"] // DAY].append(x["ret"])
    dm = [st.mean(v) for v in days.values()]
    return {"n": len(xs), "mean": round(st.mean(xs), 3), "median": round(st.median(xs), 3), "win": round(sum(x > 0 for x in xs) / len(xs), 3),
            "worst": round(min(xs), 2), "stopped": round(sum(1 for x in tr if x["why"] == "стоп") / len(tr), 3), "t": tstat(xs),
            "t_daily": tstat(dm), "days": len(dm), "hold_h": round(st.median(x["bars"] for x in tr), 1)}


def run_variant(D, PP, v):
    mode, side, ex = v.split("|")
    trades = []
    for coin, K in D.items():
        P = PP[coin]
        n = len(K)
        t, o, h, l, c = P["t"], P["o"], P["h"], P["l"], P["c"]
        Lc = PP[coin]["_L"]
        i = 300
        while i < n - 2:
            sigs = LD.signals(P, i, Lc[i - 1], Lc[i], mode, side)
            if not sigs:
                i += 1
                continue
            sg = sigs[0]
            j = i + 1
            if sg["kind"] == "limit":
                fill = None
                for jj in range(i + 1, min(n, i + 7)):
                    if (side == "L" and l[jj] <= sg["px"]) or (side == "S" and h[jj] >= sg["px"]):
                        fill = jj; break
                if fill is None:
                    i += 1
                    continue
                entry, j = (min(o[fill], sg["px"]) if side == "L" else max(o[fill], sg["px"])), fill
            else:
                entry = o[j]
            p = LD.plan(sg, side, ex, entry)
            if not p:
                i += 1
                continue
            tr = {"side": side, "entry": entry, "stop": p["stop"], "tgt": p["tgt"], "maxb": p["maxb"], "trail": p["trail"], "risk": p["risk"], "bars": 0}
            res = None
            k = j
            while k < n:
                res = LD.step_trade(tr, (o[k], h[k], l[k], c[k]) if k > j or sg["kind"] == "limit" else (entry, h[k], l[k], c[k]), P["atr"][k])
                if res:
                    break
                k += 1
            if not res:
                break
            px, why = res
            trades.append({"coin": coin, "t": t[i], "exit_t": t[k] + H, "ret": LD.ret_pct(side, entry, px, tr["bars"]), "why": why, "bars": tr["bars"],
                           "stop_atr": p["risk"] / P["atr"][i], "tgt_atr": abs(p["tgt"] - entry) / P["atr"][i], "entry": entry, "risk_pct": p["risk"] / entry})
            i = k + 1                                       # one position per coin
    return trades


def null(D, PP, v, oos, reps=200):
    mode, side, ex = v.split("|")
    if len(oos) < 20:
        return None
    sa, ta = st.median(x["stop_atr"] for x in oos), st.median(x["tgt_atr"] for x in oos)
    by = defaultdict(int)
    for x in oos:
        by[x["coin"]] += 1
    means = []
    for _ in range(reps):
        rs = []
        for coin, m in by.items():
            P = PP[coin]
            idx = [i for i in range(len(P["t"])) if P["t"][i] >= MID and P["atr"][i]]
            for i in RNG.sample(idx[:-150], min(m, len(idx) - 150)):
                e, a = P["o"][i + 1], P["atr"][i]
                s = 1 if side == "L" else -1
                stop, tg = e - s * sa * a, e + s * ta * a
                p = {"stop": stop, "tgt": tg, "tgt2": tg}
                pl = LD.plan(p, side, "sym", e)
                if not pl:
                    continue
                maxb = 120 if (ex == "asym" and side == "L") else 24 if ex == "asym" else 72
                tr = {"side": side, "entry": e, "stop": stop, "tgt": tg, "maxb": maxb, "trail": ex == "asym" and side == "S", "risk": abs(e - stop), "bars": 0}
                for k in range(i + 1, len(P["t"])):
                    r = LD.step_trade(tr, (P["o"][k], P["h"][k], P["l"][k], P["c"][k]), P["atr"][k])
                    if r:
                        rs.append(LD.ret_pct(side, e, r[0], tr["bars"])); break
        if rs:
            means.append(st.mean(rs))
    return {"reps": len(means), "mean": round(st.mean(means), 3), "max": round(max(means), 3), "p95": round(sorted(means)[int(0.95 * len(means)) - 1], 3)}


def asym(D):
    out = {}
    for name, coins in (("BTC", ["BTC"]), ("ETH", ["ETH"]), ("SOL", ["SOL"]), ("all18", list(D))):
        blk = {}
        for half, f in (("h1", lambda t: t < MID), ("h2", lambda t: t >= MID), ("all", lambda t: True)):
            sk, legs, fp = {}, {5: {"up": [], "dn": []}, 10: {"up": [], "dn": []}}, {5: {"up": [], "dn": []}, 10: {"up": [], "dn": []}}
            for agg, step in (("H1", 1), ("H4", 4), ("D1", 24)):
                rs = []
                for c_ in coins:
                    K = [k for k in D[c_] if f(k[0])]
                    cl = [k[4] for k in K][::step]
                    rs += [math.log(cl[j] / cl[j - 1]) for j in range(1, len(cl)) if cl[j - 1] > 0]
                if len(rs) > 10:
                    m, sd = st.mean(rs), st.pstdev(rs)
                    sk[agg] = round(sum((x - m) ** 3 for x in rs) / len(rs) / sd ** 3, 3) if sd else None
            for c_ in coins:
                K = [k for k in D[c_] if f(k[0])]
                cl = [k[4] for k in K]
                for X in (5, 10):
                    # zigzag
                    piv, pi_, dirn = cl[0], 0, 0
                    ext, ei = cl[0], 0
                    for j, p in enumerate(cl):
                        if dirn >= 0 and p > ext or dirn < 0 and p < ext:
                            ext, ei = p, j
                        if dirn >= 0 and p <= ext * (1 - X / 100):
                            if dirn > 0:
                                legs[X]["up"].append((ei - pi_, (ext / piv - 1) * 100))
                            piv, pi_, dirn, ext, ei = ext, ei, -1, p, j
                        elif dirn <= 0 and p >= ext * (1 + X / 100):
                            if dirn < 0:
                                legs[X]["dn"].append((ei - pi_, (1 - ext / piv) * 100))
                            piv, pi_, dirn, ext, ei = ext, ei, 1, p, j
                    # first passage
                    for s0 in range(0, len(cl) - 720, 24):
                        p0 = cl[s0]
                        hu = hd = None
                        for j in range(s0 + 1, s0 + 720):
                            if hu is None and cl[j] >= p0 * (1 + X / 100):
                                hu = j - s0
                            if hd is None and cl[j] <= p0 * (1 - X / 100):
                                hd = j - s0
                            if hu is not None and hd is not None:
                                break
                        if hu is not None:
                            fp[X]["up"].append(hu)
                        if hd is not None:
                            fp[X]["dn"].append(hd)
            z = {}
            for X in (5, 10):
                z[str(X)] = {d: {"legs": len(legs[X][d]), "hours_med": (st.median(a for a, _ in legs[X][d]) if legs[X][d] else None),
                                 "speed_med_pct_h": (round(st.median(b / max(a, 1) for a, b in legs[X][d]), 3) if legs[X][d] else None),
                                 "first_hit_n": len(fp[X][d]), "first_hit_h_med": (st.median(fp[X][d]) if fp[X][d] else None)} for d in ("up", "dn")}
            blk[half] = {"skew": sk, "moves": z}
        out[name] = blk
    return out


def portfolio(trs):
    """$10 000, 1 % risk per trade, ≤ 5 at once, one per coin; trades in order of entry"""
    eq, peak, dd, open_ = 10_000.0, 10_000.0, 0.0, []
    for x in sorted(trs, key=lambda x: x["t"]):
        open_ = [o for o in open_ if o["exit_t"] > x["t"]]
        for o in [o for o in open_ if False]:
            pass
        if len(open_) >= 5 or any(o["coin"] == x["coin"] for o in open_):
            continue
        notional = min(0.01 * eq / x["risk_pct"], eq)
        eq += notional * x["ret"] / 100
        peak = max(peak, eq); dd = max(dd, (peak - eq) / peak)
        open_.append(x)
    return {"equity": round(eq, 2), "max_dd_pct": round(dd * 100, 2)}


def main():
    D = load(sys.argv[1])
    print({k: len(v) for k, v in D.items()}, flush=True)
    PP = {}
    for coin, K in D.items():
        t, o, h, l, c, v = (list(x) for x in zip(*K))
        P = LD.prep(t, o, h, l, c, v)
        P["_L"] = [LD.levels_at(P, i) if i >= 200 else [] for i in range(len(c))]
        PP[coin] = P
        print("levels", coin, flush=True)
    O = {"variants": {}, "asym": asym(D)}
    print("asym done", flush=True)
    for v in LD.VARIANTS:
        tr = run_variant(D, PP, v)
        is_, oos = [x for x in tr if x["t"] < MID], [x for x in tr if x["t"] >= MID]
        r = {"is": summ(is_), "oos": summ(oos), "all": summ(tr)}
        r["null"] = null(D, PP, v, oos)
        nl = r["null"] or {}
        r["pass"] = bool(r["is"].get("mean", -1) > 0 and r["oos"].get("mean", -1) > 0 and (r["oos"].get("t_daily") or 0) >= Z
                         and nl.get("max") is not None and r["oos"]["mean"] > nl["max"] and r["oos"]["n"] >= 100)
        r["portfolio_oos"] = portfolio(oos)
        O["variants"][v] = r
        print(v, "IS", r["is"].get("n"), r["is"].get("mean"), "OOS", r["oos"].get("n"), r["oos"].get("mean"), r["oos"].get("t_daily"),
              "null", nl.get("mean"), nl.get("max"), "pass", r["pass"], flush=True)
    elig = [(r["is"]["mean"], v) for v, r in O["variants"].items() if r["is"].get("n", 0) >= 100]
    O["desk_variant"] = max(elig)[1] if elig else None
    O["passed"] = [v for v, r in O["variants"].items() if r["pass"]]
    json.dump(O, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
    print("desk variant", O["desk_variant"], "passed", O["passed"])


if __name__ == "__main__":
    main()
