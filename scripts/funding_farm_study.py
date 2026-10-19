"""Funding farming on small / new perpetuals — an honest check on history (19.10.2026, owner: «фарм фандинга … на токенах
EA Sports, BTV, Siren, Lobster, Aki, Ansem … и зарабатывает хорошие деньги — надо проверить»). Paper / history only.
Runs in GitHub Actions only (.github/workflows/research-funding-farm.yml): this container cannot reach exchange APIs.

Modes
  probe   which public venues answer from a US GitHub runner; perp universes; the named tokens (fuzzy); funding caps / intervals.
"""
import datetime as dt
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

H, DAY = 3_600_000, 86_400_000
UA = {"User-Agent": "Mozilla/5.0 cryptonova-research/1.0", "Accept": "*/*"}
VISION = "https://data.binance.vision/data/futures/um"
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
NAMES = ["SIREN", "ESPORT", "EAS", "BTV", "BTR", "LOBST", "LOBSTAR", "AKE", "AKI", "ANSEM", "EAT", "SPORT", "LOB"]
OUT = os.environ.get("FARM_DIR", "/tmp/farm")


def note(title, msg, level="notice"):
    print(f"{title}: {msg}", flush=True)
    m = str(msg).replace("%", "%25").replace("\r", "").replace("\n", "%0A")[:4000]
    print(f"::{level} title={title}::{m}", flush=True)


def get(url, body=None, tries=4, raw=False):
    """→ (status, parsed json | bytes). 4xx are returned at once."""
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


def ts(ms):
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


# ------------------------------------------------------------------------------------------------ venue snapshots
def snap_binance():
    s, d = get("https://fapi.binance.com/fapi/v1/premiumIndex")
    if s != 200:
        return s, {}
    s2, info = get("https://fapi.binance.com/fapi/v1/fundingInfo")
    iv = {x["symbol"]: (int(x.get("fundingIntervalHours") or 8), float(x.get("adjustedFundingRateCap") or 0)) for x in (info or [])}
    out = {}
    for x in d:
        sym = x["symbol"]
        if not sym.endswith("USDT"):
            continue
        h, cap = iv.get(sym, (8, 0))
        out[sym[:-4]] = {"r": float(x["lastFundingRate"] or 0), "h": h, "cap": cap}
    return s, out


def snap_bybit():
    s, d = get("https://api.bybit.com/v5/market/tickers?category=linear")
    if s != 200:
        return s, {}
    s2, ins = get("https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000")
    iv = {}
    for x in ((ins or {}).get("result") or {}).get("list") or []:
        iv[x["symbol"]] = (int(x.get("fundingInterval") or 480) / 60, float(x.get("upperFundingRate") or 0))
    out = {}
    for x in d["result"]["list"]:
        sym = x["symbol"]
        if not sym.endswith("USDT") or not x.get("fundingRate"):
            continue
        h, cap = iv.get(sym, (8, 0))
        out[sym[:-4]] = {"r": float(x["fundingRate"]), "h": h, "cap": cap, "vol": float(x.get("turnover24h") or 0)}
    return s, out


def snap_okx():
    s, d = get("https://www.okx.com/api/v5/public/instruments?instType=SWAP")
    if s != 200:
        return s, {}
    ids = [x["instId"] for x in d["data"] if x["instId"].endswith("-USDT-SWAP")]

    def one(i):
        s, f = get(f"https://www.okx.com/api/v5/public/funding-rate?instId={i}", tries=2)
        if s != 200 or not f.get("data"):
            return None
        x = f["data"][0]
        h = (int(x["nextFundingTime"]) - int(x["fundingTime"])) / H if x.get("nextFundingTime") else 8
        return i.split("-")[0], {"r": float(x["fundingRate"] or 0), "h": h or 8, "cap": float(x.get("maxFundingRate") or 0)}
    with ThreadPoolExecutor(8) as ex:
        out = dict(r for r in ex.map(one, ids) if r)
    return s, out


