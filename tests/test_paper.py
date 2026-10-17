"""Paper desk on synthetic candles: limit fill at a zone, ladder exits, stop-first, equity accounting."""
import sqlite3

from cnq.agents import paper_desk as pd


def mk(t0, prices, step=900_000):
    out = []
    for i, p in enumerate(prices):
        out.append([t0 + i * step, p, p * 1.004, p * 0.996, p, 100.0])
    return out


def coin(symbol, prices, levels, t0=1_700_000_000_000):
    return {"symbol": symbol, "candles": mk(t0, prices), "levels": levels, "regime": {"long": "BULL"}}


def test_limit_fill_targets_and_stop():
    conn = sqlite3.connect(":memory:")
    desk = pd.PaperDesk(conn, classes={"A", "B", "R", "C"})   # class C is observation-only on the real desks; enable it here to test limit fills
    t0 = 1_700_000_000_000
    flat = [100.0] * 300
    c = coin("BTC-USDT", flat, [{"id": "L1", "kind": "support", "lo": 99.6, "hi": 99.9, "center": 99.75, "touches": 3, "status": "ACTIVE"}])
    now = t0 + 300 * 900_000
    res = desk.step([c], True, now)
    assert res["pending"] and res["pending"][0]["side"] == "LONG" and abs(res["pending"][0]["entry"] - 99.9) < 1e-9
    # price dips into the zone, then rallies through all three targets
    entry, stop = res["pending"][0]["entry"], res["pending"][0]["stop"]
    r = entry - stop
    path = flat + [99.85] + [entry + r * 1.2, entry + r * 2.2, entry + r * 3.2]
    c2 = coin("BTC-USDT", path, c["levels"])
    now2 = t0 + len(path) * 900_000 + 1
    res2 = desk.step([c2], True, now2)
    assert res2["stats"]["trades"] == 1 and res2["closed"][0]["exit_kind"] == "TARGET"
    assert res2["closed"][0]["pnl_r"] > 1.2 and res2["equity"] > 10_000
    # new signal, stopped out: equity goes down by ~1 % of equity
    eq_before = res2["equity"]
    path3 = path + [100.0] * 3 + [99.95]            # price back at the zone edge → new limit order
    res3 = desk.step([coin("BTC-USDT", path3, c["levels"])], True, t0 + len(path3) * 900_000 + 1)
    assert res3["pending"], "second limit order expected at the zone"
    path4 = path3 + [99.85, stop * 0.99]             # fills, then crashes through the stop
    res3 = desk.step([coin("BTC-USDT", path4, c["levels"])], True, t0 + len(path4) * 900_000 + 1)
    assert res3["stats"]["trades"] == 2 and res3["closed"][0]["exit_kind"] == "STOP"
    assert -1.2 < res3["closed"][0]["pnl_r"] < -0.9 and res3["equity"] < eq_before
    assert res3["stats"]["winrate"] == 0.5 and res3["equity_history"]


def test_alt_longs_blocked_in_bear_and_cascade_market_entry():
    conn = sqlite3.connect(":memory:")
    desk = pd.PaperDesk(conn)
    t0 = 1_700_000_000_000
    flat = [10.0] * 300
    c = coin("SOL-USDT", flat, [{"id": "L1", "kind": "support", "lo": 9.96, "hi": 9.99, "center": 9.975, "touches": 2, "status": "ACTIVE"}])
    assert not desk.step([c], False, t0 + 300 * 900_000)["pending"]
    # cascade: a −6 % candle with a long lower wick → market long
    prices = [10.0] * 300 + [10.0]
    cs = mk(t0, prices)
    cs[-2] = [cs[-2][0], 10.0, 10.0, 9.2, 9.6, 500.0]   # open 10, low 9.2, close 9.6 → lower wick 50 % of range
    cs[-1] = [cs[-1][0], 9.6, 9.65, 9.55, 9.6, 100.0]
    c2 = {"symbol": "SOL-USDT", "candles": cs, "levels": [], "regime": {"long": "BULL"}}
    res = desk.step([c2], True, cs[-1][0] + 1)
    # the same candle is also a class-E climax (broad sell-off + 5× volume); E wins on this desk, B on a desk without E
    assert res["open"] and res["open"][0]["grade"] == "E" and res["open"][0]["side"] == "LONG"
    assert abs(res["open"][0]["stop"] / res["open"][0]["entry"] - (1 - pd.E_STOP)) < 1e-3   # entry carries slippage
    desk_b = pd.PaperDesk(sqlite3.connect(":memory:"), classes={"A", "B", "R"})
    res_b = desk_b.step([c2], True, cs[-1][0] + 1)
    assert res_b["open"] and res_b["open"][0]["grade"] == "B" and res_b["open"][0]["side"] == "LONG"
    res = res_b


