"""«Сигналы CRYPTO NOVA» desk: regime, pullback and breakout signals on closed candles, entry at the new day's open, stop every
run, rule exit after the daily close, risk-based size — no network (candles and prices injected)."""
import json
import os
import sqlite3
import tempfile

from cnq.agents import own_desk as od

DAY = od.DAY
D0 = 1_790_000_000_000 // DAY * DAY


def _series(closes, start=D0, last_vol=1000.0):
    """Daily rows with base volume 1 000 a day, the last closed day `last_vol` (a volume spike when > 1 500)."""
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append([start + i * DAY, prev, max(prev, c) * 1.01, min(prev, c) * 0.99, c, last_vol if i == len(closes) - 1 else 1000.0])
        prev = c
    return out


def test_regime_and_signals_on_closed_candles():
    up = [100 * 1.004 ** i for i in range(260)]
    assert od.regime(_series(up)) is True
    assert od.regime(_series(list(reversed(up)))) is False
    # breakout: a steady rise, then a close above the 20-day high
    k = _series(up[:200] + [up[199] * 1.06])
    assert ("C", 1) in od.signals(k, True) and not od.signals(k, False)
    # pullback: uptrend, then two down days → RSI(2) low, still above the rising 50-day average
    pb = up[:200] + [up[199] * 0.985, up[199] * 0.97]
    assert ("P", 1) in od.signals(_series(pb), True)


def test_desk_opens_at_the_open_stops_and_exits_by_rule():
    conn = sqlite3.connect(":memory:")
    up = [100 * 1.004 ** i for i in range(260)]
    btc = up[:200]
    coin = up[:199] + [up[198] * 1.06]                       # day 199 closes with a breakout
    day = D0 + 200 * DAY                                      # the new day: forming candle at index 200

    def daily_for(n_closed, coin_closes, btc_closes):
        def f(sym):
            src = btc_closes if sym == "BTC" else coin_closes if sym == "ETH" else None
            if src is None:
                raise RuntimeError("no data")
            rows = _series(src[:n_closed], last_vol=3000.0)
            last = rows[-1][4]
            rows.append([D0 + n_closed * DAY, last, last, last, last, 10.0])  # today's forming candle, open = last close
            return rows
        return f
    od.run(conn, out=tempfile.mkdtemp(), now=day + 10 * 60_000, daily=daily_for(200, coin, btc), prices=lambda: {})
    rows = conn.execute("SELECT symbol, rule, side, entry, stop, qty FROM own_trades").fetchall()
    assert [(r[0], r[1], r[2]) for r in rows] == [("ETH", "C", "LONG")]
    sym, rule, side, entry, stop, qty = rows[0]
    assert abs(entry - coin[199]) < 1e-9 and 0.03 * entry - 1e-9 <= entry - stop <= 0.15 * entry + 1e-9
    assert abs(qty * (entry - stop) - od.START * od.RISK) < 1e-6 or qty * entry <= od.START * od.NOTIONAL_CAP + 1e-6
    # the same day again: nothing new
    od.run(conn, out=tempfile.mkdtemp(), now=day + 40 * 60_000, daily=daily_for(200, coin, btc), prices=lambda: {})
    assert conn.execute("SELECT COUNT(*) FROM own_trades").fetchone()[0] == 1
    # a price through the stop closes it at once (a gap fills worse)
    d = od.run(conn, out=tempfile.mkdtemp(), now=day + 70 * 60_000, daily=daily_for(200, coin, btc), prices=lambda: {"ETH": stop * 0.98})
    r = conn.execute("SELECT exit_kind, exit, pnl_usd FROM own_trades").fetchone()
    assert r[0] == "стоп" and abs(r[1] - stop * 0.98) < 1e-9 and r[2] < 0
    assert d["stats"]["trades"] == 1 and d["equity"] < od.START


def test_paper_json_gets_the_profile():
    tmp = tempfile.mkdtemp()
    json.dump({"profiles": {}, "profile_list": []}, open(os.path.join(tmp, "paper.json"), "w"))
    conn = sqlite3.connect(":memory:")
    od.run(conn, out=tmp, now=D0 + 3 * 60_000, daily=lambda s: (_ for _ in ()).throw(RuntimeError("x")), prices=lambda: {})
    p = json.load(open(os.path.join(tmp, "paper.json")))
    assert p["profiles"]["own"]["name"] == od.NAME and any(x["key"] == "own" for x in p["profile_list"])