def snap_gate():
    s, d = get("https://api.gateio.ws/api/v4/futures/usdt/contracts")
    if s != 200:
        return s, {}
    out = {}
    for x in d:
        if x.get("in_delisting"):
            continue
        h = int(x.get("funding_interval") or 28800) / 3600
        out[x["name"].split("_")[0]] = {"r": float(x.get("funding_rate") or 0), "h": h, "cap": float(x.get("funding_cap_ratio") or 0),
                                        "vol": 0.0}
    return s, out


def snap_mexc():
    s, d = get("https://contract.mexc.com/api/v1/contract/ticker")
    if s != 200:
        return s, {}
    s2, det = get("https://contract.mexc.com/api/v1/contract/detail")
    out = {}
    for x in d.get("data") or []:
        sym = x["symbol"]
        if not sym.endswith("_USDT"):
            continue
        out[sym[:-5]] = {"r": float(x.get("fundingRate") or 0), "h": 8, "cap": 0, "vol": float(x.get("amount24") or 0)}
    return s, out


def snap_bingx():
    s, d = get("https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex")
    if s != 200:
        return s, {}
    out = {}
    for x in d.get("data") or []:
        sym = x["symbol"]
        if not sym.endswith("-USDT"):
            continue
        out[sym[:-5]] = {"r": float(x.get("lastFundingRate") or 0), "h": 8, "cap": 0}
    return s, out


def snap_bitget():
    s, d = get("https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES")
    if s != 200:
        return s, {}
    out = {}
    for x in d.get("data") or []:
        sym = x["symbol"]
        if sym.endswith("USDT"):
            out[sym[:-4]] = {"r": float(x.get("fundingRate") or 0), "h": 8, "cap": 0, "vol": float(x.get("usdtVolume") or 0)}
    return s, out


def snap_hl():
    s, d = get("https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"})
    if s != 200:
        return s, {}
    meta, ctx = d
    out = {}
    for u, c in zip(meta["universe"], ctx):
        if u.get("isDelisted"):
            continue
        out[u["name"]] = {"r": float(c.get("funding") or 0), "h": 1, "cap": 0.04, "vol": float(c.get("dayNtlVlm") or 0)}
    return s, out


SNAPS = {"binance": snap_binance, "bybit": snap_bybit, "okx": snap_okx, "gate": snap_gate, "mexc": snap_mexc, "bingx": snap_bingx,
         "bitget": snap_bitget, "hyperliquid": snap_hl}


