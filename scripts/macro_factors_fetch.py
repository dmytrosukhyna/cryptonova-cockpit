"""Macro factor history for docs/research/macro_factors.md — runs in GitHub Actions (research-macro.yml); the research
container cannot reach market-data sites, so the data travel back as job annotations (lzma + base64, 4000-char chunks).

  python scripts/macro_factors_fetch.py --fetch pack.b64 --md status.md   download everything, write the packed blob
  python scripts/macro_factors_fetch.py --emit pack.b64 START COUNT       print annotations for chunks START … START+COUNT-1
  python scripts/macro_factors_fetch.py --probe probe.md                  live-source check for cnq/agents/macro_watch.py
  python scripts/macro_factors_fetch.py --decode ann_dir out.json         (research container) annotations → series JSON

Sources (all free, no key):
  FRED   https://fred.stlouisfed.org/graph/fredgraph.csv?id=<ID>&cosd=2021-01-01      daily / weekly / monthly
  Yahoo  https://query1.finance.yahoo.com/v8/finance/chart/<SYM>?interval=1d|1h      daily since 2021, hourly 730 days
Series format after decode: {name: {"src", "freq", "t": [unix seconds], "v": [float]}}; Yahoo bars are stamped at the bar
OPEN (hourly close = last price in that hour); FRED values are stamped at 00:00 UTC of the observation date."""
from __future__ import annotations

import base64
import csv
import datetime as dt
import io
import json
import lzma
import os
import sys
import time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "text/csv,application/json,*/*"}
START = "2021-01-01"
FRED = ["DGS10", "DGS2", "DGS3MO", "T10Y2Y", "T10YIE", "DFII10", "DTWEXBGS", "DEXJPUS", "DEXUSEU", "DCOILWTICO", "DCOILBRENTEU",
        "VIXCLS", "SP500", "NASDAQCOM", "BAMLH0A0HYM2", "WALCL", "WTREGEN", "RRPONTSYD", "M2SL", "NFCI", "DFF"]
Y_DAILY = ["ES=F", "NQ=F", "RTY=F", "DX-Y.NYB", "JPY=X", "EURUSD=X", "CNY=X", "CL=F", "BZ=F", "GC=F", "HG=F", "^TNX", "^FVX",
           "^IRX", "^VIX", "^MOVE", "^N225", "BTC-USD", "ETH-USD", "2YY=F", "ZN=F", "COIN", "MSTR"]
Y_HOURLY = ["ES=F", "NQ=F", "RTY=F", "DX-Y.NYB", "JPY=X", "EURUSD=X", "CL=F", "BZ=F", "GC=F", "HG=F", "^TNX", "^VIX", "2YY=F",
            "ZN=F", "^N225", "BTC-USD"]
CHUNK = 4000


def get(url: str, timeout: float = 30.0, tries: int = 3) -> bytes:
    last = None
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e; time.sleep(2 + 3 * k)
    raise RuntimeError(f"{url}: {last}")


