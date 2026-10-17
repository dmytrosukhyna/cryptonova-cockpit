"""Macro factor history for docs/research/macro_factors.md — runs in GitHub Actions (research-macro.yml); the research
container cannot reach market-data sites, so the data travel back as job annotations (lzma + base64, 4000-char chunks).

  python scripts/macro_factors_fetch.py --fetch pack.b64 --md status.md   download everything, write the packed blob
  python scripts/macro_factors_fetch.py --emit pack.b64 START COUNT       print annotations for chunks START … START+COUNT-1
  python scripts/macro_factors_fetch.py --probe probe.md                  live-source check for cnq/agents/macro_watch.py
  python scripts/macro_factors_fetch.py --decode ann_dir out.json         (research container) annotations → series JSON

Reachability from Actions (probe of 17.10.2026): plain urllib → Yahoo answers 429, FRED times out, Stooq shows a JavaScript
wall; US Treasury and CBOE answer. So:
  Yahoo     via yfinance (curl_cffi browser TLS): daily since 2021, hourly for the last 730 days
  Treasury  https://home.treasury.gov/.../daily-treasury-rates.csv/<year>/all?type=daily_treasury_yield_curve   (2Y, 10Y, 3M)
            and real yields (type=daily_treasury_real_yield_curve, 10Y TIPS)
  CBOE      https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv
  NY Fed    reverse repo (RRP) https://markets.newyorkfed.org/api/rp/reverserepo/propositions/search.json
  Fiscal    Treasury General Account (TGA) https://api.fiscaldata.treasury.gov/.../dts/operating_cash_balance
  FRED      fredgraph.csv (WALCL, M2SL, BAMLH0A0HYM2 …) — best effort, usually times out from Actions
Series format after decode: {key: {"src", "freq", "t": [unix seconds], "v": [float]}}; Yahoo bars are stamped at the bar
OPEN (hourly close = last price in that hour); daily official series are stamped at 00:00 UTC of the observation date."""
from __future__ import annotations

import base64
import csv
import datetime as dt
import io
import json
import lzma
import math
import os
import sys
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "text/csv,application/json,*/*"}
START = "2021-01-01"
Y_DAILY = ["ES=F", "NQ=F", "RTY=F", "DX-Y.NYB", "JPY=X", "EURUSD=X", "CNY=X", "CL=F", "BZ=F", "GC=F", "HG=F", "^TNX", "^FVX",
           "^IRX", "^VIX", "^MOVE", "^N225", "^GSPC", "^IXIC", "BTC-USD", "ETH-USD", "2YY=F", "ZN=F", "HYG", "TLT", "UUP"]
Y_HOURLY = ["ES=F", "NQ=F", "RTY=F", "DX-Y.NYB", "JPY=X", "EURUSD=X", "CL=F", "BZ=F", "GC=F", "HG=F", "^TNX", "^VIX", "2YY=F",
            "ZN=F", "^N225", "BTC-USD"]
FRED = ["WALCL", "M2SL", "BAMLH0A0HYM2", "DGS10", "DEXJPUS"]
CHUNK = 4000


def get(url: str, timeout: float = 20.0, tries: int = 2) -> bytes:
    last = None
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e; time.sleep(1 + 2 * k)
    raise RuntimeError(f"{url}: {last}")


def ts_day(s: str, fmt: str) -> int:
    return int(dt.datetime.strptime(s, fmt).replace(tzinfo=dt.timezone.utc).timestamp())


def fred(sid: str) -> dict:
    raw = get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={START}", timeout=60, tries=1).decode()
    rd = csv.reader(io.StringIO(raw)); next(rd)
    t, v = [], []
    for row in rd:
        if len(row) >= 2 and row[1] not in ("", ".", "NA"):
            t.append(ts_day(row[0], "%Y-%m-%d")); v.append(float(row[1]))
    return {f"fred:{sid}:1d": (t, v, {})}


def treasury() -> dict:
    """daily par yields (nominal 3M/2Y/10Y) and real 10Y; one CSV per year."""
    cols = {"3 Mo": "UST3M", "2 Yr": "UST2Y", "10 Yr": "UST10Y"}
    out = {k: {} for k in list(cols.values()) + ["REAL10Y"]}
    for y in range(int(START[:4]), dt.date.today().year + 1):
        for typ in ("daily_treasury_yield_curve", "daily_treasury_real_yield_curve"):
            url = (f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/{y}/all"
                   f"?type={typ}&field_tdr_date_value={y}&page&_format=csv")
            rd = csv.DictReader(io.StringIO(get(url).decode()))
            for row in rd:
                d = ts_day(row["Date"], "%m/%d/%Y")
                if typ.endswith("real_yield_curve"):
                    x = row.get("10 YR") or row.get("10 Yr")
                    if x: out["REAL10Y"][d] = float(x)
                else:
                    for c, k in cols.items():
                        if row.get(c): out[k][d] = float(row[c])
    return {f"treasury:{k}:1d": (sorted(m), [m[t] for t in sorted(m)], {}) for k, m in out.items() if m}


