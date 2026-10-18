"""«Прогнозный движок» — which public factors really predict BTC, tested blind (18.10.2026; owner: «всё цифровое — история,
медиа, новости, страх и жадность, психология толпы, причинно-следственные связи — можно сделать систему предсказаний»).
Runs in GitHub Actions only (.github/workflows/research-forecast.yml): the research container cannot reach these sources.

Factors, one row per UTC day (each known at that day's close; sources published later are lagged one day):
  price      — Binance spot BTCUSDT daily: returns 1/7/30/90 d, distance from SMA50/200, 30-day volatility, volume z-score,
               taker-buy share (who hits the market: buyers or sellers — the crowd's aggression);
  futures    — Binance USDT-M: funding (7-day mean, z-score), from 2019-09; open interest change, top-trader and crowd
               long/short ratios, taker long/short volume ratio (data.binance.vision «metrics», from 2021-12);
  sentiment  — Fear & Greed index (alternative.me, from 2018-02): level and 7-day change;
  attention  — English Wikipedia «Bitcoin» page views (Wikimedia API, lag 1): z-score vs 90 days, 7-day change;
  news       — GDELT world-news tone and volume for «bitcoin» (lag 1) — if the free API gives history;
  money      — total stablecoin supply (DefiLlama): 7/30-day change; US spot BTC ETF net flows (Farside, from 2024-01, lag 1)
               — if the page can be read;
  macro      — S&P 500, broad dollar index, US 10-year yield (FRED): 20-day changes.

Tests:
  1. Each factor alone: rank correlation (IC) of the factor today with the BTC return over the next 7 and 30 days, per year —
     a factor is «consistent» if its IC has the same sign in at least 5 of the 6 years 2021–2026.
  2. Blind models (walk-forward): refit on the first day of every month on all earlier data (rows whose future is already known),
     predict every day of that month; horizons 1, 7, 30 days; logistic regression and gradient boosting. Out-of-sample 2021 →
     today. Scored per year by hit rate against «всегда вверх» and «как последние 30 дней» (momentum), and by AUC.
  3. Trading: exposure 70 % when the 7-day model says up, else 20 % (the same band as our BTC regime), costs 0.1 % per
     change, cash at 4 %/yr; against our regime (BTC > SMA200 and up over 60 days), fixed 40 % and buy-and-hold.
Pass mark (CRYPTO NOVA rule): ≥ 5 points over both baselines in 2025 AND in 2026 separately, and the trading rule beats the
regime after costs. Output: annotations (≤ 10 per step) + job summary. Needs: numpy, pandas, scikit-learn."""
import datetime as dt
import io
import json
import os
import re
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from fetch_alt100 import http, months_between, note  # noqa: E402

SPOT = "https://data.binance.vision/data/spot"
UM = "https://data.binance.vision/data/futures/um"
TODAY = dt.datetime.now(dt.timezone.utc).date()
COST, CASH = 0.001, 0.04
YEARS = ["2021", "2022", "2023", "2024", "2025", "2026"]


def _zip_rows(b: bytes) -> list:
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        rows = [ln.split(",") for ln in z.read(z.namelist()[0]).decode().splitlines()]
    return [r for r in rows if r and r[0][:1].isdigit()]


def _day(ts) -> pd.Timestamp:
    t = int(float(ts))
    t = t // 1000 if t > 10 ** 14 else t          # spot klines switched to microseconds in 2025
    t = t * 1000 if t < 10 ** 11 else t           # seconds → ms
    return pd.Timestamp(t, unit="ms").normalize()