def test_volume_mark_longs_only_and_the_higher_volume_first(monkeypatch):
    conn = sqlite3.connect(":memory:")
    up = [100 * 1.004 ** i for i in range(260)]
    brk = up[:199] + [up[198] * 1.06]
    coins = od.COINS[1:7]                                     # six coins break out on the same day
    vols = dict(zip(coins, (1200.0, 1400.0, 1600.0, 2500.0, 4000.0, 9000.0)))   # the volume of the breakout day vs 1 000 a day

    def daily(sym):
        src = up[:200] if sym == "BTC" else brk if sym in coins else None
        if src is None:
            raise RuntimeError("no data")
        rows = _series(src, last_vol=vols.get(sym, 1000.0))
        rows.append([D0 + 200 * DAY, rows[-1][4], rows[-1][4], rows[-1][4], rows[-1][4], 10.0])
        return rows
    monkeypatch.setattr(od, "MAX_OPEN", 3)
    od.run(conn, out=tempfile.mkdtemp(), now=D0 + 200 * DAY + 10 * 60_000, daily=daily, prices=lambda: {})
    rows = conn.execute("SELECT symbol, side, vol FROM own_trades ORDER BY vol DESC").fetchall()
    # 1 200 and 1 400 are below 1.5× the average → no trade; four pass, the room is 3 → the three biggest volumes
    assert [r[0] for r in rows] == [coins[5], coins[4], coins[3]] and all(sd == "LONG" for _, sd, _v in rows)
    # a falling market: the short signals of the rules are not traded
    conn2 = sqlite3.connect(":memory:")
    down = list(reversed(up))

    def daily2(sym):
        src = down[:200] if sym == "BTC" else (down[:199] + [down[198] * 0.94]) if sym in coins else None
        if src is None:
            raise RuntimeError("no data")
        rows = _series(src, last_vol=5000.0)
        rows.append([D0 + 200 * DAY, rows[-1][4], rows[-1][4], rows[-1][4], rows[-1][4], 10.0])
        return rows
    od.run(conn2, out=tempfile.mkdtemp(), now=D0 + 200 * DAY + 10 * 60_000, daily=daily2, prices=lambda: {})
    assert conn2.execute("SELECT COUNT(*) FROM own_trades").fetchone()[0] == 0


def test_vol_surge_is_the_last_days_dollar_volume_over_its_30_day_average():
    rows = [[0, 1, 1, 1, 10.0, 100.0]] * 29 + [[0, 1, 1, 1, 20.0, 200.0]]        # $1 000 a day, then $4 000
    assert abs(od.vol_surge(rows) - 4000 / ((29 * 1000 + 4000) / 30)) < 1e-9
    assert od.vol_surge([r[:5] for r in rows]) is None                           # no volume data → no trade


def test_the_trading_set_is_the_daily_top_by_dollar_volume(monkeypatch):
    """19.10 rounds 9–10: trade the TOP_N perps by 30-day dollar volume — a big new coin from the BingX volume pool gets in, a quiet
    core coin drops out, synthetic non-crypto pairs never count."""
    conn = sqlite3.connect(":memory:")
    up = [100 * 1.004 ** i for i in range(260)]
    brk = up[:199] + [up[198] * 1.06]
    base_vol = {"NEW": 50_000.0, "ETH": 20_000.0, "SOL": 1_000.0}     # base volume a day; the breakout day ×3

    def daily(sym):
        if sym == "BTC":
            src, v = up[:200], 100.0
        elif sym in base_vol:
            src, v = brk, base_vol[sym]
        else:
            raise RuntimeError("no data")
        rows = [[r[0], r[1], r[2], r[3], r[4], v * (3 if i == len(src) - 1 else 1)] for i, r in enumerate(_series(src))]
        rows.append([D0 + 200 * DAY, rows[-1][4], rows[-1][4], rows[-1][4], rows[-1][4], 1.0])
        return rows
    monkeypatch.setattr(od, "TOP_N", 2)
    od.run(conn, out=tempfile.mkdtemp(), now=D0 + 200 * DAY + 10 * 60_000, daily=daily, prices=lambda: {},
           volumes=lambda: {"NEW": 9e9, "NCSKXYZ2USD": 9e12, "SOL": 1.0})
    syms = sorted(r[0] for r in conn.execute("SELECT symbol FROM own_trades"))
    assert syms == ["ETH", "NEW"]                                      # BTC's dollar volume is below ETH's and NEW's here
    assert od._state(conn, "universe") == ["NEW", "ETH"]


def test_the_desk_picks_the_alt_basket_by_180_day_dollar_volume():
    conn = sqlite3.connect(":memory:")
    flat = [100.0] * 400
    coins = od.COINS[:14]                                    # BTC, ETH + 12 alts with rising volume

    def daily(sym):
        if sym not in coins:
            raise RuntimeError("no data")
        v = 1000.0 * (1 + coins.index(sym))
        rows = [[r[0], r[1], r[2], r[3], r[4], v] for r in _series(flat)]
        rows.append([D0 + 400 * DAY, 100.0, 100.0, 100.0, 100.0, 1.0])
        return rows
    od.run(conn, out=tempfile.mkdtemp(), now=D0 + 400 * DAY + 10 * 60_000, daily=daily, prices=lambda: {})
    assert od._state(conn, "alt_basket") == list(reversed(coins[4:14]))      # the 10 largest alts, BTC and ETH never
