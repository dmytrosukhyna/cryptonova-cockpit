"""Gold candles for the «Золото» test desk study (04.11.2026) — GitHub Actions only (the research sandbox cannot reach exchanges).

Writes $GOLD_DIR/gold_desk_data.json → the release asset lab-data-gold/gold_desk_data.json (.github/workflows/research-gold-desk.yml),
which scripts/gold_desk_study.py reads. Public data only, no keys:
  paxg_4h  PAXGUSDT spot 4-hour klines, Binance archive (data.binance.vision), 2022-01 → today (2022 = indicator warm-up)
  okx      OKX candles 4H and 1Dutc of XAUT-USDT, PAXG-USDT (spot) and XAU-USDT-SWAP — as far back as OKX serves them
  xau_1d   gold itself (Yahoo GC=F, COMEX front month) daily closes — a check that PAXG follows gold
  okx_instruments  which gold instruments OKX lists (spot / swap), their state and listing time
Bars are [t ms, o, h, l, c, quote volume], oldest first. Stdlib + numpy (the archive reader of scripts/gold_study.py)."""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gold_study as gs                                                               # noqa: E402

OUT = os.environ.get("GOLD_DIR", "/tmp/gold")
T0 = int(dt.datetime(2022, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
OKX = "https://www.okx.com"


def okx_all(inst: str, bar: str, t0: int = T0, pages: int = 200) -> list:
    """OKX candles back to t0 (history-candles pages of 100), closed bars only, oldest first"""
    out, after = {}, None
    for _ in range(pages):
        path = "/api/v5/market/history-candles"
        q = f"instId={inst}&bar={bar}&limit=100" + (f"&after={after}" if after else "")
        s, d = gs.get(f"{OKX}{path}?{q}", tries=3)
        rows = (d or {}).get("data") if isinstance(d, dict) else None
        if s != 200 or not rows:
            break
        for r in rows:
            if len(r) > 8 and str(r[8]) != "1":
                continue
            vol = r[7] if len(r) > 7 and r[7] not in ("", None) else r[5]
            out[int(r[0])] = [int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(vol)]
        after = rows[-1][0]
        if int(after) <= t0 or len(rows) < 100:
            break
    return [out[k] for k in sorted(out)]


def yahoo(sym: str = "GC=F") -> list:
    s, d = gs.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5y&interval=1d", tries=3)
    try:
        r = d["chart"]["result"][0]
        q = r["indicators"]["quote"][0]
        return [[int(t) * 1000, o, h, lo, c, v or 0] for t, o, h, lo, c, v in
                zip(r["timestamp"], q["open"], q["high"], q["low"], q["close"], q["volume"]) if c is not None]
    except Exception:  # noqa: BLE001
        gs.note("gold data · yahoo", f"HTTP {s}", "warning")
        return []


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    doc = {"made": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"), "okx": {}}
    a, months = gs.vision_series("spot", "PAXGUSDT", "4h")
    doc["paxg_4h"] = [[int(r[0]), *map(float, r[1:6])] for r in a if int(r[0]) >= T0] if a is not None else []
    doc["paxg_months"] = [months[0], months[-1]] if months else []
    for inst in ("XAUT-USDT", "PAXG-USDT", "XAU-USDT-SWAP"):
        doc["okx"][inst] = {"4H": okx_all(inst, "4H"), "1Dutc": okx_all(inst, "1Dutc", pages=20)}
    doc["xau_1d"] = yahoo()
    inst = {}
    for it in ("SPOT", "SWAP", "FUTURES"):
        s, d = gs.get(f"{OKX}/api/v5/public/instruments?instType={it}", tries=2)
        rows = (d or {}).get("data") or [] if isinstance(d, dict) else []
        inst[it] = [{k: x.get(k) for k in ("instId", "state", "listTime", "ctVal", "ctValCcy", "lever", "minSz", "tickSz")}
                    for x in rows if any(g in str(x.get("instId")) for g in ("XAU", "PAXG"))] if s == 200 else f"HTTP {s}"
    doc["okx_instruments"] = inst
    s, d = gs.get(f"{OKX}/api/v5/public/funding-rate-history?instId=XAU-USDT-SWAP&limit=100", tries=2)
    doc["okx_xau_funding"] = [[int(x["fundingTime"]), float(x["realizedRate"] or x["fundingRate"])] for x in (d or {}).get("data") or []] \
        if isinstance(d, dict) else f"HTTP {s}"
    with open(f"{OUT}/gold_desk_data.json", "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"))
    span = lambda xs: f"{len(xs)} bars {dt.datetime.utcfromtimestamp(xs[0][0] / 1000):%Y-%m-%d}…{dt.datetime.utcfromtimestamp(xs[-1][0] / 1000):%Y-%m-%d}" if xs else "none"  # noqa: E731
    gs.note("gold data", json.dumps({"paxg_4h": span(doc["paxg_4h"]), "xau_1d": span(doc["xau_1d"]),
                                     **{f"{k} {b}": span(v[b]) for k, v in doc["okx"].items() for b in v},
                                     "okx_instruments": {k: (len(v) if isinstance(v, list) else v) for k, v in inst.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