def test_retest_scan_and_signal():
    """Synthetic 4h series: flat 30 bars, breakout bar on volume, then a retest bar that closes back above the level."""
    from cnq.agents.paper_desk import PaperDesk, agg4h, retest_scan
    import sqlite3
    H = 14_400_000
    b4 = []
    t0 = 1_700_000_000_000 - (1_700_000_000_000 % H)
    for i in range(60):
        b4.append([t0 + i * H, 100.0, 101.0, 99.0, 100.0, 10.0])       # flat, ATR ≈ 2
    b4.append([t0 + 60 * H, 100.0, 104.0, 99.5, 103.0, 20.0])          # breakout: close 103 > 101 + 0.3·ATR, vol 2×
    b4.append([t0 + 61 * H, 103.0, 103.5, 102.5, 103.0, 10.0])
    rt = retest_scan(b4)
    assert rt and rt["side"] == "LONG" and rt["level"] == 101.0 and rt["i_retest"] is None
    b4.append([t0 + 62 * H, 103.0, 103.2, 101.5, 102.5, 10.0])          # retest: low within 0.5 ATR of 101, close above
    rt = retest_scan(b4)
    assert rt and rt["i_retest"] == 62 and rt["bars_since_retest"] == 0
    # 15m expansion for the desk: each 4h bar → 16 identical 15m candles
    c15 = []
    for b in b4:
        for k in range(16):
            c15.append([b[0] + k * 900_000, b[1], b[2], b[3], b[4], b[5] / 16])
    now = c15[-1][0] + 900_000 + H   # next 4h bar has started, last retest bar is closed
    desk = PaperDesk(sqlite3.connect(":memory:"))
    sigs = desk.signals({"symbol": "ETH-USDT", "candles": c15, "levels": []}, True, now)
    r = [x for x in sigs if x["grade"] == "R"]
    assert len(r) == 1 and r[0]["side"] == "LONG" and abs(r[0]["stop"] - (101.0 - rt["atr"])) < 1e-9 and r[0]["order"] == "MARKET"
    assert agg4h(c15)[-1][0] == b4[-1][0]


def test_three_profiles_share_signals_but_differ():
    """Same market: class C (zone limit) is observation-only — none of the three desks trades it; run() returns all three."""
    conn = sqlite3.connect(":memory:")
    t0 = 1_700_000_000_000
    flat = [10.0] * 300
    c = coin("BTC-USDT", flat, [{"id": "L1", "kind": "support", "lo": 9.96, "hi": 9.99, "center": 9.975, "touches": 3, "status": "ACTIVE"}])
    c["regime"] = {"long": "BULL"}
    out = pd.run(conn, {"coins": [c]}, now=t0 + 300 * 900_000)
    assert set(out["profiles"]) == {"cons", "bal", "aggr", "exp", "lab"} and out["profile"] == "bal"
    assert not any(out["profiles"][k]["pending"] for k in ("cons", "bal", "aggr"))
    lab = pd.PaperDesk(conn, "aggr", classes={"A", "B", "R", "C"}).step([c], True, t0 + 300 * 900_000)
    assert lab["pending"]   # the same zone would be traded only if class C were enabled
    assert out["profiles"]["cons"]["rules"].startswith("риск 0.5%")
    # tables are separate per profile
    names = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert {"paper_orders", "paper_bal_orders", "paper_cons_orders"} <= names


