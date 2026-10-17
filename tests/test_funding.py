"""Funding desk (paper funding carry): offline, fake fetch / history / prices."""
import json
import sqlite3

from cnq.agents import funding_desk as fd

T0 = 1_700_006_400_000 - 1_700_006_400_000 % fd.H8 + 3_600_000      # 1 h after a settlement boundary
BASES = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK"]
NOPX = lambda v, b: {}                                                  # noqa: E731


def fake(rates, extra=None):
    def f(bases):
        out = {"binance": dict(rates), "bybit": {}, "okx": {}, "bingx": {}, "hyperliquid": {}}
        for v, m in (extra or {}).items(): out[v] = m
        return out
    return f


def hist(rates, every=fd.H8):
    """Settled history at a constant rate per coin (per the coin's own interval `every`)."""
    def h(venue, sym, start, end):
        if sym not in rates: return []
        t0 = start - start % every + every
        return [(t, rates[sym]) for t in range(t0, end + 1, every)]
    return h


def ledger_ok(conn, r):
    m = {k: float(fd._meta(conn, k, 0)) for k in ("funding_total", "fees_total", "price_total")}
    assert abs(r["equity"] - (fd.START_EQUITY + m["funding_total"] - m["fees_total"] + m["price_total"])) < 0.01


def test_selection_threshold_and_max_coins():
    conn = sqlite3.connect(":memory:")
    rates = {b: 0.0002 for b in BASES} | {"ADA": 0.00005, "AVAX": -0.0001}     # ADA below 0.01%, AVAX negative
    res = fd.run(conn, T0, fake(rates), BASES, history=hist(rates), prices=NOPX)
    held = {p["symbol"] for p in res["positions"]}
    assert len(held) == 6 and "ADA" not in held and "AVAX" not in held
    assert res["venue"] == "binance"
    n = res["positions"][0]["notional"]
    assert abs(n - 10_000 / 6 / 1.2) < 0.01
    assert abs(res["equity"] - (10_000 - 6 * n * fd.COST)) < 0.01                  # entry costs 0.15% of notional each
    assert res["positions"][0]["avg7_n"] == fd.LOOK
    ledger_ok(conn, res)


def test_no_entry_on_a_few_hours_of_data():
    """The bug behind the live −$10: «7-day average» was taken over whatever few hours were recorded."""
    conn = sqlite3.connect(":memory:")
    for i in range(10):                                                    # 5 hours of 0.03% prints, no settled history
        r = fd.run(conn, T0 + i * 1_800_000, fake({"BTC": 0.0003}), ["BTC"], prices=NOPX)
    assert r["positions"] == [] and r["equity"] == 10_000
    for k in range(1, 20):                                                 # the same rate live for 18+ settlements → enter
        r = fd.run(conn, T0 + k * fd.H8, fake({"BTC": 0.0003}), ["BTC"], prices=NOPX)
        if r["positions"]: break
    assert r["positions"] and r["positions"][0]["avg7_n"] >= fd.MIN_SETTLED


def test_floor_rate_is_explicit_not_float_noise():
    # 18 or 21 prints of exactly 0.01%: the float average is 0.0001 or 0.00010000000000000002 — both must behave the same
    for k in (18, 21):
        conn = sqlite3.connect(":memory:")
        h = lambda v, s, a, b, k=k: [(T0 - T0 % fd.H8 - i * fd.H8, 0.0001) for i in range(k)]   # noqa: E731
        r = fd.run(conn, T0, fake({"BTC": 0.0001}), ["BTC"], history=h, prices=NOPX)
        assert [p["symbol"] for p in r["positions"]] == ["BTC"], k
    conn = sqlite3.connect(":memory:")
    r = fd.run(conn, T0, fake({"BTC": 0.000099}), ["BTC"], history=hist({"BTC": 0.000099}), prices=NOPX)
    assert r["positions"] == []


def test_history_backfill_normalises_interval():
    conn = sqlite3.connect(":memory:")
    r = fd.run(conn, T0, fake({"BTC": 0.0001}), ["BTC"], history=hist({"BTC": 0.00005}, every=4 * 3_600_000), prices=NOPX)
    assert r["positions"] and abs(r["positions"][0]["avg7"] - 0.0001) < 1e-9          # 0.005% per 4 h = 0.01% per 8 h
    calls = []
    fd.run(conn, T0 + 1_800_000, fake({"BTC": 0.0001}), ["BTC"], history=lambda *a: calls.append(a) or [], prices=NOPX)
    assert calls == []                                                     # enough history → no more requests


