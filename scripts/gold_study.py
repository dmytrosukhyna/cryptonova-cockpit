"""Gold inside crypto venues — an honest check of the engine's simple rule families on gold (19.10.2026, owner: «на золоте
хорошо зарабатывают — движения и каналы довольно понятные»). Paper / history only, docs/research/gold.md.
Runs in GitHub Actions only (.github/workflows/research-gold.yml): this container cannot reach exchange APIs.

Modes
  probe   gold instruments on crypto venues (PAXG / XAUT spot and perps, XAU perps): history length, fees, funding;
          long clean intraday gold sources (Binance archive PAXGUSDT, Dukascopy XAUUSD hour candles).
  fetch   PAXGUSDT spot 1 h (Binance archive, from its first month), Binance perp funding of PAXGUSDT / XAUUSDT, Dukascopy
          XAUUSD 1 h (2015 →) → $GOLD_DIR/*.npz
  run S   S = paxg | xau: every rule family on that series, fixed variant grids decided before the run, train ≤ 2024,
          test 2025-01-01 → today. One position at a time per rule, 1× notional (the % are % of capital, no leverage).

Rules (signal on a closed bar, filled at the next bar's open unless it is a limit order; inside a bar the stop comes first):
  trend     daily: close above SMA N (20/50/100/200); Donchian N-day breakout, exit on the N/2 opposite extreme (20/55);
            momentum of L days (21/63/126/252) — each long-only (flat otherwise) or long-short.
  channel   4 h: bounce from the N-bar channel edge (60/120) in the trend (close vs SMA N), target the middle or the far edge,
            stop 1 ATR beyond the edge; false break of the N-bar extreme (20/50) back inside, stop beyond the wick, target 1R/2R.
  levels    1 h volume profile of 30 days (60 bins, peak ≥ 1.5× the median bin) rebuilt every 4 h; touch-and-hold of the
            nearest strong level, stop 0.5/1 ATR beyond, target the next level (≤ 2R), out after 48 h; ± volume burst,
            ± trend side (SMA 200 h).
  range     1 h limit orders at the bottom / top 20–30 % of a flat N-day range (7/14), take at 50 % / 80 %, stop 15 % of the
            width beyond, out after 3 days. Maker fills (filled only if the price trades 0.01 % through).
  sessions  London / New York opening-hour breakout (follow or fade, out after 4 h or at the session end), Asia-range
            breakout in London (follow / fade); hour-of-day drift (enter at hour h UTC, hold 1/4 h, long or short).
Costs (perpetual, the engine's way): 0.06 % a side at market (taker 0.05 % + 0.01 % slippage), 0.02 % for a filled limit;
funding paid by longs / received by shorts every hour at the Binance PAXGUSDT perp's real rate where it exists, else
0.01 % per 8 h. Long-only trend rules are also shown on spot (0.12 % a side, no funding). Benchmark: buy-and-hold PAXG spot.
Selection: per family the variant with the best train Sharpe (≥ 20 trades); its test result is reported next to the
family's median test result and the share of variants positive on test. Stdlib + numpy."""
import datetime as dt
import io
import json
import lzma
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import numpy as np

UA = {"User-Agent": "Mozilla/5.0 cryptonova-research/1.0", "Accept": "*/*"}
VISION = "https://data.binance.vision/data"
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
OUT = os.environ.get("GOLD_DIR", "/tmp/gold")
H, DAY = 3_600_000, 86_400_000
TODAY = dt.datetime.now(dt.timezone.utc).date()
TEST0 = int(dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
FEE, FEE_LIMIT, FEE_SPOT = 0.0006, 0.0002, 0.0012
FUND_DEFAULT_8H = 0.0001
LDN, NYC = ZoneInfo("Europe/London"), ZoneInfo("America/New_York")


def note(title, msg, level="notice"):
    print(f"{title}: {msg}", flush=True)
    m = str(msg).replace("%", "%25").replace("\r", "").replace("\n", "%0A")[:4000]
    print(f"::{level} title={title}::{m}", flush=True)


def get(url, body=None, tries=4, raw=False, timeout=40):
    err = None
    for a in range(tries):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                         headers={**UA, "Content-Type": "application/json"}, method="POST" if body is not None else "GET")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                b = r.read()
                return r.status, (b if raw else json.loads(b.decode()))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5 + 5 * a)
                continue
            if 400 <= e.code < 500:
                return e.code, None
            err = e
        except Exception as e:  # noqa: BLE001
            err = e
        time.sleep(1.5 * (a + 1))
    return -1, str(err)[:200]


def zip_rows(b):
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        return [ln.split(",") for ln in z.read(z.namelist()[0]).decode().splitlines() if ln[:1].isdigit()]


def duka_decode(raw, base, scale):
    out = []
    for i in range(0, len(raw) - 23, 24):
        t, o, c, lo, hi, v = struct.unpack(">5if", raw[i:i + 24])
        out.append((base + t * 1000, o / scale, hi / scale, lo / scale, c / scale, v))
    return out


