"""Experiment factory backtests (owner 22.11.2026 16:48): the «obvious things, maybe reversed» on BTC — RESEARCH ONLY.
Pre-registered (22.11 ~18:40 Athens). BTCUSDT perpetual (Binance archive) 4h bars 2022-01 → now, 0.11 % a round trip (funding not
included here — BTC funding over these holds is small next to the moves; stated as a limit).
  panic_buy_7d / _30d    buy at the next daily open after a Fear & Greed close ≤ 10 (alternative.me), hold 7 / 30 days; the mirror
                         «panic_sell» is the negative of the same trades
  fear20_buy_7d           the same with ≤ 20
  brk_follow / brk_fade  a 4h close above the 20-bar high → long (follow) / short (fade) at the next bar's open, hold 24 h, stop 4 %;
                         below the 20-bar low the opposite
  mon_long / fri_long    long at Monday / Friday 00:00 UTC, hold 24 h;  usopen_long  long at 12:00 UTC (the 4h bar holding the US
                         open), exit 20:00 UTC, weekdays
Report: n, win, PF, mean, t, by year. Multiple-testing bar for «worth a forward spec with real weight»: t ≥ 3 and plus in ≥ 3 of the
5 years. Usage: exp_btc_events.py OUT.json"""
import io, json, math, statistics as st, sys, time, urllib.request, zipfile

ARC = "https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/4h"
H, DAY, COST = 3_600_000, 86_400_000, 0.11


def get(url):
    for k in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "cnq-research"}), timeout=90) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
        except Exception:  # noqa: BLE001
            time.sleep(2 * (k + 1))
    return None


def bars():
    out = {}
    y, m = 2022, 1
    now = time.gmtime()
    while (y, m) < (now.tm_year, now.tm_mon):
        b = get(f"{ARC}/BTCUSDT-4h-{y}-{m:02d}.zip")
        if b:
            z = zipfile.ZipFile(io.BytesIO(b))
            for ln in z.read(z.namelist()[0]).decode().splitlines():
                p = ln.split(",")
                if p and p[0][:1].isdigit():
                    out[int(p[0])] = (float(p[1]), float(p[2]), float(p[3]), float(p[4]))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    T = sorted(out)
    return T, out


def stat(tr):
    xs = [x for _, x in tr]
    if not xs:
        return {"n": 0}
    pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    sd = st.stdev(xs) if len(xs) > 1 else 0
    by = {}
    for t, x in tr:
        y = time.gmtime(t / 1000).tm_year
        by[y] = round(by.get(y, 0) + x, 1)
    t_ = round(st.mean(xs) / (sd / math.sqrt(len(xs))), 2) if sd else None
    return {"n": len(xs), "win": round(sum(x > 0 for x in xs) / len(xs), 3), "pf": round(pos / neg, 2) if neg else None,
            "mean": round(st.mean(xs), 3), "t": t_, "by_year": by, "bar": bool((t_ or 0) >= 3 and sum(v > 0 for v in by.values()) >= 3)}


def hold(T, K, i, side, n_bars, stop=None):
    a = K[T[i]][0]; s = 1 if side == "LONG" else -1
    j = min(i + n_bars, len(T) - 1)
    for q in range(i, j):
        o, h, l, c = K[T[q]]
        if stop and ((s > 0 and l <= a * (1 - stop / 100)) or (s < 0 and h >= a * (1 + stop / 100))):
            return -stop - COST
    return s * (K[T[j]][0] / a - 1) * 100 - COST


def main():
    T, K = bars()
    idx = {t: i for i, t in enumerate(T)}
    print(f"BTC 4h bars {len(T)}", flush=True)
    fg = json.loads(get("https://api.alternative.me/fng/?limit=0&format=json") or b"{}").get("data") or []
    fg = sorted((int(x["timestamp"]) * 1000, int(x["value"])) for x in fg)
    R = {k: [] for k in ("panic_buy_7d", "panic_buy_30d", "fear20_buy_7d", "brk_follow", "brk_fade", "mon_long", "fri_long", "usopen_long")}
    last = {}
    for t, v in fg:
        i = idx.get(t + DAY)
        if i is None:
            continue
        for k, thr, nb in (("panic_buy_7d", 10, 42), ("panic_buy_30d", 10, 180), ("fear20_buy_7d", 20, 42)):
            if v <= thr and t - last.get(k, 0) >= nb * 4 * H:
                R[k].append((t, hold(T, K, i, "LONG", nb))); last[k] = t
    nxt = 0
    for i in range(21, len(T) - 7):
        c = K[T[i]][3]
        hi = max(K[T[q]][1] for q in range(i - 20, i)); lo = min(K[T[q]][2] for q in range(i - 20, i))
        if i >= nxt and (c > hi or c < lo):
            side = "LONG" if c > hi else "SHORT"
            R["brk_follow"].append((T[i], hold(T, K, i + 1, side, 6, 4)))
            R["brk_fade"].append((T[i], hold(T, K, i + 1, "SHORT" if side == "LONG" else "LONG", 6, 4)))
            nxt = i + 6
        g = time.gmtime(T[i] / 1000)
        if g.tm_hour == 0 and g.tm_wday == 0:
            R["mon_long"].append((T[i], hold(T, K, i, "LONG", 6)))
        if g.tm_hour == 0 and g.tm_wday == 4:
            R["fri_long"].append((T[i], hold(T, K, i, "LONG", 6)))
        if g.tm_hour == 12 and g.tm_wday < 5:
            R["usopen_long"].append((T[i], hold(T, K, i, "LONG", 2)))
    O = {k: stat(v) for k, v in R.items()}
    O["panic_sell_7d"] = stat([(t, -x - 2 * COST) for t, x in R["panic_buy_7d"]])
    json.dump(O, open(sys.argv[1], "w"), ensure_ascii=False, indent=1)
    ks = sorted(O)
    for i in range(0, len(ks), 3):
        print(f"::notice title=btc events {i // 3 + 1}::" + " || ".join(k + " " + json.dumps(O[k]) for k in ks[i:i + 3]))


if __name__ == "__main__":
    main()
