"""Order-book snapshots from BingX for the panel (web/books.json): bids/asks (price, qty) per coin, so the chart's
depth band always has bars even when the viewer's browser cannot reach Binance/Bybit directly."""
import json, os, sys, time
sys.path.insert(0, os.getcwd())   # run as a script from the repo root (Actions): without this «No module named cnq»
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

# history for research (16.10): depth within ±1 % of mid and the biggest wall on each side, every server run
try:
    import sqlite3
    con = sqlite3.connect("data/agents.sqlite")
    con.execute("CREATE TABLE IF NOT EXISTS book_hist(symbol TEXT, time INTEGER, mid REAL, bid1 REAL, ask1 REAL, bwall_p REAL, bwall_usd REAL, awall_p REAL, awall_usd REAL, PRIMARY KEY(symbol, time))")
    for sym, b in out["books"].items():
        if "bids" not in b or not b["bids"] or not b["asks"]:
            continue
        bids = [(float(p), float(q)) for p, q in b["bids"]]; asks = [(float(p), float(q)) for p, q in b["asks"]]
        mid = (max(p for p, _ in bids) + min(p for p, _ in asks)) / 2
        bid1 = sum(p * q for p, q in bids if p >= mid * 0.99); ask1 = sum(p * q for p, q in asks if p <= mid * 1.01)
        bw = max(bids, key=lambda x: x[0] * x[1]); aw = max(asks, key=lambda x: x[0] * x[1])
        con.execute("INSERT OR REPLACE INTO book_hist VALUES(?,?,?,?,?,?,?,?,?)", (sym, out["asof"], mid, bid1, ask1, bw[0], bw[0] * bw[1], aw[0], aw[0] * aw[1]))
    con.commit()
    print("book_hist rows:", con.execute("SELECT count(*) FROM book_hist").fetchone()[0])
except Exception as e:  # noqa: BLE001
    print("book_hist failed", e)
ok = sum(1 for b in out["books"].values() if "bids" in b)
print(f"books.json: {ok}/{len(SYMS)} books")
