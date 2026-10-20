"""«Чуть-чуть больше сделок, даже на небольшом плюсе» (owner, 20.10.2026) — research only, nothing live is touched.
CRYPTO NOVA earns a referral share of the fees its users pay on BingX, so more trades = more income for us. But the user pays
those fees: a trade that wins $2–5 before costs can be a loss after them. The question: are there MORE trades whose average is
clearly positive AFTER all costs? Results → docs/research/frequent_net.md. Runs in GitHub Actions only
(.github/workflows/research-frequent-net.yml): this container cannot reach Binance.

Data (fetch): every Binance USDT-M perp in the archive (delisted included, like own_signals_study.wide) → daily candles → each day
the point-in-time top 40 by 30-day dollar volume (≥ 120 days of history); then 15-minute candles 2022-08 → today and the real
funding history of every coin that was ever in that top 40 since 11.2022. 1h / 4h / daily bars are built from the 15m ones.

Honesty rules: a signal uses closed bars only; the trading set (top 40), the BTC regime («рост» = BTC above its 200-day average and
up over 60 days) and daily trends come from the last COMPLETED day; entry at the next bar's open (market) or a limit order placed
at the close that counts as filled only when price trades 0.05 % THROUGH it; stops / targets / trailing are walked on 15m bars,
the stop first when a bar touches both, no target in the fill bar; a coin that stops trading closes at its last price.

Costs (BingX USDT-M, regular account): taker 0.05 % + slippage 0.03 % a market fill (0.05 % on stops, gaps fill at the open),
maker 0.02 % a limit fill, real Binance funding at each settlement held (long pays a positive rate; 0.01 % / 8 h when missing).
Stress: every market fill at 0.05 % slippage.

Account: $10 000, risk $100 (1 %) a trade at the stop, no compounding; a position ≤ 2× the account, all open ≤ 5×, ≤ 20 open;
equity marked hourly (the drawdown counts open trades). Test 2023-01-01 → today.

Setups (longs in «рост» unless noted; one position per coin and rule):
  A  «Откат в тренде» on 4h: close above a rising 50-bar average, RSI(2) < 10; out when a 4h close is above the 5-bar average or
     after 10 bars; stop 2.5 ATR (1.5–10 %). A1 market, A2 limit at the signal close (4 h), A3 = A1 + volume ≥ 1.5× its 30 days.
  B  1h pullback in a 4h + daily uptrend, fixed bracket: RSI(2) < 10 on 1h, the last 4h close above its rising 50-bar average,
     the last daily close above its 50-day average; stop 1.5 ATR(1h) (0.4–5 %), target +1R as a limit, 24 h max.
     B1 market, B2 limit at the signal close (1 h), B3 stop 2.5 ATR / target +1R.
  C  breakout of the 2-day range on volume: 1h close above the 48-hour high, the hour's volume ≥ 1.5× the 7-day hourly average.
     C1t market, stop 2 ATR, trailing 3 ATR on 1h closes, 48 h max; C1b market, stop 1.5 ATR / target 3 ATR (2R) limit;
     C2b limit at the broken level (retest, 4 h), same bracket; Cs = the mirror short in «не рост» (C1b exits).
  D  mean reversion after a 3-sigma 15m drop (15m log return ≤ −3 σ of the previous 7 days): D1 market, stop and target one more
     drop size, 8 h; D2 limit at the signal close (15 min), same exits; D3 market, no target, stop 3 drops, out after 4 h.
  E  the faster cousin of the own desk's breakout: daily close above the 5-day high and the 20-day average, the day's dollar
     volume ≥ 1.5× its 30-day average; out on a close below the previous day's low, stop 2.5 ATR (3–15 %), 14 days.
     E1 market, E2 limit at the signal close (1 day).
  REF the own desk today (P + B20 with the volume mark, daily) on the same data and costs — the yardstick.
Every rule is compared with a random entry: same coin, year, regime (and top 40), side, mode and exit geometry. Stdlib + numpy."""
import datetime as dt
import io
import json
import os
import re
import sys
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))      # scripts/