def fred(sid: str) -> tuple[list[int], list[float]]:
    raw = get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={START}").decode()
    rd = csv.reader(io.StringIO(raw)); next(rd)
    t, v = [], []
    for row in rd:
        if len(row) < 2 or row[1] in ("", ".", "NA"):
            continue
        try:
            x = float(row[1])
        except ValueError:
            continue
        d = dt.datetime.strptime(row[0], "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
        t.append(int(d.timestamp())); v.append(x)
    return t, v


def yahoo(sym: str, interval: str) -> tuple[list[int], list[float], dict]:
    q = urllib.request.quote(sym, safe="")
    if interval == "1h":
        rng = "range=730d"
    else:
        p1 = int(dt.datetime.strptime(START, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())
        rng = f"period1={p1}&period2={int(time.time())}"
    last = None
    for host in ("query1", "query2"):
        try:
            d = json.loads(get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{q}?interval={interval}&{rng}&includePrePost=false"))
            res = d["chart"]["result"][0]
            ts = res.get("timestamp") or []
            cl = res["indicators"]["quote"][0].get("close") or []
            t, v = [], []
            for a, b in zip(ts, cl):
                if b is not None:
                    t.append(int(a)); v.append(float(b))
            meta = res.get("meta", {})
            return t, v, {"tz": meta.get("exchangeTimezoneName"), "gmtoffset": meta.get("gmtoffset"), "cur": meta.get("currency")}
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"yahoo {sym} {interval}: {last}")


def pack_series(t: list[int], v: list[float]) -> dict:
    """delta-encode: times in seconds (first absolute), values as integers at 6 significant digits."""
    if not v:
        return {"t": [], "v": [], "s": 1}
    mx = max(abs(x) for x in v) or 1.0
    import math
    s = 10 ** (5 - int(math.floor(math.log10(mx))))           # ~6 significant digits for the largest value
    iv = [int(round(x * s)) for x in v]
    dtt = [t[0]] + [b - a for a, b in zip(t, t[1:])]
    dv = [iv[0]] + [b - a for a, b in zip(iv, iv[1:])]
    return {"t": dtt, "v": dv, "s": s}


def unpack_series(p: dict) -> tuple[list[int], list[float]]:
    t, v, acc, acv = [], [], 0, 0
    for a, b in zip(p["t"], p["v"]):
        acc += a; acv += b; t.append(acc); v.append(acv / p["s"])
    return t, v


def do_fetch(out: str, md: str) -> None:
    data, lines, fails = {}, ["| series | source | freq | n | first | last | last value |", "|---|---|---|---|---|---|---|"], []
    jobs = [("fred", s, "1d") for s in FRED] + [("yahoo", s, "1d") for s in Y_DAILY] + [("yahoo", s, "1h") for s in Y_HOURLY]
    for src, sid, iv in jobs:
        key = f"{src}:{sid}:{iv}"
        try:
            if src == "fred":
                t, v = fred(sid); meta = {}
            else:
                t, v, meta = yahoo(sid, iv)
            p = pack_series(t, v); p.update(src=src, freq=iv, meta=meta)
            data[key] = p
            f = lambda x: dt.datetime.fromtimestamp(x, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
            lines.append(f"| {sid} | {src} | {iv} | {len(t)} | {f(t[0]) if t else '-'} | {f(t[-1]) if t else '-'} | {v[-1] if v else '-'} |")
        except Exception as e:  # noqa: BLE001
            fails.append(f"{key}: {str(e)[:200]}")
        time.sleep(0.4)
    raw = json.dumps(data, separators=(",", ":")).encode()
    blob = base64.b64encode(lzma.compress(raw, preset=9 | lzma.PRESET_EXTREME)).decode()
    open(out, "w").write(blob)
    n = (len(blob) + CHUNK - 1) // CHUNK
    head = (f"built {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC; series {len(data)}; json {len(raw)} B; "
            f"packed {len(blob)} chars = {n} chunks of {CHUNK}; failures {len(fails)}")
    open(md, "w").write(head + "\n\n" + "\n".join(lines) + "\n\nFAILURES\n" + "\n".join(fails) + "\n")
    print(head)


def do_emit(src: str, start: int, count: int) -> None:
    """GitHub keeps ≤ 10 annotations per level per step and ≤ 50 per job: a step emits ≤ 20 (notices and warnings alternate)."""
    blob = open(src).read()
    n = (len(blob) + CHUNK - 1) // CHUNK
    for k in range(start, min(n, start + count)):
        lvl = "notice" if (k - start) % 2 == 0 else "warning"
        print(f"::{lvl} title=macro {k + 1}/{n}::{blob[k * CHUNK:(k + 1) * CHUNK]}")


def do_probe(md: str) -> None:
    """Which live sources answer from Actions (for cnq/agents/macro_watch.py)."""
    out = []
    tests = [("FRED DGS10", f"https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10&cosd={(dt.date.today() - dt.timedelta(days=20)).isoformat()}"),
             ("Yahoo q1 DX-Y.NYB 1d", "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?interval=1d&range=10d"),
             ("Yahoo q2 JPY=X 1d", "https://query2.finance.yahoo.com/v8/finance/chart/JPY%3DX?interval=1d&range=10d"),
             ("Yahoo q1 multi-quote v7", "https://query1.finance.yahoo.com/v7/finance/quote?symbols=ES%3DF,NQ%3DF"),
             ("Yahoo q1 spark", "https://query1.finance.yahoo.com/v8/finance/spark?symbols=ES%3DF,NQ%3DF,%5ETNX&range=5d&interval=1d"),
             ("Stooq ^spx daily", "https://stooq.com/q/d/l/?s=%5Espx&i=d"),
             ("Stooq quote usdjpy", "https://stooq.com/q/l/?s=usdjpy&f=sd2t2ohlcv&h&e=csv"),
             ("Stooq 60m usdjpy", "https://stooq.com/q/d/l/?s=usdjpy&i=60"),
             ("CBOE VIX csv", "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"),
             ("Treasury yields xml", "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/2026/all?type=daily_treasury_yield_curve&field_tdr_date_value=2026&page&_format=csv")]
    for name, url in tests:
        t0 = time.time()
        try:
            b = get(url, timeout=20, tries=1)
            out.append(f"OK   {name}: {len(b)} B in {time.time() - t0:.1f}s; head: {b[:160]!r}")
        except Exception as e:  # noqa: BLE001
            out.append(f"FAIL {name}: {str(e)[:220]}")
    open(md, "w").write("\n".join(out) + "\n")
    print("\n".join(out))


def do_decode(ann_dir: str, out: str) -> None:
    """Join chunks from saved annotation JSON files (GitHub check-run annotations API) and unpack every series."""
    parts = {}
    for f in sorted(os.listdir(ann_dir)):
        if not f.endswith(".json"):
            continue
        for a in json.load(open(os.path.join(ann_dir, f))):
            ti = a.get("title") or ""
            if ti.startswith("macro ") and "/" in ti:
                k, n = ti[6:].split("/")
                parts[int(k)] = (int(n), a["message"])
    n = next(iter(parts.values()))[0]
    miss = [k for k in range(1, n + 1) if k not in parts]
    if miss:
        raise SystemExit(f"missing chunks: {miss[:20]} … ({len(miss)} of {n})")
    blob = "".join(parts[k][1] for k in range(1, n + 1))
    data = json.loads(lzma.decompress(base64.b64decode(blob)))
    res = {}
    for key, p in data.items():
        t, v = unpack_series(p)
        res[key] = {"src": p["src"], "freq": p["freq"], "meta": p.get("meta", {}), "t": t, "v": v}
    json.dump(res, open(out, "w"))
    print(f"decoded {len(res)} series from {n} chunks → {out}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["--fetch"]:
        do_fetch(a[1], a[a.index("--md") + 1] if "--md" in a else "status.md")
    elif a[:1] == ["--emit"]:
        do_emit(a[1], int(a[2]), int(a[3]))
    elif a[:1] == ["--probe"]:
        do_probe(a[1])
    elif a[:1] == ["--decode"]:
        do_decode(a[1], a[2])
    else:
        print(__doc__)
