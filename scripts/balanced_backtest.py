"""Sanity backtest of the 5th spot profile «Сбалансированный · максимум потенциала» (cnq/agents/portfolio.py, BalancedPortfolio).

It drives the REAL class day by day on the only long price history in the repo: data/daily_closes18.json (daily closes of 18 large
coins, 2020-09 → 2026-09, Binance). Scores come from the real portfolio.score_universe; the BTC regime is the server fallback
(BTC above its price 200 days ago); memes use the real dip gate (own daily samples, as meme_spot does live).

What this can and cannot say (read before quoting a number):
  • survivorship: the 18 coins are TODAY's large caps — the mid sleeve picks from survivors, which flatters the result;
  • HYPE has no history here → the anchor slot falls back to the next sector leader (the rule the live code also uses);
    leaders come from the same 18 large coins (no small caps), the only meme is DOGE;
  • daily closes: stops/trailing fill at the close; no intraday wicks, no slippage; bal pays 0.1 % per trade, benchmarks pay nothing;
  • market cap / volume / Hyperliquid listing are unknown historically → treated as passing; research class «mod» = ≥ 365 days of data.
So it checks that the rules behave sensibly through a full cycle (2021 top, 2022 bear, 2023–24 bull, 2025–26), not future returns.

Usage: python scripts/balanced_backtest.py [--json]
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")
from cnq.agents import meme_spot as ms  # noqa: E402
from cnq.agents import portfolio as pf  # noqa: E402

DAY = 86_400_000


def load():
    raw = json.load(open("data/daily_closes18.json", encoding="utf-8"))
    return {k.replace("USDT", ""): {int(d): float(v) for d, v in s.items()} for k, s in raw.items()}


def tags_now() -> dict:
    try:
        return {c["symbol"]: c.get("tags") or [] for c in json.load(open("web/research.json", encoding="utf-8"))["cards"]}
    except Exception:  # noqa: BLE001
        return {}


def stats(curve: list[tuple[int, float]]) -> dict:
    v = [x for _, x in curve]
    peak, dd = v[0], 0.0
    for x in v:
        peak = max(peak, x); dd = min(dd, x / peak - 1)
    years = (curve[-1][0] - curve[0][0]) / 365
    by_year: dict[int, list] = {}
    for d, x in curve:
        by_year.setdefault(datetime.fromtimestamp(d * 86400, tz=timezone.utc).year, []).append(x)
    yr, prev = {}, v[0]
    for y in sorted(by_year):
        yr[y] = round((by_year[y][-1] / prev - 1) * 100, 1); prev = by_year[y][-1]
    return {"final_x": round(v[-1] / v[0], 2), "cagr_pct": round(((v[-1] / v[0]) ** (1 / years) - 1) * 100, 1), "max_dd_pct": round(dd * 100, 1), "years": yr}


def bench_weights(px, days, weights: dict, cash: float) -> list:
    """Monthly rebalanced fixed weights (market-value rebalance on the 1st), no fees."""
    hold, c, out, last_m = {}, 1.0, [], None
    for d in days:
        m = datetime.fromtimestamp(d * 86400, tz=timezone.utc).month
        val = c + sum(q * px[s][d] for s, q in hold.items() if d in px[s])
        if m != last_m:
            c = val * cash
            hold = {s: val * w / px[s][d] for s, w in weights.items() if d in px[s]}
            c += val * sum(w for s, w in weights.items() if d not in px[s])
            last_m = m
        out.append((d, val))
    return out


def run_bal(px, days, tags, protect: bool = True) -> tuple[list, dict]:
    """protect=False ignores the BTC regime (always «bull») — the same rules without the protection switch."""
    if True:
        conn = sqlite3.connect(":memory:")
        port = pf.BalancedPortfolio(conn)
        first = {s: min(v) for s, v in px.items()}
        athx = {}
        for s, ser in px.items():
            m = 0.0
            athx[s] = {}
            for dd in sorted(ser):
                m = max(m, ser[dd]); athx[s][dd] = m
        memes = {"DOGE"}
        out, counts = [], {}
        for d in days:
            now = d * DAY + 12 * 3_600_000
            mk = []
            for s, ser in px.items():
                if d not in ser or d - 200 not in ser:
                    continue
                p = ser[d]
                ch = {k: (p / ser[d - n] - 1) * 100 if d - n in ser else None for k, n in (("chg24", 1), ("chg7", 7), ("chg30", 30), ("chg200", 200), ("chg1y", 365))}
                ath = athx[s][d]
                mk.append({"symbol": s, "name": s, "id": s.lower(), "price": p, "mcap": 1e10, "rank": 10, "volume": 1e9, **ch,
                           "ath_chg": (p / ath - 1) * 100, "ath_date": None,
                           "atl_date": datetime.fromtimestamp(first[s] * 86400, tz=timezone.utc).isoformat().replace("+00:00", "Z")})
            btc = next((r for r in mk if r["symbol"] == "BTC"), None)
            if not btc:
                continue
            bull = (btc["chg200"] or 0) > 0 or not protect
            rows = pf.score_universe(mk, {"perps": {r["symbol"]: {} for r in mk}, "spot": {}}, bull)
            pf.picks(rows)                                   # applies the owner's trust-list bonus exactly as run() does
            cards = [{"symbol": r["symbol"], "risk": "cons" if r["symbol"] in ("BTC", "ETH", "SOL") else
                      ("mod" if d - first[r["symbol"]] >= 365 and r["symbol"] != "DOGE" else "high"),
                      "tags": (["мем"] if r["symbol"] == "DOGE" else tags.get(r["symbol"], []))} for r in rows]
            ms.ensure(conn)
            if "DOGE" in px and d in px["DOGE"]:
                ms.update_highs(conn, "DOGE", px["DOGE"][d], now)
            st = port.step(rows, cards, bull, memes, now)
            for a in st["actions"]:
                key = a["action"] + (":stop" if a["reason"].startswith("Стоп") else ":trail" if a["reason"].startswith("Трейлинг") else
                                     ":protect" if "защита" in a["reason"] or "смена режима" in a["reason"] else "")
                counts[key] = counts.get(key, 0) + 1
            out.append((d, st["index"]))
        return out, counts


def main():
    px = load()
    tags = tags_now()
    start = max(min(px["BTC"]), min(px["SOL"])) + 201
    end = max(px["BTC"])
    days = [d for d in range(start, end + 1) if d in px["BTC"]]
    bal, counts = run_bal(px, days, tags)
    bal_np, _ = run_bal(px, days, tags, protect=False)
    btc = [(d, px["BTC"][d]) for d in days]
    cons = bench_weights(px, days, {"BTC": 0.5, "ETH": 0.25, "SOL": 0.15}, 0.10)
    ew = bench_weights(px, days, {s: 1 / len(px) for s in px}, 0.0)
    res = {"period": f"{datetime.fromtimestamp(days[0] * 86400, tz=timezone.utc):%Y-%m-%d} → {datetime.fromtimestamp(days[-1] * 86400, tz=timezone.utc):%Y-%m-%d}",
           "data": "data/daily_closes18.json — 18 крупных монет, дневные закрытия Binance",
           "bal": stats(bal), "bal_no_protection": stats(bal_np), "btc_hold": stats(btc), "cons_50_25_15": stats(cons), "equal_weight_18": stats(ew),
           "trades": counts}
    if "--json" in sys.argv:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return
    print(res["period"], "|", res["data"])
    print(f"{'стратегия':22} {'×итог':>6} {'CAGR%':>7} {'макс.просадка%':>15}  по годам %")
    for k in ("bal", "bal_no_protection", "btc_hold", "cons_50_25_15", "equal_weight_18"):
        s = res[k]
        print(f"{k:22} {s['final_x']:6.2f} {s['cagr_pct']:7.1f} {s['max_dd_pct']:15.1f}  {s['years']}")
    print("сделки bal:", counts)


if __name__ == "__main__":
    main()
