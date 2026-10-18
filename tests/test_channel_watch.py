import sqlite3

from cnq.agents import channel_watch as W

H = 3_600_000
T0 = 1_792_000_000_000


def mk(btc=80_000.0, eth=2_400.0, alts=1.0):
    top = [{"symbol": "BTC", "price": btc}, {"symbol": "ETH", "price": eth}, {"symbol": "HYPE", "price": 40.0}]
    top += [{"symbol": s, "price": alts} for s in ("SOL", "XRP", "DOGE", "ADA")]
    return {"top": top}


def test_forecast_parsing():
    coins = {"BTC", "ETH", "HYPE"}
    assert W.forecasts("Жду коррекцию по #BTC сегодня", coins) == [{"coin": "BTC", "side": "down", "horizon_h": 24}]
    assert W.forecasts("Альты могут упасть на 5-10%, откат", coins) == [{"coin": "ALTS", "side": "down", "horizon_h": 72}]
    assert W.forecasts("#HYPE набираю, цель выше", coins)[0]["side"] == "up"
    assert W.forecasts("BTC: рост или падение — смотрим", coins) == []          # both directions → skipped
    assert W.forecasts("Трамп сказал про Украину", coins) == []


def test_first_run_only_remembers_then_records_and_resolves():
    conn = sqlite3.connect(":memory:")
    page1 = [{"id": 10, "text": "#BTC вниз сегодня"}]
    a = W.step(conn, mk(), T0, lambda ch: page1)
    assert a["channels"]["Mr_Trader_Pro"]["forecasts"] == 0 and a["channels"]["Mr_Trader_Pro"]["open"] == 0
    page2 = page1 + [{"id": 11, "text": "Жду откат по #BTC сегодня"}, {"id": 12, "text": "Альты: коррекция 5-10%"}]
    b = W.step(conn, mk(), T0 + H, lambda ch: page2)
    assert b["channels"]["Mr_Trader_Pro"]["open"] == 2
    c = W.step(conn, mk(btc=78_000.0, alts=1.02), T0 + 80 * H, lambda ch: page2)       # BTC −2.5 % (hit), alts +2 % (miss)
    ch = c["channels"]["Mr_Trader_Pro"]
    assert ch["forecasts"] == 2 and ch["hits"] == 1 and ch["misses"] == 1 and ch["hit_rate_pct"] == 50 and ch["always_up_pct"] == 50
    assert {r["coin"]: r["result"] for r in ch["recent"]} == {"BTC": "hit", "ALTS": "miss"} and "text" not in ch["recent"][0]
