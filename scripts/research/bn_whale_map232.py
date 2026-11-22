"""Backlog 232, 22.11.2026: audit of the coin mapping exchange → Binance USDT-M used by every research copy test
(bn_whale_*.py builds the Binance symbol as coin.upper() + "USDT"). Research only — reads ws_trips (src bn + okx) from the whale
study's database (Actions cache), writes nothing to it.

Trigger: backlog 228 found OKX trader okx:7E27C9B14BB5D378 at +30.8 % per decision in the Binance-candle copy — implausible.

Pre-registered before any result was seen (22.11 03:20 Athens):
- Window as in backlog 228: decisions opened in the last 60 days before the newest closed trip; all traders (no ≥ 20 filter here,
  this is a data audit, not a selection test).
- For every (src, coin): the trader's own entry price vs the open of the Binance 1m bar at the trader's open time, per trip.
  ratio = binance_open / trader_entry. A coin is MISMAPPED when the median |ratio − 1| over its trips > 3 %, MISSING when the
  direct symbol has no Binance candles.
- For each mismapped or missing coin try the candidates  1000<C>, 1000000<C>, <C without 1000/1000000 prefix>, k<C> → 1000<C>,
  and accept the one whose median |ratio·mult − 1| < 1 % (mult = the prefix factor), else «no match».
- Report: list of mismapped / missing coins with trips and decisions affected; for okx:7E27C9B14BB5D378 every decision (coin, side,
  trader return, copy at 2 min with the direct symbol); copy per decision at 2 min for that trader and for all OKX traders with
  mismapped coins dropped (the corrected number research should have used).
- Verdict (fixed in advance): if mismapped coins change the 2-min copy of any trader of the lock (following / passing) by more than
  0.1 % per decision or flip its sign → «исправить сопоставление в исследованиях и пересчитать»; otherwise «на выводы не влияет,
  добавить фильтр соответствия цены в общий загрузчик».
Usage: bn_whale_map232.py DB OUT.json"""
import bisect, json, os, sqlite3, statistics as st, sys, time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_delay as D  # noqa: E402

M1, H, DAY = D.M1, D.H, D.DAY
WINDOW = 60 * DAY
TARGET = "okx:7E27C9B14BB5D378"


def load_lock():
    try:
        w = json.load(open("web/whales_top.json"))
    except Exception:  # noqa: BLE001
        return set(), set()
    fol = {f.get("addr") for f in w.get("following") or [] if isinstance(f, dict)}
    lk = w.get("follow_lock") or {}
    pas = {p.get("addr") if isinstance(p, dict) else p for p in (lk.get("passing") or [])} if isinstance(lk, dict) else set()
    return fol, pas


def bar_open(S, t):
    T = S["t"]
    i = bisect.bisect_left(T, t)
    if i >= len(T) or T[i] - t > 2 * M1:
        return None
    return S["k"][T[i]][0]


def candidates(c):
    out = []
    for p, m in (("1000000", 1e6), ("1000", 1e3)):
        if c.startswith(p):
            out.append((c[len(p):], 1 / m))
        else:
            out.append((p + c, m))
    if c.startswith("K") and len(c) > 2:
        out.append(("1000" + c[1:], 1.0))
    return out