def test_cli_candles_long_conversion_from_store_objects():
    """The store returns Candle objects; the desk's 4h scan needs plain rows (regression: server step crashed silently)."""
    from cnq.bingx.rest import Candle
    from cnq.agents.paper_desk import agg4h
    t0 = 1_700_000_000_000 - (1_700_000_000_000 % 14_400_000)
    cs = [Candle(open_time=t0 + i * 900_000, open=1.0, high=1.1, low=0.9, close=1.0, volume=1.0, close_time=t0 + i * 900_000 + 899_999, quote_volume=1.0, trades=1, taker_buy_volume=0.5, taker_buy_quote=0.5) for i in range(32)]
    rows = [[k.open_time, k.open, k.high, k.low, k.close, k.volume] for k in cs]
    b4 = agg4h(rows)
    assert len(b4) == 2 and b4[0][5] == 16.0 and b4[0][2] == 1.1


def test_swing_breakout_opens_and_trails_out():
    """Experimental account: close above the 5-day high while above the 20-day mean → long; a close below yesterday's low exits (TRAIL)."""
    conn = sqlite3.connect(":memory:")
    t0 = 1_700_000_000_000
    n = 96 * 21
    prices = [100.0] * (n - 3) + [103.0, 104.0, 104.0]          # breakout on the last closed bars
    c = coin("ETH-USDT", prices, [])
    c["candles_long"] = c["candles"]
    now = t0 + n * 900_000
    desk = pd.PaperDesk(conn, "exp")
    res = desk.step([c], True, now)
    assert len(res["open"]) == 1 and res["open"][0]["grade"] == "S"
    assert abs(res["open"][0]["qty"] * res["open"][0]["entry"] - 1000) < 5      # 10 % of $10 000
    # price drifts up then closes below the previous day's lowest close → trailing exit
    more = prices + [105.0] * 100 + [99.0] * 3
    c2 = coin("ETH-USDT", more, []); c2["candles_long"] = c2["candles"]
    res2 = desk.step([c2], True, t0 + len(more) * 900_000)
    assert not res2["open"] and res2["closed"][0]["exit_kind"] == "TRAIL"


def test_main_desks_ignore_experimental_rules():
    conn = sqlite3.connect(":memory:")
    t0 = 1_700_000_000_000
    n = 96 * 21
    prices = [100.0] * (n - 3) + [103.0, 104.0, 104.0]
    c = coin("ETH-USDT", prices, []); c["candles_long"] = c["candles"]
    res = pd.PaperDesk(conn, "bal").step([c], True, t0 + n * 900_000)
    assert not res["open"]


def test_panic_breadth_threshold_is_04():
    assert pd.E_BREADTH == 0.4 and pd.E_BASKET_H == 10


def test_extra_alts_trade_only_lab_rules_and_skip_breadth():
    """17.10: extra alt coins (b_only) feed only the lab (X panic basket, Y basis, L range, W news); the main desks ignore them."""
    conn = sqlite3.connect(":memory:")
    t0 = 1_700_000_000_000
    n = 96 * 31
    c = coin("ATOM-USDT", [100.0] * n, []); c["b_only"] = True
    c["candles"][-2] = [t0 + (n - 2) * 900_000, 100.0, 100.0, 90.0, 97.0, 1000.0]   # −3 % candle on 10× volume
    assert pd.PaperDesk(conn, "bal").stress([c]) == 0.0
    now = t0 + n * 900_000
    assert not pd.PaperDesk(conn, "bal").signals(c, True, now)
    lab = pd.PaperDesk(conn, "lab"); lab._stress = 0.5
    sigs = lab.signals(c, True, now)
    assert any(s["grade"] == "X" for s in sigs) and all(s["grade"] in ("L", "W", "X", "Y") for s in sigs)
    c["basis_1h"] = -0.004
    assert any(s["grade"] == "Y" for s in lab.signals(c, True, now))


