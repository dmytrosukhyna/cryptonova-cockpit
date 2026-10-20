"""Связка движков (backlog #2, 20.10.2026): the month-by-month results of our four futures desks side by side — how they move
together, and which mix of them keeps the drawdown within −25 %. Runs in GitHub Actions only (.github/workflows/research-blend.yml).

Each sleeve is simulated with its LIVE rules and sizing on its own capital, on the honest data each one was tested on:
  own  «Сигналы CRYPTO NOVA» — every Binance USDT-M perp incl. delisted, point-in-time top 40 by 30-day $ volume, the volume mark
       1.5×, longs only in the BTC «рост» regime, risk 0.5 % a trade, ≤ 40 open, gross ≤ 2× (own_signals_study.flow_account,
       daily mark-to-market);
  ls   «Нейтральная корзина» — long the top 10 / short the bottom 10 by the quality composite, weights by 30-day σ, gross 0.75,
       monthly rebalance on the 1st, real Binance funding, 0.2 % on the replaced part (own_signals_study.ls_neutral);
  lst  «Шорт листингов» — every Binance USDT-M listing, short 4 h after, stop +30 %, 14 days, live filters, 5 % a coin, ≤ 20
       open (turbo_study, realised at the exit);
  btc  «BTC ×2 в росте» — BTC 2× only in «рост», 4 % cash otherwise, funding paid (turbo_study.btc_part, daily).
Then on the common months: correlations (all months and the months BTC fell > 10 %), and mixes — weights in steps of 10 % across
the four (286) × scale 1 / 1.5 / 2 / 3 (= 1 144 variants), rebalanced monthly. The mix is CHOSEN on the first half of the months
and SHOWN on the second half (walk-forward), next to the fixed equal mix (nothing fitted). The drawdown of a mix is measured on
month ends — it understates the intra-month dip; the single sleeves show both."""
import datetime as dt
import itertools
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from fetch_alt100 import note  # noqa: E402
import own_signals_study as OS  # noqa: E402
import turbo_study as TS  # noqa: E402

DAY = 86_400_000
NAMES = ("own", "ls", "lst", "btc")


