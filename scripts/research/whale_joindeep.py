"""Backlog 196 (17.11.2026, research only): entering a trader's position when it is deep in loss (−3…−6 / −6…−10 / −10…−15 / < −15 %)
vs joining at 0…−3 %. Design pre-registered in joindeep_common.py (committed before any result). Survivorship-free set: OKX lead
traders (closed + current positions) and Hyperliquid (fills + open positions) from lab-data-whales (16.11.2026); positions still
open at the data's end are marked at the last price. Data: whale_rules_data_20261116.json.gz → whale_rules_convert.py; the study
module whale_rules_study.py (branch research/whale-rules-0411) next to this file as study.py.
Usage: whale_joindeep.py <conv>/whales.json.gz OUT.json"""
import json, math, os, sys
from collections import defaultdict
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import study as S0
import joindeep_common as JC

MIN, H, DAY, M15 = 60_000, 3_600_000, 86_400_000, 900_000
DD = S0.load_data(sys.argv[1]); now = int(DD["made"])
MDIR = os.path.join(os.path.dirname(sys.argv[1]), "mkt")
W = S0.WhaleSeries(MDIR, None)
K = {}


def k15(coin):
    if coin not in K:
        fn = os.path.join(MDIR, f"{coin}.npz")
        a = np.load(fn)["k15"] if os.path.exists(fn) else np.zeros((0, 6))
        K[coin] = (a[:, 0].astype(np.int64), a[:, 1], a[:, 2], a[:, 3], a[:, 4]) if len(a) >= 1200 else None
    return K[coin]


def px_at(k, t):
    tt, o, _, _, c = k
    j = int(np.searchsorted(tt, t, side="right") - 1)
    if j < 0 or t - tt[j] > 3 * M15:
        return None, j
    if t >= tt[j] + M15:
        return c[j], j
    return o[j] + (c[j] - o[j]) * ((t - tt[j]) / M15), j


def copy(coin, side, j0, t_exit):
    """enter at the close of 15m bar j0, exit at t_exit (2 min after his close / the data's end); stops from our entry"""
    k = k15(coin)
    tt, o, h, l, c = k
    a, t0 = c[j0], int(tt[j0]) + M15
    t_exit = min(t_exit, int(tt[-1]) + M15)
    b, j1 = px_at(k, t_exit)
    if not a or b is None or t_exit <= t0:
        return None
    s = 1 if side == "LONG" else -1
    Sx = W.get(coin)
    out, stopped = {}, {}
    for key, stp in (("s16", 16.0), ("s8", 8.0), ("none", None)):
        ex, te, hit = b, t_exit, False
        if stp is not None:
            lvl = a * (1 - s * stp / 100)
            for i in range(j0 + 1, min(j1, len(tt) - 1) + 1):
                if (s > 0 and l[i] <= lvl) or (s < 0 and h[i] >= lvl):
                    ex = o[i] if ((s > 0 and o[i] < lvl) or (s < 0 and o[i] > lvl)) else lvl
                    te, hit = int(tt[i]) + M15, True
                    break
        fund = Sx.funding_paid(side, t0, te) if Sx is not None else 0.0
        out[key] = float(s * (ex / a - 1) * 100 - S0.COST - fund)
        stopped[key] = hit
    return out, stopped


def groups(trips):                      # as whale_joindepth.py: one decision = same coin + side within 4 h, slices kept
    by = defaultdict(list)
    for t in trips:
        by[(t["coin"], t["side"])].append(t)
    out = []
    for ts in by.values():
        ts.sort(key=lambda x: x["open_t"]); cur = None
        for t in ts:
            if cur and t["open_t"] - cur[0]["open_t"] <= 4 * H: cur.append(t)
            else:
                if cur: out.append(cur)
                cur = [t]
        if cur: out.append(cur)
    res = []
    for g in out:
        res.append({"coin": g[0]["coin"], "side": g[0]["side"], "open_t": min(x["open_t"] for x in g), "close_t": max(x["close_t"] for x in g),
                    "pnl": sum(x["pnl"] for x in g), "open": any(x.get("open") for x in g), "g": g})
    return sorted(res, key=lambda x: x["open_t"])


def w_entry_at(g, t):
    xs = [(x["entry"], max(x["notional"], 1e-9)) for x in g if x["open_t"] <= t]
    return sum(a * b for a, b in xs) / sum(b for _, b in xs) if xs else None


