"""Triangle watch on synthetic books: a planted mispricing is found with fees applied, a fair book gives no window."""
import sqlite3

from cnq.agents import tri_watch as tw

T = 1_700_000_000_000
FAIR = {("BTC", "USDT"): (100_000.0, 100_010.0, 1.0, 1.0), ("ETH", "USDT"): (4000.0, 4000.4, 10.0, 10.0),
        ("ETH", "BTC"): (0.04, 0.040004, 10.0, 10.0), ("XRP", "USDT"): (2.0, 2.0002, 1e5, 1e5), ("XRP", "BTC"): (0.00002, 0.0000200020, 1e5, 1e5)}


def test_symbol_split():
    assert tw.split("ETHBTC", None) == ("ETH", "BTC") and tw.split("WBETHETH", None) == ("WBETH", "ETH")
    assert tw.split("BTCUSDC", None) == ("BTC", "USDC") and tw.split("FDUSDUSDT", None) == ("FDUSD", "USDT")
    assert tw.split("ethbtc", None) == ("ETH", "BTC") and tw.split("ETH-BTC", "-") == ("ETH", "BTC")
    assert tw.split("BTCEUR", None) is None and tw.split("BTC-EUR", "-") is None


def test_fair_book_has_no_window_and_best_is_least_negative():
    rows, n = tw.scan(FAIR, 0.001)
    assert n == 2                                          # {BTC, ETH, USDT} and {BTC, XRP, USDT}
    assert all(r["net"] < 0 for r in rows)
    assert -0.0032 < rows[0]["net"] < -0.0029              # ≈ three fees of 0.1 % plus the spreads
    conn = sqlite3.connect(":memory:")
    d = tw.run(conn, now=T, fetch=lambda: {"Binance": FAIR}, samples=1, pause=0)
    assert d["day"]["windows"] == 0 and d["scanned_total"] == 2 and d["scanned"]["Binance"]["pairs"] == 5
    assert "наименее убыточный" in d["explain_ru"]


def test_planted_triangle_found_with_fees():
    book = dict(FAIR); book[("ETH", "BTC")] = (0.0405, 0.04051, 10.0, 10.0)      # ETH overpriced in BTC
    rows, _ = tw.scan(book, 0.001)
    r = rows[0]
    assert r["path"] == "USDT → ETH → BTC → USDT" and r["nodes"] == "BTC,ETH,USDT"
    expect = (1 / 4000.4) * 0.0405 * 100_000.0
    assert abs(r["gross"] - (expect - 1)) < 1e-6 and abs(r["net"] - (expect * 0.999 ** 3 - 1)) < 1e-6
    rows0, _ = tw.scan(book, 0.0)
    assert abs(rows0[0]["net"] - rows0[0]["gross"]) < 1e-9
    # depth: 10 ETH at the ETH/USDT ask ($40 004), 10 ETH at the ETH/BTC bid, 1 BTC at the BTC/USDT bid ($100 000) → ≈ $40 000
    assert 39_000 < r["size_usd"] < 41_000


def test_run_records_window_once_and_caps_ticket():
    book = dict(FAIR); book[("ETH", "BTC")] = (0.0405, 0.04051, 0.002, 10.0)    # only 0.002 ETH (≈ $8) at the bid
    conn = sqlite3.connect(":memory:")
    d = tw.run(conn, now=T, fetch=lambda: {"OKX": book}, samples=2, pause=0)
    net = d["now"][0]["net"]
    assert d["day"]["windows"] == 1 and d["day"]["observations"] == 2 and d["day"]["stuck_observations"] == 1
    assert abs(d["day"]["virtual_pnl_usd"] - round(net * d["now"][0]["size_usd"], 2)) < 0.02 and d["now"][0]["size_usd"] < 10
    d = tw.run(conn, now=T + 1_800_000, fetch=lambda: {"OKX": book}, samples=1, pause=0)
    assert d["day"]["windows"] == 1 and d["stuck"] and d["stuck"][0]["venue"] == "OKX"


def test_suspicious_and_bad_books_ignored():
    book = dict(FAIR); book[("ETH", "BTC")] = (0.05, 0.0501, 10.0, 10.0)          # +25 %: stale quote, not money
    conn = sqlite3.connect(":memory:")
    d = tw.run(conn, now=T, fetch=lambda: {"Gate": book}, samples=1, pause=0)
    assert d["day"]["windows"] == 0 and d["suspicious"] and d["suspicious"][0]["venue"] == "Gate"
    parsed = tw.parse([("ETHBTC", "0.05", "0.04", "1", "1"), ("XRPBTC", "1", "1.5", "1", "1"), ("BTCUSDT", "100", "100.1", None, "")], None)
    assert list(parsed) == [("BTC", "USDT")] and parsed[("BTC", "USDT")][2:] == (None, None)    # crossed and 50 %-wide books dropped
