"""Copy trading and ready-made bots beyond OKX: Binance copy trading, Binance bot square (grid bots), MEXC copy trading
(19.10.2026, owner: «не только OKX — Binance, Bybit и другие … много предлагают копитрейдинг и готовые настроенные боты»).
Bybit and Bitget answer 403 / 404 from the US runner (copytraders_probe.py), so they are not here.

The key question for anyone who copies: does the leader's big ROI reach the people who copy him? Binance and MEXC publish the
copiers' total P&L next to the leader's ROI. Plus: how long leaders have been running, their drawdowns, and for Binance —
the ROI curve of each leader over 180 days, to see whether +70 % months repeat. Only aggregates go out (no nicknames). Stdlib."""
import json
import statistics as st
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
      "Accept": "application/json, text/plain, */*", "Content-Type": "application/json", "clienttype": "web", "lang": "en"}
BN = "https://www.binance.com/bapi/futures"


def note(title, msg):
    print(f"{title}: {msg}", flush=True)
    m = str(msg).replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    print(f"::notice title={title}::{m}", flush=True)


def call(url, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=UA,
                                 method="POST" if body is not None else "GET")
    for a in range(3):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            err = str(e)[:120]
            time.sleep(1.5)
    return {"error": err}


def f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None


def binance_list(time_range, data_type="ROI", pages=40):
    rows = []
    for page in range(1, pages + 1):
        d = call(f"{BN}/v1/friendly/future/copy-trade/home-page/query-list",
                 {"pageNumber": page, "pageSize": 18, "timeRange": time_range, "dataType": data_type, "favoriteOnly": False,
                  "hideFull": False, "nickname": "", "order": "DESC", "userAsset": 0, "portfolioType": "PUBLIC"})
        lst = ((d.get("data") or {}).get("list") if isinstance(d.get("data"), dict) else None) or []
        if not lst:
            break
        rows += lst
        time.sleep(0.35)
    return rows


def binance_part():
    now = time.time() * 1000
    out = {}
    for tr in ("30D", "90D", "180D"):
        rows = binance_list(tr)
        if not rows:
            out[tr] = {"rows": 0}
            continue
        roi = [f(r.get("roi")) for r in rows]
        cp = [f(r.get("copierPnl")) for r in rows]
        mdd = [f(r.get("mdd")) for r in rows]
        days = [(now - f(r.get("startTime"), now)) / 8.64e7 for r in rows]
        top = rows[:50]
        out[tr] = {"leaders": len(rows), "roi_median_pct": round(st.median(roi), 1), "roi_p90_pct": round(q(roi, 0.9), 1),
                   "roi_over_100pct": sum(1 for x in roi if x >= 100), "roi_negative": sum(1 for x in roi if x < 0),
                   "mdd_median_pct": round(st.median(mdd), 1), "mdd_over_50pct": sum(1 for x in mdd if x >= 50),
                   "days_running_median": round(st.median(days)),
                   "copiers_pnl_total_usd": round(sum(cp)), "copiers_pnl_plus_share_pct": round(sum(1 for x in cp if x > 0) / len(cp) * 100),
                   "top50_by_roi": {"roi_median_pct": round(st.median(f(r.get("roi")) for r in top), 1),
                                    "copiers_pnl_total_usd": round(sum(f(r.get("copierPnl")) for r in top)),
                                    "copiers_in_plus": sum(1 for r in top if f(r.get("copierPnl")) > 0),
                                    "mdd_median_pct": round(st.median(f(r.get("mdd")) for r in top), 1)}}
        if tr == "180D":
            out["_ids180"] = [r.get("leadPortfolioId") for r in rows]
            out["_rows180"] = rows
    note("markets · binance copy trading", json.dumps({k: v for k, v in out.items() if not k.startswith("_")}, ensure_ascii=False))
    # ROI curves over 180 days → months, for the leaders ranked over 180 days
    ids = out.get("_ids180") or []
    months, pairs, shown, steady = [], [], False, []
    rows180 = {r.get("leadPortfolioId"): r for r in out.get("_rows180") or []}
    for pid in ids[:300]:
        d = call(f"{BN}/v1/public/future/copy-trade/lead-portfolio/chart-data?dataType=ROI&portfolioId={pid}&timeRange=180D")
        pts = d.get("data") or []
        if not shown:
            note("markets · binance chart shape", json.dumps({"keys": list(d.keys()), "n": len(pts), "head": pts[:2], "tail": pts[-2:],
                                                              "error": d.get("error") or d.get("message")}, ensure_ascii=False)[:1500])
            shown = True
        if not isinstance(pts, list) or len(pts) < 120:
            time.sleep(0.3)
            continue
        ser = sorted((int(p.get("dateTime") or 0), f(p.get("value"))) for p in pts if isinstance(p, dict))
        ends = {}
        for t, v in ser:
            ends[time.strftime("%Y-%m", time.gmtime(t / 1000))] = v
        ks = sorted(ends)
        prev, mo = None, []
        for k in ks:
            if prev is not None:
                mo.append((1 + ends[k] / 100) / (1 + prev / 100) - 1 if 1 + prev / 100 > 0 else None)
            prev = ends[k]
        mo = [x for x in mo[:-1] if x is not None]            # the current month is partial
        if len(mo) >= 4:
            months += mo
            pairs += [(mo[i], mo[i + 1]) for i in range(len(mo) - 1)]
            eq, pk, dd = 1.0, 1.0, 0.0
            for r in mo:
                eq *= 1 + r
                pk = max(pk, eq)
                dd = max(dd, 1 - eq / pk)
            if sum(1 for r in mo if r > 0) / len(mo) >= 0.75 and dd <= 0.3:
                r0 = rows180.get(pid) or {}
                steady.append({"median_month_pct": round(st.median(mo) * 100, 1), "months": len(mo), "mdd_pct": round(f(r0.get("mdd")), 1),
                               "copiers_pnl_usd": round(f(r0.get("copierPnl"))), "aum_k": round(f(r0.get("aum")) / 1000),
                               "win_pct": round(f(r0.get("winRate")))})
        time.sleep(0.3)
    if months:
        big = [b for a, b in pairs if a >= 0.7]
        note("markets · binance leaders month by month", json.dumps({
            "leaders": len(set(ids[:300])), "leader_months": len(months), "months_70pct_plus_share": round(sum(1 for r in months if r >= 0.7) / len(months) * 100, 1),
            "months_plus_share": round(sum(1 for r in months if r > 0) / len(months) * 100, 1), "median_month_pct": round(st.median(months) * 100, 1),
            "after_70pct_month": {"n": len(big), "next_plus_pct": round(sum(1 for r in big if r > 0) / len(big) * 100) if big else None,
                                  "next_median_pct": round(st.median(big) * 100, 1) if big else None},
            "steady_n": len(steady), "steady": sorted(steady, key=lambda x: -x["median_month_pct"])[:15]}, ensure_ascii=False))


