"""05.11 (owner, option 1): the one-time spot rebuild from the real OKX account (cnq/agents/spot_reseed.py)."""
import importlib.util
import json
import os
import sqlite3

from cnq.agents import portfolio as PF
from cnq.agents import spot_profiles as SP
from cnq.agents import spot_reseed as R

NOW = 1_793_900_000_000
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _guard():
    spec = importlib.util.spec_from_file_location("ci_state_guard", os.path.join(ROOT, "scripts", "ci_state_guard.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def pub():
    h = [{"time": NOW - 3_600_000 * k, "v": 95.0 + k * 0.01} for k in range(5, 0, -1)]
    return {"btc_bull": True, "portfolios": {
        "ideas": {"name": "CRYPTO NOVA портфель", "index": 95.01, "started": NOW - 20 * 86_400_000, "last_weekly": NOW - 3 * 86_400_000,
                  "history_all": h, "history": h[-2:],
                  "positions": [{"symbol": "BTC", "bucket": "core", "role": "buy", "entry": 81000.0, "entry_time": NOW - 20 * 86_400_000, "high": 83000.0,
                                 "price": 80000.0, "weight": 0.44, "name": "Bitcoin"},
                                {"symbol": "ZEC", "bucket": "mid", "role": "buy", "entry": 1150.0, "entry_time": NOW - 20 * 86_400_000, "high": 1366.0,
                                 "price": 1300.0, "weight": 0.09, "name": "Zcash"}]},
        "cons": {"index": 98.0, "history_all": [{"time": NOW - 3_600_000, "v": 98.0}],
                 "positions": [{"symbol": "BTC", "bucket": "core", "weight": 0.6, "entry": 100000.0, "price": 80000.0, "entry_time": NOW - 1}]},
        "bal": {"index": 95.28, "mode": "bull", "history_all": [{"time": NOW - 3_600_000, "v": 95.28}],
                "positions": [{"symbol": "ETH", "bucket": "core", "role": "core", "cost_weight": 0.2, "weight": 0.18, "entry": 2500.0, "price": 2400.0}]},
        "mod": {"index": 96.01, "history": [{"time": NOW - 3_600_000, "v": 96.01}],
                "positions": [{"symbol": "SOL", "bucket": "core", "weight": 0.1, "entry": 100.0, "price": 96.0, "entry_time": NOW - 1, "high": 105.0}]},
        "high": {"index": 96.8, "history_all": [{"time": NOW - 3_600_000, "v": 96.8}], "positions": []}}}


def real(asof=NOW - 5 * 60_000, **kw):
    return {"asof": asof, "stale": False, "equity_usd": 10000.0, "alloc": {"spot_pct": 65.0, "core_pct": 10.0, "active_coins_usd": 0},
            "spot": [{"sym": "BTC", "usd": 3600.0}, {"sym": "ZEC", "usd": 600.0}, {"sym": "ETH", "usd": 1300.0}, {"sym": "SOL", "usd": 500.0}], **kw}


PX = {"BTC": 80000.0, "ZEC": 1300.0, "ETH": 2400.0, "SOL": 96.0}


def test_rebuild_matches_the_account_continues_the_index_and_passes_the_guard():
    conn = sqlite3.connect(":memory:")
    r = R.apply(conn, pub(), real(), PX, now=NOW, cfg={"id": "rs-test"})
    w = {"spot": 0.65, "core": 0.10}
    assert r["gaps"] == []
    # the autopilot's targets (spot share × model weight + extra core) are what the account holds
    tg = {c: 0.65 * s for c, s in r["model"].items()}
    ce = R.al.core_extra(w)
    for c, s in r["account"].items():
        assert abs(tg.get(c, 0) + ce.get(c, 0) - s) < 1e-9
    ideas = r["ideas"]
    assert {p["symbol"] for p in ideas["positions"]} == {"BTC", "ZEC", "ETH", "SOL"} and ideas["reseed"]["id"] == "rs-test"
    btc = next(p for p in ideas["positions"] if p["symbol"] == "BTC")
    assert btc["entry"] == 81000.0 and btc["bucket"] == "core"                 # its real history kept
    eth = next(p for p in ideas["positions"] if p["symbol"] == "ETH")
    assert eth["entry"] == 2400.0                                            # a coin the old model lacked → today's price
    # the database model has the published index as its value; the published curve kept + one point now
    ip = SP.IdeasPortfolio(conn)
    assert abs(ip.value(PX) * 100 - 95.01) < 1e-6 and abs(ip.rp.cash() / ip.value(PX) - r["cash"]) < 1e-9
    assert [h["time"] for h in ideas["history_all"]][:5] == [h["time"] for h in pub()["portfolios"]["ideas"]["history_all"]]
    assert ideas["history_all"][-1] == {"time": NOW, "v": 95.01}
    assert "пересобрана" in ideas["reseed"]["note_ru"]
    # the guard: database and published model end at the same point → not stale
    g = _guard()
    db_t = conn.execute("SELECT MAX(time) FROM spot_ideas_value").fetchone()[0]
    assert g.verdict(db_t, NOW, NOW)["stale"] is False
    # display portfolios: value = published index
    assert abs(PF.BalancedPortfolio(conn).value({"ETH": 2400.0}) * 100 - 95.28) < 1e-6
    rp = SP.RiskPortfolio(conn, "cons")
    assert abs((rp.cash() + 0.6 * 80000 / 100000) * 100 - 98.0) < 1e-6
    assert abs(PF.SpotPortfolio(conn)._value({"SOL": 96.0})[0] * 100 - 96.01) < 1e-6
    # once only
    assert R.apply(conn, pub(), real(), PX, now=NOW + 1, cfg={"id": "rs-test"}) is None
    json.dumps(ideas)


def test_a_stale_snapshot_or_a_plan_with_orders_writes_nothing():
    conn = sqlite3.connect(":memory:")
    for bad in (real(asof=NOW - 3_600_000), real(stale=True)):
        try:
            R.apply(conn, pub(), bad, PX, now=NOW, cfg={"id": "rs-x"})
            raise AssertionError("must refuse")
        except ValueError:
            pass
    assert not conn.execute("SELECT name FROM sqlite_master WHERE name='spot_ideas_portfolio'").fetchone()
    assert R.plan_gaps({"BTC": 0.5}, {"BTC": 0.36}, {"spot": 0.65, "core": 0.10}, 10000.0)        # a real gap is seen
    assert R.apply(conn, pub(), real(), PX, now=NOW, cfg={}) is None                                 # not requested