def test_hysteresis_keeps_then_exits():
    conn = sqlite3.connect(":memory:")
    fd.run(conn, T0, fake({"BTC": 0.0003}), ["BTC"], history=hist({"BTC": 0.0003}), prices=NOPX)
    # live rate drops: the 21-settlement average falls below the entry bar but stays > 0 → keep
    for k in range(1, 21):
        r = fd.run(conn, T0 + k * fd.H8, fake({"BTC": 0.00002}), ["BTC"], prices=NOPX)
    assert [p["symbol"] for p in r["positions"]] == ["BTC"] and 0 < r["positions"][0]["avg7"] < fd.ENTRY_THR
    # a fresh coin at the same low level is NOT entered
    r2 = fd.run(sqlite3.connect(":memory:"), T0, fake({"ETH": 0.00002}), ["ETH"], history=hist({"ETH": 0.00002}), prices=NOPX)
    assert r2["positions"] == []
    # average turns negative → exit, exit cost charged once
    eq = r["equity"]
    for k in range(21, 40):
        r = fd.run(conn, T0 + k * fd.H8, fake({"BTC": -0.0005}), ["BTC"], prices=NOPX)
        if not r["positions"]: break
    assert r["positions"] == [] and sum("Закрыли: BTC" in e["text"] for e in r["events"]) == 1
    n = 10_000 / 6 / 1.2
    assert r["equity"] < eq - n * fd.COST + 0.01                         # exit fee taken (and the negative funding paid)
    assert r["breakdown"]["exits"] == 1 and r["breakdown"]["entries"] == 1
    ledger_ok(conn, r)


def test_settlement_credited_once():
    conn = sqlite3.connect(":memory:")
    fd.run(conn, T0, fake({"BTC": 0.0003}), ["BTC"], history=hist({"BTC": 0.0003}), prices=NOPX)
    eq0 = fd.run(conn, T0 + 3 * 3_600_000, fake({"BTC": 0.0003}), ["BTC"], prices=NOPX)["equity"]
    s = T0 - T0 % fd.H8 + fd.H8
    r1 = fd.run(conn, s + 600_000, fake({"BTC": 0.0005}), ["BTC"], prices=NOPX)    # after settlement: credit rate recorded BEFORE it
    n = r1["positions"][0]["notional"]
    assert abs(r1["equity"] - (eq0 + n * 0.0003)) < 0.01
    r2 = fd.run(conn, s + 1_800_000, fake({"BTC": 0.0005}), ["BTC"], prices=NOPX)  # same settlement window again → nothing new
    assert r2["equity"] == r1["equity"]
    assert abs(r2["positions"][0]["earned_usd"] - round(n * 0.0003, 2)) < 0.01
    assert sum("Начислен фандинг" in e["text"] for e in r2["events"]) == 1
    assert r2["breakdown"]["settlements"] == 1 and abs(r2["breakdown"]["funding_usd"] - n * 0.0003) < 0.01
    ledger_ok(conn, r2)


def test_missing_rate_is_skipped_and_noted():
    conn = sqlite3.connect(":memory:")
    fd.run(conn, T0, fake({"BTC": 0.0003}), ["BTC"], history=hist({"BTC": 0.0003}), prices=NOPX)
    eq = fd.run(conn, T0 + 60_000, fake({"BTC": 0.0003}), ["BTC"], prices=NOPX)["equity"]
    s = T0 - T0 % fd.H8 + fd.H8
    conn.execute("DELETE FROM funding_hist"); conn.commit()                  # no record before the settlement
    r = fd.run(conn, s + 600_000, fake({"BTC": 0.0003}), ["BTC"], prices=NOPX)
    assert r["equity"] == eq and any("пропущено" in e["text"] for e in r["events"])
    assert [p["symbol"] for p in r["positions"]] == ["BTC"]                 # unknown average → keep, no churn


def test_price_leg_marked_and_realised_on_exit():
    conn = sqlite3.connect(":memory:")
    fd.run(conn, T0, fake({"BTC": 0.0003}), ["BTC"], history=hist({"BTC": 0.0003}), prices=lambda v, b: {"BTC": (100.0, 100.1)})
    r = fd.run(conn, T0 + 1_800_000, fake({"BTC": 0.0003}), ["BTC"], prices=lambda v, b: {"BTC": (110.0, 110.0)})
    n = r["positions"][0]["notional"]
    pl = n * (110 / 100 - 110 / 100.1)                                     # perp premium 0.1% → 0: the short perp gains
    assert abs(r["positions"][0]["price_usd"] - round(pl, 2)) < 0.01 and abs(r["breakdown"]["price_open_usd"] - pl) < 0.01
    assert abs(r["equity_mtm"] - (r["equity"] + pl)) < 0.01
    assert abs(r["breakdown"]["if_closed_usd"] - (r["equity"] + pl - n * fd.COST - 10_000)) < 0.01
    eq = r["equity"]
    for k in range(1, 40):                                                 # funding turns negative → exit, price leg realised
        r = fd.run(conn, T0 + k * fd.H8, fake({"BTC": -0.002}), ["BTC"], prices=lambda v, b: {"BTC": (110.0, 110.0)})
        if not r["positions"]: break
    assert r["positions"] == [] and r["breakdown"]["price_realized_usd"] == round(pl, 2)
    assert any("ценовая нога +$" in e["text"] for e in r["events"])
    ledger_ok(conn, r)
    assert r["equity"] < eq                                                # negative funding + exit fee outweigh the price gain


