"""Strategy lab harness — one honest way to test many strategy ideas on the same data.

Data: 18 coins, Binance USDT-M futures, 15-minute bars 2020-09 → 2026-09 (scripts/ap_data.py: OHLC, quote volume,
long/short liquidations, open interest, funding, futures/spot CVD, top-trader long/short ratio, taker buy/sell ratio,
basis). Derivatives metrics before 2023 are partly imputed (column is_imputed_metrics) — rules that need them are
judged on 2023+ only.

Usage in a family script:
    from harness import D, entries_from_mask, run, stats, grid_exits, report
    sig = D.ret(16) <= -0.04                      # bool [coin, bar], known at the CLOSE of bar i
    E = entries_from_mask(sig, side=+1, gap=16)   # entry at the OPEN of bar i+1; at most one per `gap` bars per coin
    T = run(E, dict(sl_atr=2.0, tps=[(1.0, 1.0)], max_bars=96))   # TP/SL in ATR(14, 15m) multiples or "pct"
    print(stats(T))

Exits (exit dict):
    sl_atr / sl_pct           stop distance (ATR multiples of the entry bar, or a fraction of price)
    tps=[(dist, share), …]    take-profits at dist × stop distance (R multiples) — shares sum to 1 (rest leaves at time exit)
    be_after_tp1=True         after TP1 the stop moves to the entry price for the rest
    max_bars=96               time exit at the close of the last bar (15m bars: 96 = 24 h)
    trail_atr=None            optional trailing stop (ATR multiples from the best close) after TP1
Rules of the bar (conservative): if the stop and a target are both inside one bar, the stop counts first. The entry bar
itself can hit the stop/targets (price moves after the open). Costs: 0.14 % per round trip (taker 0.05 % × 2 + slippage
0.02 % × 2) by default; `cost=0.0004` = limit orders on both sides.

Honesty rules for the whole lab (also in README.md):
    • the signal uses only data up to the close of bar i; the trade opens at the next bar's open;
    • parameters are chosen on TRAIN = 2021-01 → 2024-12; the verdict is on TEST = 2025-01 → 2026-09 (never used to choose);
    • a strategy passes only if TEST avg > +0.05 % per trade after costs, TEST n ≥ 60, and at least 4 of the 6 calendar years
      (2021…2026) are positive; and no single coin brings more than half of the profit;
    • every family reports how many variants it tried (more variants → more luck; keep grids small).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from ap_data import load  # noqa: E402

COST = 0.0014
TRAIN_END = int(_dt.datetime(2025, 1, 1, tzinfo=_dt.timezone.utc).timestamp() * 1000)
TRAIN_START = int(_dt.datetime(2021, 1, 1, tzinfo=_dt.timezone.utc).timestamp() * 1000)


WIDE = os.environ.get("CNQ_LAB_SET", "") == "wide"           # CNQ_LAB_SET=wide → the wide universe (alt100.npz, all2023)
WIDE_PATH = os.environ.get("CNQ_LAB_WIDE", "/tmp/claude-0/-home-claude/5491f1a8-ca5d-58c4-b6e0-e27b29449aba/scratchpad/lab/alt100_all2023.npz")
TOPN = int(os.environ.get("CNQ_LAB_TOPN", "50"))              # point-in-time: trade a coin only while it is in the top-N by 30-day volume
if WIDE:                                                      # wide data starts 2022-11-20 (+30 days to rank) → train 2023–2024
    TRAIN_START = int(_dt.datetime(2023, 1, 1, tzinfo=_dt.timezone.utc).timestamp() * 1000)
SUFFIX = f"_wide{TOPN}" if WIDE else ""                       # result files of a wide run: results/<family>_wide50.json
UNIVERSE_RU = f"топ-{TOPN} по объёму" if WIDE else "18"


def _load_wide():
    z = np.load(WIDE_PATH, allow_pickle=True)
    keys = ["open", "high", "low", "close", "volume_quote", "long_liq_usd", "short_liq_usd", "open_interest_usd", "funding_rate_pct",
            "future_cvd_15m", "spot_cvd_15m", "ls_ratio_top", "taker_volume_ratio", "is_imputed_metrics", "basis_index_bps"]
    shape = z["close"].shape
    nan = np.broadcast_to(np.float32(np.nan), shape)                # missing series: one read-only NaN view, no memory
    A = {k: (z[k].astype(np.float32) if k in z.files else nan) for k in keys}
    if "is_imputed_metrics" not in z.files:
        A["is_imputed_metrics"] = np.broadcast_to(np.float32(0), shape)
    return [f"{x}USDT" for x in z["syms"]], z["times"], A                # same names as the 18-coin set (BTCUSDT …)


class _Data:
    def __init__(self):
        syms, times, A = _load_wide() if WIDE else load()
        self.syms = [str(s) for s in syms]
        self.times = np.asarray(times, dtype=np.int64)
        self.O, self.H, self.L, self.C = A["open"], A["high"], A["low"], A["close"]
        self.V = A["volume_quote"]
        self.LLIQ, self.SLIQ = A["long_liq_usd"], A["short_liq_usd"]
        self.OI, self.FUND = A["open_interest_usd"], A["funding_rate_pct"]
        self.FCVD, self.SCVD = A["future_cvd_15m"], A["spot_cvd_15m"]
        self.LS, self.TAKER = A["ls_ratio_top"], A["taker_volume_ratio"]
        self.IMP, self.BASIS = A["is_imputed_metrics"], A["basis_index_bps"]
        self.NS, self.T = self.C.shape
        self.year = np.array([_dt.datetime.fromtimestamp(t / 1000, _dt.timezone.utc).year for t in self.times])
        self.hour = ((self.times // 3_600_000) % 24).astype(int)
        self.dow = ((self.times // 86_400_000 + 3) % 7).astype(int)        # 0 = Monday
        self.ATR = self._atr(14)
        self.ELIG = self._eligible(TOPN) if WIDE else None

    def _eligible(self, n):
        """[coin, bar] True while the coin is among the n most traded by trailing 30-day quote volume (known at that bar)."""
        V = np.nan_to_num(self.V.astype(np.float64))
        cs = np.cumsum(V, axis=1)
        w = 30 * 96
        el = np.zeros(self.C.shape, dtype=bool)
        for i in range(w, self.T, 96):                      # re-ranked once a day, applied to the next day
            vol = cs[:, i] - cs[:, i - w]
            ok = np.isfinite(self.C[:, i]) & (vol > 0)
            order = np.argsort(-np.where(ok, vol, -1))
            top = order[:n][ok[order[:n]]]
            el[top, i + 1:min(self.T, i + 97)] = True
        return el

    def _atr(self, n):
        H, L, C = self.H, self.L, self.C
        pc = np.concatenate([C[:, :1], C[:, :-1]], axis=1)
        tr = np.nanmax(np.stack([H - L, np.abs(H - pc), np.abs(L - pc)]), axis=0)
        tr = np.nan_to_num(tr)
        k = np.ones(n) / n
        out = np.full_like(C, np.nan)
        for i in range(self.NS):
            out[i, n - 1:] = np.convolve(tr[i], k, "valid")
        return out

    # handy series (all [coin, bar], value known at the close of the bar)
    def ret(self, n):
        out = np.full_like(self.C, np.nan)
        out[:, n:] = self.C[:, n:] / self.C[:, :-n] - 1
        return out

    def ema(self, x, n):
        a = 2 / (n + 1)
        out = np.full_like(x, np.nan)
        for i in range(x.shape[0]):
            v = np.nan
            row = x[i]
            for j in range(row.shape[0]):
                if np.isfinite(row[j]):
                    v = row[j] if not np.isfinite(v) else a * row[j] + (1 - a) * v
                out[i, j] = v
        return out

    def rolling(self, x, n, fn="max"):
        """Rolling max/min/mean/std over the last n bars INCLUDING the current one."""
        # pandas rolling, column by column (a sliding-window copy of [coins × bars × n] does not fit in memory for n ≈ 96)
        import pandas as pd
        r = pd.DataFrame(np.asarray(x, dtype=np.float64).T).rolling(n, min_periods=1)
        v = {"max": r.max, "min": r.min, "mean": r.mean, "sum": r.sum, "std": lambda: r.std(ddof=0)}[fn]().to_numpy().T
        if fn == "sum":
            v = np.nan_to_num(v)                                         # as np.nansum: an all-NaN window sums to 0
        out = np.full(np.shape(x), np.nan, dtype=np.result_type(x, np.float32))
        out[:, n - 1:] = v[:, n - 1:]
        return out

    def rsi(self, n=14, x=None):
        C = self.C if x is None else x
        d = np.diff(C, axis=1, prepend=np.nan)
        up, dn = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
        a = 1 / n
        out = np.full_like(C, np.nan)
        for i in range(C.shape[0]):
            au = ad = np.nan
            for j in range(C.shape[1]):
                if not np.isfinite(C[i, j]):
                    continue
                if not np.isfinite(au):
                    au, ad = up[i, j], dn[i, j]
                else:
                    au = a * up[i, j] + (1 - a) * au
                    ad = a * dn[i, j] + (1 - a) * ad
                out[i, j] = 100.0 if ad == 0 else 100 - 100 / (1 + au / ad)
        return out


_D = None


def data() -> _Data:
    global _D
    if _D is None:
        _D = _Data()
    return _D


class _Lazy:
    def __getattr__(self, k):
        return getattr(data(), k)


D = _Lazy()


def entries_from_mask(mask, side, gap: int = 16, start: int | None = None):
    """Bool [coin, bar] → list of (coin, bar, side). side: +1 long, −1 short, or an int array of the same shape (+1/−1/0).
    At most one entry per coin every `gap` bars (the first signal of a cluster)."""
    d = data()
    t0 = start if start is not None else int(np.searchsorted(d.times, TRAIN_START))
    out = []
    sd = side if isinstance(side, np.ndarray) else None
    for k in range(d.NS):
        idx = np.flatnonzero(np.nan_to_num(mask[k], nan=0).astype(bool))
        last = -10 ** 9
        for i in idx:
            if i < t0 or i + 1 >= d.T or i - last < gap:
                continue
            if d.ELIG is not None and not d.ELIG[k, i]:
                continue
            sg = int(sd[k, i]) if sd is not None else int(side)
            if sg == 0:
                continue
            out.append((k, int(i), sg))
            last = i
    return out


def run(entries, exit: dict, cost: float = COST):
    """Simulate every entry independently (no portfolio limits). → list of trades
    {k, i (signal bar), e (entry bar), x (exit bar), side, ret (after costs), kind}."""
    d = data()
    O, H, L, C, ATR = d.O, d.H, d.L, d.C, d.ATR
    max_bars = int(exit.get("max_bars", 96))
    tps = exit.get("tps") or []
    be = bool(exit.get("be_after_tp1"))
    trail = exit.get("trail_atr")
    out = []
    for k, i, sg in entries:
        e = i + 1
        if e >= d.T or not np.isfinite(O[k, e]):
            continue
        px = O[k, e]
        if exit.get("sl_pct") is not None:
            sd = px * exit["sl_pct"]
        else:
            a = ATR[k, i]
            if not np.isfinite(a) or a <= 0:
                continue
            sd = a * exit.get("sl_atr", 2.0)
        end = min(d.T - 1, e + max_bars - 1)
        hh, ll, cc = H[k, e:end + 1], L[k, e:end + 1], C[k, e:end + 1]
        if not np.all(np.isfinite(hh)):
            good = np.isfinite(hh)
            if not good.any():
                continue
            last_good = np.flatnonzero(good)[-1]
            hh, ll, cc = hh[:last_good + 1], ll[:last_good + 1], cc[:last_good + 1]
        fav = hh * sg if sg > 0 else -ll    # side space: higher = better (long: high; short: −low)
        adv = ll * sg if sg > 0 else -hh    # side space: the worst point of the bar
        cl = cc * sg
        p0 = px * sg
        stop = p0 - sd
        n = len(cc)
        left, got, tp_hit = 1.0, 0.0, 0
        jt0 = js0 = 0                       # where to look for the next target / the stop
        x = n - 1
        done = False
        for n_, (dist, share) in enumerate(tps):
            target = p0 + dist * sd
            jt = np.flatnonzero(fav[jt0:] >= target)
            js = np.flatnonzero(adv[js0:] <= stop)
            jt = jt[0] + jt0 if len(jt) else None
            js = js[0] + js0 if len(js) else None
            if js is not None and (jt is None or js <= jt):        # stop first (same bar → stop first)
                got += left * (stop - p0) / px
                left, x, done = 0.0, js, True
                break
            if jt is None:
                break
            take = min(share, left)
            got += take * (target - p0) / px
            left -= take
            tp_hit, x = n_ + 1, jt
            jt0, js0 = jt, jt + 1                                  # next targets may fill in the same bar; the stop from the next bar
            if n_ == 0 and be:
                stop = max(stop, p0)
            if left <= 1e-9:
                done = True
                break
        if not done and left > 1e-9:
            if trail is not None and tp_hit:
                a = ATR[k, i] * trail
                st_ = stop
                xx = None
                for j in range(js0, n):
                    if adv[j] <= st_:
                        got += left * (st_ - p0) / px
                        xx = j
                        break
                    st_ = max(st_, cl[j] - a)
                if xx is None:
                    got += left * (cl[-1] - p0) / px
                    xx = n - 1
                x = xx
            else:
                js = np.flatnonzero(adv[js0:] <= stop)
                if len(js):
                    got += left * (stop - p0) / px
                    x = js0 + js[0]
                else:
                    got += left * (cl[-1] - p0) / px
                    x = n - 1
        out.append({"k": k, "i": i, "e": e, "x": e + int(x), "side": sg, "ret": got - cost, "tp": tp_hit})
    return out


def stats(trades, label: str = "") -> dict:
    d = data()
    if not trades:
        return {"n": 0}
    r = np.array([t["ret"] for t in trades])
    t = np.array([d.times[t_["e"]] for t_ in trades])
    yr = np.array([d.year[t_["e"]] for t_ in trades])
    k = np.array([t_["k"] for t_ in trades])

    def s(mask):
        x = r[mask]
        if not len(x):
            return {"n": 0}
        gp, gl = x[x > 0].sum(), -x[x <= 0].sum()
        return {"n": int(len(x)), "avg_pct": round(float(x.mean() * 100), 3), "win": round(float((x > 0).mean()), 3),
                "pf": round(float(gp / gl), 2) if gl > 0 else None, "sum_pct": round(float(x.sum() * 100), 1)}

    tr, te = (t >= TRAIN_START) & (t < TRAIN_END), t >= TRAIN_END
    years = {int(y): s(yr == y) for y in sorted(set(yr.tolist()))}
    cum = np.cumsum(r[np.argsort(t)])
    dd = float((cum - np.maximum.accumulate(cum)).min() * 100) if len(cum) else 0.0
    by_coin = {}
    for kk in set(k.tolist()):
        by_coin[d.syms[kk]] = float(r[k == kk].sum() * 100)
    tot = sum(v for v in by_coin.values() if v > 0)
    top_share = max(by_coin.values()) / tot if tot > 0 else None
    span_days = max(1.0, (t.max() - t.min()) / 86_400_000)
    # 18.10 (lab reports): a per-trade average can be carried by 5–10 days when every coin moves together.
    # So also: the average of daily averages, the test average without its 3 best days, and a 5-slot account.
    day = t // 86_400_000
    te_m = t >= TRAIN_END
    extra = {}
    if te_m.any():
        dte, rte = day[te_m], r[te_m]
        ud, inv = np.unique(dte, return_inverse=True)
        dsum = np.bincount(inv, weights=rte)
        dcnt = np.bincount(inv)
        extra["test_day_avg"] = round(float((dsum / dcnt).mean() * 100), 3)
        extra["test_days"] = int(len(ud))
        best = set(np.argsort(-dsum)[:3].tolist())
        keep = np.array([j not in best for j in inv])
        extra["test_ex3_avg"] = round(float(rte[keep].mean() * 100), 3) if keep.any() else None
    extra["pf5"] = portfolio(trades, 5)
    return {**extra, "label": label, "all": s(np.ones(len(r), bool)), "train": s(tr), "test": s(te), "years": years,
            "pos_years": sum(1 for y, v in years.items() if v.get("n", 0) >= 10 and v["avg_pct"] > 0),
            "years_n": sum(1 for y, v in years.items() if v.get("n", 0) >= 10),
            "max_dd_sum_pct": round(dd, 1), "top_coin_share": round(top_share, 2) if top_share is not None else None,
            "per_day": round(len(r) / span_days, 2), "median_hold_h": round(float(np.median([(t_["x"] - t_["e"] + 1) / 4 for t_ in trades])), 2)}


def portfolio(trades, slots: int = 5) -> dict:
    """A real account: at most `slots` positions at once (one per coin), each 1/slots of the start capital, no compounding.
    Trades are taken in order of entry; a signal that finds every slot busy is skipped. → % of capital by year / train / test."""
    d = data()
    busy, coins_open, taken = [], set(), []
    import heapq
    for tr in sorted(trades, key=lambda q: (q["e"], q["k"])):
        while busy and busy[0][0] < tr["e"]:
            _, kk = heapq.heappop(busy)
            coins_open.discard(kk)
        if len(busy) >= slots or tr["k"] in coins_open:
            continue
        heapq.heappush(busy, (tr["x"], tr["k"]))
        coins_open.add(tr["k"])
        taken.append(tr)
    if not taken:
        return {"taken": 0}
    r = np.array([q["ret"] for q in taken]) / slots * 100
    tx = np.array([d.times[q["x"]] for q in taken])
    yx = np.array([d.year[q["x"]] for q in taken])
    o = np.argsort(tx)
    cum = np.cumsum(r[o])
    years = {int(y): round(float(r[yx == y].sum()), 1) for y in sorted(set(yx.tolist()))}
    te = tx >= TRAIN_END
    ex3 = None
    if te.any():                                   # the account on TEST without its 3 best days (by the day a trade closed)
        dd_ = tx[te] // 86_400_000
        ud, inv = np.unique(dd_, return_inverse=True)
        dsum = np.bincount(inv, weights=r[te])
        ex3 = round(float(dsum.sum() - np.sort(dsum)[::-1][:3].sum()), 1)
    return {"slots": slots, "test_ex3_pct": ex3, "taken": int(len(taken)), "skipped": int(len(trades) - len(taken)),
            "train_pct": round(float(r[(tx >= TRAIN_START) & (tx < TRAIN_END)].sum()), 1), "test_pct": round(float(r[tx >= TRAIN_END].sum()), 1),
            "years": years, "pos_years": sum(1 for v in years.values() if v > 0),
            "max_dd_pct": round(float((cum - np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]).min()), 1)}


def passes(st: dict) -> bool:
    te = st.get("test") or {}
    yn = st.get("years_n", 0)
    if yn < 3:
        return False
    need = 4 if yn >= 6 else 3 if yn >= 4 else yn                   # 6 years (2021–26) → 4; 4 years (2023–26, wide set) → 3
    ok = (te.get("n", 0) >= 60 and (te.get("avg_pct") or -1) > 0.05 and st.get("pos_years", 0) >= need
          and (st.get("top_coin_share") or 1) <= 0.5)
    # robustness (added 18.10; older result files without these fields are judged on the old rule until re-run)
    if ok and "test_day_avg" in st:
        pf = st.get("pf5") or {}
        # the 5-slot account is what would really be traded: it must earn on TEST and in most years;
        # and the per-trade test average must survive dropping the 3 best days. (test_day_avg is shown, not required:
        # a day with 1 trade weighs as much as a day with 20 there.)
        # 18.10 (owner): the market of the last two years is what matters — the account must earn in 2025 AND in 2026,
        # and still be positive on 2025–26 without its 3 best days. (The per-trade test without the 3 best days is shown, not required:
        # a 5-slot account cannot take the 20 trades of a market-wide day anyway.)
        ys = pf.get("years") or {}
        ok = ((pf.get("test_pct") or -1) > 0 and (pf.get("test_ex3_pct") or -1) > 0 and pf.get("pos_years", 0) >= need
              and (ys.get(2025, ys.get("2025")) or -1) > 0 and (ys.get(2026, ys.get("2026")) or -1) > 0)
    return ok


# standard exit grid: everyone tries the same small set (1 target, 3 targets, 5 targets like the app, time exit)
def grid_exits(max_bars: int = 96):
    return {
        "1tp_1r":   dict(sl_atr=2.0, tps=[(1.0, 1.0)], max_bars=max_bars),
        "1tp_2r":   dict(sl_atr=2.0, tps=[(2.0, 1.0)], max_bars=max_bars),
        "3tp_be":   dict(sl_atr=2.0, tps=[(1.0, 1 / 3), (2.0, 1 / 3), (3.0, 1 / 3)], be_after_tp1=True, max_bars=max_bars),
        "5tp_be":   dict(sl_atr=2.0, tps=[(0.5, .2), (1.0, .2), (1.5, .2), (2.0, .2), (2.5, .2)], be_after_tp1=True, max_bars=max_bars),
        "tp1_trail": dict(sl_atr=2.0, tps=[(1.0, 0.5)], be_after_tp1=True, trail_atr=2.0, max_bars=max_bars * 2),
        "time":     dict(sl_atr=4.0, tps=[], max_bars=max_bars),
    }


def report(path: str, family: str, results: list, tried: int, notes_ru: str = "") -> None:
    """results: [{id, name_ru, rule_ru, exit, params, stats}] → JSON for the panel / merge step."""
    for r in results:
        r["passes"] = passes(r["stats"])
    if SUFFIX:
        root, ext = os.path.splitext(path)
        path, family = root + SUFFIX + ext, family + SUFFIX
        notes_ru = (f"Широкий набор: все фьючерсы Binance с 2023 года (с удалёнными), в торговле только монеты из топ-{TOPN} "
                    f"по объёму за 30 дней на тот момент; обучение 2023–2024, проверка 2025–2026. " + notes_ru)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"family": family, "universe": "wide" if SUFFIX else "core18", "topn": TOPN if SUFFIX else 18, "tried": tried, "notes_ru": notes_ru, "results": results}, f, ensure_ascii=False, indent=1, default=float)
