"""Metals, indices, commodities and stocks as USDT perpetuals on Binance (backlog #3, 21.10.2026): can the autolab portfolio
«Мультирынок · тренд» be traded on the same venue as the crypto desks? Paper / history only, docs/research/tradfi_perps.md.
Runs in GitHub Actions only (.github/workflows/research-tradfi.yml). Public data only: no keys, no orders.

Steps (one run, all fixed in advance — nothing is fitted, 0 rule variants tried):
  1. Discover every TradFi-looking USDT perp in the Binance archive (data.binance.vision): metals, energy, US indices / ETFs,
     single stocks. Optional live probe of fapi exchangeInfo / premiumIndex (often 451 from US runners — recorded, not needed).
  2. Per perp: first month, days of history, 30-day median daily quote volume, funding (whole history and last 90 days,
     annualised, share of prints where longs pay), weekend share of volume and the weekend move.
  3. Tracking: daily close-to-close returns of the perp vs its underlying (Yahoo daily via yfinance) on common days —
     correlation and tracking error. Correlation < 0.6 → «does not track» (e.g. SPXUSDT is the SPX6900 meme coin).
  4. Cost of «Мультирынок · тренд» on perps: the autolab portfolio (average of 4 momentum signals 21/63/126/252 days, long or
     short, equal risk 15 % vol target, ≤ 2× per market) on Yahoo history of the members that have a tracking perp, last 5
     years. Net result three ways: autolab cost (0.03–0.05 % per unit change), perp cost (0.05 % taker + slippage by
     liquidity tier, per unit change) and perp cost + funding (each market's own average annual funding: longs pay, shorts
     receive). Halves of the window shown separately.
Stdlib + numpy (+ yfinance for step 3–4)."""
import datetime as dt
import io
import json
import math
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np

UA = {"User-Agent": "Mozilla/5.0 cryptonova-research/1.0", "Accept": "*/*"}
VISION = "https://data.binance.vision/data"
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
TODAY = dt.datetime.now(dt.timezone.utc).date()
DAY = 86_400_000
# perp base → (class, Yahoo underlying, name)
UNDER = {
    "XAU": ("metal", "GC=F", "Золото"), "XAG": ("metal", "SI=F", "Серебро"), "XPT": ("metal", "PL=F", "Платина"),
    "XPD": ("metal", "PA=F", "Палладий"), "HG": ("metal", "HG=F", "Медь"), "COPPER": ("metal", "HG=F", "Медь"),
    "CL": ("energy", "CL=F", "Нефть WTI"), "WTI": ("energy", "CL=F", "Нефть WTI"), "BZ": ("energy", "BZ=F", "Нефть Brent"),
    "BRENT": ("energy", "BZ=F", "Нефть Brent"), "NG": ("energy", "NG=F", "Газ"), "NATGAS": ("energy", "NG=F", "Газ"),
    "SPX": ("index", "^GSPC", "S&P 500"), "SPY": ("index", "SPY", "S&P 500 ETF"), "US500": ("index", "^GSPC", "S&P 500"),
    "NDX": ("index", "^NDX", "Nasdaq 100"), "QQQ": ("index", "QQQ", "Nasdaq 100 ETF"), "US100": ("index", "^NDX", "Nasdaq 100"),
    "DJI": ("index", "^DJI", "Dow Jones"), "US30": ("index", "^DJI", "Dow Jones"), "IWM": ("index", "IWM", "Russell 2000 ETF"),
    "EWY": ("index", "EWY", "Korea ETF"), "EWJ": ("index", "EWJ", "Japan ETF"),
    "TSLA": ("stock", "TSLA", "Tesla"), "NVDA": ("stock", "NVDA", "Nvidia"), "AAPL": ("stock", "AAPL", "Apple"),
    "AMZN": ("stock", "AMZN", "Amazon"), "GOOGL": ("stock", "GOOGL", "Alphabet"), "META": ("stock", "META", "Meta"),
    "MSFT": ("stock", "MSFT", "Microsoft"), "MSTR": ("stock", "MSTR", "Strategy"), "COIN": ("stock", "COIN", "Coinbase"),
    "HOOD": ("stock", "HOOD", "Robinhood"), "CRCL": ("stock", "CRCL", "Circle"), "INTC": ("stock", "INTC", "Intel"),
    "AMD": ("stock", "AMD", "AMD"), "PLTR": ("stock", "PLTR", "Palantir"), "NFLX": ("stock", "NFLX", "Netflix"),
    "BABA": ("stock", "BABA", "Alibaba"), "ORCL": ("stock", "ORCL", "Oracle"), "AVGO": ("stock", "AVGO", "Broadcom"),
    "MU": ("stock", "MU", "Micron"), "TSM": ("stock", "TSM", "TSMC"), "GME": ("stock", "GME", "GameStop"),
}
TAKER = 0.0005
SLIP = {"A": 0.0001, "B": 0.0003, "C": 0.001}            # ≥ $50M a day / ≥ $5M / below


