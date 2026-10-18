"""Where do free stablecoins earn the most for the risk? (19.10.2026; owner: «если есть смысл использовать стейблы в таких
процессах — рассмотреть; если свободные стейблы дадут лучше результат в других сферах — думать как и что».)
Runs in GitHub Actions only (.github/workflows/research-stables.yml): the research container cannot reach these APIs.

1. Every single-asset stablecoin pool on DefiLlama with ≥ $100M deposited (lending, savings rates, curated vaults, tokenized
   T-bills, Pendle fixed-rate PT, Ethena …) + Hyperliquid HLP: today's base rate (no reward tokens) and its last-365-day history
   from DefiLlama's chart API — mean, worst 10 % of days, minimum, 2025 and 2026 means, deposits trend.
2. Funding carry (buy the coin, short its perpetual — no direction): BTC and ETH on Binance, 2023 → today, per year; and a
   switching rule «carry only while the last 7 days pay more than lending, else lending» with 0.2 % per switch.
Each source gets our risk class (2 = blue-chip lending / T-bills, 3 = one more layer of risk, 4 = can lose principal in a bad
week) and the allocator's discount (risk 3 → −1.5 points, risk 4 → −4) — the «net» rate is what the allocator would compare.
Output: annotations (≤ 10) + job summary. Stdlib + numpy."""
import datetime as dt
import io
import json
import os
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from fetch_alt100 import http, months_between, note  # noqa: E402

TODAY = dt.datetime.now(dt.timezone.utc).date()
UM = "https://data.binance.vision/data/futures/um"
DISCOUNT = {2: 0.0, 3: 1.5, 4: 4.0}
LENDING = 3.6          # Aave USDC/USDT + Sky today (web/yield.json)


def risk_of(p: dict) -> int:
    pr, sym, meta = p.get("project", ""), (p.get("symbol") or "").upper(), (p.get("poolMeta") or "").lower()
    if pr in ("aave-v3", "compound-v3", "sky-lending", "spark-savings", "sparklend") or "buidl" in pr or "treasur" in meta \
            or sym in ("USDY", "OUSG", "BUIDL", "USTB", "BENJI", "USYC"):
        return 2
    if "hlp" in sym.lower() or "hyperliquid" in pr:
        return 4
    return 3


def chart(pool: str) -> list:
    s, b = http(f"https://yields.llama.fi/chart/{pool}")
    try:
        return json.loads(b).get("data") or [] if s == 200 else []
    except Exception:  # noqa: BLE001
        return []


def hist_stats(rows: list) -> dict:
    cut = (TODAY - dt.timedelta(days=365)).isoformat()
    pts = [(r["timestamp"][:10], r.get("apyBase") if r.get("apyBase") is not None else r.get("apy"), r.get("tvlUsd")) for r in rows]
    pts = [(d, float(a), t) for d, a, t in pts if a is not None and d >= cut]
    if len(pts) < 30:
        return {"days": len(pts)}
    a = np.array([x[1] for x in pts])
    out = {"days": len(pts), "mean": round(float(a.mean()), 2), "p10": round(float(np.percentile(a, 10)), 2),
           "min": round(float(a.min()), 2), "max": round(float(a.max()), 2)}
    for y in ("2025", "2026"):
        v = [x[1] for x in pts if x[0][:4] == y]
        if len(v) >= 20:
            out[y] = round(float(np.mean(v)), 2)
    t0, t1 = pts[0][2], pts[-1][2]
    if t0 and t1:
        out["tvl_change_pct"] = round((t1 / t0 - 1) * 100)
    return out


def pools_part():
    s, b = http("https://yields.llama.fi/pools")
    pools = json.loads(b)["data"] if s == 200 else []
    cand = [p for p in pools if p.get("stablecoin") and p.get("exposure") == "single" and (p.get("tvlUsd") or 0) >= 1e8
            and (p.get("apyBase") or 0) > 0]
    hlp = [p for p in pools if "HLP" in (p.get("symbol") or "").upper() or "hlp" in (p.get("project") or "")]
    cand = sorted(cand, key=lambda p: -p["tvlUsd"])[:45] + sorted(hlp, key=lambda p: -(p.get("tvlUsd") or 0))[:2]
    with ThreadPoolExecutor(8) as ex:
        hs = list(ex.map(lambda p: hist_stats(chart(p["pool"])), cand))
    rows = []
    for p, h in zip(cand, hs):
        r = risk_of(p)
        base_now = round(float(p.get("apyBase") or p.get("apy") or 0), 2)
        mean = h.get("mean", base_now)
        rows.append({"name": f"{p['project']} · {p['symbol']} · {p['chain']}" + (f" · {p['poolMeta']}" if p.get("poolMeta") else ""),
                     "tvl_m": round(p["tvlUsd"] / 1e6), "now": base_now, "reward": round(float(p.get("apyReward") or 0), 2),
                     "risk": r, "net_now": round(base_now - DISCOUNT[r], 2), "net_mean_1y": round(mean - DISCOUNT[r], 2), **h})
    rows.sort(key=lambda x: -x["net_mean_1y"])
    low = [x for x in rows if x["risk"] == 2]
    mid = [x for x in rows if x["risk"] == 3 and "pendle" not in x["name"]]
    pen = [x for x in rows if "pendle" in x["name"]]
    high = [x for x in rows if x["risk"] == 4]
    note("stables · risk 2 (lending, savings, T-bills)", json.dumps(low[:14], ensure_ascii=False))
    note("stables · risk 3 (vaults, synthetic dollars, credit)", json.dumps(mid[:14], ensure_ascii=False))
    note("stables · Pendle fixed rate", json.dumps(pen[:10], ensure_ascii=False))
    note("stables · risk 4 (HLP market-making)", json.dumps(high, ensure_ascii=False))
    return rows


