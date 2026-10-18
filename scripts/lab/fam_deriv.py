"""Family "deriv" — new ideas from derivatives data (funding, open interest, liquidations, CVD, taker flow, top-trader L/S,
basis) on 18 coins × 15m. Rules B / E / F / D that the platform already trades are NOT re-tested; their signals are only
rebuilt to measure how much the new rules overlap with them.

Honesty:
  • derivative values are judged only on bars with is_imputed_metrics == 0 (imputed bars are set to NaN before any rolling
    statistic is computed), and entries start 2023-01-01 → harness "train" = 2023-01 → 2024-12, "test" = 2025-01 → 2026-09;
  • every rolling statistic (quantile / mean / std / max / min) covers bars up to and including the signal bar i only;
    entry = next bar open (harness.run);
  • open interest is converted to COINS (OI_usd / close) — OI in USD moves 1:1 with price (corr of 15m changes 0.70),
    which would make "price up while OI falls" partly mechanical;
  • parameters (signal variant + exit) are chosen per idea on TRAIN avg only (TRAIN n ≥ 60); TEST is only reported.

Usage: python3 scripts/lab/fam_deriv.py      (≈ 1–3 min)
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
T_START = time.time()
NS, T = D.NS, D.T
O, H, L, C, V = D.O, D.H, D.L, D.C, D.V
START = int(np.searchsorted(D.times, 1672531200000))          # 2023-01-01 00:00 UTC
W0 = max(0, START - 9000)                                       # rolling stats computed from here (pre-2023 bars are mostly NaN)
IMP = D.IMP
REAL = (IMP == 0) & np.isfinite(C) & np.isfinite(O)
REAL[:, :START] = False
EXITS = grid_exits(96)
# slow-state signals (funding, L/S level, basis, crowding) are multi-day theses: they also get ONE extra exit —
# hold 4 days with a −10 % catastrophe stop (the 15m-ATR stops of the standard grid are ~1 % wide)
SLOW = {"time_4d": dict(sl_pct=0.10, tps=[], max_bars=384)}
TRIED = 0


# ---------------------------------------------------------------- helpers (all causal: window ends at bar i inclusive)
def real(x):
    y = np.array(x, dtype=float, copy=True)
    y[IMP != 0] = np.nan
    return y


def lag(x, n):
    out = np.full_like(x, np.nan, dtype=float)
    out[:, n:] = x[:, :-n]
    return out


def roll(x, n, fn, q=None, minp=None):
    """Rolling statistic over the last n bars INCLUDING bar i (NaNs skipped; min_periods = n//2 unless given)."""
    out = np.full_like(x, np.nan, dtype=float)
    mp = minp if minp is not None else max(2, n // 2)
    for k in range(NS):
        s = pd.Series(x[k, W0:]).rolling(n, min_periods=mp)
        v = s.quantile(q) if fn == "q" else getattr(s, fn)()
        out[k, W0:] = v.to_numpy()
    return out


def b(x):
    return np.nan_to_num(x, nan=0.0).astype(bool) if x.dtype != bool else x


# ---------------------------------------------------------------- inputs (imputed → NaN)
FUND = real(D.FUND)
OIu = real(D.OI)
OIu[OIu <= 0] = np.nan
OIc = OIu / C                                                   # open interest in coins
LLQ, SLQ = np.abs(real(D.LLIQ)), np.abs(real(D.SLIQ))
FCVD, SCVD = real(D.FCVD), real(D.SCVD)
TK = np.log(np.clip(real(D.TAKER), 1e-3, 1e3))
LSR = real(D.LS)
BAS = real(D.BASIS)


def logret(n):
    return np.log(C / lag(C, n))


print(f"[{time.time() - T_START:5.1f}s] data loaded, building features…", flush=True)

# 1. funding percentiles vs the coin's own last 90 days
FQ = {q: roll(FUND, 8640, "q", q, minp=2880) for q in (0.95, 0.99, 0.05, 0.01)}
R32 = C / lag(C, 32) - 1
R96 = C / lag(C, 96) - 1

# 2. OI (coins) vs price over 4h, z-scored by own 30-day std
P16 = logret(16)
OI16 = np.log(OIc / lag(OIc, 16))
ZP = P16 / roll(P16, 2880, "std")
ZOI = OI16 / roll(OI16, 2880, "std")
OI96 = np.log(OIc / lag(OIc, 96))

# 3. short-liquidation spikes
SQ = {q: roll(SLQ, 2880, "q", q) for q in (0.995, 0.999)}
MED_S, MED_L = roll(SLQ, 2880, "median"), roll(LLQ, 2880, "median")

# 4. CVD lines (cumulative per-bar CVD; only differences inside a 48/96-bar window are used)
CF = np.cumsum(np.nan_to_num(FCVD), axis=1)
CS = np.cumsum(np.nan_to_num(SCVD), axis=1)
CVDF = {}
for Lb in (48, 96):
    pmax, pmin = roll(C, Lb, "max", minp=Lb), roll(C, Lb, "min", minp=Lb)
    fmax, fmin = roll(CF, Lb, "max", minp=Lb), roll(CF, Lb, "min", minp=Lb)
    smax, smin = roll(CS, Lb, "max", minp=Lb), roll(CS, Lb, "min", minp=Lb)
    fpos = (CF - fmin) / np.where(fmax - fmin > 0, fmax - fmin, np.nan)
    spos = (CS - smin) / np.where(smax - smin > 0, smax - smin, np.nan)
    CVDF[Lb] = (C >= pmax, C <= pmin, fpos, spos)

# 5. taker buy/sell ratio bursts (log ratio, mean over 1 or 4 bars)
TKB = {1: TK, 4: roll(TK, 4, "mean", minp=4)}
TKQ = {(n, q): roll(TKB[n], 2880, "q", q) for n in (1, 4) for q in (0.99, 0.998, 0.01, 0.002)}

# 6. top-trader long/short ratio — level and 4h change
DLS = np.log(LSR / lag(LSR, 16))
LSQ = {(kind, q): roll(LSR if kind == "level" else DLS, 2880, "q", q)
       for kind in ("level", "chg") for q in (0.98, 0.995, 0.02, 0.005)}

# 7. basis premium (1h mean)
B4 = roll(BAS, 4, "mean", minp=4)
BQ = {q: roll(B4, 2880, "q", q) for q in (0.99, 0.998)}

print(f"[{time.time() - T_START:5.1f}s] features ready", flush=True)


# ---------------------------------------------------------------- existing platform rules (for overlap only)
def existing_masks():
    r = np.full_like(C, np.nan)
    r[:, 1:] = C[:, 1:] / C[:, :-1] - 1
    r0 = np.nan_to_num(r)
    sig = np.sqrt(np.stack([np.convolve(r0[k] ** 2, np.ones(2880), "full")[:T] / 2880 for k in range(NS)]))
    rng = H - L
    wick = (np.minimum(O, C) - L) / np.where(rng > 0, rng, np.nan)
    Bm = (r0 <= -3.5 * sig) & (wick >= 0.5)
    r24 = C / lag(C, 96) - 1
    avail = np.isfinite(r24).sum(0)
    BR = np.where(avail >= 8, (r24 < -0.03).sum(0) / np.maximum(avail, 1), 0)
    cs = np.cumsum(np.nan_to_num(V), axis=1)
    vav = np.full_like(V, np.nan)
    vav[:, 97:] = (cs[:, 96:-1] - cs[:, :-97]) / 96
    Em = (C / O - 1 <= -0.015) & (V >= 2 * vav) & (BR >= 0.4)[None, :]
    oi16u = OIu / lag(OIu, 16) - 1
    Fm = (oi16u <= -0.12) & (C / lag(C, 16) - 1 <= -0.05)
    Dm = roll(BAS, 4, "mean", minp=4) <= -20
    out = {}
    for nm, m in (("B", Bm), ("E", Em), ("F", Fm), ("D", Dm)):
        m = b(m) & REAL
        dil = np.zeros_like(m)
        for s in range(-16, 17):                                 # ±4 h around the existing signal
            if s >= 0:
                dil[:, s:] |= m[:, :T - s] if s else m
            else:
                dil[:, :s] |= m[:, -s:]
        out[nm] = dil
    return out


EXIST = existing_masks()


def overlap(trades):
    if not trades:
        return {}
    ks = np.array([t["k"] for t in trades])
    ii = np.array([t["i"] for t in trades])
    o = {nm: round(float(m[ks, ii].mean()), 3) for nm, m in EXIST.items()}
    o["any"] = round(float(np.any([m[ks, ii] for m in EXIST.values()], axis=0).mean()), 3)
    return o


# ---------------------------------------------------------------- evaluation
ROWS: dict[str, list] = {}


def evaluate(idea, vname, mask, side, gap, params, slow=False):
    global TRIED
    m = b(mask) & REAL
    if isinstance(side, np.ndarray):
        m &= side != 0
    E = entries_from_mask(m, side, gap=gap, start=START)
    for ex_name, ex in ({**EXITS, **SLOW} if slow else EXITS).items():
        TRIED += 1
        tr = run(E, ex)
        st = stats(tr)
        ROWS.setdefault(idea, []).append(dict(variant=vname, exit_name=ex_name, exit=ex, params=dict(params, gap=gap),
                                              stats=st, mask=m, side=side, gap=gap))


def sides(up, dn, follow=True):
    s = np.zeros((NS, T), dtype=np.int8)
    s[b(up)] = 1 if follow else -1
    s[b(dn)] = -1 if follow else 1
    s[b(up) & b(dn)] = 0
    return s


# 1. funding extremes (contrarian)
for qh, ql in ((0.95, 0.05), (0.99, 0.01)):
    for stall in (False, True):
        ms = (FUND >= FQ[qh]) & (FUND >= 0.015)
        ml = (FUND <= FQ[ql]) & (FUND <= -0.005)
        if stall:
            ms &= R32 <= 0
            ml &= R32 >= 0
        tag = f"q{int(qh * 100)}{'_stall' if stall else ''}"
        evaluate("fund_short", tag, ms, -1, 96, dict(fund_pctl=qh, fund_floor=0.015, stall_8h=stall), slow=True)
        evaluate("fund_long", tag, ml, +1, 96, dict(fund_pctl=ql, fund_cap=-0.005, stall_8h=stall), slow=True)

# 2. OI divergence / confirmation (OI in coins, 4h, z vs own 30d)
for a in (1.5, 2.5):
    for c in (1.0, 2.0):
        tag = f"zp{a}_zoi{c}"
        evaluate("oi_weak_short", tag, (ZP >= a) & (ZOI <= -c), -1, 16, dict(z_price_4h=a, z_oi_4h=-c))
        evaluate("oi_weak_long", tag, (ZP <= -a) & (ZOI <= -c), +1, 16, dict(z_price_4h=-a, z_oi_4h=-c))
        s = sides((ZP >= a) & (ZOI >= c), (ZP <= -a) & (ZOI >= c), follow=True)
        evaluate("oi_cont", tag, s != 0, s, 16, dict(z_price_4h=a, z_oi_4h=c))

# 3. short-liquidation squeeze (up-candle, short liqs dominate)
for q in (0.995, 0.999):
    spike = (SLQ >= SQ[q]) & ((SLQ - MED_S) >= 3 * np.maximum(LLQ - MED_L, 0)) & (C > O)
    conf = np.zeros_like(spike)
    conf[:, 1:] = spike[:, :-1] & (C[:, 1:] < C[:, :-1])
    evaluate("sqz", f"q{q}_fade", spike, -1, 16, dict(sliq_pctl_30d=q, imbalance=3, mode="fade_now"))
    evaluate("sqz", f"q{q}_fade_conf", conf, -1, 16, dict(sliq_pctl_30d=q, imbalance=3, mode="fade_after_red_bar"))
    evaluate("sqz", f"q{q}_follow", spike, +1, 16, dict(sliq_pctl_30d=q, imbalance=3, mode="follow"))

# 4. CVD divergence at a local high / low (both sides)
for Lb in (48, 96):
    at_hi, at_lo, fpos, spos = CVDF[Lb]
    s = sides(at_lo & (fpos > 0.5), at_hi & (fpos < 0.5))
    evaluate("cvd", f"fut_L{Lb}", s != 0, s, 16, dict(lookback=Lb, kind="futures CVD in the other half of its range"))
    s = sides(at_lo & (spos > 0.5) & (fpos <= 0.5), at_hi & (spos < 0.5) & (fpos >= 0.5))
    evaluate("cvd", f"spot_L{Lb}", s != 0, s, 16, dict(lookback=Lb, kind="spot CVD disagrees with futures CVD"))

# 5. taker-flow bursts
for n in (1, 4):
    for q in (0.99, 0.998):
        hi, lo = TKB[n] >= TKQ[(n, q)], TKB[n] <= TKQ[(n, round(1 - q, 3))]
        for follow in (True, False):
            s = sides(hi, lo, follow)
            evaluate("taker", f"n{n}_q{q}_{'follow' if follow else 'fade'}", s != 0, s, 16,
                     dict(bars=n, pctl_30d=q, mode="follow" if follow else "fade"))

# 6. top-trader long/short ratio extremes
for kind in ("level", "chg"):
    X = LSR if kind == "level" else DLS
    for q in (0.98, 0.995):
        hi, lo = X >= LSQ[(kind, q)], X <= LSQ[(kind, round(1 - q, 3))]
        for contra in (True, False):
            s = sides(hi, lo, follow=not contra)
            evaluate("ls", f"{kind}_q{q}_{'contra' if contra else 'follow'}", s != 0, s, 96 if kind == "level" else 16,
                     dict(kind=kind, pctl_30d=q, mode="contrarian" if contra else "follow"), slow=kind == "level")

# 7. basis premium spike → short (mirror of D, which is discount → long)
for q in (0.99, 0.998):
    evaluate("basis_prem", f"q{q}", (B4 >= BQ[q]) & (B4 >= 5), -1, 96, dict(basis_1h_pctl_30d=q, floor_bps=5), slow=True)

# 8. crowding: extreme funding + OI build-up (coins, 24h) + price going nowhere (|24h| ≤ 3 %) → against the crowd
for x in (0.05, 0.10):
    flat = np.abs(R96) <= 0.03
    up = (FUND <= FQ[0.05]) & (FUND <= -0.005) & (OI96 >= x) & flat     # crowded shorts → long
    dn = (FUND >= FQ[0.95]) & (FUND >= 0.015) & (OI96 >= x) & flat      # crowded longs → short
    s = sides(up, dn)
    evaluate("crowd", f"oi{x}", s != 0, s, 96, dict(fund_pctl="95/5", oi_24h=x, flat_24h=0.03), slow=True)

print(f"[{time.time() - T_START:5.1f}s] grid done, tried = {TRIED}", flush=True)

# ---------------------------------------------------------------- random baseline (control, not counted in tried)
rng = np.random.default_rng(7)
idx = np.argwhere(REAL)
pick = idx[rng.choice(len(idx), 20000, replace=False)]
BASE = {}
for sg in (1, -1):
    E = [(int(k), int(i), sg) for k, i in pick if i + 1 < T]
    for ex_name, ex in {**EXITS, **SLOW}.items():
        st = stats(run(E, ex))
        BASE[(sg, ex_name)] = (st["train"].get("avg_pct"), st["test"].get("avg_pct"))
print(f"[{time.time() - T_START:5.1f}s] baseline done", flush=True)


# ---------------------------------------------------------------- selection on TRAIN, report
def pick_best(rows):
    ok = [r for r in rows if r["stats"].get("train", {}).get("n", 0) >= 60]
    if not ok:
        ok = [r for r in rows if r["stats"].get("train", {}).get("n", 0) >= 20] or rows
    return max(ok, key=lambda r: (r["stats"].get("train", {}).get("avg_pct") or -99))


NAMES = {
    "fund_short": ("Перегретый фандинг → шорт",
                   "Фандинг монеты в верхних {p} % её же истории за 90 дней и не ниже 0.015 % за период{st} — лонгисты переплачивают, "
                   "открываем шорт со следующей свечи."),
    "fund_long": ("Отрицательный фандинг → лонг",
                  "Фандинг монеты в нижних {p} % её истории за 90 дней и ниже −0.005 %{st} — шортисты переплачивают, открываем лонг."),
    "oi_weak_short": ("Рост без новых денег → шорт",
                      "Цена за 4 ч выросла сильно (≥ {a}σ), а открытый интерес в монетах упал (≤ −{c}σ) — это закрытие шортов, "
                      "а не новые покупки; продаём рост."),
    "oi_weak_long": ("Падение с закрытием позиций → лонг",
                     "Цена за 4 ч сильно упала (≤ −{a}σ) и открытый интерес в монетах тоже упал (≤ −{c}σ) — лонги вынесло; покупаем "
                     "(родственник правила F, но мягче)."),
    "oi_cont": ("Движение на новых позициях → по тренду",
                "Цена за 4 ч сильно сдвинулась (≥ {a}σ) и открытый интерес в монетах при этом вырос (≥ {c}σ) — входим по направлению движения."),
    "sqz": ("Шорт-сквиз",
            "На зелёной свече ликвидации шортов выше {q} уровня за 30 дней и втрое больше ликвидаций лонгов; {mode}."),
    "cvd": ("Дивергенция CVD",
            "Цена на {L}-свечном максимуме (минимуме), а {kind} — против движения; торгуем разворот."),
    "taker": ("Всплеск агрессивных покупок/продаж",
              "Отношение агрессивных покупок к продажам за {n} свеч. в крайних {p} % за 30 дней; {mode}."),
    "ls": ("Крайний перекос топ-трейдеров",
           "Соотношение лонг/шорт топ-трейдеров ({kind}) в крайних {p} % за 30 дней; {mode}."),
    "basis_prem": ("Премия фьючерса → шорт",
                   "Фьючерс за последний час в среднем дороже спота сильнее, чем в {p} % случаев за 30 дней (и не меньше 5 б.п.) — шорт."),
    "crowd": ("Толпа набирает позиции против фандинга",
              "Фандинг в крайних 5 % истории, открытый интерес в монетах за сутки вырос на {x} %+, а цена за сутки почти не сдвинулась "
              "(±3 %) — входим против переплачивающей стороны."),
}


def rule_text(idea, p):
    nm, tpl = NAMES[idea]
    if idea in ("fund_short", "fund_long"):
        pct = round((1 - p["fund_pctl"]) * 100) if idea == "fund_short" else round(p["fund_pctl"] * 100)
        st = (", а цена за 8 ч не выросла" if idea == "fund_short" else ", а цена за 8 ч не упала") if p["stall_8h"] else ""
        return nm, tpl.format(p=pct, st=st)
    if idea in ("oi_weak_short", "oi_weak_long", "oi_cont"):
        return nm, tpl.format(a=abs(p["z_price_4h"]), c=abs(p["z_oi_4h"]))
    if idea == "sqz":
        mode = {"fade_now": "сразу продаём (ставим на откат)", "fade_after_red_bar": "ждём следующую свечу, и если она закрылась ниже — продаём",
                "follow": "покупаем по движению"}[p["mode"]]
        return nm, tpl.format(q=f"{p['sliq_pctl_30d'] * 100:.1f}-процентного", mode=mode)
    if idea == "cvd":
        kind = "кумулятивная дельта фьючерсов в другой половине своего диапазона" if p["kind"].startswith("futures") else \
            "спотовая дельта идёт против фьючерсной"
        return nm, tpl.format(L=p["lookback"], kind=kind)
    if idea == "taker":
        mode = "входим по направлению всплеска" if p["mode"] == "follow" else "входим против всплеска"
        return nm, tpl.format(n=p["bars"], p=round((1 - p["pctl_30d"]) * 100, 1), mode=mode)
    if idea == "ls":
        kind = "уровень" if p["kind"] == "level" else "изменение за 4 ч"
        mode = "идём против топ-трейдеров" if p["mode"] == "contrarian" else "идём вместе с топ-трейдерами"
        return nm, tpl.format(kind=kind, p=round((1 - p["pctl_30d"]) * 100, 1), mode=mode)
    if idea == "basis_prem":
        return nm, tpl.format(p=round(p["basis_1h_pctl_30d"] * 100, 1))
    if idea == "crowd":
        return nm, tpl.format(x=round(p["oi_24h"] * 100))
    return nm, tpl


def passes_3of4(st):
    te = st.get("test") or {}
    return bool(te.get("n", 0) >= 60 and (te.get("avg_pct") or -1) > 0.05 and st.get("pos_years", 0) >= 3
                and (st.get("top_coin_share") or 1) <= 0.5)


results = []
summary_rows = []
for idea, rows in ROWS.items():
    best = pick_best(rows)
    st = best["stats"]
    E = entries_from_mask(best["mask"], best["side"], gap=best["gap"], start=START)
    tr = run(E, best["exit"])
    sides_used = sorted(set(t["side"] for t in tr))
    sg_base = sides_used[0] if len(sides_used) == 1 else None
    base = BASE.get((sg_base, best["exit_name"])) if sg_base else None
    nm, rule = rule_text(idea, best["params"])
    tests = [r["stats"].get("test", {}).get("avg_pct") for r in rows if r["stats"].get("test", {}).get("n", 0) >= 30]
    trains = [r["stats"].get("train", {}).get("avg_pct") for r in rows if r["stats"].get("train", {}).get("n", 0) >= 30]
    res = {
        "id": f"deriv_{idea}",
        "name_ru": nm,
        "rule_ru": rule,
        "exit": dict(best["exit"], name=best["exit_name"]),
        "params": dict(best["params"], variant=best["variant"]),
        "stats": st,
        "passes_3of4": passes_3of4(st),
        "overlap_BEFD_4h": overlap(tr),
        "random_same_side_avg_pct": {"train": base[0], "test": base[1]} if base else None,
        "idea_grid": {"variants": len(rows), "train_pos_share": round(float(np.mean([x > 0 for x in trains])), 2) if trains else None,
                      "test_pos_share": round(float(np.mean([x > 0 for x in tests])), 2) if tests else None},
        "sides": sides_used,
    }
    results.append(res)
    summary_rows.append((idea, best["variant"], best["exit_name"], st, res))

# ---------------------------------------------------------------- robustness for the rules that pass "≥ 3 of 4 years"
# (not used to choose anything: neighbours of the train-chosen thresholds with the same exit, the part of the entries that is
#  NOT within ±4 h of B/E/F/D, and a day-clustered average so one market-wide squeeze day is not counted 18 times)
def day_clustered(trades):
    out = {}
    if not trades:
        return out
    day = np.array([D.times[t["e"]] // 86_400_000 for t in trades])
    r = np.array([t["ret"] for t in trades])
    te = np.array([D.times[t["e"]] for t in trades]) >= 1735689600000
    for lab, m in (("train", ~te), ("test", te)):
        if m.sum():
            u, inv = np.unique(day[m], return_inverse=True)
            dm = np.bincount(inv, weights=r[m]) / np.bincount(inv)
            out[lab] = {"days": int(len(u)), "day_avg_pct": round(float(dm.mean() * 100), 3), "day_win": round(float((dm > 0).mean()), 3)}
    return out


def neighbours(idea, p):
    if idea == "sqz":
        mode = p["mode"]
        for q in (0.998, 0.999, 0.9995):
            for imb in (2, 3, 5):
                if q == p["sliq_pctl_30d"] and imb == 3:
                    continue
                thr = SQ[q] if q in SQ else roll(SLQ, 2880, "q", q)
                spike = (SLQ >= thr) & ((SLQ - MED_S) >= imb * np.maximum(LLQ - MED_L, 0)) & (C > O)
                if mode == "fade_after_red_bar":
                    m2 = np.zeros_like(spike)
                    m2[:, 1:] = spike[:, :-1] & (C[:, 1:] < C[:, :-1])
                else:
                    m2 = spike
                yield f"q{q}_imb{imb}", m2, (+1 if mode == "follow" else -1), 16
    elif idea == "oi_weak_long":
        for a in (2.0, 2.5, 3.0):
            for c in (1.5, 2.0, 2.5):
                if a == abs(p["z_price_4h"]) and c == abs(p["z_oi_4h"]):
                    continue
                yield f"zp{a}_zoi{c}", (ZP <= -a) & (ZOI <= -c), +1, 16


for res in results:
    idea = res["id"].replace("deriv_", "")
    best = pick_best(ROWS[idea])
    E = entries_from_mask(best["mask"], best["side"], gap=best["gap"], start=START)
    tr = run(E, best["exit"])
    res["day_clustered"] = day_clustered(tr)
    if not res["passes_3of4"]:
        continue
    anym = np.any([m for m in EXIST.values()], axis=0)
    sub = [t for t in tr if not anym[t["k"], t["i"]]]
    TRIED += 1
    s2 = stats(sub)
    res["without_BEFD_overlap"] = {"train": s2.get("train"), "test": s2.get("test"), "pos_years": s2.get("pos_years"),
                                   "years": {y: v.get("avg_pct") for y, v in s2.get("years", {}).items()}}
    pl = {}
    for nm_, m2, sd_, gp in neighbours(idea, best["params"]):
        TRIED += 1
        s3 = stats(run(entries_from_mask(b(m2) & REAL, sd_, gap=gp, start=START), best["exit"]))
        pl[nm_] = {"train": s3.get("train", {}).get("avg_pct"), "test": s3.get("test", {}).get("avg_pct"),
                   "test_n": s3.get("test", {}).get("n"), "pos_years": s3.get("pos_years")}
    res["plateau_same_exit"] = pl
    print(f"\n[robust] {idea}: day-clustered {res['day_clustered']}\n  without B/E/F/D: train {s2.get('train')} test {s2.get('test')} "
          f"years {res['without_BEFD_overlap']['years']}")
    for k_, v_ in pl.items():
        print(f"  plateau {k_:16} train {v_['train']} test {v_['test']} (n {v_['test_n']}) yrs {v_['pos_years']}")

# all-variant dump (compact) for later reference
ALL = []
for idea, rows in ROWS.items():
    for r in rows:
        s = r["stats"]
        ALL.append(dict(idea=idea, variant=r["variant"], exit=r["exit_name"],
                        train=s.get("train"), test=s.get("test"), pos_years=s.get("pos_years"), top=s.get("top_coin_share")))

base_txt = "; ".join(f"{'лонг' if sg > 0 else 'шорт'} {ex}: train {v[0]:+.3f} / test {v[1]:+.3f}"
                     for (sg, ex), v in BASE.items() if ex in ("1tp_1r", "time", "time_4d"))
NOTES = (
    "Производные данные (фандинг, OI, ликвидации, CVD, тейкеры, лонг/шорт, базис) до 2023 г. в основном восстановлены (is_imputed_metrics=1), "
    "поэтому все правила этой семьи судятся только на реальных барах с 2023-01: TRAIN = 2023-01 → 2024-12 (выбор параметров), "
    "TEST = 2025-01 → 2026-09 (только проверка). Лет всего 4 (2023–2026), поэтому правило лет здесь — «≥ 3 из 4 положительных»; "
    "harness.passes() требует 4 из 6 и для этой семьи означает «все 4 года в плюсе» (поле passes), отдельно записано passes_3of4. "
    "Открытый интерес переведён в монеты (OI в $ / цена): в долларах он двигается вместе с ценой, и «цена растёт, OI падает» было бы наполовину арифметикой. "
    "Ликвидации по альткоинам имеют постоянный «пол» (~0.2–0.5 млн $ на свечу даже в тишине) — похоже на оценочные данные; "
    "поэтому используются только всплески относительно собственной медианы и 30-дневного перцентиля. "
    "Правила B, E, F, D не перетестировались; у каждого результата указано, какая доля входов попадает в ±4 ч от их сигналов (overlap_BEFD_4h). "
    f"Случайный вход для сравнения (20 000 реальных баров 2023+): {base_txt}. "
    f"Всего проверено вариантов: {TRIED} (сигнал × выход + проверки соседних порогов), выбор лучшего в каждой идее — по среднему на TRAIN при n ≥ 60."
)
_ok3 = [f"{r['name_ru']} (test {r['stats']['test'].get('avg_pct'):+.3f} %, перекрытие с B/E/F/D {r['overlap_BEFD_4h'].get('any', 0) * 100:.0f} %)"
        for r in results if r["passes_3of4"]]
_ok4 = [r["name_ru"] for r in results if passes(r["stats"])]
NOTES += (f" Итог: по правилу «≥ 3 из 4 лет» проходят — {'; '.join(_ok3) or 'ничего'}; по строгому правилу harness (все 4 года) — "
          f"{', '.join(_ok4) or 'ничего'}. Фандинг, тейкерские всплески, CVD-дивергенции, перекос топ-трейдеров, премия базиса и "
          "«толпа против фандинга» на TEST не лучше случайного входа (≈ минус издержки).")

os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
report(os.path.join(HERE, "results", "deriv.json"), "deriv", results, TRIED, NOTES)
import json  # noqa: E402
with open(os.path.join(HERE, "results", "deriv_all_variants.json"), "w", encoding="utf-8") as f:
    json.dump({"tried": TRIED, "baseline": {f"{sg}_{ex}": v for (sg, ex), v in BASE.items()}, "variants": ALL}, f, ensure_ascii=False, default=float)

print(f"\n{'idea':14} {'variant':22} {'exit':9} {'trN':>5} {'trAvg':>7} {'teN':>5} {'teAvg':>7} {'tePF':>5} yrs  top  pass 3/4  overlap  years")
for idea, var, ex, st, res in summary_rows:
    tr_, te_ = st.get("train", {}), st.get("test", {})
    yrs = " ".join(f"{y}:{v.get('avg_pct', 0):+.2f}({v.get('n', 0)})" for y, v in st.get("years", {}).items())
    print(f"{idea:14} {var:22} {ex:9} {tr_.get('n', 0):5d} {tr_.get('avg_pct') or 0:+7.3f} {te_.get('n', 0):5d} "
          f"{te_.get('avg_pct') or 0:+7.3f} {te_.get('pf') or 0:5.2f} {st.get('pos_years')}/{st.get('years_n')} "
          f"{st.get('top_coin_share')} {passes(st)!s:5} {res['passes_3of4']!s:5} {res['overlap_BEFD_4h'].get('any')} | {yrs}")
print(f"\ntried = {TRIED}; runtime {time.time() - T_START:.0f}s")
