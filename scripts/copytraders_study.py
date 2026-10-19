"""The best copy traders — how long do big months last, and how do the steady ones trade? (19.10.2026, owner: «люди имеют по 80,
по 90% месячных; можно найти самых успешных копи-трейдеров, проанализировать, найти стратегии»). Runs in GitHub Actions only
(.github/workflows/research-copytraders.yml): the research container cannot reach the exchanges.

Source: OKX's PUBLIC copy-trading API (no keys): lead trader ranks, each trader's daily P&L for a year, and closed positions
(about 3 months). Questions:
  1. How many lead traders exist, and how many show +70 % or more in a month?
  2. Of those big months, how often is the NEXT month also in plus? (does a big month say anything about the next one)
  3. Who is steady for 6+ months (most months in plus, shallow drawdown), and how do they trade: how often, how long they hold,
     what leverage, which coins, long or short.
Only aggregates and anonymised profiles go out (no nicknames). Stdlib only."""
import json
import os
import statistics as st
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter

HOSTS = ["https://www.okx.com", "https://my.okx.com", "https://app.okx.com", "https://aws.okx.com"]
HOST = None
_last = [0.0]


def note(title, msg):
    print(f"{title}: {msg}", flush=True)
    m = str(msg).replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    print(f"::notice title={title}::{m}", flush=True)


def get(path, **q):
    global HOST
    hosts = [HOST] if HOST else HOSTS
    err = None
    for h in hosts:
        for attempt in range(4):
            wait = 0.45 - (time.time() - _last[0])
            if wait > 0:
                time.sleep(wait)
            _last[0] = time.time()
            url = f"{h}/api/v5/copytrading/{path}?" + urllib.parse.urlencode({k: v for k, v in q.items() if v is not None})
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "cnq-research/1.0"}), timeout=30) as r:
                    d = json.loads(r.read().decode())
                if str(d.get("code")) == "0":
                    HOST = h
                    return d.get("data") or []
                err = f"{h} code={d.get('code')} {d.get('msg')}"
                if str(d.get("code")) in ("50011",):         # rate limit
                    time.sleep(2)
                    continue
                break
            except Exception as e:  # noqa: BLE001
                err = f"{h} {str(e)[:120]}"
                time.sleep(1)
    raise RuntimeError(err)


def ranks(min_days):
    out, page, total = [], 1, 1
    while page <= total and page <= 60:
        d = get("public-lead-traders", instType="SWAP", sortType="overview", state="0", minLeadDays=str(min_days), page=str(page), limit="20")
        if not d:
            break
        total = int(d[0].get("totalPage") or 1)
        out += d[0].get("ranks") or []
        page += 1
    return out


def monthly(daily):
    """daily = [(ts, pnlRatio)] in time order. OKX gives the CUMULATIVE ratio over the window (checked in the probe note);
    month return = (1 + cum at month end) / (1 + cum at the previous month end) − 1."""
    import datetime as dt
    ends = {}
    for ts, cum in daily:
        ends[dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc).strftime("%Y-%m")] = cum
    ms = sorted(ends)
    prev, out = 0.0, {}
    for m in ms:
        out[m] = (1 + ends[m]) / (1 + prev) - 1 if 1 + prev > 0 else None
        prev = ends[m]
    return out


