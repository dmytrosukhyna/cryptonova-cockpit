import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from synth import make_candles  # noqa: E402

from cnq.bingx.orderbook import OrderBook  # noqa: E402
from cnq.bingx.rest import BingXRest, Candle  # noqa: E402
from cnq.collector.quality import coverage, find_gaps, sanity  # noqa: E402
from cnq.features.candles import atr, cvd, delta, relative_volume, resample  # noqa: E402
from cnq.features.levels import build_levels, find_swings  # noqa: E402
from cnq.paper.replay import run_replay  # noqa: E402
from cnq.paper.simulator import PaperEngine  # noqa: E402
from cnq.risk.engine import ContractSpec, build_plan, ladder_outcome_r  # noqa: E402
from cnq.risk.ev import breakeven_p_tp1  # noqa: E402
from cnq.setups.discovery import Discovery  # noqa: E402
from cnq.store.db import Store  # noqa: E402


# ----------------------------------------------------------------- BingX parsing
def test_kline_parse_and_delta():
    raw = [1700000000000, "100", "110", "95", "105", "50", 1700000059999, "5000", 120, "30", "3000"]
    c = BingXRest._parse_candle(raw)
    assert c.taker_buy_volume == 30 and c.taker_sell_volume == 20 and c.delta == 10
    assert c.close_time == 1700000059999


def test_orderbook_incremental_and_gap():
    ob = OrderBook("BTC-USDT")
    assert ob.apply({"action": "all", "lastUpdateId": 10, "T": 1,
                     "bids": [["100", "1"], ["99", "2"]], "asks": [["101", "1"], ["102", "3"]]})
    assert ob.apply({"action": "update", "lastUpdateId": 11, "T": 2, "bids": [["99", "0"], ["98", "5"]], "asks": []})
    assert 99 not in ob.bids and ob.bids[98] == 5 and ob.best_bid() == 100
    assert not ob.apply({"action": "update", "lastUpdateId": 13, "T": 3, "bids": [], "asks": []})
    assert ob.synced is False and ob.gaps == 1
    assert ob.apply({"action": "all", "lastUpdateId": 20, "T": 4, "bids": [["100", "1"]], "asks": [["101", "1"]]})
    assert ob.imbalance(0.05) == 0.0


# ----------------------------------------------------------------- quality
def test_gaps_and_coverage():
    cs = make_candles(100)
    assert find_gaps(cs, "15m") == []
    broken = cs[:50] + cs[52:]
    assert len(find_gaps(broken, "15m")) == 1
    assert coverage(broken, "15m")["gaps"] == 1
    assert sanity(cs) == []


# ----------------------------------------------------------------- features
def test_features_causal_shapes():
    cs = make_candles(500)
    import numpy as np
    h = np.array([c.high for c in cs]); l = np.array([c.low for c in cs]); c_ = np.array([c.close for c in cs])
    v = np.array([c.volume for c in cs]); tb = np.array([c.taker_buy_volume for c in cs])
    assert (atr(h, l, c_) > 0).all()
    assert abs(cvd(v, tb)[-1] - delta(v, tb).sum()) < 1e-9
    rv = relative_volume(v)
    assert rv[0] == 1.0 and rv.shape == v.shape
    # causality: changing the future must not change the past
    rv2 = relative_volume(np.concatenate([v[:300], v[300:] * 10]))
    assert (rv2[:300] == rv[:300]).all()
    h1 = resample(cs, 4)
    assert len(h1) == 125 and h1[0].volume == sum(c.volume for c in cs[:4])


def test_levels_found_and_swings_confirm_lag():
    cs = make_candles(1500)
    sw = find_swings(cs, 5, 5, upto=1000)
    assert sw and all(s.confirm_index <= 1000 for s in sw)
    levels = build_levels(cs)
    assert levels and all(L.lo <= L.hi for L in levels)
    assert all(0 <= L.score <= 100 for L in levels)
    assert any(L.touch_count >= 3 for L in levels)
    # causality: levels known at index 1000 must not depend on later candles
    early = build_levels(cs, upto=1000)
    early2 = build_levels(cs[:1000])
    assert [(L.lo, L.hi, L.status) for L in early] == [(L.lo, L.hi, L.status) for L in early2]


# ----------------------------------------------------------------- risk engine
def test_ladder_arithmetic():
    assert ladder_outcome_r("STRICT_CRYPTO_NOVA", 0) == -1.0
    assert ladder_outcome_r("STRICT_CRYPTO_NOVA", 1) == -0.4
    assert ladder_outcome_r("STRICT_CRYPTO_NOVA", 2) == 0.5
    assert ladder_outcome_r("STRICT_CRYPTO_NOVA", 5) == 1.1
    assert ladder_outcome_r("CRYPTO_NOVA_RUNNER", 5) == 1.05
    assert 0.6 < breakeven_p_tp1("STRICT_CRYPTO_NOVA", 0.75) < 0.7


