"""Стоп-охота или настоящий пробой? Вопрос владельца 22.10.2026 (docs/research/sweep.md).

When BTC comes to its 20-day low: is a break a real breakdown (short) or a stop hunt that reverses (long)?

Events, per coin, on Binance spot candles (data-api.binance.vision, public, no keys):
  level L = lowest low of the previous 20 days (daily: 20 bars, 4h: 120 bars), fixed on the break bar.
  SWEEP      low breaks L by >= 0.5 %, then a close back above L within W closes counting the break bar
             (daily W = 1, 2, 3; 4h W = 6, 12, 24). Confirmation = that close; entry at the next bar's open.
  BREAKDOWN  close below L and the next closes stay below: 3 daily closes in a row (4h: 18 closes = 3 days).
             Confirmation = the 3rd close; entry at the next bar's open.
  One episode per coin: after a break bar, a new event can start only once the previous episode is resolved
  (reclaimed or confirmed) plus a 5-day cool-down, so the samples do not overlap.
Regime on the confirmation bar, from DAILY closes of BTC (the system's regime is BTC-wide):
  above / below the 200-day average; «рост» = above the 200-day average AND up over 60 days (cnq/agents/turbo_desk.py).
Forward returns from the entry open to the close 3 / 7 / 14 days later, compared with the same-length return from
every day in the same regime (the «buy at random» baseline).
Rules, costs 0.1 % a side, one position per coin, stop checked on bar lows/highs, stop first inside a bar:
  long after a confirmed sweep: stop under the sweep low (−0.2 %), time exit 7 or 14 days;
  short after a confirmed breakdown: stop above the highest high since the break (+0.2 %), time exit 7 or 14 days.
Output: JSON blocks «sweep · …» in the log (and --md file with the tables).
Usage: python scripts/sweep_study.py [--md out.md]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np

API = "https://data-api.binance.vision/api/v3/klines"
DAY = 86_400_000
H4 = 14_400_000
START = int(dt.datetime(2017, 8, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
EV_FROM = dt.datetime(2019, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000
# the 10 most liquid Binance spot alts by 2026 USDT volume that existed before 2021 (stables / wrapped excluded).
# Survivorship: chosen by today's liquidity — coins that died since 2019 are not in the list.
ALTS = ["ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "TRX", "LINK", "AVAX", "LTC"]
FEE = 0.001          # per side
BREAK = 0.005        # sweep must pierce the level by 0.5 %
COOL = 5             # days between episodes


def http(url: str, tries: int = 6) -> list:
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "cnq-research/1.0"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read())
        except Exception:  # noqa: BLE001
            time.sleep(min(30, 1.5 * 2 ** a))
    return []


def klines(sym: str, interval: str, step: int) -> np.ndarray:
    out, t = [], START
    now = int(time.time() * 1000)
    while t < now:
        d = http(f"{API}?symbol={sym}USDT&interval={interval}&startTime={t}&limit=1000")
        if not d:
            break
        out += [[float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])] for r in d]
        t = int(d[-1][0]) + step
        if len(d) < 1000:
            break
    a = np.array(out, dtype=float)
    if len(a) and a[-1, 0] + step > now:      # drop the unfinished bar
        a = a[:-1]
    return a


def btc_regime(d: np.ndarray) -> dict:
    """day ms -> (above SMA200, «рост») using the close of that day."""
    c = d[:, 4]
    out = {}
    for i in range(200, len(d)):
        sma = c[i - 199:i + 1].mean()
        above = c[i] > sma
        out[int(d[i, 0])] = (bool(above), bool(above and c[i] > c[i - 60]))
    return out


def events(a: np.ndarray, per_day: int, win: int, bd_n: int) -> list[dict]:
    """a: bars [t,o,h,l,c]. win: reclaim window in closes (incl. the break bar). bd_n: closes below for a breakdown."""
    lb = 20 * per_day
    o, h, l, c = a[:, 1], a[:, 2], a[:, 3], a[:, 4]
    ev, i, n = [], lb, len(a)
    while i < n - 1:
        L = l[i - lb:i].min()
        if a[i, 0] < EV_FROM or not (l[i] < L or c[i] < L):
            i += 1
            continue
        # break bar i. Sweep: pierce >= 0.5 % and a close back above L within `win` closes
        pierce_by = i
        low = l[i]
        res = None
        for j in range(i, min(n, i + max(win, bd_n))):
            low = min(low, l[j])
            if c[j] > L and j - i < win and low <= L * (1 - BREAK):
                res = ("sweep", j)
                break
            if c[j] > L:            # back above before a valid sweep / breakdown → neither
                res = ("none", j)
                break
            if j - i + 1 >= bd_n and (c[i:j + 1] < L).all():
                res = ("breakdown", j)
                break
        if res is None:
            if i + max(win, bd_n) >= n:
                break
            res = ("none", i)
        kind, j = res
        if kind != "none" and j + 1 < n:
            ev.append({"kind": kind, "i": i, "j": j, "level": float(L), "t_conf": int(a[j, 0]),
                       "low": float(l[i:j + 1].min()), "high": float(h[i:j + 1].max()),
                       "depth": float(l[i:j + 1].min() / L - 1)})
        i = j + 1 + (COOL * per_day if kind != "none" else 0)
    return ev


def fwd(a: np.ndarray, e: dict, per_day: int, days: int):
    k = e["j"] + 1
    m = k + days * per_day - 1
    if m >= len(a):
        return None
    return a[m, 4] / a[k, 1] - 1


def trade(a: np.ndarray, e: dict, per_day: int, side: int, hold: int, stop: float | None):
    """side +1 long / −1 short; stop price or None. Returns net return or None (not enough bars)."""
    k = e["j"] + 1
    end = k + hold * per_day - 1
    if end >= len(a):
        return None
    ent = a[k, 1]
    ex = a[end, 4]
    if stop is not None:
        for m in range(k, end + 1):
            o, hi, lo = a[m, 1], a[m, 2], a[m, 3]
            if side > 0 and lo <= stop:
                ex = min(o, stop)
                break
            if side < 0 and hi >= stop:
                ex = max(o, stop)
                break
    return side * (ex / ent - 1) - 2 * FEE


def tstat(x) -> float:
    x = np.asarray(x, dtype=float)
    if len(x) < 3 or x.std(ddof=1) == 0:
        return 0.0
    return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x))))


def summ(x, years=None, weeks=None) -> dict:
    """weeks: the confirmation week of each value — events of many coins in one week are one shock, so t_week averages
    them per week first (an honest t for the pooled groups)."""
    keep = [k for k, v in enumerate(x) if v is not None]
    x = np.asarray([x[k] for k in keep], dtype=float)
    if not len(x):
        return {"n": 0}
    out = {"n": int(len(x)), "avg_pct": round(float(x.mean()) * 100, 2), "med_pct": round(float(np.median(x)) * 100, 2),
           "win_pct": round(float((x > 0).mean()) * 100), "t": round(tstat(x), 2)}
    if weeks is not None:
        by = {}
        for k, v in zip(keep, x):
            by.setdefault(weeks[k], []).append(v)
        out["n_weeks"] = len(by)
        out["t_week"] = round(tstat([np.mean(v) for v in by.values()]), 2)
    if years is not None:
        by = {}
        for y, v in years:
            if v is not None:
                by.setdefault(y, []).append(v)
        out["years"] = {y: round(float(np.sum(v)) * 100, 1) for y, v in sorted(by.items())}
        out["years_plus"] = f"{sum(1 for v in by.values() if np.sum(v) > 0)}/{len(by)}"
    return out


def baseline(a: np.ndarray, per_day: int, reg: dict, days: int) -> dict:
    """forward `days` return from every bar open (daily: every day), by regime of the previous day."""
    out = {"all": [], "above": [], "below": [], "rost": []}
    step = per_day
    for k in range(0, len(a) - days * per_day, step):
        if a[k, 0] < EV_FROM:
            continue
        r = reg.get(int(a[k, 0] - a[k, 0] % DAY) - DAY)
        if r is None:
            continue
        v = a[k + days * per_day - 1, 4] / a[k, 1] - 1
        out["all"].append(v)
        out["above" if r[0] else "below"].append(v)
        if r[1]:
            out["rost"].append(v)
    return {k: round(float(np.mean(v)) * 100, 2) if v else None for k, v in out.items()}


def study(data: dict, reg: dict, tf: str, win: int) -> dict:
    per_day = 1 if tf == "1d" else 6
    bd_n = 3 * per_day
    res = {}
    rows = []                     # (coin, event, regime tuple, a)
    for sym, a in data[tf].items():
        for e in events(a, per_day, win, bd_n):
            dk = e["t_conf"] - e["t_conf"] % DAY
            r = reg.get(dk if tf == "1d" else dk - DAY)   # 4h: last complete day
            if r is None:
                continue
            rows.append((sym, e, r, a))
    for grp_name, coins in (("BTC", {"BTC"}), ("ALTS", set(ALTS)), ("ALL", {"BTC", *ALTS})):
        g = {}
        for kind in ("sweep", "breakdown"):
            for regn, pick in (("any", lambda r: True), ("above", lambda r: r[0]), ("below", lambda r: not r[0]),
                               ("rost", lambda r: r[1])):
                sel = [(s, e, r, a) for s, e, r, a in rows if s in coins and e["kind"] == kind and pick(r)]
                cell = {"n": len(sel)}
                wk = [e["t_conf"] // (7 * DAY) for s, e, r, a in sel]
                for d in (3, 7, 14):
                    cell[f"fwd{d}"] = summ([fwd(a, e, per_day, d) for s, e, r, a in sel], weeks=wk)
                side = 1 if kind == "sweep" else -1
                for hold in (7, 14):
                    for st in ("struct", "none"):
                        def stop_of(e):
                            if st == "none":
                                return None
                            return e["low"] * 0.998 if side > 0 else e["high"] * 1.002
                        tr = [(dt.datetime.fromtimestamp(e["t_conf"] / 1000, dt.timezone.utc).year,
                               trade(a, e, per_day, side, hold, stop_of(e))) for s, e, r, a in sel]
                        cell[f"rule_h{hold}_{st}"] = c2 = summ([v for _, v in tr], tr, wk)
                        if grp_name == "BTC":           # one position at a time: equity vs buy-and-hold
                            eq, busy, days_in = 1.0, -1, 0
                            for (s, e, r, a), (_, v) in zip(sel, tr):
                                if v is None or e["j"] + 1 <= busy:
                                    continue
                                eq *= 1 + v
                                busy = e["j"] + hold * per_day
                                days_in += hold
                            c2["equity_x"] = round(eq, 2)
                            c2["days_in_market"] = days_in
                cell["avg_stop_dist_pct"] = round(float(np.mean([abs((e["low"] if side > 0 else e["high"]) / a[e["j"] + 1, 1] - 1)
                                                                  for s, e, r, a in sel if e["j"] + 1 < len(a)])) * 100, 1) if sel else None
                cell["avg_depth_pct"] = round(float(np.mean([e["depth"] for s, e, r, a in sel])) * 100, 2) if sel else None
                g[f"{kind}.{regn}"] = cell
        res[grp_name] = g
    return res


def recent(data: dict, reg: dict) -> list:
    """the last BTC events, daily W=3 — what happened at the level lately."""
    a = data["1d"]["BTC"]
    out = []
    for e in events(a, 1, 3, 3)[-12:]:
        out.append({"date": dt.datetime.fromtimestamp(e["t_conf"] / 1000, dt.timezone.utc).strftime("%Y-%m-%d"), "kind": e["kind"],
                    "level": round(e["level"]), "depth_pct": round(e["depth"] * 100, 1),
                    "fwd7_pct": None if (v := fwd(a, e, 1, 7)) is None else round(v * 100, 1),
                    "regime_above": reg.get(e["t_conf"] - e["t_conf"] % DAY, (None,))[0]})
    return out


def note(title: str, obj) -> None:
    print(f"\n### {title}\n{json.dumps(obj, ensure_ascii=False)}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", default="")
    args = ap.parse_args()
    syms = ["BTC", *ALTS]
    data = {"1d": {}, "4h": {}}
    with ThreadPoolExecutor(8) as ex:
        for tf, step in (("1d", DAY), ("4h", H4)):
            for s, a in zip(syms, ex.map(lambda s: klines(s, tf, step), syms)):
                if len(a):
                    data[tf][s] = a
    note("sweep · data", {tf: {s: [len(a), dt.datetime.fromtimestamp(a[0, 0] / 1000, dt.timezone.utc).strftime("%Y-%m-%d"),
                                     dt.datetime.fromtimestamp(a[-1, 0] / 1000, dt.timezone.utc).strftime("%Y-%m-%d")]
                                 for s, a in d.items()} for tf, d in data.items()})
    reg = btc_regime(data["1d"]["BTC"])
    base = {}
    for s in ("BTC", "ETH"):
        if s in data["1d"]:
            base[s] = {f"fwd{d}": baseline(data["1d"][s], 1, reg, d) for d in (3, 7, 14)}
    note("sweep · baseline (every day, avg forward %, by BTC regime)", base)
    # strategy-level baseline: buy-and-hold BTC per regime, and the cost of doing nothing = 0
    a = data["1d"]["BTC"]
    i0 = int(np.searchsorted(a[:, 0], EV_FROM))
    note("sweep · buy-and-hold BTC 2019→now", {"x": round(a[-1, 4] / a[i0, 1], 2), "from": a[i0, 1], "to": a[-1, 4]})
    out = {}
    for tf, wins in (("1d", (1, 2, 3)), ("4h", (6, 12, 24))):
        for w in wins:
            out[f"{tf}.W{w}"] = r = study(data, reg, tf, w)
            note(f"sweep · {tf} W={w}", r)
    note("sweep · BTC recent events (1d, W=3)", recent(data, reg))
    # the current levels
    c = a[:, 4]
    note("sweep · BTC now", {"close": c[-1], "low20": float(a[-20:, 3].min()), "high20": float(a[-20:, 2].max()),
                             "sma200": round(float(c[-200:].mean()), 1)})
    if args.md:
        with open(args.md, "w") as f:
            json.dump(out, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
