"""Backlog 233 (21.11.2026): Ivan (owner pick, Bybit) — would a tighter stop for HIS copies have been better than the −16 %
protective stop? Research only, calculation for the owner; real rules are not touched.

Data: every Ivan copy the «Киты» desk took (web/whales_live.json desk.closed + desk.open, whale «ivan:bybit»), our entry time and
price, our exit time (trader closed / our stop) or, for open copies, the desk's current mark. Path: Binance USD-M 5m candles
(data.binance.vision; kPEPE → 1000PEPEUSDT). Costs 0.11 % per copy + funding, as in bn_whale_stops.py.

Variants (pre-registered before any result was seen):
 A  as is: −16 % from our entry (reference).
 B  −8 % from our entry, Ivan only (primary).
 info: −10 %, −12 %, and the 219 time stop (open ≥ 72 h and move ≤ −5 % → exit at that bar).
 C  «no new Ivan copies while his open loss > 20 % of his account» needs his equity history — the server keeps only the current
    snapshot (fut_owner.json ivan.stats), so C is reported as not computable.
Pass (stated in the backlog): better than A in BOTH halves (by entry time) and n ≥ 20; with n < 20 the result is a calculation
for the owner, not a pass.
Usage: bn_whale_ivan233.py DB(unused) OUT.json"""
import json, os, statistics as st, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B

H = 3_600_000
SYM = {"kPEPE": "1000PEPEUSDT"}


def walk(S, side, t_in, t_out, stop, time_stop=None):
    """return (ret %, exit kind) — exit at t_out (or last bar) unless the stop / time stop fires first"""
    i = B.bar_at(S, t_in)
    if i is None:
        return None, "no data"
    j = B.bar_at(S, t_out) if t_out else None
    if j is None:
        j = len(S["t"]) - 1
    s = 1 if side == "LONG" else -1
    a = S["k"][S["t"][i]][0]
    lvl = a * (1 - s * stop / 100)
    b, kind, t_end = S["k"][S["t"][j]][0], "as trader", S["t"][j]
    for q in range(i, j):
        o, h, l, c = S["k"][S["t"][q]]
        if (s > 0 and l <= lvl) or (s < 0 and h >= lvl):
            b = o if q > i and ((s > 0 and o < lvl) or (s < 0 and o > lvl)) else lvl
            kind, t_end = f"stop {stop}", S["t"][q]
            break
        if time_stop and S["t"][q] - S["t"][i] >= time_stop[0] * H and s * (o / a - 1) * 100 <= time_stop[1]:
            b, kind, t_end = o, "time stop", S["t"][q]
            break
    f = sum(r for tt, r in S["fund"] if S["t"][i] < tt <= t_end) * 100 * s
    return s * (b / a - 1) * 100 - B.COST - f, kind


def main():
    out = sys.argv[2] if len(sys.argv) > 2 else "bn_whale_ivan233.json"
    D = json.load(open("web/whales_live.json"))["desk"]
    rows = [dict(c, open_now=False) for c in D["closed"] if str(c.get("whale", "")).startswith("ivan")]
    rows += [dict(c, open_now=True) for c in D["open"] if str(c.get("whale", "")).startswith("ivan")]
    rows.sort(key=lambda c: c["opened_t"])
    V = {"A16": (16, None), "B8": (8, None), "i10": (10, None), "i12": (12, None), "iT72": (16, (72, -5.0))}
    res = []
    for c in rows:
        sym = SYM.get(c["coin"], c["coin"] + "USDT")
        t_out = None if c["open_now"] else c["closed_t"]
        S = B.load_sym(sym, c["opened_t"] - H, (t_out or c["opened_t"] + 30 * 24 * H))
        r = {"coin": c["coin"], "side": c["side"], "opened": c["opened_t"], "closed": t_out, "open_now": c["open_now"],
             "notional": c["notional"], "desk_pct": round(100 * (c.get("pnl") if c.get("pnl") is not None else c.get("upnl", 0)) / c["notional"], 2),
             "desk_why": c.get("why")}
        if not S:
            r["err"] = f"no candles {sym}"
            res.append(r)
            continue
        s = 1 if c["side"] == "LONG" else -1
        i = B.bar_at(S, c["opened_t"])
        j = B.bar_at(S, t_out) if t_out else len(S["t"]) - 1
        if i is not None and j is not None and j > i:
            a = S["k"][S["t"][i]][0]
            r["mae_pct"] = round(min(s * ((S["k"][S["t"][q]][2] if s > 0 else S["k"][S["t"][q]][1]) / a - 1) * 100 for q in range(i, j + 1)), 2)
            r["last_bar"] = S["t"][-1]
        for name, (stop, ts) in V.items():
            ret, kind = walk(S, c["side"], c["opened_t"], t_out, stop, ts)
            r[name] = round(ret, 2) if ret is not None else None
            r[name + "_kind"] = kind
        if c["open_now"]:
            # candles end yesterday: a copy that has not hit the variant's stop by then is marked at the desk's current price
            for name in V:
                if r.get(name + "_kind") == "as trader":
                    r[name] = round(r["desk_pct"], 2)
                    r[name + "_kind"] = "open, desk mark"
        res.append(r)
    ok = [r for r in res if r.get("A16") is not None]
    mid = ok[len(ok) // 2]["opened"] if ok else 0
    summ = {}
    for name in V:
        d = [r[name] - r["A16"] for r in ok]
        usd = sum(r[name] / 100 * r["notional"] for r in ok)
        summ[name] = {"n": len(ok), "sum_pct": round(sum(r[name] for r in ok), 2), "mean_pct": round(st.mean(r[name] for r in ok), 2) if ok else None,
                      "desk_usd": round(usd, 2), "diff_vs_A_pct": round(sum(d), 2),
                      "h1_diff": round(sum(r[name] - r["A16"] for r in ok if r["opened"] < mid), 2),
                      "h2_diff": round(sum(r[name] - r["A16"] for r in ok if r["opened"] >= mid), 2),
                      "winners_cut": sum(1 for r in ok if r["A16"] > 0 and r[name] < r["A16"] - 0.5)}
    O = {"n": len(res), "n_ok": len(ok), "summary": summ, "copies": res, "C": "not computable: only the current snapshot of Ivan's account is kept",
         "verdict": "расчёт для владельца (n < 20)" if len(ok) < 20 else "see halves"}
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"::notice title=ivan233 n::{len(res)} copies, with candles {len(ok)}")
    for name, s_ in summ.items():
        print(f"::notice title=ivan233 {name}::" + json.dumps(s_, ensure_ascii=False))
    for r in res:
        print("::notice title=ivan233 copy::" + json.dumps({k: r.get(k) for k in ("coin", "open_now", "desk_pct", "mae_pct", "A16", "A16_kind", "B8", "B8_kind", "i12", "iT72", "iT72_kind", "err")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
