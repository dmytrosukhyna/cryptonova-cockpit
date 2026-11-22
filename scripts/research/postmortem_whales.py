"""Post-mortem of the whale desk and the real account's futures since 04.11.2026 (owner 22.11 17:19). RESEARCH ONLY.
Pre-registered (22.11 ~19:10 Athens) — what is measured and how each cause is assigned, before any number was seen:
Data: the paper whale desk's closed copies (web/whales_live.json desk.closed, every field the desk keeps: trader, coin, side, our
entry, the trader's entry, gap, late join, our / his leverage, exit reason, P/L) and the real account's closed swaps since 04.11
(docs/research/postmortem_real_closed_20261122.json, collected from the public feed's history, with the desk-trade id «sig»).
Context per copy (no look-ahead, Binance 1m bars): BTC 4h EMA20 regime at the entry (last completed bar), session (UTC: Asia 0–8,
Europe 8–13, US 13–21, late 21–24), number of the desk's copies open on the same side at the entry, the trader's OWN result for
the decision (ws_trips: his trip of that coin + side opened within 6 h of our entry; his exit / our entry, side-adjusted).
Cause of a losing copy (first that applies):
  1 stop_mismatch  our −16 % stop fired and the trader's own decision ended better than −16 % (or in profit)
  2 rule_exit      our own rule closed it (fast exit, unfollowed, flip) and the trader's decision ended better than our exit
  3 lag            the trader's decision was in profit but ours lost (entry gap / exit lag)
  4 crowded        ≥ 3 desk copies open on the same side at the entry (a correlated basket), trader lost too
  5 regime         against the BTC 4h regime (alt long below a falling EMA20 / alt short above a rising one), trader lost too
  6 market         the trader lost as well — no rule of ours to blame
Fix backtests on the same copies (1m bars from our entry to the desk's exit): stop none / −25 % / −8 %; skip against regime;
cap 2 open copies a side; skip late joins / gap > 0.5 %; hold to the trader's own exit instead of our rule exits.
Usage: postmortem_whales.py DB OUT.json"""
import bisect, json, os, sqlite3, statistics as st, sys, time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_delay as D  # noqa: E402
import exp_variants as EV  # noqa: E402

M1, H, DAY = D.M1, D.H, D.DAY
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
SINCE = 1793826000000                       # 04.11.2026 21:00 UTC — the whale era of the real account


def session(t):
    h = time.gmtime(t / 1000).tm_hour
    return "asia" if h < 8 else "eu" if h < 13 else "us" if h < 21 else "late"


def walk(S, side, t_in, t_out, px_out, stop):
    """our copy from t_in to t_out (exit at px_out unless the stop fires first) → % of notional (no costs)"""
    T = S["t"]; i, j = bisect.bisect_left(T, t_in), bisect.bisect_left(T, t_out)
    if i >= len(T):
        return None
    s = 1 if side == "LONG" else -1
    a = S["k"][T[i]][0]
    if stop:
        lvl = a * (1 - s * stop / 100)
        for q in range(i, min(j, len(T))):
            o, h, l = S["k"][T[q]]
            if (s > 0 and l <= lvl) or (s < 0 and h >= lvl):
                return -stop
    return s * (px_out / a - 1) * 100


