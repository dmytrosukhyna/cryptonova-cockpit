"""«Уровни · шире стоп» (owner 27.11.2026 09:38). RESEARCH ONLY. The EXACT live desk (levels_study.run_variant logic: the desk's
levels, signals, target R, max 72 bars, one position per coin) with only the stop changed (cnq/agents/levels_wide.py):
desk (S − 0.5 ATR) | a1.5 | a2 | a3 | swing | swing24. Data as levels_v2_study (30 coins, 15m → H1, 2022-11-20 → 2026-11-04), halves
at 2024-11-12; reported on all 30 / original 18 / new 12. 6 variants → Bonferroni 0.05 / 6 two-sided → t ≥ 2.64 on the 2nd half.
Also the depth of the winners: how far below the level (in ATR(H1)) the price went before a winning trade reached its target —
which stop would have kept the winners — and, for the desk's stops, the share stopped and then at the target within 72 bars.
Usage: levels_wide_study.py core18.npz NEW12.npz OUT.json"""
import json, math, os, statistics as st, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cnq.agents import levels_desk as LD, levels_wide as LW  # noqa: E402
import levels_v2_study as V2  # noqa: E402

H, MID = LD.H, V2.MID
NEW12 = set(V2.NEW12)
Z = 2.64


def run(D, PP, v, mode):
    md, side, ex = v.split("|")
    out = []
    for coin, K in D.items():
        P = PP[coin]
        t, o, h, l, c = P["t"], P["o"], P["h"], P["l"], P["c"]
        n, Lc = len(c), P["_L"]
        i = 300
        while i < n - 2:
            sigs = LD.signals(P, i, Lc[i - 1], Lc[i], md, side)
            if not sigs or sigs[0]["kind"] != "market":
                i += 1
                continue
            sg, maxb = sigs[0], None
            entry = o[i + 1]
            if mode != "desk":
                sg, maxb = LW.wide(P, i, sg, side, mode, entry)
                if not sg:
                    i += 1
                    continue
            p = LD.plan(sg, side, ex, entry)
            if not p:
                i += 1
                continue
            tr = {"side": side, "entry": entry, "stop": p["stop"], "tgt": p["tgt"], "maxb": maxb or p["maxb"], "trail": False, "risk": p["risk"], "bars": 0}
            k, res = i + 1, None
            while k < n:
                res = LD.step_trade(tr, (o[k], h[k], l[k], c[k]), P["atr"][k])
                if res:
                    break
                k += 1
            if not res:
                break
            px, why = res
            a = P["atr"][i]
            lvl = sg["lvl"]
            depth = ((lvl - min(l[i + 1:k + 1])) if side == "L" else (max(h[i + 1:k + 1]) - lvl)) / a
            swept = None
            if why == "стоп":
                swept = any((h[q] >= p["tgt"]) if side == "L" else (l[q] <= p["tgt"]) for q in range(k + 1, min(n, k + 73)))
            out.append({"coin": coin, "t": t[i], "exit_t": t[k] + H, "ret": LD.ret_pct(side, entry, px, tr["bars"]), "why": why,
                        "risk_pct": p["risk"] / entry * 100, "depth_atr": depth, "swept": swept, "bars": tr["bars"]})
            i = k + 1
    return out


def summ(xs):
    if not xs:
        return {"n": 0}
    r = np.array([x["ret"] for x in xs])
    d = np.array([x["exit_t"] // LD.DAY for x in xs])
    u, inv = np.unique(d, return_inverse=True)
    dm = np.bincount(inv, weights=r) / np.bincount(inv)
    t_ = float(dm.mean() / (dm.std(ddof=1) / math.sqrt(dm.size))) if dm.size > 2 and dm.std(ddof=1) > 0 else None
    gp, gl = float(r[r > 0].sum()), float(-r[r < 0].sum())
    return {"n": int(r.size), "mean": round(float(r.mean()), 3), "win": round(float((r > 0).mean()), 3), "pf": round(gp / gl, 2) if gl else None,
            "t": round(t_, 2) if t_ is not None else None, "stopped": round(sum(x["why"] == "стоп" for x in xs) / len(xs), 3),
            "stop_pct_med": round(st.median(x["risk_pct"] for x in xs), 2), "hold_med_h": st.median(x["bars"] for x in xs)}


def main():
    core, new, outp = sys.argv[1:4]
    D = V2.load(core)
    D.update(V2.load(new))
    D = {c_: K for c_, K in D.items() if len(K) >= 2000}
    PP = {}
    for coin, K in D.items():
        t, o, h, l, c, v = (list(x) for x in zip(*K))
        P = LD.prep(t, o, h, l, c, v)
        P["_L"] = [LD.levels_at(P, i) if i >= 200 else [] for i in range(len(c))]
        PP[coin] = P
        print("levels", coin, flush=True)
    v = (LD.cfg() or {}).get("variant") or "range|L|sym"
    res = {"variant": v, "modes": {}}
    for mode in ("desk",) + LW.MODES:
        tr = run(D, PP, v, mode)
        row = {}
        for sn, f in (("all30", lambda x: True), ("orig18", lambda x: x["coin"] not in NEW12), ("new12", lambda x: x["coin"] in NEW12)):
            xs = [x for x in tr if f(x)]
            row[sn] = {"is": summ([x for x in xs if x["t"] < MID]), "oos": summ([x for x in xs if x["t"] >= MID])}
        a = row["all30"]
        row["passes"] = bool(a["is"].get("mean", -1) > 0 and a["oos"].get("mean", -1) > 0 and (a["oos"].get("t") or 0) >= Z and a["oos"]["n"] >= 100
                             and (row["new12"]["is"].get("mean", 0) + row["new12"]["oos"].get("mean", 0)) > 0)
        st_ = [x for x in tr if x["why"] == "стоп"]
        row["swept_share"] = round(sum(bool(x["swept"]) for x in st_) / len(st_), 3) if st_ else None
        win = [x["depth_atr"] for x in tr if x["ret"] > 0]          # how deep below the level the winners went (ATR)
        if win:
            res.setdefault("winners_depth_atr", {})[mode] = {"n": len(win), **{f"p{q}": round(float(np.percentile(win, q)), 2) for q in (50, 75, 90, 95)},
                                                             "under": {str(k): round(sum(d < k for d in win) / len(win), 3) for k in (0.5, 1, 1.5, 2, 3)}}
        if mode == "desk":
            sw = [x["depth_atr"] for x in st_ if x["swept"]]
            res["swept_depth_atr"] = {"n": len(sw), **({f"p{q}": round(float(np.percentile(sw, q)), 2) for q in (50, 75, 90)} if sw else {})}
        res["modes"][mode] = row
        print(f"::notice title=wide {mode}::" + json.dumps({k: row[k] for k in ("all30", "passes", "swept_share")} | {"new12": row["new12"]})[:4000], flush=True)
    print("::notice title=wide winners depth::" + json.dumps({"winners": res.get("winners_depth_atr"), "swept_stops": res.get("swept_depth_atr")})[:4000])
    json.dump(res, open(outp, "w"), ensure_ascii=False, indent=1, default=float)


if __name__ == "__main__":
    main()