DAY, BAR = 86_400_000, 900_000
START = int(dt.datetime(2022, 8, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
TEST = int(dt.datetime(2023, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
TOP = 40
TAKER, MAKER, SLIP, SLIP_STOP, THROUGH, FUND_DEF = 0.05, 0.02, 0.03, 0.05, 0.05, 0.01      # % of notional
ACCOUNT, RISK_USD, POS_MAX, GROSS_MAX, OPEN_MAX = 10_000.0, 100.0, 2.0, 5.0, 20
YEARS = ("2023", "2024", "2025", "2026")
BIG = 10 ** 9


def note(title, msg, level="notice"):
    print(f"{title}: {msg}", flush=True)
    m = str(msg).replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    print(f"::{level} title={title}::{m}", flush=True)


def publish(title, obj, n=2900):
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    parts = [s[i:i + n] for i in range(0, len(s), n)] or [""]
    for k, p in enumerate(parts):
        note(f"{title} {k + 1}/{len(parts)}" if len(parts) > 1 else title, p)


# ------------------------------------------------------------------------------------------------------------------- data
def _parse_kl(b):
    try:
        with zipfile.ZipFile(io.BytesIO(b)) as z:
            txt = z.read(z.namelist()[0]).decode()
        lines = txt.strip().splitlines()
        if lines and not lines[0][:1].isdigit():
            lines = lines[1:]
        if not lines:
            return None
        a = np.loadtxt(lines, delimiter=",", usecols=(0, 1, 2, 3, 4, 7), dtype=np.float64, ndmin=2)
    except Exception:  # noqa: BLE001 — a broken archive file = a missing month
        return None
    a[:, 0] = np.where(a[:, 0] > 1e14, a[:, 0] // 1000, a[:, 0])
    return a


def _parse_fund(b):
    try:
        with zipfile.ZipFile(io.BytesIO(b)) as z:
            txt = z.read(z.namelist()[0]).decode()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for ln in txt.strip().splitlines():
        p = ln.split(",")
        if not p[0][:1].isdigit():
            continue
        try:
            t = int(float(p[0]))
            out.append((t // 1000 if t > 10 ** 14 else t, float(p[-1])))
        except ValueError:
            continue
    return out


def fetch(out_dir):
    from fetch_alt100 import http
    from own_signals_study import S3, UM, all_um_symbols, daily_wide, sma
    os.makedirs(out_dir, exist_ok=True)
    t_start = dt.datetime.now()
    syms = all_um_symbols()
    with ThreadPoolExecutor(48) as ex:
        raw = dict(zip(syms, ex.map(daily_wide, syms)))
    D = {s[:-4]: k for s, k in raw.items() if k is not None}
    names = sorted(D)
    days = sorted({int(t) for K in D.values() for t in K["t"]})
    dpos = {d: i for i, d in enumerate(days)}
    V = np.full((len(names), len(days)), np.nan)
    for a, s in enumerate(names):
        K = D[s]
        v = sma(K["bv"] * K["c"], 30)                 # 30-day dollar volume (base × close), as the live desk computes it
        for q in range(119, len(K["t"])):             # ≥ 120 days of history
            if not np.isnan(v[q]):
                V[a, dpos[int(K["t"][q])]] = v[q]
    R = np.full(V.shape, 9999, np.int16)
    for d in range(len(days)):
        ok = np.flatnonzero(~np.isnan(V[:, d]))
        R[ok[np.argsort(-V[ok, d])], d] = np.arange(len(ok))
    B = D["BTC"]
    bc = B["c"]
    s200 = sma(bc, 200)
    with np.errstate(invalid="ignore"):
        bull_b = np.where(np.isnan(s200), np.nan, ((bc > s200) & (bc > np.r_[np.full(60, np.nan), bc[:-60]])).astype(float))
    bull = np.full(len(days), np.nan)
    for q, t in enumerate(B["t"]):
        bull[dpos[int(t)]] = bull_b[q]
    d_from = np.searchsorted(days, int(dt.datetime(2022, 11, 1, tzinfo=dt.timezone.utc).timestamp() * 1000))
    sel = sorted({names[a] for a in range(len(names)) if R[a, d_from:].min() < TOP} | {"BTC"})
    today = dt.datetime.now(dt.timezone.utc).date()
    end = int(dt.datetime(today.year, today.month, today.day, tzinfo=dt.timezone.utc).timestamp() * 1000)
    NB = (end - START) // BAR

    def one(s):
        sym = s + "USDT"
        st, b = http(f"{S3}?prefix=data/futures/um/monthly/klines/{sym}/15m/")
        months = sorted(m for m in set(re.findall(rf"{sym}-15m-(\d{{4}}-\d{{2}})\.zip</Key>", b if st == 200 else "")) if m >= "2022-08")
        parts = []
        for mo in months:
            st, z = http(f"{UM}/monthly/klines/{sym}/15m/{sym}-15m-{mo}.zip", binary=True)
            if st == 200 and z:
                a = _parse_kl(z)
                if a is not None:
                    parts.append(a)
        last_m = (today.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
        if months and months[-1] == last_m:          # still listed → this month's daily files
            d = today.replace(day=1)
            while d < today:
                st, z = http(f"{UM}/daily/klines/{sym}/15m/{sym}-15m-{d:%Y-%m-%d}.zip", binary=True)
                if st == 200 and z:
                    a = _parse_kl(z)
                    if a is not None:
                        parts.append(a)
                d += dt.timedelta(days=1)
        st, b = http(f"{S3}?prefix=data/futures/um/monthly/fundingRate/{sym}/")
        fm = sorted(m for m in set(re.findall(rf"{sym}-fundingRate-(\d{{4}}-\d{{2}})\.zip</Key>", b if st == 200 else "")) if m >= "2022-07")
        fund = []
        for mo in fm:
            st, z = http(f"{UM}/monthly/fundingRate/{sym}/{sym}-fundingRate-{mo}.zip", binary=True)
            if st == 200 and z:
                fund += _parse_fund(z)
        return s, (np.concatenate(parts) if parts else None), fund, len(months)

    with ThreadPoolExecutor(32) as ex:
        res = list(ex.map(one, sel))
    keep = [r for r in res if r[1] is not None and len(r[1]) > 96 * 30]
    n = len(keep)
    O = np.full((n, NB), np.nan, np.float32)
    H, L, C, Q = O.copy(), O.copy(), O.copy(), O.copy()
    f_t, f_r, f_off = [], [], [0]
    out_names = []
    for a, (s, arr, fund, _nm) in enumerate(keep):
        j = ((arr[:, 0] - START) // BAR).astype(np.int64)
        ok = (j >= 0) & (j < NB) & ((arr[:, 0] - START) % BAR == 0)
        j, arr2 = j[ok], arr[ok]
        O[a, j], H[a, j], L[a, j], C[a, j], Q[a, j] = arr2[:, 1], arr2[:, 2], arr2[:, 3], arr2[:, 4], arr2[:, 5]
        fd = sorted(set(fund))
        f_t += [t for t, _r in fd]
        f_r += [r for _t, r in fd]
        f_off.append(len(f_t))
        out_names.append(s)
    rows = [names.index(s) for s in out_names]
    np.savez(os.path.join(out_dir, "fnet.npz"), start=START, syms=np.array(out_names), O=O, H=H, L=L, C=C, Q=Q,
             rank=R[rows], rank_days=np.array(days, np.int64), bull=bull,
             f_t=np.array(f_t, np.int64), f_r=np.array(f_r, np.float64), f_off=np.array(f_off, np.int64))
    last_ok = int(np.flatnonzero(np.isfinite(C[out_names.index("BTC")]))[-1])
    meta = {"perps_in_archive": len(syms), "with_120d": len(names), "ever_top40_since_2022_11": len(sel), "kept_15m": n,
            "delisted_like": int(sum(1 for a in range(n) if not np.isfinite(C[a, last_ok]))),
            "funding_events": len(f_t), "bars": int(NB), "last_bar": dt.datetime.fromtimestamp((START + last_ok * BAR) / 1000, dt.timezone.utc).isoformat(),
            "minutes": round((dt.datetime.now() - t_start).seconds / 60, 1)}
    with open(os.path.join(out_dir, "meta.json"), "w") as f:
        json.dump(meta, f)
    note("frequent net · data", json.dumps(meta))


# -------------------------------------------------------------------------------------------------------- indicators
def shift(x, k):
    out = np.full(len(x), np.nan)
    if 0 < k < len(x):
        out[k:] = x[:-k]
    return out


def roll_mean(x, n):
    ok = np.isfinite(x)
    cs = np.concatenate([[0.0], np.cumsum(np.where(ok, x, 0.0))])
    cn = np.concatenate([[0], np.cumsum(ok)])
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        s, k = cs[n:] - cs[:-n], cn[n:] - cn[:-n]
        with np.errstate(invalid="ignore", divide="ignore"):
            out[n - 1:] = np.where(k == n, s / n, np.nan)
    return out


def roll_std_prev(x, n, need):
    """σ of the previous n values (the current one excluded), NaN unless ≥ need of them exist."""
    ok = np.isfinite(x)
    xf = np.where(ok, x, 0.0)
    c1 = np.concatenate([[0.0], np.cumsum(xf)])
    c2 = np.concatenate([[0.0], np.cumsum(xf * xf)])
    cn = np.concatenate([[0], np.cumsum(ok)])
    out = np.full(len(x), np.nan)
    if len(x) > n:
        s1, s2, k = c1[n:-1] - c1[:-n - 1], c2[n:-1] - c2[:-n - 1], cn[n:-1] - cn[:-n - 1]
        with np.errstate(invalid="ignore", divide="ignore"):
            var = (s2 - s1 * s1 / k) / (k - 1)
            out[n:] = np.where(k >= need, np.sqrt(np.maximum(var, 0.0)), np.nan)
    return out


def roll_max_prev(x, n):
    from numpy.lib.stride_tricks import sliding_window_view
    out = np.full(len(x), np.nan)
    if len(x) > n:
        out[n:] = sliding_window_view(x, n).max(axis=1)[:-1]
    return out


def roll_min_prev(x, n):
    from numpy.lib.stride_tricks import sliding_window_view
    out = np.full(len(x), np.nan)
    if len(x) > n:
        out[n:] = sliding_window_view(x, n).min(axis=1)[:-1]
    return out


def rsi2(c):
    d = np.diff(c, prepend=np.nan)
    with np.errstate(invalid="ignore"):
        up, dn = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
    up[np.isnan(d)] = np.nan
    dn[np.isnan(d)] = np.nan
    au, ad = roll_mean(up, 2), roll_mean(dn, 2)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(ad == 0, 100.0, 100 - 100 / (1 + au / ad))


class Coin:
    """One coin: 15m arrays (float64) and lazily built 1h / 4h / daily bars with their indicators."""

    def __init__(self, ctx, a):
        self.a, self.sym = a, ctx.syms[a]
        self.o, self.h, self.l, self.c, self.v = (ctx.A[k][a].astype(np.float64) for k in ("O", "H", "L", "C", "Q"))
        self._tf = {}
        lo, hi = ctx.A["f_off"][a], ctx.A["f_off"][a + 1]
        self.ft, self.fr = ctx.A["f_t"][lo:hi], ctx.A["f_r"][lo:hi]

    def tf(self, g):
        if g not in self._tf:
            if g == 1:
                o, h, l, c, v = self.o, self.h, self.l, self.c, self.v
            else:
                n = len(self.c) // g * g
                O_, H_, L_, C_, V_ = (x[:n].reshape(-1, g) for x in (self.o, self.h, self.l, self.c, self.v))
                bad = np.isnan(C_).any(1) | np.isnan(O_).any(1) | np.isnan(H_).any(1) | np.isnan(L_).any(1)
                o, h, l, c, v = O_[:, 0].copy(), H_.max(1), L_.min(1), C_[:, -1].copy(), np.nansum(V_, 1)
                for x in (o, h, l, c, v):
                    x[bad] = np.nan
            pc = shift(c, 1)
            with np.errstate(invalid="ignore"):
                tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
            self._tf[g] = {"o": o, "h": h, "l": l, "c": c, "v": v, "atr": roll_mean(tr, 14), "rsi2": rsi2(c),
                           "s5": roll_mean(c, 5), "s20": roll_mean(c, 20), "s50": roll_mean(c, 50)}
        return self._tf[g]

    def funding(self, t0, t1, side):
        """Funding paid (−) or received (+), % of notional, for settlements in (t0, t1]."""
        if t1 <= t0:
            return 0.0
        lo, hi = np.searchsorted(self.ft, t0, "right"), np.searchsorted(self.ft, t1, "right")
        if hi > lo:
            return float(-side * self.fr[lo:hi].sum() * 100)
        near = np.searchsorted(self.ft, t0 - DAY), np.searchsorted(self.ft, t1 + DAY)
        if near[1] > near[0]:                          # the archive has rates around → simply no settlement inside
            return 0.0
        n8 = (t1 // (8 * 3600_000)) - (t0 // (8 * 3600_000))
        return float(-side * n8 * FUND_DEF)


# ------------------------------------------------------------------------------------------------------------ trades
def trade(cd, j0, side, mode, lim, life, stop_pct, tgt_pct, max_bars, nend, exit_mask=None, trail_pct=None, trail_g=4):
    """One trade on the 15m path. j0 = the first bar after the signal close. → dict, ("nofill", j) or None (still open at
    the end of the data)."""
    o, h, l, c = cd.o, cd.h, cd.l, cd.c
    if j0 >= nend or not np.isfinite(o[j0]):
        return None
    if mode == "mkt":
        jf, fill, fee_in, sx = j0, o[j0] * (1 + side * SLIP / 100), TAKER, 1
    else:
        e = min(j0 + life, nend)
        seg = l[j0:e] if side == 1 else h[j0:e]
        with np.errstate(invalid="ignore"):
            hit = np.flatnonzero(seg <= lim * (1 - THROUGH / 100)) if side == 1 else np.flatnonzero(seg >= lim * (1 + THROUGH / 100))
        if not len(hit):
            return ("nofill", e)
        jf = j0 + int(hit[0])
        fill = min(lim, o[jf]) if side == 1 else max(lim, o[jf])
        fee_in, sx = MAKER, 0
    sd = fill * stop_pct / 100
    S = fill - side * sd
    T = fill + side * fill * tgt_pct / 100 if tgt_pct else None
    end = min(nend, jf + max_bars + 1)
    O, Hs, Ls = o[jf:end], h[jf:end], l[jf:end]
    bad = np.flatnonzero(~np.isfinite(O) | ~np.isfinite(Ls) | ~np.isfinite(Hs))
    lim_end = int(bad[0]) if len(bad) else len(O)
    with np.errstate(invalid="ignore"):
        st = np.flatnonzero(Ls[:lim_end] <= S) if side == 1 else np.flatnonzero(Hs[:lim_end] >= S)
        k_stop = int(st[0]) if len(st) else BIG
        k_tgt = BIG
        if T is not None and lim_end > 1:
            tg = np.flatnonzero(Hs[1:lim_end] >= T * (1 + THROUGH / 100)) if side == 1 else np.flatnonzero(Ls[1:lim_end] <= T * (1 - THROUGH / 100))
            k_tgt = int(tg[0]) + 1 if len(tg) else BIG
        k_rule = BIG
        if exit_mask is not None and lim_end > 1:
            em = np.flatnonzero(exit_mask[jf + 1:jf + lim_end])
            k_rule = int(em[0]) + 1 if len(em) else BIG
        k_trail = BIG
        if trail_pct:
            ks = np.arange(lim_end)
            ks = ks[(jf + ks + 1) % trail_g == 0]
            if len(ks):
                cl = c[jf + ks]
                best = (np.maximum if side == 1 else np.minimum).accumulate(np.r_[fill, cl])[1:]
                td = fill * trail_pct / 100
                trg = np.flatnonzero(cl < best - td) if side == 1 else np.flatnonzero(cl > best + td)
                if len(trg) and ks[trg[0]] + 1 < lim_end:
                    k_trail = int(ks[trg[0]]) + 1
    k_time = max_bars if max_bars < lim_end else BIG
    k_open = min(k_rule, k_trail, k_time)                   # exits at a bar's open
    if k_open <= min(k_stop, k_tgt):
        if k_open == BIG:                                     # nothing happened before the data stops
            if jf + lim_end >= nend:
                return None                                   # still open at the end of the data
            k = lim_end - 1                                   # the coin stopped trading: out at its last close
            px, fee_out, sx_out, kind = c[jf + k] * (1 - side * SLIP / 100), TAKER, 1, "delist"
            kx = k + 1
        else:
            px, fee_out, sx_out = O[k_open] * (1 - side * SLIP / 100), TAKER, 1
            kind = "rule" if k_open == k_rule else ("trail" if k_open == k_trail else "time")
            kx = k_open
    elif k_stop <= k_tgt:
        base = min(O[k_stop], S) if side == 1 else max(O[k_stop], S)
        px, fee_out, sx_out, kind, kx = base * (1 - side * SLIP_STOP / 100), TAKER, 0, "stop", k_stop
    else:
        px, fee_out, sx_out, kind, kx = T, MAKER, 0, "target", k_tgt
    jx = jf + kx
    gross = side * (px / fill - 1) * 100
    fund = cd.funding(START + jf * BAR, START + jx * BAR, side)
    fees = fee_in + fee_out
    return {"jf": jf, "jx": jx, "side": side, "fill": float(fill), "gross": float(gross), "fees": fees, "sx": sx + sx_out,
            "fund": fund, "net": float(gross - fees + fund), "stop": float(stop_pct), "kind": kind}


# ------------------------------------------------------------------------------------------------------------ setups
def spec_list(only):
    S = []

    def add(setup, name, **kw):
        if not only or setup in only:
            S.append(dict(setup=setup, name=name, **kw))
    for nm, mode, vol in (("A1", "mkt", 0), ("A2", "lim", 0), ("A3", "mkt", 1.5)):
        add("A", nm, rule="A", g=16, side=1, mode=mode, vol=vol, life=16)
    # round 2 — the neighbours of A3, the only near-pass of round 1: other volume marks and a maker entry
    for nm, mode, vol in (("A3v13", "mkt", 1.3), ("A3v17", "mkt", 1.7), ("A3v20", "mkt", 2.0), ("A4", "lim", 1.5)):
        add("A3X", nm, rule="A", g=16, side=1, mode=mode, vol=vol, life=16)
    for nm, mode, k in (("B1", "mkt", 1.5), ("B2", "lim", 1.5), ("B3", "mkt", 2.5)):
        add("B", nm, rule="B", g=4, side=1, mode=mode, k=k, life=4)
    for nm, mode, ex, side in (("C1t", "mkt", "trail", 1), ("C1b", "mkt", "bracket", 1), ("C2b", "lim", "bracket", 1),
                               ("Cs", "mkt", "bracket", -1)):
        add("C", nm, rule="C", g=4, side=side, mode=mode, ex=ex, life=16)
    for nm, mode, ex in (("D1", "mkt", "bracket"), ("D2", "lim", "bracket"), ("D3", "mkt", "time")):
        add("D", nm, rule="D", g=1, side=1, mode=mode, ex=ex, life=1)
    for nm, mode in (("E1", "mkt"), ("E2", "lim")):
        add("E", nm, rule="E", g=96, side=1, mode=mode, life=96)
    add("REF", "REF-P", rule="P", g=96, side=1, mode="mkt", life=96)
    add("REF", "REF-B20", rule="B20", g=96, side=1, mode="mkt", life=96)
    return S


def build(cd, sp, nb):
    """→ per signal bar k of the spec's timeframe: sig (bool), stop %, target %, trail %, limit price, max bars, exit mask."""
    g, rule = sp["g"], sp["rule"]
    X = cd.tf(g)
    c, h, l, v, atr = X["c"], X["h"], X["l"], X["v"], X["atr"]
    n = len(c)
    tgt = trail = mask = None
    lim = c
    with np.errstate(invalid="ignore", divide="ignore"):
        atrp = atr / c * 100
        if rule == "A":
            sig = (c > X["s50"]) & (X["s50"] > shift(X["s50"], 5)) & (X["rsi2"] < 10)
            if sp["vol"]:
                sig &= v >= sp["vol"] * roll_mean(v, 180)
            stop = np.clip(2.5 * atrp, 1.5, 10)
            mx = 10 * 16
            ex = np.flatnonzero(c > X["s5"])
        elif rule == "B":
            X4, XD = cd.tf(16), cd.tf(96)
            up4 = (X4["c"] > X4["s50"]) & (X4["s50"] > shift(X4["s50"], 5))
            upD = XD["c"] > XD["s50"]
            k = np.arange(n)
            i4 = (k + 1) * 4 // 16 - 1
            iD = (k + 1) * 4 // 96 - 1
            ok4 = np.zeros(n, bool)
            okD = np.zeros(n, bool)
            m4 = (i4 >= 0) & (i4 < len(up4))
            mD = (iD >= 0) & (iD < len(upD))
            ok4[m4] = up4[i4[m4]]
            okD[mD] = upD[iD[mD]]
            sig = (X["rsi2"] < 10) & ok4 & okD
            stop = np.clip(sp["k"] * atrp, 0.4, 5)
            tgt = stop
            mx = 24 * 4
            ex = None
        elif rule == "C":
            side = sp["side"]
            vol = v >= 1.5 * roll_mean(shift(v, 1), 168)
            if side == 1:
                lvl = roll_max_prev(h, 48)
                sig = (c > lvl) & vol
            else:
                lvl = roll_min_prev(l, 48)
                sig = (c < lvl) & vol
            if sp["ex"] == "trail":
                stop = np.clip(2 * atrp, 0.7, 8)
                trail = 3 * atrp
            else:
                stop = np.clip(1.5 * atrp, 0.5, 6)
                tgt = 2 * stop
            if sp["mode"] == "lim":
                lim = lvl
            mx = 48 * 4
            ex = None
        elif rule == "D":
            r = np.log(c / shift(c, 1))
            sd = roll_std_prev(r, 672, 600)
            sig = r <= -3 * sd
            drop = -r * 100
            if sp["ex"] == "bracket":
                stop = np.clip(drop, 0.3, 10)
                tgt = stop
                mx = 32
            else:
                stop = np.clip(3 * drop, 0.9, 20)
                mx = 16
            ex = None
        elif rule == "E":
            sig = (c > roll_max_prev(h, 5)) & (c > X["s20"]) & (v >= 1.5 * roll_mean(v, 30))
            stop = np.clip(2.5 * atrp, 3, 15)
            mx = 14 * 96
            ex = np.flatnonzero(c < shift(l, 1))
        elif rule == "P":
            sig = (c > X["s50"]) & (X["s50"] > shift(X["s50"], 5)) & (X["rsi2"] < 10) & (v >= 1.5 * roll_mean(v, 30))
            stop = np.clip(2.5 * atrp, 3, 15)
            mx = 10 * 96
            ex = np.flatnonzero(c > X["s5"])
        else:   # B20 — the own desk's 20-day breakout, chandelier exit 3 ATR from the best close
            sig = (c > roll_max_prev(h, 20)) & (c > X["s20"]) & (v >= 1.5 * roll_mean(v, 30))
            stop = np.clip(2.5 * atrp, 3, 15)
            trail = 3 * atrp
            mx = 14 * 96
            ex = None
    if ex is not None:
        mask = np.zeros(nb, bool)
        jj = (ex + 1) * g
        mask[jj[jj < nb]] = True
    return {"sig": np.nan_to_num(sig).astype(bool) if sig.dtype != bool else sig, "stop": stop, "tgt": tgt, "trail": trail,
            "lim": lim, "mx": mx, "mask": mask}


# ----------------------------------------------------------------------------------------------------------- analysis
class Ctx:
    def __init__(self, path):
        Z = np.load(path, allow_pickle=True)
        self.A = {k: Z[k] for k in ("O", "H", "L", "C", "Q", "f_t", "f_r", "f_off")}          # an npz re-reads on every access
        self.syms = [str(s) for s in Z["syms"]]
        self.nb = self.A["O"].shape[1]
        bi = self.syms.index("BTC")
        self.nend = int(np.flatnonzero(np.isfinite(self.A["C"][bi]))[-1]) + 1
        nd = self.nb // 96 + 2
        gd = START + np.arange(nd) * DAY
        rd = Z["rank_days"]
        pos = np.searchsorted(rd, gd)
        okp = (pos < len(rd)) & (rd[np.minimum(pos, len(rd) - 1)] == gd)
        self.bull = np.full(nd, np.nan)
        self.bull[okp] = Z["bull"][pos[okp]]
        self.rank = np.full((len(self.syms), nd), 9999, np.int16)
        self.rank[:, okp] = Z["rank"][:, pos[okp]]
        self.jtest = (TEST - START) // BAR
        self.days_test = (self.nend - self.jtest) / 96
        self.year_days = {}
        for d in range(self.jtest // 96, self.nend // 96):
            y = dt.datetime.fromtimestamp((START + d * DAY) / 1000, dt.timezone.utc).strftime("%Y")
            self.year_days[y] = self.year_days.get(y, 0) + 1
        self.bull_days = float(np.sum(self.bull[np.arange(self.jtest // 96, self.nend // 96) - 1] == 1))
        self.mon = np.array([int(dt.datetime.fromtimestamp(x / 1000, dt.timezone.utc).strftime("%Y%m")) for x in gd])

    def gate_ok(self, a, js, side):
        dd = js // 96 - 1
        want = 1.0 if side == 1 else 0.0
        return (dd >= 0) & (self.bull[dd] == want) & (self.rank[a, dd] < TOP)


def run_coin(ctx, cd, sp):
    """All trades of one rule on one coin (one position at a time) + 3 paired random entries for each."""
    rng = np.random.default_rng(zlib.crc32(f"{sp['name']}|{cd.sym}".encode()))
    B = build(cd, sp, ctx.nb)
    g, side = sp["g"], sp["side"]
    ks = np.flatnonzero(B["sig"])
    js = (ks + 1) * g
    m = (js >= ctx.jtest) & (js < ctx.nend)
    ks, js = ks[m], js[m]
    m = ctx.gate_ok(cd.a, js, side)
    ks, js = ks[m], js[m]
    out, rnd, nofill = [], [], 0
    busy = -1
    for k, j in zip(ks, js):
        if j <= busy:
            continue
        st, tg = B["stop"][k], (B["tgt"][k] if B["tgt"] is not None else None)
        tr_ = B["trail"][k] if B["trail"] is not None else None
        if not np.isfinite(st) or (tg is not None and not np.isfinite(tg)) or (tr_ is not None and not np.isfinite(tr_)):
            continue
        lim = B["lim"][k]
        r = trade(cd, j, side, sp["mode"], lim, sp["life"], st, tg, B["mx"], ctx.nend, B["mask"], tr_, g if g > 1 else 4)
        if r is None:
            continue
        if isinstance(r, tuple):
            busy = r[1]
            nofill += 1
            continue
        busy = r["jx"]
        r["a"], r["k"] = cd.a, int(k)
        out.append(r)
    # random entries: same coin, year, regime, side, mode and exit geometry (stop / target / trail %, hold, rule exits)
    if out:
        cand = np.arange(ctx.jtest // g + 1, ctx.nend // g - 1) * g
        cand = cand[ctx.gate_ok(cd.a, cand, side) & np.isfinite(cd.o[cand]) & np.isfinite(cd.c[cand - 1])]
        if len(cand):
            mon_c = ctx.mon[cand // 96] // 100                # the year (house style: same coin, side, year); time-sorted
            for r in out:
                mk = ctx.mon[r["jf"] // 96] // 100
                pool = cand[np.searchsorted(mon_c, mk, "left"):np.searchsorted(mon_c, mk, "right")]
                if not len(pool):
                    continue
                tg = B["tgt"][r["k"]] if B["tgt"] is not None else None
                tr_ = B["trail"][r["k"]] if B["trail"] is not None else None
                for j in rng.choice(pool, 3):
                    j = int(j)
                    rr = trade(cd, j, side, sp["mode"], cd.c[j - 1], sp["life"], r["stop"], tg, B["mx"], ctx.nend, B["mask"], tr_, g if g > 1 else 4)
                    if isinstance(rr, dict):
                        rnd.append(rr["net"])
    return out, rnd, nofill, len(ks)


def account(ctx, trades, coins_c):
    """Fixed $100 risk a trade, position ≤ 2× / all open ≤ 5× the $10 000 account, ≤ 20 open; equity marked hourly."""
    trades = sorted(trades, key=lambda r: (r["jf"], r["a"]))
    opn, taken = [], []
    for r in trades:
        opn = [x for x in opn if x[0] > r["jf"]]
        notional = min(RISK_USD / (r["stop"] / 100), POS_MAX * ACCOUNT)
        if len(opn) >= OPEN_MAX or sum(x[1] for x in opn) + notional > GROSS_MAX * ACCOUNT:
            continue
        opn.append((r["jx"], notional))
        r = dict(r, notional=notional, usd=notional * r["net"] / 100, fees_usd=notional * r["fees"] / 100)
        taken.append(r)
    nh = ctx.nb // 4 + 2
    realized, unreal = np.zeros(nh), np.zeros(nh)
    for r in taken:
        h0, h1 = r["jf"] // 4, r["jx"] // 4
        if h1 > h0:
            c1 = coins_c[r["a"]][h0 * 4 + 3: h1 * 4: 4]
            c1 = np.where(np.isfinite(c1), c1, r["fill"])
            unreal[h0:h0 + len(c1)] += r["notional"] * r["side"] * (c1 / r["fill"] - 1)
        realized[h1] += r["usd"]
    hs = ctx.jtest // 4
    eq = ACCOUNT + np.cumsum(realized)[hs:ctx.nend // 4 + 1] + unreal[hs:ctx.nend // 4 + 1]
    peak = np.maximum.accumulate(eq)
    dd = max(-100.0, float((eq / peak - 1).min()) * 100)            # % of the peak (a losing book can fall below zero)
    dd_usd = float((eq - peak).min()) / ACCOUNT * 100                # % of the starting $10 000
    return taken, dd, dd_usd


def sp_side(taken):
    return 1 if np.mean([r["side"] for r in taken]) > 0 else -1


def stats(ctx, name, trades, rnd, nofill, ncand, coins_c):
    if not trades:
        return {"name": name, "n": 0}
    taken, dd, dd_usd = account(ctx, trades, coins_c)
    net = np.array([r["net"] for r in taken])
    usd = np.array([r["usd"] for r in taken])
    yr = np.array([dt.datetime.fromtimestamp((START + r["jf"] * BAR) / 1000, dt.timezone.utc).strftime("%Y") for r in taken])
    mo = np.array([dt.datetime.fromtimestamp((START + r["jx"] * BAR) / 1000, dt.timezone.utc).strftime("%Y-%m") for r in taken])
    months = sorted(set(mo))
    m_usd = np.array([usd[mo == m].sum() for m in months])
    total = float(usd.sum())
    kinds = {}
    for r in taken:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    held_h = np.array([(r["jx"] - r["jf"]) / 4 for r in taken])
    fees_usd = np.array([r["fees_usd"] for r in taken])
    stress = net - 0.02 * np.array([r["sx"] for r in taken])
    all_net = np.array([r["net"] for r in trades])
    res = {
        "name": name, "n": len(taken), "tpd": round(len(taken) / ctx.days_test, 2), "sig_pd": round(ncand / ctx.days_test, 2),
        "nofill": nofill, "win": round(float((net > 0).mean()) * 100, 1),
        "avg": round(float(net.mean()), 3), "avg_gross": round(float(np.mean([r["gross"] for r in taken])), 3),
        "fees": round(float(np.mean([r["fees"] for r in taken])), 3), "slip_n": round(float(np.mean([r["sx"] for r in taken])), 2),
        "fund": round(float(np.mean([r["fund"] for r in taken])), 3), "avg_stress": round(float(stress.mean()), 3),
        "avg_all_signals": round(float(all_net.mean()), 3), "rand": round(float(np.mean(rnd)), 3) if rnd else None, "rand_n": len(rnd),
        "stop_med": round(float(np.median([r["stop"] for r in taken])), 2), "hold_h": round(float(held_h.mean()), 1),
        "notional": round(float(np.mean([r["notional"] for r in taken]))),
        "user_usd": round(float(usd.mean()), 2), "fees_usd": round(float(fees_usd.mean()), 2),
        "ref20": round(float(fees_usd.mean()) * 0.2, 2), "ref50": round(float(fees_usd.mean()) * 0.5, 2),
        "y_avg": {y: (round(float(net[yr == y].mean()), 3) if (yr == y).any() else None) for y in YEARS},
        "y_n": {y: int((yr == y).sum()) for y in YEARS},
        "y_pct": {y: round(float(usd[yr == y].sum()) / ACCOUNT * 100, 1) for y in YEARS},
        "total_pct": round(total / ACCOUNT * 100, 1), "dd": round(dd, 1), "dd_usd": round(dd_usd, 1),
        "avg_R": round(float(np.mean([r["net"] / r["stop"] for r in taken])), 3),
        "tpd_bull": round(len(taken) / max(1.0, ctx.bull_days if sp_side(taken) == 1 else ctx.days_test - ctx.bull_days), 2),
        "best_m_share": round(float(m_usd.max()) / total * 100, 1) if total > 0 else None,
        "best_m": months[int(np.argmax(m_usd))] if len(months) else None, "months_plus": round(float((m_usd > 0).mean()) * 100),
        "m_usd": {m: round(float(x)) for m, x in zip(months, m_usd)} if name.startswith(("A3", "A4")) else None,
        "tpd_y": {y: round(int((yr == y).sum()) / max(1.0, ctx.year_days.get(y, 1)), 2) for y in YEARS},
        "kinds": kinds, "t_stat": round(float(net.mean() / (net.std() + 1e-12) * np.sqrt(len(net))), 2),
    }
    res["pass"] = {"avg>=0.25": res["avg"] >= 0.25, "years+": all(res["y_pct"][y] > 0 for y in YEARS),
                   "tpd>=2": res["tpd"] >= 2, "dd<=25": res["dd"] >= -25,
                   "month<=25%": res["best_m_share"] is not None and res["best_m_share"] <= 25}
    res["PASS"] = all(res["pass"].values())
    return res


def analyze(path, only):
    ctx = Ctx(path)
    specs = spec_list(set(only.split(",")) if only else None)
    acc = {sp["name"]: ([], [], 0, 0) for sp in specs}
    coins_c = {}
    for a in range(len(ctx.syms)):
        if (ctx.rank[a] < TOP).sum() == 0:
            continue
        cd = Coin(ctx, a)
        coins_c[a] = cd.c
        for sp in specs:
            t, r, nf, nc = run_coin(ctx, cd, sp)
            T, R, NF, NC = acc[sp["name"]]
            acc[sp["name"]] = (T + t, R + r, NF + nf, NC + nc)
    out = {}
    for sp in specs:
        T, R, NF, NC = acc[sp["name"]]
        out[sp["name"]] = stats(ctx, sp["name"], T, R, NF, NC, coins_c)
    if any(sp["setup"] == "REF" for sp in specs):           # the own desk as one book: both rules in one account
        T = acc["REF-P"][0] + acc["REF-B20"][0]
        R = acc["REF-P"][1] + acc["REF-B20"][1]
        out["REF"] = stats(ctx, "REF", T, R, acc["REF-P"][2] + acc["REF-B20"][2], acc["REF-P"][3] + acc["REF-B20"][3], coins_c)
    for k, v in out.items():
        publish(f"fnet · {k}", v)
    os.makedirs("fnet_out", exist_ok=True)
    with open(os.path.join("fnet_out", f"res_{only or 'all'}.json"), "w") as f:
        json.dump(out, f, ensure_ascii=False)
    short = {k: [v.get("tpd"), v.get("win"), v.get("avg"), v.get("rand"), v.get("dd"), [v.get("y_pct", {}).get(y) for y in YEARS],
                 v.get("best_m_share"), v.get("user_usd"), v.get("ref20"), v.get("ref50"), v.get("PASS")] for k, v in out.items()}
    publish(f"fnet · summary {(only or 'all').replace(',', '+')} [a day / win % / avg % / random % / dd % / 2023…2026 % / best month share % / user $ / ref 20 % / ref 50 % / pass]", short)


def costs(path):
    """Real funding that a long in the top 40 pays, per 8 h, by year and regime (Binance archive)."""
    ctx = Ctx(path)
    acc = {}
    for a in range(len(ctx.syms)):
        lo, hi = ctx.A["f_off"][a], ctx.A["f_off"][a + 1]
        ft, fr = ctx.A["f_t"][lo:hi], ctx.A["f_r"][lo:hi]
        if not len(ft):
            continue
        dd = (ft - START) // DAY
        ok = (dd >= 1) & (ft >= TEST) & (dd < ctx.rank.shape[1])
        for d in np.unique(dd[ok]):
            if ctx.rank[a, d - 1] >= TOP:
                continue
            day_sum = float(fr[dd == d].sum())
            y = dt.datetime.fromtimestamp((START + int(d) * DAY) / 1000, dt.timezone.utc).strftime("%Y")
            reg = "bull" if ctx.bull[d - 1] == 1 else "other"
            acc.setdefault((y, reg), []).append(day_sum)
    out = {f"{y} {reg}": [round(float(np.mean(v)) / 3 * 100, 4), round(float(np.median(v)) / 3 * 100, 4), len(v)]
           for (y, reg), v in sorted(acc.items())}
    allb = [x for (y, reg), v in acc.items() if reg == "bull" for x in v]
    allo = [x for (y, reg), v in acc.items() if reg == "other" for x in v]
    out["all bull"] = [round(float(np.mean(allb)) / 3 * 100, 4), round(float(np.median(allb)) / 3 * 100, 4), len(allb)] if allb else None
    out["all other"] = [round(float(np.mean(allo)) / 3 * 100, 4), round(float(np.median(allo)) / 3 * 100, 4), len(allo)] if allo else None
    bd = ctx.bull[ctx.jtest // 96: ctx.nend // 96]
    out["bull_share_days_%"] = round(float(np.nanmean(bd == 1)) * 100, 1)
    yrs = {}
    for d in range(ctx.jtest // 96, ctx.nend // 96):
        y = dt.datetime.fromtimestamp((START + d * DAY) / 1000, dt.timezone.utc).strftime("%Y")
        yrs.setdefault(y, []).append(ctx.bull[d - 1] == 1)
    out["bull_days_by_year_%"] = {y: round(float(np.mean(v)) * 100) for y, v in yrs.items()}
    out["test_days"] = round(ctx.days_test, 1)
    publish("fnet · funding % per 8 h for a long in the top 40 [mean / median / coin-days]", out)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "fetch":
        fetch(sys.argv[2])
    elif cmd == "costs":
        costs(os.path.join(sys.argv[2], "fnet.npz"))
    else:
        analyze(os.path.join(sys.argv[2], "fnet.npz"), sys.argv[3] if len(sys.argv) > 3 else "")