def main():
    db, out = sys.argv[1], sys.argv[2]
    fol, pas = load_lock()
    lock = fol | pas
    c = sqlite3.connect(db)
    rows = c.execute("SELECT src, trader, coin, side, open_t, close_t, entry, exit, notional FROM ws_trips "
                     "WHERE src IN ('bn','okx') AND close_t > 0 AND coin IS NOT NULL AND coin != '' ORDER BY src, trader, open_t").fetchall()
    if not rows:
        json.dump({"error": "no rows"}, open(out, "w")); print("::error::no rows"); return
    t_end = max(r[5] for r in rows)
    since = t_end - WINDOW
    rows = [r for r in rows if r[4] >= since and r[6]]
    by_coin = defaultdict(list)
    for r in rows:
        by_coin[(r[0], r[2].upper())].append(r)
    print(f"trips {len(rows)}, (src, coin) {len(by_coin)}", flush=True)
    cache = {}

    def S_of(sym, spans):
        if sym not in cache:
            cache[sym] = D.load_sym(sym, spans)
        return cache[sym]

    audit, fixmap = [], {}
    for n, ((src, coin), lst) in enumerate(sorted(by_coin.items(), key=lambda kv: -len(kv[1]))):
        spans = [(r[4], r[4] + 5 * M1) for r in lst]
        S = S_of(coin + "USDT", spans)
        rat = [bar_open(S, r[4]) / r[6] for r in lst if S and bar_open(S, r[4])] if S else []
        dev = st.median([abs(x - 1) for x in rat]) * 100 if rat else None
        state = "missing" if not rat else ("mismapped" if dev > 3 else "ok")
        rec = {"src": src, "coin": coin, "trips": len(lst), "traders": len({r[1] for r in lst}), "state": state,
               "median_dev_pct": None if dev is None else round(dev, 2),
               "median_ratio": round(st.median(rat), 5) if rat else None}
        if state != "ok":
            for cand, mult in candidates(coin):
                S2 = S_of(cand + "USDT", spans)
                if not S2:
                    continue
                r2 = [bar_open(S2, r[4]) / (r[6] * mult) for r in lst if bar_open(S2, r[4])]
                if r2 and st.median([abs(x - 1) for x in r2]) * 100 < 1:
                    rec["match"] = cand + "USDT"; rec["mult"] = mult; fixmap[(src, coin)] = cand + "USDT"
                    break
            else:
                rec["match"] = None
            audit.append(rec)
        if n % 25 == 0:
            print(f"audit {n + 1}/{len(by_coin)} {src}:{coin} {state}", flush=True)
    bad = {(a["src"], a["coin"]) for a in audit if a["state"] == "mismapped"}
    # decisions (same grouping as backlog 228) and the 2-min copy, direct mapping vs corrected (mismapped dropped / remapped)
    by = defaultdict(list)
    for r in rows:
        tid = r[1] if str(r[1]).startswith(r[0] + ":") else f"{r[0]}:{r[1]}"
        by[(r[0], tid)].append(r)
    per = {}
    target_rows = []
    for (src, tid), lst in by.items():
        cur, mine = {}, []
        for r in lst:
            k = (r[2], r[3]); e = cur.get(k)
            if e and r[4] - e["open_t"] <= 4 * H:
                e["g"].append(r)
            else:
                e = {"coin": r[2].upper(), "side": r[3], "open_t": r[4], "g": [r]}; cur[k] = e; mine.append(e)
        direct, fixed = [], []
        for e in mine:
            e["close_t"] = max(x[5] for x in e["g"])
            s = 1 if e["side"] == "LONG" else -1
            w = [max(x[8] or 0, 1e-9) for x in e["g"]]
            wret = sum(s * (x[7] / x[6] - 1) * 100 * wi for x, wi in zip(e["g"], w) if x[6] and x[7]) / sum(w)
            sp = [(e["open_t"], e["close_t"])]
            Sd = S_of(e["coin"] + "USDT", sp)
            vd = D.copy(Sd, e["side"], e["open_t"] + 2 * M1, e["close_t"] + 2 * M1) if Sd else None
            vd = vd if isinstance(vd, float) else None
            if vd is not None:
                direct.append(vd)
            key = (src, e["coin"])
            if key in bad:
                sym = fixmap.get(key)
                Sf = S_of(sym, sp) if sym else None
                vf = D.copy(Sf, e["side"], e["open_t"] + 2 * M1, e["close_t"] + 2 * M1) if Sf else None
                vf = vf if isinstance(vf, float) else None
            else:
                vf = vd
            if key in fixmap and key not in bad:  # missing directly, found under a prefix
                Sf = S_of(fixmap[key], sp)
                vf = D.copy(Sf, e["side"], e["open_t"] + 2 * M1, e["close_t"] + 2 * M1) if Sf else None
                vf = vf if isinstance(vf, float) else None
            if vf is not None:
                fixed.append(vf)
            if tid == TARGET:
                target_rows.append({"t": time.strftime("%Y-%m-%d %H:%M", time.gmtime(e["open_t"] / 1000)), "coin": e["coin"],
                                    "side": e["side"], "trader_ret": round(wret, 2), "copy_direct": None if vd is None else round(vd, 2),
                                    "copy_fixed": None if vf is None else round(vf, 2), "mismapped": key in bad})
        if direct or fixed:
            per[tid] = {"src": src, "n_direct": len(direct), "mean_direct": round(st.mean(direct), 3) if direct else None,
                        "n_fixed": len(fixed), "mean_fixed": round(st.mean(fixed), 3) if fixed else None,
                        "sum_direct": round(sum(direct), 2), "sum_fixed": round(sum(fixed), 2), "lock": tid in lock}
    changed = []
    for tid, p in per.items():
        if p["mean_direct"] is None or p["mean_fixed"] is None:
            if p["n_direct"] != p["n_fixed"]:
                changed.append(tid)
            continue
        if abs(p["mean_fixed"] - p["mean_direct"]) > 0.1 or (p["sum_direct"] > 0) != (p["sum_fixed"] > 0):
            changed.append(tid)
    lock_changed = [t for t in changed if per[t]["lock"]]
    verdict = ("исправить сопоставление в исследованиях и пересчитать" if lock_changed
               else "на выводы не влияет, добавить фильтр соответствия цены в общий загрузчик")
    O = {"window": [time.strftime("%Y-%m-%d", time.gmtime(since / 1000)), time.strftime("%Y-%m-%d", time.gmtime(t_end / 1000))],
         "trips": len(rows), "coins": len(by_coin),
         "mismapped": [a for a in audit if a["state"] == "mismapped"],
         "missing": [a for a in audit if a["state"] == "missing"],
         "target": {"id": TARGET, "per": per.get(TARGET), "decisions": target_rows},
         "changed_traders": {t: per[t] for t in changed}, "lock_changed": lock_changed, "verdict": verdict}
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    mm = ", ".join(f"{a['src']}:{a['coin']} dev {a['median_dev_pct']}% ×{a['trips']} → {a.get('match')}" for a in O["mismapped"][:12])
    ms = ", ".join(f"{a['src']}:{a['coin']}×{a['trips']}→{a.get('match')}" for a in O["missing"][:15])
    tp = per.get(TARGET) or {}
    print(f"::notice title=map232 mismapped::{len(O['mismapped'])} coins: {mm}")
    print(f"::notice title=map232 missing::{len(O['missing'])} coins (trips {sum(a['trips'] for a in O['missing'])}): {ms}")
    top = sorted(target_rows, key=lambda r: -(r['copy_direct'] or 0))[:6]
    print(f"::notice title=map232 target::{TARGET} direct n {tp.get('n_direct')} mean {tp.get('mean_direct')} → fixed n {tp.get('n_fixed')} "
          f"mean {tp.get('mean_fixed')}; top: " + "; ".join(f"{r['t']} {r['coin']} {r['side']} trader {r['trader_ret']} copy {r['copy_direct']} mm {r['mismapped']}" for r in top))
    ch = "; ".join(f"{t} {per[t]['mean_direct']}→{per[t]['mean_fixed']} (n {per[t]['n_direct']}→{per[t]['n_fixed']}, lock {per[t]['lock']})" for t in changed[:10])
    print(f"::notice title=map232 verdict::{verdict}; changed {len(changed)} traders, lock {len(lock_changed)}: {ch}")


if __name__ == "__main__":
    main()
