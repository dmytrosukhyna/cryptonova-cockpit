"""«Сигналы CRYPTO NOVA» desk: regime, pullback and breakout signals on closed candles, entry at the new day's open, stop every
run, rule exit after the daily close, risk-based size — no network (candles and prices injected)."""
import json
import os
import sqlite3
import tempfile

from cnq.agents import own_desk as od

DAY = od.DAY
D0 = 1_790_000_000_000 // DAY * DAY


def _series(closes, start=D0):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append([start + i * DAY, prev, max(prev, c) * 1.01, min(prev, c) * 0.99, c])
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
            rows = _series(src[:n_closed])
            last = rows[-1][4]
            rows.append([D0 + n_closed * DAY, last, last, last, last])        # today's forming candle, open = last close
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
