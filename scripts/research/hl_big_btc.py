"""Experiment program item 3 (owner 22.11 16:18: «a trader opening ~$5M BTC long — find big Hyperliquid BTC longs this week, add the
best-scoring to WATCH, not to the real account»). Research only, public Hyperliquid data, nothing is copied.

Method (fixed before the run): the Hyperliquid leaderboard (stats-data) → accounts ≥ $1M value with all-time P&L > 0 and month
P&L > 0, top 300 by month P&L + top 300 by account value → clearinghouseState of each (≤ 600 calls, 0.25 s apart) → open BTC
positions with notional ≥ $1M. Score = month ROI + 0.5 × all-time ROI (capped 10) − a penalty when the month volume > 50 × the
account (market-maker-like) — a ranking for the watch list only, not a copy bar (the copy bar stays whale_study's 60-day record).
Usage: hl_big_btc.py OUT.json"""
import json, sys, time, urllib.request

LB = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
HL = "https://api.hyperliquid.xyz/info"
UA = {"User-Agent": "cnq-research", "Content-Type": "application/json"}


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
        return json.loads(r.read())


def post(body):
    for k in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(HL, data=json.dumps(body).encode(), headers=UA), timeout=30) as r:
                return json.loads(r.read())
        except Exception:  # noqa: BLE001
            time.sleep(1 + k)
    return None


def f(x):
    try:
        return float(x)
    except Exception:  # noqa: BLE001
        return 0.0


def main():
    out = sys.argv[1]
    lb = get(LB).get("leaderboardRows") or []
    rows = []
    for x in lb:
        m = {k: v for k, v in (x.get("windowPerformances") or [])}
        av = f(x.get("accountValue"))
        mo, al, wk = m.get("month") or {}, m.get("allTime") or {}, m.get("week") or {}
        if av < 1e6 or f(al.get("pnl")) <= 0 or f(mo.get("pnl")) <= 0:
            continue
        rows.append({"addr": (x.get("ethAddress") or "").lower(), "name": x.get("displayName") or "", "account": av,
                     "pnl_m": f(mo.get("pnl")), "roi_m": f(mo.get("roi")), "pnl_w": f(wk.get("pnl")), "pnl_all": f(al.get("pnl")),
                     "roi_all": f(al.get("roi")), "vlm_m": f(mo.get("vlm"))})
    pick = {r["addr"]: r for r in sorted(rows, key=lambda r: -r["pnl_m"])[:300]}
    pick.update({r["addr"]: r for r in sorted(rows, key=lambda r: -r["account"])[:300]})
    print(f"leaderboard {len(lb)}, ≥ $1M and profitable {len(rows)}, scanned {len(pick)}", flush=True)
    found = []
    for n, (a, r) in enumerate(pick.items()):
        s = post({"type": "clearinghouseState", "user": a})
        time.sleep(0.25)
        for p in ((s or {}).get("assetPositions") or []):
            q = p.get("position") or {}
            if q.get("coin") != "BTC":
                continue
            sz = f(q.get("szi")); ntl = abs(f(q.get("positionValue")))
            if ntl < 1e6:
                continue
            score = r["roi_m"] + 0.5 * min(r["roi_all"], 10) - (0.5 if r["vlm_m"] > 50 * r["account"] else 0)
            found.append({**r, "side": "LONG" if sz > 0 else "SHORT", "btc_notional": round(ntl), "entry": f(q.get("entryPx")),
                          "lev": (q.get("leverage") or {}).get("value"), "upnl": round(f(q.get("unrealizedPnl"))),
                          "liq": f(q.get("liquidationPx")), "score": round(score, 3)})
        if n % 100 == 0:
            print(f"scanned {n + 1}/{len(pick)}, BTC ≥ $1M so far {len(found)}", flush=True)
    found.sort(key=lambda x: -x["score"])
    longs = [x for x in found if x["side"] == "LONG"]
    O = {"t": int(time.time() * 1000), "scanned": len(pick), "btc_positions_1m": len(found), "longs": longs, "shorts": [x for x in found if x["side"] == "SHORT"]}
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)
    print(f"::notice title=hl btc::scanned {len(pick)}, BTC ≥ $1M: {len(found)} ({len(longs)} long)")
    for x in longs[:9]:
        print("::notice title=hl btc long::" + json.dumps({k: x[k] for k in ("addr", "name", "btc_notional", "entry", "lev", "upnl", "account",
                                                                         "pnl_m", "roi_m", "roi_all", "pnl_w", "score")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