# BTC regime: close above its 50-day SMA of daily closes (days before the entry's day)
bk = k15("BTC")
day_close = {}
for t, c in zip(bk[0], bk[4]):
    day_close[int(t) // DAY] = c
days = sorted(day_close)


def regime(t):
    d = int(t) // DAY
    prev = [day_close[x] for x in days if d - 50 <= x < d]
    if len(prev) < 45:
        return None
    p, _ = px_at(bk, t)
    return "рост" if p and p > sum(prev) / len(prev) else "спад"


whales = []
for w in DD.get("hl") or []:
    if len(w["fills"]) < 8: continue
    whales.append(("hl", w.get("name") or w["addr"][:10], S0.hl_trips(w, now)))
for t in DD.get("okx") or []:
    tr = S0.okx_trips(t, now)
    if len(tr) < 8: continue
    whales.append(("okx", t.get("name") or t["code"], tr))

rows, n_dec, n_open_dec = [], 0, 0
for src, name, trips in whales:
    done = []
    for e in groups(trips):
        t0 = e["open_t"]
        pri = [d for d in done if d[1] < t0 and d[0] >= t0 - 60 * DAY]
        bar = False
        if len(pri) >= 20:
            wins = sum(1 for d in pri if d[2] > 0); gp = sum(d[2] for d in pri if d[2] > 0); gl = -sum(d[2] for d in pri if d[2] < 0)
            bar = wins / len(pri) >= 0.70 and (gp / gl if gl > 0 else 99) >= 2 and gp - gl > 0
        done.append((t0, e["close_t"], e["pnl"]))
        k = k15(e["coin"])
        if k is None or e["close_t"] - t0 <= 2 * MIN:
            continue
        tt, _, _, _, cc = k
        close_t = min(e["close_t"], int(tt[-1]) + M15) if e["open"] else e["close_t"]
        n_dec += 1; n_open_dec += 1 if e["open"] else 0
        s = 1 if e["side"] == "LONG" else -1
        j = int(np.searchsorted(tt, t0 + 2 * MIN - M15, side="left"))     # first bar closing after his entry + 2 min
        while j < len(tt) and int(tt[j]) + M15 < t0 + 2 * MIN:
            j += 1
        got = {}
        reg = regime(t0)
        while j < len(tt) and int(tt[j]) + M15 < close_t and len(got) < len(JC.BANDS):
            tc = int(tt[j]) + M15
            we = w_entry_at(e["g"], tc)
            if we:
                gap = s * (cc[j] / we - 1) * 100
                for nm, up, lo in JC.BANDS:
                    if nm in got:
                        continue
                    ok = (lo <= gap <= up) if nm == "B0" else (gap <= up)
                    if ok:
                        got[nm] = (j, float(gap), bool(lo is not None and nm != "B0" and gap < lo))
            j += 1
        for nm, (jj, gap, gapped) in got.items():
            v = copy(e["coin"], e["side"], jj, close_t + 2 * MIN)
            if v is None or not all(math.isfinite(x) for x in v[0].values()):
                continue
            rows.append({"src": src, "trader": name, "dec": f"{src}|{name}|{t0}|{e['coin']}|{e['side']}", "t": t0, "band": nm, "gap": round(gap, 3),
                         "gapped": gapped, "bar": bar, "open": e["open"], "regime": reg, "cls": "BTC/ETH" if e["coin"] in ("BTC", "ETH") else "alts",
                         "ret": v[0], "stopped": v[1], "wait_h": round((int(tt[jj]) + M15 - t0) / H, 2)})

O = {"made": now, "decisions": n_dec, "decisions_open_at_end": n_open_dec, "rows": len(rows)}
prim = [r for r in rows if r["bar"]]
O["primary"] = JC.verdict(prim)
O["primary_stops"] = {k: JC.block(prim, k) for k in ("s16", "s8", "none")}
O["all_traders"] = JC.verdict(rows)
O["no_bar"] = JC.verdict([r for r in rows if not r["bar"]])
O["closed_only_primary"] = JC.verdict([r for r in prim if not r["open"]])      # what a closed-only archive would show (bias check)
for key, f in (("src_okx", lambda r: r["src"] == "okx"), ("src_hl", lambda r: r["src"] == "hl"), ("rost", lambda r: r["regime"] == "рост"),
               ("spad", lambda r: r["regime"] == "спад"), ("btc_eth", lambda r: r["cls"] == "BTC/ETH"), ("alts", lambda r: r["cls"] == "alts")):
    O["primary_" + key] = JC.block([r for r in prim if f(r)])
    O["all_" + key] = JC.block([r for r in rows if f(r)])
O["by_trader_primary"] = {}
for nm, _, _ in JC.BANDS[1:]:
    c = defaultdict(list)
    for r in prim:
        if r["band"] == nm:
            c[r["trader"]].append(r["ret"]["s16"])
    O["by_trader_primary"][nm] = sorted(([k, len(v), round(sum(v), 1)] for k, v in c.items()), key=lambda x: -x[1])[:8]
O["wait_h_median"] = {nm: (sorted(r["wait_h"] for r in prim if r["band"] == nm) or [None])[len([r for r in prim if r["band"] == nm]) // 2]
                      for nm, _, _ in JC.BANDS}
json.dump(O, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
json.dump(rows, open(sys.argv[2] + ".rows.json", "w"), ensure_ascii=False)
v = O["primary"]
print("decisions", n_dec, "open at end", n_open_dec, "rows", len(rows), "primary rows", len(prim), "verdict:", v["verdict"])
for h in ("all", "h1", "h2"):
    print(h, {b: (x.get("n"), x.get("mean"), x.get("median"), x.get("win"), x.get("t_vs_B0")) for b, x in v.get(h, {}).items()})
print(v.get("bands"))
