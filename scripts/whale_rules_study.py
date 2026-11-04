"""Whale-rules study (04.11.2026, research only). Owner: «взять всех этих китов, проанализировать, почему они входят в такие
сделки, почему они прибыльные, и добавить эти правила в нашу систему».

Inputs (all public, see scripts/whale_rules_fetch.py):
  whale_rules_data.json.gz   release lab-data-whales  — HL fills / orders / portfolio, OKX lead-trader closed positions,
                                                         15m candles + funding + OI of the coins they trade (2025-01 → now)
  alt100.npz                 release lab-data-all2023 — 155 Binance USDT-M perps, 15m 2022-11 → 2026-10-18 (backtest universe)
  trend_data.json            release lab-data-trend   — BTC/ETH 1h 2023-01 → 2026-11-04 + funding (BTC regime)

Steps
  1  trips      every closed trade of every whale (HL: fills → round trips with cnq.agents.whale_study.round_trips;
                OKX: each closed sub-position), whale stats 30/90/180 d, the best whales.
  2  context    market context at each entry (as of the last closed hour before it), trade shape; random-entry control
                (same coin, same side, same hold, random hour within ±7 days).
  3  compare    winners vs losers vs random entries, per feature bucket.
  4  patterns   every candidate rule (pre-registered list + data-driven ones taken from the whales' FIRST half only) is
                backtested on our 155-coin history 2023-01 → 2026-10 with costs + funding, per year, on coins the whales
                never traded too, against a placebo (same coins / years / side / hold, random hours, 300 draws).
  5  copy       copying each whale's new trades with 15 / 25 / 35 min delay, costs + funding; walk-forward test of whale
                selection rules (select on the first half of the history, copy on the second half).
Writes --md (Russian report tables) and --json."""
from __future__ import annotations

import argparse, gzip, json, math, os, random, statistics as st, sys, time
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cnq.agents.whale_study import round_trips  # noqa: E402
from cnq.features.levels import build_levels, find_swings  # noqa: E402
from cnq.features.candles import atr as atr_w  # noqa: E402
from cnq.bingx.rest import Candle  # noqa: E402

H1, M15, DAY = 3_600_000, 900_000, 86_400_000
COST = 0.11            # % per round trip: taker 0.045 % × 2 + slippage 0.02 % (same as the copy desk study)
STOP = 8.0             # % protective stop, as on the copy desk
YEARS = (2023, 2024, 2025, 2026)
RNG = random.Random(7)


# ======================================================================================================= helpers
def ema(x, n):
    out = np.empty_like(x); a = 2 / (n + 1); m = x[0]
    for i, v in enumerate(x):
        m = v if i == 0 else a * v + (1 - a) * m
        out[i] = m
    return out


def rsi(c, n=14):
    d = np.diff(c, prepend=c[0]); up = np.where(d > 0, d, 0.0); dn = np.where(d < 0, -d, 0.0)
    au, ad = np.empty_like(c), np.empty_like(c); au[0] = ad[0] = 1e-12
    for i in range(1, len(c)):
        au[i] = (au[i - 1] * (n - 1) + up[i]) / n; ad[i] = (ad[i - 1] * (n - 1) + dn[i]) / n
    return 100 - 100 / (1 + au / np.maximum(ad, 1e-12))


def lag(x, k):
    y = np.full_like(x, np.nan); y[k:] = x[:-k]; return y


def roll_max(x, n):
    from numpy.lib.stride_tricks import sliding_window_view as sw
    y = np.full_like(x, np.nan)
    if len(x) >= n: y[n - 1:] = sw(x, n).max(axis=1)
    return y


def roll_min(x, n):
    from numpy.lib.stride_tricks import sliding_window_view as sw
    y = np.full_like(x, np.nan)
    if len(x) >= n: y[n - 1:] = sw(x, n).min(axis=1)
    return y


def yr(t):
    return time.gmtime(t / 1000).tm_year


