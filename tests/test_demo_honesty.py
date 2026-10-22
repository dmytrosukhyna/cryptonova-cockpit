"""22.10 (audit 20.10 §4): the demo desks must not flatter their record — a short sells lower (slippage against it), funding is
charged every 8 h, a market entry needs a fresh bar and fills at the freshest price, the entry bar is risk-checked, and the
trades taken before the fix get ONE appended correction row (the trades themselves are not rewritten)."""
import json
import sqlite3

from cnq.agents import funding_accrual as FA
from cnq.agents import own_desk as od
from cnq.agents import paper_desk as pd

H8 = FA.PERIOD_MS
T0 = 1_792_627_200_000            # 2026-10-22 00:00 UTC — a settlement boundary


def mk(t0, prices, step=900_000):
    return [[t0 + i * step, p, p * 1.001, p * 0.999, p, 100.0] for i, p in enumerate(prices)]


def coin(sym, t0, prices, **kw):
    return {"symbol": sym, "candles": mk(t0, prices), "levels": [], "regime": {"long": "BULL"}, **kw}


def sig(side, entry, stop, sid="C-TEST-1"):
    return {"id": sid, "symbol": "SOL", "side": side, "grade": "C", "kind": "TEST", "entry": entry, "stop": stop,
            "tps": [entry * (0.5 if side == "SHORT" else 1.5)] * 3, "parts": [1.0, 0.0, 0.0], "order": "MARKET", "hold_h": 48, "reason": "test"}


def desk_with(signals):
    d = pd.PaperDesk(sqlite3.connect(":memory:"), "aggr", classes={"C"})
    d.signals = lambda c, b, n: [s for s in signals if s["symbol"] == c["symbol"].replace("-USDT", "")]
    return d


def test_market_short_fills_below_the_signal_price():
    now = T0 + 300 * 900_000
    d = desk_with([sig("SHORT", 100.0, 105.0)])
    res = d.step([coin("SOL-USDT", T0, [100.0] * 300)], True, now)
    t = res["open"][0]
    assert t["side"] == "SHORT" and abs(t["entry"] - 100.0 * (1 - pd.SLIP)) < 1e-9          # was 100 × (1 + SLIP) before 22.10
    d2 = desk_with([sig("LONG", 100.0, 95.0)])
    t2 = d2.step([coin("SOL-USDT", T0, [100.0] * 300)], True, now)["open"][0]
    assert abs(t2["entry"] - 100.0 * (1 + pd.SLIP)) < 1e-9                                  # a long still buys higher


def test_market_entry_needs_a_fresh_bar():
    t0 = T0
    d = desk_with([sig("LONG", 100.0, 95.0)])
    stale_now = t0 + 300 * 900_000 + 45 * 60_000                                          # the newest bar is 60 min old
    res = d.step([coin("SOL-USDT", t0, [100.0] * 300)], True, stale_now)
    assert not res["open"] and any("отстают" in e for e in res["events"])
    res = d.step([coin("SOL-USDT", t0 + 4 * 900_000, [100.0] * 300)], True, stale_now)   # fresh data → the same signal opens
    assert res["open"]


def test_climax_entry_takes_the_freshest_price_not_the_bar_open():
    cs = mk(T0, [10.0] * 301)
    cs[-2] = [cs[-2][0], 10.0, 10.0, 9.2, 9.6, 500.0]
    cs[-1] = [cs[-1][0], 9.6, 9.65, 9.4, 9.45, 100.0]                                     # opened 9.6, now trades at 9.45
    d = pd.PaperDesk(sqlite3.connect(":memory:"), classes={"A", "B", "R"})
    res = d.step([{"symbol": "SOL-USDT", "candles": cs, "levels": [], "regime": {"long": "BULL"}}], True, cs[-1][0] + 60_000)
    t = res["open"][0]
    assert t["grade"] == "B" and abs(t["entry"] - 9.45 * (1 + pd.SLIP)) < 1e-9


