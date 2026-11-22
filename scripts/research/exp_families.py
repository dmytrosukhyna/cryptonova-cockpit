"""Experiment factory backtests (owner 22.11.2026 16:48) — the GROUPS / confirmation and CONTRARIAN families on our stored whale
history. RESEARCH ONLY.

Pre-registered (written before any result was seen, 22.11 ~18:30 Athens). Data: ws_trips (bn + okx, closed trips), the last 60 days
before the newest closed trip; decision = one trader's entries in the same coin + side within 4 h. Copy on Binance 1m bars: entry
at the open of the first bar ≥ the trigger time + 2 min, exit at the first bar ≥ the leader's close + 2 min, stop −16 % from our
entry, 0.11 % a round trip + funding; result = % of notional. «Lock» = web/whales_top.json following + follow_lock.passing.

GROUPS (leaders = lock traders; confirmers = every other trader in ws_trips with ≥ 10 decisions in the window):
  g_base          copy the leader at once (= today's stream)
  g_conf1_N       copy only when ≥ 1 confirmer opened the same coin + side within N h AFTER the leader (N = 1, 4, 12, 24);
                  entry at that confirmer's open + 2 min
  g_conf2_N       the same with ≥ 2 confirmers (entry at the 2nd)
  g_clu_24        ≥ 1 confirmer from the leader's own cluster within 24 h (cluster = traders whose (coin, side, day) sets overlap the
                  leader's by Jaccard ≥ 0.15, computed on the FIRST half of the window only; tested on the second half)
  p_move1         entry only after the price moved +1 % our way within 12 h of the leader's open (1m closes)
  p_retest        entry only when the price comes back to the leader's entry (± 0.3 %) within 24 h
CONTRARIAN (out of sample: traders judged on the first half, traded on the second half):
  c_worst         traders with ≥ 10 decisions in the first half, sum of their own results < 0 and win share < 45 % → the OPPOSITE
                  side of their second-half decisions
  c_neg           every trader with a negative first-half sum (≥ 10 decisions) → opposite side, second half
  c_best_follow   the mirror check: traders with a positive first-half sum → follow in the second half
Multiple testing: ≈ 15 looks → a family «wins» only with t ≥ 3 AND mean > g_base's (or > 0 for contrarian) in both halves of its
own trades. Usage: exp_families.py DB OUT.json"""
import bisect, json, math, os, sqlite3, statistics as st, sys, time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_delay as D  # noqa: E402
import exp_variants as EV  # noqa: E402

M1, H, DAY = D.M1, D.H, D.DAY
WINDOW, LAG = 60 * DAY, 2


def decisions(rows, since):
    by = defaultdict(list)
    for r in rows:
        tid = r[1] if str(r[1]).startswith(r[0] + ":") else f"{r[0]}:{r[1]}"
        by[tid].append(r)
    eps = defaultdict(list)
    for tid, lst in by.items():
        cur = {}
        for r in lst:
            k = (r[2].upper(), r[3]); e = cur.get(k)
            if e and r[4] - e["open_t"] <= 4 * H:
                e["g"].append(r); e["close_t"] = max(e["close_t"], r[5])
            else:
                e = {"trader": tid, "coin": r[2].upper(), "side": r[3], "open_t": r[4], "close_t": r[5], "g": [r]}; cur[k] = e
                if r[4] >= since:
                    eps[tid].append(e)
    for tid, es in eps.items():
        for e in es:
            g = e["g"]; w = [max(x[8] or 0, 1e-9) for x in g]; s = 1 if e["side"] == "LONG" else -1
            e["wret"] = sum(s * (x[7] / x[6] - 1) * 100 * wi for x, wi in zip(g, w) if x[6] and x[7]) / sum(w) - 0.11
            e["entry"] = sum((x[6] or 0) * wi for x, wi in zip(g, w)) / sum(w)
    return eps


