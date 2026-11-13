"""13.11 (owner 22:03, PAPER ONLY): «Альты от китов (тест)» — buy when smart wallets turn a token to «накопление»."""
import os

from cnq.agents import smart_alts_desk as S
from cnq.execution import okx_futures as fu

DAY = S.DAY
T0 = 1_800_000_000_000
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tok(key, status, n=2, liq=900_000.0, px=1.0, sym=None):
    return {"key": key, "symbol": sym or key.upper(), "chain": "eth", "status": status, "n": n, "liq": liq, "price": px,
            "buyers": [{"label": "Jump Trading"}, {"label": "Wintermute"}][:n]}


def _sm(*toks):
    return {"watch": {"tokens": list(toks)}}


MK = {"top": [{"symbol": "BTC", "rank": 1, "price": 100_000.0}, {"symbol": "ETH", "rank": 2, "price": 3000.0},
              {"symbol": "USDT", "rank": 3, "price": 1.0, "stable": True}, {"symbol": "SOL", "rank": 4, "price": 100.0},
              {"symbol": "XRP", "rank": 5, "price": 2.0}]}


def test_buys_only_on_a_turn_to_accumulation_with_two_buyers_and_liquidity():
    d = S.step(None, _sm(), MK, T0)
    d["seen"] = {"a": "накопление", "b": "под наблюдением", "c": "под наблюдением", "d": "под наблюдением"}
    sm = _sm(_tok("a", "накопление"), _tok("b", "накопление"), _tok("c", "накопление", n=1), _tok("d", "накопление", liq=100_000.0))
    d = S.step(d, sm, MK, T0 + 600_000)
    assert [o["key"] for o in d["open"]] == ["b"] and d["open"][0]["notional"] == 1000.0     # «a» was already accumulating
    assert d["open"][0]["bench"]["btc"] == 100_000.0 and set(d["open"][0]["bench"]["alts"]) == {"SOL", "XRP"}
    assert sum(1 for e in d["events"] if e["kind"] == "skip") == 2


def test_exits_on_selling_fewer_buyers_stop_and_time_with_costs_and_benchmarks():
    d = S.step(None, _sm(), MK, T0)
    d["seen"] = {k: "под наблюдением" for k in "wxyz"}
    d = S.step(d, _sm(*(_tok(k, "накопление") for k in "wxyz")), MK, T0 + 1)
    assert len(d["open"]) == 4
    mk2 = {"top": [dict(r, price=r["price"] * 1.1) for r in MK["top"]]}
    sm = _sm(_tok("w", "продают", px=1.2), _tok("x", "накопление", n=1, px=1.1), _tok("y", "накопление", px=0.70), _tok("z", "накопление", px=1.05))
    d = S.step(d, sm, mk2, T0 + DAY)
    why = {c["key"]: c["why"] for c in d["closed"]}
    assert why == {"w": "умные кошельки продают", "x": "покупателей меньше 2", "y": "стоп −25%"}
    w = next(c for c in d["closed"] if c["key"] == "w")
    assert abs(w["pnl_pct"] - (20 - 0.6)) < 1e-9 and w["btc_same_time_usd"] > 90 and w["alts_same_time_usd"] > 90   # +10 % − costs
    d = S.step(d, sm, mk2, T0 + 31 * DAY)
    assert next(c for c in d["closed"] if c["key"] == "z")["why"] == "30 дней прошло"
    assert d["bench_now"]["btc_usd"] == d["bench"]["btc_pnl"]


def test_owner_only_paper_and_never_on_the_real_account(tmp_path):
    import json
    web = tmp_path / "web"; web.mkdir()
    (web / "smart_money.json").write_text(json.dumps(_sm(_tok("a", "накопление"))))
    (web / "markets.json").write_text(json.dumps(MK))
    st = {}
    doc = S.run(str(tmp_path), str(web), now=T0, kv_get=st.get, kv_set=st.__setitem__)
    assert doc["test"] and doc["owner_only"] and doc["real_account"] is False and doc["open"] == []   # the first run is a baseline
    assert (tmp_path / "smart_alts.json").exists() and not fu.copy_sig("smart:a", {})
    srv = open(os.path.join(ROOT, "cnq", "api", "server.py"), encoding="utf-8").read()
    assert 'path == "/v2/owner/smart_alts"' in srv and not os.path.exists(os.path.join(ROOT, "web", "smart_alts.json"))


def test_backtest_replays_the_logged_snapshots():
    bt = S.backtest(os.path.join(ROOT, "data", "smart_money", "watchlist"))
    assert bt is None or (bt["days"] >= 1 and "note_ru" in bt)