def test_the_entry_bar_is_risk_checked_with_the_live_price():
    d = desk_with([sig("LONG", 100.0, 99.0)])
    prices = [100.0] * 300
    now = T0 + 299 * 900_000 + 60_000                                                     # inside the newest (entry) bar
    assert d.step([coin("SOL-USDT", T0, prices)], True, now)["open"]
    crashed = mk(T0, prices)
    crashed[-1] = [crashed[-1][0], 100.0, 100.0, 98.0, 98.5, 100.0]                       # same bar, live price under the stop
    res = d.step([{"symbol": "SOL-USDT", "candles": crashed, "levels": [], "regime": {"long": "BULL"}}], True, now + 120_000)
    assert not res["open"] and res["closed"][0]["exit_kind"] == "STOP"


def test_funding_is_charged_by_side_once_per_settlement():
    conn = sqlite3.connect(":memory:")
    trades = [{"id": "L", "symbol": "SOL", "side": "LONG", "opened": T0 - H8 + 1, "qty": 10.0, "qty_left": 10.0, "entry": 100.0},
              {"id": "S", "symbol": "SOL", "side": "SHORT", "opened": T0 - H8 + 1, "qty": 10.0, "qty_left": 10.0, "entry": 100.0}]
    coins = {"SOL": {"funding": [[T0 + 3_000, 0.0001]], "candles": mk(T0 - 900_000, [100.0, 100.0, 100.0])}}
    got = FA.accrue(conn, "aggr", trades, coins, T0 + 2 * 3_600_000)
    assert abs(got["L"] + 0.1) < 1e-9 and abs(got["S"] - 0.1) < 1e-9                    # 0.01 % of $1 000: long pays, short receives
    assert FA.accrue(conn, "aggr", trades, coins, T0 + 3 * 3_600_000) == {}                # never twice
    s = FA.summary(conn, "aggr")
    assert s["settlements"] == 2 and s["missing"] == 0 and s["usd"] == 0.0


def test_funding_waits_for_the_settled_number_and_never_invents_one():
    conn = sqlite3.connect(":memory:")
    t = [{"id": "L", "symbol": "SOL", "side": "LONG", "opened": T0 - 1, "qty": 1.0, "qty_left": 1.0, "entry": 100.0}]
    assert FA.accrue(conn, "d", t, {"SOL": {}}, T0 + 30 * 60_000) == {}                  # 30 min after: wait for the store
    assert conn.execute("SELECT COUNT(*) FROM funding_ledger").fetchone()[0] == 0
    assert FA.accrue(conn, "d", t, {"SOL": {"funding_now": 0.0002}}, T0 + 90 * 60_000)["L"] < 0   # no history, ≤ 2 h → live rate
    t2 = [{**t[0], "id": "L2"}]
    assert FA.accrue(conn, "d", t2, {"SOL": {}}, T0 + 5 * 3_600_000).get("L2", 0.0) == 0.0         # 5 h, nothing known → missing, 0 $
    assert FA.summary(conn, "d")["missing"] == 1


def test_desk_step_charges_funding_into_the_open_trade():
    d = desk_with([sig("LONG", 100.0, 90.0)])
    open_at = T0 - 2 * 900_000
    res = d.step([coin("SOL-USDT", open_at - 299 * 900_000, [100.0] * 300)], True, open_at + 60_000)
    assert res["open"]
    later = T0 + 2 * 3_600_000
    c = coin("SOL-USDT", later - 299 * 900_000, [100.0] * 300, funding=[[T0, 0.0005]])
    res = d.step([c], True, later)
    o = res["open"][0]
    assert o["pnl_usd"] < 0 and res["funding"]["settlements"] == 1 and res["funding"]["usd"] < 0