def binance_bots():
    rows = []
    for page in range(1, 11):
        d = call(f"{BN}/v1/public/future/common/strategy/landing-page/queryTopStrategy",
                 {"page": page, "rows": 20, "direction": "", "strategyType": 2, "symbol": "", "zone": "", "runningTime": "", "sort": "roi"})
        lst = d.get("data") or []
        if not lst:
            break
        rows += lst
        time.sleep(0.4)
    if not rows:
        note("markets · binance bots", "no rows")
        return
    lev = [f((r.get("strategyParams") or {}).get("leverage"), 1) for r in rows]
    days = [f(r.get("runningTime")) / 86400 for r in rows]
    roi = [f(r.get("roi")) for r in rows]
    note("markets · binance futures grid bots (top by ROI)", json.dumps({
        "bots": len(rows), "roi_median_pct": round(st.median(roi)), "leverage_median": st.median(lev), "leverage_ge_20_share_pct": round(sum(1 for x in lev if x >= 20) / len(lev) * 100),
        "running_days_median": round(st.median(days), 1), "running_under_30d_share_pct": round(sum(1 for x in days if x < 30) / len(days) * 100),
        "symbols": [k for k, _ in __import__("collections").Counter(r.get("symbol") for r in rows).most_common(6)],
        "initial_margin_median_usd": round(st.median(f(r.get("initialMargin")) for r in rows), 1),
        "seven_day_mdd_median_pct": round(st.median(f(r.get("sevenDayMdd")) for r in rows) * (100 if max(f(r.get("sevenDayMdd")) for r in rows) <= 1 else 1), 1)}))


def mexc_part():
    rows = []
    for page in range(1, 31):
        d = call(f"https://futures.mexc.com/copyFutures/api/v1/traders/v2?condition=%5B%5D&limit=20&orderBy=ROI&page={page}")
        lst = ((d.get("data") or {}).get("content") if isinstance(d.get("data"), dict) else d.get("data")) or []
        if not isinstance(lst, list) or not lst:
            break
        rows += lst
        time.sleep(0.35)
    if not rows:
        note("markets · mexc", "no rows")
        return
    roi = [f(r.get("roi")) for r in rows]
    fp = [f(r.get("followProfit")) for r in rows]
    mdd = [f(r.get("maxDrawdown")) for r in rows]
    scale = 100 if max(abs(x) for x in roi) < 50 else 1
    note("markets · mexc copy trading", json.dumps({
        "leaders": len(rows), "roi_median_pct": round(st.median(roi) * scale, 1), "roi_over_100pct": sum(1 for x in roi if x * scale >= 100),
        "mdd_median": round(st.median(mdd), 3), "copiers_profit_total_usd": round(sum(fp)),
        "copiers_profit_plus_share_pct": round(sum(1 for x in fp if x > 0) / len(fp) * 100),
        "sample_keys": sorted(rows[0].keys())[:50]}, ensure_ascii=False)[:3000])


def main():
    for fn in (binance_part, binance_bots, mexc_part):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            note(f"markets · {fn.__name__} error", str(e)[:300])


if __name__ == "__main__":
    main()
