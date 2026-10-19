"""Gold inside crypto venues — an honest check of the engine's simple rule families on gold (19.10.2026, owner: «на золоте
хорошо зарабатывают — движения и каналы довольно понятные»). Paper / history only.
Runs in GitHub Actions only (.github/workflows/research-gold.yml): this container cannot reach exchange APIs.

Modes
  probe   gold instruments on crypto venues (PAXG / XAUT spot and perps, XAU perps): history length, fees, funding;
          long clean intraday gold sources (Binance archive PAXGUSDT, Dukascopy XAUUSD hour candles).
"""
import datetime as dt
import io
import json
import lzma
import os
import struct
import sys
import time
import urllib.error
import urllib.request
import zipfile

UA = {"User-Agent": "Mozilla/5.0 cryptonova-research/1.0", "Accept": "*/*"}
VISION = "https://data.binance.vision/data"
OUT = os.environ.get("GOLD_DIR", "/tmp/gold")


def note(title, msg, level="notice"):
    print(f"{title}: {msg}", flush=True)
    m = str(msg).replace("%", "%25").replace("\r", "").replace("\n", "%0A")[:4000]
    print(f"::{level} title={title}::{m}", flush=True)


def get(url, body=None, tries=4, raw=False):
    err = None
    for a in range(tries):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                         headers={**UA, "Content-Type": "application/json"}, method="POST" if body is not None else "GET")
            with urllib.request.urlopen(req, timeout=40) as r:
                b = r.read()
                return r.status, (b if raw else json.loads(b.decode()))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(5 + 5 * a)
                continue
            if 400 <= e.code < 500:
                return e.code, None
            err = e
        except Exception as e:  # noqa: BLE001
            err = e
        time.sleep(1.5 * (a + 1))
    return -1, str(err)[:200]


def zip_rows(b):
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        return [ln.split(",") for ln in z.read(z.namelist()[0]).decode().splitlines() if ln[:1].isdigit()]


