"""11.11 (owner 11:30, PAPER ONLY): the test desk «Разворот» — the opposite side after one of OUR copies closes clearly in profit."""
import sqlite3

from cnq.agents import reversal_desk as R
from cnq.agents import whale_study as W
from cnq.execution import okx_futures as fu

H, DAY = R.H, R.DAY
T0 = 1_800_000_000_000 // H * H


def _closed(coin, side, entry, exit_, t, whale="bn:1"):
    return {"whale": whale, "coin": coin, "side": side, "entry": entry, "exit": exit_, "closed_t": t, "pnl": 1.0}


def test_now_variant_opens_the_opposite_side_and_exits_by_stop_or_time():
    v = {"entry": "now", "min_profit": 0.05, "hold_days": 3, "stop": 0.08}
    px = {"ZEC": 900.0, "ETH": 2000.0}
    kw = dict(px_of=lambda c: px.get(c), candles=lambda c, n: {}, fund=lambda c, a, b: 0.0)
    d = R.step(None, [_closed("ZEC", "SHORT", 1000.0, 900.0, T0 - DAY)], T0, v, **kw)       # closed BEFORE the desk started → ignored
    assert d["open"] == [] and d["cands"] == []
    closed = [_closed("ZEC", "SHORT", 1000.0, 900.0, T0 + H), _closed("ETH", "LONG", 2000.0, 2040.0, T0 + H)]   # ETH only +2 %
    d = R.step(d, closed, T0 + H + 600_000, v, **kw)
    assert [(o["coin"], o["side"]) for o in d["open"]] == [("ZEC", "LONG")] and d["open"][0]["notional"] == 1000.0
    d = R.step(d, closed, T0 + 2 * H, v, **kw)                                            # the same closed trade is not taken twice
    assert len(d["open"]) == 1
    px["ZEC"] = 820.0                                                                    # −8.9 % → the stop
    d = R.step(d, closed, T0 + 3 * H, v, **kw)
    assert d["open"] == [] and d["closed"][0]["why"].startswith("стоп") and d["closed"][0]["pnl"] < 0 and d["equity"] < 10000
    late = [_closed("SOL", "LONG", 100.0, 110.0, T0 + 4 * H)]
    px["SOL"] = 110.0
    d = R.step(d, late, T0 + 7 * H, v, **kw)                                             # seen 3 h after the close → too late
    assert d["open"] == [] and any("поздно" in e["why"] for e in d["events"])


def test_candle_variant_waits_for_a_4h_reversal_bar_and_time_exit():
    v = {"entry": "candle", "min_profit": 0.05, "hold_days": 3, "stop": 0.16}
    px, K = {"ZEC": 900.0}, {}
    kw = dict(px_of=lambda c: px.get(c), candles=lambda c, n: K, fund=lambda c, a, b: 0.001)
    d = R.step(None, [], T0, v, **kw)
    closed = [_closed("ZEC", "SHORT", 1000.0, 900.0, T0 + 600_000)]
    d = R.step(d, closed, T0 + H, v, **kw)
    assert d["open"] == [] and len(d["cands"]) == 1                                       # waiting for the candle
    for i in range(4):                                                                   # the first 4-hour block: +2 %
        K[T0 + H + i * H] = (900.0 + i * 4.5, 920.0, 899.0, 904.5 + i * 4.5)
    px["ZEC"] = 918.0
    d = R.step(d, closed, T0 + 5 * H + 600_000, v, **kw)
    assert [(o["coin"], o["side"], o["entry"]) for o in d["open"]] == [("ZEC", "LONG", 918.0)] and d["cands"] == []
    px["ZEC"] = 950.0
    d = R.step(d, closed, T0 + 5 * H + 3 * DAY + 700_000, v, **kw)
    c = d["closed"][0]
    assert "по времени" in c["why"] and abs(c["pnl_pct"] - ((950 / 918 - 1) - 0.001 - 0.001) * 100) < 0.01


def test_the_desk_is_a_labelled_test_and_can_never_reach_the_real_account(tmp_path):
    import json
    (tmp_path / "whales_live.json").write_text(json.dumps({"asof": T0, "desk": {"open": [], "closed": []}}))
    conn = sqlite3.connect(":memory:"); W.ensure_schema(conn)
    doc = R.run(conn, str(tmp_path), now=T0, kv_get=W.kv_get, kv_set=W.kv_set, px_of=lambda c: None)
    assert doc["test"] and doc["owner_only"] and doc["paper"] and doc["real_account"] is False and "ТЕСТ" in doc["label_ru"]
    assert doc["variant"] == {"entry": "candle", "min_profit": 0.05, "hold_days": 3, "stop": 0.16} and doc["backtest"]["n"] == 426
    assert not fu.copy_sig("reversal:R-ZEC-L-1") and not fu.copy_sig("razvorot:x", {})
