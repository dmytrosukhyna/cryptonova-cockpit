"""Funding farming on small / new perpetuals — an honest check on history (19.10.2026, owner: «фарм фандинга … на токенах
EA Sports, BTV, Siren, Lobster, Aki, Ansem … и зарабатывает хорошие деньги — надо проверить»). Paper / history only.
Runs in GitHub Actions only (.github/workflows/research-funding-farm.yml): this container cannot reach exchange APIs.

Modes
  probe   which public venues answer from a US GitHub runner; perp universes; the named tokens (fuzzy); funding caps / intervals.
  probe2  history depth of the hedge venues; the named tokens in the Binance archive.
  scan    Binance USDT-M funding archive, EVERY perp incl. delisted, 2023-01 → 2026-09 (data.binance.vision): how often
          funding is extreme, intervals, caps; the named tokens. Gate funding archive (download.gatedata.org) for the same
          coins. Signals of the three ways to farm. → $FARM_DIR
  prices  1-hour candles around every signal: Binance perp + Binance spot (data.binance.vision), Gate perp (gatedata).
  sim     the three ways, a portfolio of 5 slots, fixed size (no compounding), per year / month, drawdown, capacity.
  now     cross-venue funding spreads right now (OKX, Gate, MEXC, BingX, Bitget, Hyperliquid; Binance / Bybit refuse US).

Rules fixed before the run (no tuning; two thresholds and three leverages are reported, the main one is 0.1 % / 3×):
  signal  a settled print with |funding| ≥ THR per 8 h (rate × 8 / interval h); entry at the open of the hour of that print
          (after it: that print is not received); funding received only for prints while held. No entry in the first 24 h
          of a leg's trading, nor when the two legs' prices differ by > 3 % (rule v2).
  exit    (v2, main) when the funding received since entry over the last ≤ 24 h averages < 0.02 % per 8 h, checked from
          the 8th hour. (v1, the first run: exit on the latest prints alone — it churned, average hold 7–19 h; kept for
          comparison; v2 was written after seeing v1, so it is not an out-of-sample result.)
  (a)     cross-venue: Binance perp vs Gate perp of the same coin. Entry when the gap of the two latest prints ≥ THR per 8 h:
          short where funding is higher, long where lower. Exit when the received gap < 0.02 % per 8 h, after 14 days, or
          when one venue's data ends (delisting: that leg 2 % worse). Margin N / L on each venue (capital = 2N / L).
  (b)     Binance spot long + Binance perp short, only when funding is positive and the coin has Binance spot. Capital = N + N / L.
  (c)     naked: only the perp side that receives funding, 1× (capital = N) — shown as a warning of the price risk.
  costs   taker fee per fill: Binance perp 0.05 %, Binance spot 0.10 %, Gate perp 0.075 %; half-spread 0.05 % per fill
          (0.02 % when the coin trades ≥ $20 M a day on that venue); impact σ_day × √(size / day volume) per fill.
          Sensitivity «cheap»: half-spread 0.02 % and half that impact.
  liquidation  a perp leg is liquidated when the 1-hour high / low moves 1 / L − 2.5 % against it: the leg loses its margin,
          the other leg is closed at that hour's close. No margin top-ups between venues (realistic for an unattended bot).
  portfolio  5 slots, each 1/5 of the starting capital; the strongest signal first; idle cash earns 0.
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


# ================================================================================================== history
import gzip  # noqa: E402
import pickle  # noqa: E402

import numpy as np  # noqa: E402

GATE = "https://download.gatedata.org/futures_usdt"
M0, M1 = "2023-01", "2026-09"
T_END = int(dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
THRS = (0.001, 0.003)
LEVS = (2, 3, 5)
EXIT_THR, MAX_HOLD_H, MM = 0.0002, 14 * 24, 0.025
FEE = {"bin": 0.0005, "spot": 0.001, "gate": 0.00075}
NAMED = ["SIRENUSDT", "ESPORTSUSDT", "AKEUSDT", "BTRUSDT", "BTWUSDT", "SPORTFUNUSDT"]
SLOTS = 5


def dumps(x, **_k):
    return json.dumps(x, ensure_ascii=False, default=lambda o: o.item() if hasattr(o, "item") else str(o))


def _utc(sec):
    return dt.datetime.fromtimestamp(sec, dt.timezone.utc)


YB = np.array([int(dt.datetime(y, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000) for y in range(2020, 2028)])


def year_of(t):
    return 2019 + np.searchsorted(YB, t, side="right")


def months(a, b):
    y, m = int(a[:4]), int(a[5:7])
    out = []
    while f"{y:04d}-{m:02d}" <= b:
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def mshift(mo, k):
    y, m = int(mo[:4]), int(mo[5:7]) + k
    while m > 12:
        y, m = y + 1, m - 12
    while m < 1:
        y, m = y - 1, m + 12
    return f"{y:04d}-{m:02d}"


def zrows(b):
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        return [ln.split(",") for ln in z.read(z.namelist()[0]).decode().splitlines() if ln[:1].isdigit()]


def s3_months(prefix):
    s, b = get(f"{S3}?prefix={prefix}", raw=True, tries=3)
    if s != 200:
        return []
    return sorted(set(m.decode() for m in re.findall(rb"<Key>[^<]*?-(\d{4}-\d{2})\.zip</Key>", b)))


def spot_name(sym):
    g, _ = gate_name(sym)
    return g.replace("_USDT", "USDT")


def gate_name(sym):
    base = sym[:-4]
    for p in ("1000000", "1000", "1M"):
        if base.startswith(p) and len(base) > len(p) + 1:
            return base[len(p):] + "_USDT", (1e6 if p in ("1000000", "1M") else 1e3)
    return base + "_USDT", 1.0


def bin_funding(sym):
    ms_ = [m for m in s3_months(f"data/futures/um/monthly/fundingRate/{sym}/") if M0 <= m <= M1]
    rows = []
    for m in ms_:
        s, b = get(f"{VISION}/monthly/fundingRate/{sym}/{sym}-fundingRate-{m}.zip", raw=True)
        if s == 200:
            rows += zrows(b)
    if not rows:
        return sym, None
    a = np.array(sorted((int(r[0]), float(r[2]), float(r[1] or 8)) for r in rows))
    return sym, a


def gate_csv(kind, gsym, mo):
    s, b = get(f"{GATE}/{kind}/{mo.replace('-', '')}/{gsym}-{mo.replace('-', '')}.csv.gz", raw=True, tries=3)
    if s != 200 or not b:
        return []
    try:
        return [ln.split(",") for ln in gzip.decompress(b).decode().splitlines() if ln[:1].isdigit()]
    except Exception:  # noqa: BLE001
        return []


def gate_funding(sym, mos):
    gsym, _ = gate_name(sym)
    rows = []
    for m in mos:
        rows += gate_csv("funding_applies", gsym, m)
    if not rows:
        return sym, None
    t = np.array([int(r[0]) * 1000 for r in rows], dtype=np.int64)
    r = np.array([float(r[1]) for r in rows])
    o = np.argsort(t)
    t, r = t[o], r[o]
    gap = np.diff(t) / H
    iv = np.r_[gap[:1] if len(gap) else [8.0], gap]
    iv = np.clip(np.round(iv), 1, 8)                                   # missing applications → never more than 8 h
    return sym, np.c_[t, r, iv]


def r8(a):
    return a[:, 1] * 8 / np.maximum(a[:, 2], 1)


def scan():
    syms = vision_list("fundingRate")
    with ThreadPoolExecutor(24) as ex:
        BF = {s: a for s, a in ex.map(bin_funding, syms) if a is not None and len(a)}
    pickle.dump(BF, open(f"{OUT}/bin_funding.pkl", "wb"))
    allr = np.concatenate([np.c_[a, r8(a), np.full(len(a), j)] for j, a in enumerate(BF.values())])
    yr = year_of(allr[:, 0])
    ext = np.abs(allr[:, 3]) >= 0.001
    by_year = {}
    for y in sorted(set(yr)):
        m = yr == y
        by_year[int(y)] = {"prints": int(m.sum()), "extreme_pct": round(float(ext[m].mean() * 100), 2),
                           "coins_listed": len(set(allr[m, 4].astype(int))), "coins_with_extreme": len(set(allr[m & ext, 4].astype(int))),
                           "extreme_coin_hours": int(allr[m & ext, 2].sum()),
                           "neg_share_pct": round(float((allr[m & ext, 1] < 0).mean() * 100), 0) if (m & ext).any() else None,
                           "median_abs_r8_pct_extreme": round(float(np.median(np.abs(allr[m & ext, 3])) * 100), 3) if (m & ext).any() else None}
    iv = {str(int(k)): int((allr[ext, 2] == k).sum()) for k in sorted(set(allr[ext, 2]))}
    big = {f">={x}%": int((np.abs(allr[:, 1]) >= x / 100).sum()) for x in (0.5, 1, 2)}
    note("farm scan · binance archive", dumps({"symbols": len(BF), "prints": len(allr), "period": f"{M0}…{M1}",
                                                    "extreme_share_pct": round(float(ext.mean() * 100), 2), "by_year": by_year,
                                                    "extreme_by_interval_h": iv, "prints_per_print_rate": big,
                                                    "max_abs_print_pct": round(float(np.abs(allr[:, 1]).max() * 100), 3)}, ensure_ascii=False))
    top = sorted(((s, float(a[np.abs(r8(a)) >= 0.001, 2].sum()), float(np.mean(r8(a)) * 100), _utc(a[0, 0] / 1000).strftime("%Y-%m"),
                   _utc(a[-1, 0] / 1000).strftime("%Y-%m")) for s, a in BF.items()), key=lambda x: -x[1])[:25]
    note("farm scan · top coins by extreme hours", dumps([(s, int(h), round(m, 3), a, b) for s, h, m, a, b in top]))
    named = {}
    for s in NAMED:
        a = BF.get(s)
        if a is None:
            named[s] = "not in archive"
            continue
        x = r8(a)
        e = np.abs(x) >= 0.001
        named[s] = {"from": _utc(a[0, 0] / 1000).strftime("%Y-%m-%d"), "to": _utc(a[-1, 0] / 1000).strftime("%Y-%m-%d"),
                    "prints": len(a), "intervals_h": sorted(set(int(v) for v in a[:, 2])), "extreme_pct": round(float(e.mean() * 100), 1),
                    "avg_r8_pct": round(float(x.mean() * 100), 4), "min_print_pct": round(float(a[:, 1].min() * 100), 3),
                    "max_print_pct": round(float(a[:, 1].max() * 100), 3),
                    "sum_received_on_extreme_pct": round(float(np.abs(a[e, 1]).sum() * 100), 1)}
    note("farm scan · named tokens on Binance", dumps(named, ensure_ascii=False))
    # Gate funding archive for the same coins (months the coin is on Binance)
    jobs = []
    for s, a in BF.items():
        mo = sorted({_utc(t / 1000).strftime("%Y-%m") for t in a[::max(1, len(a) // 60), 0]} |
                    {_utc(a[-1, 0] / 1000).strftime("%Y-%m")})
        mo = months(mo[0], mo[-1])
        jobs.append((s, mo))
    with ThreadPoolExecutor(24) as ex:
        GF = {s: a for s, a in ex.map(lambda j: gate_funding(*j), jobs) if a is not None and len(a)}
    pickle.dump(GF, open(f"{OUT}/gate_funding.pkl", "wb"))
    sig = signals(BF, GF)
    pickle.dump(sig, open(f"{OUT}/signals.pkl", "wb"))
    note("farm scan · gate archive + signals", dumps({"binance coins": len(BF), "also on gate (funding)": len(GF),
                                                         "signals": {k: len(v) for k, v in sig.items()},
                                                         "signal coins": {k: len({x[1] for x in v}) for k, v in sig.items()}}, ensure_ascii=False))


def latest(a, t):
    """index of the last print with time ≤ t (or -1)."""
    return int(np.searchsorted(a[:, 0], t, side="right")) - 1


def signals(BF, GF):
    """→ {key: [(entry_hour, sym, strength, side_bin)]} for key = c@thr, b@thr, a@thr. side_bin: side of the Binance leg."""
    out = {f"{m}@{thr}": [] for m in ("a", "b", "c") for thr in THRS}
    for s, a in BF.items():
        x = r8(a)
        for thr in THRS:
            for i in np.where(np.abs(x) >= thr)[0]:
                t = int(a[i, 0])
                hr = t - t % H
                side = -1 if x[i] > 0 else 1
                out[f"c@{thr}"].append((hr, s, abs(float(x[i])), side))
                if x[i] > 0:
                    out[f"b@{thr}"].append((hr, s, float(x[i]), -1))
        g = GF.get(s)
        if g is None:
            continue
        gx = r8(g)
        ts_ = np.unique(np.r_[a[:, 0], g[:, 0]])
        ts_ = ts_[(ts_ >= max(a[0, 0], g[0, 0]))]
        ia = np.searchsorted(a[:, 0], ts_, side="right") - 1
        ig = np.searchsorted(g[:, 0], ts_, side="right") - 1
        ok = (ia >= 0) & (ig >= 0)
        ts_, ia, ig = ts_[ok], ia[ok], ig[ok]
        fresh = (ts_ - a[ia, 0] <= 9 * H) & (ts_ - g[ig, 0] <= 9 * H)
        gap = x[ia] - gx[ig]
        for thr in THRS:
            for k in np.where(fresh & (np.abs(gap) >= thr))[0]:
                t = int(ts_[k])
                out[f"a@{thr}"].append((t - t % H, s, abs(float(gap[k])), -1 if gap[k] > 0 else 1))
    for k in out:
        out[k].sort()
    return out


# ---------------------------------------------------------------------------------------------- prices
def bin_klines(kind, sym, mos):
    base = "https://data.binance.vision/data/spot" if kind == "spot" else VISION
    have = set(s3_months(f"data/{'spot' if kind == 'spot' else 'futures/um'}/monthly/klines/{sym}/1h/"))
    rows = []
    for m in mos:
        if m not in have:
            continue
        s, b = get(f"{base}/monthly/klines/{sym}/1h/{sym}-1h-{m}.zip", raw=True)
        if s == 200:
            rows += zrows(b)
    if not rows:
        return None
    ms_ = lambda v: int(v) // 1000 if int(v) > 10 ** 14 else int(v)    # noqa: E731
    d = {}
    for r in rows:
        d[ms_(r[0])] = (float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7]))
    t = np.array(sorted(d), dtype=np.int64)
    v = np.array([d[k] for k in t])
    return {"t": t, "o": v[:, 0], "h": v[:, 1], "l": v[:, 2], "c": v[:, 3], "q": v[:, 4]}


def gate_klines(sym, mos, mult):
    gsym, _ = gate_name(sym)
    rows = []
    for m in mos:
        rows += gate_csv("candlesticks_1h", gsym, m)
    if not rows:
        return None
    d = {int(r[0]) * 1000: (float(r[5]), float(r[3]), float(r[4]), float(r[2]), float(r[1])) for r in rows}   # t, v, c, h, l, o
    t = np.array(sorted(d), dtype=np.int64)
    v = np.array([d[k] for k in t])
    q = v[:, 4] * (mult or 0) * v[:, 3]
    return {"t": t, "o": v[:, 0], "h": v[:, 1], "l": v[:, 2], "c": v[:, 3], "q": q, "q_known": bool(mult)}


def prices():
    sig = pickle.load(open(f"{OUT}/signals.pkl", "rb"))
    need = {"bin": {}, "spot": {}, "gate": {}}
    for k, v in sig.items():
        for hr, s, _, _ in v:
            mo = _utc(hr / 1000).strftime("%Y-%m")
            ms3 = {mshift(mo, -1), mo, mshift(mo, 1)}
            need["bin"].setdefault(s, set()).update(ms3)
            if k.startswith("b"):
                need["spot"].setdefault(spot_name(s), set()).update(ms3)
            if k.startswith("a"):
                need["gate"].setdefault(s, set()).update(ms3)
    s_, d = get("https://api.gateio.ws/api/v4/futures/usdt/contracts")
    mult = {x["name"]: float(x.get("quanto_multiplier") or 0) for x in (d or [])} if s_ == 200 else {}
    P = {}
    with ThreadPoolExecutor(24) as ex:
        P["bin"] = dict(zip(need["bin"], ex.map(lambda s: bin_klines("um", s, sorted(m for m in need["bin"][s] if m <= M1)), need["bin"])))
        P["spot"] = dict(zip(need["spot"], ex.map(lambda s: bin_klines("spot", s, sorted(m for m in need["spot"][s] if m <= M1)), need["spot"])))
        P["gate"] = dict(zip(need["gate"], ex.map(lambda s: gate_klines(s, sorted(m for m in need["gate"][s] if m <= M1), mult.get(gate_name(s)[0])), need["gate"])))
    pickle.dump(P, open(f"{OUT}/prices.pkl", "wb"))
    # sanity: gate vs binance closes on common hours
    ratio_ok, ratio_bad = 0, []
    for s, g in P["gate"].items():
        b = P["bin"].get(s)
        if g is None or b is None:
            continue
        com, ib, ig = np.intersect1d(b["t"], g["t"], return_indices=True)
        if len(com) < 24:
            continue
        rr = np.median(b["c"][ib] / g["c"][ig])
        k = 10 ** (3 * round(np.log10(rr) / 3))
        if abs(rr / k - 1) < 0.1:
            ratio_ok += 1
        else:
            ratio_bad.append((s, round(float(rr), 4)))
    note("farm prices · data", dumps({k: {"asked": len(need[k]), "got": sum(1 for v in P[k].values() if v is not None)} for k in P} |
                                         {"gate scale ok": ratio_ok, "gate different token (skipped)": ratio_bad[:30],
                                          "gate volume known": sum(1 for v in P["gate"].values() if v and v["q_known"])}, ensure_ascii=False))


# ---------------------------------------------------------------------------------------------- simulation
def bar_index(K, t):
    i = int(np.searchsorted(K["t"], t))
    return i if i < len(K["t"]) and K["t"][i] == t else -1


def liq_stats(K, i):
    """σ of a day and USD volume of a day from the 7 days before bar i (fallback: what exists)."""
    a = max(0, i - 168)
    c = K["c"][a:i + 1]
    lr = np.diff(np.log(c)) if len(c) > 2 else np.array([0.05])
    sig = float(np.std(lr) * np.sqrt(24)) if len(lr) > 2 else 0.1
    q = float(np.mean(K["q"][a:i + 1]) * 24) if i >= 0 else 0.0
    return max(sig, 0.005), q


def norm_gap(r):
    """price ratio of two legs → deviation from 1 after the 1 / 1 000 / 1 000 000 contract scale."""
    k = 10 ** (3 * round(np.log10(r) / 3))
    return abs(r / k - 1)


def trade(legs, hr0, L, mode, rule="v2"):
    """legs: [{K, F (prints or None), side, fee, spot}] → result dict or None. Fills at the open of hr0; see module doc.
    rule v1: exit when the LATEST prints give < 0.02 % per 8 h (first run — churned, avg hold 7–19 h);
    rule v2: exit when the funding received since entry over the last ≤ 24 h averages < 0.02 % per 8 h, checked from the
             8th hour; no entry in the first 24 h of a leg's trading or when the two legs' prices differ by > 3 %."""
    idx0 = [bar_index(g["K"], hr0) for g in legs]
    if min(idx0) < 0:
        return None
    p0 = [g["K"]["o"][i] for g, i in zip(legs, idx0)]
    if min(p0) <= 0:
        return None
    if rule == "v2":
        if min(idx0) < 24 or any(g["K"]["t"][i] - g["K"]["t"][max(0, i - 24)] > 30 * H for g, i in zip(legs, idx0)):
            return None
        if len(legs) == 2 and norm_gap(p0[0] / p0[1]) > 0.03:
            return None
    fund, path, last_c = 0.0, [], list(p0)
    recv_hist = []                                                   # (hour index, received funding of that hour)
    gap_entry = (p0[0] / p0[1]) if len(legs) == 2 else 1.0
    liq, delist, why, n, exit_px = None, False, "hold", 0, None
    while True:
        T = hr0 + n * H
        cur, ended = [], []
        for k, g in enumerate(legs):
            i = idx0[k] if n == 0 else bar_index(g["K"], T)
            cur.append(i if i >= 0 else None)
            ended.append(i < 0 and g["K"]["t"][-1] < T)
        if n > 0:
            recv, got = 0.0, 0.0
            for k, g in enumerate(legs):
                F = g["F"]
                if F is None:
                    continue
                j0, j1 = np.searchsorted(F[:, 0], T, side="left"), np.searchsorted(F[:, 0], T + H, side="left")
                if j1 > j0:                                     # prints of this hour: held at the print → received / paid
                    got += -g["side"] * float(F[j0:j1, 1].sum())
                if j1 > 0:
                    recv += -g["side"] * F[j1 - 1, 1] * 8 / max(F[j1 - 1, 2], 1)
            fund += got
            recv_hist.append(got)
            if rule == "v2":
                w = recv_hist[-24:]
                avg8 = sum(w) / len(w) * 8
                faded = n >= 8 and avg8 < EXIT_THR
            else:
                faded = recv < EXIT_THR
            if faded:
                why = "funding faded"
            elif n >= MAX_HOLD_H:
                why = "14 days"
        if any(ended) or T >= T_END:
            why = "data end"
            delist = T < T_END - 2 * DAY
            exit_px = list(last_c)
            if delist:
                for k, g in enumerate(legs):
                    if ended[k]:
                        exit_px[k] *= (1 - g["side"] * 0.02)          # thin book before the delisting: 2 % worse
            break
        if why != "hold":
            exit_px = [g["K"]["o"][i] if i is not None else last_c[k] for k, (g, i) in enumerate(zip(legs, cur))]
            break
        for k, g in enumerate(legs):                                 # liquidation of a perp leg inside this hour
            i = cur[k]
            if g["spot"] or i is None:
                continue
            adverse = (g["K"]["h"][i] / p0[k] - 1) if g["side"] == -1 else (1 - g["K"]["l"][i] / p0[k])
            if adverse >= 1 / L - MM:
                liq = k
                break
        if liq is not None:
            why = "liquidated"
            exit_px = [g["K"]["c"][i] if i is not None else last_c[k] for k, (g, i) in enumerate(zip(legs, cur))]
            break
        for k, g in enumerate(legs):
            if cur[k] is not None:
                last_c[k] = g["K"]["c"][cur[k]]
        path.append(sum(g["side"] * (last_c[k] / p0[k] - 1) for k, g in enumerate(legs)) + fund)
        n += 1
    legs_pnl = [(-1 / L) if k == liq else g["side"] * (exit_px[k] / p0[k] - 1) for k, g in enumerate(legs)]
    price = sum(legs_pnl)
    st = [list(liq_stats(g["K"], i)) for g, i in zip(legs, idx0)]
    for k, g in enumerate(legs):                                     # Gate volume unknown (dead contract) → 30 % of Binance
        if st[k][1] <= 0 and k > 0 and st[0][1] > 0:
            st[k][1] = 0.3 * st[0][1]
    fees = sum(2 * g["fee"] for g in legs) - (legs[liq]["fee"] if liq is not None else 0)
    return {"t0": hr0, "t1": hr0 + n * H, "fund": fund, "price": price, "gross": fund + price, "why": why, "liq": liq is not None,
            "delist": delist, "path": np.array(path + [fund + price]), "fees": fees, "liq_legs": st, "gap_entry": gap_entry, "hours": n}


COSTS = "base"


def slip(tr, N):
    """slippage + half-spread of all fills for notional N per leg (fraction of notional).
    base: half-spread 0.05 % (0.02 % if ≥ $20 M a day), impact 1.0 × σ_day × √(N / day volume);
    cheap (optimistic): half-spread 0.02 %, impact 0.5 × σ_day × √(N / day volume)."""
    tot = 0.0
    for sig, q in tr["liq_legs"]:
        if COSTS == "cheap":
            hs, Y = 0.0002, 0.5
        else:
            hs, Y = (0.0002 if q >= 20e6 else 0.0005), 1.0
        imp = Y * sig * np.sqrt(N / q) if q > 0 else 0.05
        tot += 2 * (hs + min(imp, 0.2))
    return tot


def cap_ratio(mode, L):
    return {"a": L / 2, "b": L / (L + 1), "c": 1.0}[mode]


def build_legs(mode, s, side_bin, BF, GF, P):
    b = P["bin"].get(s)
    if b is None:
        return None
    if mode == "c":
        return [{"K": b, "F": BF[s], "side": side_bin, "fee": FEE["bin"], "spot": False}]
    if mode == "b":
        sp = P["spot"].get(spot_name(s))
        if sp is None:
            return None
        return [{"K": b, "F": BF[s], "side": -1, "fee": FEE["bin"], "spot": False},
                {"K": sp, "F": None, "side": 1, "fee": FEE["spot"], "spot": True}]
    g = P["gate"].get(s)
    if g is None or s not in GF:
        return None
    return [{"K": b, "F": BF[s], "side": side_bin, "fee": FEE["bin"], "spot": False},
            {"K": g, "F": GF[s], "side": -side_bin, "fee": FEE["gate"], "spot": False}]


def portfolio(mode, thr, L, sig, BF, GF, P, capital=10_000.0, cache=None, rule="v2"):
    """Fixed slot size = capital / 5; returns trades taken (each with pnl at that size) and the hourly equity curve."""
    cache = {} if cache is None else cache
    slot = capital / SLOTS
    N = slot * cap_ratio(mode, L)
    open_until, busy_coin, taken = [], {}, []
    for hr, s, strength, side in sorted(sig, key=lambda x: (x[0], -x[2])):
        open_until = [u for u in open_until if u > hr]
        if busy_coin.get(s, 0) > hr or len(open_until) >= SLOTS:
            continue
        key = (mode, s, hr, side, L, rule)
        if key not in cache:
            legs = build_legs(mode, s, side, BF, GF, P)
            tr = trade(legs, hr, L, mode, rule) if legs else None
            if tr and mode == "a":
                k = 10 ** (3 * round(np.log10(tr["gap_entry"]) / 3))
                if abs(tr["gap_entry"] / k - 1) >= 0.1:
                    tr = None                                              # a different token under the same ticker
            cache[key] = tr
        tr = cache[key]
        if tr is None:
            continue
        cost = tr["fees"] + slip(tr, N)
        net = tr["gross"] - cost
        taken.append({**tr, "sym": s, "net": net, "pnl": net * N, "cost": cost, "N": N, "strength": strength})
        open_until.append(tr["t1"])
        busy_coin[s] = tr["t1"]
    return taken


def curve(taken, t0, t1):
    hrs = np.arange(t0, t1 + H, H)
    eq = np.zeros(len(hrs))
    for x in taken:
        i0 = int((x["t0"] - t0) // H)
        p = x["path"] * x["N"]
        p = np.r_[p[:-1] - x["cost"] * x["N"] / 2, x["pnl"]] if len(p) > 1 else np.array([x["pnl"]])
        i1 = min(len(hrs), i0 + len(p))
        eq[i0:i1] += p[:i1 - i0]
        if i1 < len(hrs):
            eq[i1:] += x["pnl"]
    return hrs, eq


def summarize(taken, capital=10_000.0):
    if not taken:
        return {"trades": 0, "pct_per_year": 0.0, "max_dd_pct": 0.0, "years_pct": {}, "top2_share_pct": None, "liquidated": 0,
                "win_pct": None, "cost_part_pct": 0.0}, [0.0] * len(months(M0, M1))
    t0 = int(dt.datetime(2023, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
    hrs, eq = curve(taken, t0, T_END)
    E = capital + eq
    dd = -1.0 if E.min() <= 0 else float(((E - np.maximum.accumulate(E)) / np.maximum.accumulate(E)).min())   # −100 % = wiped out
    yrs = (T_END - t0) / (365.25 * DAY)
    years, months_ = {}, {}
    for x in taken:
        d = _utc(x["t1"] / 1000)
        years[d.year] = years.get(d.year, 0) + x["pnl"]
        months_[d.strftime("%Y-%m")] = months_.get(d.strftime("%Y-%m"), 0) + x["pnl"]
    allm = months(M0, M1)
    mvals = [months_.get(m, 0.0) for m in allm]
    tot = sum(x["pnl"] for x in taken)
    gains = sorted([x["pnl"] for x in taken if x["pnl"] > 0], reverse=True)
    avg_month = tot / len(allm)
    fund_month = sum(x["fund"] * x["N"] for x in taken) / len(allm)
    wipes = [x for x in taken if x["pnl"] < 0 and fund_month > 0 and -x["pnl"] >= fund_month]
    worst = min(taken, key=lambda x: x["pnl"]) if taken else None
    return {"trades": len(taken), "pct_per_year": round(tot / capital / yrs * 100, 1), "total_pct": round(tot / capital * 100, 1),
            "max_dd_pct": round(dd * 100, 1), "years_pct": {k: round(v / capital * 100, 1) for k, v in sorted(years.items())},
            "months_plus": f"{sum(1 for v in mvals if v > 0)}/{len(mvals)}", "win_pct": round(100 * np.mean([x['pnl'] > 0 for x in taken]), 0) if taken else None,
            "avg_trade_pct_notional": round(100 * float(np.mean([x["net"] for x in taken])), 3) if taken else None,
            "funding_part_pct": round(100 * sum(x["fund"] * x["N"] for x in taken) / capital, 1),
            "price_part_pct": round(100 * sum(x["price"] * x["N"] for x in taken) / capital, 1),
            "cost_part_pct": round(-100 * sum(x["cost"] * x["N"] for x in taken) / capital, 1),
            "liquidated": sum(1 for x in taken if x["liq"]), "delisted": sum(1 for x in taken if x["delist"]),
            "top2_share_pct": round(100 * sum(gains[:2]) / tot, 0) if tot > 0 and gains else None,
            "wipe_events": len(wipes), "worst_trade": (worst["sym"], _utc(worst["t0"] / 1000).strftime("%Y-%m-%d"), worst["why"],
                                                       round(worst["pnl"] / capital * 100, 2),
                                                       round(-worst["pnl"] / fund_month, 1) if fund_month > 0 else None) if worst else None,
            "avg_hold_h": round(float(np.mean([x["hours"] for x in taken])), 0) if taken else None}, mvals


def sim(which):
    BF = pickle.load(open(f"{OUT}/bin_funding.pkl", "rb"))
    GF = pickle.load(open(f"{OUT}/gate_funding.pkl", "rb"))
    SIG = pickle.load(open(f"{OUT}/signals.pkl", "rb"))
    P = pickle.load(open(f"{OUT}/prices.pkl", "rb"))
    global COSTS
    cache, grid, mode = {}, {}, which
    Lmain = 3 if mode != "c" else 1
    tk1 = portfolio(mode, 0.001, Lmain, SIG[f"{mode}@0.001"], BF, GF, P, cache=cache, rule="v1")
    s1, _ = summarize(tk1)
    grid[f"v1 (first run) 0.1% {Lmain}x"] = {k: s1[k] for k in ("pct_per_year", "max_dd_pct", "years_pct", "trades", "liquidated", "win_pct")} | {
        "avg_hold_h": s1.get("avg_hold_h")}
    for thr in THRS:
        for L in (LEVS if mode != "c" else (1,)):
            tk = portfolio(mode, thr, L, SIG[f"{mode}@{thr}"], BF, GF, P, cache=cache)
            s, mv = summarize(tk)
            grid[f"{thr * 100:g}% {L}x"] = {k: s[k] for k in ("pct_per_year", "max_dd_pct", "years_pct", "trades", "liquidated", "win_pct")}
            if thr != 0.001 or L != Lmain:
                continue
            crit = {"years_plus_3of4": sum(1 for v in s["years_pct"].values() if v > 0) >= 3,
                    "test_2025_26_plus": (s["years_pct"].get(2025, 0) + s["years_pct"].get(2026, 0)) > 0,
                    "dd_under_10": s["max_dd_pct"] > -10, "top2_under_50": (s["top2_share_pct"] or 999) < 50}
            s["criteria"], s["pass"] = crit, all(crit.values())
            note(f"farm sim · {mode} main (0.1% per 8h, {L}x)", dumps(s, ensure_ascii=False))
            note(f"farm sim · {mode} main months pct of capital", dumps(dict(zip(months(M0, M1), [round(v / 100, 2) for v in mv]))))
            why = {}
            for x in tk:
                w = why.setdefault(x["why"], [0, 0.0])
                w[0] += 1
                w[1] += x["pnl"] / 100
            note(f"farm sim · {mode} exits (count, pct of capital)", dumps({k: [v[0], round(v[1], 1)] for k, v in why.items()} |
                                                                             {"worst10": [(x["sym"], _utc(x["t0"] / 1000).strftime("%Y-%m-%d"), x["why"], x["hours"],
                                                                                           round(x["fund"] * 100, 2), round(x["price"] * 100, 2), round(x["pnl"] / 100, 2))
                                                                                          for x in sorted(tk, key=lambda x: x["pnl"])[:10]],
                                                                              "best10": [(x["sym"], _utc(x["t0"] / 1000).strftime("%Y-%m-%d"), x["why"], x["hours"],
                                                                                          round(x["fund"] * 100, 2), round(x["price"] * 100, 2), round(x["pnl"] / 100, 2))
                                                                                         for x in sorted(tk, key=lambda x: -x["pnl"])[:10]]}))
            # per-trade view without slot limits: every coin's own sequence of trades
            per = []
            seen_until = {}
            for hr, sym, st_, side in SIG[f"{mode}@{thr}"]:
                if seen_until.get(sym, 0) > hr:
                    continue
                key = (mode, sym, hr, side, L, "v2")
                if key not in cache:
                    legs = build_legs(mode, sym, side, BF, GF, P)
                    tr = trade(legs, hr, L, mode, "v2") if legs else None
                    if tr and mode == "a":
                        k = 10 ** (3 * round(np.log10(tr["gap_entry"]) / 3))
                        if abs(tr["gap_entry"] / k - 1) >= 0.1:
                            tr = None
                    cache[key] = tr
                tr = cache[key]
                if tr is None:
                    continue
                seen_until[sym] = tr["t1"]
                per.append((sym, tr, tr["gross"] - tr["fees"] - slip(tr, 1000.0)))
            if per:
                nets = np.array([x[2] for x in per])
                yrs_ = year_of(np.array([x[1]["t0"] for x in per]))
                named = {}
                for sym, tr, nt in per:
                    if sym in NAMED:
                        d = named.setdefault(sym, [0, 0.0, 0.0, 0.0, 0])
                        d[0] += 1
                        d[1] += tr["fund"] * 100
                        d[2] += tr["price"] * 100
                        d[3] += nt * 100
                        d[4] += int(tr["liq"])
                note(f"farm sim · {mode} every trade, no slot limit ($1k per leg)", dumps({
                    "trades": len(per), "coins": len({x[0] for x in per}), "avg_net_pct": round(float(nets.mean() * 100), 3),
                    "median_net_pct": round(float(np.median(nets) * 100), 3), "win_pct": round(float((nets > 0).mean() * 100), 0),
                    "liquidated_pct": round(100 * np.mean([x[1]["liq"] for x in per]), 1), "delisted": sum(1 for x in per if x[1]["delist"]),
                    "by_year_avg_net_pct": {int(y): [int((yrs_ == y).sum()), round(float(nets[yrs_ == y].mean() * 100), 3)] for y in sorted(set(yrs_))},
                    "avg_funding_pct": round(100 * float(np.mean([x[1]["fund"] for x in per])), 3),
                    "avg_gross_pct": round(100 * float(np.mean([x[1]["gross"] for x in per])), 3),
                    "gross_by_year_pct": {int(y): round(100 * float(np.mean([x[1]["gross"] for x, yy in zip(per, yrs_) if yy == y])), 3) for y in sorted(set(yrs_))},
                    "avg_price_pct": round(100 * float(np.mean([x[1]["price"] for x in per])), 3),
                    "avg_hours": round(float(np.mean([x[1]["hours"] for x in per])), 0),
                    "named (n, fund, price, net, liq) pct of notional": {k: [v[0]] + [round(x, 1) for x in v[1:4]] + [v[4]] for k, v in named.items()}},
                    ensure_ascii=False))
            sens = {"gross_per_trade_pct": round(100 * float(np.mean([x["gross"] for x in tk])), 3) if tk else None,
                    "cost_per_trade_pct": round(100 * float(np.mean([x["cost"] for x in tk])), 3) if tk else None}
            ex = [x for x in tk if not x["delist"] and x["why"] != "data end"]
            se, _ = summarize(ex)
            sens["without delisting / data-end trades"] = {k: se[k] for k in ("pct_per_year", "max_dd_pct", "years_pct")}
            COSTS = "cheap"
            tkc = portfolio(mode, thr, L, SIG[f"{mode}@{thr}"], BF, GF, P, cache=cache)
            sc, _ = summarize(tkc)
            COSTS = "base"
            sens["cheap costs (half-spread 0.02%, half impact)"] = {k: sc[k] for k in ("pct_per_year", "max_dd_pct", "years_pct", "trades")}
            note(f"farm sim · {mode} sensitivity", dumps(sens))
            cap = {}
            for C in (1e4, 1e5, 1e6, 5e6):
                tkc = portfolio(mode, thr, L, SIG[f"{mode}@{thr}"], BF, GF, P, capital=C, cache=cache)
                sc, _ = summarize(tkc, capital=C)
                cap[f"${C:,.0f}"] = {"pct_per_year": sc["pct_per_year"], "cost_part_pct": sc["cost_part_pct"], "max_dd_pct": sc["max_dd_pct"]}
            note(f"farm sim · {mode} capacity (total capital, 5 slots)", dumps(cap))
            if mode == "c":
                fund_only = sum(x["fund"] * x["N"] for x in tk) / 10_000 * 100
                note("farm sim · funding on the table (c, 0.1%, 1x, no price, no costs)",
                     dumps({"total_pct": round(fund_only, 1), "per_year_pct": round(fund_only / 3.75, 1)}))
    note(f"farm sim · {mode} grid (pct per year, dd, years)", dumps(grid, ensure_ascii=False))


def now():
    snaps = {}
    for v in ("okx", "gate", "mexc", "bingx", "bitget", "hyperliquid"):
        try:
            s, out = SNAPS[v]()
        except Exception:  # noqa: BLE001
            out = {}
        snaps[v] = out
    coins = {}
    for v, out in snaps.items():
        for c, x in out.items():
            coins.setdefault(c, {})[v] = x["r"] * 8 / (x["h"] or 8)
    spreads = []
    for c, d in coins.items():
        if len(d) < 2:
            continue
        hi, lo = max(d, key=d.get), min(d, key=d.get)
        spreads.append((round((d[hi] - d[lo]) * 100, 3), c, hi, round(d[hi] * 100, 3), lo, round(d[lo] * 100, 3)))
    spreads.sort(reverse=True)
    note("farm now · cross-venue gaps (pct per 8h)", dumps({"coins on 2+ venues": len(spreads),
                                                                ">=0.1": sum(1 for x in spreads if x[0] >= 0.1),
                                                                ">=0.3": sum(1 for x in spreads if x[0] >= 0.3), "top": spreads[:40]}))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "probe"
    os.makedirs(OUT, exist_ok=True)
    if mode == "sim":
        sim(sys.argv[2])
    else:
        {"probe": probe, "probe2": probe2, "scan": scan, "prices": prices, "now": now}[mode]()
