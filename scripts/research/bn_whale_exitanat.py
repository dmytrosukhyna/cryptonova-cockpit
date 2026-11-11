"""Backlog 135 (side result of 126): exit anatomy of the locked-list whales and ≤ 3 exit rules derived from it.
Research only — reads ws_trips from the whale study's database (Actions cache), writes nothing to it. Same machinery as
bn_whale_trail.py (Binance 5m candles from data.binance.vision, copy +2 min, exit +2 min after the whale, stop −16 %,
costs 0.11 % a round trip + funding, result in R with a 16 % risk unit).

Pre-registered before any result was seen (11.11 21:20 Athens):
 Part A — anatomy, only the 5 Binance whales of the locked list (in-sample, description only). Per decision (entries in one coin and
   side within 4 h): exit move r, best move before the exit MFE, hold. Class: «near best» r > 0 and MFE − r ≤ 0.25·MFE;
   «after giveback» r > 0 and MFE − r > 0.25·MFE; «loss» r ≤ 0. «Common rule» exists only if ≥ 4 of the 5 whales have ≥ 50 % of
   their winning exits «near best» (take-profit style) — otherwise the result is «no common exit rule».
 Part B — three exit rules, levels fixed from the 5 list whales only (Part A), tested ONLY on the other Binance traders (≥ 20
   decisions; the 5 list whales excluded → out of sample by trader), against BASE = exit with the whale / stop −16 %:
   TP   full exit at +TP %, TP = median over the 5 whales of their median winning exit move;
   TIME exit at T hours if the whale is still in, T = median over the 5 whales of the 75th percentile hold of their winning decisions;
   GB   once the move reaches +TP %, exit when price gives back half of the best move (stop at entry + 0.5·best move).
 Pass (as backlog 130): paired difference vs BASE with t ≥ 2.5 in BOTH time halves, ≥ 0 on walk-forward «good» traders (h2),
 worst run not worse than 1.25× BASE. Three variants. A pass means a DEMO desk only; real-money rules — owner's decision.
Usage: bn_whale_exitanat.py DB OUT.json"""
import json, math, os, sqlite3, statistics as st, sys, time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B  # noqa: E402
from bn_whale_trail import paired  # noqa: E402

COST, H, DAY = B.COST, B.H, B.DAY
UNIT = 16.0
LIST = {"5215286443298625280", "5236187913165742336", "5219234690161308161", "5214137267197226241", "5221883157531439360"}
VAR = ["BASE", "TP", "TIME", "GB"]


def key(tr):
    return str(tr).split(":")[-1]


def anatomy(S, side, t0, t1):
    """best favourable move (%) between whale open and whale close"""
    i, j = B.bar_at(S, t0), B.bar_at(S, t1)
    if i is None or j is None or j <= i:
        return None
    s = 1 if side == "LONG" else -1
    a = S["k"][S["t"][i]][0]; best = 0.0
    for q in range(i, j):
        o, h, l, c = S["k"][S["t"][q]]
        best = max(best, s * ((h if s > 0 else l) / a - 1) * 100)
    return best


def path_ret(S, side, t_in, t_out, var, TP, T):
    if var == "TIME":
        t_out = min(t_out, t_in + T * H)
    i, j = B.bar_at(S, t_in), B.bar_at(S, t_out)
    if i is None or j is None or j <= i:
        return None
    s = 1 if side == "LONG" else -1
    a = S["k"][S["t"][i]][0]
    stop = a * (1 - s * 0.16); best = a
    tp = a * (1 + s * TP / 100)
    exit_px, t_end = None, S["t"][j]
    for q in range(i, j):
        o, h, l, c = S["k"][S["t"][q]]
        adverse, fav = (l, h) if s > 0 else (h, l)
        if (adverse <= stop) if s > 0 else (adverse >= stop):       # conservative: adverse extreme first
            gap = (o < stop) if s > 0 else (o > stop)
            exit_px = o if (q > i and gap) else stop; t_end = S["t"][q]; break
        if var == "TP" and ((fav >= tp) if s > 0 else (fav <= tp)):
            exit_px = o if ((o > tp) if s > 0 else (o < tp)) and q > i else tp; t_end = S["t"][q]; break
        best = max(best, fav) if s > 0 else min(best, fav)
        mv = s * (best / a - 1) * 100
        if var == "GB" and mv >= TP:                                  # effective from the next bar
            g = a * (1 + s * 0.5 * mv / 100)
            if s * (g - stop) > 0:
                stop = g
    if exit_px is None:
        exit_px = S["k"][S["t"][j]][0]
    f = sum(r for tt, r in S["fund"] if S["t"][i] < tt <= t_end) * 100 * s
    return s * (exit_px / a - 1) * 100 - COST - f


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None