def test_one_time_correction_is_appended_once_and_counts_in_equity():
    conn = sqlite3.connect(":memory:")
    d = pd.PaperDesk(conn, "aggr")
    sig_px, qty = 100.0, 10.0
    row = lambda tid, side, entry, opened, closed: (tid, "SOL", side, "C", "T", entry, 105.0, 105.0, "[1,1,1]", "[1,0,0]", qty, 0.0, 50.0,
                                                    opened, opened + 10 * H8, closed, "STOP", 0.0, 0.0, 0, "x", opened)
    conn.execute(f"INSERT INTO {d.T_TR} VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row("SH", "SHORT", sig_px * (1 + pd.SLIP), T0 - H8 - 5, T0 + 60_000))
    conn.execute(f"INSERT INTO {d.T_TR} VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row("LG", "LONG", 100.0, T0 - 5, T0 + 60_000))
    conn.commit()
    coins = [coin("SOL-USDT", T0 - 900_000, [100.0] * 4, funding=[[T0, 0.0001]])]
    det = pd.correct_once(conn, d, coins, T0 + 3 * H8)
    expected_slip = -2 * pd.SLIP * sig_px * qty                                            # −$0.40
    assert abs(det["short_slippage_usd"] - round(expected_slip, 2)) < 1e-9 and det["short_trades"] == 1
    assert det["funding_settlements"] == 2 and abs(det["funding_usd"]) < 1e-9             # short +0.10, long −0.10 at T0
    assert pd.correct_once(conn, d, coins, T0 + 4 * H8) is None                            # once
    res = d.step(coins, True, T0 + 4 * H8)
    assert res["adjustments"] and res["adjustments"][0]["id"] == pd.CORR_ID
    closed_sum = sum(t["net_usd"] for t in res["closed"])
    assert abs(res["equity"] - (10_000 + closed_sum + expected_slip)) < 0.02


def test_own_desk_funding_and_correction():
    conn = sqlite3.connect(":memory:")
    od.ensure(conn)
    conn.execute("INSERT INTO own_trades(id, symbol, rule, side, opened, opened_day, entry, stop, qty, best) VALUES(?,?,?,?,?,?,?,?,?,?)",
                 ("OWN-P-SOL-1", "SOL", "P", "LONG", T0 - 5, T0 - 5, 100.0, 90.0, 10.0, 100.0))
    conn.execute("INSERT INTO own_trades(id, symbol, rule, side, opened, opened_day, entry, stop, qty, best, closed, exit, exit_kind, pnl_usd) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("OWN-P-ADA-1", "ADA", "P", "LONG", T0 - 5, T0 - 5, 1.0, 0.9, 1000.0, 1.0, T0 + 60_000, 1.1, "правило", 98.6))
    conn.commit()
    hist = lambda sym, a, b: [[T0, 0.0002]]
    det = od.correct_once(conn, T0 + H8, hist)
    assert det["closed_trades"] == 1 and det["funding_settlements"] == 1 and abs(det["funding_usd"] + 0.2) < 1e-9   # $1 000 × 0.02 %
    assert od.correct_once(conn, T0 + H8, hist) is None
    assert abs(od.equity_closed(conn) - (10_000 + 98.6 - 0.2)) < 1e-9
    od.accrue_funding(conn, T0 + 2 * 3_600_000, {"SOL": 100.0}, hist)                    # the open SOL long pays $1 000 × 0.02 %
    fund = conn.execute("SELECT fund_usd FROM own_trades WHERE id='OWN-P-SOL-1'").fetchone()[0]
    assert abs(fund + 0.2) < 1e-9
    t = od._open_rows(conn)[0]
    pnl = od._close(conn, t, 100.0, "правило", T0 + 3 * 3_600_000)
    assert abs(pnl - (-(100.0 + 100.0) * 10.0 * od.FEE - 0.2)) < 1e-9                     # fees + the funding paid


def test_store_reads_settled_funding():
    from cnq.store.db import Store
    s = Store(":memory:")
    s.upsert_funding("SOL-USDT", [{"time": T0, "rate": 0.0001}, {"time": T0 + H8, "rate": -0.0002}])
    assert s.funding("SOL-USDT", T0 + 1) == [[T0 + H8, -0.0002]] and len(s.funding("SOL-USDT")) == 2