def main():
    db, out = sys.argv[1], sys.argv[2]
    desk = json.load(open(os.path.join(ROOT, "web", "whales_live.json")))["desk"]
    real = json.load(open(os.path.join(ROOT, "docs", "research", "postmortem_real_closed_20261122.json")))
    cl = [c for c in desk["closed"] if c.get("opened_t") and c.get("closed_t") and c.get("entry") and c.get("exit")]
    c = sqlite3.connect(db)
    trips = defaultdict(list)
    for r in c.execute("SELECT src, trader, coin, side, open_t, close_t, entry, exit FROM ws_trips WHERE close_t > 0"):
        tid = r[1] if str(r[1]).startswith(r[0] + ":") else f"{r[0]}:{r[1]}"
        trips[(tid, str(r[2]).upper(), r[3])].append(r)
    need = defaultdict(list)
    for x in cl:
        need[x["coin"].upper() + "USDT"].append((x["opened_t"] - H, x["closed_t"] + 10 * DAY))
    need["BTCUSDT"].append((min(x["opened_t"] for x in cl) - 10 * DAY, max(x["closed_t"] for x in cl)))
    data = {s: D.load_sym(s, sp) for s, sp in need.items()}
    R = EV.btc_regime(data["BTCUSDT"]) if data.get("BTCUSDT") else {}
    rows = []
    for x in sorted(cl, key=lambda x: x["opened_t"]):
        coin, side, s = x["coin"].upper(), x["side"], (1 if x["side"] == "LONG" else -1)
        ours = s * (x["exit"] / x["entry"] - 1) * 100
        same = sum(1 for y in cl if y is not x and y["side"] == side and y["opened_t"] <= x["opened_t"] < y["closed_t"])
        reg_ok = EV.regime_ok(R, coin, side, x["opened_t"])
        tr = [r for r in trips.get((x["whale"], coin, side), []) if abs(r[4] - x["opened_t"]) <= 6 * H]
        his = None
        if tr:
            r = min(tr, key=lambda r: abs(r[4] - x["opened_t"]))
            his = {"ret": s * (r[7] / r[6] - 1) * 100 if r[6] and r[7] else None, "close_t": r[5], "exit": r[7]}
        why = str(x.get("why") or "")
        rule = not why.startswith("кит закрыл") and not why.startswith("стоп")
        stopped = why.startswith("стоп")
        cause = None
        if x["pnl"] < 0:
            h = (his or {}).get("ret")
            if stopped and h is not None and h > -16:
                cause = "stop_mismatch"
            elif rule and h is not None and h > ours:
                cause = "rule_exit"
            elif h is not None and h > 0:
                cause = "lag"
            elif same >= 2:
                cause = "crowded"
            elif not reg_ok:
                cause = "regime"
            else:
                cause = "market"
        S = data.get(coin + "USDT")
        fx = {}
        if S:
            for k, stp in (("stop_none", None), ("stop25", 25.0), ("stop8", 8.0)):
                v = walk(S, side, x["opened_t"], x["closed_t"], x["exit"], stp)
                fx[k] = None if v is None else v - 0.11
            if rule and his and his.get("exit") and his["close_t"] > x["closed_t"]:
                v = walk(S, side, x["opened_t"], his["close_t"], his["exit"], 16.0)
                fx["hold_to_trader"] = None if v is None else v - 0.11
        rows.append({"t": x["opened_t"], "closed_t": x["closed_t"], "whale": x["whale"], "coin": coin, "side": side, "pnl": x["pnl"],
                     "ours_pct": round(ours - 0.11, 3), "margin": x.get("margin"), "notional": x.get("notional"), "gap": x.get("gap_pct"),
                     "join": bool(x.get("join")), "lev": x.get("lev"), "whale_lev": x.get("whale_lev"), "why": why[:60], "same_side": same,
                     "regime_ok": reg_ok, "session": session(x["opened_t"]), "his": his, "cause": cause, "fx": fx})
    last14 = max(r["closed_t"] for r in rows) - 14 * DAY
    O = {"copies": len(rows), "since_0411": sum(1 for r in rows if r["t"] >= SINCE), "last14": sum(1 for r in rows if r["closed_t"] >= last14)}

    def pf(xs):
        p, n = sum(a for a in xs if a > 0), -sum(a for a in xs if a < 0)
        return round(p / n, 2) if n else None

    for win, sel in (("since_0411", [r for r in rows if r["t"] >= SINCE]), ("last14", [r for r in rows if r["closed_t"] >= last14])):
        by = defaultdict(lambda: {"n": 0, "usd": 0.0})
        for r in sel:
            if r["cause"]:
                by[r["cause"]]["n"] += 1; by[r["cause"]]["usd"] = round(by[r["cause"]]["usd"] + r["pnl"], 2)
        base = [r["ours_pct"] for r in sel]
        fixes = {"base": {"n": len(base), "sum_pct": round(sum(base), 1), "pf": pf(base)}}
        for k in ("stop_none", "stop25", "stop8"):
            v = [r["fx"].get(k) if r["fx"].get(k) is not None else r["ours_pct"] for r in sel]
            fixes[k] = {"n": len(v), "sum_pct": round(sum(v), 1), "pf": pf(v)}
        v = [r["ours_pct"] for r in sel if r["regime_ok"]]
        fixes["skip_against_regime"] = {"n": len(v), "sum_pct": round(sum(v), 1), "pf": pf(v)}
        v = [r["ours_pct"] for r in sel if r["same_side"] < 2]
        fixes["cap2_same_side"] = {"n": len(v), "sum_pct": round(sum(v), 1), "pf": pf(v)}
        v = [r["ours_pct"] for r in sel if not r["join"] and abs(r["gap"] or 0) <= 0.5]
        fixes["skip_late_or_gap"] = {"n": len(v), "sum_pct": round(sum(v), 1), "pf": pf(v)}
        v = [r["fx"].get("hold_to_trader", r["ours_pct"]) for r in sel]
        fixes["hold_to_trader_exit"] = {"n": len(v), "sum_pct": round(sum(v), 1), "pf": pf(v),
                                        "changed": sum(1 for r in sel if "hold_to_trader" in r["fx"])}
        ses = defaultdict(list)
        for r in sel:
            ses[r["session"]].append(r["pnl"])
        O[win] = {"pnl_usd": round(sum(r["pnl"] for r in sel), 2), "pf_usd": pf([r["pnl"] for r in sel]),
                  "causes": dict(sorted(by.items(), key=lambda kv: kv[1]["usd"])), "fixes": fixes,
                  "sessions": {k: {"n": len(v), "usd": round(sum(v), 1)} for k, v in ses.items()},
                  "longs_usd": round(sum(r["pnl"] for r in sel if r["side"] == "LONG"), 1),
                  "shorts_usd": round(sum(r["pnl"] for r in sel if r["side"] == "SHORT"), 1),
                  "trader_lost_too": sum(1 for r in sel if r["pnl"] < 0 and (r["his"] or {}).get("ret") is not None and r["his"]["ret"] < 0),
                  "trader_won_we_lost": sum(1 for r in sel if r["pnl"] < 0 and (r["his"] or {}).get("ret") is not None and r["his"]["ret"] > 0),
                  "no_trader_match": sum(1 for r in sel if r["pnl"] < 0 and not r["his"])}
    rr = [x for x in real if x.get("closed", 0) >= SINCE]
    O["real"] = {"n": len(rr), "usd": round(sum(x.get("pnl_usd") or 0 for x in rr), 2), "pf": pf([x.get("pnl_usd") or 0 for x in rr]),
                 "by_desk": {}}
    for x in rr:
        k = str(x.get("sig") or "?").split(":")[0]
        b = O["real"]["by_desk"].setdefault(k, {"n": 0, "usd": 0.0})
        b["n"] += 1; b["usd"] = round(b["usd"] + (x.get("pnl_usd") or 0), 2)
    O["worst"] = sorted(rows, key=lambda r: r["pnl"])[:15]
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    for win in ("since_0411", "last14"):
        print(f"::notice title=pm {win} causes::{json.dumps({k: O[win][k] for k in ('pnl_usd', 'pf_usd', 'causes', 'longs_usd', 'shorts_usd', 'trader_lost_too', 'trader_won_we_lost', 'no_trader_match')}, ensure_ascii=False)}")
        print(f"::notice title=pm {win} fixes::{json.dumps(O[win]['fixes'], ensure_ascii=False)} sessions {json.dumps(O[win]['sessions'])}")
    print(f"::notice title=pm real::{json.dumps(O['real'], ensure_ascii=False)}")
    for r in O["worst"][:5]:
        print("::notice title=pm worst::" + json.dumps({k: r[k] for k in ("whale", "coin", "side", "pnl", "ours_pct", "gap", "join", "lev", "whale_lev",
                                                                       "why", "same_side", "regime_ok", "session", "his", "cause", "fx")}, ensure_ascii=False)[:1500])


if __name__ == "__main__":
    main()
