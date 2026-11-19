"""19.11.2026 (owner: «был ли я прав по направлению рынка и по монетам»): hourly candles of the coins the owner talked about, from
03.11 to now — OKX spot, else OKX swap, else Bybit spot / linear, else Binance spot. Public market data only, research only.
Usage: owner_calls_prices.py OUT.json"""
import json, sys, time, urllib.request

COINS = ["BTC", "ETH", "SOL", "ZEC", "VVV", "MORPHO", "GRASS", "LIT", "ADA", "UNI", "PEPE", "AAVE", "ENA", "ONDO", "OP", "ARB", "EIGEN",
         "AVAX", "QNT", "PUMP", "XTEN", "HYPE"]
START = int(time.mktime(time.strptime("2026-11-03", "%Y-%m-%d")) * 1000)
H = 3_600_000


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "cnq-research"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def okx(inst):
    out, after = {}, ""
    for _ in range(40):
        d = get(f"https://www.okx.com/api/v5/market/history-candles?instId={inst}&bar=1H&limit=100" + (f"&after={after}" if after else ""))
        rows = d.get("data") or []
        if not rows:
            break
        for r in rows:
            out[int(r[0])] = [int(r[0])] + [float(x) for x in r[1:5]]
        after = rows[-1][0]
        if int(after) < START:
            break
        time.sleep(0.12)
    return sorted(v for k, v in out.items() if k >= START)


def bybit(sym, cat):
    out, end = {}, int(time.time() * 1000)
    for _ in range(10):
        d = get(f"https://api.bybit.com/v5/market/kline?category={cat}&symbol={sym}&interval=60&limit=1000&end={end}")
        rows = (d.get("result") or {}).get("list") or []
        if not rows:
            break
        for r in rows:
            out[int(r[0])] = [int(r[0])] + [float(x) for x in r[1:5]]
        end = int(rows[-1][0]) - 1
        if end < START:
            break
    return sorted(v for k, v in out.items() if k >= START)


def binance(sym):
    d = get(f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=1h&startTime={START}&limit=1000")
    return [[int(r[0])] + [float(x) for x in r[1:5]] for r in d]


res, src = {}, {}
for c in COINS:
    for name, fn in ((f"okx {c}-USDT", lambda: okx(f"{c}-USDT")), (f"okx {c}-USDT-SWAP", lambda: okx(f"{c}-USDT-SWAP")),
                     (f"bybit spot {c}USDT", lambda: bybit(f"{c}USDT", "spot")), (f"bybit linear {c}USDT", lambda: bybit(f"{c}USDT", "linear")),
                     (f"binance {c}USDT", lambda: binance(f"{c}USDT"))):
        try:
            k = fn()
        except Exception as e:  # noqa: BLE001
            k = []
            print(f"{name}: {type(e).__name__}")
        if len(k) >= 24:
            res[c], src[c] = k, name
            break
    print(f"::notice title={c}::{src.get(c, 'нет данных')} · {len(res.get(c, []))} ч" +
          (f" · {res[c][0][4]} → {res[c][-1][4]}" if c in res else ""))
json.dump({"made": int(time.time() * 1000), "src": src, "k1h": res}, open(sys.argv[1], "w"))