def cboe_vix() -> dict:
    rd = csv.DictReader(io.StringIO(get("https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv").decode()))
    t, v = [], []
    for row in rd:
        d = ts_day(row["DATE"], "%m/%d/%Y")
        if d >= ts_day(START, "%Y-%m-%d"):
            t.append(d); v.append(float(row["CLOSE"]))
    return {"cboe:VIX:1d": (t, v, {})}


def nyfed_rrp() -> dict:
    url = f"https://markets.newyorkfed.org/api/rp/reverserepo/propositions/search.json?startDate={START}&endDate={dt.date.today().isoformat()}"
    ops = json.loads(get(url, timeout=60))["repo"]["operations"]
    m = {}
    for o in ops:
        d = ts_day(o["operationDate"], "%Y-%m-%d"); m[d] = m.get(d, 0.0) + float(o.get("totalAmtAccepted") or 0) / 1e9   # $ bln
    return {"nyfed:RRP:1d": (sorted(m), [m[t] for t in sorted(m)], {})}


def fiscal_tga() -> dict:
    base = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/dts/operating_cash_balance"
    m, page, kinds = {}, 1, set()
    while True:
        url = f"{base}?filter=record_date:gte:{START}&fields=record_date,account_type,open_today_bal,close_today_bal&page[size]=5000&page[number]={page}"
        d = json.loads(get(url, timeout=60))
        for r in d["data"]:
            a = (r.get("account_type") or "").lower(); kinds.add(a[:60])
            if "closing balance" in a:
                x = r.get("close_today_bal") if r.get("close_today_bal") not in (None, "null") else r.get("open_today_bal")
                if x not in (None, "null"):
                    m[ts_day(r["record_date"], "%Y-%m-%d")] = float(x) / 1000.0        # $ mln → $ bln
        if page >= int(d.get("meta", {}).get("total-pages", 1)): break
        page += 1
    if not m:
        raise RuntimeError(f"no closing balance rows; account types seen: {sorted(kinds)[:8]}")
    return {"fiscal:TGA:1d": (sorted(m), [m[t] for t in sorted(m)], {})}


def yahoo_yf(syms: list[str], interval: str) -> dict:
    import yfinance as yf
    out = {}
    for s in syms:
        try:
            tk = yf.Ticker(s)
            h = tk.history(period="730d", interval="1h", auto_adjust=False, prepost=False) if interval == "1h" else \
                tk.history(start=START, interval="1d", auto_adjust=False)
            h = h[h["Close"].notna()]
            t = [int(x.timestamp()) for x in h.index]; v = [float(x) for x in h["Close"]]
            tz = str(getattr(h.index, "tz", "") or "")
            out[f"yahoo:{s}:{interval}"] = (t, v, {"tz": tz}) if t else RuntimeError("empty")
        except Exception as e:  # noqa: BLE001
            out[f"yahoo:{s}:{interval}"] = RuntimeError(str(e)[:200])
        time.sleep(0.3)
    return out


def pack_series(t: list[int], v: list[float]) -> dict:
    """delta-encode: times in seconds (first absolute), values as integers at 6 significant digits."""
    if not v:
        return {"t": [], "v": [], "s": 1}
    mx = max(abs(x) for x in v) or 1.0
    s = 10 ** (5 - int(math.floor(math.log10(mx))))
    iv = [int(round(x * s)) for x in v]
    return {"t": [t[0]] + [b - a for a, b in zip(t, t[1:])], "v": [iv[0]] + [b - a for a, b in zip(iv, iv[1:])], "s": s}


def unpack_series(p: dict) -> tuple[list[int], list[float]]:
    t, v, acc, acv = [], [], 0, 0
    for a, b in zip(p["t"], p["v"]):
        acc += a; acv += b; t.append(acc); v.append(acv / p["s"])
    return t, v