def test_legacy_account_gets_ledger_and_price_baseline():
    conn = sqlite3.connect(":memory:")
    fd._ensure(conn)
    conn.executescript("""DROP TABLE funding_desk_pos;
        CREATE TABLE funding_desk_pos(symbol TEXT PRIMARY KEY, notional REAL, entry_time INTEGER, earned_usd REAL);""")
    conn.execute("INSERT INTO funding_desk_pos VALUES('LTC', 1388.89, ?, 0.28)", (T0 - fd.DAY,))
    for k, v in (("venue", "binance"), ("equity", 10_000 - 1388.89 * fd.COST + 0.28), ("last_settled", T0 - T0 % fd.H8)):
        fd._set(conn, k, v)
    conn.execute("INSERT INTO funding_desk_equity VALUES(?, 10000)", (T0 - fd.DAY - 3_600_000,))
    conn.execute("INSERT INTO funding_desk_events VALUES(?, 'Открыли: LTC — …')", (T0 - fd.DAY,))
    r = fd.run(conn, T0 + 60_000, fake({"LTC": 0.0001}), ["LTC"], prices=lambda v, b: {"LTC": (60.0, 60.01)})
    b = r["breakdown"]
    assert abs(b["fees_usd"] - 1388.89 * fd.COST) < 0.01 and abs(b["funding_usd"] - 0.28) < 0.01 and b["entries"] == 1
    assert r["positions"][0]["price_from"] == T0 + 60_000 and b["price_from"] == T0 + 60_000
    assert any(e["text"].startswith("Правила уточнены") for e in r["events"])
    assert "комиссий −$2.08" in r["explain_ru"] and "+$0.28" in r["explain_ru"] and "раньше она не учитывалась" in r["explain_ru"]
    ledger_ok(conn, r)


def test_no_data_no_change_and_json_shape():
    conn = sqlite3.connect(":memory:")
    r = fd.run(conn, T0, lambda b: {}, ["BTC"])
    assert r["venue"] is None and r["equity"] == 10_000 and r["positions"] == []
    assert "не входил" in r["explain_ru"]
    extra = {"bybit": {"BTC": 0.0001}, "hyperliquid": {"BTC": 0.0004, "ETH": 0.0001}}
    rates = {"BTC": 0.0002, "ETH": 0.0002}
    r = fd.run(conn, T0 + 1_800_000, fake(rates, extra), ["BTC", "ETH"], history=hist(rates), prices=NOPX)
    for k in ("asof", "venue", "equity", "equity_mtm", "start_equity", "pnl_usd", "pnl_pct", "positions", "history", "events",
              "next_settlement", "table", "note", "name", "about", "breakdown", "explain_ru", "rules"):
        assert k in r, k
    assert set(r["positions"][0]) >= {"symbol", "notional", "entry_time", "earned_usd", "rate_now", "avg7", "avg7_n", "price_usd",
                                      "price_from", "entry_fee_usd", "days_held"}
    assert set(r["breakdown"]) >= {"funding_usd", "fees_usd", "price_open_usd", "price_realized_usd", "exit_fees_usd", "if_closed_usd",
                                   "settlements", "entries", "exits", "daily_funding_usd", "breakeven_days", "pnl_usd"}
    btc = next(t for t in r["table"] if t["symbol"] == "BTC")
    assert btc["spread"]["short_on"] == "hyperliquid" and btc["spread"]["long_on"] == "bybit"
    assert abs(btc["spread"]["apr_pct"] - 0.0003 * 3 * 365 * 100) < 0.01
    assert r["next_settlement"] % fd.H8 == 0 and r["next_settlement"] > r["asof"]
    assert "приближение" in r["note"] and "+5%" in r["about"]
    assert "комиссий −$" in r["explain_ru"] and "окупится" in r["explain_ru"] and "объём" in r["explain_ru"]
    json.dumps(r, ensure_ascii=False)
    assert len(r["history"]) <= 500