def note(title, msg, level="notice"):
    print(f"{title}: {msg}", flush=True)
    m = str(msg).replace("%", "%25").replace("\r", "").replace("\n", "%0A")[:4000]
    print(f"::{level} title={title}::{m}", flush=True)


def get(url, tries=3, raw=False, timeout=40):
    err = None
    for a in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
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


def s3_list(prefix, delim=True):
    """Keys (or common prefixes) under data/<prefix>, all pages."""
    out, marker = [], ""
    for _ in range(40):
        url = f"{S3}?prefix=data/{prefix}" + ("&delimiter=/" if delim else "") + (f"&marker={marker}" if marker else "")
        s, b = get(url, raw=True)
        if s != 200:
            break
        tag = rb"<Prefix>([^<]+)</Prefix>" if delim else rb"<Key>([^<]+)</Key>"
        got = [x.decode() for x in re.findall(tag, b)]
        got = [g for g in got if g != f"data/{prefix}"]
        out += got
        if b"<IsTruncated>true</IsTruncated>" not in b or not got:
            break
        marker = got[-1]
    return out


def months(pref):
    keys = s3_list(pref, delim=False)
    return sorted(set(m for k in keys for m in re.findall(r"-(\d{4}-\d{2})\.zip$", k)))


def daily_klines(sym):
    ms_ = months(f"futures/um/monthly/klines/{sym}/1d/")
    urls = [f"{VISION}/futures/um/monthly/klines/{sym}/1d/{sym}-1d-{m}.zip" for m in ms_]
    d = (dt.date.fromisoformat(ms_[-1] + "-01") + dt.timedelta(days=32)).replace(day=1) if ms_ else TODAY - dt.timedelta(days=60)
    while d < TODAY:
        urls.append(f"{VISION}/futures/um/daily/klines/{sym}/1d/{sym}-1d-{d:%Y-%m-%d}.zip")
        d += dt.timedelta(days=1)
    with ThreadPoolExecutor(8) as ex:
        got = list(ex.map(lambda u: get(u, raw=True, tries=2), urls))
    rows = {}
    for s, b in got:
        if s == 200:
            for r in zip_rows(b):
                rows[int(r[0])] = (float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7]))
    return sorted(rows.items()), ms_


def hourly_last(sym, days=56):
    urls = [f"{VISION}/futures/um/daily/klines/{sym}/1h/{sym}-1h-{TODAY - dt.timedelta(days=i):%Y-%m-%d}.zip" for i in range(1, days + 1)]
    with ThreadPoolExecutor(8) as ex:
        got = list(ex.map(lambda u: get(u, raw=True, tries=2), urls))
    rows = {}
    for s, b in got:
        if s == 200:
            for r in zip_rows(b):
                rows[int(r[0])] = (float(r[4]), float(r[7]))
    return sorted(rows.items())


def funding(sym):
    ms_ = months(f"futures/um/monthly/fundingRate/{sym}/")
    rows = []
    for m in ms_:
        s, b = get(f"{VISION}/futures/um/monthly/fundingRate/{sym}/{sym}-fundingRate-{m}.zip", raw=True)
        if s == 200:
            rows += [(int(r[0]), float(r[2])) for r in zip_rows(b)]
    return sorted(rows)


def ann_funding(f, since=None):
    f = [x for x in f if since is None or x[0] >= since]
    if len(f) < 3:
        return None, None
    span = (f[-1][0] - f[0][0]) / (365 * DAY)
    return (sum(r for _, r in f) / span if span > 0.02 else None), float(np.mean([r > 0 for _, r in f]))


def yahoo_daily(tk):
    try:
        import yfinance as yf
        df = yf.Ticker(tk).history(period="max", interval="1d", auto_adjust=True)
        return {ts.date().isoformat(): float(c) for ts, c in zip(df.index, df["Close"]) if c and c > 0}
    except Exception as e:  # noqa: BLE001
        print("yahoo", tk, e)
        return {}


