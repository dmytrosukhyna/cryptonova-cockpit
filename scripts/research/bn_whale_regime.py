"""Backlog 102, 07.11.2026: are lead traders' LONG copies worse when BTC is in a downtrend («спад»)? Binance half.
Research only — reads ws_trips (src='bn') from the whale study's database (Actions cache), writes nothing to it.

Pre-registered (written before any result was seen):
- decision = one trader's entries in the same coin + side within 4 h (as in bn_whale_delay.py), trader ≥ 20 decisions;
- BTC regime at the whale's open, from BTCUSDT daily closes of days completed BEFORE the open (no look-ahead):
  R1 «SMA50»: last close above / below the 50-day simple average;
  R2 «тренд 28 д»: 28-day return > 0 / ≤ 0 (the same r28 that terminal.json's mid regime uses);
- copy: entry d = 2 and 6 min after the whale's open, exit d after his last close, stop −16 %, 0.11 % a round trip + funding
  (1-minute candles, bn_whale_delay.copy);
- cells: side (LONG / SHORT) × regime (up / down) → mean / t / PF / win, both halves of time;
- verdict «longs in a downtrend are worse» (2 regimes × 2 delays = 4 looks): for some (R, d) long copies in «down» have
  mean ≤ 0 in BOTH halves AND the difference long-down − long-up < 0 in both halves with Welch t ≤ −2.5 on all decisions.
  If passed — only a candidate (half risk or skip for long copies in «спад»), money rule only with Dmytro's yes.
- known bias (backlog 104): ws_trips holds closed positions only, still-open long losers drop out.
Usage: bn_whale_regime.py DB OUT.json"""
import bisect, json, math, os, sqlite3, statistics as st, sys, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_delay as B

M1, H, DAY = 60_000, 3_600_000, 86_400_000
DELAYS = [2, 6]


def btc_daily(t0, t1):
    k = {}
    y, m = time.gmtime((t0 - 80 * DAY) / 1000)[:2]
    end = time.gmtime(t1 / 1000)[:2]
    cur = time.gmtime()[:2]
    while (y, m) <= end:
        tag = f"{y}-{m:02d}"
        if (y, m) != cur:
            for p in B.csv_rows(B.get(f"{B.ARC}/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-{tag}.zip")):
                k[int(p[0])] = float(p[4])
        else:
            d = int(calendar_month_start(y, m))
            while d < time.time() * 1000 - DAY:
                s = time.strftime("%Y-%m-%d", time.gmtime(d / 1000))
                for p in B.csv_rows(B.get(f"{B.ARC}/daily/klines/BTCUSDT/1d/BTCUSDT-1d-{s}.zip")):
                    k[int(p[0])] = float(p[4])
                d += DAY
        m += 1
        if m > 12:
            y, m = y + 1, 1
    ts = sorted(k)
    return ts, [k[t] for t in ts]


def calendar_month_start(y, m):
    import calendar
    return calendar.timegm((y, m, 1, 0, 0, 0)) * 1000


def regime(ts, cs, t):
    i = bisect.bisect_right(ts, t - DAY)          # days whose candle closed before t (open + 1 day ≤ t)
    if i < 50:
        return None
    last = cs[i - 1]
    sma50 = sum(cs[i - 50:i]) / 50
    r28 = last / cs[i - 29] - 1
    return {"R1": "up" if last > sma50 else "down", "R2": "up" if r28 > 0 else "down"}


def welch(a, b):
    if len(a) < 2 or len(b) < 2:
        return None
    va, vb = st.pvariance(a), st.pvariance(b)
    se = math.sqrt(va / len(a) + vb / len(b))
    return round((st.mean(a) - st.mean(b)) / se, 2) if se else None


