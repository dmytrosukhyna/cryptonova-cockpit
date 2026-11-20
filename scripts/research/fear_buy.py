"""П. 221 (owner 20.11.2026): «на дне, в панике, альты и ядро надо набирать, а не продавать; пятница-сливатница — потом свинговые лонги».

Hypotheses fixed before looking (N = 6 variants, all reported):
  H1  buy at extreme fear — Fear & Greed ≤ 10 / ≤ 15 / ≤ 20, an EPISODE = the first such day after ≥ 14 days without one
      (clustered days are not independent); forward return of BTC, ETH, SOL and an equal-weight alt basket at 7 / 30 / 90 days,
      entry = the signal day's close, costs 0.2 % round trip; the deepest further drop within 30 days (how much it hurt first).
      Benchmark: ALL days with BTC below its SMA200 (extreme fear lives almost only there — buying «any bear day» is the fair rival).
  H2  «пятница-сливатница»: Thu close → Fri close return vs the other weekdays (BTC, alt basket), all days and BTC < SMA200 only;
      t-stat of the difference; both halves (2018-02…2022-06 / 2022-07…).
  H3  the combined idea for a demo: extreme fear (≤ 15) episode → wait for the next Friday close (buy the Friday dip) vs buy at once;
      forward 30 / 90 d.
Data: Binance spot daily (data-api.binance.vision), alternative.me Fear & Greed (from 2018-02). Prints ::notice lines. Stdlib only.
History, not a promise; not financial advice."""
from __future__ import annotations

import json
import math
import statistics as S
import sys
import time
import urllib.request
from datetime import datetime, timezone

CORE = ["BTC", "ETH", "SOL"]
ALTS = ["XRP", "ADA", "DOGE", "LINK", "AVAX", "DOT", "LTC", "BNB", "UNI", "AAVE", "NEAR", "ATOM", "TRX", "BCH", "ETC", "XLM", "FIL", "OP", "ARB", "SUI"]
COST = 0.002


def get(url: str):
    for i in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "cnq-research"}), timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(2 + i * 3)
    raise RuntimeError(f"{url}: {err}")


def day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def klines(sym: str) -> dict:
    out, start = {}, 1514764800000
    while True:
        d = get(f"https://data-api.binance.vision/api/v3/klines?symbol={sym}USDT&interval=1d&limit=1000&startTime={start}")
        if not d:
            break
        for k in d:
            out[day(k[0])] = float(k[4])
        if len(d) < 1000:
            break
        start = d[-1][0] + 86_400_000
    return out


def fng() -> dict:
    d = get("https://api.alternative.me/fng/?limit=0&format=json")["data"]
    return {day(int(x["timestamp"]) * 1000): float(x["value"]) for x in d}


def note(title: str, msg: str):
    print(f"::notice title={title}::{msg}")
    sys.stdout.flush()


def tstat(a: list, b: list) -> float:
    if len(a) < 3 or len(b) < 3:
        return float("nan")
    va, vb = S.pvariance(a), S.pvariance(b)
    se = math.sqrt(va / len(a) + vb / len(b))
    return (S.mean(a) - S.mean(b)) / se if se else float("nan")


