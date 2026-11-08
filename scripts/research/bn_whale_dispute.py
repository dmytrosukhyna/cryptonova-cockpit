"""Backlog 112, Binance half (08.11.2026, research only): copies when ANOTHER lead trader holds the same coin the other way.
Reads ws_trips (src='bn') from the whale study's database (Actions cache), writes nothing to it. Same pre-registered design as
whale_dispute.py (OKX/HL half, written before any result was seen):
- decision = one trader's entries in the same coin + side within 4 h (as bn_whale_regime.py); population = decisions of traders that
  pass the live 60-day bar at the open (>= 20 prior closed decisions in 60 days, win >= 70 %, PF >= 2, P&L > 0; decision P&L =
  sum of side * (exit / entry − 1) * notional);
- copy: entry 2 min after the open, exit 2 min after the last close, stop −16 %, 0.11 % + funding (1-minute candles, bn_whale_delay.copy);
- at the open t0, other traders' decisions open at t0 (open < t0 < close), same coin:
  D_bar = another bar-passing trader holds the OPPOSITE side; D_any = any other trader holds the opposite side;
  agree (reported only) = another bar-passing trader holds the SAME side;
- pass (2 looks): disputed mean < 0 in both halves, disputed − rest < 0 in both halves, Welch t <= −2.5 on all.
- known bias (backlog 104): ws_trips holds closed positions only.
Usage: bn_whale_dispute.py DB OUT.json"""
import json, math, os, sqlite3, statistics as st, sys, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_delay as B

M1, H, DAY = 60_000, 3_600_000, 86_400_000


def welch(a, b):
    if len(a) < 2 or len(b) < 2:
        return None
    se = math.sqrt(st.pvariance(a) / len(a) + st.pvariance(b) / len(b))
    return round((st.mean(a) - st.mean(b)) / se, 2) if se else None


def main():
    db, out = sys.argv[1], sys.argv[2]
    c = sqlite3.connect(db)
    rows = c.execute("SELECT trader, coin, side, open_t, close_t, entry, exit, notional FROM ws_trips WHERE src='bn' AND close_t > 0 "
                     "ORDER BY trader, open_t").fetchall()
    by = defaultdict(list)
    for r in rows:
        by[r[0]].append(r)
    decs = []
    for tr, lst in by.items():
        cur, mine = {}, []
        for r in lst:
            k = (r[1], r[2]); e = cur.get(k)
            if e and r[3] - e["open_t"] <= 4 * H:
                e["g"].append(r)
            else:
                e = {"trader": tr, "coin": r[1], "side": r[2], "open_t": r[3], "g": [r]}; cur[k] = e; mine.append(e)
        for e in mine:
            e["close_t"] = max(x[4] for x in e["g"])
            s = 1 if e["side"] == "LONG" else -1
            e["pnl"] = sum(s * (x[6] / x[5] - 1) * (x[7] or 0) for x in e["g"] if x[5] and x[6])
        mine.sort(key=lambda e: e["open_t"])
        for e in mine:
            t0 = e["open_t"]
            pri = [d for d in mine if d["close_t"] < t0 and d["open_t"] >= t0 - 60 * DAY]
            bar = False
            if len(pri) >= 20:
                wins = sum(1 for d in pri if d["pnl"] > 0); gp = sum(d["pnl"] for d in pri if d["pnl"] > 0)
                gl = -sum(d["pnl"] for d in pri if d["pnl"] < 0)
                bar = wins / len(pri) >= 0.70 and (gp / gl if gl > 0 else 99) >= 2 and gp - gl > 0
            e["bar"] = bar
        decs += mine
    by_coin = defaultdict(list)
    for d in decs:
        by_coin[d["coin"]].append(d)
    eps = [e for e in decs if e["bar"] and e["close_t"] - e["open_t"] > 2 * M1]
    print(f"bn rows {len(rows)}, decisions {len(decs)}, passing the bar {len(eps)}", flush=True)
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
        if not S:
            continue
        v = B.copy(S, e["side"], e["open_t"] + 2 * M1, e["close_t"] + 2 * M1)
        if v is None or not math.isfinite(v):
            continue
        t0 = e["open_t"]
        peers = [d for d in by_coin[e["coin"]] if d["trader"] != e["trader"] and d["open_t"] < t0 < d["close_t"]]
        res.append({"t": t0, "trader": e["trader"], "v": v,
                    "D_bar": any(p["bar"] and p["side"] != e["side"] for p in peers),
                    "D_any": any(p["side"] != e["side"] for p in peers),
                    "agree": any(p["bar"] and p["side"] == e["side"] for p in peers)})
    res.sort(key=lambda r: r["t"])
    if len(res) < 30:
        json.dump({"error": "few rows", "n": len(res)}, open(out, "w")); print(f"::error::few rows {len(res)}"); return
    mid = res[len(res) // 2]["t"]
    O = {"n": len(res), "traders": len({r["trader"] for r in res}),
         "from": time.strftime("%Y-%m-%d", time.gmtime(res[0]["t"] / 1000)), "to": time.strftime("%Y-%m-%d", time.gmtime(res[-1]["t"] / 1000)),
         "mid": time.strftime("%Y-%m-%d", time.gmtime(mid / 1000)),
         "share": {k: round(sum(r[k] for r in res) / len(res), 3) for k in ("D_bar", "D_any", "agree")}}
    blk = {}
    for p, f in {"all": lambda r: True, "h1": lambda r: r["t"] < mid, "h2": lambda r: r["t"] >= mid}.items():
        rs = [r for r in res if f(r)]
        q = {"base": B.stat([r["v"] for r in rs])}
        for k in ("D_bar", "D_any", "agree"):
            a, b = [r["v"] for r in rs if r[k]], [r["v"] for r in rs if not r[k]]
            q[k] = B.stat(a); q[f"no_{k}"] = B.stat(b)
            q[f"diff_{k}"] = {"mean": round(st.mean(a) - st.mean(b), 3) if len(a) > 1 and b else None, "t": welch(a, b)}
        blk[p] = q
    O["bn"] = blk
    verdict = {}
    for k in ("D_bar", "D_any"):
        h1, h2, a = blk["h1"], blk["h2"], blk["all"]
        verdict[k] = bool(all(x[k].get("mean") is not None and x[k]["mean"] < 0 for x in (h1, h2))
                          and all(x[f"diff_{k}"]["mean"] is not None and x[f"diff_{k}"]["mean"] < 0 for x in (h1, h2))
                          and a[f"diff_{k}"]["t"] is not None and a[f"diff_{k}"]["t"] <= -2.5)
    O["verdict"] = verdict
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"::notice title=dispute n::{O['n']} decisions, {O['traders']} traders, {O['from']} → {O['to']} (mid {O['mid']}), "
          f"share {O['share']}, verdict {verdict}")
    for p in ("all", "h1", "h2"):
        q = blk[p]
        line = " | ".join(f"{k}: {q[k].get('mean')}/n{q[k].get('n')}/pf{q[k].get('pf')} vs {q['no_' + k].get('mean')} "
                          f"Δ{q['diff_' + k]['mean']} t{q['diff_' + k]['t']}" for k in ("D_bar", "D_any", "agree"))
        print(f"::notice title=dispute {p}::base {q['base'].get('mean')}/n{q['base'].get('n')} | {line}")


if __name__ == "__main__":
    main()
