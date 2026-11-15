"""Backlog 174, 15.11.2026 21:20 Athens: a cap on same-direction alt risk across ALL copies (instead of a per-source pack limit).
Research only; real-money rules change only with Dmytro's yes.

Pre-registered before any result was seen:
- «Alt» = any coin except BTC and ETH. Same direction = same side (LONG or SHORT).
- At each new alt copy: room = N × budget − Σ risk of still-open same-side alt copies (at their applied size).
  New copy size scale = clip(room / its risk, 0.25, 1) — reduced, never skipped (floor 25 %). BTC/ETH copies unchanged.
- Two variants only: N = 15 % and N = 25 % of the budget (budget fixed at $10 000 = desk start; risk/copy ≈ 3 %).
- Data A: desk «Киты» closed copies since 15.10 (reconstructed from git history of web/whales_top.json, net_usd after costs);
  P&L scales linearly with size. Data B: data/whale_trades closed decisions of the 6 locked traders (by name), copy at
  3 % risk with stop −16 % (notional 18.75 % of budget), loss capped at −16 %, costs 0.12 % per round trip.
- Metrics: total, worst UTC day (realized by close day), max drawdown of the realized curve; halves by open time (median).
- Pass: worst day better AND max drawdown better, AND total not lower in either half (both A and B, else «не доказано»).
Usage: whale_dircap174.py closed_all.json data/whale_trades web/whales_top.json OUT.json
"""
import json, glob, os, sys, statistics as st
from collections import defaultdict

DAY = 86_400_000
BUDGET = 10_000.0


def is_alt(c):
    return c.upper() not in ("BTC", "ETH")


def simulate(trades, N):
    """trades: dicts with o, c, coin, side, risk, pnl (full size). Returns list of (trade, scale)."""
    ev = sorted(trades, key=lambda t: t["o"])
    openl = []  # (close_t, side, risk_applied)
    out = []
    for t in ev:
        openl = [x for x in openl if x[0] > t["o"]]
        s = 1.0
        if N is not None and is_alt(t["coin"]):
            used = sum(r for c, sd, r in openl if sd == t["side"])
            room = N * BUDGET - used
            s = max(0.25, min(1.0, room / t["risk"])) if t["risk"] > 0 else 1.0
        if is_alt(t["coin"]):
            openl.append((t["c"], t["side"], t["risk"] * s))
        out.append((t, s))
    return out


def metrics(res):
    if not res:
        return {}
    tot = sum(t["pnl"] * s for t, s in res)
    day = defaultdict(float)
    for t, s in res:
        day[t["c"] // DAY] += t["pnl"] * s
    eq, peak, mdd = 0.0, 0.0, 0.0
    for t, s in sorted(res, key=lambda x: x[0]["c"]):
        eq += t["pnl"] * s; peak = max(peak, eq); mdd = min(mdd, eq - peak)
    return {"n": len(res), "total": round(tot, 1), "worst_day": round(min(day.values()), 1), "max_dd": round(mdd, 1),
            "scaled_n": sum(1 for _, s in res if s < 1), "avg_scale_alt": round(st.mean([s for t, s in res if is_alt(t["coin"])] or [1]), 3)}


def run(trades):
    if not trades:
        return {}
    med = st.median([t["o"] for t in trades])
    out = {}
    for name, N in (("base", None), ("N15", 0.15), ("N25", 0.25)):
        res = simulate(trades, N)
        out[name] = {"all": metrics(res), "h1": metrics([r for r in res if r[0]["o"] < med]),
                     "h2": metrics([r for r in res if r[0]["o"] >= med])}
    for v in ("N15", "N25"):
        b, x = out["base"], out[v]
        x["pass"] = (x["all"]["worst_day"] > b["all"]["worst_day"] and x["all"]["max_dd"] > b["all"]["max_dd"]
                     and x["h1"]["total"] >= b["h1"]["total"] and x["h2"]["total"] >= b["h2"]["total"])
    return out


def data_a(path):
    xs = json.load(open(path))
    return [{"o": x["opened"], "c": x["closed"], "coin": x["symbol"], "side": x["side"], "risk": float(x.get("risk_usd") or 300),
             "pnl": float(x["net_usd"]), "src": x.get("whale")} for x in xs]


def data_b(d, whales_top):
    names = {f["name"] for f in json.load(open(whales_top))["following"] if f.get("src") != "ivan"}
    seen = {}
    for f in sorted(glob.glob(os.path.join(d, "*.jsonl"))):
        for l in open(f):
            x = json.loads(l)
            if x.get("kind") != "closed" or x.get("name") not in names:
                continue
            seen[(x["trader"], x["id"])] = x
    risk = 0.03 * BUDGET; notional = risk / 0.16
    out = []
    for x in seen.values():
        if not x.get("entry") or not x.get("exit") or x["close_t"] <= x["open_t"]:
            continue
        s = 1 if x["side"] == "LONG" else -1
        r = max(-0.16, s * (x["exit"] / x["entry"] - 1)) - 0.0012
        out.append({"o": x["open_t"], "c": x["close_t"], "coin": x["coin"], "side": x["side"], "risk": risk,
                    "pnl": notional * r, "src": x["name"]})
    return out, sorted(names)


if __name__ == "__main__":
    A = data_a(sys.argv[1]); B, names = data_b(sys.argv[2], sys.argv[3])
    res = {"made": "2026-11-15", "A_desk": run(A), "B_locked": run(B), "B_names": names,
           "A_span": [min(t["o"] for t in A), max(t["o"] for t in A)], "B_by_trader": {}}
    for n in names:
        res["B_by_trader"][n] = sum(1 for t in B if t["src"] == n)
    json.dump(res, open(sys.argv[4], "w"), ensure_ascii=False, indent=1)
    for k in ("A_desk", "B_locked"):
        for v, r in res[k].items():
            print(k, v, r["all"], "h1", r["h1"].get("total"), "h2", r["h2"].get("total"), "pass", r.get("pass"))
    print(res["B_by_trader"])