def _monthly_then_daily(url_m, url_d, start: str) -> list:
    last_full = (TODAY.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
    rows = []
    with ThreadPoolExecutor(12) as ex:
        for s, b in ex.map(lambda m: http(url_m(m), binary=True), months_between(start, last_full)):
            if s == 200:
                rows += _zip_rows(b)
        days = [TODAY.replace(day=1) + dt.timedelta(days=i) for i in range((TODAY - TODAY.replace(day=1)).days)] if url_d else []
        for s, b in ex.map(lambda d: http(url_d(d), binary=True), days):
            if s == 200:
                rows += _zip_rows(b)
    return rows


def price() -> pd.DataFrame:
    rows = _monthly_then_daily(lambda m: f"{SPOT}/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-{m}.zip",
                               lambda d: f"{SPOT}/daily/klines/BTCUSDT/1d/BTCUSDT-1d-{d:%Y-%m-%d}.zip", "2017-08")
    df = pd.DataFrame({"day": [_day(r[0]) for r in rows], "close": [float(r[4]) for r in rows],
                       "vol": [float(r[5]) for r in rows], "taker": [float(r[9]) / max(float(r[5]), 1e-9) for r in rows]})
    return df.drop_duplicates("day").set_index("day").sort_index()


def funding() -> pd.Series:
    rows = _monthly_then_daily(lambda m: f"{UM}/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-{m}.zip",
                               None, "2019-09")
    s = pd.Series([float(r[2]) for r in rows], index=[_day(r[0]) for r in rows])
    return s.groupby(level=0).sum().sort_index()


def metrics() -> pd.DataFrame:
    days = pd.date_range("2021-12-01", pd.Timestamp(TODAY) - pd.Timedelta(days=1))

    def one(d):
        s, b = http(f"{UM}/daily/metrics/BTCUSDT/BTCUSDT-metrics-{d:%Y-%m-%d}.zip", binary=True)
        if s != 200:
            return None
        with zipfile.ZipFile(io.BytesIO(b)) as z:
            lines = z.read(z.namelist()[0]).decode().splitlines()
        rows = [ln.split(",") for ln in lines[1:] if ln.count(",") >= 7]
        if not rows:
            return None

        def num(i):
            v = [float(r[i]) for r in rows if r[i] not in ("", "nan")]
            return float(np.mean(v)) if v else np.nan
        try:
            return d, float(rows[-1][3]), num(5), num(6), num(7)
        except ValueError:
            return None
    with ThreadPoolExecutor(24) as ex:
        out = [x for x in ex.map(one, days) if x]
    return pd.DataFrame(out, columns=["day", "oi_usd", "ls_top", "ls_crowd", "taker_ls"]).set_index("day").sort_index()


def fear_greed() -> pd.Series:
    s, b = http("https://api.alternative.me/fng/?limit=0&format=json")
    d = json.loads(b)["data"] if s == 200 else []
    return pd.Series([float(x["value"]) for x in d], index=[_day(x["timestamp"]) for x in d]).sort_index()


def stablecoins() -> pd.Series:
    s, b = http("https://stablecoins.llama.fi/stablecoincharts/all")
    d = json.loads(b) if s == 200 else []
    v = [((x.get("totalCirculatingUSD") or {}).get("peggedUSD") or (x.get("totalCirculating") or {}).get("peggedUSD")) for x in d]
    return pd.Series(v, index=[_day(x["date"]) for x in d], dtype=float).dropna().sort_index()


def fred(sid: str) -> pd.Series:
    s, b = http(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}")
    if s != 200:
        return pd.Series(dtype=float)
    df = pd.read_csv(io.StringIO(b), na_values=["."])
    return pd.Series(df.iloc[:, 1].values, index=pd.to_datetime(df.iloc[:, 0]), dtype=float).dropna()


def wiki() -> pd.Series:
    url = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/Bitcoin/daily/"
           f"20160101/{TODAY:%Y%m%d}")
    import urllib.request
    try:     # Wikimedia asks for a descriptive User-Agent
        req = urllib.request.Request(url, headers={"User-Agent": "cnq-research/1.0 (https://cryptonova.vip; research script)"})
        with urllib.request.urlopen(req, timeout=60) as r:
            items = json.loads(r.read().decode()).get("items", [])
    except Exception:  # noqa: BLE001
        items = []
    return pd.Series([float(x["views"]) for x in items], index=[pd.Timestamp(x["timestamp"][:8]) for x in items]).sort_index()