def duka_hours(sym, year, month0):
    """Dukascopy hour candles of one month → [(ms, o, h, l, c, vol)] (BID side). Format: big-endian, 24-byte records."""
    s, b = get(f"https://datafeed.dukascopy.com/datafeed/{sym}/{year}/{month0:02d}/BID_candles_hour_1.bi5", raw=True)
    if s != 200 or not b:
        return s, []
    raw = lzma.decompress(b)
    base = int(dt.datetime(year, month0 + 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    out = []
    for i in range(0, len(raw) - 23, 24):
        t, o, c, lo, hi, v = struct.unpack(">5if", raw[i:i + 24])
        out.append((base + t * 1000, o / 1000, hi / 1000, lo / 1000, c / 1000, v))
    return s, out


def probe():
    res = {}
    for name, path in [("spot PAXGUSDT 1h 2020-09", "spot/monthly/klines/PAXGUSDT/1h/PAXGUSDT-1h-2020-09.zip"),
                       ("spot PAXGUSDT 1h 2020-01", "spot/monthly/klines/PAXGUSDT/1h/PAXGUSDT-1h-2020-01.zip"),
                       ("spot PAXGUSDT 1h 2019-10", "spot/monthly/klines/PAXGUSDT/1h/PAXGUSDT-1h-2019-10.zip"),
                       ("spot PAXGUSDT 1h 2026-09", "spot/monthly/klines/PAXGUSDT/1h/PAXGUSDT-1h-2026-09.zip"),
                       ("spot XAUTUSDT 1h 2026-09", "spot/monthly/klines/XAUTUSDT/1h/XAUTUSDT-1h-2026-09.zip"),
                       ("um PAXGUSDT 1h 2026-09", "futures/um/monthly/klines/PAXGUSDT/1h/PAXGUSDT-1h-2026-09.zip"),
                       ("um XAUUSDT 1h 2026-09", "futures/um/monthly/klines/XAUUSDT/1h/XAUUSDT-1h-2026-09.zip"),
                       ("um XAUTUSDT 1h 2026-09", "futures/um/monthly/klines/XAUTUSDT/1h/XAUTUSDT-1h-2026-09.zip"),
                       ("um PAXGUSDT funding 2026-09", "futures/um/monthly/fundingRate/PAXGUSDT/PAXGUSDT-fundingRate-2026-09.zip"),
                       ("um XAUUSDT funding 2026-09", "futures/um/monthly/fundingRate/XAUUSDT/XAUUSDT-fundingRate-2026-09.zip")]:
        s, b = get(f"{VISION}/{path}", raw=True, tries=2)
        if s == 200:
            r = zip_rows(b)
            res[name] = f"200 rows {len(r)} first {','.join(r[0][:5]) if r else ''}"
        else:
            res[name] = f"HTTP {s}"
    note("gold probe · binance archive", json.dumps(res, ensure_ascii=False))
    # first months on the archive (S3 listing)
    first = {}
    for pref in ("spot/monthly/klines/PAXGUSDT/1h/", "futures/um/monthly/klines/PAXGUSDT/1h/", "futures/um/monthly/klines/XAUUSDT/1h/",
                 "futures/um/monthly/klines/XAUTUSDT/1h/", "spot/monthly/klines/XAUTUSDT/1h/"):
        s, b = get(f"https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?prefix=data/{pref}", raw=True, tries=2)
        if s == 200:
            import re
            ks = re.findall(rb"<Key>[^<]*?-(\d{4}-\d{2})\.zip</Key>", b)
            first[pref] = f"{len(ks)} months {ks[0].decode() if ks else ''} → {ks[-1].decode() if ks else ''}"
        else:
            first[pref] = f"HTTP {s}"
    note("gold probe · archive months", json.dumps(first, ensure_ascii=False))
    # Dukascopy XAUUSD
    d = {}
    for y, m0 in ((2015, 0), (2020, 0), (2024, 5), (2026, 8)):
        try:
            s, rows = duka_hours("XAUUSD", y, m0)
            d[f"{y}-{m0 + 1:02d}"] = f"HTTP {s}, {len(rows)} hours, first {rows[0] if rows else None}"
        except Exception as e:  # noqa: BLE001
            d[f"{y}-{m0 + 1:02d}"] = f"error {str(e)[:120]}"
    note("gold probe · dukascopy XAUUSD hours", json.dumps(d, ensure_ascii=False, default=str))
    # perps on venues now
    live = {}
    s, x = get("https://api.bybit.com/v5/market/tickers?category=linear")
    live["bybit"] = [(r["symbol"], r.get("fundingRate"), r.get("turnover24h")) for r in ((x or {}).get("result") or {}).get("list") or []
                     if any(k in r["symbol"] for k in ("XAU", "PAXG", "GOLD"))] if s == 200 else f"HTTP {s}"
    s, x = get("https://www.okx.com/api/v5/public/instruments?instType=SWAP")
    live["okx"] = [r["instId"] for r in (x or {}).get("data") or [] if any(k in r["instId"] for k in ("XAU", "PAXG", "GOLD"))] if s == 200 else f"HTTP {s}"
    s, x = get("https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex")
    live["bingx"] = [(r["symbol"], r.get("lastFundingRate")) for r in (x or {}).get("data") or []
                     if any(k in r["symbol"] for k in ("XAU", "PAXG", "GOLD"))] if s == 200 else f"HTTP {s}"
    s, x = get("https://api.gateio.ws/api/v4/futures/usdt/contracts")
    live["gate"] = [(r["name"], r.get("funding_rate"), r.get("funding_interval"), r.get("maker_fee_rate"), r.get("taker_fee_rate"))
                    for r in (x or []) if any(k in r["name"] for k in ("XAU", "PAXG", "GOLD"))] if s == 200 else f"HTTP {s}"
    s, x = get("https://contract.mexc.com/api/v1/contract/ticker")
    live["mexc"] = [(r["symbol"], r.get("fundingRate"), r.get("amount24")) for r in (x or {}).get("data") or []
                    if any(k in r["symbol"] for k in ("XAU", "PAXG", "GOLD"))] if s == 200 else f"HTTP {s}"
    s, x = get("https://fapi.binance.com/fapi/v1/premiumIndex")
    live["binance"] = [(r["symbol"], r.get("lastFundingRate")) for r in (x or [])
                       if any(k in r["symbol"] for k in ("XAU", "PAXG", "GOLD"))] if s == 200 else f"HTTP {s}"
    s, x = get("https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"})
    if s == 200:
        live["hyperliquid"] = [(u["name"], c.get("funding"), c.get("dayNtlVlm")) for u, c in zip(x[0]["universe"], x[1])
                               if any(k in u["name"] for k in ("XAU", "PAXG", "GOLD"))]
    s, x = get("https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES")
    live["bitget"] = [(r["symbol"], r.get("fundingRate"), r.get("usdtVolume")) for r in (x or {}).get("data") or []
                      if any(k in r["symbol"] for k in ("XAU", "PAXG", "GOLD"))] if s == 200 else f"HTTP {s}"
    note("gold probe · gold perps now", json.dumps(live, ensure_ascii=False))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "probe"
    os.makedirs(OUT, exist_ok=True)
    {"probe": probe}[mode]()