def duka_hours(sym, year, month0, scale=1000):
    """Dukascopy hour candles of one month → [(ms, o, h, l, c, vol)] (BID side). Big-endian 24-byte records."""
    s, b = get(f"https://datafeed.dukascopy.com/datafeed/{sym}/{year}/{month0:02d}/BID_candles_hour_1.bi5", raw=True, tries=3, timeout=20)
    if s != 200 or not b:
        return s, []
    base = int(dt.datetime(year, month0 + 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    return s, duka_decode(lzma.decompress(b), base, scale)


def duka_minutes_day(sym, d, scale=1000):
    s, b = get(f"https://datafeed.dukascopy.com/datafeed/{sym}/{d.year}/{d.month - 1:02d}/{d.day:02d}/BID_candles_min_1.bi5", raw=True, tries=2, timeout=20)
    if s != 200 or not b:
        return []
    base = int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp() * 1000)
    return duka_decode(lzma.decompress(b), base, scale)


# ================================================================================================== probe
def probe():
    res = {}
    for name, path in [("spot PAXGUSDT 1h 2020-09", "spot/monthly/klines/PAXGUSDT/1h/PAXGUSDT-1h-2020-09.zip"),
                       ("spot PAXGUSDT 1h 2026-09", "spot/monthly/klines/PAXGUSDT/1h/PAXGUSDT-1h-2026-09.zip"),
                       ("um PAXGUSDT 1h 2026-09", "futures/um/monthly/klines/PAXGUSDT/1h/PAXGUSDT-1h-2026-09.zip"),
                       ("um XAUUSDT 1h 2026-09", "futures/um/monthly/klines/XAUUSDT/1h/XAUUSDT-1h-2026-09.zip"),
                       ("um PAXGUSDT funding 2026-09", "futures/um/monthly/fundingRate/PAXGUSDT/PAXGUSDT-fundingRate-2026-09.zip"),
                       ("um XAUUSDT funding 2026-09", "futures/um/monthly/fundingRate/XAUUSDT/XAUUSDT-fundingRate-2026-09.zip")]:
        s, b = get(f"{VISION}/{path}", raw=True, tries=2)
        res[name] = f"200 rows {len(zip_rows(b))}" if s == 200 else f"HTTP {s}"
    note("gold probe · binance archive", json.dumps(res, ensure_ascii=False))
    first = {}
    for pref in ("spot/monthly/klines/PAXGUSDT/1h/", "futures/um/monthly/klines/PAXGUSDT/1h/", "futures/um/monthly/klines/XAUUSDT/1h/",
                 "futures/um/monthly/klines/XAUTUSDT/1h/", "spot/monthly/klines/XAUTUSDT/1h/"):
        first[pref] = months_in_archive(pref)
    note("gold probe · archive months", json.dumps({k: f"{len(v)} {v[:1]}…{v[-1:]}" for k, v in first.items()}, ensure_ascii=False))
    d = {}
    for y, m0 in ((2015, 0), (2020, 0), (2024, 5), (2026, 8)):
        try:
            s, rows = duka_hours("XAUUSD", y, m0)
            d[f"{y}-{m0 + 1:02d}"] = f"HTTP {s}, {len(rows)} hours, first {rows[0] if rows else None}"
        except Exception as e:  # noqa: BLE001
            d[f"{y}-{m0 + 1:02d}"] = f"error {str(e)[:120]}"
    note("gold probe · dukascopy XAUUSD hours", json.dumps(d, ensure_ascii=False, default=str))


def months_in_archive(pref):
    s, b = get(f"{S3}?prefix=data/{pref}", raw=True, tries=3)
    if s != 200:
        return []
    return sorted(set(m.decode() for m in re.findall(rb"<Key>[^<]*?-(\d{4}-\d{2})\.zip</Key>", b)))


# ================================================================================================== fetch
def vision_series(kind, sym, iv="1h"):
    """kind = spot | um → hourly klines from the archive (monthly + the current month daily)."""
    base = "spot" if kind == "spot" else "futures/um"
    months = months_in_archive(f"{base}/monthly/klines/{sym}/{iv}/")
    urls = [f"{VISION}/{base}/monthly/klines/{sym}/{iv}/{sym}-{iv}-{m}.zip" for m in months]
    d = (dt.date.fromisoformat(months[-1] + "-01") + dt.timedelta(days=32)).replace(day=1) if months else TODAY.replace(day=1)
    while d < TODAY:
        urls.append(f"{VISION}/{base}/daily/klines/{sym}/{iv}/{sym}-{iv}-{d:%Y-%m-%d}.zip")
        d += dt.timedelta(days=1)
    with ThreadPoolExecutor(8) as ex:
        got = list(ex.map(lambda u: get(u, raw=True), urls))
    rows = []
    for s, b in got:
        if s == 200:
            rows += zip_rows(b)
    ms = lambda v: int(v) // 1000 if int(v) > 10 ** 14 else int(v)        # noqa: E731 — newer spot archives are in µs
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: ms(r[0])):
        k = ms(r[0])
        if k in seen:
            continue
        seen.add(k)
        out.append((k, float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7])))   # quote volume
    return np.array(out) if out else None, months