def test_build_plan_sizing_and_limits():
    spec = ContractSpec("BTC-USDT", qty_step=0.0001, price_step=0.1, min_qty=0.0001, min_notional_usdt=2)
    p = build_plan("BTC-USDT", "LONG", 60000, 59400, 10000, 1.0, spec, leverage=5)
    assert p.ok, p.warnings
    assert abs(p.qty * (p.r_distance + 60000 * (0.001 + 0.0003)) - 100) < 60000 * 0.0001 + 1e-6
    assert p.tps[0] == 60300 and p.tps[-1] == 61500
    assert abs(sum(p.tp_qty) - p.qty) < 1e-9
    assert p.pnl_at_tp[-1] > p.pnl_at_tp[0] and p.pnl_at_tp[0] < 0  # TP1-then-stop is a loss
    bad = build_plan("BTC-USDT", "LONG", 60000, 60500, 10000, 1.0, spec)
    assert not bad.ok
    liq = build_plan("BTC-USDT", "LONG", 60000, 55000, 10000, 1.0, spec, leverage=50)
    assert any("liquidation" in w for w in liq.warnings)


# ----------------------------------------------------------------- paper engine
def _c(t, o, h, l, c):
    return Candle(t, o, h, l, c, 10, t + 1, 10 * c, 1, 5, 5 * c)


def test_paper_stop_first_and_ladder():
    spec = ContractSpec("X", qty_step=0.001, price_step=0.1, taker_fee=0.0, maker_fee=0.0)
    eng = PaperEngine(spec, slippage_pct=0.0)
    plan = build_plan("X", "LONG", 100, 90, 10000, 1.0, spec)
    pos = eng.open_position("s", plan, _c(1, 100, 100, 100, 100))
    # candle touches TP1 (105) and the stop (90) at once → stop first → -1R
    eng.step(_c(2, 100, 106, 89, 95), 1.0)
    rec = eng.trade_record(pos, "r", "X")
    assert rec["exit_kind"] == "STOP" and math.isclose(rec["result_r"], -1.0, abs_tol=0.01)

    eng = PaperEngine(spec, slippage_pct=0.0)
    plan = build_plan("X", "LONG", 100, 90, 10000, 1.0, spec)
    pos = eng.open_position("s", plan, _c(1, 100, 100, 100, 100))
    eng.step(_c(2, 100, 105.5, 99, 104), 1.0)      # TP1 only; stop unchanged
    assert pos.tps_hit == 1 and pos.stop == 90
    eng.step(_c(3, 104, 110.5, 103, 109), 1.0)     # TP2 → stop to break-even
    assert pos.tps_hit == 2 and pos.stop == 100
    eng.step(_c(4, 109, 109, 99, 101), 1.0)        # back to BE → out at 100
    rec = eng.trade_record(pos, "r", "X")
    assert rec["exit_kind"] == "STOP" and math.isclose(rec["result_r"], 0.5, abs_tol=0.01)


# ----------------------------------------------------------------- end to end
def test_replay_end_to_end_with_store():
    cs = make_candles(2500)
    with tempfile.TemporaryDirectory() as d:
        store = Store(os.path.join(d, "t.sqlite"))
        store.upsert_candles("BTC-USDT", "15m", cs)
        assert store.candle_bounds("BTC-USDT", "15m")[2] == 2500
        res = run_replay("BTC-USDT", cs, "15m", store=store, min_score=0)
        s = res["summary"]
        assert s["setups_by_status"] and sum(s["setups_by_status"].values()) == len(res["setups"])
        n_db = store.conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
        assert n_db == s["trades"]
        # no look-ahead: MARKET fills after the CALL candle; LIMIT fills on the touch candle
        # at a price that candle actually traded through
        for st in res["setups"]:
            if "trade" in st.evidence:
                call_c = cs[st.evidence["call_index"]]
                tr = st.evidence["trade"]
                if st.entry_type == "LIMIT":
                    assert tr["entry_time"] == call_c.open_time
                    assert call_c.low <= tr["entry"] <= call_c.high
                else:
                    assert tr["entry_time"] > call_c.open_time
        store.close()


def test_discovery_statuses_progress():
    cs = make_candles(2000)
    d = Discovery("BTC-USDT", cs)
    for i in range(len(cs)):
        d.step(i)
    statuses = {s.status for s in d.setups}
    assert "ATTENTION" in statuses or "EXPIRED" in statuses or "CALL" in statuses
    for s in d.setups:
        assert s.transitions and s.transitions[0][1] == "DISCOVERED"