def _zip_rows(b: bytes) -> list:
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        rows = [ln.split(",") for ln in z.read(z.namelist()[0]).decode().splitlines()]
    return [r for r in rows if r and r[0][:1].isdigit()]


def funding_part():
    last_full = (TODAY.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")
    out = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        with ThreadPoolExecutor(8) as ex:
            res = list(ex.map(lambda m: http(f"{UM}/monthly/fundingRate/{sym}/{sym}-fundingRate-{m}.zip", binary=True),
                              months_between("2023-01", last_full)))
        daily = {}
        for s, b in res:
            if s != 200:
                continue
            for r in _zip_rows(b):
                d = dt.datetime.fromtimestamp(int(r[0]) / 1000, dt.timezone.utc).date()
                daily[d] = daily.get(d, 0.0) + float(r[2])
        days = sorted(daily)
        f = np.array([daily[d] for d in days])
        yrs = np.array([d.year for d in days])
        row = {}
        for y in (2023, 2024, 2025, 2026):
            m = yrs == y
            if m.sum() > 20:
                row[str(y)] = {"carry_pct_yr": round(float(f[m].mean() * 365 * 100), 2),
                               "negative_days_pct": round(float((f[m] < 0).mean() * 100))}
        # switching: carry while the trailing 7 days pay more than lending (+ a margin for the costs), else lending
        lend_d = LENDING / 100 / 365
        val, on, switches, per_year = 1.0, False, 0, {}
        for i in range(7, len(f)):
            tr = f[i - 7:i].mean() * 365 * 100
            want = tr > LENDING + 2.0
            if want != on:
                val *= 1 - 0.002
                switches += 1
                on = want
            r = f[i] if on else lend_d
            val *= 1 + r
            per_year.setdefault(days[i].year, []).append(r)
        row["switch_rule"] = {str(y): round(float(np.prod([1 + x for x in v]) - 1) * 100, 2) for y, v in per_year.items()}
        row["switch_rule"]["switches"] = switches
        row["last_30d_pct_yr"] = round(float(f[-30:].mean() * 365 * 100), 2)
        out[sym] = row
    note("stables · funding carry (BTC, ETH)", json.dumps(out, ensure_ascii=False))
    return out


def pendle_part():
    """Pendle fixed-rate markets on dollars (PT, held to maturity) — DefiLlama lists them under project «pendle»; the PT rate
    is the fixed rate locked at purchase. ≥ $10M, any exposure flag."""
    s, b = http("https://yields.llama.fi/pools")
    pools = json.loads(b)["data"] if s == 200 else []
    pen = [p for p in pools if p.get("project") == "pendle" and "USD" in (p.get("symbol") or "").upper() and (p.get("tvlUsd") or 0) >= 1e7]
    pen = sorted(pen, key=lambda p: -p["tvlUsd"])[:16]
    with ThreadPoolExecutor(8) as ex:
        hs = list(ex.map(lambda p: hist_stats(chart(p["pool"])), pen))
    rows = [{"name": f"{p['symbol']} · {p['chain']} · {p.get('poolMeta') or ''}", "tvl_m": round(p["tvlUsd"] / 1e6),
             "apy": round(float(p.get("apy") or 0), 2), "base": round(float(p.get("apyBase") or 0), 2),
             "reward": round(float(p.get("apyReward") or 0), 2), "stable": p.get("stablecoin"), "exposure": p.get("exposure"),
             "underlying": p.get("underlyingTokens"), **h} for p, h in zip(pen, hs)]
    note("stables · Pendle markets on dollars", json.dumps(rows, ensure_ascii=False))
    projects = sorted({p.get("project") for p in pools if "pendle" in (p.get("project") or "")})
    note("stables · Pendle projects on DefiLlama", json.dumps(projects))


def main():
    if "--pendle" in sys.argv:
        return pendle_part()
    rows = pools_part()
    fund = funding_part()
    best = [{"name": x["name"], "risk": x["risk"], "net_mean_1y": x["net_mean_1y"], "mean": x.get("mean"), "p10": x.get("p10"),
             "tvl_m": x["tvl_m"]} for x in rows[:10]]
    note("stables · ranking (mean of the last year minus the risk discount)", json.dumps(best, ensure_ascii=False))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write("## Свободные стейблы\n```\n" + json.dumps({"pools": rows, "funding": fund}, ensure_ascii=False, indent=1) + "\n```\n")


if __name__ == "__main__":
    main()