def vision_funding(sym):
    months = months_in_archive(f"futures/um/monthly/fundingRate/{sym}/")
    rows = []
    for m in months:
        s, b = get(f"{VISION}/futures/um/monthly/fundingRate/{sym}/{sym}-fundingRate-{m}.zip", raw=True)
        if s == 200:
            rows += zip_rows(b)
    out = sorted((int(r[0]), float(r[2]), int(r[1]) if r[1] else 8) for r in rows)      # calc_time, rate, interval h
    return out, months


def fetch_paxg():
    info = {}
    paxg, months = vision_series("spot", "PAXGUSDT")
    np.save(f"{OUT}/paxg_1h.npy", paxg)
    info["paxg spot 1h"] = f"{len(paxg)} bars {months[0]}…{months[-1]} + days of this month"
    for sym in ("PAXGUSDT", "XAUUSDT", "XAUTUSDT"):
        f, m = vision_funding(sym)
        info[f"funding {sym}"] = f"{len(f)} prints {m[:1]}…{m[-1:]}, avg per 8h {np.mean([r * 8 / h for _, r, h in f]) * 100:.4f}%" if f else "none"
        if f:
            np.save(f"{OUT}/funding_{sym}.npy", np.array(f, dtype=float))
        k, m2 = vision_series("um", sym)
        if k is not None:
            np.save(f"{OUT}/perp_{sym}_1h.npy", k)
            info[f"perp {sym} 1h"] = f"{len(k)} bars {m2[:1]}…{m2[-1:]}, avg quote vol/h ${np.mean(k[-720:, 5]) / 1e6:.2f}M (last 30 d)"
            # perp vs spot gap (same hours)
            m_ = dict(zip(paxg[:, 0].astype(np.int64), paxg[:, 4]))
            g = np.array([c / m_[int(t)] - 1 for t, c in zip(k[:, 0], k[:, 4]) if int(t) in m_])
            if len(g):
                info[f"perp {sym} vs PAXG spot"] = f"median {np.median(g) * 100:.2f}%, 5-95% {np.percentile(g, 5) * 100:.2f}…{np.percentile(g, 95) * 100:.2f}%"
    note("gold data · binance", json.dumps(info, ensure_ascii=False))


def fetch_xau():
    """Dukascopy XAUUSD 1 h, newest month first, gently (it throttles), within a time budget."""
    t_start, budget = time.time(), 40 * 60
    jobs = []
    y, mo = TODAY.year, TODAY.month - 2                                  # the last full month (0-based month)
    if mo < 0:
        y, mo = y - 1, 11
    while (y, mo) >= (2015, 0):
        jobs.append((y, mo))
        mo -= 1
        if mo < 0:
            y, mo = y - 1, 11
    rows, ok, fail = [], 0, 0
    for k in range(0, len(jobs), 2):
        if time.time() - t_start > budget:
            break
        with ThreadPoolExecutor(2) as ex:
            got = list(ex.map(lambda a: duka_hours("XAUUSD", *a), jobs[k:k + 2]))
        for s_, rr in got:
            ok += s_ == 200
            fail += s_ != 200
            rows += rr
    d = TODAY.replace(day=1)
    while d < TODAY and time.time() - t_start < budget + 600:          # the current month: minutes → hours
        mins = duka_minutes_day("XAUUSD", d)
        hrs = {}
        for t, o, h, l, c, v in mins:
            kk = t - t % H
            if kk not in hrs:
                hrs[kk] = [kk, o, h, l, c, v]
            else:
                x = hrs[kk]
                x[2], x[3], x[4], x[5] = max(x[2], h), min(x[3], l), c, x[5] + v
        rows += [tuple(x) for x in hrs.values()]
        d += dt.timedelta(days=1)
    info = {"months ok": ok, "months failed": fail, "minutes": round((time.time() - t_start) / 60, 1)}
    xa = np.array(sorted(set(rows)))
    if len(xa):
        flat = (xa[:, 5] <= 0) & (xa[:, 2] == xa[:, 3])
        xa = xa[~flat]
        np.save(f"{OUT}/xau_1h.npy", xa)
        info["XAUUSD 1h"] = f"{len(xa)} bars {dt.datetime.fromtimestamp(xa[0, 0] / 1000, dt.timezone.utc):%Y-%m-%d} → {dt.datetime.fromtimestamp(xa[-1, 0] / 1000, dt.timezone.utc):%Y-%m-%d}"
        if os.path.exists(f"{OUT}/paxg_1h.npy"):
            paxg = np.load(f"{OUT}/paxg_1h.npy")
            m = dict(zip(xa[:, 0].astype(np.int64), xa[:, 4]))
            pr = np.array([p / m[int(t)] - 1 for t, p in zip(paxg[:, 0], paxg[:, 4]) if int(t) in m])
            if len(pr):
                med = np.median(pr)
                info["paxg vs xauusd"] = (f"common hours {len(pr)}, median premium {med * 100:.2f}%, gap>1% from median in "
                                          f"{(np.abs(pr - med) > 0.01).mean() * 100:.1f}% of hours, >3% in {(np.abs(pr - med) > 0.03).mean() * 100:.2f}%")
    note("gold data · dukascopy", json.dumps(info, ensure_ascii=False))