def test_rule_d_basis_discount_opens_share_sized_long():
    from cnq.agents import basis_watch as bw
    conn = sqlite3.connect(":memory:")
    t0 = 1_700_000_000_000
    n = 300
    c = coin("SOL-USDT", [100.0] * n, [])
    now = t0 + n * 900_000
    fake = (("bingx", lambda bases: {"SOL": -0.0030}),)
    bw.record(conn, ["SOL"], now - 40 * 60_000, fetchers=fake)
    assert bw.avg_1h(conn, "SOL", now - 40 * 60_000) is None            # one sample is not enough
    bw.record(conn, ["SOL"], now, fetchers=fake)
    c["basis_1h"] = bw.avg_1h(conn, "SOL", now)
    assert abs(c["basis_1h"] + 0.003) < 1e-12
    res = pd.PaperDesk(conn, "bal").step([c], True, now)
    d = [t for t in res["open"] if t["grade"] == "D"]
    assert d and abs(d[0]["qty"] * d[0]["entry"] - 1500) < 5             # 15 % of $10 000
    c2 = coin("ETH-USDT", [100.0] * n, []); c2["basis_1h"] = -0.0010     # −0.10 % is not deep enough
    assert not [s for s in pd.PaperDesk(conn, "cons").signals(c2, True, now) if s["grade"] == "D"]


def test_lab_range_low_limit_fills_only_through_level():
    conn = sqlite3.connect(":memory:")
    t0 = 1_700_000_000_000 - (1_700_000_000_000 % 3_600_000)
    n = 96 * 15
    # 14+ days oscillating 90..110, now at 100 → range low ≈ 89.6, L entry ≈ 0.3 into the range
    prices = [100 + 10 * __import__("math").sin(i / 40) for i in range(n)]
    prices[-100:] = [99.0] * 100                                # calm last day (no broad sell-off → longs allowed)
    c = coin("ETH-USDT", prices, [], t0); c["candles_long"] = c["candles"]
    now = t0 + n * 900_000
    desk = pd.PaperDesk(conn, "lab")
    res = desk.step([c], True, now)
    lab = [o for o in res["pending"] if o["grade"] in ("L", "N", "Q")]
    assert lab, "lab places limit orders inside a range"
    o = next((x for x in lab if x["grade"] == "L"), lab[0])
    assert o["expires"] - now <= 3_600_000                       # valid ~1 h
    # next 15m candle only touches the level (no fill), the one after trades through it (fill)
    touch = [now, o["entry"] * 1.002, o["entry"] * 1.003, o["entry"] * 1.00005 if o["side"] == "LONG" else o["entry"] * 0.9999, o["entry"] * 1.002, 100.0]
    rows = c["candles"] + [touch]
    c2 = dict(c, candles=rows, candles_long=rows)
    res2 = desk.step([c2], True, now + 900_000)
    assert not [t for t in res2["open"] if t["id"] == o["id"]] or o["side"] == "SHORT"


def test_lab_news_rule_trades_strong_fresh_news_once():
    conn = sqlite3.connect(":memory:")
    from cnq.agents.news import SCHEMA, _migrate
    conn.executescript(SCHEMA); _migrate(conn)
    t0 = 1_700_000_000_000
    n = 300
    now = t0 + n * 900_000
    conn.execute("INSERT INTO news_impact (news_id,asset,category,direction,horizon,magnitude,confidence,reasoning,confirm,created,price_at) "
                 "VALUES ('n1','MARKET','macro','bear','hours',5,0.8,'','',?,100)", (now - 10 * 60_000,))
    conn.execute("INSERT INTO news_impact (news_id,asset,category,direction,horizon,magnitude,confidence,reasoning,confirm,created,price_at) "
                 "VALUES ('n2','ETH','etf','bull','days',3,0.9,'','',?,100)", (now - 10 * 60_000,))      # too weak
    c = coin("BTC-USDT", [100.0] * n, []); e = coin("ETH-USDT", [100.0] * n, [])
    desk = pd.PaperDesk(conn, "lab")
    w = [s for s in desk.signals(c, True, now) + desk.signals(e, True, now) if s["grade"] == "W"]
    assert len(w) == 1 and w[0]["symbol"] == "BTC" and w[0]["side"] == "SHORT" and w[0]["hold_h"] == 6