def main():
    db, out = sys.argv[1], sys.argv[2]
    c = sqlite3.connect(db)
    rows = c.execute("SELECT trader, coin, side, open_t, close_t, entry, exit, notional FROM ws_trips WHERE src='bn' AND close_t > 0 "
                     "ORDER BY trader, open_t").fetchall()
    by = defaultdict(list)
    for r in rows:
        by[r[0]].append(r)
    eps = []
    for tr, lst in by.items():
        cur, mine = {}, []
        for r in lst:
            k = (r[1], r[2]); e = cur.get(k)
            if e and r[3] - e["open_t"] <= 4 * H:
                e["g"].append(r)
            else:
                e = {"trader": tr, "coin": r[1], "side": r[2], "open_t": r[3], "g": [r]}; cur[k] = e; mine.append(e)
        if len(mine) >= 20:
            eps += mine
    for e in eps:
        e["close_t"] = max(x[4] for x in e["g"])
    print(f"bn rows {len(rows)}, decisions: {len(eps)}", flush=True)
    ts, cs = btc_daily(min(e["open_t"] for e in eps), max(e["close_t"] for e in eps))
    print(f"btc daily {len(ts)}", flush=True)
    syms = defaultdict(list)
    for e in eps:
        syms[e["coin"] + "USDT"].append(e)
    data = {}
    for n, (sym, es) in enumerate(sorted(syms.items(), key=lambda kv: -len(kv[1]))):
        data[sym] = B.load_sym(sym, [(x["open_t"], x["close_t"]) for x in es])
        if n % 10 == 0:
            print(f"candles {n + 1}/{len(syms)} {sym} {'ok' if data[sym] else 'none'}", flush=True)
    res = []
    for e in eps:
        S = data.get(e["coin"] + "USDT")
        rg = regime(ts, cs, e["open_t"])
        if not S or not rg:
            continue
        r = {"trader": e["trader"], "t": e["open_t"], "side": e["side"], **rg}
        ok = True
        for d in DELAYS:
            v = B.copy(S, e["side"], e["open_t"] + d * M1, e["close_t"] + d * M1)
            if v is None:
                ok = False; break
            r[f"d{d}"] = v
        if ok:
            res.append(r)
    res.sort(key=lambda r: r["t"])
    if not res:
        json.dump({"error": "no rows"}, open(out, "w")); print("::error::no rows"); return
    mid = res[len(res) // 2]["t"]
    O = {"n": len(res), "traders": len({r["trader"] for r in res}),
         "from": time.strftime("%Y-%m-%d", time.gmtime(res[0]["t"] / 1000)), "to": time.strftime("%Y-%m-%d", time.gmtime(res[-1]["t"] / 1000)),
         "mid": time.strftime("%Y-%m-%d", time.gmtime(mid / 1000)),
         "share_down": {R: round(sum(r[R] == "down" for r in res) / len(res), 3) for R in ("R1", "R2")}}
    verdict = []
    for R in ("R1", "R2"):
        for d in DELAYS:
            blk = {}
            for p, f in {"all": lambda r: True, "h1": lambda r: r["t"] < mid, "h2": lambda r: r["t"] >= mid}.items():
                q = {}
                for side in ("LONG", "SHORT"):
                    for g in ("up", "down"):
                        q[f"{side}_{g}"] = B.stat([r[f"d{d}"] for r in res if f(r) and r["side"] == side and r[R] == g])
                    lu = [r[f"d{d}"] for r in res if f(r) and r["side"] == side and r[R] == "up"]
                    ld = [r[f"d{d}"] for r in res if f(r) and r["side"] == side and r[R] == "down"]
                    q[f"{side}_diff"] = {"mean": round(st.mean(ld) - st.mean(lu), 3) if ld and lu else None, "t": welch(ld, lu)}
                blk[p] = q
            O[f"{R}_d{d}"] = blk
            a, h1, h2 = blk["all"], blk["h1"], blk["h2"]
            ok = (all((x["LONG_down"].get("mean") is not None and x["LONG_down"]["mean"] <= 0) for x in (h1, h2))
                  and all((x["LONG_diff"]["mean"] is not None and x["LONG_diff"]["mean"] < 0) for x in (h1, h2))
                  and a["LONG_diff"]["t"] is not None and a["LONG_diff"]["t"] <= -2.5)
            verdict.append({"R": R, "d": d, "passed": ok})
    O["verdict"] = verdict
    O["passed_any"] = any(v["passed"] for v in verdict)
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"::notice title=regime n::{O['n']} decisions, {O['traders']} traders, {O['from']} → {O['to']} (mid {O['mid']}), "
          f"down share {O['share_down']}, passed_any={O['passed_any']}")
    for R in ("R1", "R2"):
        for d in DELAYS:
            for p in ("all", "h1", "h2"):
                q = O[f"{R}_d{d}"][p]
                line = " ".join(f"{k}:{q[k].get('mean')}/n{q[k].get('n')}/pf{q[k].get('pf')}" for k in
                                ("LONG_up", "LONG_down", "SHORT_up", "SHORT_down"))
                print(f"::notice title={R} d{d} {p}::{line} | Ldiff {q['LONG_diff']['mean']}/t{q['LONG_diff']['t']} "
                      f"Sdiff {q['SHORT_diff']['mean']}/t{q['SHORT_diff']['t']}")


if __name__ == "__main__":
    main()