def ym(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m")


def monthly(points: list) -> dict:
    """[(ms, equity)] → {YYYY-MM: return of that month} from month-end equity (first month vs the first point)."""
    last, first = {}, None
    for t, v in points:
        last[ym(t)] = v
        first = first if first is not None else v
    out, prev = {}, first
    for m in sorted(last):
        out[m] = last[m] / prev - 1 if prev > 0 else -1.0
        prev = last[m]
    return out


def daily_dd(points: list) -> float:
    eq = np.array([v for _t, v in points])
    return float((eq / np.maximum.accumulate(eq) - 1).min()) if len(eq) else 0.0


def sleeve_own():
    D, C, days = OS._wide_data()
    curve = []
    OS.flow_account(C, D, days, min_surge=1.5, surge_key="surge_vc30", score="surge_vc30", max_open=40, top=40, curve_out=curve)
    return monthly(curve), daily_dd(curve)


def sleeve_ls():
    out = {}
    OS.ls_neutral(iv=True, ret_out=out)
    rets = out[0.75]
    return {ym(d1 - DAY): r for d1, r in rets}, None


def sleeve_lst():
    syms = sorted(s for s in TS.LS.vision_symbols() if s.endswith("USDT"))
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(16) as ex:
        data = [d for d in ex.map(lambda s: TS.LS.load(s, TS.TODAY), syms) if d]
    trs = [x for d in data if (x := TS.trade(d)) is not None]
    trs = sorted((x for x in trs if not (x[3] is not None and x[3] >= 0.10) and not (x[4] is not None and x[4] < 2e6)),
                 key=lambda x: x[1])
    E, open_, pts = 1.0, [], [(trs[0][1], 1.0)]
    for r, te, tx, *_ in trs:
        for o in sorted([o for o in open_ if o[1] <= te], key=lambda o: o[1]):
            E += o[0] * o[2]
            pts.append((o[1], E))
            open_.remove(o)
        if len(open_) < 20:
            open_.append((0.05 * E, tx, r))
    for o in sorted(open_, key=lambda o: o[1]):
        E += o[0] * o[2]
        pts.append((o[1], E))
    return monthly(pts), daily_dd(pts)


def sleeve_btc():
    k = TS._rows(lambda m: f"https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-{m}.zip", "2019-01")
    days = {}
    for r in k:
        t = int(r[0]); t = t // 1000 if t > 10 ** 14 else t
        days[t - t % DAY] = float(r[4])
    f = TS._rows(lambda m: f"https://data.binance.vision/data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-{m}.zip", "2019-09")
    fund = {}
    for r in f:
        t = int(r[0])
        fund[t - t % DAY] = fund.get(t - t % DAY, 0.0) + float(r[2])
    ts = sorted(days)
    c = np.array([days[t] for t in ts])
    E, pts, btc_m = 1.0, [], {}
    t21 = int(dt.datetime(2021, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    for i in range(260, len(ts) - 1):
        if ts[i] < t21:
            continue
        if not pts:
            pts.append((ts[i], 1.0))
        bull = c[i] > c[i - 199:i + 1].mean() and c[i] > c[i - 60]
        r = c[i + 1] / c[i] - 1
        E *= max(0.0, 1 + 2 * r - 2 * fund.get(ts[i + 1], 0.0)) if bull else 1 + 0.04 / 365
        pts.append((ts[i + 1], E))
    raw = [(t, v) for t, v in zip(ts, c) if t >= t21]
    return monthly(pts), daily_dd(pts), monthly(raw)


def stats(v: np.ndarray) -> dict:
    eq = np.cumprod(1 + v)
    dd = float((eq / np.maximum.accumulate(np.r_[1.0, eq])[1:] - 1).min())
    return {"m": round(float(v.mean()) * 100, 2), "wm": round(float(v.min()) * 100, 1), "dd_me": round(dd * 100, 1),
            "plus": round(float((v > 0).mean()) * 100), "x": round(float(eq[-1]), 2),
            "sh": round(float(v.mean() / (v.std() + 1e-12) * np.sqrt(12)), 2)}


def main():
    S, ddd = {}, {}
    for name, fn in (("btc", sleeve_btc), ("lst", sleeve_lst), ("own", sleeve_own), ("ls", sleeve_ls)):
        try:
            res = fn()
        except Exception as e:  # noqa: BLE001 — one broken sleeve should not hide the others
            note(f"blend · sleeve {name} failed", repr(e)[:500], level="warning")
            continue
        S[name], ddd[name] = res[0], res[1]
        if name == "btc":
            btc_raw = res[2]
    last_full = (TS.TODAY.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
    have = [n for n in NAMES if n in S]
    months = sorted(set.intersection(*[set(S[n]) for n in have]))
    months = [m for m in months if m <= last_full]
    R = np.array([[S[n][m] for n in have] for m in months])
    single = {n: {**stats(R[:, j]), "dd_daily": round(ddd[n] * 100, 1) if ddd[n] is not None else None,
                  "months_own": len(S[n])} for j, n in enumerate(have)}
    corr = np.corrcoef(R.T)
    bad = np.array([btc_raw.get(m, 0) < -0.10 for m in months])
    corr_bad = np.corrcoef(R[bad].T) if bad.sum() >= 4 else None
    bad_avg = {n: round(float(R[bad, j].mean()) * 100, 2) for j, n in enumerate(have)} if bad.any() else {}
    note("blend · sleeves on the common months", json.dumps({"from": months[0], "to": months[-1], "n": len(months),
         "btc_down10_months": int(bad.sum()), "single": single, "avg_in_btc_down10": bad_avg,
         "corr": {f"{a}/{b}": round(float(corr[i, j]), 2) for i, a in enumerate(have) for j, b in enumerate(have) if i < j},
         "corr_btc_down10": ({f"{a}/{b}": round(float(corr_bad[i, j]), 2) for i, a in enumerate(have) for j, b in enumerate(have)
                              if i < j} if corr_bad is not None else None),
         "months_table": {m: [round(float(x) * 100, 1) for x in R[q]] for q, m in enumerate(months)}}, ensure_ascii=False))

    k = len(have)
    grid = [w for w in itertools.product(range(11), repeat=k) if sum(w) == 10]
    scales = (1.0, 1.5, 2.0, 3.0)
    half = len(months) // 2
    A, B = R[:half], R[half:]

    def mix(Rx, w, sc):
        return Rx @ (np.array(w) / 10.0) * sc
    best, rows = None, []
    for w in grid:
        for sc in scales:
            a = stats(mix(A, w, sc))
            if a["dd_me"] >= -25 and (best is None or a["m"] > best[2]["m"]):
                best = (w, sc, a)
    tried = len(grid) * len(scales)
    eq_w = tuple([10 // k] * k)
    res = {"variants": tried, "split": f"{months[0]}…{months[half - 1]} | {months[half]}…{months[-1]}",
           "chosen_on_first_half": {"w": dict(zip(have, [x * 10 for x in best[0]])), "scale": best[1], "first": best[2],
                                    "second": stats(mix(B, best[0], best[1])), "all": stats(mix(R, best[0], best[1]))} if best else None}
    for sc in scales:
        res[f"equal ×{sc}"] = {"first": stats(mix(A, eq_w, sc)), "second": stats(mix(B, eq_w, sc)), "all": stats(mix(R, eq_w, sc))}
    for j, n in enumerate(have):                     # each sleeve alone, at the scale that fits −25 % on the first half
        w = tuple(10 if q == j else 0 for q in range(k))
        fit = max([sc for sc in scales if stats(mix(A, w, sc))["dd_me"] >= -25], default=None)
        if fit:
            res[f"{n} alone ×{fit}"] = {"first": stats(mix(A, w, fit)), "second": stats(mix(B, w, fit))}
    top = sorted(((stats(mix(R, w, sc)), w, sc) for w in grid for sc in scales), key=lambda x: -x[0]["m"])
    res["in_sample_best_dd25_all_months"] = next(({"w": dict(zip(have, [x * 10 for x in w])), "scale": sc, **s}
                                                 for s, w, sc in top if s["dd_me"] >= -25), None)
    note("blend · mixes (chosen on the first half, shown on the second)", json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