def probe():
    lines, snaps = {}, {}
    for v, f in SNAPS.items():
        try:
            s, out = f()
        except Exception as e:  # noqa: BLE001
            s, out = -1, {}
            lines[v] = f"error {str(e)[:120]}"
        snaps[v] = out
        lines.setdefault(v, f"HTTP {s}, {len(out)} USDT perps")
    note("farm probe · venues now", json.dumps(lines, ensure_ascii=False))
    # history endpoints
    hist = {}
    for name, url, body in [
        ("vision fundingRate SIREN 2025-06", f"{VISION}/monthly/fundingRate/SIRENUSDT/SIRENUSDT-fundingRate-2025-06.zip", None),
        ("vision fundingRate BTC 2023-01", f"{VISION}/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2023-01.zip", None),
        ("vision 1h klines SIREN 2025-06", f"{VISION}/monthly/klines/SIRENUSDT/1h/SIRENUSDT-1h-2025-06.zip", None),
        ("vision spot 1h SIREN 2025-06", "https://data.binance.vision/data/spot/monthly/klines/SIRENUSDT/1h/SIRENUSDT-1h-2025-06.zip", None),
        ("bybit funding hist BTC 2023", f"https://api.bybit.com/v5/market/funding/history?category=linear&symbol=BTCUSDT&limit=200&endTime={1675209600000}", None),
        ("bybit kline BTC 2023", "https://api.bybit.com/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60&start=1672531200000&limit=200", None),
        ("okx funding hist BTC old", "https://www.okx.com/api/v5/public/funding-rate-history?instId=BTC-USDT-SWAP&limit=100&after=1704067200000", None),
        ("okx candles BTC 2023", "https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT-SWAP&bar=1H&limit=100&after=1675209600000", None),
        ("gate funding hist BTC 2023", "https://api.gateio.ws/api/v4/futures/usdt/funding_rate?contract=BTC_USDT&limit=1000&from=1672531200&to=1675209600", None),
        ("gate candles BTC 2023", "https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract=BTC_USDT&interval=1h&from=1672531200&to=1675209600", None),
        ("mexc funding hist BTC p30", "https://contract.mexc.com/api/v1/contract/funding_rate/history?symbol=BTC_USDT&page_num=30&page_size=100", None),
        ("mexc kline BTC 2023", "https://contract.mexc.com/api/v1/contract/kline/BTC_USDT?interval=Min60&start=1672531200&end=1675209600", None),
        ("bingx funding hist BTC 2023", "https://open-api.bingx.com/openApi/swap/v2/quote/fundingRate?symbol=BTC-USDT&startTime=1672531200000&endTime=1675209600000&limit=1000", None),
        ("bingx kline BTC 2023", "https://open-api.bingx.com/openApi/swap/v3/quote/klines?symbol=BTC-USDT&interval=1h&startTime=1672531200000&endTime=1675209600000&limit=1000", None),
        ("bitget funding hist BTC p20", "https://api.bitget.com/api/v2/mix/market/history-fund-rate?symbol=BTCUSDT&productType=usdt-futures&pageSize=100&pageNo=20", None),
        ("bitget candles BTC 2024", "https://api.bitget.com/api/v2/mix/market/history-candles?symbol=BTCUSDT&productType=usdt-futures&granularity=1H&endTime=1706745600000&limit=200", None),
        ("hl fundingHistory BTC 2023", "https://api.hyperliquid.xyz/info", {"type": "fundingHistory", "coin": "BTC", "startTime": 1672531200000}),
        ("hl candles BTC oldest", "https://api.hyperliquid.xyz/info", {"type": "candleSnapshot", "req": {"coin": "BTC", "interval": "1h", "startTime": 1672531200000, "endTime": 1675209600000}}),
    ]:
        s, d = get(url, body, tries=2, raw=url.endswith(".zip"))
        if s == 200 and url.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(d)) as z:
                txt = z.read(z.namelist()[0]).decode().splitlines()
            hist[name] = f"200 rows {len(txt)} :: {' | '.join(txt[:3])[:160]}"
        elif s == 200:
            txt = json.dumps(d)[:220]
            hist[name] = f"200 :: {txt}"
        else:
            hist[name] = f"HTTP {s} {str(d)[:100] if d else ''}"
    items = list(hist.items())
    note("farm probe · history endpoints 1", json.dumps(dict(items[:9]), ensure_ascii=False))
    note("farm probe · history endpoints 2", json.dumps(dict(items[9:]), ensure_ascii=False))
    # named tokens (fuzzy, by venue)
    found = {}
    for v, out in snaps.items():
        for coin in out:
            cu = coin.upper().lstrip("1000").lstrip("K")
            for n in NAMES:
                if cu.startswith(n) or (len(n) >= 4 and n in cu):
                    found.setdefault(coin, {})[v] = round(out[coin]["r"] * 100, 4)
    note("farm probe · named tokens (funding % per interval now)", json.dumps(found, ensure_ascii=False))
    # extreme now: |rate per 8h| ≥ 0.1 %
    ext = []
    for v, out in snaps.items():
        for coin, x in out.items():
            r8 = x["r"] * 8 / (x["h"] or 8)
            if abs(r8) >= 0.001:
                ext.append((round(r8 * 100, 3), v, coin, x["h"], x.get("cap")))
    ext.sort(key=lambda e: -abs(e[0]))
    note("farm probe · extreme now (pct per 8h, venue, coin, interval h, cap)", f"{len(ext)} rows :: " + json.dumps(ext[:60]))
    # intervals / caps distribution on Binance and Bybit
    for v in ("binance", "bybit", "gate"):
        out = snaps.get(v) or {}
        dist = {}
        for x in out.values():
            dist[str(x["h"])] = dist.get(str(x["h"]), 0) + 1
        caps = sorted({round(x["cap"] * 100, 3) for x in out.values()})
        note(f"farm probe · {v} intervals and caps", json.dumps({"intervals_h": dist, "caps_pct": caps[:30]}))


