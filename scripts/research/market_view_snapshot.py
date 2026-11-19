"""Market-direction snapshot (19.11, analyst «Направление рынка»). Public read-only endpoints, no keys, no trading.
Collects derivatives positioning for BTC/ETH/SOL from OKX / Binance / Bybit / Hyperliquid / Deribit and writes one JSON.
Every source is optional: a blocked/failed one is recorded under "errors" and the rest still go out.
usage: python scripts/research/market_view_snapshot.py out.json
"""
import json, sys, time, urllib.request, urllib.error

UA = {"User-Agent": "cnq-research/1.0"}
ERR = {}


def get(url, data=None, timeout=15):
    req = urllib.request.Request(url, headers={**UA, **({"Content-Type": "application/json"} if data else {})},
                                 data=json.dumps(data).encode() if data else None)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def safe(name, fn):
    try:
        return fn()
    except Exception as e:  # noqa
        ERR[name] = repr(e)[:160]
        return None


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def book_imb(bids, asks, mid, pcts=(0.01, 0.02)):
    out = {}
    for p in pcts:
        b = sum(f(x[0]) * f(x[1]) for x in bids if f(x[0]) >= mid * (1 - p))
        a = sum(f(x[0]) * f(x[1]) for x in asks if f(x[0]) <= mid * (1 + p))
        out[f"{int(p*100)}pct"] = {"bid_usd": round(b), "ask_usd": round(a), "imb": round((b - a) / (b + a), 4) if a + b else None}
    return out


def okx(ccy):
    base = "https://www.okx.com/api/v5"
    inst = f"{ccy}-USDT-SWAP"
    o = {}
    fr = safe(f"okx_fr_{ccy}", lambda: get(f"{base}/public/funding-rate?instId={inst}")["data"][0])
    if fr:
        o["funding"] = f(fr.get("fundingRate"))
    frh = safe(f"okx_frh_{ccy}", lambda: get(f"{base}/public/funding-rate-history?instId={inst}&limit=21")["data"])
    if frh:
        v = [f(x["realizedRate"] or x["fundingRate"]) for x in frh]
        o["funding_7d_avg"] = sum(v) / len(v)
    t = safe(f"okx_tk_{ccy}", lambda: get(f"{base}/market/ticker?instId={inst}")["data"][0])
    if t:
        o["last"] = f(t["last"])
    ixt = safe(f"okx_ix_{ccy}", lambda: get(f"{base}/market/index-tickers?instId={ccy}-USDT")["data"][0])
    if ixt and t:
        o["basis_pct"] = round((f(t["last"]) / f(ixt["idxPx"]) - 1) * 100, 4)
    oi = safe(f"okx_oih_{ccy}", lambda: get(f"{base}/rubik/stat/contracts/open-interest-volume?ccy={ccy}&period=1H")["data"])
    if oi:  # [ts, oi_usd, vol_usd] newest first
        o["oi_usd"] = f(oi[0][1])
        if len(oi) > 24:
            o["oi_chg24_pct"] = round((f(oi[0][1]) / f(oi[24][1]) - 1) * 100, 2)
        if len(oi) > 4:
            o["oi_chg4_pct"] = round((f(oi[0][1]) / f(oi[4][1]) - 1) * 100, 2)
    ls = safe(f"okx_ls_{ccy}", lambda: get(f"{base}/rubik/stat/contracts/long-short-account-ratio?ccy={ccy}&period=1H")["data"])
    if ls:
        o["ls_ratio"] = f(ls[0][1])
        if len(ls) > 24:
            o["ls_ratio_24h_ago"] = f(ls[24][1])
    tv = safe(f"okx_taker_{ccy}", lambda: get(f"{base}/rubik/stat/taker-volume?ccy={ccy}&instType=CONTRACTS&period=1H")["data"])
    if tv:  # [ts, sell, buy]
        s4 = sum(f(x[1]) for x in tv[:4]); b4 = sum(f(x[2]) for x in tv[:4])
        s24 = sum(f(x[1]) for x in tv[:24]); b24 = sum(f(x[2]) for x in tv[:24])
        o["taker_buy_share_4h"] = round(b4 / (b4 + s4), 4) if b4 + s4 else None
        o["taker_buy_share_24h"] = round(b24 / (b24 + s24), 4) if b24 + s24 else None
    bk = safe(f"okx_book_{ccy}", lambda: get(f"{base}/market/books-full?instId={ccy}-USDT&sz=5000")["data"][0])
    if bk and o.get("last"):
        o["spot_book"] = book_imb(bk["bids"], bk["asks"], o["last"])
    liq = safe(f"okx_liq_{ccy}", lambda: get(f"{base}/public/liquidation-orders?instType=SWAP&uly={ccy}-USDT&state=filled&limit=100")["data"])
    if liq:
        now = time.time() * 1000
        ct = {"BTC": 0.01, "ETH": 0.1, "SOL": 1}.get(ccy, 1)
        L = S = 0.0
        for row in liq:
            for d in row.get("details", []):
                if now - f(d["ts"]) > 4 * 3600e3:
                    continue
                usd = f(d["sz"]) * ct * f(d["bkPx"])
                if d.get("posSide") == "long" or (d.get("posSide") == "net" and d.get("side") == "sell"):
                    L += usd
                else:
                    S += usd
        o["liq_4h_usd"] = {"long": round(L), "short": round(S)}
    return o