def main():
    db, out = sys.argv[1], sys.argv[2]
    c = sqlite3.connect(db)
    rows = c.execute("SELECT trader, coin, side, open_t, close_t, entry, exit, pnl, notional, lever FROM ws_trips WHERE src='bn' "
                     "AND close_t > 0 ORDER BY trader, open_t").fetchall()
    by = defaultdict(list)
    for r in rows:
        by[r[0]].append(r)
    eps = []
    for tr, lst in by.items():
        cur, mine = {}, []
        for r in lst:
            k = (r[1], r[2]); e = cur.get(k)
            if e and r[3] - e["open_t"] <= 4 * H:
                e["g"].append(r)
            else:
                e = {"trader": tr, "coin": r[1], "side": r[2], "open_t": r[3], "g": [r]}; cur[k] = e; mine.append(e)
        if len(mine) >= 20 or key(tr) in LIST:
            eps += mine
    for e in eps:
        g = e["g"]; w = [max(x[8] or 0, 1e-9) for x in g]
        e["close_t"] = max(x[4] for x in g)
        s = 1 if e["side"] == "LONG" else -1
        e["wret"] = sum(s * (x[6] / x[5] - 1) * 100 * wi for x, wi in zip(g, w) if x[5] and x[6]) / sum(w)
        e["hold_h"] = (e["close_t"] - e["open_t"]) / H
    syms = defaultdict(list)
    for e in eps:
        syms[e["coin"] + "USDT"].append(e)
    data = {}
    for n, (sym, es) in enumerate(sorted(syms.items(), key=lambda kv: -len(kv[1]))):
        data[sym] = B.load_sym(sym, min(x["open_t"] for x in es) - DAY, max(x["close_t"] for x in es) + DAY)
        if n % 10 == 0:
            print(f"candles {n + 1}/{len(syms)} {sym} {'ok' if data[sym] else 'none'}", flush=True)
    # Part A — anatomy of the list whales
    A, per = {}, defaultdict(list)
    for e in eps:
        if key(e["trader"]) not in LIST:
            continue
        S = data.get(e["coin"] + "USDT")
        mfe = anatomy(S, e["side"], e["open_t"], e["close_t"]) if S else None
        if mfe is None:
            continue
        r = e["wret"]; mfe = max(mfe, r)
        cls = "loss" if r <= 0 else ("near" if mfe - r <= 0.25 * mfe else "giveback")
        per[key(e["trader"])].append({"r": r, "mfe": mfe, "h": e["hold_h"], "cls": cls})
    med_win, p75h, near_ok = [], [], 0
    for w, xs in per.items():
        win = [x for x in xs if x["cls"] != "loss"]
        nr = sum(x["cls"] == "near" for x in win) / len(win) if win else 0
        near_ok += nr >= 0.5
        A[w] = {"n": len(xs), "win": round(len(win) / len(xs), 3), "near_share_of_wins": round(nr, 3),
                "med_win_r": round(st.median([x["r"] for x in win]), 2) if win else None,
                "med_win_mfe": round(st.median([x["mfe"] for x in win]), 2) if win else None,
                "med_giveback_win": round(st.median([x["mfe"] - x["r"] for x in win]), 2) if win else None,
                "med_loss_r": round(st.median([x["r"] for x in xs if x["cls"] == "loss"]), 2) if len(win) < len(xs) else None,
                "loss_mfe_med": round(st.median([x["mfe"] for x in xs if x["cls"] == "loss"]), 2) if len(win) < len(xs) else None,
                "med_hold_h": round(st.median([x["h"] for x in xs]), 1), "p75_hold_win_h": round(q([x["h"] for x in win], 0.75) or 0, 1)}
        if win:
            med_win.append(st.median([x["r"] for x in win])); p75h.append(q([x["h"] for x in win], 0.75))
    TP = round(st.median(med_win), 2) if med_win else 3.0
    T = round(st.median(p75h), 1) if p75h else 48.0
    O = {"anatomy": A, "whales_near_ge_50pct": near_ok, "common_rule": near_ok >= 4, "TP_pct": TP, "T_h": T}
    print(f"::notice title=exitanat A::{json.dumps(A, ensure_ascii=False)}")
    print(f"::notice title=exitanat levels::near≥50% at {near_ok}/5 whales, common={near_ok >= 4}, TP={TP}%, T={T}h")
    # Part B — rules on the other traders
    res = []
    for e in eps:
        if key(e["trader"]) in LIST:
            continue
        S = data.get(e["coin"] + "USDT")
        if not S:
            continue
        r = {"trader": e["trader"], "t": e["open_t"], "wret": e["wret"]}
        ok = True
        for k in VAR:
            v = path_ret(S, e["side"], e["open_t"] + 120_000, e["close_t"] + 120_000, k, TP, T)
            if v is None or not math.isfinite(v):
                ok = False; break
            r[k] = v / UNIT
        if ok:
            res.append(r)
    res.sort(key=lambda r: r["t"])
    if not res:
        json.dump({"error": "no rows", **O}, open(out, "w")); print("::error::no rows"); return
    mid = res[len(res) // 2]["t"]
    h1 = defaultdict(list)
    for r in res:
        if r["t"] < mid:
            h1[r["trader"]].append(r["wret"] - COST)
    good = {w for w, xs in h1.items() if len(xs) >= 20 and sum(x > 0 for x in xs) / len(xs) >= 0.70
            and sum(x for x in xs if x > 0) / max(1e-9, -sum(x for x in xs if x < 0)) >= 2}
    O.update({"n": len(res), "traders": len({r["trader"] for r in res}), "good_n": len(good),
              "from": time.strftime("%Y-%m-%d", time.gmtime(res[0]["t"] / 1000)), "to": time.strftime("%Y-%m-%d", time.gmtime(res[-1]["t"] / 1000))})
    parts = {"all": lambda r: True, "h1": lambda r: r["t"] < mid, "h2": lambda r: r["t"] >= mid,
             "good_h2": lambda r: r["t"] >= mid and r["trader"] in good}
    for p, f in parts.items():
        rs = [r for r in res if f(r)]
        O[p] = {k: B.stat([r[k] for r in rs]) for k in VAR}
        O[p]["vs_base"] = {k: paired([r[k] for r in rs], [r["BASE"] for r in rs]) for k in VAR if k != "BASE"}
    verdict = {}
    for k in VAR[1:]:
        t1, t2 = O["h1"]["vs_base"][k].get("t") or 0, O["h2"]["vs_base"][k].get("t") or 0
        g = O["good_h2"]["vs_base"][k].get("dR") or 0
        dd_ok = (O["all"][k].get("ddR") or 0) >= 1.25 * (O["all"]["BASE"].get("ddR") or 0)
        verdict[k] = "прошло" if (t1 >= 2.5 and t2 >= 2.5 and g >= 0 and dd_ok) else "не прошло"
    O["verdict"] = verdict
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"::notice title=exitanat n::{O['n']} decisions, {O['traders']} traders (list excluded), {O['from']} → {O['to']}, good(h1)={len(good)}")
    for p in parts:
        line = " ".join(f"{k}:{v.get('R')}/{v.get('t')}/{v.get('ddR')}/w{v.get('win')}" for k, v in O[p].items() if k in VAR)
        vb = " ".join(f"{k}:{v.get('dR')}/t{v.get('t')}/ch{v.get('changed')}" for k, v in O[p]["vs_base"].items())
        print(f"::notice title=exitanat {p}::{line} || vs BASE {vb}")
    print(f"::notice title=exitanat verdict::{verdict}")


if __name__ == "__main__":
    main()