def test_lab_nikkei_reversal_after_tokyo_close():
    import datetime as _dt
    conn = sqlite3.connect(":memory:")
    now = int(_dt.datetime(2026, 10, 19, 7, 0, tzinfo=_dt.timezone.utc).timestamp() * 1000)
    n = 300
    t0 = now - n * 900_000
    c = coin("BTC-USDT", [80000.0] * n, [], t0)
    desk = pd.PaperDesk(conn, "lab"); desk._nikkei = {"chg": -2.1, "date": "2026-10-19"}
    j = [s for s in desk.signals(c, True, now) if s["grade"] == "J"]
    assert len(j) == 1 and j[0]["side"] == "LONG" and j[0]["hold_h"] == 24
    desk._nikkei = {"chg": -2.1, "date": "2026-10-18"}                      # stale close → nothing
    assert not [s for s in desk.signals(c, True, now) if s["grade"] == "J"]
    desk._nikkei = {"chg": 0.9, "date": "2026-10-19"}                       # small move → nothing
    assert not [s for s in desk.signals(c, True, now) if s["grade"] == "J"]


# ── one-time reset of the three autopilot desks (owner, 17.10.2026) ──────────────────────────────────────────────────────
def _put_trade(conn, tbl, tid, sym, side, grade, entry, qty, pnl, opened, closed, exit_kind="STOP"):
    stop = entry * (1.05 if side == "SHORT" else 0.95)
    conn.execute(f"INSERT INTO {tbl} VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (tid, sym, side, grade, "TEST", entry, stop, stop, "[1e9,1e9,1e9]", "[1.0,0.0,0.0]", qty, 0.0 if closed else qty, 100.0,
                  opened, opened + 48 * 3_600_000, closed, exit_kind if closed else None, pnl, pnl / 100.0, 0, "test", opened))