def main():
    try:
        r1 = ranks(1)
    except Exception as e:  # noqa: BLE001
        note("copytraders · error", str(e))
        return
    first = r1[0] if r1 else {}
    note("copytraders · probe", json.dumps({"host": HOST, "rank_keys": sorted(first.keys()),
                                            "sample": {k: first.get(k) for k in ("pnl", "pnlRatio", "aum", "winRatio", "leadDays", "copyTraderNum")},
                                            "pnlRatios_head": (first.get("pnlRatios") or [])[:3], "pnlRatios_tail": (first.get("pnlRatios") or [])[-3:]}))
    # population
    lead = [float(x.get("leadDays") or 0) for x in r1]
    roi = [float(x.get("pnlRatio") or 0) for x in r1]
    note("copytraders · population", json.dumps({"traders": len(r1), "lead_days_median": st.median(lead) if lead else None,
                                                 "with_180d_plus": sum(1 for x in lead if x >= 180), "with_365d_plus": sum(1 for x in lead if x >= 365),
                                                 "roi_overview_median_pct": round(st.median(roi) * 100, 1) if roi else None,
                                                 "roi_over_100pct": sum(1 for x in roi if x >= 1.0), "roi_negative": sum(1 for x in roi if x < 0)}))
    long_ = [x for x in r1 if float(x.get("leadDays") or 0) >= 180]
    prof, months_all, pairs = [], [], []
    sample_shown = False
    for x in long_[:400]:
        code = x.get("uniqueCode")
        try:
            d = get("public-pnl", instType="SWAP", uniqueCode=code, lastDays="4")
        except Exception:  # noqa: BLE001
            continue
        rows = sorted((int(r["beginTs"]), float(r.get("pnlRatio") or 0)) for r in d if r.get("beginTs"))
        if not sample_shown and rows:
            note("copytraders · pnl shape", json.dumps({"n_days": len(rows), "head": rows[:3], "tail": rows[-3:], "raw0": d[0]}))
            sample_shown = True
        if len(rows) < 150:
            continue
        mo = {k: v for k, v in monthly(rows).items() if v is not None}
        ks = sorted(mo)[1:-1]                      # drop the partial first and current months
        if len(ks) < 6:
            continue
        v = [mo[k] for k in ks]
        months_all += v
        pairs += [(v[i], v[i + 1]) for i in range(len(v) - 1)]
        eq, peak, dd = 1.0, 1.0, 0.0
        for r in v:
            eq *= 1 + r
            peak = max(peak, eq)
            dd = max(dd, 1 - eq / peak)
        prof.append({"code": code, "months": len(v), "median_m": st.median(v), "plus_share": sum(1 for r in v if r > 0) / len(v),
                     "max_dd": dd, "year_x": eq, "m70": sum(1 for r in v if r >= 0.7), "aum": float(x.get("aum") or 0),
                     "followers": int(x.get("copyTraderNum") or 0)})
    if not prof:
        note("copytraders · result", "no trader with 6+ full months of daily P&L")
        return
    big = [b for a, b in pairs if a >= 0.7]
    mid = [b for a, b in pairs if 0.3 <= a < 0.7]
    neg = [b for a, b in pairs if a < 0]
    note("copytraders · months", json.dumps({
        "traders_6m_plus": len(prof), "trader_months": len(months_all),
        "months_70pct_plus_share": round(sum(1 for r in months_all if r >= 0.7) / len(months_all) * 100, 1),
        "months_plus_share": round(sum(1 for r in months_all if r > 0) / len(months_all) * 100, 1),
        "median_month_pct": round(st.median(months_all) * 100, 1),
        "traders_with_any_70pct_month": sum(1 for p in prof if p["m70"] > 0),
        "traders_with_3plus_70pct_months": sum(1 for p in prof if p["m70"] >= 3),
        "traders_year_x_ge_10": sum(1 for p in prof if p["year_x"] >= 10),
        "after_70pct_month": {"n": len(big), "next_plus_pct": round(sum(1 for r in big if r > 0) / len(big) * 100) if big else None,
                              "next_median_pct": round(st.median(big) * 100, 1) if big else None},
        "after_30_70pct_month": {"n": len(mid), "next_plus_pct": round(sum(1 for r in mid if r > 0) / len(mid) * 100) if mid else None,
                                 "next_median_pct": round(st.median(mid) * 100, 1) if mid else None},
        "after_minus_month": {"n": len(neg), "next_plus_pct": round(sum(1 for r in neg if r > 0) / len(neg) * 100) if neg else None,
                              "next_median_pct": round(st.median(neg) * 100, 1) if neg else None},
        "drawdown_median_pct": round(st.median(p["max_dd"] for p in prof) * 100),
        "drawdown_over_50pct": sum(1 for p in prof if p["max_dd"] > 0.5)}))
    steady = sorted([p for p in prof if p["plus_share"] >= 0.75 and p["max_dd"] <= 0.30 and p["months"] >= 6],
                    key=lambda p: -p["median_m"])
    note("copytraders · steady (6+ months, 75 %+ months in plus, drawdown ≤ 30 %)", json.dumps({
        "n": len(steady), "of": len(prof),
        "profiles": [{"months": p["months"], "median_month_pct": round(p["median_m"] * 100, 1), "plus_months_pct": round(p["plus_share"] * 100),
                      "max_dd_pct": round(p["max_dd"] * 100), "year_x": round(p["year_x"], 2), "aum_k": round(p["aum"] / 1000),
                      "followers": p["followers"]} for p in steady[:20]]}))
    # how the steady ones trade
    styles = []
    for p in steady[:15]:
        pos, after = [], None
        for _ in range(4):
            try:
                d = get("public-subpositions-history", instType="SWAP", uniqueCode=p["code"], limit="100", after=after)
            except Exception:  # noqa: BLE001
                break
            if not d:
                break
            pos += d
            after = d[-1].get("subPosId")
            if len(d) < 100:
                break
        if not pos:
            continue
        if not styles:
            note("copytraders · position shape", json.dumps({"keys": sorted(pos[0].keys())}))
        hold = [(int(r["closeTime"]) - int(r["openTime"])) / 3.6e6 for r in pos if r.get("closeTime") and r.get("openTime")]
        lev = [float(r["lever"]) for r in pos if r.get("lever")]
        pr = [float(r["pnlRatio"]) for r in pos if r.get("pnlRatio") not in (None, "")]
        span_d = (max(int(r.get("closeTime") or 0) for r in pos) - min(int(r.get("openTime") or 0) for r in pos)) / 8.64e7 if pos else 0
        insts = Counter(r.get("instId", "").replace("-USDT-SWAP", "") for r in pos)
        styles.append({"median_month_pct": round(p["median_m"] * 100, 1), "positions": len(pos), "per_day": round(len(pos) / max(span_d, 1), 1),
                       "hold_h_median": round(st.median(hold), 1) if hold else None, "lever_median": st.median(lev) if lev else None,
                       "long_share_pct": round(sum(1 for r in pos if r.get("posSide") == "long") / len(pos) * 100),
                       "win_pct": round(sum(1 for x in pr if x > 0) / len(pr) * 100) if pr else None,
                       "avg_win_pct": round(st.mean([x for x in pr if x > 0]) * 100, 1) if any(x > 0 for x in pr) else None,
                       "avg_loss_pct": round(st.mean([x for x in pr if x <= 0]) * 100, 1) if any(x <= 0 for x in pr) else None,
                       "worst_pct": round(min(pr) * 100, 1) if pr else None,
                       "top_coins": [k for k, _ in insts.most_common(4)], "coins": len(insts)})
    note("copytraders · how the steady ones trade", json.dumps(styles, ensure_ascii=False))


if __name__ == "__main__":
    main()
