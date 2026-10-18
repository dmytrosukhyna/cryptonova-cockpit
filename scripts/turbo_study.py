"""«Турбо» — how far can our proven edges be pushed toward the owner's target (19.10.2026, owner: «цель 100%; промежуточная —
+70–80% в месяц; делаем всё, чтобы её добиться»). Runs in GitHub Actions only (.github/workflows/research-turbo.yml).

Two proven edges, pushed by SIZE (how much of the account per trade) and LEVERAGE, month by month, with compounding:
  1. Short of new listings (the live rule: short 4 h after a Binance USDT-M listing, stop +30 %, exit after 14 days; optional
     filters of the live desk — skip a coin up ≥ 10 % in its first 4 h, skip first-4 h turnover < $2M). Account simulation in time
     order: each new listing gets `share` × current equity (notional — above 1 open position this is leverage), at most `max_open`
     open; profit or loss lands at the exit (realised; an open trade can be at most −31 % of its notional thanks to the stop).
     Shares 5 / 10 / 15 / 20 / 30 / 50 % × max open 10 / 20 / 40. Plus the growth-optimal size (Kelly) from the per-trade results.
  2. BTC with leverage only while our regime says «рост» (BTC above its 200-day average and up over 60 days), cash at 4 %
     otherwise; leverage 1–5×, rebalanced daily, funding paid on the leverage (Binance BTCUSDT funding).
For every variant: median / average month, share of months in plus, worst month, months at +70 % or more, worst drawdown, the
result per year and in total — the honest map of «how much a month» against «how deep it can fall». Stdlib + numpy."""
import datetime as dt
import io
import json
import os
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from fetch_alt100 import http, months_between, note  # noqa: E402
import listings_study as LS  # noqa: E402

DAY = 86_400_000
TODAY = dt.datetime.now(dt.timezone.utc).date()


def trade(L: dict, delay_h: int = 4, hold_d: int = 14, stop: float = 0.3):
    """The live rule with its exit time: (return on notional incl. funding and costs, entry ms, exit ms, first-4h move, 4h turnover)."""
    t, o, h, c = L["t"], L["o"], L["h"], L["c"]
    e, x = delay_h, delay_h + hold_d * 24
    if x >= len(t) or t[x] - t[e] > (hold_d * 24 + 6) * LS.H:
        return None
    ent, exit_i, ret = o[e], x, None
    hit = np.where(h[e:x + 1] >= ent * (1 + stop))[0]
    if len(hit):
        exit_i, ret = e + int(hit[0]), -(stop + LS.SLIP)
    if ret is None:
        ret = 1 - c[x] / ent
    m = (L["ft"] >= t[e]) & (L["ft"] < t[exit_i] + LS.H)
    fund = float(L["fv"][m].sum()) if len(L["fv"]) else 0.0
    m4 = c[3] / o[0] - 1 if len(c) > 4 else None
    q4 = float(L["q"][:4].sum()) if "q" in L else None
    return ret + fund - LS.COST, int(t[e]), int(t[exit_i]), m4, q4


