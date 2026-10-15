"""Order-book snapshots from BingX for the panel (web/books.json): bids/asks (price, qty) per coin, so the chart's
depth band always has bars even when the viewer's browser cannot reach Binance/Bybit directly."""
import json, time
from cnq.bingx.rest import BingXRest
from cnq.config import Config

SYMS = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT", "DOGE-USDT", "ADA-USDT", "AVAX-USDT", "LINK-USDT", "LTC-USDT",
        "APT-USDT", "ARB-USDT", "BCH-USDT", "DOT-USDT", "NEAR-USDT", "OP-USDT", "SUI-USDT", "TRX-USDT"]
rest = BingXRest(Config().bingx)
out = {"asof": int(time.time() * 1000), "source": "BingX USDT-M depth", "books": {}}
for s in SYMS:
    try:
        d = rest.depth(s, 100)
        out["books"][s.replace("-", "")] = {"t": d["T"], "bids": [[p, q] for p, q in d["bids"][:100]], "asks": [[p, q] for p, q in d["asks"][:100]]}
    except Exception as e:  # noqa: BLE001
        out["books"][s.replace("-", "")] = {"error": str(e)[:80]}
    time.sleep(0.25)
json.dump(out, open("web/books.json", "w"))
ok = sum(1 for b in out["books"].values() if "bids" in b)
print(f"books.json: {ok}/{len(SYMS)} books")