# ------------------------------------------------------------------------------------------------------------------------
def discover():
    pre = s3_list("futures/um/monthly/fundingRate/") + s3_list("futures/um/daily/klines/")
    syms = sorted(set(p.rstrip("/").split("/")[-1] for p in pre))
    hits = [s for s in syms if s.endswith("USDT") and s[:-4] in UNDER]
    note("tradfi · discover", f"{len(syms)} UM symbols in archive; TradFi candidates: {', '.join(hits)}")
    live = {}
    for name, url in (("exchangeInfo", "https://fapi.binance.com/fapi/v1/exchangeInfo"),
                      ("premiumIndex", "https://fapi.binance.com/fapi/v1/premiumIndex")):
        s, b = get(url, tries=1)
        live[name] = s
        if s == 200 and name == "exchangeInfo":
            tf = [(x["symbol"], x.get("underlyingType"), x.get("contractType")) for x in b.get("symbols", [])
                  if x.get("underlyingType") not in (None, "COIN") or x["symbol"][:-4] in UNDER]
            note("tradfi · exchangeInfo non-coin", json.dumps(tf[:120]))
            hits = sorted(set(hits) | {s_ for s_, u, _ in tf if s_.endswith("USDT") and s_[:-4] in UNDER})
    note("tradfi · live api", json.dumps(live))
    return hits


def profile(sym):
    k, ms_ = daily_klines(sym)
    f = funding(sym)
    h = hourly_last(sym)
    base = sym[:-4]
    cls, tk, name = UNDER[base]
    out = {"sym": sym, "name": name, "class": cls, "first": ms_[0] if ms_ else (dt.datetime.fromtimestamp(k[0][0] / 1000, dt.timezone.utc).date().isoformat() if k else None),
           "days": len(k)}
    if not k:
        return out, None
    vol = [v[4] for _, v in k[-30:]]
    out["vol30_med_musd"] = round(float(np.median(vol)) / 1e6, 1)
    out["tier"] = "A" if out["vol30_med_musd"] >= 50 else "B" if out["vol30_med_musd"] >= 5 else "C"
    a_all, pos_all = ann_funding(f)
    a90, _ = ann_funding(f, since=k[-1][0] - 90 * DAY)
    out["fund_ann_all_pct"] = None if a_all is None else round(a_all * 100, 2)
    out["fund_ann_90d_pct"] = None if a90 is None else round(a90 * 100, 2)
    out["fund_longs_pay_share"] = None if pos_all is None else round(pos_all, 2)
    if h:
        wd = [dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).weekday() for t, _ in h]
        qv = np.array([x[1] for _, x in h])
        we = np.array([d >= 5 for d in wd])
        out["weekend_vol_share"] = round(float(qv[we].sum() / qv.sum()), 3) if qv.sum() else None   # 2/7 = 0.286 if flat
        # weekend move: Fri 21:00 UTC → Sun 22:00 UTC close
        px = dict(h)
        mv = []
        for t, _ in h:
            d = dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc)
            if d.weekday() == 4 and d.hour == 21:
                t2 = t + (49 * 3_600_000)
                if t2 in px:
                    mv.append(abs(px[t2][0] / px[t][0] - 1))
        out["weekend_abs_move_pct"] = round(float(np.mean(mv)) * 100, 2) if mv else None
    # tracking vs underlying
    y = yahoo_daily(tk)
    perp = {dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).date().isoformat(): v[3] for t, v in k}
    common = sorted(set(perp) & set(y))
    if len(common) > 25:
        p = np.array([perp[d] for d in common])
        u = np.array([y[d] for d in common])
        rp, ru = np.diff(np.log(p)), np.diff(np.log(u))
        out["corr_daily"] = round(float(np.corrcoef(rp, ru)[0, 1]), 2)
        out["track_err_ann_pct"] = round(float(np.std(rp - ru) * math.sqrt(252)) * 100, 1)
        out["level_ratio_med"] = round(float(np.median(p / u)), 3)
    out["tracks"] = bool(out.get("corr_daily") is not None and out["corr_daily"] >= 0.6)
    return out, y


def signal(c):
    """autolab «Мультирынок · тренд»: average of sign(momentum 21/63/126/252) in [−1, 1]; vol-target 15 %, ≤ 2×."""
    n = len(c)
    s = np.zeros(n)
    for L in (21, 63, 126, 252):
        m = np.full(n, np.nan)
        m[L:] = c[L:] / c[:-L] - 1
        s += np.nan_to_num(np.sign(m))
    s /= 4
    r = np.zeros(n)
    r[1:] = np.diff(np.log(c))
    vol = np.full(n, np.nan)
    for i in range(63, n):
        vol[i] = np.std(r[i - 63:i]) * math.sqrt(252)
    w = np.where(np.isnan(vol) | (vol <= 0), 0.0, np.clip(s * 0.15 / np.where(vol > 0, vol, 1), -2, 2))
    w[:252] = 0
    return w, r