def _reset_world():
    """cons: curve only; bal: 3 stopped retest shorts; aggr: retest short + 2 level trades + an OPEN rule-D long + a pending limit;
    lab: one trade (must stay). SOL has a −0.30 % futures discount at `now` → rule D fires for the same id again after the reset."""
    from cnq.agents import basis_watch as bw
    conn = sqlite3.connect(":memory:")
    t0, n = 1_700_000_000_000, 300
    now = t0 + n * 900_000
    c = coin("SOL-USDT", [100.0] * n, [], t0)
    fake = (("bingx", lambda bases: {"SOL": -0.0030}),)
    bw.record(conn, ["SOL"], now - 40 * 60_000, fetchers=fake); bw.record(conn, ["SOL"], now, fetchers=fake)
    D = {k: pd.PaperDesk(conn, k) for k in ("cons", "bal", "aggr", "lab")}
    for k in ("cons", "bal", "aggr", "lab"):
        conn.execute(f"INSERT INTO {D[k].T_EQ}(time, equity) VALUES(?,?)", (t0, 10_000.0))
    for i, (sym, pnl) in enumerate((("ETH", -98.2), ("ADA", -98.5), ("AVAX", -98.7))):
        _put_trade(conn, D["bal"].T_TR, f"R-{sym}-1", sym, "SHORT", "R", 100.0, 10.0, pnl, t0 + i, t0 + 3_600_000 + i)
    _put_trade(conn, D["aggr"].T_TR, "R-ADA-1", "ADA", "SHORT", "R", 100.0, 10.0, -144.8, t0, t0 + 3_600_000)
    _put_trade(conn, D["aggr"].T_TR, "C-ETH-1", "ETH", "LONG", "C", 100.0, 10.0, -96.3, t0 + 1, t0 + 7_200_000)
    _put_trade(conn, D["aggr"].T_TR, "C-DOGE-1", "DOGE", "SHORT", "C", 100.0, 10.0, 92.6, t0 + 2, t0 + 7_200_000, "BREAKEVEN")
    did = f"D-SOL-{now // 3_600_000}"
    _put_trade(conn, D["aggr"].T_TR, did, "SOL", "LONG", "D", 110.0, 10.0, 0.0, now - 600_000, None)
    conn.execute(f"INSERT INTO {D['aggr'].T_ORD} VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("C-ETH-2", "ETH", "LONG", "C", "LEVEL_LIMIT", 99.0, 98.0, "[100,101,102]", "[0.4,0.3,0.3]", 1.0, 1.0, now - 600_000, now + 3_600_000, "PENDING", "t"))
    _put_trade(conn, D["lab"].T_TR, "N-SUI-1", "SUI", "LONG", "N", 1.0, 100.0, -46.1, t0, t0 + 3_600_000)
    conn.commit()
    return conn, c, now, did


def test_reset_desks_runs_once_archives_and_restarts_clean():
    import json
    conn, c, now, did = _reset_world()
    net = lambda pnls: round(sum(p - 100.0 * 10.0 * pd.FEE for p in pnls), 2)
    out = pd.run(conn, {"coins": [c], "_no_network": True}, now=now)
    P = out["profiles"]
    for k in ("cons", "bal", "aggr"):
        assert P[k]["equity"] == 10_000.0 and P[k]["closed"] == [] and P[k]["stats"]["trades"] == 0, k
        assert P[k]["equity_history"][0]["time"] == now and P[k]["since"] == now and P[k]["reset"]["time"] == now, k
        assert P[k]["events"][0] == P[k]["reset"]["note_ru"], k
    rb = P["bal"]["reset"]
    assert rb["archived_pnl_usd"] == net([-98.2, -98.5, -98.7]) and rb["archived_trades"] == 3
    assert "прошлые 3 сделки (3 шорта по правилу «ретест уровня», выключено 16.10) — в архиве, итог −$" in rb["note_ru"]
    assert rb["note_ru"].endswith(pd._usd_ru(rb["archived_pnl_usd"])) and rb["note_ru"].startswith("Счёт обнулён 17.10 по решению владельца")
    assert P["cons"]["reset"]["archived_pnl_usd"] == 0.0 and "сделок не было, итог $0" in P["cons"]["reset"]["note_ru"]
    assert out["reset"] == rb and out["since"] == now                                   # top level = the default «норма» desk
    # aggr: the open D long was closed at the last price (100 < entry 110) into the archive, the pending limit cancelled
    ra = P["aggr"]["reset"]
    assert ra["archived_trades"] == 4 and "1 шорт по правилу «ретест уровня», выключено 16.10" in ra["note_ru"]
    assert "1 лонг и 1 шорт по правилу «наблюдение у уровня», сейчас не торгуется" in ra["note_ru"] and "1 лонг по правилу «откуп дисконта фьючерса»" in ra["note_ru"]
    arch = json.loads(conn.execute("SELECT json FROM paper_archive WHERE id=?", (f"{pd.RESET_ID}:aggr",)).fetchone()[0])
    d_tr = next(t for t in arch["trades"] if t["id"] == did)
    assert d_tr["exit_kind"] == "RESET" and d_tr["closed"] == now and d_tr["net_usd"] < -100
    assert arch["closed_at_reset"][0]["id"] == did and arch["closed_at_reset"][0]["price"] == 100.0 and arch["cancelled_orders"] == ["C-ETH-2"]
    assert ra["archived_pnl_usd"] == round(sum(t["net_usd"] for t in arch["trades"]), 2) == round(-144.8 - 96.3 + 92.6 + d_tr["net_usd"] - 3 * 100 * 10 * pd.FEE, 2)
    assert len(arch["equity_history"]) >= 1 and arch["stats"]["trades"] == 4
    # the same D signal fires again at `now`: fresh desks take it, aggr does not re-open an archived id
    assert [t["id"] for t in P["aggr"]["open"]] == [] and P["aggr"]["pending"] == []
    assert [t["id"] for t in P["bal"]["open"]] == [did] and [t["id"] for t in P["cons"]["open"]] == [did]
    # lab is not touched
    assert P["lab"]["reset"] is None and P["lab"]["stats"]["trades"] == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_archive").fetchone()[0] == 3
    assert conn.execute("SELECT id FROM migrations").fetchall() == [(pd.RESET_ID,)]
    # second run: nothing archived again, the note stays in `reset` but is not repeated as an event
    out2 = pd.run(conn, {"coins": [c], "_no_network": True}, now=now + 1_800_000)
    assert conn.execute("SELECT COUNT(*) FROM paper_archive").fetchone()[0] == 3 and pd.reset_desks(conn, now + 3_600_000) == {}
    for k in ("cons", "bal", "aggr"):
        q = out2["profiles"][k]
        assert q["reset"]["time"] == now and q["equity_history"][0]["time"] == now and not any("обнулён" in e for e in q["events"]), k
    assert out2["profiles"]["bal"]["reset"]["archived_pnl_usd"] == rb["archived_pnl_usd"]


def test_reset_desks_fresh_db_and_late_run_do_not_wipe():
    conn = sqlite3.connect(":memory:")
    now = 1_792_270_000_000
    assert pd.reset_desks(conn, now) == {}                                      # nothing to reset → only marked as applied
    d = pd.PaperDesk(conn, "bal")
    _put_trade(conn, d.T_TR, "B-SOL-1", "SOL", "LONG", "B", 100.0, 1.0, 50.0, now, now + 1)
    conn.commit()
    assert pd.reset_desks(conn, now + 60_000) == {} and conn.execute(f"SELECT COUNT(*) FROM {d.T_TR}").fetchone()[0] == 1
    conn2 = sqlite3.connect(":memory:")
    d2 = pd.PaperDesk(conn2, "bal")
    _put_trade(conn2, d2.T_TR, "B-SOL-1", "SOL", "LONG", "B", 100.0, 1.0, 50.0, now, now + 1)
    conn2.commit()
    assert pd.reset_desks(conn2, pd.RESET_UNTIL + 1) == {}                      # a database that meets the migration too late is kept
    assert conn2.execute(f"SELECT COUNT(*) FROM {d2.T_TR}").fetchone()[0] == 1
    assert conn2.execute("SELECT note FROM migrations").fetchone()[0].startswith("skipped")
    assert pd._plural(1, "a", "b", "c") == "a" and pd._plural(3, "a", "b", "c") == "b" and pd._plural(6, "a", "b", "c") == "c" and pd._plural(12, "a", "b", "c") == "c"
    assert pd._usd_ru(-298.84) == "−$299" and pd._usd_ru(0.0) == "$0" and pd._usd_ru(1234.4) == "+$1 234"


def test_lab_is_called_test_of_new_ideas_for_people():
    assert pd.PROFILES["lab"]["name"] == "Тест новых идей" and pd.PROFILES["lab"]["prefix"] == "paper_lab_"
    t0, n = 1_700_000_000_000, 400
    c = coin("SOL-USDT", [100.0] * n, [], t0)
    c["_alt"] = True; c["basis_1h"] = -0.004
    y = [s for s in pd.PaperDesk(sqlite3.connect(":memory:"), "lab").signals(c, True, t0 + n * 900_000) if s["grade"] == "Y"]
    assert y and y[0]["reason"].startswith("Тест новых идей: ")
    # reasons already stored with the old name are renamed once by run()
    conn = sqlite3.connect(":memory:")
    lab = pd.PaperDesk(conn, "lab")
    _put_trade(conn, lab.T_TR, "N-SUI-1", "SUI", "LONG", "N", 1.0, 100.0, 0.0, t0, None)
    conn.execute(f"UPDATE {lab.T_TR} SET reason='Лаборатория: часовая поддержка 1.0'")
    conn.commit()
    out = pd.run(conn, {"coins": [c], "_no_network": True}, now=t0 + n * 900_000)
    assert conn.execute(f"SELECT reason FROM {lab.T_TR} WHERE id='N-SUI-1'").fetchone()[0] == "Тест новых идей: часовая поддержка 1.0"
    assert out["profiles"]["lab"]["name"] == "Тест новых идей" and "lab" in {p["key"] for p in out["profile_list"]}