class Series:
    """1h OHLC of one coin + causal features at each hour (value at index i = known at the CLOSE of bar i)."""

    def __init__(self, t, o, h, l, c, fund=None, oi=None, btc=None):
        self.t, self.o, self.h, self.l, self.c = (np.asarray(x, dtype=float) for x in (t, o, h, l, c))
        self.t = self.t.astype(np.int64)
        n = len(c)
        c = self.c
        self.ret4 = c / lag(c, 4) - 1
        self.ret24 = c / lag(c, 24) - 1
        self.ret7d = c / lag(c, 168) - 1
        self.ret30d = c / lag(c, 720) - 1
        self.atr1h = atr_w(self.h, self.l, c) / c
        # 4h EMA50 trend (computed on 4h closes = hourly closes at hours 3, 7, ... UTC, then forward-filled)
        is4 = ((self.t // H1) % 4) == 3
        e4 = np.full(n, np.nan); s4 = np.full(n, np.nan)
        idx4 = np.flatnonzero(is4)
        if len(idx4) > 60:
            em = ema(c[idx4], 50)
            e4[idx4] = em; s4[idx4] = em / np.concatenate([[em[0]] * 6, em[:-6]]) - 1
            for arr in (e4, s4):
                last = np.nan
                for i in range(n):
                    if not np.isnan(arr[i]): last = arr[i]
                    else: arr[i] = last
        self.tr4 = np.where(np.isnan(e4), 0, np.where((c > e4) & (s4 > 0), 1, np.where((c < e4) & (s4 < 0), -1, 0)))
        isd = ((self.t // H1) % 24) == 23
        ed = np.full(n, np.nan); idxd = np.flatnonzero(isd)
        if len(idxd) > 30:
            ed[idxd] = ema(c[idxd], 20)
            last = np.nan
            for i in range(n):
                if not np.isnan(ed[i]): last = ed[i]
                else: ed[i] = last
        self.tr1d = np.where(np.isnan(ed), 0, np.where(c > ed, 1, -1))
        self.rsi = rsi(c)
        hi7, lo7 = roll_max(self.h, 168), roll_min(self.l, 168)
        self.rng7 = (c - lo7) / np.maximum(hi7 - lo7, 1e-12)
        a = self.atr1h
        self.volrank = np.full(n, 0.5)
        for i in range(720, n, 1):
            w = a[i - 720:i + 1]
            self.volrank[i] = (w < a[i]).mean()
        self.fund = np.full(n, np.nan)
        if fund:
            ft = np.array([x[0] for x in fund], dtype=np.int64); fv = np.array([x[1] for x in fund], dtype=float)
            j = np.searchsorted(ft, self.t + H1, side="right") - 1          # last funding print ≤ bar close
            ok = j >= 0
            self.fund[ok] = fv[j[ok]]
            self._ft, self._fv = ft, fv
        else:
            self._ft = self._fv = None
        self.oi24 = np.full(n, np.nan)
        if oi is not None:
            oi = np.asarray(oi, dtype=float)
            with np.errstate(divide="ignore", invalid="ignore"):
                self.oi24 = oi / lag(oi, 24) - 1
            self.oi24[~np.isfinite(self.oi24)] = np.nan
        self.btc = btc
        self._lv = None
        self.p15 = None                          # optional 15m [t, o, c] for finer entry / exit prices

    def price(self, t):
        """Price at time t: linear from open to close inside the 15m bar (if 15m data), else inside the 1h bar."""
        if self.p15 is not None:
            tt, oo, cc = self.p15
            j = int(np.searchsorted(tt, t, side="right") - 1)
            if 0 <= j < len(tt) and t < tt[j] + M15:
                return oo[j] + (cc[j] - oo[j]) * ((t - tt[j]) / M15)
        i = self.idx(t + H1)
        if i < 0 or i >= len(self.t) or t >= self.t[i] + H1:
            return None
        return self.o[i] + (self.c[i] - self.o[i]) * ((t - self.t[i]) / H1)

    def idx(self, t):
        """index of the last bar CLOSED at or before time t."""
        return int(np.searchsorted(self.t, t - H1, side="right") - 1)

    def funding_paid(self, side, t0, t1):
        """% of notional paid by the position over (t0, t1] (positive = cost)."""
        if self._ft is None:
            return 0.0
        a, b = np.searchsorted(self._ft, t0, side="right"), np.searchsorted(self._ft, t1, side="right")
        return float(self._fv[a:b].sum()) * 100 * (1 if side == "LONG" else -1)

    # ---- levels engine (cnq.features.levels) on 4h candles, rebuilt every 4 × 4h like LevelContext
    def levels_at(self, i):
        if self._lv is None:
            n4 = len(self.t) // 4
            base = int(np.flatnonzero(((self.t // H1) % 4) == 0)[0]) if len(self.t) else 0
            m = (len(self.t) - base) // 4
            sl = slice(base, base + 4 * m)
            t4 = self.t[sl].reshape(m, 4)[:, 0]
            o4 = self.o[sl].reshape(m, 4)[:, 0]; c4 = self.c[sl].reshape(m, 4)[:, -1]
            h4 = self.h[sl].reshape(m, 4).max(1); l4 = self.l[sl].reshape(m, 4).min(1)
            cs = [Candle(int(t4[k]), float(o4[k]), float(h4[k]), float(l4[k]), float(c4[k]), 1.0, int(t4[k]) + 4 * H1 - 1, 1.0, 1, 0.5, 0.5)
                  for k in range(m)]
            ar = {"t": t4, "o": o4, "h": h4, "l": l4, "c": c4, "v": np.ones(m), "tb": np.full(m, 0.5)}
            self._lv = {"base": base, "cs": cs, "a": ar, "atr": atr_w(h4, l4, c4), "sw": find_swings(cs, 5, 5), "cache": {}}
        L = self._lv
        k = (i + 1 - L["base"]) // 4                    # 4h candles fully closed at the close of hour i
        key = k - k % 4
        if key < 30:
            return None
        if key not in L["cache"]:
            lv = [x for x in build_levels(L["cs"], upto=key, max_age=600, swings=L["sw"], a=L["a"], atr_now=float(L["atr"][key - 1]))
                  if x.score >= 50 and x.status in ("ACTIVE", "RECLAIMED")]
            L["cache"][key] = ([x.center for x in lv], float(L["atr"][key - 1]))
        return L["cache"][key]

    def lvl_dist(self, i):
        """(distance to nearest support below, to nearest resistance above) in % of price, and in 4h ATRs."""
        r = self.levels_at(i)
        if r is None:
            return None
        cen, a4 = r
        p = self.c[i]
        below = [p - x for x in cen if x <= p]; above = [x - p for x in cen if x > p]
        ds = min(below) / p * 100 if below else None
        dr = min(above) / p * 100 if above else None
        a4p = a4 / p * 100 if a4 else None
        return ds, dr, a4p


def ctx_at(S: Series, t, side):
    i = S.idx(t)
    if i < 200 or i >= len(S.t):
        return None
    d = {"ret4": S.ret4[i] * 100, "ret24": S.ret24[i] * 100, "ret7d": S.ret7d[i] * 100, "ret30d": S.ret30d[i] * 100,
         "atr1h": S.atr1h[i] * 100, "volrank": S.volrank[i], "tr4": int(S.tr4[i]), "tr1d": int(S.tr1d[i]), "rsi": S.rsi[i],
         "rng7": S.rng7[i], "fund": S.fund[i] * 100 if not np.isnan(S.fund[i]) else None,
         "oi24": S.oi24[i] * 100 if not np.isnan(S.oi24[i]) else None, "hour": int((t // H1) % 24), "dow": int(((t // DAY) + 3) % 7)}
    lv = S.lvl_dist(i)
    if lv:
        d["sup"], d["res"], d["atr4"] = lv
    if S.btc is not None:
        j = S.btc.idx(t)
        if j >= 200:
            d.update(btc_tr4=int(S.btc.tr4[j]), btc_tr1d=int(S.btc.tr1d[j]), btc_ret24=S.btc.ret24[j] * 100, btc_ret7d=S.btc.ret7d[j] * 100)
    for k, v in list(d.items()):
        if isinstance(v, float) and not math.isfinite(v):
            d[k] = None
    # side-relative views: «with trend» etc.
    s = 1 if side == "LONG" else -1
    d["with_tr4"] = s * d["tr4"]; d["with_tr1d"] = s * d["tr1d"]
    if "btc_tr1d" in d:
        d["with_btc1d"] = s * d["btc_tr1d"]
    d["mom24"] = s * d["ret24"] if d["ret24"] is not None else None       # >0 = enters in the direction of the last day's move
    d["mom7d"] = s * d["ret7d"] if d["ret7d"] is not None else None
    d["fund_side"] = s * d["fund"] if d.get("fund") is not None else None  # >0 = pays funding (crowded side)
    if d.get("sup") is not None or d.get("res") is not None:
        # distance to the level «against» the trade: long → support below (where the stop goes), short → resistance above
        d["lvl_behind"] = d.get("sup") if side == "LONG" else d.get("res")
        d["lvl_ahead"] = d.get("res") if side == "LONG" else d.get("sup")
    return d


def fwd(S: Series, t0, t1, side, stop=None):
    """Return % (after costs and funding) of a trade entered at price(t0) and closed at price(t1); optional stop % checked
    on the 1h bars in between (a gap through the stop fills at the bar open)."""
    a, b = S.price(t0), S.price(t1)
    if not a or not b:
        return None
    s = 1 if side == "LONG" else -1
    if stop:
        i0, i1 = S.idx(t0 + H1), S.idx(t1 + H1)
        lvl = a * (1 - s * stop / 100)
        for k in range(i0 + 1, i1):
            if (s > 0 and S.l[k] <= lvl) or (s < 0 and S.h[k] >= lvl):
                b = S.o[k] if ((s > 0 and S.o[k] < lvl) or (s < 0 and S.o[k] > lvl)) else lvl
                t1 = int(S.t[k]); break
    return s * (b / a - 1) * 100 - COST - S.funding_paid(side, t0, t1)


# ======================================================================================================= data
def load_data(path):
    with gzip.open(path, "rt") as g:
        return json.load(g)


def build_series_whales(D, trend):
    btc = None
    out = {}
    k = trend["kl"]["BTCUSDT"]
    bt = sorted((int(x[0]), x[1:5]) for x in k)
    bf = sorted([int(x[0]), float(x[1])] for x in trend["fund"]["BTCUSDT"])
    btc = Series([x[0] for x in bt], [x[1][0] for x in bt], [x[1][1] for x in bt], [x[1][2] for x in bt], [x[1][3] for x in bt], fund=bf)
    for coin, m in (D.get("mkt") or {}).items():
        rows = m.get("k15") or []
        if len(rows) >= 4 * 300:
            arr = np.array(rows, dtype=float)
            hb = (arr[:, 0] // H1).astype(np.int64)
            uniq, first = np.unique(hb, return_index=True)
            last = np.r_[first[1:] - 1, len(arr) - 1]
            t = uniq * H1; o = arr[first, 1]; c = arr[last, 4]
            h = np.maximum.reduceat(arr[:, 2], first); l = np.minimum.reduceat(arr[:, 3], first)
            # keep only full hours with continuous index
        elif len(m.get("k1h_hl") or []) >= 300:
            arr = np.array(m["k1h_hl"], dtype=float)
            t, o, h, l, c = arr[:, 0].astype(np.int64), arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4]
        else:
            continue
        fund = [list(x) for x in (m.get("fund") or [])]
        fl = [list(x) for x in (m.get("fund_hl") or [])]
        if fl:
            cut = fl[0][0]
            fund = [x for x in fund if x[0] < cut] + fl     # HL hourly prints for the last 40 days (≈ same 8h total)
        fund.sort()
        oi = None
        if m.get("oi"):
            od = dict((int(a), b) for a, b in m["oi"])
            oi = np.array([od.get(int(x), np.nan) for x in t], dtype=float)
        out[coin] = Series(t, o, h, l, c, fund=fund, oi=oi, btc=btc)
        if len(rows) >= 4 * 300:
            out[coin].p15 = (arr[:, 0].astype(np.int64), arr[:, 1], arr[:, 4])
    return out, btc


# ======================================================================================================= 1. trips
def hl_trips(w):
    fills = [{"t": x[0], "coin": x[1], "side": x[2], "px": x[3], "sz": x[4], "dir": x[5], "start_pos": x[6], "closed_pnl": x[7], "fee": x[8]}
             for x in w["fills"]]
    fills.sort(key=lambda r: r["t"])
    trips, _ = round_trips(fills)
    av = None
    hist = {}
    for k, v in (w.get("portfolio") or []):
        hist[k] = v
    ah = (hist.get("allTime") or hist.get("month") or {}).get("accountValueHistory") or []
    ah = sorted((int(a), float(b)) for a, b in ah)
    # trigger orders (stops) for stop-distance
    orders = [o for o in (w.get("orders") or []) if o.get("trig") and o.get("t")]
    out = []
    for tr in trips:
        if tr.get("ret_pct") is None or tr["entry"] <= 0:
            continue
        acc = None
        if ah:
            j = max(0, min(len(ah) - 1, int(np.searchsorted([a for a, _ in ah], tr["open_t"])) - 1))
            acc = ah[j][1]
        lev = tr["notional"] / acc if acc and acc > 0 else None
        stop = None
        for o in orders:
            if o["coin"] == tr["coin"] and tr["open_t"] - 60_000 <= o["t"] <= tr["close_t"] and "Stop" in (o.get("type") or ""):
                d = abs(o["trig"] / tr["entry"] - 1) * 100
                if (tr["side"] == "LONG" and o["trig"] < tr["entry"]) or (tr["side"] == "SHORT" and o["trig"] > tr["entry"]):
                    stop = d if stop is None else min(stop, d)
        out.append({"coin": tr["coin"], "side": tr["side"], "open_t": tr["open_t"], "close_t": tr["close_t"], "entry": tr["entry"], "exit": tr["exit"],
                    "ret": tr["ret_pct"], "pnl": tr["pnl"], "notional": tr["notional"], "lev": lev, "stop": stop, "fills": tr["fills"]})
    return out


def okx_trips(t):
    out = []
    for r in t["closed"]:
        sid, inst, side, ot, ct, e, ex, lev, mg, pnl, pr, ctype, sz = r
        if not e or not ex or not ot or not ct:
            continue
        s = 1 if side == "long" else -1
        out.append({"coin": (inst or "").split("-")[0], "side": "LONG" if s > 0 else "SHORT", "open_t": ot, "close_t": ct, "entry": e, "exit": ex,
                    "ret": s * (ex / e - 1) * 100, "pnl": pnl, "notional": mg * lev, "lev": lev, "stop": None, "fills": 1, "ctype": ctype})
    out.sort(key=lambda x: x["open_t"])
    return out


def episodes(trips):
    """Many lead traders open a coin in several sub-positions / slices within hours. One decision = one entry: trips of the
    same coin + side opened within 4 h of the first one are merged (notional-weighted entry / exit / return)."""
    out = []
    by = defaultdict(list)
    for t in trips:
        by[(t["coin"], t["side"])].append(t)
    for k, ts in by.items():
        ts.sort(key=lambda x: x["open_t"])
        cur = None
        for t in ts:
            if cur and t["open_t"] - cur["open_t"] <= 4 * H1:
                cur["g"].append(t)
            else:
                if cur: out.append(cur)
                cur = {"open_t": t["open_t"], "g": [t]}
        if cur: out.append(cur)
    res = []
    for e in out:
        g = e["g"]; w = [max(x["notional"], 1e-9) for x in g]; W = sum(w)
        f0 = g[0]
        res.append({**f0, "open_t": min(x["open_t"] for x in g), "close_t": max(x["close_t"] for x in g),
                    "entry": sum(x["entry"] * wi for x, wi in zip(g, w)) / W, "exit": sum(x["exit"] * wi for x, wi in zip(g, w)) / W,
                    "ret": sum(x["ret"] * wi for x, wi in zip(g, w)) / W, "pnl": sum(x["pnl"] for x in g), "notional": W,
                    "lev": st.median([x["lev"] for x in g if x["lev"]]) if any(x["lev"] for x in g) else None,
                    "stop": min([x["stop"] for x in g if x["stop"]], default=None), "n_sub": len(g)})
    res.sort(key=lambda x: x["open_t"])
    return res


def wstats(tr, since, now):
    x = [t for t in tr if t["close_t"] >= since and t["close_t"] <= now]
    if not x:
        return {"n": 0}
    gp = sum(t["pnl"] for t in x if t["pnl"] > 0); gl = -sum(t["pnl"] for t in x if t["pnl"] < 0)
    days = max(1.0, (now - since) / DAY)
    return {"n": len(x), "pnl": round(sum(t["pnl"] for t in x)), "win": round(sum(t["pnl"] > 0 for t in x) / len(x), 3),
            "pf": round(gp / gl, 2) if gl > 0 else 99.0, "hold_h": round(st.median((t["close_t"] - t["open_t"]) / H1 for t in x), 1),
            "per_day": round(len(x) / days, 2), "avg_ret": round(st.mean(t["ret"] for t in x), 3),
            "lev": round(st.median([t["lev"] for t in x if t.get("lev")]), 1) if any(t.get("lev") for t in x) else None,
            "long": round(sum(t["side"] == "LONG" for t in x) / len(x), 2),
            "coins": len({t["coin"] for t in x}),
            "best_share": round(max((t["pnl"] for t in x), default=0) / max(1.0, sum(t["pnl"] for t in x if t["pnl"] > 0)), 2)}


# ======================================================================================================= 3. compare
FEATS = [("with_tr4", [(-1.5, -0.5), (-0.5, 0.5), (0.5, 1.5)], "по тренду 4ч (−1 против, 0 нет тренда, +1 по тренду)"),
         ("with_tr1d", [(-1.5, 0), (0, 1.5)], "по дневному тренду монеты"),
         ("with_btc1d", [(-1.5, 0), (0, 1.5)], "по дневному тренду BTC"),
         ("mom24", [(-1e9, -5), (-5, -1), (-1, 1), (1, 5), (5, 1e9)], "движение монеты за 24ч в сторону сделки, %"),
         ("mom7d", [(-1e9, -15), (-15, -5), (-5, 5), (5, 15), (15, 1e9)], "движение за 7 дней в сторону сделки, %"),
         ("rsi", [(0, 30), (30, 45), (45, 55), (55, 70), (70, 101)], "RSI 1ч"),
         ("rng7", [(-0.01, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)], "где цена в диапазоне 7 дней (0 дно, 1 верх)"),
         ("volrank", [(-0.01, 0.33), (0.33, 0.66), (0.66, 1.01)], "волатильность против своих 30 дней"),
         ("fund_side", [(-1e9, -0.005), (-0.005, 0.005), (0.005, 0.02), (0.02, 1e9)], "фандинг со стороны сделки, % (>0 платит)"),
         ("oi24", [(-1e9, -5), (-5, 0), (0, 5), (5, 1e9)], "изменение открытого интереса за 24ч, %"),
         ("lvl_behind", [(0, 1), (1, 3), (3, 8), (8, 1e9)], "до уровня «за спиной» (поддержка у лонга), %"),
         ("lvl_ahead", [(0, 1), (1, 3), (3, 8), (8, 1e9)], "до уровня «впереди» (сопротивление у лонга), %"),
         ("hour", [(0, 8), (8, 13), (13, 20), (20, 24)], "час входа UTC")]


def bucket(v, bs):
    if v is None:
        return None
    for k, (a, b) in enumerate(bs):
        if a <= v < b:
            return k
    return None


def compare(rows):
    """rows: whale entries with ctx, ret, ret_rand (random entries on same coin/side/hold ±7 d). Per feature bucket:
    share of whale entries vs random, win rate, avg return of whale vs random."""
    out = {}
    for f, bs, ru in FEATS:
        tab = []
        for k, (a, b) in enumerate(bs):
            W = [r for r in rows if bucket(r["ctx"].get(f), bs) == k]
            R = [x for r in rows for x in r["rand"] if bucket(x["ctx"].get(f), bs) == k]
            if not W and not R:
                continue
            tab.append({"b": f"{a:g}…{b:g}", "n_w": len(W), "share_w": len(W) / max(1, sum(1 for r in rows if r["ctx"].get(f) is not None)),
                        "share_r": len(R) / max(1, sum(1 for r in rows for x in r["rand"] if x["ctx"].get(f) is not None)),
                        "win_w": sum(r["ret"] > 0 for r in W) / len(W) if W else None, "avg_w": st.mean(r["ret"] for r in W) if W else None,
                        "avg_r": st.mean(x["ret"] for x in R) if R else None, "n_r": len(R)})
        out[f] = {"ru": ru, "tab": tab}
    return out


# ======================================================================================================= 4. patterns
# Pre-registered (written before the whale data came back; from the owner's examples and the common whale styles).
# Each: side, condition on ctx (dict), hold in hours. Exit: time, protective stop −8 % (as the copy desk). All on 1h bars.
def _g(d, k, default=None):
    v = d.get(k)
    return default if v is None else v


def R_(side, pre, lv, hold):
    """side, cheap condition (no levels), level condition (or None), hold hours."""
    return (side, pre, lv, hold)


PRE = {
    "P1 шорт после роста +15% за 7 дн у сопротивления (≤ 2%), фандинг > 0.01%": R_(
        "SHORT", lambda d: _g(d, "ret7d", 0) >= 15 and _g(d, "fund", 0) > 0.01, lambda d: d.get("res") is not None and d["res"] <= 2, 24),
    "P2 лонг на откате −3% за 24ч к поддержке (≤ 1.5%) в восходящем тренде": R_(
        "LONG", lambda d: d["tr1d"] == 1 and d["tr4"] >= 0 and _g(d, "ret24", 0) <= -3, lambda d: d.get("sup") is not None and d["sup"] <= 1.5, 24),
    "P3 шорт на отскоке +3% за 24ч к сопротивлению (≤ 1.5%) в нисходящем тренде": R_(
        "SHORT", lambda d: d["tr1d"] == -1 and d["tr4"] <= 0 and _g(d, "ret24", 0) >= 3, lambda d: d.get("res") is not None and d["res"] <= 1.5, 24),
    "P4 лонг после обвала −20% за 7 дн при фандинге < 0": R_(
        "LONG", lambda d: _g(d, "ret7d", 0) <= -20 and _g(d, "fund", 0) < 0, None, 48),
    "P5 шорт при фандинге ≥ 0.05% после роста +10% за 7 дн": R_(
        "SHORT", lambda d: _g(d, "fund", 0) >= 0.05 and _g(d, "ret7d", 0) >= 10, None, 24),
    "P6 лонг по тренду (монета 4ч и BTC 1д вверх), OI +5% за 24ч, рост 0…8%": R_(
        "LONG", lambda d: d["tr4"] == 1 and _g(d, "btc_tr1d", 0) == 1 and _g(d, "oi24", 0) >= 5 and 0 < _g(d, "ret24", 0) <= 8, None, 24),
}


def base_ctx(S, i, btc):
    d = {"ret4": S.ret4[i] * 100, "ret24": S.ret24[i] * 100, "ret7d": S.ret7d[i] * 100, "ret30d": S.ret30d[i] * 100,
         "atr1h": S.atr1h[i] * 100, "volrank": S.volrank[i], "tr4": int(S.tr4[i]), "tr1d": int(S.tr1d[i]), "rsi": S.rsi[i],
         "rng7": S.rng7[i], "fund": S.fund[i] * 100 if not np.isnan(S.fund[i]) else None,
         "oi24": S.oi24[i] * 100 if not np.isnan(S.oi24[i]) else None, "hour": int((S.t[i] // H1 + 1) % 24)}
    for k in ("ret4", "ret24", "ret7d", "ret30d", "rsi", "rng7"):
        if not math.isfinite(d[k]):
            d[k] = None
    j = btc.idx(int(S.t[i]) + H1)
    if j >= 200:
        d.update(btc_tr4=int(btc.tr4[j]), btc_tr1d=int(btc.tr1d[j]), btc_ret24=btc.ret24[j] * 100, btc_ret7d=btc.ret7d[j] * 100)
    return d


def scan_universe(U, btc, rules, coins_whale, years=YEARS):
    """Signals on every 1h bar of every universe coin; one open trade per coin per rule; entry at the next bar open,
    time exit / −8 % stop, costs + funding."""
    res = {name: [] for name in rules}
    for sym, S in U.items():
        n = len(S.t)
        busy = {name: -1 for name in rules}
        ys = np.array([yr(int(x)) for x in S.t])
        for i in range(800, n - 2):
            if ys[i] not in years:
                continue
            d = None
            for name, (side, pre, lv, hold) in rules.items():
                if i <= busy[name]:
                    continue
                if d is None:
                    d = base_ctx(S, i, btc)
                try:
                    if not pre(d):
                        continue
                    if lv is not None:
                        if "sup" not in d:
                            x = S.lvl_dist(i)
                            d["sup"], d["res"] = (x[0], x[1]) if x else (None, None)
                        s = 1 if side == "LONG" else -1
                        d["lvl_behind"] = d["sup"] if s > 0 else d["res"]; d["lvl_ahead"] = d["res"] if s > 0 else d["sup"]
                        if not lv(d):
                            continue
                except (TypeError, KeyError):
                    continue
                t0 = int(S.t[i + 1]); t1 = t0 + hold * H1
                if t1 > int(S.t[-1]):
                    continue
                r = fwd(S, t0, t1, side, stop=STOP)
                if r is None:
                    continue
                res[name].append({"sym": sym, "i": i + 1, "t": t0, "y": yr(t0), "ret": r, "whale_coin": sym in coins_whale})
                busy[name] = i + hold
    return res


def stats(xs):
    if not xs:
        return {"n": 0}
    gp = sum(x for x in xs if x > 0); gl = -sum(x for x in xs if x < 0)
    sd = st.pstdev(xs) if len(xs) > 1 else 0
    return {"n": len(xs), "win": sum(x > 0 for x in xs) / len(xs), "avg": st.mean(xs), "pf": gp / gl if gl > 0 else 99.0,
            "t": st.mean(xs) / sd * math.sqrt(len(xs)) if sd > 0 else 0.0, "sum": sum(xs)}


def placebo(U, trades, side, hold, reps=300):
    """Same coin / year / side / hold, random entry hour; → distribution of the mean return."""
    if not trades:
        return None
    pools = defaultdict(list)
    for tr in trades:
        pools[(tr["sym"], tr["y"])].append(tr)
    cache = {}
    means = []
    for _ in range(reps):
        rs = []
        for (sym, y), lst in pools.items():
            S = U[sym]
            key = (sym, y)
            if key not in cache:
                ys = np.array([yr(int(x)) for x in S.t[::24]])
                ok = np.flatnonzero(ys == y) * 24
                cache[key] = ok[(ok > 800) & (ok < len(S.t) - hold - 2)]
            cand = cache[key]
            if len(cand) == 0:
                continue
            for _k in range(len(lst)):
                i = int(cand[RNG.randrange(len(cand))]) + RNG.randrange(24)
                if i >= len(S.t) - hold - 1:
                    continue
                r = fwd(S, int(S.t[i]), int(S.t[i]) + hold * H1, side, stop=STOP)
                if r is not None:
                    rs.append(r)
        if rs:
            means.append(st.mean(rs))
    return means


# ======================================================================================================= 5. copy
def copy_ret(S: Series, tr, delay_min):
    D = delay_min * 60_000
    if tr["close_t"] - tr["open_t"] <= D:
        return "missed"
    r = fwd(S, tr["open_t"] + D, tr["close_t"] + D, tr["side"])
    return r


# ======================================================================================================= main
def universe_series(npz_path, fund, btc):
    z = np.load(npz_path)
    syms, bsyms, times = list(z["syms"]), list(z["bsyms"]), z["times"]
    n4 = (len(times) // 4) * 4
    t1 = times[:n4:4]
    O, Hh, Ll, C, OI = (z[k][:, :n4] for k in ("open", "high", "low", "close", "open_interest_usd"))
    U = {}
    for k, s in enumerate(syms):
        o = O[k].reshape(-1, 4); h = Hh[k].reshape(-1, 4); l = Ll[k].reshape(-1, 4); c = C[k].reshape(-1, 4)
        oi = OI[k].reshape(-1, 4)[:, -1].astype(float)
        ok = np.isfinite(c).all(1) & (c[:, -1] > 0) & np.isfinite(o[:, 0]) & np.isfinite(h).all(1) & np.isfinite(l).all(1)
        if ok.sum() < 3000:
            continue
        idx = np.flatnonzero(ok)
        sl = slice(int(idx[0]), int(idx[-1]) + 1)
        oo, hh, ll, cc = o[sl, 0].astype(float), h[sl].max(1).astype(float), l[sl].min(1).astype(float), c[sl, -1].astype(float)
        okk = ok[sl]
        for i in np.flatnonzero(~okk):            # interior holes: flat bar at the previous close
            oo[i] = hh[i] = ll[i] = cc[i] = cc[i - 1]
        oi_s = oi[sl].copy(); oi_s[~(oi_s > 0)] = np.nan
        U[str(s)] = Series(t1[sl], oo, hh, ll, cc, fund=[list(x) for x in fund.get(str(bsyms[k]), [])], oi=oi_s, btc=btc)
    return U


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data"); ap.add_argument("npz"); ap.add_argument("trend")
    ap.add_argument("--json", default="whale_rules_results.json")
    ap.add_argument("--max-coins", type=int, default=0)
    ap.add_argument("--stage", default="all")
    a = ap.parse_args()
    D = load_data(a.data)
    trend = json.load(open(a.trend))
    now = int(D["made"])
    W, btc = build_series_whales(D, trend)
    print(f"whale-coin series: {len(W)}", flush=True)
    R = {"made": now, "whales": [], "notes": []}

    # ---------------- 1. trips + whale stats
    whales = []
    for w in D.get("hl") or []:
        if len(w["fills"]) < 8:
            continue
        tr = hl_trips(w)
        whales.append({"src": "hl", "id": w["addr"], "name": w.get("name") or w["addr"][:10], "account": w.get("account"), "capped": w.get("capped"),
                       "trips": tr, "ep": tr, "first_t": min((x[0] for x in w["fills"]), default=now)})
    for t in D.get("okx") or []:
        tr = okx_trips(t)
        if len(tr) < 8:
            continue
        whales.append({"src": "okx", "id": "okx:" + t["code"], "name": t.get("name") or t["code"], "account": t.get("aum"), "trips": tr,
                       "ep": episodes(tr), "first_t": min(x["open_t"] for x in tr), "followers": t.get("followers"), "lead_days": t.get("lead_days")})
    for w in whales:
        w["s30"] = wstats(w["ep"], now - 30 * DAY, now); w["s90"] = wstats(w["ep"], now - 90 * DAY, now); w["s180"] = wstats(w["ep"], now - 180 * DAY, now)
        w["span_d"] = round((now - min([x["open_t"] for x in w["ep"]], default=now)) / DAY)
    print(f"whales with ≥ 8 trips: {len(whales)} (HL {sum(w['src']=='hl' for w in whales)}, OKX {sum(w['src']=='okx' for w in whales)})", flush=True)

    # best whales (by 90-day P&L among those with ≥ 20 decisions in 90 d, PF ≥ 1.5, win ≥ 55 %, median hold ≥ 1 h)
    def good(w):
        s = w["s90"]
        return s.get("n", 0) >= 20 and s.get("pnl", 0) > 0 and s.get("pf", 0) >= 1.5 and s.get("win", 0) >= 0.55 and s.get("hold_h", 0) >= 1
    best = sorted([w for w in whales if good(w)], key=lambda w: -w["s90"]["pnl"])[:10]
    R["funnel"] = {"hl_fetched": len(D.get("hl") or []), "okx_fetched": len(D.get("okx") or []), "with_trips": len(whales), "best": len(best)}
    for w in sorted(whales, key=lambda w: -(w["s90"].get("pnl") or 0))[:40]:
        R["whales"].append({k: w.get(k) for k in ("src", "id", "name", "account", "s30", "s90", "s180", "span_d", "followers", "lead_days", "capped")}
                           | {"best": w in best, "n_trips": len(w["trips"]), "n_ep": len(w["ep"])})
    print("best:", [(w["name"][:14], w["s90"]) for w in best], flush=True)

    # ---------------- 2. context of every decision of the best whales (+ random control)
    rows = []
    for w in best:
        for tr in w["ep"]:
            S = W.get(tr["coin"])
            if S is None:
                continue
            c = ctx_at(S, tr["open_t"], tr["side"])
            if c is None:
                continue
            hold = max(H1 // 4, tr["close_t"] - tr["open_t"])
            rand = []
            for _ in range(5):
                t0 = tr["open_t"] + RNG.randint(-7 * 24, 7 * 24) * H1 + RNG.randint(0, 3) * M15
                if t0 + hold > int(S.t[-1]) + H1:
                    continue
                cx = ctx_at(S, t0, tr["side"]); rr = fwd(S, t0, t0 + hold, tr["side"])
                if cx is not None and rr is not None:
                    rand.append({"ctx": cx, "ret": rr})
            own = tr["ret"] - COST - S.funding_paid(tr["side"], tr["open_t"], tr["close_t"])
            rows.append({"whale": w["name"], "src": w["src"], "coin": tr["coin"], "side": tr["side"], "t": tr["open_t"], "hold_h": hold / H1,
                         "lev": tr.get("lev"), "stop": tr.get("stop"), "ret": own, "ret_raw": tr["ret"], "pnl": tr["pnl"], "ctx": c, "rand": rand})
    print(f"decisions with context: {len(rows)}", flush=True)
    R["n_rows"] = len(rows)
    R["rows_by_whale"] = {w["name"]: sum(1 for r in rows if r["whale"] == w["name"]) for w in best}
    R["span"] = [min((r["t"] for r in rows), default=0), max((r["t"] for r in rows), default=0)]
    # equal weight per whale for the comparison: cap each whale at 150 decisions (most recent)
    capped = []
    for w in best:
        rr = [r for r in rows if r["whale"] == w["name"]]
        capped += rr[-150:]
    R["compare"] = compare(capped)
    R["compare_n"] = len(capped)
    R["shape"] = {
        "win": stats([r["ret"] for r in capped]),
        "rand": stats([x["ret"] for r in capped for x in r["rand"]]),
        "hold_med": st.median(r["hold_h"] for r in capped) if capped else None,
        "hold_win": st.median(r["hold_h"] for r in capped if r["ret"] > 0) if capped else None,
        "hold_loss": st.median(r["hold_h"] for r in capped if r["ret"] <= 0) if any(r["ret"] <= 0 for r in capped) else None,
        "ret_win": st.median(r["ret"] for r in capped if r["ret"] > 0) if capped else None,
        "ret_loss": st.median(r["ret"] for r in capped if r["ret"] <= 0) if any(r["ret"] <= 0 for r in capped) else None,
        "lev": st.median([r["lev"] for r in capped if r["lev"]]) if any(r["lev"] for r in capped) else None,
        "stop_known": sum(1 for r in capped if r["stop"]), "stop_med": st.median([r["stop"] for r in capped if r["stop"]]) if any(r["stop"] for r in capped) else None,
        "long_share": sum(r["side"] == "LONG" for r in capped) / max(1, len(capped)),
        "worst": sorted(r["ret"] for r in capped)[:5],
    }
    # per whale: what share of P&L came from the market (random same coin/side/hold) vs timing
    R["per_whale_vs_rand"] = {}
    for w in best:
        rr = [r for r in capped if r["whale"] == w["name"]]
        R["per_whale_vs_rand"][w["name"]] = {"own": stats([r["ret"] for r in rr]), "rand": stats([x["ret"] for r in rr for x in r["rand"]])}

    # ---------------- 4. data-driven patterns from the FIRST half of the decisions only
    rows_sorted = sorted(capped, key=lambda r: r["t"])
    half = rows_sorted[: len(rows_sorted) // 2]
    second = rows_sorted[len(rows_sorted) // 2:]
    cand = []
    for f, bs, ru in FEATS:
        if f == "hour":
            continue
        for k, (lo, hi) in enumerate(bs):
            for side in ("LONG", "SHORT"):
                W1 = [r for r in half if r["side"] == side and bucket(r["ctx"].get(f), bs) == k]
                R1 = [x for r in half if r["side"] == side for x in r["rand"] if bucket(x["ctx"].get(f), bs) == k]
                if len(W1) < 15 or len(R1) < 30:
                    continue
                edge = st.mean(r["ret"] for r in W1) - st.mean(x["ret"] for x in R1)
                cand.append((edge, f, k, lo, hi, side, len(W1), st.mean(r["ret"] for r in W1), st.mean(x["ret"] for x in R1)))
    cand.sort(reverse=True)
    R["dd_candidates"] = [{"edge": c[0], "f": c[1], "lo": c[3], "hi": c[4], "side": c[5], "n": c[6], "avg_w": c[7], "avg_r": c[8]} for c in cand[:15]]
    hold_dd = int(min(72, max(4, round((R["shape"]["hold_med"] or 24)))))
    dd_rules = {}
    for c in cand[:6]:
        edge, f, k, lo, hi, side = c[:6]
        if edge <= 0:
            continue
        # check on the whales' second half first (only for the record; all 6 go to the backtest anyway)
        W2 = [r for r in second if r["side"] == side and bucket(r["ctx"].get(f), dict((x[0], x[1]) for x in FEATS)[f]) == k]

        def mk(f=f, lo=lo, hi=hi, side=side):
            sg = 1 if side == "LONG" else -1
            def val(d):
                if f == "with_tr4": return sg * d["tr4"]
                if f == "with_tr1d": return sg * d["tr1d"]
                if f == "with_btc1d": return sg * d["btc_tr1d"] if d.get("btc_tr1d") is not None else None
                if f == "mom24": return sg * d["ret24"] if d.get("ret24") is not None else None
                if f == "mom7d": return sg * d["ret7d"] if d.get("ret7d") is not None else None
                if f == "fund_side": return sg * d["fund"] if d.get("fund") is not None else None
                return d.get(f)
            ok = lambda d: (lambda v: v is not None and lo <= v < hi)(val(d))  # noqa: E731
            if f in ("lvl_behind", "lvl_ahead"):
                return (lambda d: True), ok
            return ok, None
        name = f"D{len(dd_rules)+1} {side.lower()}: {dict((x[0], x[2]) for x in FEATS)[f]} в [{lo:g}; {hi:g})"
        pre_, lv_ = mk()
        dd_rules[name] = (side, pre_, lv_, hold_dd)
        R.setdefault("dd_second_half", {})[name] = stats([r["ret"] for r in W2])
    rules = {**PRE, **dd_rules}
    R["n_patterns"] = len(rules)
    R["hold_dd"] = hold_dd
    if a.stage == "whales":
        json.dump(R, open(a.json, "w"), default=float); return

    # ---------------- 4b. backtest on our universe
    fund = D.get("fund") or {}
    U = universe_series(a.npz, fund, btc)
    if a.max_coins:
        U = dict(list(U.items())[: a.max_coins])
    print(f"universe series: {len(U)}", flush=True)
    whale_coins = {r["coin"].upper() for w in whales for r in w["ep"]}
    T0 = time.time()
    bt = scan_universe(U, btc, rules, whale_coins)
    print(f"scan done in {time.time()-T0:.0f}s", flush=True)
    R["backtest"] = {}
    K = len(rules)
    for name, (side, pre, lv, hold) in rules.items():
        tr = bt[name]
        rs = [x["ret"] for x in tr]
        sub = tr if len(tr) <= 1500 else RNG.sample(tr, 1500)
        pl = placebo(U, sub, side, hold, reps=100) if tr else None
        mu = st.mean(rs) if rs else 0
        p = (sum(1 for m in pl if m >= mu) + 1) / (len(pl) + 1) if pl else None
        by_y = {y: stats([x["ret"] for x in tr if x["y"] == y]) for y in YEARS}
        R["backtest"][name] = {"side": side, "hold": hold, "all": stats(rs), "by_year": by_y,
                               "non_whale_coins": stats([x["ret"] for x in tr if not x["whale_coin"]]),
                               "whale_coins": stats([x["ret"] for x in tr if x["whale_coin"]]),
                               "placebo_mean": st.mean(pl) if pl else None, "placebo_p95": float(np.percentile(pl, 95)) if pl else None,
                               "p": p, "pass": bool(p is not None and p < 0.05 / K and stats(rs).get("pf", 0) >= 1.2
                                                    and sum(1 for y in YEARS if by_y[y].get("n", 0) >= 10 and by_y[y]["avg"] > 0) >= 3
                                                    and stats([x["ret"] for x in tr if not x["whale_coin"]]).get("avg", -1) > 0)}
        print(name, R["backtest"][name]["all"], "p", p, flush=True)

    # ---------------- 5. copy edge with delay
    R["copy"] = {}
    for w in best:
        res = {}
        for dmin in (0, 15, 25, 35):
            rs, missed, nod = [], 0, 0
            for tr in w["ep"]:
                if tr["close_t"] < now - 90 * DAY:
                    continue
                S = W.get(tr["coin"])
                if S is None:
                    nod += 1; continue
                r = copy_ret(S, tr, dmin) if dmin else fwd(S, tr["open_t"], tr["close_t"], tr["side"])
                if r == "missed":
                    missed += 1
                elif r is None:
                    nod += 1
                else:
                    rs.append(r)
            res[dmin] = {**stats(rs), "missed": missed, "no_data": nod}
        own = [tr["ret"] - COST for tr in w["ep"] if tr["close_t"] >= now - 90 * DAY]
        res["own"] = stats(own)
        R["copy"][w["name"]] = res
    # walk-forward whale selection: stats on [split−L, split), copy with 30 min on [split, now]
    sel_rules = [(wn, pf, n, hh) for wn in (0.6, 0.7) for pf in (1.5, 2.0) for n in (10, 20) for hh in (1, 4)]
    R["walk"] = []
    for split_d in (45, 30):
        split = now - split_d * DAY
        base_list = []
        per = {}
        for w in whales:
            pre = wstats(w["ep"], split - 60 * DAY, split)
            post = []
            for tr in w["ep"]:
                if tr["open_t"] < split:
                    continue
                S = W.get(tr["coin"])
                if S is None:
                    continue
                r = copy_ret(S, tr, 30)
                if isinstance(r, float):
                    post.append(r)
            if pre.get("n", 0) >= 5 and post:
                per[w["id"]] = (pre, post)
        allw = [st.mean(p) for _, p in per.values()]
        for wn, pf, n, hh in sel_rules:
            sel = [p for pr, p in per.values() if pr["win"] >= wn and pr["pf"] >= pf and pr["n"] >= n and pr["hold_h"] >= hh and pr["pnl"] > 0]
            R["walk"].append({"split_d": split_d, "rule": f"win ≥ {int(wn*100)}%, PF ≥ {pf}, ≥ {n} сделок за 60 дн, держит ≥ {hh} ч",
                              "whales": len(sel), "trades": sum(len(p) for p in sel),
                              "avg_whale": st.mean(st.mean(p) for p in sel) if sel else None,
                              "pos_whales": sum(1 for p in sel if st.mean(p) > 0),
                              "avg_trade": st.mean(x for p in sel for x in p) if sel else None})
        R["walk"].append({"split_d": split_d, "rule": "все киты с ≥ 5 сделками (база)", "whales": len(allw), "trades": sum(len(p) for _, p in per.values()),
                          "avg_whale": st.mean(allw) if allw else None, "pos_whales": sum(1 for x in allw if x > 0),
                          "avg_trade": st.mean(x for _, p in per.values() for x in p) if per else None})
    json.dump(R, open(a.json, "w"), default=float, ensure_ascii=False)
    print("wrote", a.json)


if __name__ == "__main__":
    main()