def main():
    fg = fng()
    px = {}
    for c in CORE + ALTS:
        try:
            px[c] = klines(c)
        except Exception as e:  # noqa: BLE001
            note("miss", f"{c}: {e}")
    days = sorted(d for d in px["BTC"] if d >= "2018-02-01")
    idx = {d: i for i, d in enumerate(days)}
    btc = [px["BTC"][d] for d in days]
    sma = [S.mean(btc[i - 199:i + 1]) if i >= 199 else None for i in range(len(days))]
    # BTC full history for SMA200 from 2018-02: prepend earlier closes
    allb = sorted(px["BTC"])
    full = [px["BTC"][d] for d in allb]
    fpos = {d: i for i, d in enumerate(allb)}
    sma = [S.mean(full[fpos[d] - 199:fpos[d] + 1]) if fpos[d] >= 199 else None for d in days]
    bear = [s is not None and b < s for b, s in zip(btc, sma)]
    note("data", f"days {days[0]}…{days[-1]} ({len(days)}), F&G {len(fg)} days, coins {len(px)}; BTC<SMA200 {sum(bear)} days")

    def fwd(c: str, i: int, h: int):
        d0, j = days[i], i + h
        if j >= len(days):
            return None
        a, b = px[c].get(d0), px[c].get(days[j])
        if not a or not b:
            return None
        return b / a - 1 - COST

    def basket(i: int, h: int):
        v = [fwd(c, i, h) for c in ALTS]
        v = [x for x in v if x is not None]
        return S.mean(v) if len(v) >= 5 else None

    def dd30(c: str, i: int):
        a = px[c].get(days[i])
        lo = [px[c].get(days[j]) for j in range(i + 1, min(i + 31, len(days))) if px[c].get(days[j])]
        return (min(lo) / a - 1) if a and lo else None

    def series(c, i, h):
        return basket(i, h) if c == "ALT" else fwd(c, i, h)

    def stats(vals):
        v = [x for x in vals if x is not None]
        if not v:
            return "n 0"
        return f"n {len(v)} ср {S.mean(v)*100:+.1f}% мед {S.median(v)*100:+.1f}% в плюсе {sum(x > 0 for x in v)/len(v)*100:.0f}%"

    bear_days = [i for i in range(len(days)) if bear[i]]
    half = idx.get("2022-07-01", len(days) // 2)
    # H1
    for thr in (10, 15, 20):
        eps, last = [], -99
        for i, d in enumerate(days):
            v = fg.get(d)
            if v is not None and v <= thr:
                if i - last >= 14:
                    eps.append(i)
                last = i
        note(f"H1 F&G<={thr} episodes", f"{len(eps)}: " + ", ".join(f"{days[i]}({int(fg[days[i]])})" for i in eps))
        for c in ("BTC", "ETH", "SOL", "ALT"):
            for h in (7, 30, 90):
                e = [series(c, i, h) for i in eps]
                b = [series(c, i, h) for i in bear_days]
                e1 = [series(c, i, h) for i in eps if i < half]
                e2 = [series(c, i, h) for i in eps if i >= half]
                ee = [x for x in e if x is not None]
                bb = [x for x in b if x is not None]
                note(f"H1 F&G<={thr} {c} {h}d",
                     f"страх: {stats(e)} | любой день BTC<SMA200: {stats(b)} | t {tstat(ee, bb):+.2f} | 1-я пол.: {stats(e1)} | 2-я: {stats(e2)}")
        if thr == 15:
            for i in eps:
                r = {c: series(c, i, h) for c in ("BTC", "ALT") for h in (30, 90)}
                rows = [f"{c} 30д {series(c, i, 30)*100:+.0f}% 90д {series(c, i, 90)*100:+.0f}%" if series(c, i, 90) is not None
                        else f"{c} 30д {(series(c, i, 30) or 0)*100:+.0f}%" for c in ("BTC", "ALT")]
                note(f"H1 episode {days[i]}", f"F&G {int(fg[days[i]])}, BTC {btc[i]:.0f}, ещё вниз за 30д: BTC {(dd30('BTC', i) or 0)*100:.0f}% · " + " · ".join(rows))
    # H2 weekday
    for c in ("BTC", "ALT"):
        for only_bear in (False, True):
            by = {w: [] for w in range(7)}
            by1 = {w: [] for w in range(7)}
            by2 = {w: [] for w in range(7)}
            for i in range(1, len(days)):
                if only_bear and not bear[i - 1]:
                    continue
                r = (series(c, i - 1, 1) or 0) + COST if series(c, i - 1, 1) is not None else None
                if r is None:
                    continue
                w = datetime.strptime(days[i], "%Y-%m-%d").weekday()
                by[w].append(r)
                (by1 if i < half else by2)[w].append(r)
            fri = by[4]
            oth = [x for w in range(7) if w != 4 for x in by[w]]
            txt = " ".join(f"{'пн вт ср чт пт сб вс'.split()[w]} {S.mean(by[w])*100:+.2f}%" for w in range(7))
            note(f"H2 {c} {'BTC<SMA200' if only_bear else 'все дни'}",
                 f"{txt} | пт {S.mean(fri)*100:+.2f}% (в минусе {sum(x < 0 for x in fri)/len(fri)*100:.0f}%, n {len(fri)}) против остальных {S.mean(oth)*100:+.2f}%, t {tstat(fri, oth):+.2f} | "
                 f"пт 1-я пол. {S.mean(by1[4])*100:+.2f}% 2-я {S.mean(by2[4])*100:+.2f}%")
    # H3 extreme fear → wait for Friday close vs buy now
    eps, last = [], -99
    for i, d in enumerate(days):
        v = fg.get(d)
        if v is not None and v <= 15:
            if i - last >= 14:
                eps.append(i)
            last = i
    for c in ("BTC", "ALT"):
        for h in (30, 90):
            now_, fri_, gain = [], [], []
            for i in eps:
                j = next((k for k in range(i, min(i + 7, len(days))) if datetime.strptime(days[k], "%Y-%m-%d").weekday() == 4), None)
                if j is None:
                    continue
                a, b = series(c, i, h + (j - i)), series(c, j, h)
                if a is None or b is None:
                    continue
                now_.append(a)
                fri_.append(b)
                gain.append(b - a)
            note(f"H3 {c} {h}d", f"купить сразу: {stats(now_)} | ждать пятницу: {stats(fri_)} | разница ср {S.mean(gain)*100:+.2f} п.п., пятница лучше в {sum(g > 0 for g in gain)}/{len(gain)}" if gain else "n 0")
    i = len(days) - 1
    note("now", f"{days[i]}: F&G {fg.get(days[i])}, BTC {btc[i]:.0f}, SMA200 {sma[i]:.0f}")


if __name__ == "__main__":
    main()
