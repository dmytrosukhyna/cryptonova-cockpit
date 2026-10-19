"""Short of new listings on MORE venues — MEXC and Hyperliquid perpetuals (19.10.2026, morning cycle toward +70 % a month: «больше
сделок того же правила»). Runs in GitHub Actions only (.github/workflows/research-listings-more.yml).

The rule is the live one (listing_desk.py, checked on 729 Binance listings, +2.6 % a trade, plus every year): short 4 h after the
first hour of trading, stop +30 % (filled 1 % worse), exit after 14 days; skip a coin that rose ≥ 10 % in its first 4 h; funding
paid / received by the short; costs 0.14 % a trade. Question: does it hold on venues that list MORE coins (MEXC lists far more,
earlier and smaller), and on Hyperliquid (on-chain perps)? A venue that holds adds trades — the same edge, more often.

MEXC: public contract API (contract.mexc.com): contract list, 1-hour candles from the listing, funding history.
Hyperliquid: public info API: meta, 1-hour candles (only the latest 5 000 hours exist → listings of the last ~200 days),
funding history. Listing time = the first candle. Stdlib + numpy."""
import datetime as dt
import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np

H, DAY = 3_600_000, 86_400_000
COST, SLIP, STOP, DELAY, HOLD_D = 0.0014, 0.01, 0.30, 4, 14
UA = {"User-Agent": "Mozilla/5.0 cryptonova-research/1.0", "Content-Type": "application/json"}
SINCE = int(dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)


def note(title, msg):
    print(f"{title}: {msg}", flush=True)
    m = str(msg).replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    print(f"::notice title={title}::{m}", flush=True)


def get(url, body=None, tries=4):
    for a in range(tries):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=UA,
                                         method="POST" if body is not None else "GET")
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(1.5 * (a + 1))
    raise RuntimeError(str(err)[:160])


def trade(L):
    """→ (net return on notional, funding part, listed ms, first-4h move, first-4h quote turnover) or None."""
    t, o, h, c = L["t"], L["o"], L["h"], L["c"]
    e, x = DELAY, DELAY + HOLD_D * 24
    if x >= len(t) or t[x] - t[e] > (HOLD_D * 24 + 6) * H:
        return None
    ent = o[e]
    if ent <= 0:
        return None
    exit_i, ret = x, None
    hit = np.where(h[e:x + 1] >= ent * (1 + STOP))[0]
    if len(hit):
        exit_i, ret = e + int(hit[0]), -(STOP + SLIP)
    if ret is None:
        ret = 1 - c[x] / ent
    m = (L["ft"] >= t[e]) & (L["ft"] < t[exit_i] + H)
    fund = float(L["fv"][m].sum()) if len(L["fv"]) else 0.0
    m4 = c[DELAY - 1] / o[0] - 1
    q4 = float(L["q"][:DELAY].sum())
    return float(ret + fund - COST), fund, int(t[0]), float(m4), q4


# ------------------------------------------------------------------------------------------------ MEXC
MX = "https://contract.mexc.com/api/v1/contract"


def mexc_list():
    d = get(f"{MX}/detail")
    rows = d.get("data") or []
    out = []
    for r in rows:
        s = r.get("symbol") or ""
        if s.endswith("_USDT"):
            out.append((s, int(r.get("createTime") or r.get("openingTime") or 0)))
    return out, (sorted(rows[0].keys()) if rows else [])