# ================================================================================================== engine
class Series:
    def __init__(self, a, name):
        self.name = name
        self.t = a[:, 0].astype(np.int64)
        self.o, self.h, self.l, self.c, self.v = (a[:, k].astype(float) for k in (1, 2, 3, 4, 5))
        self.n = len(self.t)
        tr = np.maximum(self.h - self.l, np.maximum(abs(self.h - np.r_[self.c[0], self.c[:-1]]), abs(self.l - np.r_[self.c[0], self.c[:-1]])))
        self.atr = ema(tr, 14)
        self.fund_h = np.full(self.n, FUND_DEFAULT_8H / 8)               # funding per hour (fraction, paid by longs)
        self.fund_src = "default 0.01% per 8h"
        self.dt = [dt.datetime.fromtimestamp(x / 1000, dt.timezone.utc) for x in self.t]

    def set_funding(self, prints):
        if not prints:
            return
        pt = np.array([p[0] for p in prints], dtype=np.int64)
        pr = np.array([p[1] / max(1, p[2]) for p in prints])            # per hour
        idx = np.searchsorted(pt, self.t, side="right") - 1
        ok = idx >= 0
        self.fund_h[ok] = pr[idx[ok]]
        self.fund_src = f"Binance perp real rate from {dt.datetime.utcfromtimestamp(pt[0] / 1000):%Y-%m}, before that 0.01% per 8h"


def ema(x, n):
    out = np.empty_like(x)
    a = 2 / (n + 1)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def sma(x, n):
    c = np.cumsum(np.r_[0.0, x])
    out = np.full(len(x), np.nan)
    out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def returns_from_trades(S, trades, spot=False):
    """trades: (i_in, px_in, fee_in, i_out, px_out, fee_out, side). → hourly net returns of a 1× account."""
    r = np.zeros(S.n)
    for i0, p0, f0, i1, p1, f1, sd in trades:
        if i1 == i0:
            r[i0] += sd * (p1 / p0 - 1) - f0 - f1
        else:
            r[i0] += sd * (S.c[i0] / p0 - 1) - f0
            if i1 > i0 + 1:
                r[i0 + 1:i1] += sd * (S.c[i0 + 1:i1] / S.c[i0:i1 - 1] - 1)
            r[i1] += sd * (p1 / S.c[i1 - 1] - 1) - f1
        if not spot:
            r[i0:i1 + 1] -= sd * S.fund_h[i0:i1 + 1]
    return r


def trade_rets(S, trades, spot=False):
    out = []
    for i0, p0, f0, i1, p1, f1, sd in trades:
        fund = 0.0 if spot else sd * float(S.fund_h[i0:i1 + 1].sum())
        out.append((S.t[i0], sd * (p1 / p0 - 1) - f0 - f1 - fund))
    return out


def walk(S, i, sd, entry, stop, target, t_max, fee_exit=FEE, target_from_next=False):
    """Hourly path from bar i (entered at `entry`); stop first inside a bar. → (i_out, px_out, fee_out)."""
    j = i
    while j < S.n:
        if S.t[j] >= t_max:
            return j, S.o[j], fee_exit
        if sd == 1:
            if S.l[j] <= stop:
                return j, min(stop, S.o[j]) if j > i else stop, FEE
            if target is not None and S.h[j] >= target and not (target_from_next and j == i):
                return j, max(target, S.o[j]) if j > i else target, FEE_LIMIT
        else:
            if S.h[j] >= stop:
                return j, max(stop, S.o[j]) if j > i else stop, FEE
            if target is not None and S.l[j] <= target and not (target_from_next and j == i):
                return j, min(target, S.o[j]) if j > i else target, FEE_LIMIT
        j += 1
    return S.n - 1, S.c[-1], FEE


# ---------------------------------------------------------------------------------------- families
def daily_index(S):
    """→ (day keys, index of the last hourly bar of each UTC day)."""
    day = S.t // DAY
    last = np.r_[np.where(np.diff(day) != 0)[0], S.n - 1]
    return day[last], last


