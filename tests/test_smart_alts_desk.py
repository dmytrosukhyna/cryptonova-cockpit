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


def test_open_positions_carry_their_own_benchmarks_and_the_owner_card_is_owner_only():
    """13.11: the owner card compares every position (open too) with BTC and the top-20 alts on the same timing; it is shown only in
    the owner session (vip tab owner: true, app card behind CNAuth.state.isOwner) and reads only the owner endpoint"""
    import json
    import shutil
    import subprocess
    import tempfile
    d = S.step(None, _sm(), MK, T0)
    d["seen"] = {"z": "под наблюдением"}
    d = S.step(d, _sm(_tok("z", "накопление")), MK, T0 + 1)
    mk2 = {"top": [dict(r, price=r["price"] * 1.1) for r in MK["top"]]}
    d = S.step(d, _sm(_tok("z", "накопление", px=1.05)), mk2, T0 + DAY)
    o = d["open"][0]
    assert abs(o["btc_same_time_usd"] - 100.0) < 0.01 and abs(o["alts_same_time_usd"] - 100.0) < 0.01 and o["upnl"] == 50.0
    pub = S.public(d, T0 + DAY)
    assert "bench" not in pub["open"][0] and pub["open"][0]["btc_same_time_usd"] == o["btc_same_time_usd"]
    r = lambda *p: open(os.path.join(ROOT, *p), encoding="utf-8").read()
    core, app, vip, js = r("web", "vip", "core.js"), r("web", "app.html"), r("web", "vip.html"), r("web", "smart_alts.js")
    assert "{ id: 'smart_alts', name: 'Альты от китов (тест)', short: 'Альты', icon: 'strategies', owner: true }" in core
    assert "smart_alts: '/v2/owner/smart_alts'" in core and '<script src="vip/smart_alts.js"></script>' in vip
    assert 'id="smartAltsCard" hidden' in app and "CNAuth.fetchOwner('/v2/owner/smart_alts')" in app
    i = app.index("async function renderSmartAlts")
    assert app.index("CNAuth.state.isOwner", i) < app.index("fetchOwner('/v2/owner/smart_alts')", i)
    assert "smart_alts.json" not in app + js + r("web", "vip", "smart_alts.js") and not os.path.exists(os.path.join(ROOT, "web", "smart_alts.json"))
    node = shutil.which("node")
    if not node:
        return
    doc = {**pub, "backtest": {"days": 10, "from": "2026-11-04", "to": "2026-11-13", "trades_closed": 2, "open_now": 1, "pnl_usd": 12.0,
                               "closed": [{"symbol": "AAA", "pnl_pct": 5.0, "why": "умные кошельки продают"}], "note_ru": "Короткая история — это не доказательство."}}
    p = os.path.join(tempfile.mkdtemp(), "s.js")
    open(p, "w", encoding="utf-8").write("global.window={};\n" + js + f"\nprocess.stdout.write(window.CNSmartAlts.html({{data:{json.dumps(doc, ensure_ascii=False)}}}));")
    out = subprocess.run([node, p], capture_output=True, text=True, timeout=30).stdout
    for must in ("ТЕСТ", "Правило.", "Оговорка:", "это не доказательство", "BTC на те же даты", "20 крупных альтов", "Z</b>", "eth", "Jump Trading",
                 "BTC +$100,00", "альты +$100,00", "+$50,00"):
        assert must in out, must
