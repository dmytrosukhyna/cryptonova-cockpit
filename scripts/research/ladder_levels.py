"""Owner 20.11.2026 23:51: re-plan the core ladder «грамотно» from data. Gathers (public market data only, no keys, no orders):
  • order-book bids (OKX spot + swap full depth, Binance spot + USDT-M, Bybit linear) — the bid notional per price bucket below the price;
  • volume profile — OKX hourly candles of the last 180 days, volume per price bucket (high-volume nodes);
  • swing lows — OKX daily candles of the last 2 years (a low under the 5 days on each side) and the lowest lows of the last 30 / 90 / 180 days;
  • funding (OKX swap, current and the last 30 prints) and open interest (OKX).
For BTC (bucket $250), ETH and SOL (bucket 0.5 % of the price). Prints compact job annotations. Usage: ladder_levels.py OUT.json"""
import json, statistics as st, sys, time, urllib.request

H, DAY = 3_600_000, 86_400_000


def get(url):
    for k in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "cnq-research"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(1 + k)
    print(f"fail {url[:90]}: {type(err).__name__}")
    return None


def okx_candles(inst, bar, n_pages):
    out, after = {}, ""
    for _ in range(n_pages):
        d = get(f"https://www.okx.com/api/v5/market/history-candles?instId={inst}&bar={bar}&limit=100" + (f"&after={after}" if after else ""))
        rows = (d or {}).get("data") or []
        if not rows:
            break
        for r in rows:
            out[int(r[0])] = (int(r[0]), float(r[2]), float(r[3]), float(r[4]), float(r[7]))     # t, high, low, close, quote vol
        after = rows[-1][0]
        time.sleep(0.12)
    return [out[k] for k in sorted(out)]


def books(coin):
    """[(venue, price, usd)] bids"""
    out = []
    d = get(f"https://www.okx.com/api/v5/market/books-full?instId={coin}-USDT&sz=5000")
    for b in (((d or {}).get("data") or [{}])[0].get("bids") or []):
        out.append(("okx_spot", float(b[0]), float(b[0]) * float(b[1])))
    d = get(f"https://www.okx.com/api/v5/public/instruments?instType=SWAP&instId={coin}-USDT-SWAP")
    ct = float((((d or {}).get("data") or [{}])[0]).get("ctVal") or 0)
    d = get(f"https://www.okx.com/api/v5/market/books-full?instId={coin}-USDT-SWAP&sz=5000")
    for b in (((d or {}).get("data") or [{}])[0].get("bids") or []):
        out.append(("okx_swap", float(b[0]), float(b[0]) * float(b[1]) * ct))
    d = get(f"https://api.binance.com/api/v3/depth?symbol={coin}USDT&limit=5000")
    for p, q in (d or {}).get("bids") or []:
        out.append(("bn_spot", float(p), float(p) * float(q)))
    d = get(f"https://fapi.binance.com/fapi/v1/depth?symbol={coin}USDT&limit=1000")
    for p, q in (d or {}).get("bids") or []:
        out.append(("bn_perp", float(p), float(p) * float(q)))
    d = get(f"https://api.bybit.com/v5/market/orderbook?category=linear&symbol={coin}USDT&limit=500")
    for p, q in (((d or {}).get("result") or {}).get("b") or []):
        out.append(("bybit_perp", float(p), float(p) * float(q)))
    return out


def bucket(px, step):
    return round(int(px // step) * step, 8)


def main():
    O = {"t": int(time.time() * 1000)}
    for coin in ("BTC", "ETH", "SOL"):
        tk = get(f"https://www.okx.com/api/v5/market/ticker?instId={coin}-USDT")
        px = float(tk["data"][0]["last"])
        step = 250.0 if coin == "BTC" else px * 0.005
        lo = px * 0.85
        # order books
        bk = books(coin)
        venues = sorted({v for v, _, _ in bk})
        agg, by_v, depth = {}, {}, {}
        for v, p, u in bk:
            if lo <= p < px:
                b = bucket(p, step)
                agg[b] = agg.get(b, 0.0) + u
                by_v.setdefault(v, {})[b] = by_v.setdefault(v, {}).get(b, 0.0) + u
            depth[v] = min(depth.get(v, 1e18), p)
        top_bids = sorted(agg.items(), key=lambda kv: -kv[1])[:12]
        # volume profile (hourly, 180 d)
        h1 = okx_candles(f"{coin}-USDT", "1H", 45)
        vp = {}
        for t, h, l, c, v in h1:
            if t >= O["t"] - 180 * DAY:
                b = bucket((h + l + c) / 3, step)
                vp[b] = vp.get(b, 0.0) + v
        below = {b: v for b, v in vp.items() if lo <= b < px}
        med = st.median(vp.values()) if vp else 0
        hvn = sorted(below.items(), key=lambda kv: -kv[1])[:10]
        # swing lows (daily, 2 y)
        d1 = okx_candles(f"{coin}-USDT", "1D", 8)
        swings = []
        for i in range(5, len(d1) - 5):
            if d1[i][2] == min(x[2] for x in d1[i - 5:i + 6]):
                swings.append((d1[i][0], d1[i][2]))
        recent = [s for s in swings if lo <= s[1] < px * 1.05][-10:]
        lows = {f"{n}d": min((x[2] for x in d1 if x[0] >= O["t"] - n * DAY), default=None) for n in (30, 90, 180, 365)}
        # funding / OI
        fr = get(f"https://www.okx.com/api/v5/public/funding-rate-history?instId={coin}-USDT-SWAP&limit=30")
        frs = [float(x["realizedRate"] or x["fundingRate"]) for x in ((fr or {}).get("data") or [])]
        oi = get(f"https://www.okx.com/api/v5/public/open-interest?instType=SWAP&instId={coin}-USDT-SWAP")
        O[coin] = {"px": px, "step": step, "venues": venues, "book_floor": depth,
                   "top_bids": [[b, round(u)] for b, u in top_bids],
                   "bids_by_venue_top": {v: [[b, round(u)] for b, u in sorted(m.items(), key=lambda kv: -kv[1])[:5]] for v, m in by_v.items()},
                   "hvn_below": [[b, round(v / med, 2) if med else None] for b, v in hvn],
                   "swing_lows": [[time.strftime("%Y-%m-%d", time.gmtime(t / 1000)), p] for t, p in recent], "lows": lows,
                   "funding_now": frs[0] if frs else None, "funding_30_mean": round(st.mean(frs), 6) if frs else None,
                   "oi_usd": float((((oi or {}).get("data") or [{}])[0]).get("oiUsd") or 0) or None}
        print(f"{coin} px {px}: book venues {venues}; bids {top_bids[:5]}; hvn {hvn[:4]}; lows {lows}", flush=True)
    json.dump(O, open(sys.argv[1], "w"))


if __name__ == "__main__":
    main()