def fam_trend(S):
    days, last = daily_index(S)
    dc = S.c[last]
    dh = np.array([S.h[a + 1:b + 1].max() if b > a else S.h[b] for a, b in zip(np.r_[-1, last[:-1]], last)])
    dl = np.array([S.l[a + 1:b + 1].min() if b > a else S.l[b] for a, b in zip(np.r_[-1, last[:-1]], last)])
    out = {}

    def from_daily(pos_d, name, spot=False):
        trades, cur, i0, p0 = [], 0, None, None
        for k in range(len(last) - 1):
            want = pos_d[k]
            i = last[k] + 1                                            # first bar of the next day
            if want != cur:
                if cur != 0:
                    trades.append((i0, p0, FEE_SPOT if spot else FEE, i, S.o[i], FEE_SPOT if spot else FEE, cur))
                if want != 0:
                    i0, p0 = i, S.o[i]
                cur = want
        if cur != 0:
            trades.append((i0, p0, FEE_SPOT if spot else FEE, S.n - 1, S.c[-1], 0.0, cur))
        out[name] = (trades, spot)

    for n in (20, 50, 100, 200):
        m = sma(dc, n)
        up = np.where(np.isnan(m), 0, np.where(dc > m, 1, -1))
        from_daily(np.where(up > 0, 1, 0), f"sma{n} long")
        from_daily(np.where(up > 0, 1, 0), f"sma{n} long spot", spot=True)
        from_daily(np.where(np.isnan(m), 0, up), f"sma{n} long-short")
    for n in (20, 55):
        for ls in (False, True):
            pos, cur = np.zeros(len(dc), dtype=int), 0
            for k in range(n, len(dc)):
                hi, lo = dh[k - n:k].max(), dl[k - n:k].min()
                hx, lx = dh[k - n // 2:k].max(), dl[k - n // 2:k].min()
                if cur == 0:
                    if dc[k] > hi:
                        cur = 1
                    elif ls and dc[k] < lo:
                        cur = -1
                elif cur == 1 and dc[k] < lx:
                    cur = -1 if (ls and dc[k] < lo) else 0
                elif cur == -1 and dc[k] > hx:
                    cur = 1 if dc[k] > hi else 0
                pos[k] = cur
            from_daily(pos, f"donchian{n} {'long-short' if ls else 'long'}")
            if not ls:
                from_daily(pos, f"donchian{n} long spot", spot=True)
    for L in (21, 63, 126, 252):
        mom = np.r_[np.full(L, np.nan), dc[L:] / dc[:-L] - 1]
        sgn = np.where(np.isnan(mom), 0, np.sign(mom)).astype(int)
        from_daily(np.where(sgn > 0, 1, 0), f"mom{L} long")
        from_daily(np.where(sgn > 0, 1, 0), f"mom{L} long spot", spot=True)
        from_daily(sgn, f"mom{L} long-short")
    return out


def bars4h(S):
    k = S.t // (4 * H)
    last = np.r_[np.where(np.diff(k) != 0)[0], S.n - 1]
    first = np.r_[0, last[:-1] + 1]
    h4 = np.array([S.h[a:b + 1].max() for a, b in zip(first, last)])
    l4 = np.array([S.l[a:b + 1].min() for a, b in zip(first, last)])
    c4 = S.c[last]
    o4 = S.o[first]
    return first, last, o4, h4, l4, c4


def fam_channel(S):
    first, last, o4, h4, l4, c4 = bars4h(S)
    tr = np.maximum(h4 - l4, np.maximum(abs(h4 - np.r_[c4[0], c4[:-1]]), abs(l4 - np.r_[c4[0], c4[:-1]])))
    atr4 = ema(tr, 14)
    out = {}
    for N in (60, 120):
        m = sma(c4, N)
        for tgt in ("mid", "edge"):
            trades, busy = [], -1
            for k in range(N, len(c4) - 1):
                i = last[k] + 1
                if i <= busy or i >= S.n:
                    continue
                hi, lo = h4[k - N:k].max(), l4[k - N:k].min()
                w = hi - lo
                if w <= 0 or np.isnan(m[k]):
                    continue
                if c4[k] > m[k] and l4[k] <= lo + 0.1 * w and c4[k] > lo:
                    sd, stop, target = 1, lo - atr4[k], (lo + hi) / 2 if tgt == "mid" else hi
                elif c4[k] < m[k] and h4[k] >= hi - 0.1 * w and c4[k] < hi:
                    sd, stop, target = -1, hi + atr4[k], (lo + hi) / 2 if tgt == "mid" else lo
                else:
                    continue
                e = S.o[i]
                if (target - e) * sd <= 0 or (e - stop) * sd <= 0:
                    continue
                j, px, f = walk(S, i, sd, e, stop, target, S.t[i] + 5 * DAY)
                trades.append((i, e, FEE, j, px, f, sd))
                busy = j
            out[f"bounce{N} to {tgt}"] = (trades, False)
    for N in (20, 50):
        for R in (1, 2):
            for sides in ("both", "long"):
                trades, busy = [], -1
                for k in range(N, len(c4) - 1):
                    i = last[k] + 1
                    if i <= busy or i >= S.n:
                        continue
                    lo, hi = l4[k - N:k].min(), h4[k - N:k].max()
                    if l4[k] < lo and c4[k] > lo:
                        sd, stop = 1, l4[k] - 0.1 * atr4[k]
                    elif sides == "both" and h4[k] > hi and c4[k] < hi:
                        sd, stop = -1, h4[k] + 0.1 * atr4[k]
                    else:
                        continue
                    e = S.o[i]
                    risk = (e - stop) * sd
                    if risk <= 0:
                        continue
                    j, px, f = walk(S, i, sd, e, stop, e + sd * R * risk, S.t[i] + 5 * DAY)
                    trades.append((i, e, FEE, j, px, f, sd))
                    busy = j
                out[f"false break {N} {R}R {sides}"] = (trades, False)
    return out


def fam_levels(S):
    out = {}
    sm200 = sma(S.c, 200)
    vavg = sma(S.v, 20)
    levels = [None] * S.n
    cur = []
    for i in range(720, S.n):
        if i % 4 == 0 or not cur:
            a = slice(i - 720, i)
            lo, hi = S.l[a].min(), S.h[a].max()
            if hi <= lo:
                cur = []
            else:
                tp = (S.h[a] + S.l[a] + S.c[a]) / 3
                b = np.clip(((tp - lo) / (hi - lo) * 60).astype(int), 0, 59)
                vol = np.bincount(b, weights=S.v[a], minlength=60)
                med = np.median(vol[vol > 0]) if (vol > 0).any() else 0
                pk = [q for q in range(60) if vol[q] >= 1.5 * med and vol[q] >= vol[max(0, q - 1)] and vol[q] >= vol[min(59, q + 1)]]
                cur = [lo + (q + 0.5) * (hi - lo) / 60 for q in pk]
        levels[i] = cur
    for k_atr in (0.5, 1.0):
        for volf in (False, True):
            for trend in (False, True):
                trades, busy = [], -1
                for i in range(720, S.n - 1):
                    if i + 1 <= busy or not levels[i]:
                        continue
                    if volf and not (S.v[i] >= 1.5 * vavg[i - 1]):
                        continue
                    L = levels[i]
                    sd = 0
                    below = [x for x in L if S.l[i] <= x < S.c[i] and S.o[i] > x]
                    above = [x for x in L if S.h[i] >= x > S.c[i] and S.o[i] < x]
                    if below and (not trend or S.c[i] > sm200[i]):
                        lv, sd = max(below), 1
                    elif above and (not trend or S.c[i] < sm200[i]):
                        lv, sd = min(above), -1
                    if not sd:
                        continue
                    e = S.o[i + 1]
                    stop = lv - sd * k_atr * S.atr[i]
                    risk = (e - stop) * sd
                    if risk <= 0:
                        continue
                    nxt = [x for x in L if (x - e) * sd > 0]
                    tg = (min(nxt) if sd == 1 else max(nxt)) if nxt else e + sd * 2 * risk
                    if (tg - e) * sd > 2 * risk:
                        tg = e + sd * 2 * risk
                    j, px, f = walk(S, i + 1, sd, e, stop, tg, S.t[i + 1] + 48 * H)
                    trades.append((i + 1, e, FEE, j, px, f, sd))
                    busy = j
                out[f"levels stop{k_atr}atr{' vol' if volf else ''}{' trend' if trend else ''}"] = (trades, False)
    return out


def fam_range(S):
    out = {}
    for Nd in (7, 14):
        n = Nd * 24
        for zone in (0.2, 0.3):
            for take in (0.5, 0.8):
                trades, busy = [], -1
                for i in range(n, S.n - 1):
                    if i + 1 <= busy:
                        continue
                    a = slice(i - n + 1, i + 1)
                    lo, hi = S.l[a].min(), S.h[a].max()
                    w = hi - lo
                    if w <= 0 or abs(S.c[i] - S.c[i - n + 1]) > 0.5 * w:       # not a flat range
                        continue
                    j = i + 1
                    buy, sell = lo + zone * w, hi - zone * w
                    if S.c[i] > buy and S.l[j] < buy * (1 - 0.0001):
                        sd, e, stop, tg = 1, buy, lo - 0.15 * w, lo + take * w
                    elif S.c[i] < sell and S.h[j] > sell * (1 + 0.0001):
                        sd, e, stop, tg = -1, sell, hi + 0.15 * w, hi - take * w
                    else:
                        continue
                    k, px, f = walk(S, j, sd, e, stop, tg, S.t[j] + 3 * DAY, target_from_next=True)
                    trades.append((j, e, FEE_LIMIT, k, px, f, sd))
                    busy = k
                out[f"range {Nd}d zone{zone} take{take}"] = (trades, False)
    return out


def local_hour(S, tz):
    return np.array([d.astimezone(tz).hour for d in S.dt]), np.array([d.astimezone(tz).date().toordinal() for d in S.dt])


def fam_sessions(S):
    out = {}
    wd = np.array([d.weekday() for d in S.dt])
    for city, tz in (("London", LDN), ("New York", NYC)):
        hr, day = local_hour(S, tz)
        for mode in ("follow", "fade"):
            for ex in ("4h", "close"):
                trades, busy = [], -1
                for i in range(1, S.n - 1):
                    if hr[i] != 8 or wd[i] >= 5 or i <= busy:
                        continue
                    H0, L0 = S.h[i], S.l[i]
                    w = H0 - L0
                    if w <= 0:
                        continue
                    j = i + 1
                    while j < S.n - 1 and day[j] == day[i] and hr[j] < 16:
                        sig = 1 if S.c[j] > H0 else (-1 if S.c[j] < L0 else 0)
                        if sig:
                            k = j + 1
                            sd = sig if mode == "follow" else -sig
                            e = S.o[k]
                            if mode == "follow":
                                stop, tg = (L0, None) if sd == 1 else (H0, None)
                            else:
                                stop = e + sd * -1 * w
                                tg = (H0 + L0) / 2
                                if (tg - e) * sd <= 0:
                                    break
                            if (e - stop) * sd <= 0:
                                break
                            end_ms = S.t[k] + 4 * H if ex == "4h" else int(dt.datetime.combine(dt.date.fromordinal(int(day[i])), dt.time(16), tz).timestamp() * 1000)
                            q, px, f = walk(S, k, sd, e, stop, tg, end_ms)
                            trades.append((k, e, FEE, q, px, f, sd))
                            busy = q
                            break
                        j += 1
                out[f"{city} open {mode} to {ex}"] = (trades, False)
    hr = np.array([d.hour for d in S.dt])
    dd = S.t // DAY
    for mode in ("follow", "fade"):
        trades, busy = [], -1
        for i in range(8, S.n - 1):
            if hr[i] != 7 or wd[i] >= 5 or i <= busy:
                continue
            a = [x for x in range(i - 7, i) if dd[x] == dd[i]]
            if len(a) < 5:
                continue
            H0, L0 = S.h[a].max(), S.l[a].min()
            w = H0 - L0
            if w <= 0:
                continue
            j = i
            while j < S.n - 1 and dd[j] == dd[i] and hr[j] < 12:
                sig = 1 if S.c[j] > H0 else (-1 if S.c[j] < L0 else 0)
                if sig:
                    k = j + 1
                    sd = sig if mode == "follow" else -sig
                    e = S.o[k]
                    if mode == "follow":
                        stop, tg = (H0 + L0) / 2, e + sig * w
                    else:
                        stop, tg = e - sd * 0.5 * w, (H0 + L0) / 2
                    if (e - stop) * sd <= 0 or (tg - e) * sd <= 0:
                        break
                    q, px, f = walk(S, k, sd, e, stop, tg, int(dd[i]) * DAY + 16 * H)
                    trades.append((k, e, FEE, q, px, f, sd))
                    busy = q
                    break
                j += 1
        out[f"Asia range in London {mode}"] = (trades, False)
    return out


def fam_hours(S):
    out = {}
    hr = np.array([d.hour for d in S.dt])
    wd = np.array([d.weekday() for d in S.dt])
    for h in range(24):
        idx = np.where((hr == h) & (wd < 5))[0]
        for hold in (1, 4):
            for sd in (1, -1):
                trades = []
                for i in idx:
                    k = np.searchsorted(S.t, S.t[i] + hold * H)
                    if k >= S.n:
                        break
                    trades.append((i, S.o[i], FEE, k, S.o[k], FEE, sd))
                out[f"hour {h:02d} UTC hold {hold}h {'long' if sd == 1 else 'short'}"] = (trades, False)
    return out


# ---------------------------------------------------------------------------------------- metrics
def stats(S, r, tr, t0, t1):
    m = (S.t >= t0) & (S.t < t1)
    if not m.any():
        return None
    eq = np.cumprod(1 + r[m])
    peak = np.maximum.accumulate(np.r_[1.0, eq])[1:]
    dd = float((eq / peak - 1).min())
    yrs = (S.t[m][-1] - S.t[m][0]) / (365.25 * DAY)
    days = S.t[m] // DAY
    dsum = np.bincount(days - days[0], weights=r[m])
    dsum = dsum[np.bincount(days - days[0]) > 0]
    sharpe = float(dsum.mean() / dsum.std() * np.sqrt(365)) if dsum.std() > 0 else 0.0
    x = [v for t, v in tr if t0 <= t < t1]
    total = float(eq[-1] - 1)
    gains = sorted([v for v in x if v > 0], reverse=True)
    net = sum(x)
    return {"ret": round(total * 100, 1), "cagr": round(((1 + total) ** (1 / max(yrs, 0.1)) - 1) * 100, 1), "dd": round(dd * 100, 1),
            "sh": round(sharpe, 2), "n": len(x), "win": round(100 * np.mean([v > 0 for v in x]), 0) if x else None,
            "avg": round(100 * float(np.mean(x)), 3) if x else None, "per_mo": round(len(x) / max(yrs * 12, 0.1), 1),
            "top2": round(100 * sum(gains[:2]) / net, 0) if net > 0 and gains else None}


def years_of(S, r):
    y = np.array([d.year for d in S.dt])
    return {int(k): round((float(np.prod(1 + r[y == k])) - 1) * 100, 1) for k in sorted(set(y))}


def evaluate(S, fams):
    t_beg, t_end = int(S.t[0]), int(S.t[-1]) + 1
    bh_r = np.r_[0.0, S.c[1:] / S.c[:-1] - 1]
    bh = {"train": stats(S, bh_r, [], t_beg, TEST0), "test": stats(S, bh_r, [], TEST0, t_end), "all": stats(S, bh_r, [], t_beg, t_end),
          "years": years_of(S, bh_r)}
    note(f"gold {S.name} · buy and hold", json.dumps(bh, ensure_ascii=False))
    summary = {}
    for fname, fn in fams:
        V = fn(S)
        rows = []
        for vname, (trades, spot) in V.items():
            r = returns_from_trades(S, trades, spot)
            tr = trade_rets(S, trades, spot)
            a, b = stats(S, r, tr, t_beg, TEST0), stats(S, r, tr, TEST0, t_end)
            if not a or not b:
                continue
            rows.append((vname, a, b, r, tr, trades, spot))
        ok = [x for x in rows if x[1]["n"] >= 20]
        if not ok:
            note(f"gold {S.name} · {fname}", "no variant with 20+ trades in train")
            continue
        best = max(ok, key=lambda x: x[1]["sh"])
        tests = [x[2]["ret"] for x in rows]
        yr = years_of(S, best[3])
        al = stats(S, best[3], best[4], t_beg, t_end)
        train_years = [v for k, v in yr.items() if 2021 <= k <= 2024]
        res = {"variants": len(rows), "test_pos": sum(1 for v in tests if v > 0), "test_median": round(float(np.median(tests)), 1),
               "test_best": max(rows, key=lambda x: x[2]["ret"])[0] + f" {max(tests)}",
               "chosen": best[0], "train": best[1], "test": best[2], "all": al, "years": yr,
               "train_years_pos": f"{sum(1 for v in train_years if v > 0)}/{len(train_years)}",
               "top_train5": [(x[0], x[1]["sh"], x[2]["ret"]) for x in sorted(ok, key=lambda x: -x[1]["sh"])[:5]]}
        crit = {"test>0": bool(best[2]["ret"] > 0), "train years 3/4": bool(sum(1 for v in train_years if v > 0) >= 3),
                "dd>-20": bool(al["dd"] > -20), "top2<50": bool((al["top2"] or 999) < 50),
                "beats B&H test": bool(best[2]["ret"] > bh["test"]["ret"] or (best[2]["sh"] > bh["test"]["sh"] and best[2]["dd"] > bh["test"]["dd"] / 2))}
        res["criteria"] = crit
        half = [(i0, p0, f0 / 2, i1, p1, f1 / 2, sd) for i0, p0, f0, i1, p1, f1, sd in best[5]]
        rh = returns_from_trades(S, half, best[6])
        res["half_fees_test"] = stats(S, rh, trade_rets(S, half, best[6]), TEST0, t_end)["ret"]
        res["half_fees_years"] = years_of(S, rh)
        summary[fname] = {"chosen": best[0], "test": best[2]["ret"], "test_sh": best[2]["sh"], "pass": all(v for k, v in crit.items() if k != "beats B&H test"),
                          "beats_bh": crit["beats B&H test"]}
        note(f"gold {S.name} · {fname}", json.dumps(res, ensure_ascii=False))
    note(f"gold {S.name} · summary", json.dumps({"fund": S.fund_src, "bars": S.n, "from": f"{S.dt[0]:%Y-%m-%d}", "to": f"{S.dt[-1]:%Y-%m-%d}",
                                                  "families": summary}, ensure_ascii=False))


FAMS = [("trend", fam_trend), ("channel", fam_channel), ("levels", fam_levels), ("range", fam_range), ("sessions", fam_sessions),
        ("hours", fam_hours)]


def run(which):
    f = f"{OUT}/{'paxg' if which == 'paxg' else 'xau'}_1h.npy"
    if not os.path.exists(f):
        note(f"gold {which}", "no data file — the fetch step failed", "warning")
        return
    a = np.load(f)
    S = Series(a, which)
    if os.path.exists(f"{OUT}/funding_PAXGUSDT.npy"):
        S.set_funding([tuple(x) for x in np.load(f"{OUT}/funding_PAXGUSDT.npy")])
    evaluate(S, FAMS)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "probe"
    os.makedirs(OUT, exist_ok=True)
    if mode == "run":
        run(sys.argv[2])
    else:
        {"probe": probe, "fetch_paxg": fetch_paxg, "fetch_xau": fetch_xau}[mode]()