def okx_candles(inst, bar, n):
    out, after = [], ""
    while len(out) < n:
        d = get(f"https://www.okx.com/api/v5/market/history-candles?instId={inst}&bar={bar}&limit=100" + (f"&after={after}" if after else ""))["data"]
        if not d:
            break
        out += d
        after = d[-1][0]
    return [[int(x[0]), f(x[1]), f(x[2]), f(x[3]), f(x[4]), f(x[7])] for x in out][::-1]


def binance(ccy):
    b, o = "https://fapi.binance.com", {}
    sym = f"{ccy}USDT"
    p = safe(f"bn_prem_{ccy}", lambda: get(f"{b}/fapi/v1/premiumIndex?symbol={sym}"))
    if p:
        o["funding"] = f(p["lastFundingRate"]); o["basis_pct"] = round((f(p["markPrice"]) / f(p["indexPrice"]) - 1) * 100, 4)
    oi = safe(f"bn_oih_{ccy}", lambda: get(f"{b}/futures/data/openInterestHist?symbol={sym}&period=1h&limit=25"))
    if oi:
        o["oi_usd"] = f(oi[-1]["sumOpenInterestValue"]); o["oi_chg24_pct"] = round((f(oi[-1]["sumOpenInterest"]) / f(oi[0]["sumOpenInterest"]) - 1) * 100, 2)
    g = safe(f"bn_ls_{ccy}", lambda: get(f"{b}/futures/data/globalLongShortAccountRatio?symbol={sym}&period=1h&limit=25"))
    if g:
        o["ls_ratio"] = f(g[-1]["longShortRatio"]); o["ls_ratio_24h_ago"] = f(g[0]["longShortRatio"])
    t = safe(f"bn_top_{ccy}", lambda: get(f"{b}/futures/data/topLongShortPositionRatio?symbol={sym}&period=1h&limit=1"))
    if t:
        o["top_pos_ls"] = f(t[-1]["longShortRatio"])
    tk = safe(f"bn_taker_{ccy}", lambda: get(f"{b}/futures/data/takerlongshortRatio?symbol={sym}&period=1h&limit=24"))
    if tk:
        bv = sum(f(x["buyVol"]) for x in tk); sv = sum(f(x["sellVol"]) for x in tk)
        o["taker_buy_share_24h"] = round(bv / (bv + sv), 4)
    d = safe(f"bn_book_{ccy}", lambda: get(f"{b}/fapi/v1/depth?symbol={sym}&limit=1000"))
    if d and p:
        o["perp_book"] = book_imb(d["bids"], d["asks"], f(p["markPrice"]))
    return o


def bybit(ccy):
    b, o = "https://api.bybit.com/v5/market", {}
    t = safe(f"bb_tk_{ccy}", lambda: get(f"{b}/tickers?category=linear&symbol={ccy}USDT")["result"]["list"][0])
    if t:
        o["funding"] = f(t["fundingRate"]); o["oi_usd"] = f(t["openInterestValue"])
    a = safe(f"bb_ls_{ccy}", lambda: get(f"{b}/account-ratio?category=linear&symbol={ccy}USDT&period=1h&limit=25")["result"]["list"])
    if a:
        o["long_share"] = f(a[0]["buyRatio"]); o["long_share_24h_ago"] = f(a[-1]["buyRatio"])
    oi = safe(f"bb_oih_{ccy}", lambda: get(f"{b}/open-interest?category=linear&symbol={ccy}USDT&intervalTime=1h&limit=25")["result"]["list"])
    if oi:
        o["oi_chg24_pct"] = round((f(oi[0]["openInterest"]) / f(oi[-1]["openInterest"]) - 1) * 100, 2)
    return o


