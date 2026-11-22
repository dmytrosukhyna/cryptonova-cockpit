"""22.11 (owner 16:18): the experiment program — paper variants of the whale copy stream and the daily strategy board. Made-up data;
OKX and GitHub are never called."""
import json
import os
import tempfile

from cnq.agents import exp_desks as X
from cnq.agents import strategy_board as SB

H = 3_600_000
T0 = 1_795_000_000_000


def _desk(opens, closed=()):
    return {"open": list(opens), "closed": list(closed)}


def _o(coin, side, entry, t, price=None, whale="bn:1"):
    return {"whale": whale, "coin": coin, "side": side, "entry": entry, "opened_t": t, "price": price or entry}


def test_variants_filter_and_exit_like_registered():
    st = {"started": T0}
    up = (1.0, 0.9, 0.8)          # BTC 4h close > EMA20, EMA20 rising → no alt shorts
    ctx = {"regime": up, "smart": {"UNI": "acc"}}
    d = _desk([_o("UNI", "SHORT", 5.0, T0 + 60_000), _o("BTC", "LONG", 60000.0, T0 + 60_000, whale="bn:2"),
               _o("BTC", "LONG", 60000.0, T0 + 60_000, whale="bn:3")])
    X.step(st, d, T0 + 120_000, ctx, atr_of=lambda c: 1000.0)
    by = {}
    for p in st["open"]:
        by.setdefault(p["coin"], set()).add(p["v"])
    assert "a_regime" not in by["UNI"] and "b_smart" not in by["UNI"] and "base" in by["UNI"]     # filters (a) and (b)
    assert "d_major2" in by["BTC"] and "d_major2" not in by["UNI"]                               # (d) two whales on BTC
    assert "e_btc" in by["BTC"] and "e_majors" not in by["UNI"]                                  # (e)
    # prices move: UNI short −9 % against us → only the −8 % stop closes; BTC +20 % → c_be1r arms breakeven
    d2 = _desk([_o("UNI", "SHORT", 5.0, T0 + 60_000, 5.45), _o("BTC", "LONG", 60000.0, T0 + 60_000, 72000.0, "bn:2"),
                _o("BTC", "LONG", 60000.0, T0 + 60_000, 72000.0, "bn:3")])
    X.step(st, d2, T0 + H, ctx)
    closed = {(c["coin"], c["v"]) : c for c in st["closed"]}
    assert ("UNI", "c_stop8") in closed and closed[("UNI", "c_stop8")]["why"] == "стоп" and ("UNI", "base") not in closed
    be = next(p for p in st["open"] if p["coin"] == "BTC" and p["v"] == "c_be1r")
    assert be["be"]
    # BTC falls back to the entry: the breakeven variant exits near 0, base stays open; trailing (ATR 1000 × 1.5) exits too
    d3 = _desk([_o("BTC", "LONG", 60000.0, T0 + 60_000, 59990.0, "bn:2"), _o("BTC", "LONG", 60000.0, T0 + 60_000, 59990.0, "bn:3")],
               closed=[{**_o("UNI", "SHORT", 5.0, T0 + 60_000), "exit": 4.5, "closed_t": T0 + 2 * H}])
    X.step(st, d3, T0 + 2 * H, ctx)
    closed = {(c["coin"], c["v"], c["whale"]): c for c in st["closed"]}
    assert closed[("BTC", "c_be1r", "bn:2")]["why"] == "стоп" and abs(closed[("BTC", "c_be1r", "bn:2")]["pct"] + 0.11) < 0.01
    assert closed[("BTC", "c_trail", "bn:2")]["why"] == "стоп" and closed[("BTC", "c_trail", "bn:2")]["pct"] > 15
    assert closed[("UNI", "base", "bn:1")]["why"] == "кит вышел" and closed[("UNI", "base", "bn:1")]["pct"] > 9
    assert any(p["coin"] == "BTC" and p["v"] == "base" for p in st["open"])
    # 48 h
    X.step(st, d3, T0 + 49 * H, ctx)
    assert any(c["v"] == "c_time48" and c["why"] == "48 ч" for c in st["closed"])
    # a copy opened before the program started is not taken in
    st2 = {"started": T0 + 10 * H}
    X.step(st2, d, T0 + 10 * H, ctx)
    assert st2["open"] == []


def test_promotion_needs_30_forward_trades_and_better_pf_and_dd_than_the_source():
    st = {"closed": [{"v": "c_stop8", "pct": 2.0 if i % 3 else -1.0, "closed_t": T0 + i} for i in range(30)]}
    s = X.summary(st, {"pf": 0.66, "max_dd_pct": 8.5})["variants"]["c_stop8"]
    assert s["n"] == 30 and s["pf"] > 0.66 and s["promote"]
    st["closed"] = st["closed"][:29]
    assert not X.summary(st, {"pf": 0.66, "max_dd_pct": 8.5})["variants"]["c_stop8"]["promote"]


def test_strategy_board_table_rank_and_flag(tmp_path):
    web, data = str(tmp_path / "web"), str(tmp_path / "data")
    os.makedirs(web); os.makedirs(os.path.join(data, "levels")); os.makedirs(os.path.join(data, "hacks"))
    now = T0 + 40 * 24 * H
    good = [{"closed_t": now - (i + 1) * 20 * H, "usd": 30.0 if i % 4 else -20.0, "pct": 1.0} for i in range(36)]
    bad = [{"closed_t": now - (i + 1) * 20 * H, "pnl": -10.0 if i % 3 else 8.0} for i in range(36)]
    json.dump({"desk": {"closed": bad, "start": 10000}}, open(os.path.join(web, "whales_live.json"), "w"))
    json.dump({"start_usd": 10276.07, "closed": [{"closed": now - H, "pnl_usd": -2.09}]}, open(os.path.join(web, "public_real.json"), "w"))
    json.dump({"start": 10000, "closed": good}, open(os.path.join(data, "levels", "state.json"), "w"))
    json.dump({"start": 10000, "closed": good[:5]}, open(os.path.join(data, "hacks", "state.json"), "w"))
    b = SB.write(web, data, now)
    rows = {r["key"]: r for r in b["rows"]}
    assert rows["levels"]["beats_source"] and not rows["hacks"]["beats_source"] and not rows["whales"]["beats_source"]
    assert rows["levels"]["rank"] < rows["whales"]["rank"] and rows["whales"]["d30"]["pf"] < 1
    assert rows["real"]["d7"]["n"] == 1 and rows["real"]["d7"]["sum"] == -2.09
    md = SB.markdown(b)
    assert "| Уровни |" in md and "**да**" in md and "bn:" not in md                       # numbers only, no trader ids
    assert json.load(open(os.path.join(data, "strategy_board.json")))["rows"]


def test_server_routes_are_owner_only_and_the_thread_starts():
    import inspect
    from cnq.api import server
    src = inspect.getsource(server)
    assert '"/v2/owner/exp_desks"' in src and '"/v2/owner/strategy_board"' in src and "api.experiments.start()" in src
    assert "/v2/exp_desks" not in src.replace("/v2/owner/exp_desks", "")