def month_key(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m")


def stats_from_curve(points: list) -> dict:
    """points: [(ms, equity)] in time order, starting at (t0, 1.0). Monthly returns from month-end equity."""
    if len(points) < 2:
        return {}
    months = months_between(month_key(points[0][0]), month_key(points[-1][0]))
    end, j, eq = {}, 0, points[0][1]
    for mo in months:
        while j < len(points) and month_key(points[j][0]) <= mo:
            eq = points[j][1]
            j += 1
        end[mo] = eq
    prev, mret = points[0][1], []
    for mo in months:
        mret.append(end[mo] / prev - 1 if prev > 0 else -1.0)
        prev = end[mo]
    v = np.array(mret)
    eqs = np.array([p[1] for p in points])
    dd = float((eqs / np.maximum.accumulate(eqs) - 1).min())
    years = {}
    for mo, r in zip(months, mret):
        years.setdefault(mo[:4], []).append(r)
    return {"months": len(v), "median_month_pct": round(float(np.median(v)) * 100, 1), "avg_month_pct": round(float(v.mean()) * 100, 1),
            "geo_month_pct": round(float((np.prod(1 + np.clip(v, -0.999, None)) ** (1 / len(v)) - 1) * 100), 1),
            "months_plus_pct": round(float((v > 0).mean()) * 100), "worst_month_pct": round(float(v.min()) * 100, 1),
            "best_month_pct": round(float(v.max()) * 100, 1), "months_70plus": int((v >= 0.70).sum()),
            "max_dd_pct": round(dd * 100), "total_x": round(float(eqs[-1] / eqs[0]), 2),
            "years_pct": {y: round(float(np.prod([1 + r for r in rs]) - 1) * 100) for y, rs in years.items()}}


def sim_listings(trs: list, share: float, max_open: int) -> dict:
    trs = sorted(trs, key=lambda x: x[1])
    E, open_, pts = 1.0, [], []
    t0 = trs[0][1] if trs else 0
    pts.append((t0, 1.0))
    for r, te, tx, *_ in trs:
        for o in sorted([o for o in open_ if o[1] <= te], key=lambda o: o[1]):
            E += o[0] * o[2]
            pts.append((o[1], max(E, 0.0)))
            open_.remove(o)
        if E <= 0:
            break
        if len(open_) < max_open:
            open_.append((share * E, tx, r))
    for o in sorted(open_, key=lambda o: o[1]):
        E += o[0] * o[2]
        pts.append((o[1], max(E, 0.0)))
    out = stats_from_curve(pts)
    out["ruined"] = E <= 0.05
    return out


def kelly(rs: np.ndarray) -> dict:
    fs = np.linspace(0.01, 3.0, 300)
    g = [float(np.mean(np.log(np.clip(1 + f * rs, 1e-9, None)))) for f in fs]
    i = int(np.argmax(g))
    return {"mean_pct": round(float(rs.mean()) * 100, 2), "sd_pct": round(float(rs.std()) * 100, 1),
            "kelly_share_pct": round(float(fs[i]) * 100), "half_kelly_share_pct": round(float(fs[i]) * 50)}


def listings_part():
    syms = sorted(s for s in LS.vision_symbols() if s.endswith("USDT"))
    with ThreadPoolExecutor(16) as ex:
        data = [d for d in ex.map(lambda s: LS.load(s, TODAY), syms) if d]
    trs = [x for d in data if (x := trade(d)) is not None]
    filt = [x for x in trs if not (x[3] is not None and x[3] >= 0.10) and not (x[4] is not None and x[4] < 2e6)]
    note("turbo · listings data", json.dumps({"listings": len(data), "trades": len(trs), "after_live_filters": len(filt),
                                              "kelly_all": kelly(np.array([x[0] for x in trs])),
                                              "kelly_filtered": kelly(np.array([x[0] for x in filt]))}, ensure_ascii=False))
    for name, T in (("all", trs), ("filtered", filt)):
        grid = {}
        for share in (0.05, 0.10, 0.15, 0.20, 0.30, 0.50):
            for mo in (10, 20, 40):
                grid[f"{int(share * 100)}%×{mo}"] = sim_listings(T, share, mo)
        keep = {k: {kk: v[kk] for kk in ("median_month_pct", "geo_month_pct", "months_plus_pct", "worst_month_pct", "months_70plus",
                                          "max_dd_pct", "total_x", "years_pct", "ruined") if kk in v} for k, v in grid.items()}
        note(f"turbo · listing short, {name}", json.dumps(keep, ensure_ascii=False))


def _rows(url_m, start):
    last_full = (TODAY.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
    out = []
    with ThreadPoolExecutor(12) as ex:
        for s, b in ex.map(lambda m: http(url_m(m), binary=True), months_between(start, last_full)):
            if s == 200:
                with zipfile.ZipFile(io.BytesIO(b)) as z:
                    out += [ln.split(",") for ln in z.read(z.namelist()[0]).decode().splitlines() if ln[:1].isdigit()]
    return out


def btc_part():
    k = _rows(lambda m: f"https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-{m}.zip", "2019-01")
    days = {}
    for r in k:
        t = int(r[0]); t = t // 1000 if t > 10 ** 14 else t
        days[t - t % DAY] = float(r[4])
    f = _rows(lambda m: f"https://data.binance.vision/data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-{m}.zip", "2019-09")
    fund = {}
    for r in f:
        t = int(r[0])
        fund[t - t % DAY] = fund.get(t - t % DAY, 0.0) + float(r[2])
    ts = sorted(days)
    c = np.array([days[t] for t in ts])
    out = {}
    for lev in (1, 2, 3, 4, 5):
        E, pts, start = 1.0, [], None
        for i in range(260, len(ts) - 1):
            if ts[i] < int(dt.datetime(2021, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000):
                continue
            start = start or ts[i]
            if not pts:
                pts.append((ts[i], 1.0))
            bull = c[i] > c[i - 199:i + 1].mean() and c[i] > c[i - 60]
            r = c[i + 1] / c[i] - 1
            if bull:
                E *= max(0.0, 1 + lev * r - lev * fund.get(ts[i + 1], 0.0))       # a long pays positive funding
            else:
                E *= 1 + 0.04 / 365
            pts.append((ts[i + 1], E))
            if E <= 0:
                break
        out[f"{lev}x"] = stats_from_curve(pts)
    note("turbo · BTC with leverage only in «рост»", json.dumps(out, ensure_ascii=False))


def main():
    btc_part()
    listings_part()


if __name__ == "__main__":
    main()