def hyperliquid():
    d = get("https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"})
    out = {}
    for m, c in zip(d[0]["universe"], d[1]):
        if m["name"] in ("BTC", "ETH", "SOL"):
            out[m["name"]] = {"funding_1h": f(c["funding"]), "oi_usd": round(f(c["openInterest"]) * f(c["markPx"])),
                              "premium": f(c.get("premium")), "mark": f(c["markPx"])}
    return out


def deribit(ccy):
    b, o = "https://www.deribit.com/api/v2/public", {}
    s = get(f"{b}/get_book_summary_by_currency?currency={ccy}&kind=option")["result"]
    put_oi = sum(x["open_interest"] for x in s if x["instrument_name"].endswith("-P"))
    call_oi = sum(x["open_interest"] for x in s if x["instrument_name"].endswith("-C"))
    put_v = sum(x["volume"] for x in s if x["instrument_name"].endswith("-P"))
    call_v = sum(x["volume"] for x in s if x["instrument_name"].endswith("-C"))
    o["pc_oi"] = round(put_oi / call_oi, 3) if call_oi else None
    o["pc_vol24"] = round(put_v / call_v, 3) if call_v else None
    spot = s[0].get("underlying_price") or s[0].get("estimated_delivery_price")
    # crude 10%-OTM skew on the expiry nearest to ~7 days: put IV(0.9S) - call IV(1.1S)
    now = time.time() * 1000
    exp = {}
    for x in s:
        parts = x["instrument_name"].split("-")
        exp.setdefault(parts[1], []).append(x)
    best, bestd = None, 1e18
    for k, rows in exp.items():
        try:
            ts = time.mktime(time.strptime(k, "%d%b%y")) * 1000 + 8 * 3600e3
        except ValueError:
            continue
        dd = abs((ts - now) / 86400e3 - 7)
        if (ts - now) > 2 * 86400e3 and dd < bestd:
            best, bestd = k, dd
    if best:
        rows = exp[best]
        def iv_at(kind, strike):
            c = [x for x in rows if x["instrument_name"].endswith(kind) and x.get("mark_iv")]
            c.sort(key=lambda x: abs(float(x["instrument_name"].split("-")[2]) - strike))
            return c[0]["mark_iv"] if c else None
        u = rows[0].get("underlying_price") or spot
        p, cc = iv_at("-P", 0.9 * u), iv_at("-C", 1.1 * u)
        o["skew_expiry"] = best; o["put10_iv"] = p; o["call10_iv"] = cc
        o["skew_10pct"] = round(p - cc, 2) if p and cc else None
    dv = safe(f"dvol_{ccy}", lambda: get(f"{b}/get_volatility_index_data?currency={ccy}&resolution=3600&start_timestamp={int(now-26*3600e3)}&end_timestamp={int(now)}")["result"]["data"])
    if dv:
        o["dvol"] = dv[-1][4]; o["dvol_24h_ago"] = dv[0][4]
    return o


def main(path):
    snap = {"ts": int(time.time() * 1000), "okx": {}, "binance": {}, "bybit": {}, "deribit": {}}
    for c in ("BTC", "ETH", "SOL"):
        snap["okx"][c] = safe(f"okx_{c}", lambda: okx(c))
        snap["binance"][c] = safe(f"bn_{c}", lambda: binance(c))
        snap["bybit"][c] = safe(f"bb_{c}", lambda: bybit(c))
    snap["hyperliquid"] = safe("hl", hyperliquid)
    for c in ("BTC", "ETH"):
        snap["deribit"][c] = safe(f"deribit_{c}", lambda: deribit(c))
    snap["btc_1d"] = safe("btc_1d", lambda: okx_candles("BTC-USDT", "1Dutc", 300))
    snap["btc_1h"] = safe("btc_1h", lambda: okx_candles("BTC-USDT", "1H", 200))
    snap["errors"] = ERR
    json.dump(snap, open(path, "w"))
    print(json.dumps({k: v for k, v in snap.items() if k not in ("btc_1d", "btc_1h")})[:6000])


if __name__ == "__main__":
    main(sys.argv[1])