def do_fetch(out: str, md: str) -> None:
    from concurrent.futures import ThreadPoolExecutor
    tasks = [("treasury", treasury), ("cboe", cboe_vix), ("nyfed", nyfed_rrp), ("fiscal", fiscal_tga),
             ("yahoo 1d", lambda: yahoo_yf(Y_DAILY, "1d")), ("yahoo 1h", lambda: yahoo_yf(Y_HOURLY, "1h"))] + \
            [(f"fred {s}", (lambda s=s: fred(s))) for s in FRED]

    def one(task):
        name, fn = task; t0 = time.time()
        try:
            return name, fn(), None, time.time() - t0
        except Exception as e:  # noqa: BLE001
            return name, {}, str(e)[:240], time.time() - t0
    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(one, tasks))
    data, fails = {}, []
    lines = ["| series | n | first | last | last value |", "|---|---|---|---|---|"]
    f = lambda x: dt.datetime.fromtimestamp(x, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
    for name, got, err, sec in res:
        if err: fails.append(f"{name}: {err} ({sec:.0f}s)")
        for key, val in got.items():
            if isinstance(val, Exception):
                fails.append(f"{key}: {val}"); continue
            t, v, meta = val
            p = pack_series(t, v); p.update(src=key.split(":")[0], freq=key.split(":")[-1], meta=meta)
            data[key] = p
            lines.append(f"| {key} | {len(t)} | {f(t[0]) if t else '-'} | {f(t[-1]) if t else '-'} | {v[-1] if v else '-'} |")
    raw = json.dumps(data, separators=(",", ":")).encode()
    blob = base64.b64encode(lzma.compress(raw, preset=9 | lzma.PRESET_EXTREME)).decode()
    open(out, "w").write(blob)
    n = (len(blob) + CHUNK - 1) // CHUNK
    head = (f"built {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC; series {len(data)}; json {len(raw)} B; "
            f"packed {len(blob)} chars = {n} chunks of {CHUNK}; failures {len(fails)}; times: "
            + ", ".join(f"{name} {sec:.0f}s" for name, _, _, sec in res))
    open(md, "w").write(head + "\n\nFAILURES\n" + "\n".join(fails) + "\n\n" + "\n".join(lines) + "\n")
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
    today = dt.date.today()
    tests = [("CBOE delayed _VIX", "https://cdn.cboe.com/api/global/delayed_quotes/quotes/_VIX.json"),
             ("CBOE delayed _SPX", "https://cdn.cboe.com/api/global/delayed_quotes/quotes/_SPX.json"),
             ("CBOE delayed _NDX", "https://cdn.cboe.com/api/global/delayed_quotes/quotes/_NDX.json"),
             ("CBOE delayed UUP", "https://cdn.cboe.com/api/global/delayed_quotes/quotes/UUP.json"),
             ("CBOE delayed FXY", "https://cdn.cboe.com/api/global/delayed_quotes/quotes/FXY.json"),
             ("CBOE delayed GLD", "https://cdn.cboe.com/api/global/delayed_quotes/quotes/GLD.json"),
             ("CBOE delayed USO", "https://cdn.cboe.com/api/global/delayed_quotes/quotes/USO.json"),
             ("Treasury month CSV", f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/all/{today:%Y%m}?type=daily_treasury_yield_curve&field_tdr_date_value_month={today:%Y%m}&page&_format=csv"),
             ("ECB eurofxref daily", "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"),
             ("Frankfurter latest USD", "https://api.frankfurter.app/latest?from=USD&to=JPY,EUR"),
             ("NY Fed RRP last", "https://markets.newyorkfed.org/api/rp/reverserepo/propositions/search.json?startDate=" + (today - dt.timedelta(days=7)).isoformat()),
             ("Google Finance USDJPY", "https://www.google.com/finance/quote/USD-JPY"),
             ("Hyperliquid meta (crypto venue, sanity)", "https://api.hyperliquid.xyz/info")]
    for name, url in tests:
        t0 = time.time()
        try:
            if "hyperliquid" in url:
                req = urllib.request.Request(url, data=b'{"type":"meta"}', headers={**UA, "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=15) as r: b = r.read()
            else:
                b = get(url, timeout=15, tries=1)
            out.append(f"OK   {name}: {len(b)} B in {time.time() - t0:.1f}s; head: {b[:140]!r}")
        except Exception as e:  # noqa: BLE001
            out.append(f"FAIL {name}: {str(e)[:160]}")
    key = os.environ.get("TWELVE_DATA_KEY", "")
    if key:
        for sym in ("USD/JPY", "XAU/USD", "WTI/USD", "DXY", "SPY"):
            try:
                d = json.loads(get(f"https://api.twelvedata.com/quote?symbol={urllib.parse.quote(sym)}&apikey={key}", timeout=15, tries=1))
                out.append(f"TD   {sym}: " + (f"close {d.get('close')} prev {d.get('previous_close')} at {d.get('datetime')}" if "close" in d else str(d)[:150]))
            except Exception as e:  # noqa: BLE001
                out.append(f"TD   {sym}: FAIL {str(e)[:120]}")
            time.sleep(8)                                    # free plan: 8 requests per minute
    else:
        out.append("TD   no TWELVE_DATA_KEY in the environment")
    try:
        import yfinance as yf
        for s in ("DX-Y.NYB", "JPY=X", "ES=F", "^TNX"):
            t0 = time.time()
            h = yf.Ticker(s).history(period="5d", interval="1d")
            out.append(f"YF   {s}: {len(h)} rows in {time.time() - t0:.1f}s; last {float(h['Close'].iloc[-1]) if len(h) else None}")
    except Exception as e:  # noqa: BLE001
        out.append(f"YF   FAIL {str(e)[:200]}")
    try:
        from curl_cffi import requests as cr
        r = cr.get("https://query1.finance.yahoo.com/v8/finance/chart/JPY%3DX?interval=1d&range=10d", impersonate="chrome", timeout=15)
        out.append(f"CURL_CFFI yahoo chart: HTTP {r.status_code}, {len(r.content)} B")
    except Exception as e:  # noqa: BLE001
        out.append(f"CURL_CFFI FAIL {str(e)[:200]}")
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
