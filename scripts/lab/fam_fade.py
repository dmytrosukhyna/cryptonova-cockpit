#!/usr/bin/env python3
"""Family "fade": app-like counter-trend signals (fade the move) and their flip (momentum, go WITH the move).

Background: 315 real third-party signals from the owner's app (Sept–Oct 2026) lost −0.11 %/trade after costs, the same plan
mirrored made +0.33 %/trade. Those signals: ~50/50 long/short, mostly "short against an uptrend", stop 1.6 %, 5 targets
0.8/1.6/2.4/3.2/4.0 % (0.5R…2.5R), market entry, mid/large alts. Question: does the flip work over 2021–2026, and with which exit?

Ideas (each tested in both directions — FADE = against the move, FLIP = with the move):
  A  sharp move: return over 4 or 16 bars (1 h / 4 h) in units of the coin's own volatility (std of 15m returns over 7 days)
  B  RSI extreme in a trend: RSI(14) on 15m or 1h-equivalent ≥ level while close > EMA200(15m) (mirror for lows)
  C  24h/72h extreme: close beyond the previous 96/288-bar high/low by m × ATR
  D  app replica: 4h move z-score ≥ z AND RSI ≥ 70 AND uptrend (mirror for downs) — "short after a rise against an uptrend"
Exits: the app plan (5 targets with BE), 3-target and 1-target versions of it, and the standard grid_exits().
Parameters are chosen on TRAIN (2021–2024) only; TEST (2025-01 → 2026-09) and per-year numbers are read afterwards.

No look-ahead: every feature at bar i uses closes/highs/lows up to and including bar i only (rolling windows end at i,
the previous-extreme uses bars i-N … i-1); the harness enters at the open of bar i+1.

Run:  python3 fam_fade.py      → results/fade.json
"""
from __future__ import annotations

import os
import sys
import time
import warnings

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import D, entries_from_mask, run, stats, grid_exits, report, passes  # noqa: E402

warnings.filterwarnings("ignore")

GAP = 16              # at most one entry per coin every 4 h (first bar of a cluster)
MIN_TRAIN_N = 300     # a variant must have at least this many TRAIN trades to be eligible as "best"
MAX_BARS = 96         # 24 h time exit for all exits (trailing version: 48 h, as in grid_exits)


# ---------------------------------------------------------------- exits
def all_exits() -> dict:
    ex = {
        "app5": dict(sl_pct=0.016, tps=[(0.5, .2), (1.0, .2), (1.5, .2), (2.0, .2), (2.5, .2)], be_after_tp1=True, max_bars=MAX_BARS),
        "app3": dict(sl_pct=0.016, tps=[(0.5, 1 / 3), (1.0, 1 / 3), (1.5, 1 / 3)], be_after_tp1=True, max_bars=MAX_BARS),
        "app1": dict(sl_pct=0.016, tps=[(0.5, 1.0)], max_bars=MAX_BARS),
    }
    ex.update(grid_exits(MAX_BARS))
    return ex


def _f(x, nd=1):
    s = f"{x:.{nd}f}".rstrip("0").rstrip(".") if nd else f"{x:.0f}"
    return s.replace(".", ",")


def exit_ru(name: str, ex: dict) -> str:
    h = ex.get("max_bars", 96) / 4
    if ex.get("sl_pct") is not None:
        sl = ex["sl_pct"] * 100
        stop = f"стоп {_f(sl)} % от входа"
        dist = lambda r: f"{_f(r * sl)} %"  # noqa: E731
    else:
        stop = f"стоп {_f(ex.get('sl_atr', 2.0))}×ATR(15м)"
        dist = lambda r: f"{_f(r)}R"  # noqa: E731
    tps = ex.get("tps") or []
    if not tps:
        tgt = "без целей, выход по времени"
    elif len(tps) == 1 and abs(tps[0][1] - 1) < 1e-9:
        tgt = f"1 цель {dist(tps[0][0])} (закрываем всё)"
    else:
        share = int(round(tps[0][1] * 100))
        tgt = f"{len(tps)} цели/целей {'/'.join(dist(t[0]) for t in tps)} по {share} %"
        if ex.get("trail_atr"):
            tgt = f"1 цель {dist(tps[0][0])} на {share} %, остаток — трейлинг-стоп {_f(ex['trail_atr'])}×ATR"
    be = ", после первой цели стоп в безубыток" if ex.get("be_after_tp1") else ""
    return f"{stop}, {tgt}{be}, держим максимум {_f(h, 0)} ч"


