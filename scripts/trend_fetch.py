"""Trend study data (04.11.2026): BTC/ETH USDT-M perps, 1h klines 2023-01 → now and real funding history.
Public data only, no keys. Sources: data.binance.vision (monthly + daily zips), then the newest hours from
the Binance futures API, falling back to OKX swap candles. Output: trend_data.json (one file, uploaded as artifact)."""
import csv, datetime as dt, io, json, time, urllib.request, zipfile

V = "https://data.binance.vision/data/futures/um"
SYMS = ["BTCUSDT", "ETHUSDT"]
START = dt.date(2023, 1, 1)
TODAY = dt.datetime.now(dt.timezone.utc).date()


def get(url, tries=4):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "cnq-research"}), timeout=60) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code in (404, 403, 451):
                return e.code, b""
        except Exception:
            pass
        time.sleep(1 + k)
    return 0, b""


def zrows(b):
    z = zipfile.ZipFile(io.BytesIO(b))
    rows = list(csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]))))
    return [r for r in rows if r and r[0][:1].isdigit()]


def months():
    y, m = START.year, START.month
    while (y, m) < (TODAY.year, TODAY.month):
        yield f"{y}-{m:02d}"
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def days_this_month():
    d = TODAY.replace(day=1)
    while d < TODAY:
        yield d.isoformat()
        d += dt.timedelta(days=1)


out = {"made": dt.datetime.now(dt.timezone.utc).isoformat(), "kl": {}, "fund": {}, "src": {}}
for s in SYMS:
    K, F, miss = {}, {}, []
    for mo in months():
        c, b = get(f"{V}/monthly/klines/{s}/1h/{s}-1h-{mo}.zip")
        if c == 200:
            for r in zrows(b): K[int(r[0])] = [float(r[1]), float(r[2]), float(r[3]), float(r[4])]
        else:  # month file missing → daily files of that month
            y, m = map(int, mo.split("-"))
            d = dt.date(y, m, 1)
            while d.month == m:
                c2, b2 = get(f"{V}/daily/klines/{s}/1h/{s}-1h-{d}.zip")
                if c2 == 200:
                    for r in zrows(b2): K[int(r[0])] = [float(r[1]), float(r[2]), float(r[3]), float(r[4])]
                else: miss.append(str(d))
                d += dt.timedelta(days=1)
        c, b = get(f"{V}/monthly/fundingRate/{s}/{s}-fundingRate-{mo}.zip")
        if c == 200:
            for r in zrows(b): F[int(r[0])] = float(r[2])
    for d in days_this_month():
        c, b = get(f"{V}/daily/klines/{s}/1h/{s}-1h-{d}.zip")
        if c == 200:
            for r in zrows(b): K[int(r[0])] = [float(r[1]), float(r[2]), float(r[3]), float(r[4])]
        else: miss.append(d)
    # newest hours + recent funding: Binance API, else OKX
    last = max(K) if K else 0
    c, b = get(f"https://fapi.binance.com/fapi/v1/klines?symbol={s}&interval=1h&limit=1500&startTime={last}")
    src = "vision"
    if c == 200:
        for r in json.loads(b): K[int(r[0])] = [float(x) for x in r[1:5]]
        src = "vision+fapi"
        c, b = get(f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={s}&limit=1000&startTime={max(F) if F else 0}")
        if c == 200:
            for r in json.loads(b): F[int(r["fundingTime"])] = float(r["fundingRate"])
    else:
        inst = s.replace("USDT", "-USDT-SWAP")
        after = ""
        for _ in range(20):
            c, b = get(f"https://www.okx.com/api/v5/market/history-candles?instId={inst}&bar=1H&limit=100{after}")
            if c != 200: break
            rows = json.loads(b).get("data", [])
            if not rows: break
            done = False
            for r in rows:
                t = int(r[0])
                if r[8] != "1": continue  # unfinished candle
                if t <= last: done = True; continue
                K.setdefault(t, [float(r[1]), float(r[2]), float(r[3]), float(r[4])])
            if done: break
            after = f"&after={rows[-1][0]}"
        src = "vision+okx" if max(K) > last else "vision"
        c, b = get(f"https://www.okx.com/api/v5/public/funding-rate-history?instId={inst}&limit=100")
        if c == 200:
            for r in json.loads(b).get("data", []):
                t = int(r["fundingTime"])
                if not F or t > max(F): F[t] = float(r["realizedRate"])
    out["kl"][s] = sorted([t] + v for t, v in K.items())
    out["fund"][s] = sorted([t, v] for t, v in F.items())
    out["src"][s] = {"src": src, "missing_days": miss, "bars": len(K), "funding": len(F)}
    print(s, out["src"][s], dt.datetime.fromtimestamp(max(K) / 1000, dt.timezone.utc))
json.dump(out, open("trend_data.json", "w"))