def first_move(S, side, t0, pct, hours):
    T = S["t"]; i = bisect.bisect_left(T, t0); s = 1 if side == "LONG" else -1
    if i >= len(T):
        return None
    a = S["k"][T[i]][0]
    for q in range(i, bisect.bisect_left(T, t0 + hours * H)):
        o, h, l = S["k"][T[q]]
        if s * ((h if s > 0 else l) / a - 1) * 100 >= pct:
            return T[q] + M1
    return None


def first_touch(S, px, t0, hours, tol=0.003):
    T = S["t"]
    for q in range(bisect.bisect_left(T, t0), bisect.bisect_left(T, t0 + hours * H)):
        o, h, l = S["k"][T[q]]
        if l <= px * (1 + tol) and h >= px * (1 - tol):
            return T[q] + M1
    return None


def stat(xs, base_mean=None):
    s = EV.stat(xs)
    if xs:
        h = len(xs) // 2
        s["h1"] = round(st.mean(xs[:h]), 3) if h else None
        s["h2"] = round(st.mean(xs[h:]), 3) if len(xs) - h else None
        ref = 0.0 if base_mean is None else base_mean
        s["wins_bar"] = bool(len(xs) >= 30 and (s.get("t") or 0) >= 3 and s["h1"] is not None and s["h1"] > ref and s["h2"] > ref)
    return s


