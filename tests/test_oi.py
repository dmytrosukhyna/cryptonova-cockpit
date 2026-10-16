"""Rule F (open-interest flush): OI history → 4h change → desk opens a long sized as a share of equity."""
import sqlite3

from cnq.agents import oi_watch, paper_desk as pd


def test_oi_change_same_source():
    conn = sqlite3.connect(":memory:")
    t0 = 1_700_000_000_000
    vals = {0: 1000.0, 2: 950.0, 4: 870.0, 6: 860.0, 8: 850.0}          # half-hours → OI (BingX)
    for h, v in vals.items():
        oi_watch.record(conn, ["SOL"], t0 + h * 1_800_000, fetch_bingx=lambda b, v=v: v, fetch_hl=lambda: {"SOL": 5.0})
    ch = oi_watch.change(conn, "SOL", t0 + 8 * 1_800_000)
    assert abs(ch - (850 / 1000 - 1)) < 1e-9          # compared with BingX 4 h ago, never mixed with the Hyperliquid value
    assert oi_watch.change(conn, "BTC", t0 + 8 * 1_800_000) is None


def test_flush_opens_long_with_share_sizing():
    t0 = 1_700_000_000_000
    prices = [100.0] * 280 + [100 - i * 0.5 for i in range(1, 21)]      # −10 % over the last 20 bars (5 h)
    candles = [[t0 + i * 900_000, p, p * 1.002, p * 0.998, p, 100.0] for i, p in enumerate(prices)]
    coin = {"symbol": "SOL-USDT", "candles": candles, "levels": [], "regime": {"long": "BULL"}, "oi_chg4h": -0.15}
    desk = pd.PaperDesk(sqlite3.connect(":memory:"), "bal")
    res = desk.step([coin], True, t0 + len(prices) * 900_000)
    f = [t for t in res["open"] if t["grade"] == "F"]
    assert f, res["events"]
    notional = f[0]["qty"] * f[0]["entry"]
    assert 2400 < notional < 2600                     # 25 % of $10 000 («норма»)
    coin["oi_chg4h"] = -0.05                          # ordinary deleveraging → no signal
    desk2 = pd.PaperDesk(sqlite3.connect(":memory:"), "bal")
    assert not [t for t in desk2.step([coin], True, t0 + len(prices) * 900_000)["open"] if t["grade"] == "F"]