# ---------------------------------------------------------------- features (all known at the close of bar i)
def build_features():
    t0 = time.time()
    C, H, L = D.C, D.H, D.L
    lr = np.diff(np.log(C), axis=1, prepend=np.nan)
    vol = pd.DataFrame(lr.T).rolling(672, min_periods=400).std().values.T          # 7-day std of 15m log returns, window ends at i
    F = {"vol": vol}
    for N in (4, 16):
        F[f"z{N}"] = D.ret(N) / (vol * np.sqrt(N))
    F["rsi15"] = D.rsi(14)
    r1h = np.full_like(C, np.nan)
    for p in range(4):                                                              # 1h-equivalent RSI: closes i, i-4, i-8, …
        r1h[:, p::4] = D.rsi(14, x=np.ascontiguousarray(C[:, p::4]))
    F["rsi1h"] = r1h
    ema = D.ema(C, 200)
    F["upt"], F["dnt"] = C > ema, C < ema
    for N in (96, 288):                                                             # previous N-bar extreme: bars i-N … i-1
        mx = pd.DataFrame(H.T).rolling(N, min_periods=N).max().values.T
        mn = pd.DataFrame(L.T).rolling(N, min_periods=N).min().values.T
        pmx, pmn = np.full_like(C, np.nan), np.full_like(C, np.nan)
        pmx[:, 1:], pmn[:, 1:] = mx[:, :-1], mn[:, :-1]
        F[f"pmx{N}"], F[f"pmn{N}"] = pmx, pmn
    print(f"features {time.time() - t0:.1f}s", flush=True)
    return F


def gt(a, b):
    with np.errstate(invalid="ignore"):
        return np.nan_to_num(a, nan=-np.inf) > b if np.isscalar(b) else (np.isfinite(a) & np.isfinite(b) & (np.nan_to_num(a) > np.nan_to_num(b)))


def ge(a, v):
    return np.isfinite(a) & (np.nan_to_num(a, nan=-np.inf) >= v)


def le(a, v):
    return np.isfinite(a) & (np.nan_to_num(a, nan=np.inf) <= v)


# each idea: params grid → (up_event, down_event) masks. FADE: short on up / long on down. FLIP: the opposite.
def idea_masks(F, idea: str, p: dict):
    C = D.C
    if idea == "A":
        z = F[f"z{p['N']}"]
        return ge(z, p["z"]), le(z, -p["z"])
    if idea == "B":
        r = F["rsi15" if p["tf"] == "15m" else "rsi1h"]
        return ge(r, p["lvl"]) & F["upt"], le(r, 100 - p["lvl"]) & F["dnt"]
    if idea == "C":
        atr = D.ATR
        up = gt(C, F[f"pmx{p['N']}"] + p["m"] * atr)
        dn = gt(F[f"pmn{p['N']}"] - p["m"] * atr, C)
        return up, dn
    if idea in ("D", "U"):
        z = F["z16"]
        r = F["rsi15" if p["tf"] == "15m" else "rsi1h"]
        up = ge(z, p["z"]) & ge(r, 70) & F["upt"]
        if idea == "U":                     # only the app's most common type: "after a rise in an uptrend" (no mirror for drops)
            return up, np.zeros_like(up)
        return up, le(z, -p["z"]) & le(r, 30) & F["dnt"]
    raise ValueError(idea)


GRIDS = {
    "A": [dict(N=N, z=z) for N in (4, 16) for z in (2.5, 3.5, 4.5)],
    "B": [dict(tf=tf, lvl=l) for tf in ("15m", "1h") for l in (70, 75, 80)],
    "C": [dict(N=N, m=m) for N in (96, 288) for m in (0.0, 0.5, 1.0)],
    "D": [dict(tf=tf, z=z) for tf in ("15m", "1h") for z in (1.5, 2.5, 3.5)],
    # U = one-sided D: only the "short against an uptrend after a rise" signals (the most common app type) and its flip (long)
    "U": [dict(tf=tf, z=z) for tf in ("15m", "1h") for z in (1.5, 2.5, 3.5)],
}
IDEAS_2WAY = ("A", "B", "C", "D")