def portfolio(members, start, cost_kind):
    """members: [(key, closes-by-date dict, cost per unit change, funding/yr)] → (dates, daily returns)."""
    days = sorted(set().union(*[set(d) for _, d, _, _ in members]))
    days = [d for d in days if d >= start]
    R = []
    for key, dd, c_unit, fund in members:
        ser = sorted(dd.items())
        dts = [d for d, _ in ser]
        c = np.array([v for _, v in ser])
        w, r = signal(c)
        net = np.zeros(len(c))
        net[1:] = w[:-1] * r[1:]
        if cost_kind != "none":
            net[1:] -= np.abs(np.diff(w)) * c_unit
        if cost_kind == "fund":
            gap = np.diff(np.array([dt.date.fromisoformat(x).toordinal() for x in dts]), prepend=0) / 365.0
            net[1:] -= w[:-1] * fund * gap[1:]        # longs pay positive funding, shorts receive (calendar days)
        m = dict(zip(dts, net))
        R.append([m.get(d, 0.0) for d in days])
    R = np.array(R)
    return days, R.mean(axis=0) if len(R) else np.zeros(len(days))


def stats(days, r):
    if len(r) < 50:
        return None
    eq = np.cumsum(r)
    yrs = (dt.date.fromisoformat(days[-1]) - dt.date.fromisoformat(days[0])).days / 365.25
    ann = float(np.exp(eq[-1] / yrs) - 1) if yrs > 0 else 0
    sh = float(np.mean(r) / np.std(r) * math.sqrt(252)) if np.std(r) > 0 else 0
    dd = float(np.min(np.exp(eq - np.maximum.accumulate(eq)) - 1))
    return {"ann_pct": round(ann * 100, 2), "sharpe": round(sh, 2), "maxdd_pct": round(dd * 100, 1), "years": round(yrs, 1)}


def main():
    hits = discover()
    with ThreadPoolExecutor(4) as ex:
        res = list(ex.map(profile, hits))
    rows = [r for r, _ in res]
    for r in rows:
        note(f"tradfi · {r['sym']}", json.dumps(r, ensure_ascii=False))
    # Multirynok on perps: one tracking perp per underlying ticker (the most liquid)
    best = {}
    for (r, y) in res:
        if not r.get("tracks") or r["class"] == "stock" or not y:
            continue
        tk = UNDER[r["sym"][:-4]][1]
        if tk not in best or r.get("vol30_med_musd", 0) > best[tk][0].get("vol30_med_musd", 0):
            best[tk] = (r, y)
    if not best:
        note("tradfi · multirynok", "no tracking non-stock perp found", "warning")
        return
    start = (TODAY - dt.timedelta(days=int(5 * 365.25 + 400))).isoformat()          # 252-day warm-up + 5 years
    acost = {"metal": 0.0005, "energy": 0.0005, "index": 0.0003}
    out = {}
    for kind in ("none", "autolab", "perp", "fund"):
        mem = []
        for tk, (r, y) in best.items():
            cu = acost.get(r["class"], 0.0005) if kind == "autolab" else TAKER + SLIP[r.get("tier", "C")]
            fd = (r.get("fund_ann_all_pct") or 0) / 100
            mem.append((tk, {d: v for d, v in y.items() if d >= start}, cu, fd))
        days, pr = portfolio(mem, start, "none" if kind == "none" else ("fund" if kind == "fund" else "cost"))
        cut = 252
        days, pr = days[cut:], pr[cut:]
        h = len(days) // 2
        out[kind] = {"all": stats(days, pr), "half1": stats(days[:h], pr[:h]), "half2": stats(days[h:], pr[h:])}
    note("tradfi · multirynok on perps", json.dumps({"members": {tk: r["sym"] for tk, (r, _) in best.items()}, **out}, ensure_ascii=False))
    # turnover per year (unit changes) — what the costs are made of
    tv = {}
    for tk, (r, y) in best.items():
        c = np.array([v for d, v in sorted(y.items()) if d >= start])
        w, _ = signal(c)
        tv[r["sym"]] = {"turnover_per_yr": round(float(np.abs(np.diff(w[252:])).sum() / max(1, (len(w) - 252) / 252)), 1),
                        "avg_long_share": round(float(np.mean(w[252:] > 0)), 2), "fund_ann_pct": r.get("fund_ann_all_pct")}
    note("tradfi · turnover & funding by member", json.dumps(tv, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