def gdelt() -> pd.DataFrame:
    out = {}
    for mode in ("timelinetone", "timelinevolraw"):
        url = (f"https://api.gdeltproject.org/api/v2/doc/doc?query=bitcoin&mode={mode}&format=json&timelinesmooth=0"
               f"&startdatetime=20170101000000&enddatetime={TODAY:%Y%m%d}000000")
        s, b = http(url)
        try:
            tl = json.loads(b)["timeline"][0]["data"] if s == 200 else []
        except Exception:  # noqa: BLE001
            tl = []
        out[mode] = pd.Series([float(x["value"]) for x in tl], index=[pd.Timestamp(x["date"][:8]) for x in tl], dtype=float)
    return pd.DataFrame({"news_tone": out["timelinetone"], "news_vol": out["timelinevolraw"]})


def etf_flows() -> pd.Series:
    s, b = http("https://farside.co.uk/bitcoin-etf-flow-all-data/")
    if s != 200 or not b:
        return pd.Series(dtype=float)
    out = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", b, re.S):
        cells = [re.sub(r"<[^>]+>|&nbsp;", "", c).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        if len(cells) < 3:
            continue
        try:
            day = pd.Timestamp(dt.datetime.strptime(cells[0], "%d %b %Y"))
        except ValueError:
            continue
        v = cells[-1].replace(",", "")
        neg = v.startswith("(") and v.endswith(")")
        v = v.strip("()")
        try:
            out[day] = -float(v) if neg else float(v)
        except ValueError:
            pass
    return pd.Series(out, dtype=float).sort_index()


def zs(x: pd.Series, n: int = 90) -> pd.Series:
    return (x - x.rolling(n, min_periods=n // 2).mean()) / x.rolling(n, min_periods=n // 2).std()


def build() -> tuple[pd.DataFrame, dict]:
    with ThreadPoolExecutor(8) as ex:
        jobs = {k: ex.submit(f) for k, f in (("px", price), ("fund", funding), ("met", metrics), ("fng", fear_greed),
                                              ("stab", stablecoins), ("wiki", wiki), ("news", gdelt), ("etf", etf_flows),
                                              ("spx", lambda: fred("SP500")), ("dxy", lambda: fred("DTWEXBGS")),
                                              ("y10", lambda: fred("DGS10")))}
        src = {k: j.result() for k, j in jobs.items()}
    px = src["px"]
    idx = px.index
    f = pd.DataFrame(index=idx)
    lc = np.log(px["close"])
    for h in (1, 7, 30, 90):
        f[f"ret_{h}d"] = lc.diff(h)
    f["gap_sma50"] = px["close"] / px["close"].rolling(50).mean() - 1
    f["gap_sma200"] = px["close"] / px["close"].rolling(200).mean() - 1
    f["vol_30d"] = lc.diff().rolling(30).std()
    f["volume_z"] = zs(np.log(px["vol"]))
    f["taker_buy_7d"] = px["taker"].rolling(7).mean() - 0.5
    fu = src["fund"].reindex(idx)
    f["funding_7d"] = fu.rolling(7, min_periods=5).mean()
    f["funding_z"] = zs(fu.rolling(7, min_periods=5).mean())
    m = src["met"].reindex(idx)
    f["oi_7d"] = np.log(m["oi_usd"]).diff(7)
    f["ls_top_7d"] = m["ls_top"].rolling(7, min_periods=4).mean()
    f["ls_crowd_7d"] = m["ls_crowd"].rolling(7, min_periods=4).mean()
    f["taker_ls_7d"] = m["taker_ls"].rolling(7, min_periods=4).mean()
    g = src["fng"].reindex(idx)
    f["fear_greed"] = g
    f["fear_greed_7d"] = g.diff(7)
    st = src["stab"].reindex(idx).ffill(limit=3)
    f["stable_7d"] = np.log(st).diff(7)
    f["stable_30d"] = np.log(st).diff(30)
    w = src["wiki"].reindex(idx).shift(1)
    f["wiki_z"] = zs(np.log(w))
    f["wiki_7d"] = np.log(w.rolling(7).mean()).diff(7)
    nw = src["news"].reindex(idx).shift(1) if len(src["news"]) else pd.DataFrame(index=idx, columns=["news_tone", "news_vol"])
    f["news_tone_7d"] = nw["news_tone"].astype(float).rolling(7, min_periods=4).mean()
    f["news_vol_z"] = zs(np.log(nw["news_vol"].astype(float).rolling(7, min_periods=4).mean()))
    etf = src["etf"].reindex(idx).shift(1)
    f["etf_5d"] = etf.rolling(5, min_periods=1).sum().where(idx >= "2024-01-12")   # $M, weekends count as no flow
    for k, name in (("spx", "spx_20d"), ("dxy", "dollar_20d"), ("y10", "yield10_20d")):
        s = src[k].reindex(idx).ffill(limit=4)
        f[name] = (s.diff(20) if k == "y10" else np.log(s).diff(20))
    cover = {k: (str(v.index.min().date()) if len(v) else None, len(v)) for k, v in src.items()}
    return pd.concat([px, f], axis=1), cover


def ic_table(d: pd.DataFrame, feats: list, h: int) -> dict:
    fwd = np.log(d["close"]).shift(-h) - np.log(d["close"])
    out = {}
    for c in feats:
        row = {}
        for y in YEARS:
            m = (d.index.year == int(y)) & d[c].notna() & fwd.notna()
            if m.sum() >= 60:
                row[y] = round(float(d.loc[m, c].rank().corr(fwd[m].rank())), 3)
        same = [v for v in row.values()]
        pos, neg = sum(v > 0 for v in same), sum(v < 0 for v in same)
        row["consistent"] = bool(len(same) >= 5 and max(pos, neg) >= len(same) - 1 and abs(np.mean(same)) >= 0.03)
        out[c] = row
    return out


def walk_forward(d: pd.DataFrame, feats: list, h: int, kind: str) -> pd.Series:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    fwd = np.log(d["close"]).shift(-h) - np.log(d["close"])
    y = (fwd > 0).astype(float).where(fwd.notna())
    X = d[feats]
    prob = pd.Series(np.nan, index=d.index)
    months = pd.date_range("2021-01-01", d.index[-1], freq="MS")
    for i, m0 in enumerate(months):
        m1 = months[i + 1] if i + 1 < len(months) else d.index[-1] + pd.Timedelta(days=1)
        train = (d.index <= m0 - pd.Timedelta(days=h + 1)) & y.notna() & (d.index >= "2018-03-01")
        test = (d.index >= m0) & (d.index < m1)
        if train.sum() < 365 or test.sum() == 0:
            continue
        cols = [c for c in feats if X.loc[train, c].notna().sum() >= 200 and X.loc[train, c].nunique() > 2]
        Xt, yt = X.loc[train, cols], y[train]
        if kind == "logit":
            mu, sd = Xt.mean(), Xt.std().replace(0, 1)
            Z = ((Xt - mu) / sd).fillna(0).clip(-5, 5)
            mdl = LogisticRegression(C=0.05, max_iter=2000).fit(Z, yt)
            prob[test] = mdl.predict_proba(((X.loc[test, cols] - mu) / sd).fillna(0).clip(-5, 5))[:, 1]
        else:
            mdl = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.04, max_iter=200, min_samples_leaf=40,
                                                 l2_regularization=1.0, random_state=7).fit(Xt, yt)
            prob[test] = mdl.predict_proba(X.loc[test, cols])[:, 1]
    return prob


def auc(p, yv) -> float | None:
    p, yv = np.asarray(p), np.asarray(yv)
    pos, neg = p[yv == 1], p[yv == 0]
    if len(pos) < 10 or len(neg) < 10:
        return None
    r = pd.Series(np.concatenate([pos, neg])).rank().values
    return round(float((r[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))), 3)


def score(d: pd.DataFrame, prob: pd.Series, h: int) -> dict:
    fwd = np.log(d["close"]).shift(-h) - np.log(d["close"])
    mom = np.sign(d["ret_30d"])
    out = {}
    for y in ["all"] + YEARS:
        m = prob.notna() & fwd.notna() & (fwd != 0)
        if y != "all":
            m &= d.index.year == int(y)
        if m.sum() < 30:
            continue
        up = (fwd[m] > 0)
        hit = float(((prob[m] > 0.5) == up).mean())
        out[y] = {"n": int(m.sum()), "hit": round(hit * 100, 1), "always_up": round(float(up.mean()) * 100, 1),
                  "momentum": round(float(((mom[m] > 0) == up).mean()) * 100, 1),
                  "auc": auc(prob[m].values, up.astype(int).values)}
    return out


def trade(d: pd.DataFrame, prob7: pd.Series) -> dict:
    r = d["close"].pct_change().shift(-1)            # tomorrow's return, decided at today's close
    sma = d["close"].rolling(200).mean()
    regime = ((d["close"] > sma) & (d["close"] / d["close"].shift(60) > 1)).astype(float)
    rules = {"model_7d": 0.2 + 0.5 * (prob7 > 0.5).astype(float),
             "regime": 0.2 + 0.5 * regime,
             "regime_and_model": 0.2 + 0.5 * ((prob7 > 0.5) & (regime > 0)).astype(float),
             "fixed_40": pd.Series(0.4, index=d.index), "buy_hold": pd.Series(1.0, index=d.index)}
    ok = prob7.notna() & r.notna()
    out = {}
    for k, e in rules.items():
        e = e[ok]
        ret = e * r[ok] + (1 - e) * CASH / 365 - COST * e.diff().abs().fillna(0)
        eq = (1 + ret).cumprod()
        row = {"total_pct": round(float(eq.iloc[-1] - 1) * 100), "max_dd_pct": round(float((eq / eq.cummax() - 1).min()) * 100)}
        for y in YEARS:
            ry = ret[ret.index.year == int(y)]
            if len(ry) > 20:
                row[y] = round(float((1 + ry).prod() - 1) * 100, 1)
        out[k] = row
    return out


def overheat(d: pd.DataFrame) -> None:
    """Step 2 (18.10, after the first run): no blind model beat the baselines, but four «crowd» factors kept the same sign every
    year — greed (Fear & Greed), leverage (funding), stretch above SMA200 and the 90-day run-up all point to WEAKER returns
    ahead. Test a fixed, simple rule on top of our regime: each factor's percentile within its own past 365 days (no look-ahead),
    averaged and smoothed over 7 days = «перегрев толпы» 0–1. Bull regime and heat > X → 45 % instead of 70 %; optional mirror: bear regime and heat < 1 − X
    (panic) → 35 % instead of 20 %."""
    comps = ["fear_greed", "funding_7d", "gap_sma200", "ret_90d"]
    pr = pd.DataFrame({c: d[c].rolling(365, min_periods=180).apply(lambda x: float((x[:-1] < x[-1]).mean()), raw=True)
                       for c in comps})
    heat = pr.mean(axis=1, skipna=True).where(pr.notna().sum(axis=1) >= 3).rolling(7, min_periods=5).mean()   # weekly, less churn
    out_ic = {}
    for h in (7, 30):
        fwd = np.log(d["close"]).shift(-h) - np.log(d["close"])
        out_ic[f"{h}d"] = {y: round(float(heat[(heat.index.year == int(y)) & heat.notna() & fwd.notna()].rank()
                                       .corr(fwd[(heat.index.year == int(y)) & heat.notna() & fwd.notna()].rank())), 3)
                           for y in ["2019", "2020"] + YEARS}
    now_heat = round(float(heat.dropna().iloc[-1]), 2) if heat.notna().any() else None
    note("forecast · overheat IC", json.dumps({"ic_by_year": out_ic, "heat_today": now_heat,
                                               "parts_today": {c: round(float(pr[c].dropna().iloc[-1]), 2) for c in comps if pr[c].notna().any()}},
                                              ensure_ascii=False))
    r = d["close"].pct_change().shift(-1)
    sma = d["close"].rolling(200).mean()
    bull = (d["close"] > sma) & (d["close"] / d["close"].shift(60) > 1)
    base = 0.2 + 0.5 * bull.astype(float)
    rules = {"regime": base}
    for X in (0.7, 0.8, 0.9):
        trim = base.where(~(bull & (heat > X)), 0.45)
        rules[f"trim>{X}"] = trim
        rules[f"trim>{X}+panic<{round(1 - X, 1)}"] = trim.where(~(~bull & (heat < 1 - X)), 0.35)
    res = {}
    for start in ("2019-01-01", "2021-01-01"):
        ok = (d.index >= start) & r.notna() & heat.notna()
        res[start[:4]] = {}
        for k, e in rules.items():
            e = e[ok]
            ret = e * r[ok] + (1 - e) * CASH / 365 - COST * e.diff().abs().fillna(0)
            eq = (1 + ret).cumprod()
            row = {"total_pct": round(float(eq.iloc[-1] - 1) * 100), "max_dd_pct": round(float((eq / eq.cummax() - 1).min()) * 100),
                   "changes": int((e.diff().abs() > 0).sum())}
            for y in ["2019", "2020"] + YEARS:
                ry = ret[ret.index.year == int(y)]
                if len(ry) > 20:
                    row[y] = round(float((1 + ry).prod() - 1) * 100, 1)
            res[start[:4]][k] = row
    note("forecast · overheat rules", json.dumps(res, ensure_ascii=False))


def main():
    d, cover = build()
    if "--overheat" in sys.argv:
        return overheat(d)
    feats = [c for c in d.columns if c not in ("close", "vol", "taker")]
    note("forecast · data", json.dumps({"rows": len(d), "from": str(d.index.min().date()), "to": str(d.index.max().date()),
                                        "sources": cover, "factors": len(feats)}, ensure_ascii=False))
    for h in (7, 30):
        t = ic_table(d, feats, h)
        cons = sorted([k for k, v in t.items() if v.get("consistent")], key=lambda k: -abs(np.mean([v for y, v in t[k].items() if y in YEARS])))
        note(f"forecast · factors IC {h}d", json.dumps({"consistent": cons, "table": t}, ensure_ascii=False))
    res, probs = {}, {}
    for h in (1, 7, 30):
        res[h] = {}
        for kind in ("logit", "gbm"):
            p = walk_forward(d, feats, h, kind)
            probs[(h, kind)] = p
            res[h][kind] = score(d, p, h)
        note(f"forecast · blind model {h}d", json.dumps(res[h], ensure_ascii=False))
    best = max(("logit", "gbm"), key=lambda k: (res[7][k].get("all") or {}).get("auc") or 0)
    tr = trade(d, probs[(7, best)])
    note(f"forecast · trading 7d ({best})", json.dumps(tr, ensure_ascii=False))
    verdict = {}
    for h in (1, 7, 30):
        for kind in ("logit", "gbm"):
            s = res[h][kind]
            ok = all(y in s and s[y]["hit"] - max(s[y]["always_up"], s[y]["momentum"]) >= 5 for y in ("2025", "2026"))
            verdict[f"{h}d_{kind}"] = {"passes": ok, **{y: (round(s[y]["hit"] - max(s[y]["always_up"], s[y]["momentum"]), 1)
                                                            if y in s else None) for y in ("2025", "2026")}}
    beat = all(tr["model_7d"].get(y, -1e9) > tr["regime"].get(y, 1e9) for y in ("2025", "2026"))
    note("forecast · verdict", json.dumps({"edge_vs_best_baseline_points": verdict, "trading_beats_regime_2025_2026": beat},
                                          ensure_ascii=False))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write("## Прогнозный движок\n```\n" + json.dumps({"models": res, "trading": tr, "verdict": verdict}, ensure_ascii=False,
                                                              indent=1) + "\n```\n")


if __name__ == "__main__":
    main()