# plain-Russian descriptions (the {…} are filled from params)
NAME_RU = {
    ("A", "fade"): "Против резкого движения",
    ("A", "flip"): "По резкому движению (флип)",
    ("B", "fade"): "RSI-перекупленность против тренда",
    ("B", "flip"): "RSI-перекупленность по тренду (флип)",
    ("C", "fade"): "Ложный пробой экстремума",
    ("C", "flip"): "Пробой экстремума (флип)",
    ("D", "fade"): "Как в приложении: шорт после роста",
    ("D", "flip"): "Как в приложении, наоборот (флип)",
    ("U", "fade"): "Шорт после роста в аптренде",
    ("U", "flip"): "Лонг после роста в аптренде (флип)",
}


def rule_ru(idea, mode, p, ex_name, ex):
    tfh = {4: "1 ч", 16: "4 ч"}
    w = lambda a, b: a if mode == "fade" else b  # noqa: E731
    if idea == "A":
        s = (f"Если за последние {tfh[p['N']]} монета выросла больше чем на {_f(p['z'])} своих «обычных» колебания — "
             f"{w('шорт', 'лонг')}; если так же упала — {w('лонг', 'шорт')}.")
    elif idea == "B":
        s = (f"Если RSI(14, {p['tf'].replace('m', 'м').replace('h', 'ч')}) ≥ {p['lvl']} и цена выше EMA200 (восходящий тренд) — "
             f"{w('шорт', 'лонг')}; если RSI ≤ {100 - p['lvl']} и цена ниже EMA200 — {w('лонг', 'шорт')}.")
    elif idea == "C":
        per = {96: "24 ч", 288: "72 ч"}[p["N"]]
        extra = "" if p["m"] == 0 else f" больше чем на {_f(p['m'])}×ATR"
        s = (f"Если свеча закрылась выше максимума прошлых {per}{extra} — {w('шорт', 'лонг')}; "
             f"если ниже минимума — {w('лонг', 'шорт')}.")
    elif idea == "D":
        s = (f"Если за 4 ч рост больше {_f(p['z'])} «обычных» колебаний, RSI(14, {p['tf'].replace('m', 'м').replace('h', 'ч')}) ≥ 70 и тренд вверх — "
             f"{w('шорт', 'лонг')}; зеркально после падения при RSI ≤ 30 в нисходящем тренде — {w('лонг', 'шорт')}.")
    else:
        s = (f"Только один тип сигнала: если за 4 ч рост больше {_f(p['z'])} «обычных» колебаний, "
             f"RSI(14, {p['tf'].replace('m', 'м').replace('h', 'ч')}) ≥ 70 и цена выше EMA200 — {w('шорт (против роста)', 'лонг (по росту)')}.")
    return s + f" Вход по рынку на следующей свече; {exit_ru(ex_name, ex)}."


def side_array(up, dn, mode):
    s = np.zeros(up.shape, dtype=np.int8)
    f = -1 if mode == "fade" else 1
    s[up & ~dn] = f
    s[dn & ~up] = -f
    return s


def short_stats(st):
    tr, te = st.get("train", {}), st.get("test", {})
    return dict(tr_n=tr.get("n", 0), tr_avg=tr.get("avg_pct"), te_n=te.get("n", 0), te_avg=te.get("avg_pct"),
                pos=st.get("pos_years", 0), yrs=st.get("years_n", 0), top=st.get("top_coin_share"))


def side_split(trades):
    out = {}
    tr_end = __import__("harness").TRAIN_END
    for sg, nm in ((1, "long"), (-1, "short")):
        for per, cond in (("train", lambda t: D.times[t["e"]] < tr_end), ("test", lambda t: D.times[t["e"]] >= tr_end)):
            r = [t["ret"] for t in trades if t["side"] == sg and cond(t)]
            out[f"{nm}_{per}"] = {"n": len(r), "avg_pct": round(float(np.mean(r) * 100), 3) if r else None}
    return out


