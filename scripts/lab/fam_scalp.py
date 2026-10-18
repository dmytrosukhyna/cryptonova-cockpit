"""Family "scalp" — short holds ("instant work": quick small profits).

Part A — 15m bars, 18 coins, harness.run (entry at the next bar's open, stop first inside a bar):
  A1 reversion of a sharp single bar (range > k×ATR of the previous bar, close at its extreme) → fade it
  A2 deviation from EMA20 / the day's VWAP (> k×ATR) → back toward the mean
  A3 opening-range breakout of the session open (Asia 00:00 UTC, London 08:00 local, New York 09:30 local, DST-aware)
  A4 time-of-day drift (hours whose sign is the same in every TRAIN year; hours chosen on TRAIN only)
  A5 taker-flow burst (z-score of log taker buy/sell ratio vs the last 24 h; 2023+ only, the data before is imputed)
  Holds 2 / 4 / 8 bars (30 min – 2 h). Exits: stop 1×ATR & target 0.5 R, stop 2×ATR & target 0.5 R, or pure time exit.
Part B — true 1-minute scalping on BTC (Bitstamp BTC/USD spot, 2021-01 → 2026-09), own vectorised simulator in the same
  spirit (entry at the next minute's open, stop first inside a bar, entry bar may hit stop/target):
  B1 1–5 min momentum burst with volume → follow;  B2 1–3 min spike → fade;  B3 30/60-min range breakout (± compression);
  B4 New York open (09:30 local): direction of the first 5/15 minutes → follow.  Holds 5 / 15 / 30 min.
  TRAIN 2021–2023, TEST 2024-01 → 2026-09 (Bitstamp history file + its "updates" file, cut before the 2026-09-30 maintenance).

Costs, three views for every final variant:
  taker   0.14 % round trip (harness default)                — what a market-order bot really pays;
  maker   0.04 % round trip, EVERY signal assumed filled      — an upper bound, not reachable in practice;
  limit   realistic limit entry: a limit order at the signal close, filled only if the price trades THROUGH it by
          ≥ 0.02 % within the next 15m bar (1m: within 3 minutes); in the fill bar only the stop may hit; entry 0.02 %,
          exit 0.02 % at the target (resting limit) or 0.07 % at the stop / time exit (market). Unfilled → no trade.
Selection: every (signal, exit) variant is scored by its GROSS average per trade on TRAIN (cost-free, so the choice is the
same for every cost view), with a minimum number of TRAIN trades. TEST is never used to choose. Every evaluated variant is
counted in `tried`.

Usage: python scripts/lab/fam_scalp.py   → scripts/lab/results/scalp.json
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import harness as HN  # noqa: E402
from harness import COST, D, entries_from_mask, passes, report, run, stats  # noqa: E402

MAKER = 0.0004                    # limit orders on both sides (harness note)
M_SIDE, T_SIDE = 0.0002, 0.0007   # one side: maker fee / taker fee + slippage
FILL_BUF = 0.0002                 # a limit fills only if price trades through it by ≥ 0.02 %
TRIED = 0
T0 = time.time()
UTC = dt.timezone.utc
SCR = os.environ.get("CNQ_SCRATCH", "/tmp/claude-0/-home-claude/5491f1a8-ca5d-58c4-b6e0-e27b29449aba/scratchpad")
BTC1M_GZ = os.path.join(SCR, "ds/bitstamp-btcusd-minute-data/data/historical/btcusd_bitstamp_1min_2012-2025.csv.gz")
BTC1M_UPD = os.path.join(SCR, "ds/bitstamp-btcusd-minute-data/data/updates/btcusd_bitstamp_1min_latest.csv")
BTC1M_CACHE = os.path.join(SCR, "scalp/btc1m_full.npz")


def log(*a):
    print(f"[{time.time() - T0:6.0f}s]", *a, flush=True)


def ms(y, m=1, d=1):
    return int(dt.datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


# ───────────────────────────── shared single-target simulator (used for Part B and the "limit" view of Part A) ──
def sim(O, H, L, C, k, i, sg, sd, tp_r, mb, mode="market", fill_win=1, buf=FILL_BUF):
    """Vectorised single-target simulator. O..C: 2-D [series, bar]; entries are signal bars i (known at their close).
    market: entry at the open of bar i+1; stop first inside a bar; the entry bar may hit stop/target (harness rule).
    limit : limit order at the signal close C[i]; filled only if the price trades through it by ≥ buf within fill_win
            bars; in the fill bar only the stop may hit. Time exit at the close of bar i+mb (mb bars after the signal).
    sd = stop distance in price; target = tp_r × sd (tp_r None → no target)."""
    T = O.shape[1]
    k, i = np.asarray(k, int), np.asarray(i, int)
    sg, sd = np.asarray(sg, float), np.asarray(sd, float)
    e = i + 1
    keep = e + mb - 1 <= T - 1
    k, i, sg, sd, e = k[keep], i[keep], sg[keep], sd[keep], e[keep]
    J = np.arange(mb)
    idx = e[:, None] + J[None, :]
    kk = k[:, None]
    hh, ll, cc = H[kk, idx], L[kk, idx], C[kk, idx]
    lg = (sg > 0)[:, None]
    fav = np.where(lg, hh, -ll)
    adv = np.where(lg, ll, -hh)
    cl = cc * sg[:, None]
    ok = np.isfinite(fav).all(1) & np.isfinite(adv).all(1) & np.isfinite(sd) & (sd > 0)
    if mode == "market":
        px = O[k, e]
        f = np.zeros(len(k), int)
        t_from = f
        filled = np.isfinite(px)
    else:
        px = C[k, i]
        thr = px * sg - buf * px
        fm = adv[:, :fill_win] <= thr[:, None]
        filled = fm.any(1)
        f = fm.argmax(1)
        t_from = f + 1
    ok &= filled & np.isfinite(px)
    p0 = px * sg
    stop = p0 - sd
    sh = (adv <= stop[:, None]) & (J[None, :] >= f[:, None])
    js = np.where(sh.any(1), sh.argmax(1), mb)
    tpd = (tp_r or 0.0) * sd
    if tp_r:
        tg = (fav >= (p0 + tpd)[:, None]) & (J[None, :] >= t_from[:, None])
        jt = np.where(tg.any(1), tg.argmax(1), mb)
    else:
        jt = np.full(len(k), mb)
    is_stop = (js < mb) & (js <= jt)
    is_tp = ~is_stop & (jt < mb)
    with np.errstate(all="ignore"):
        gross = np.where(is_stop, -sd / px, np.where(is_tp, tpd / px, (cl[:, -1] - p0) / px))
    x = np.where(is_stop, js, np.where(is_tp, jt, mb - 1))
    kind = np.where(is_stop, 2, np.where(is_tp, 1, 0))
    o = ok
    return dict(k=k[o], i=i[o], sg=sg[o], e=(e + f)[o], x=(e + x)[o], gross=gross[o], kind=kind[o],
                n_sig=int(len(k)), n_fill=int(o.sum()))


def limit_cost(kind):
    return M_SIDE + np.where(kind == 1, M_SIDE, T_SIDE)


# ═════════════════════════════════════════════════════ PART A (15m, 18 coins, harness) ══
EXA = {}
for _mb in (2, 4, 8):
    EXA[f"s1t05_{_mb}"] = dict(sl_atr=1.0, tps=[(0.5, 1.0)], max_bars=_mb)
    EXA[f"s2t05_{_mb}"] = dict(sl_atr=2.0, tps=[(0.5, 1.0)], max_bars=_mb)
    EXA[f"time_{_mb}"] = dict(sl_atr=2.0, tps=[], max_bars=_mb)


def gross_avg(T, a, b):
    if not T:
        return np.nan, 0
    r = np.array([t["ret"] for t in T])
    te = D.times[np.array([t["e"] for t in T])]
    m = (te >= a) & (te < b)
    return (float(r[m].mean()) if m.any() else np.nan), int(m.sum())


def search_a(idea, signals, exits=EXA, min_n=300, start=None, train=(HN.TRAIN_START, HN.TRAIN_END)):
    """signals: {name: (mask, side, params)}; evaluates every (signal × exit) on TRAIN gross avg; returns the best."""
    global TRIED
    rows, best, cacheE = [], None, {}
    for sname, (mask, side, sp) in signals.items():
        for ename, ex in exits.items():
            mb = ex["max_bars"]
            key = (sname, mb)
            if key not in cacheE:
                cacheE[key] = entries_from_mask(mask, side, gap=mb, start=start)
            E = cacheE[key]
            T = run(E, ex, cost=0.0)
            avg, n = gross_avg(T, *train)
            TRIED += 1
            rows.append((avg, n, sname, ename))
            if n >= min_n and np.isfinite(avg) and (best is None or avg > best[0]):
                best = (avg, n, sname, ename, E, ex, sp)
    rows.sort(key=lambda r: -(r[0] if np.isfinite(r[0]) else -9))
    log(f"{idea}: {len(rows)} variants; top by TRAIN gross avg %:")
    for r in rows[:5]:
        print(f"      {r[2]:<22} {r[3]:<10} n={r[1]:<7} gross={r[0] * 100:+.3f}%")
    return best


_SIM_CHECKED = False


def finalize_a(rid, name_ru, rule_ru, E, ex, params):
    """Taker (harness), maker (cost 0.0004, all fills), realistic limit (own sim) stats for the chosen variant."""
    global _SIM_CHECKED
    Tg = run(E, ex, cost=0.0)
    taker = [dict(t, ret=t["ret"] - COST) for t in Tg]
    maker = [dict(t, ret=t["ret"] - MAKER) for t in Tg]
    k = np.array([e[0] for e in E])
    i = np.array([e[1] for e in E])
    sg = np.array([e[2] for e in E])
    sd = D.ATR[k, i] * ex.get("sl_atr", 2.0)
    tp_r = ex["tps"][0][0] if ex.get("tps") else None
    if not _SIM_CHECKED:   # cross-check own simulator against harness.run on the same entries
        S = sim(D.O, D.H, D.L, D.C, k, i, sg, sd, tp_r, ex["max_bars"], "market")
        log(f"   sim check: harness gross avg {np.mean([t['ret'] for t in Tg]) * 100:+.4f}% n={len(Tg)}  |  own sim "
            f"{S['gross'].mean() * 100:+.4f}% n={len(S['gross'])}")
        _SIM_CHECKED = True
    S = sim(D.O, D.H, D.L, D.C, k, i, sg, sd, tp_r, ex["max_bars"], "limit", fill_win=1)
    lim = [{"k": int(a), "i": int(b), "e": int(c), "x": int(d), "side": int(s), "ret": float(g - lc), "tp": int(kd == 1)}
           for a, b, c, d, s, g, lc, kd in zip(S["k"], S["i"], S["e"], S["x"], S["sg"], S["gross"],
                                               limit_cost(S["kind"]), S["kind"])]
    st, stm, stl = stats(taker, rid + " taker"), stats(maker, rid + " maker"), stats(lim, rid + " limit")
    stl["fill_rate"] = round(S["n_fill"] / max(1, S["n_sig"]), 3)
    return {"id": rid, "name_ru": name_ru, "rule_ru": rule_ru, "exit": ex, "params": params, "costs": "taker 0.14%",
            "stats": st, "stats_maker": stm, "stats_limit": stl,
            "passes_maker": passes(stm), "passes_limit": passes(stl)}


def exit_ru(ex, bar_min=15, atr_ru="ATR"):
    mb = ex["max_bars"] * bar_min
    hold = f"{mb} мин" if mb < 60 else (f"{mb // 60} ч" if mb % 60 == 0 else f"{mb / 60:.1f} ч")
    sl = ex.get("sl_atr")
    if ex.get("tps"):
        tp = ex["tps"][0][0] * sl
        return f"стоп {sl:g}×{atr_ru}, цель {tp:g}×{atr_ru}, выход по времени через {hold}"
    return f"без цели, страховочный стоп {sl:g}×{atr_ru}, выход по времени через {hold}"


COSTS_RU = "Основная оценка — рыночные ордера (0,14 % за сделку туда-обратно); рядом — лимитные (0,04 %, если бы всё исполнялось) и реалистичный лимит."


def part_a():
    log("Part A: preparing 15m signals")
    O, H, L, C, ATR, V = D.O, D.H, D.L, D.C, D.ATR, D.V
    NS, T = D.NS, D.T
    atr_prev = np.concatenate([np.full((NS, 1), np.nan), ATR[:, :-1]], 1)
    out = []
    with np.errstate(all="ignore"):
        rng = H - L
        clv = (C - L) / rng

    # ── A1 sharp single-bar reversion ──
    sig = {}
    for kx in (2.5, 3.5):
        big = rng > kx * atr_prev
        side = np.where(big & (C < O) & (clv < 0.25), 1, np.where(big & (C > O) & (clv > 0.75), -1, 0)).astype(np.int8)
        sig[f"k{kx}"] = (side != 0, side, {"range_atr": kx, "close_at_extreme": "нижняя/верхняя четверть"})
    b = search_a("A1 spike-revert", sig)
    _, _, sname, ename, E, ex, sp = b
    out.append(finalize_a("scalp_spike_revert", "Откат после резкой 15-минутной свечи",
                          f"Если 15-минутная свеча длиннее {sp['range_atr']:g}×ATR и закрылась у своего края, на открытии следующей "
                          f"свечи входим в обратную сторону (после падения — лонг, после роста — шорт); {exit_ru(ex)}. {COSTS_RU}",
                          E, ex, dict(sp, exit=ename)))

    # ── A2 EMA20 / VWAP deviation reversion ──
    ema20 = D.ema(C, 20)
    day = D.times // 86_400_000
    tp_ = np.nan_to_num((H + L + C) / 3)
    vv = np.nan_to_num(V)
    cpv, cv = np.cumsum(tp_ * vv, 1), np.cumsum(vv, 1)
    first = np.r_[0, np.flatnonzero(np.diff(day)) + 1]
    st = np.zeros(T, int)
    st[first] = first
    st = np.maximum.accumulate(st)
    prev = np.maximum(st - 1, 0)
    bpv = np.where(st > 0, cpv[:, prev], 0.0)
    bv = np.where(st > 0, cv[:, prev], 0.0)
    with np.errstate(all="ignore"):
        vwap = (cpv - bpv) / (cv - bv)
        dev_e, dev_v = (C - ema20) / ATR, (C - vwap) / ATR
    sig = {}
    for base, dev, ks in (("ema20", dev_e, (2.5, 3.5)), ("vwap", dev_v, (4.0, 6.0))):
        for kx in ks:
            side = np.where(dev < -kx, 1, np.where(dev > kx, -1, 0)).astype(np.int8)
            sig[f"{base}_k{kx}"] = (side != 0, side, {"base": base, "dev_atr": kx})
    b = search_a("A2 mean-dev revert", sig)
    _, _, sname, ename, E, ex, sp = b
    base_ru = "средней EMA20 (5 ч)" if sp["base"] == "ema20" else "средней цены дня (VWAP с 00:00 UTC)"
    out.append(finalize_a("scalp_meandev_revert", "Возврат к средней после сильного отклонения",
                          f"Если цена закрытия ушла от {base_ru} дальше чем на {sp['dev_atr']:g}×ATR, входим в сторону средней на "
                          f"открытии следующей свечи; {exit_ru(ex)}. {COSTS_RU}", E, ex, dict(sp, exit=ename)))
    del ema20, cpv, cv, bpv, bv, vwap, dev_e, dev_v, tp_, vv, sig, b, E
    import gc
    gc.collect()

    # ── A3 session opening-range breakout (DST-aware) ──
    d0 = int(D.times[0] // 86_400_000)
    nd = T // 96 + 2

    def open_bars(tz, hh, mm, weekdays):
        r = []
        for dd in range(nd):
            date = dt.date(1970, 1, 1) + dt.timedelta(days=d0 + dd)
            if weekdays and date.weekday() >= 5:
                continue
            t_ = int(dt.datetime(date.year, date.month, date.day, hh, mm, tzinfo=ZoneInfo(tz)).timestamp() * 1000)
            bb = (t_ - int(D.times[0])) // 900_000
            if 0 <= bb < T - 20:
                r.append(int(bb))
        return np.array(r)

    sess = {"asia": open_bars("UTC", 0, 0, False), "europe": open_bars("Europe/London", 8, 0, True),
            "us": open_bars("America/New_York", 9, 30, True)}

    def orb(opens, m, W=4):
        mask = np.zeros((NS, T), bool)
        side = np.zeros((NS, T), np.int8)
        for s in opens:
            orh, orl = np.nanmax(H[:, s:s + m], 1), np.nanmin(L[:, s:s + m], 1)
            cw = C[:, s + m:s + m + W]
            up, dn = cw > orh[:, None], cw < orl[:, None]
            hit = up | dn
            anyh = hit.any(1)
            j = hit.argmax(1)
            for kk in np.flatnonzero(anyh):
                jj = s + m + j[kk]
                mask[kk, jj] = True
                side[kk, jj] = 1 if up[kk, j[kk]] else -1
        return mask, side

    sig = {}
    with np.errstate(all="ignore"):
        for sn, opens in (("all", np.concatenate([sess["asia"], sess["europe"], sess["us"]])), ("us", sess["us"])):
            for m in (1, 2):
                mk, sd_ = orb(np.sort(opens), m)
                sig[f"{sn}_or{m}"] = (mk, sd_, {"sessions": sn, "or_bars": m, "breakout_window_bars": 4})
    b = search_a("A3 session ORB", sig)
    _, _, sname, ename, E, ex, sp = b
    sess_ru = "открытия Азии (00:00 UTC), Лондона (08:00 по Лондону) и Нью-Йорка (9:30 по Нью-Йорку)" if sp["sessions"] == "all" \
        else "открытия Нью-Йорка (9:30 по Нью-Йорку, 13:30/14:30 UTC)"
    out.append(finalize_a("scalp_session_orb", "Пробой диапазона открытия сессии",
                          f"После {sess_ru} берём диапазон первых {sp['or_bars'] * 15} мин; если в следующий час 15-минутная свеча "
                          f"закрылась выше/ниже диапазона — входим в сторону пробоя на открытии следующей свечи; {exit_ru(ex)}. {COSTS_RU}",
                          E, ex, dict(sp, exit=ename)))

    # ── A4 time-of-day drift (hours chosen on TRAIN only) ──
    global TRIED
    minute = ((D.times // 60_000) % 60).astype(int)
    hour_next = (D.hour + 1) % 24                        # bar i opens at hh:45 → the entry bar opens at (hh+1):00
    m45 = np.broadcast_to(minute == 45, (NS, T))
    best4 = None
    # hour scan on TRAIN entries only, with the own simulator (identical to harness.run for these exits — checked above;
    # harness.run on ~900k entries does not fit the 2 GB memory limit of this box)
    i_tr0 = int(np.searchsorted(D.times, HN.TRAIN_START))
    i_tr1 = int(np.searchsorted(D.times, HN.TRAIN_END)) - 1
    kk_, ii_ = np.nonzero(m45[:, :i_tr1] & np.isfinite(ATR[:, :i_tr1]))
    sel = ii_ >= i_tr0
    kk_, ii_ = kk_[sel], ii_[sel]
    for mb in (2, 4):
        ex = dict(sl_atr=3.0, tps=[], max_bars=mb)
        S = sim(O, H, L, C, kk_, ii_, np.ones(len(ii_)), ATR[kk_, ii_] * 3.0, None, mb, "market")
        r, ei = S["gross"], S["i"]
        hrs, yrs = hour_next[ei], D.year[ei + 1]
        trm = np.ones(len(r), bool)
        del S
        cand = []
        for h in range(24):
            TRIED += 1
            mh = (hrs == h) & trm
            ys = [r[mh & (yrs == y)].mean() for y in (2021, 2022, 2023, 2024)]
            mu, se = r[mh].mean(), r[mh].std() / np.sqrt(mh.sum())
            if all(np.sign(y_) == np.sign(mu) for y_ in ys):
                cand.append((abs(mu / se), h, int(np.sign(mu)), mu))
        cand.sort(reverse=True)
        pick = cand[:3]
        hs = {h: s for _, h, s, _ in pick}
        side = np.zeros((NS, T), int)
        for h, s in hs.items():
            side[:, (minute == 45) & (hour_next == h)] = s
        E2 = entries_from_mask(side != 0, side, gap=1)
        avg, n = gross_avg(run(E2, ex, cost=0.0), HN.TRAIN_START, HN.TRAIN_END)
        log(f"A4 ToD hold {mb} bars: consistent-sign hours {[(h, s, round(mu * 100, 3)) for _, h, s, mu in cand]} → pick "
            f"{hs}, TRAIN gross {avg * 100:+.3f}% n={n}")
        if best4 is None or avg > best4[0]:
            best4 = (avg, mb, hs, E2, ex)
    _, mb, hs, E, ex = best4
    hrs_ru = ", ".join(f"{h:02d}:00 UTC — {'лонг' if s > 0 else 'шорт'}" for h, s in sorted(hs.items()))
    out.append(finalize_a("scalp_tod_drift", "Дрейф по часам суток",
                          f"Каждый день в выбранные на TRAIN часы ({hrs_ru}) входим на открытии часа во всех монетах; "
                          f"{exit_ru(ex)}. {COSTS_RU}", E, ex, {"hours": {str(h): s for h, s in hs.items()}, "hold_bars": mb,
                                                               "selection": "top-3 hours by |t| with the same sign in 2021–2024"}))

    # ── A5 taker-flow burst (2023+, real data only) ──
    t23 = int(np.searchsorted(D.times, ms(2023)))
    with np.errstate(all="ignore"):
        x = np.log(D.TAKER)
    df = pd.DataFrame(x.T)
    mu = df.rolling(96, min_periods=48).mean().values.T
    sdv = df.rolling(96, min_periods=48).std().values.T
    with np.errstate(all="ignore"):
        z = (x - mu) / sdv
    real = np.nan_to_num(D.IMP, nan=1.0) == 0
    sig = {}
    for kx in (2.0, 2.5):
        for mode, sgn in (("follow", 1), ("fade", -1)):
            side = np.where(real & (z > kx), sgn, np.where(real & (z < -kx), -sgn, 0)).astype(np.int8)
            sig[f"{mode}_z{kx}"] = (side != 0, side, {"taker_z": kx, "mode": mode})
    b = search_a("A5 taker burst", sig, min_n=200, start=t23, train=(ms(2023), HN.TRAIN_END))
    _, _, sname, ename, E, ex, sp = b
    dir_ru = "в ту же сторону (за покупателями — лонг)" if sp["mode"] == "follow" else "против всплеска (после рывка покупок — шорт)"
    out.append(finalize_a("scalp_taker_burst", "Всплеск рыночных покупок/продаж (taker)",
                          f"Если доля агрессивных покупок к продажам за 15 мин отклонилась от нормы последних суток больше чем на "
                          f"{sp['taker_z']:g} сигмы, входим {dir_ru} на открытии следующей свечи; {exit_ru(ex)}. Только 2023+ "
                          f"(TRAIN 2023–2024). {COSTS_RU}", E, ex, dict(sp, exit=ename, data_from="2023-01")))
    return out


# ═════════════════════════════════════════════════════ PART B (BTC 1m, own simulator) ══
B_TRAIN = (int(dt.datetime(2021, 1, 1, tzinfo=UTC).timestamp()), int(dt.datetime(2024, 1, 1, tzinfo=UTC).timestamp()))
B_END = int(dt.datetime(2026, 9, 30, tzinfo=UTC).timestamp())   # cut before the Bitstamp maintenance block


def load_1m():
    if os.path.exists(BTC1M_CACHE):
        z = np.load(BTC1M_CACHE)
        df = pd.DataFrame({c: z[c] for c in "tohlcv"})
    else:
        parts = []
        for ch in pd.read_csv(BTC1M_GZ, chunksize=2_000_000):
            ch.columns = list("tohlcv")
            ch = ch[ch.t >= B_TRAIN[0]]
            if len(ch):
                parts.append(ch)
        if os.path.exists(BTC1M_UPD):
            u = pd.read_csv(BTC1M_UPD)
            u.columns = list("tohlcv")
            parts.append(u)
        df = pd.concat(parts).sort_values("t").drop_duplicates("t")
        g = pd.DataFrame({"t": np.arange(df.t.min(), df.t.max() + 60, 60)})
        df = g.merge(df, on="t", how="left")
        df["c"] = df["c"].ffill()
        for c in "ohl":
            df[c] = df[c].fillna(df["c"])
        df["v"] = df["v"].fillna(0.0)
        os.makedirs(os.path.dirname(BTC1M_CACHE), exist_ok=True)
        np.savez(BTC1M_CACHE, **{c: df[c].values for c in "tohlcv"})
    df = df[df.t < B_END].reset_index(drop=True)
    return df


def stats_1m(t_s, ret, hold_min, label):
    """Same keys as harness.stats; TRAIN 2021–2023, TEST 2024-01 → 2026-09; one instrument → top_coin_share = 1."""
    if len(ret) == 0:
        return {"n": 0, "data": "BTC 1m Bitstamp"}
    order = np.argsort(t_s)
    t_s, ret, hold_min = t_s[order], ret[order], hold_min[order]
    yr = pd.to_datetime(t_s, unit="s").year.values

    def s(mask):
        x = ret[mask]
        if not len(x):
            return {"n": 0}
        gp, gl = x[x > 0].sum(), -x[x <= 0].sum()
        return {"n": int(len(x)), "avg_pct": round(float(x.mean() * 100), 3), "win": round(float((x > 0).mean()), 3),
                "pf": round(float(gp / gl), 2) if gl > 0 else None, "sum_pct": round(float(x.sum() * 100), 1)}

    tr = (t_s >= B_TRAIN[0]) & (t_s < B_TRAIN[1])
    te = t_s >= B_TRAIN[1]
    years = {int(y): s(yr == y) for y in sorted(set(yr.tolist()))}
    cum = np.cumsum(ret)
    dd = float((cum - np.maximum.accumulate(cum)).min() * 100)
    span = max(1.0, (t_s.max() - t_s.min()) / 86400)
    out = {"label": label, "all": s(np.ones(len(ret), bool)), "train": s(tr), "test": s(te), "years": years,
           "pos_years": sum(1 for v in years.values() if v.get("n", 0) >= 10 and v["avg_pct"] > 0),
           "years_n": sum(1 for v in years.values() if v.get("n", 0) >= 10),
           "max_dd_sum_pct": round(dd, 1), "top_coin_share": 1.0, "per_day": round(len(ret) / span, 2),
           "median_hold_h": round(float(np.median(hold_min)) / 60, 3), "data": "BTC 1m Bitstamp",
           "train_period": "2021-01 → 2023-12", "test_period": "2024-01 → 2026-09"}
    return out


def passes_ex_coin(st):
    te = st.get("test") or {}
    return bool(te.get("n", 0) >= 60 and (te.get("avg_pct") or -1) > 0.05 and st.get("pos_years", 0) >= 4)


EXB = {}
for _h in (5, 15, 30):
    EXB[f"s4t1_{_h}"] = dict(sl_atr=4.0, tp_r=1.0, max_bars=_h)
    EXB[f"time_{_h}"] = dict(sl_atr=8.0, tp_r=None, max_bars=_h)


def thin(idx, gap):
    """At most one signal every `gap` bars (the first of a cluster) — one position at a time."""
    out, last = [], -10 ** 9
    for x in idx:
        if x - last >= gap:
            out.append(x)
            last = x
    return np.array(out, int)


def part_b():
    global TRIED
    log("Part B: loading BTC 1m")
    df = load_1m()
    t = df.t.values
    o, h, l, c, v = (df[x].values.astype(float) for x in "ohlcv")
    del df
    N = len(c)
    log(f"   1m bars: {N:,}  {pd.to_datetime(t[0], unit='s')} → {pd.to_datetime(t[-1], unit='s')}")
    O2, H2, L2, C2 = o[None], h[None], l[None], c[None]
    r1 = np.r_[np.nan, c[1:] / c[:-1] - 1]
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    S = pd.Series
    sig60 = S(r1).rolling(60, min_periods=60).std().values
    atr60 = S(tr).rolling(60, min_periods=60).mean().values
    vmean = S(v).rolling(240, min_periods=240).mean().values
    live = (v > 0) & (atr60 > 0)
    t_train = (t >= B_TRAIN[0]) & (t < B_TRAIN[1])

    def shift(x, m):
        return np.r_[np.full(m, np.nan), x[:-m]]

    def run_b(sig_idx, side_arr, ex, mode="market"):
        ii = thin(sig_idx, ex["max_bars"])
        ii = ii[ii + ex["max_bars"] + 1 < N]
        sd = atr60[ii] * ex["sl_atr"]
        parts = [sim(O2, H2, L2, C2, np.zeros(len(ch), int), ch, side_arr[ch], sd[a:a + len(ch)], ex["tp_r"],
                     ex["max_bars"], mode, fill_win=3)
                 for a, ch in ((a, ii[a:a + 40_000]) for a in range(0, max(1, len(ii)), 40_000))]
        R = {key: np.concatenate([p_[key] for p_ in parts]) for key in ("k", "i", "sg", "e", "x", "gross", "kind")}
        R["n_sig"] = sum(p_["n_sig"] for p_ in parts)
        R["n_fill"] = sum(p_["n_fill"] for p_ in parts)
        return R

    def search_b(idea, signals, min_n=300):
        global TRIED
        rows, best = [], None
        for sname, (cond, side, sp) in signals.items():
            idx = np.flatnonzero(cond & live & (side != 0))
            for ename, ex in EXB.items():
                R = run_b(idx, side, ex)
                m = t_train[R["e"]]
                avg, n = (float(R["gross"][m].mean()) if m.any() else np.nan), int(m.sum())
                TRIED += 1
                rows.append((avg, n, sname, ename))
                if n >= min_n and np.isfinite(avg) and (best is None or avg > best[0]):
                    best = (avg, n, sname, ename, idx, side, ex, sp)
        rows.sort(key=lambda r: -(r[0] if np.isfinite(r[0]) else -9))
        log(f"{idea}: {len(rows)} variants; top by TRAIN gross avg %:")
        for r in rows[:5]:
            print(f"      {r[2]:<22} {r[3]:<10} n={r[1]:<7} gross={r[0] * 100:+.4f}%")
        return best

    def finalize_b(rid, name_ru, rule_ru, best):
        avg, n, sname, ename, idx, side, ex, sp = best
        R = run_b(idx, side, ex, "market")
        RL = run_b(idx, side, ex, "limit")
        hold = (R["x"] - R["e"] + 1).astype(float)
        st = stats_1m(t[R["e"]], R["gross"] - COST, hold, rid + " taker")
        stm = stats_1m(t[R["e"]], R["gross"] - MAKER, hold, rid + " maker")
        stl = stats_1m(t[RL["e"]], RL["gross"] - limit_cost(RL["kind"]), (RL["x"] - RL["e"] + 1).astype(float), rid + " limit")
        stl["fill_rate"] = round(RL["n_fill"] / max(1, RL["n_sig"]), 3)
        # gross move vs costs: how big is the average absolute move the strategy captures?
        st["gross_avg_pct"] = round(float(R["gross"].mean() * 100), 4)
        st["median_stop_pct"] = round(float(np.median(atr60[R["i"]] * ex["sl_atr"] / c[R["i"]]) * 100), 3)
        ex_out = {"sl_atr1m60": ex["sl_atr"], "tps": [(ex["tp_r"], 1.0)] if ex["tp_r"] else [], "max_bars_1m": ex["max_bars"]}
        return {"id": rid, "name_ru": name_ru, "rule_ru": rule_ru, "exit": ex_out, "params": dict(sp, exit=ename),
                "costs": "taker 0.14%", "stats": st, "stats_maker": stm, "stats_limit": stl,
                "passes_maker": passes(stm), "passes_limit": passes(stl),
                "passes_ex_coin_rule": {"taker": passes_ex_coin(st), "maker": passes_ex_coin(stm), "limit": passes_ex_coin(stl)}}

    def exit_b_ru(ex):
        if ex["tp_r"]:
            return (f"стоп {ex['sl_atr']:g}×ATR(1м, 60 мин), цель {ex['tp_r'] * ex['sl_atr']:g}×ATR, выход по времени через "
                    f"{ex['max_bars']} мин")
        return f"без цели, страховочный стоп {ex['sl_atr']:g}×ATR(1м, 60 мин), выход по времени через {ex['max_bars']} мин"

    out = []
    vsum = {m: S(v).rolling(m, min_periods=m).sum().values for m in (1, 3, 5)}
    rm = {m: np.r_[np.full(m, np.nan), c[m:] / c[:-m] - 1] for m in (1, 3, 5)}

    # B1 momentum burst with volume → follow
    sig = {}
    with np.errstate(all="ignore"):
        for m in (1, 5):
            sp_ = shift(sig60, m)
            for kx in (3.0, 5.0):
                cond = (np.abs(rm[m]) > kx * sp_ * np.sqrt(m)) & (vsum[m] > 3 * m * shift(vmean, m))
                side = np.nan_to_num(np.sign(rm[m])).astype(int)
                sig[f"m{m}_k{kx}"] = (cond, side, {"burst_min": m, "burst_sigma": kx, "volume_x": 3})
    b = search_b("B1 1m momentum burst", sig)
    sp = b[7]
    out.append(finalize_b("scalp1m_burst_follow", "BTC 1м: импульс с объёмом — по ходу",
                          f"Если BTC за {sp['burst_min']} мин сдвинулся больше чем на {sp['burst_sigma']:g} обычных минутных колебаний "
                          f"при объёме в 3 раза выше обычного, входим в сторону рывка на открытии следующей минуты; {exit_b_ru(b[6])}. "
                          f"{COSTS_RU}", b))

    # B2 micro mean-reversion after a 1–3 min spike → fade
    sig = {}
    with np.errstate(all="ignore"):
        for m in (1, 3):
            sp_ = shift(sig60, m)
            for kx in (4.0, 6.0):
                cond = np.abs(rm[m]) > kx * sp_ * np.sqrt(m)
                side = -np.nan_to_num(np.sign(rm[m])).astype(int)
                sig[f"m{m}_k{kx}"] = (cond, side, {"spike_min": m, "spike_sigma": kx})
    b = search_b("B2 1m spike fade", sig)
    sp = b[7]
    out.append(finalize_b("scalp1m_spike_fade", "BTC 1м: откат после минутного всплеска",
                          f"Если BTC за {sp['spike_min']} мин дёрнулся больше чем на {sp['spike_sigma']:g} обычных минутных колебаний, "
                          f"входим против рывка на открытии следующей минуты; {exit_b_ru(b[6])}. {COSTS_RU}", b))

    # B3 range breakout (± compression) → follow
    sig = {}
    with np.errstate(all="ignore"):
        for n in (30, 60):
            hh = shift(S(h).rolling(n).max().values, 1)
            ll = shift(S(l).rolling(n).min().values, 1)
            width = (hh - ll) / c
            med = shift(S(width).rolling(1440, min_periods=720).median().values, 1)
            side = np.where(c > hh, 1, np.where(c < ll, -1, 0))
            for flt in ("none", "tight"):
                cond = side != 0
                if flt == "tight":
                    cond = cond & (width < 0.6 * med)
                sig[f"n{n}_{flt}"] = (cond, side, {"range_min": n, "compression": flt})
    b = search_b("B3 1m range breakout", sig)
    sp = b[7]
    comp_ru = " после затишья (диапазон уже 60 % обычного за сутки)" if sp["compression"] == "tight" else ""
    out.append(finalize_b("scalp1m_range_break", "BTC 1м: пробой минутного диапазона",
                          f"Если минутная свеча закрылась выше максимума (ниже минимума) последних {sp['range_min']} мин{comp_ru}, "
                          f"входим в сторону пробоя на открытии следующей минуты; {exit_b_ru(b[6])}. {COSTS_RU}", b))

    # B4 New York open: first 5/15 minutes → follow
    sig = {}
    t0 = int(t[0])
    d_first = dt.datetime.fromtimestamp(t0, UTC).date()
    opens = []
    for dd in range((int(t[-1]) - t0) // 86400 + 2):
        date = d_first + dt.timedelta(days=dd)
        if date.weekday() >= 5:
            continue
        ts = int(dt.datetime(date.year, date.month, date.day, 9, 30, tzinfo=ZoneInfo("America/New_York")).timestamp())
        bb = (ts - t0) // 60
        if 0 <= bb < N - 100:
            opens.append(bb)
    opens = np.array(opens)
    for m in (5, 15):
        cond = np.zeros(N, bool)
        side = np.zeros(N, int)
        ii = opens + m - 1
        r_ = c[ii] / o[opens] - 1
        cond[ii] = r_ != 0
        side[ii] = np.sign(r_).astype(int)
        sig[f"first{m}"] = (cond, side, {"first_min": m})
    b = search_b("B4 NY-open 1m", sig, min_n=300)
    sp = b[7]
    out.append(finalize_b("scalp1m_ny_open", "BTC 1м: импульс открытия Нью-Йорка",
                          f"По будням в 9:30 по Нью-Йорку смотрим первые {sp['first_min']} мин: в какую сторону ушёл BTC, туда и входим "
                          f"на открытии следующей минуты; {exit_b_ru(b[6])}. {COSTS_RU}", b))
    return out


def main():
    res_a = part_a()
    log(f"Part A done, tried so far {TRIED}")
    import gc
    HN._D = None          # Part B does not need the 15m arrays; the box has a ~2 GB memory limit
    gc.collect()
    res_b = part_b()
    log(f"Part B done, tried total {TRIED}")
    results = res_a + res_b
    notes = (
        "Скальпинг (короткие сделки 15 мин – 2 ч на 15м-свечах 18 монет и 5–30 мин на минутках BTC). Главное: издержки съедают всё. "
        "Каждая стратегия показана тремя способами: 1) рыночные ордера 0,14 % за круг (это реальность для бота с маркет-входом — "
        "основной вердикт, поле stats); 2) лимитные 0,04 % при условии, что исполняется КАЖДЫЙ сигнал (stats_maker) — это верхняя "
        "граница, в жизни недостижима; 3) реалистичный лимит (stats_limit): лимитка по цене закрытия сигнальной свечи, считается "
        "исполненной, только если цена прошла сквозь неё на 0,02 % в течение следующей 15м-свечи (на минутках — 3 минут), в свече "
        "исполнения считается только стоп, выход по цели — лимиткой (0,02 %), по стопу/времени — рынком (0,07 %). Неисполненные "
        "сигналы пропускаются — поэтому лимит сам отбирает худшие сделки (цена пришла к нам, потому что идёт против нас). "
        "Параметры выбраны по TRAIN (15м: 2021–2024, taker-поток: 2023–2024; минутки BTC: 2021–2023) по средней сделке без "
        "комиссии; TEST (15м: 2025-01 → 2026-09; минутки: 2024-01 → 2026-09) для выбора не использовался. "
        "Минутки — Bitstamp BTC/USD спот (история + файл обновлений, до 2026-09-29), а торговать бот будет фьючерсы — "
        "микроструктура, спред и фитили другие; правило «одна монета ≤ 50 % прибыли» для одной монеты BTC не выполнимо по "
        "определению, поэтому для минуток есть отдельное поле passes_ex_coin_rule. "
        f"Всего проверено вариантов: {TRIED}.")
    path = os.path.join(HERE, "results", "scalp.json")
    report(path, "scalp", results, TRIED, notes)
    log(f"wrote {path}")
    print("\nSUMMARY (avg % per trade after costs; n = TEST trades)")
    print(f"{'id':<24}{'tk_tr':>8}{'tk_te':>8}{'mk_tr':>8}{'mk_te':>8}{'lim_tr':>8}{'lim_te':>8}{'fill':>6}{'n_te':>7}"
          f"{'n/day':>7}{'posY':>5} pass(t/m/l)")
    for r in results:
        st, sm, sl = r["stats"], r["stats_maker"], r["stats_limit"]
        g = lambda s, p: (s.get(p) or {}).get("avg_pct", float("nan"))
        print(f"{r['id']:<24}{g(st, 'train'):>8.3f}{g(st, 'test'):>8.3f}{g(sm, 'train'):>8.3f}{g(sm, 'test'):>8.3f}"
              f"{g(sl, 'train'):>8.3f}{g(sl, 'test'):>8.3f}{sl.get('fill_rate', 0):>6.2f}{(st.get('test') or {}).get('n', 0):>7}"
              f"{st.get('per_day', 0):>7.1f}{st.get('pos_years', 0):>5} {r['passes']}/{r['passes_maker']}/{r['passes_limit']}"
              + (f" ex-coin {r['passes_ex_coin_rule']}" if "passes_ex_coin_rule" in r else ""))
        print("      years taker:", {y: v.get("avg_pct") for y, v in st["years"].items()}, " maker pos_years", sm.get("pos_years"))


if __name__ == "__main__":
    main()