def mexc_load(sym, created):
    start = max(created // 1000 - 3600, int(SINCE / 1000) - 86400) if created else int(SINCE / 1000)
    d = get(f"{MX}/kline/{sym}?interval=Min60&start={start}&end={start + 2000 * 3600}")
    k = d.get("data") or {}
    if not k or not k.get("time") or len(k["time"]) < DELAY + HOLD_D * 24 + 2:
        return None
    t = np.array(k["time"], dtype=np.int64) * 1000
    if created and t[0] > created + 2 * DAY:                 # the data does not start at the listing
        return None
    if t[0] < SINCE:
        return None
    o, h, c = (np.array(k[x], dtype=float) for x in ("open", "high", "close"))
    q = np.array(k.get("amount") or [0] * len(t), dtype=float)
    fr = get(f"{MX}/funding_rate/history?symbol={sym}&page_num=1&page_size=100")
    lst = ((fr.get("data") or {}).get("resultList") or []) if isinstance(fr.get("data"), dict) else []
    ft = np.array([int(x.get("settleTime") or 0) for x in lst], dtype=np.int64)
    fv = np.array([float(x.get("fundingRate") or 0) for x in lst])
    return {"sym": sym, "t": t, "o": o, "h": h, "c": c, "q": q, "ft": ft, "fv": fv}


# ------------------------------------------------------------------------------------------------ Hyperliquid
HL = "https://api.hyperliquid.xyz/info"


def hl_list():
    m = get(HL, {"type": "meta"})
    return [u["name"] for u in m.get("universe") or [] if not u.get("isDelisted")]


def hl_load(coin):
    now = int(time.time() * 1000)
    k = get(HL, {"type": "candleSnapshot", "req": {"coin": coin, "interval": "1h", "startTime": now - 5000 * H, "endTime": now}})
    if not isinstance(k, list) or len(k) < DELAY + HOLD_D * 24 + 2:
        return None
    t = np.array([int(x["t"]) for x in k], dtype=np.int64)
    if t[0] <= now - 4990 * H:                                 # older than the window → the listing is not in the data
        return None
    o, h, c = (np.array([float(x[f]) for x in k]) for f in ("o", "h", "c"))
    q = np.array([float(x["v"]) * float(x["c"]) for x in k])
    fh = get(HL, {"type": "fundingHistory", "coin": coin, "startTime": int(t[0]), "endTime": int(t[0]) + 20 * DAY})
    ft = np.array([int(x["time"]) for x in fh or []], dtype=np.int64)
    fv = np.array([float(x["fundingRate"]) for x in fh or []])
    return {"sym": coin, "t": t, "o": o, "h": h, "c": c, "q": q, "ft": ft, "fv": fv}


def summarize(name, trs):
    if not trs:
        return {"n": 0}
    r = np.array([x[0] for x in trs])
    keep = [x for x in trs if x[3] < 0.10]
    q = np.array([x[4] for x in keep]) if keep else np.array([])
    terc = np.quantile(q, [1 / 3, 2 / 3]) if len(q) >= 9 else None
    by_q = {}
    for x in trs:
        k = dt.datetime.fromtimestamp(x[2] / 1000, dt.timezone.utc)
        by_q.setdefault(f"{k.year}Q{(k.month - 1) // 3 + 1}", []).append(x[0])
    out = {"listings": len(trs), "per_month": round(len(trs) / max(1, (max(x[2] for x in trs) - min(x[2] for x in trs)) / (30 * DAY)), 1),
           "avg_pct": round(float(r.mean()) * 100, 2), "median_pct": round(float(np.median(r)) * 100, 2), "win_pct": round(float((r > 0).mean()) * 100),
           "stopped_pct": round(float(np.mean([x[0] <= -(STOP + SLIP) for x in trs])) * 100), "funding_avg_pct": round(float(np.mean([x[1] for x in trs])) * 100, 2),
           "skip_pump_avg_pct": round(float(np.mean([x[0] for x in keep])) * 100, 2) if keep else None, "skip_pump_n": len(keep),
           "by_quarter": {k: [round(float(np.mean(v)) * 100, 2), len(v)] for k, v in sorted(by_q.items())}}
    if terc is not None:
        out["turnover_terciles_pct"] = [round(float(np.mean([x[0] for x in keep if lo <= x[4] < hi])) * 100, 2)
                                        for lo, hi in ((-1, terc[0]), (terc[0], terc[1]), (terc[1], 1e30))]
    # account: 5 % of equity per listing (and 20 % «turbo»), compounding, at most 20 open, realised at the exit
    for share in (0.05, 0.20):
        ev = sorted([(x[2] + DELAY * H, 0, i) for i, x in enumerate(keep)] + [(x[2] + (DELAY + HOLD_D * 24) * H, 1, i) for i, x in enumerate(keep)])
        E, size, n_open, curve = 1.0, {}, 0, []
        for tt, kind, i in ev:
            if kind == 0:
                if n_open < 20:
                    size[i] = E * share
                    n_open += 1
            elif i in size:
                E += size.pop(i) * keep[i][0]
                n_open -= 1
                curve.append((tt, E))
        mo, last, prev = {}, {}, 1.0
        for tt, e in curve:
            last[dt.datetime.fromtimestamp(tt / 1000, dt.timezone.utc).strftime("%Y-%m")] = e
        for m in sorted(last):
            mo[m] = last[m] / prev - 1
            prev = last[m]
        v = np.array(list(mo.values())) if mo else np.array([0.0])
        eqs = np.array([e for _, e in curve]) if curve else np.array([1.0])
        out[f"account_{int(share * 100)}pct"] = {"total_x": round(float(E), 2), "avg_month_pct": round(float(v.mean()) * 100, 1),
                                                 "months_plus_pct": round(float((v > 0).mean()) * 100),
                                                 "max_dd_pct": round(float((eqs / np.maximum.accumulate(eqs) - 1).min()) * 100)}
    note(f"listings more · {name}", json.dumps(out, ensure_ascii=False))
    return out


def main():
    try:
        syms, keys = mexc_list()
        note("listings more · mexc contracts", json.dumps({"n": len(syms), "keys": keys[:40],
                                                            "with_create_time": sum(1 for _, c in syms if c), "sample": syms[:3]}))
        cand = [(s, c) for s, c in syms if not c or c >= SINCE - DAY]
        with ThreadPoolExecutor(6) as ex:
            data = [d for d in ex.map(lambda a: _safe(mexc_load, *a), cand) if d]
        summarize("MEXC since 2024", [x for x in (trade(L) for L in data) if x])
    except Exception as e:  # noqa: BLE001
        note("listings more · mexc error", str(e)[:300])
    try:
        coins = hl_list()
        with ThreadPoolExecutor(4) as ex:
            data = [d for d in ex.map(lambda c: _safe(hl_load, c), coins) if d]
        note("listings more · hyperliquid coins", json.dumps({"universe": len(coins), "listed_in_window": len(data)}))
        summarize("Hyperliquid last ~200 days", [x for x in (trade(L) for L in data) if x])
    except Exception as e:  # noqa: BLE001
        note("listings more · hyperliquid error", str(e)[:300])


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001
        return None


if __name__ == "__main__":
    main()