def main():
    t_start = time.time()
    F = build_features()
    EX = all_exits()
    rows = []          # every evaluated variant
    tried = 0
    cache_entries = {}
    for idea, grid in GRIDS.items():
        for p in grid:
            up, dn = idea_masks(F, idea, p)
            for mode in ("fade", "flip"):
                E = entries_from_mask(side_array(up, dn, mode) != 0, side=side_array(up, dn, mode), gap=GAP)
                cache_entries[(idea, mode, tuple(sorted(p.items())))] = E
                for en, ex in EX.items():
                    T = run(E, ex)
                    st = stats(T, f"{idea}-{mode}-{p}-{en}")
                    tried += 1
                    rows.append(dict(idea=idea, mode=mode, p=p, exit=en, st=st))
            s = short_stats(rows[-1]["st"])
            print(f"{idea} {p} entries={len(E)}  [{time.time() - t_start:.0f}s]", flush=True)

    # ---- choose the best variant per idea × direction on TRAIN only
    def key(r):
        return (r["idea"], r["mode"])

    best = {}
    for r in rows:
        tr = r["st"].get("train", {})
        if tr.get("n", 0) < MIN_TRAIN_N:
            continue
        k = key(r)
        if k not in best or tr["avg_pct"] > best[k]["st"]["train"]["avg_pct"]:
            best[k] = r

    def find(idea, mode, p, en):
        for r in rows:
            if r["idea"] == idea and r["mode"] == mode and r["p"] == p and r["exit"] == en:
                return r
        return None

    # ---- print overview
    print("\n=== best per idea × direction (chosen on TRAIN) ===")
    hdr = f"{'id':<12}{'params':<26}{'exit':<10}{'tr_n':>7}{'tr_avg':>8}{'te_n':>6}{'te_avg':>8}{'pos':>5}{'top':>6} pass  paired-opposite(tr/te)"
    print(hdr)
    for idea in GRIDS:
        for mode in ("fade", "flip"):
            r = best.get((idea, mode))
            if r is None:
                print(idea, mode, "no eligible variant")
                continue
            s = short_stats(r["st"])
            opp = find(idea, "flip" if mode == "fade" else "fade", r["p"], r["exit"])
            so = short_stats(opp["st"])
            print(f"{idea + '-' + mode:<12}{str(r['p']):<26}{r['exit']:<10}{s['tr_n']:>7}{s['tr_avg']:>8}{s['te_n']:>6}{s['te_avg']!s:>8}"
                  f"{s['pos']:>3}/{s['yrs']}{s['top']!s:>6} {'YES' if passes(r['st']) else 'no '}   {so['tr_avg']}/{so['te_avg']}")

    # ---- exit comparison: average over ALL param combos per idea × direction (train and test)
    print("\n=== exits: mean avg_pct over the 6 param combos (train | test) ===")
    exit_tab = {}
    for idea in GRIDS:
        for mode in ("fade", "flip"):
            line = f"{idea}-{mode:<5}"
            for en in EX:
                xs = [r for r in rows if r["idea"] == idea and r["mode"] == mode and r["exit"] == en]
                tr = np.mean([r["st"]["train"].get("avg_pct", np.nan) for r in xs if r["st"]["train"].get("n", 0) >= 50])
                te = np.mean([r["st"]["test"].get("avg_pct", np.nan) for r in xs if r["st"]["test"].get("n", 0) >= 20])
                exit_tab[(idea, mode, en)] = (tr, te)
                line += f" {en}:{tr:+.3f}|{te:+.3f}"
            print(line)
    print("\nexit ranking (mean over the two-sided ideas A–D, by direction):")
    exit_rank = {}
    for mode in ("fade", "flip"):
        res = []
        for en in EX:
            tr = np.nanmean([exit_tab[(i, mode, en)][0] for i in IDEAS_2WAY])
            te = np.nanmean([exit_tab[(i, mode, en)][1] for i in IDEAS_2WAY])
            res.append((en, tr, te))
        res.sort(key=lambda x: -x[1])
        exit_rank[mode] = res
        print(mode, "  ".join(f"{en}:{tr:+.3f}|{te:+.3f}" for en, tr, te in res))

    # ---- pre-registered app replica (no selection): idea D, 15m RSI, z=2.5, app plan 5 targets, original vs mirrored
    app_p = dict(tf="15m", z=2.5)
    app_pair = {m: find("D", m, app_p, "app5") for m in ("fade", "flip")}
    # sensitivity: the same pair with a 72 h max hold (closer to "hold until stop/targets")
    sens = {}
    for m in ("fade", "flip"):
        E = cache_entries[("D", m, tuple(sorted(app_p.items())))]
        ex = dict(EX["app5"], max_bars=288)
        sens[m] = stats(run(E, ex))
        tried += 1
    print("\n=== pre-registered app replica (D, 15m, z=2.5, app5) ===")
    for m in ("fade", "flip"):
        st = app_pair[m]["st"]
        print(m, short_stats(st), {y: v.get("avg_pct") for y, v in st["years"].items()}, "| 72h hold:", short_stats(sens[m]))
    # month by month (descriptive, same variant — no new choice): how often does one month look like the owner's +0.33 %?
    monthly = {}
    for m in ("fade", "flip"):
        T = run(cache_entries[("D", m, tuple(sorted(app_p.items())))], EX["app5"])
        mk = {}
        for t in T:
            ts = int(D.times[t["e"]] // 1000)
            key_m = time.strftime("%Y-%m", time.gmtime(ts))
            mk.setdefault(key_m, []).append(t["ret"])
        monthly[m] = {k: (len(v), float(np.mean(v) * 100)) for k, v in sorted(mk.items())}
    months = sorted(set(monthly["fade"]) & set(monthly["flip"]))
    fl = np.array([monthly["flip"][k][1] for k in months])
    fa = np.array([monthly["fade"][k][1] for k in months])
    month_info = dict(n_months=len(months), flip_pos=int((fl > 0).sum()), flip_ge_033=int((fl >= 0.33).sum()),
                      flip_beats_fade=int((fl > fa).sum()), fade_pos=int((fa > 0).sum()),
                      last=[(k, monthly["fade"][k][0], round(monthly["fade"][k][1], 2), round(monthly["flip"][k][1], 2)) for k in months[-4:]],
                      best_flip=sorted(zip(fl.round(2).tolist(), months))[-3:], worst_flip=sorted(zip(fl.round(2).tolist(), months))[:3])
    print("monthly:", month_info)
    # app plan on every idea's own best params: original vs flip (paired, no extra selection)
    print("\n=== app5 / app3 / app1 on each idea: fade vs flip, mean over param combos (train | test) ===")
    for idea in GRIDS:
        print(idea, "  ".join(f"{en}: fade {exit_tab[(idea, 'fade', en)][0]:+.3f}|{exit_tab[(idea, 'fade', en)][1]:+.3f}  "
                               f"flip {exit_tab[(idea, 'flip', en)][0]:+.3f}|{exit_tab[(idea, 'flip', en)][1]:+.3f}"
                               for en in ("app5", "app3", "app1")))

    # ---- assemble results
    results = []
    for idea in GRIDS:
        for mode in ("fade", "flip"):
            r = best.get((idea, mode))
            if r is None:
                continue
            opp_mode = "flip" if mode == "fade" else "fade"
            opp = find(idea, opp_mode, r["p"], r["exit"])
            other_best = best.get((idea, opp_mode))
            E = cache_entries[(idea, mode, tuple(sorted(r["p"].items())))]
            split = side_split(run(E, EX[r["exit"]]))
            res = dict(
                id=f"fade_{idea}_{mode}",
                name_ru=NAME_RU[(idea, mode)],
                rule_ru=rule_ru(idea, mode, r["p"], r["exit"], EX[r["exit"]]),
                exit=dict(EX[r["exit"]], name=r["exit"]),
                params=dict(r["p"], idea=idea, mode=mode, gap=GAP),
                stats=r["st"],
                role="original" if mode == "fade" else "flip",
                paired_opposite={"mode": opp_mode, "train": opp["st"]["train"], "test": opp["st"]["test"], "pos_years": opp["st"]["pos_years"]},
                beats_other_best_on_train=bool(other_best and r["st"]["train"]["avg_pct"] > other_best["st"]["train"]["avg_pct"]),
                side_split=split,
            )
            results.append(res)
    for m in ("fade", "flip"):
        r = app_pair[m]
        results.append(dict(
            id=f"fade_app_{m}",
            name_ru="Сигналы как в приложении, план 1:1" if m == "fade" else "Сигналы как в приложении, зеркально (флип)",
            rule_ru=rule_ru("D", m, app_p, "app5", EX["app5"]) + " Параметры заданы заранее, без подбора.",
            exit=dict(EX["app5"], name="app5"),
            params=dict(app_p, idea="D", mode=m, gap=GAP, preregistered=True),
            stats=r["st"],
            role="original" if m == "fade" else "flip",
            sens_hold_72h={"train": sens[m]["train"], "test": sens[m]["test"], "pos_years": sens[m]["pos_years"]},
            monthly_avg_pct={k: round(v[1], 3) for k, v in monthly[m].items()},
        ))
    return rows, best, results, tried, exit_rank, exit_tab, app_pair, sens, month_info, t_start


EXIT_RU = {"app5": "план приложения (стоп 1,6 %, 5 целей, безубыток)", "app3": "3 цели приложения 0,8/1,6/2,4 % с безубытком",
           "app1": "1 цель 0,8 % (половина стопа)", "1tp_1r": "1 цель 1R", "1tp_2r": "1 цель 2R", "3tp_be": "3 цели 1/2/3R с безубытком",
           "5tp_be": "5 целей 0,5–2,5R с безубытком", "tp1_trail": "1 цель + трейлинг", "time": "без целей, выход через 24 ч (стоп 4 ATR)"}


def notes(results, tried, exit_rank, exit_tab, app_pair, month_info):
    pc = lambda v: "—" if v is None else f"{v:+.2f}".replace(".", ",") + " %"  # noqa: E731
    by = {r["id"]: r for r in results}
    n_pass = sum(1 for r in results if passes(r["stats"]))
    two = IDEAS_2WAY
    # paired comparison with the app plan (same signals, same exit), mean over the 6 param combos per idea
    te_f = np.mean([exit_tab[(i, "fade", "app5")][1] for i in two]); te_g = np.mean([exit_tab[(i, "flip", "app5")][1] for i in two])
    tr_f = np.mean([exit_tab[(i, "fade", "app5")][0] for i in two]); tr_g = np.mean([exit_tab[(i, "flip", "app5")][0] for i in two])
    win_te = sum(1 for i in two if exit_tab[(i, "flip", "app5")][1] > exit_tab[(i, "fade", "app5")][1])
    win_tr = sum(1 for i in two if exit_tab[(i, "flip", "app5")][0] > exit_tab[(i, "fade", "app5")][0])
    af, ag = app_pair["fade"]["st"], app_pair["flip"]["st"]
    yrs = lambda st: ", ".join(f"{y} {v.get('avg_pct'):+.2f}".replace(".", ",") for y, v in st["years"].items() if v.get("n", 0))  # noqa: E731
    # long vs short inside the best two-sided variants
    lt = [by[f"fade_{i}_{m}"]["side_split"] for i in two for m in ("fade", "flip") if f"fade_{i}_{m}" in by]
    long_tr_pos = sum(1 for s in lt if (s["long_train"]["avg_pct"] or -1) > 0)
    short_tr_pos = sum(1 for s in lt if (s["short_train"]["avg_pct"] or -1) > 0)
    u0, u1 = by.get("fade_U_fade"), by.get("fade_U_flip")
    mi = month_info
    rk = {m: [en for en, _, _ in exit_rank[m]] for m in exit_rank}
    ex_flip = {en: (tr, te) for en, tr, te in exit_rank["flip"]}
    ex_fade = {en: (tr, te) for en, tr, te in exit_rank["fade"]}
    parts = [
        f"Что проверяли: сигналы «против движения», как в приложении (резкий рост/падение за 1 или 4 ч, перекупленность по RSI в тренде, "
        f"выход за максимум/минимум 24 или 72 ч, копия сигнала приложения «шорт после роста против аптренда»), и их флип — то же самое, "
        f"но вход в сторону движения. 18 монет, 15-минутки, 2021–2026, комиссия 0,14 % за сделку. Параметры выбирали только по 2021–2024, "
        f"проверка — 2025–2026. Всего вариантов (сигнал × сторона × выход): {tried}.",
        f"Итог: ни один вариант не прошёл фильтры ({n_pass} из {len(results)}). Оригинал (против движения) теряет почти везде.",
        f"Флип против оригинала, с планом приложения (стоп 1,6 %, 5 целей, безубыток), на тех же сигналах: "
        f"2021–2024 оригинал {pc(tr_f)}, флип {pc(tr_g)}; 2025–2026 оригинал {pc(te_f)}, флип {pc(te_g)} за сделку "
        f"(флип лучше в {win_tr} из {len(two)} идей на 2021–24 и в {win_te} из {len(two)} на 2025–26). "
        f"То есть флип теряет меньше, но в плюс за 6 лет не выходит.",
        f"Копия сигналов приложения, параметры заданы заранее: оригинал {pc(af['train']['avg_pct'])} (2021–24) и {pc(af['test']['avg_pct'])} (2025–26), "
        f"флип {pc(ag['train']['avg_pct'])} и {pc(ag['test']['avg_pct'])}. По годам — оригинал: {yrs(af)}; флип: {yrs(ag)}. "
        f"По месяцам флип был в плюсе в {mi['flip_pos']} из {mi['n_months']} месяцев и давал +0,33 % и больше только в {mi['flip_ge_033']} месяцах — "
        f"значит, +0,33 % за сентябрь–октябрь 2026 — удачный период, а не правило.",
        f"Откуда разница: в лучших вариантах лонги были в плюсе на 2021–24 в {long_tr_pos} из {len(lt)} случаев, шорты — в {short_tr_pos} из {len(lt)}. "
        f"Флип помогает, потому что превращает «шорт после роста» в «лонг после роста», а лонги в эти годы просто выигрывали от роста рынка.",
    ]
    if u0 and u1:
        parts.append(
            f"Самый частый тип сигнала приложения отдельно («после роста в аптренде»): шорт (как в приложении) — "
            f"{pc(u0['stats']['train']['avg_pct'])} / {pc(u0['stats']['test'].get('avg_pct'))}, лонг (флип) — "
            f"{pc(u1['stats']['train']['avg_pct'])} / {pc(u1['stats']['test'].get('avg_pct'))} (2021–24 / 2025–26), "
            f"плюсовых лет у флипа {u1['stats']['pos_years']} из {u1['stats']['years_n']}, фильтры {'пройдены' if passes(u1['stats']) else 'не пройдены'}.")
    parts.append(
        f"Выходы: для оригинала лучше всего «{EXIT_RU[rk['fade'][0]]}», для флипа — «{EXIT_RU[rk['flip'][0]]}» (по 2021–24). "
        f"Для флипа: 5 целей приложения {pc(ex_flip['app5'][0])} / {pc(ex_flip['app5'][1])}, 3 цели {pc(ex_flip['app3'][0])} / {pc(ex_flip['app3'][1])}, "
        f"1 цель 0,8 % {pc(ex_flip['app1'][0])} / {pc(ex_flip['app1'][1])} (2021–24 / 2025–26, среднее по идеям). "
        f"1 цель на 0,8 % — худший выход и для оригинала ({pc(ex_fade['app1'][0])}), и для флипа: цель в половину стопа требует очень высокого процента побед. "
        f"Из трёх планов приложения 5 целей с безубытком лучше 3 целей, а 3 цели лучше 1 цели.")
    parts.append(
        "Оговорки: сделки по разным монетам в один момент сильно связаны (весь рынок двигается вместе), поэтому реальная надёжность ниже, "
        "чем кажется по числу сделок; производные метрики не использовались; сигналы приложения могут иметь свои правила, которых мы не знаем.")
    return " ".join(parts)


if __name__ == "__main__":
    rows, best, results, tried, exit_rank, exit_tab, app_pair, sens, month_info, t_start = main()
    nt = notes(results, tried, exit_rank, exit_tab, app_pair, month_info)
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    report(os.path.join(HERE, "results", "fade.json"), "fade", results, tried, nt)
    print("\n" + nt)
    print(f"\ntried={tried}  results={len(results)}  total {time.time() - t_start:.0f}s")