def main():
    db, out = sys.argv[1], sys.argv[2]
    lock = EV.load_lock()
    c = sqlite3.connect(db)
    rows = c.execute("SELECT src, trader, coin, side, open_t, close_t, entry, exit, notional FROM ws_trips "
                     "WHERE src IN ('bn','okx') AND close_t > 0 AND coin IS NOT NULL AND coin != '' ORDER BY src, trader, open_t").fetchall()
    t_end = max(r[5] for r in rows); since = t_end - WINDOW; mid = since + WINDOW // 2
    eps = decisions(rows, since)
    leaders = {t: es for t, es in eps.items() if t in lock}
    conf = {t: es for t, es in eps.items() if t not in lock and len(es) >= 10}
    opens = defaultdict(list)                                         # (coin, side) → [(open_t, trader)] of confirmers
    for t, es in conf.items():
        for e in es:
            opens[(e["coin"], e["side"])].append((e["open_t"], t))
    for v in opens.values():
        v.sort()
    # clusters on the first half
    sig = {t: {(e["coin"], e["side"], e["open_t"] // DAY) for e in es if e["open_t"] < mid} for t, es in eps.items()}
    clu = {}
    for L in leaders:
        a = sig.get(L) or set()
        clu[L] = {t for t in conf if a and sig.get(t) and len(a & sig[t]) / len(a | sig[t]) >= 0.15}
    # contrarian sets on the first half
    first = {t: [e for e in es if e["open_t"] < mid] for t, es in eps.items()}
    worst = {t for t, es in first.items() if len(es) >= 10 and sum(e["wret"] for e in es) < 0 and sum(e["wret"] > 0 for e in es) / len(es) < 0.45}
    neg = {t for t, es in first.items() if len(es) >= 10 and sum(e["wret"] for e in es) < 0}
    pos = {t for t, es in first.items() if len(es) >= 10 and sum(e["wret"] for e in es) > 0}
    print(f"rows {len(rows)}, traders {len(eps)}, leaders {len(leaders)}, confirmers {len(conf)}, worst {len(worst)}, neg {len(neg)}, pos {len(pos)}", flush=True)
    need = defaultdict(list)
    for es in leaders.values():
        for e in es:
            need[e["coin"] + "USDT"].append((e["open_t"] - H, e["close_t"] + H))
    for t in neg | pos:
        for e in eps[t]:
            if e["open_t"] >= mid:
                need[e["coin"] + "USDT"].append((e["open_t"] - H, e["close_t"] + H))
    data = {}
    for n, (sym, spans) in enumerate(sorted(need.items(), key=lambda kv: -len(kv[1]))):
        data[sym] = D.load_sym(sym, spans)
        if n % 20 == 0:
            print(f"candles {n + 1}/{len(need)} {sym} {'ok' if data[sym] else 'none'}", flush=True)
    R = defaultdict(list)
    for L, es in leaders.items():
        for e in sorted(es, key=lambda x: x["open_t"]):
            S = data.get(e["coin"] + "USDT")
            if not S:
                continue
            t_out = e["close_t"] + LAG * M1
            b = EV.sim(S, e["side"], e["open_t"] + LAG * M1, t_out)
            if b is None:
                continue
            R["g_base"].append((e["open_t"], b[0]))
            later = [(t, tr) for t, tr in opens[(e["coin"], e["side"])] if e["open_t"] < t < e["close_t"]]
            for N in (1, 4, 12, 24):
                w = [x for x in later if x[0] <= e["open_t"] + N * H]
                for k in (1, 2):
                    if len(w) >= k:
                        v = EV.sim(S, e["side"], w[k - 1][0] + LAG * M1, t_out)
                        if v:
                            R[f"g_conf{k}_{N}"].append((e["open_t"], v[0]))
            if e["open_t"] >= mid:
                w = [x for x in later if x[0] <= e["open_t"] + 24 * H and x[1] in clu.get(L, set())]
                if w:
                    v = EV.sim(S, e["side"], w[0][0] + LAG * M1, t_out)
                    if v:
                        R["g_clu_24"].append((e["open_t"], v[0]))
                R["g_base_h2"].append((e["open_t"], b[0]))
            tm = first_move(S, e["side"], e["open_t"] + LAG * M1, 1.0, 12)
            if tm and tm < t_out:
                v = EV.sim(S, e["side"], tm, t_out)
                if v:
                    R["p_move1"].append((e["open_t"], v[0]))
            tt = first_touch(S, e["entry"], e["open_t"] + LAG * M1 + 15 * M1, 24) if e.get("entry") else None
            if tt and tt < t_out:
                v = EV.sim(S, e["side"], tt, t_out)
                if v:
                    R["p_retest"].append((e["open_t"], v[0]))
    flip = {"LONG": "SHORT", "SHORT": "LONG"}
    for name, group, inv in (("c_worst", worst, True), ("c_neg", neg, True), ("c_best_follow", pos, False)):
        for t in group:
            for e in eps[t]:
                if e["open_t"] < mid:
                    continue
                S = data.get(e["coin"] + "USDT")
                if not S:
                    continue
                v = EV.sim(S, flip[e["side"]] if inv else e["side"], e["open_t"] + LAG * M1, e["close_t"] + LAG * M1)
                if v:
                    R[name].append((e["open_t"], v[0]))
    base_mean = st.mean([x for _, x in R["g_base"]]) if R["g_base"] else 0.0
    O = {"window": [time.strftime("%Y-%m-%d", time.gmtime(since / 1000)), time.strftime("%Y-%m-%d", time.gmtime(t_end / 1000))],
         "sets": {"leaders": len(leaders), "confirmers": len(conf), "worst": len(worst), "neg": len(neg), "pos": len(pos)}, "families": {}}
    for k, xs in R.items():
        xs = [x for _, x in sorted(xs)]
        O["families"][k] = stat(xs, base_mean if k.startswith(("g_", "p_")) and k not in ("g_base", "g_base_h2") else None)
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"::notice title=fam sets::{json.dumps(O['sets'])} window {O['window']}")
    ks = sorted(O["families"])
    for i in range(0, len(ks), 4):                       # ≤ 10 annotations a step: 4 families a line
        print(f"::notice title=fam {i // 4 + 1}::" + " || ".join(
            k + " " + json.dumps({x: O["families"][k].get(x) for x in ("n", "win", "pf", "mean", "max_dd", "t", "h1", "h2", "wins_bar")})
            for k in ks[i:i + 4]))


if __name__ == "__main__":
    main()