def probe2():
    """History depth of the hedge venues + the named tokens in the Binance archive."""
    now = int(time.time())
    res = {}
    tests = [
        ("gate fr BTC no range", "https://api.gateio.ws/api/v4/futures/usdt/funding_rate?contract=BTC_USDT&limit=1000"),
        ("gate fr SIREN no range", "https://api.gateio.ws/api/v4/futures/usdt/funding_rate?contract=SIREN_USDT&limit=1000"),
        ("gate fr BTC from", f"https://api.gateio.ws/api/v4/futures/usdt/funding_rate?contract=BTC_USDT&from={now - 400 * 86400}&to={now - 300 * 86400}"),
        ("gate candles BTC 400d", f"https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract=BTC_USDT&interval=1h&from={now - 400 * 86400}&to={now - 330 * 86400}"),
        ("gate candles BTC 1000d", f"https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract=BTC_USDT&interval=1h&from={now - 1000 * 86400}&to={now - 950 * 86400}"),
        ("gatedata candles 2024-01", "https://download.gatedata.org/futures_usdt/candlesticks_1h/202401/BTC_USDT-202401.csv.gz"),
        ("gatedata funding_applies", "https://download.gatedata.org/futures_usdt/funding_applies/202401/BTC_USDT-202401.csv.gz"),
        ("gatedata funding_rate", "https://download.gatedata.org/futures_usdt/funding_rate/202401/BTC_USDT-202401.csv.gz"),
        ("gatedata funding_rates", "https://download.gatedata.org/futures_usdt/funding_rates/202401/BTC_USDT-202401.csv.gz"),
        ("okx static swaprate monthly", "https://static.okx.com/cdn/okex/traderecords/swaprate/monthly/202401/allswaprate-swaprate-2024-01.zip"),
        ("okx static swaprate daily", "https://static.okx.com/cdn/okex/traderecords/swaprate/daily/20240101/allswaprate-swaprate-2024-01-01.zip"),
        ("mexc fr SIREN p1", "https://contract.mexc.com/api/v1/contract/funding_rate/history?symbol=SIREN_USDT&page_num=1&page_size=1000"),
        ("mexc fr BTW p1", "https://contract.mexc.com/api/v1/contract/funding_rate/history?symbol=BTW_USDT&page_num=1&page_size=1000"),
        ("bitget fr BTC p10", "https://api.bitget.com/api/v2/mix/market/history-fund-rate?symbol=BTCUSDT&productType=usdt-futures&pageSize=100&pageNo=10"),
        ("bitget fr BTC p5", "https://api.bitget.com/api/v2/mix/market/history-fund-rate?symbol=BTCUSDT&productType=usdt-futures&pageSize=100&pageNo=5"),
        ("bingx fr BTC 60d", f"https://open-api.bingx.com/openApi/swap/v2/quote/fundingRate?symbol=BTC-USDT&startTime={(now - 60 * 86400) * 1000}&endTime={(now - 50 * 86400) * 1000}&limit=1000"),
        ("bingx fr BTC 300d", f"https://open-api.bingx.com/openApi/swap/v2/quote/fundingRate?symbol=BTC-USDT&startTime={(now - 300 * 86400) * 1000}&endTime={(now - 290 * 86400) * 1000}&limit=1000"),
        ("bingx kline BTC 300d", f"https://open-api.bingx.com/openApi/swap/v3/quote/klines?symbol=BTC-USDT&interval=1h&startTime={(now - 300 * 86400) * 1000}&endTime={(now - 290 * 86400) * 1000}&limit=1000"),
        ("okx fr BTC latest", "https://www.okx.com/api/v5/public/funding-rate-history?instId=BTC-USDT-SWAP&limit=400"),
    ]
    for name, url in tests:
        s, d = get(url, tries=2, raw=True)
        if s == 200:
            b = d
            if url.endswith(".gz"):
                import gzip
                b = gzip.decompress(d)
                txt = b.decode()[:200]
                res[name] = f"200 {len(b)} bytes :: {txt}"
                continue
            if url.endswith(".zip"):
                res[name] = f"200 zip {len(d)} bytes"
                continue
            try:
                j = json.loads(b.decode())
            except Exception:  # noqa: BLE001
                res[name] = f"200 non-json {b[:120]}"
                continue
            if isinstance(j, list):
                tt = [x.get("t") if isinstance(x, dict) else None for x in j]
                tt = [x for x in tt if x]
                res[name] = f"200 list {len(j)} " + (f"{ts(min(tt) * 1000)} → {ts(max(tt) * 1000)}" if tt else str(j[:1])[:150])
            else:
                dd = j.get("data")
                if isinstance(dd, dict) and "resultList" in dd:
                    rl = dd["resultList"]
                    res[name] = f"200 total {dd.get('totalCount')} pages {dd.get('totalPage')} " + (f"{ts(rl[-1]['settleTime'])} → {ts(rl[0]['settleTime'])}" if rl else "")
                elif isinstance(dd, list) and dd and isinstance(dd[0], dict) and "fundingTime" in dd[0]:
                    tt = [int(x["fundingTime"]) for x in dd]
                    res[name] = f"200 rows {len(dd)} {ts(min(tt))} → {ts(max(tt))}"
                elif isinstance(dd, list):
                    res[name] = f"200 rows {len(dd)} {str(dd[:1])[:150]}"
                else:
                    res[name] = f"200 {str(j)[:150]}"
        else:
            res[name] = f"HTTP {s}"
    items = list(res.items())
    note("farm probe2 · depth 1", json.dumps(dict(items[:10]), ensure_ascii=False))
    note("farm probe2 · depth 2", json.dumps(dict(items[10:]), ensure_ascii=False))
    syms = vision_list("fundingRate")
    hits = sorted(s for s in syms if any(s.startswith(n) or s.startswith("1000" + n) for n in
                                         ("SIREN", "ESPORT", "AKE", "ANSEM", "BTR", "BTW", "LOB", "SPORT", "EAT", "AKI", "BTV", "CLAW")))
    note("farm probe2 · binance archive", json.dumps({"fundingRate symbols": len(syms), "named": hits}, ensure_ascii=False))
    s, d = get("https://api.hyperliquid.xyz/info", {"type": "meta"})
    hl = [u["name"] for u in (d or {}).get("universe") or []]
    note("farm probe2 · hyperliquid named", json.dumps([x for x in hl if any(n in x.upper() for n in ("SIREN", "ESPORT", "AKE", "ANSEM", "BTR", "BTW", "LOB", "SPORT"))]))


def vision_list(kind="fundingRate"):
    """All USDT-M symbols in the Binance archive for `kind` (S3 listing, paged)."""
    syms, marker = [], ""
    for _ in range(20):
        s, b = get(f"{S3}?delimiter=/&prefix=data/futures/um/monthly/{kind}/" + (f"&marker={marker}" if marker else ""), raw=True)
        if s != 200:
            break
        p = re.findall(rf"<Prefix>data/futures/um/monthly/{kind}/([A-Z0-9]+)/</Prefix>", b.decode())
        syms += p
        if "<IsTruncated>true</IsTruncated>" not in b.decode() or not p:
            break
        marker = f"data/futures/um/monthly/{kind}/{p[-1]}/"
    return sorted(set(syms))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "probe"
    os.makedirs(OUT, exist_ok=True)
    {"probe": probe, "probe2": probe2}[mode]()
