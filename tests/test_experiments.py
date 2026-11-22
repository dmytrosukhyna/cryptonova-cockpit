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


def test_factory_seeds_50_plus_specs_runs_them_and_evolves():
    from cnq.agents import exp_factory as F
    seeds = F.seed_specs()
    assert len(seeds) >= 50 and len({s["id"] for s in seeds}) == len(seeds)
    fams = {s["family"] for s in seeds}
    assert {"baseline", "filter", "confirm", "stop", "tp", "hold", "size", "contrarian", "calendar", "panic", "breakout", "crowd"} <= fams
    st = {}
    now = T0 + 3 * H                                                     # a Tuesday? not needed: market events checked below
    desk = {"open": [{"whale": "bn:1", "coin": "UNI", "side": "SHORT", "entry": 5.0, "opened_t": now}], "closed": []}
    r = F.step(st, now, {}, {"whales": desk}, {"UNI": 5.0, "BTC": 60000.0})
    assert r["live_specs"] == len(seeds) and r["opened"] > 10 and "cycle_ms" in st
    byspec = {p["spec"]: p for p in st["open"]}
    base = next(s["id"] for s in st["specs"] if s["src"] == "whales" and s["family"] == "baseline")
    inv = next(s["id"] for s in st["specs"] if s["src"] == "whales" and s["family"] == "contrarian" and not s.get("tp"))
    assert byspec[base]["side"] == "SHORT" and byspec[inv]["side"] == "LONG"
    assert not any(st_["spec"].startswith("whales|follow|major") for st_ in st["open"])           # UNI is not a major
    assert any(q["spec"].startswith("whales|follow|-|conf1") for q in st["pending"])
    # +1 % our way (short: price down) → the confirmation opens; −4.2 % against → the −4 % stop closes
    F.step(st, now + 300_000, {}, {"whales": desk}, {"UNI": 4.94, "BTC": 60000.0})
    assert any(p["spec"].startswith("whales|follow|-|conf1") for p in st["open"])
    F.step(st, now + 600_000, {}, {"whales": desk}, {"UNI": 5.23, "BTC": 60000.0})
    s4 = next(s["id"] for s in st["specs"] if s["src"] == "whales" and s["stop"] == 4)
    c = next(c for c in st["closed"] if c["spec"] == s4)
    assert c["why"] == "стоп" and abs(c["pct"] - (-4.11)) < 1e-6
    # the whale exits → base closes with the source
    desk2 = {"open": [], "closed": [{"whale": "bn:1", "coin": "UNI", "side": "SHORT", "entry": 5.0, "opened_t": now, "exit": 4.5}]}
    F.step(st, now + 900_000, {}, {"whales": desk2}, {"UNI": 4.5, "BTC": 60000.0})
    cb = next(c for c in st["closed"] if c["spec"] == base)
    assert cb["why"] == "источник вышел" and cb["pct"] > 9


DAY_MS = 86_400_000


def test_factory_calendar_evolution_and_candidate_guard():
    from cnq.agents import exp_factory as F
    import calendar as _cal
    mon = _cal.timegm((2026, 11, 23, 0, 5, 0)) * 1000                       # Monday 23.11.2026 00:05 UTC
    st = {}
    F.step(st, mon, {"fng": 9}, {}, {"BTC": 60000.0})
    subs = {p["spec"] for p in st["open"]}
    assert any("|mon" in x for x in subs) and any(x.startswith("fear|") for x in subs)
    assert not any("|fri" in x for x in subs)
    # evolution: a spec with 40 losing trades dies; a winner with ≥ 15 trades spawns a mutation the next day
    loser = next(s for s in st["specs"] if s["src"] == "brk")
    winner = next(s for s in st["specs"] if s["src"] == "lsr")
    st["closed"] += [{"spec": loser["id"], "pct": -1.0, "closed_t": mon + i} for i in range(40)]
    st["closed"] += [{"spec": winner["id"], "pct": 2.0 if i % 4 else -1.0, "closed_t": mon + i} for i in range(20)]
    n0 = len(st["specs"])
    F.step(st, mon + DAY_MS, {}, {}, {"BTC": 60000.0})
    assert not next(s for s in st["specs"] if s["id"] == loser["id"])["alive"]
    kids = [s for s in st["specs"] if s.get("parent") == winner["id"]]
    assert len(st["specs"]) > n0 and kids and kids[0]["gen"] >= 1
    g = st["generations"][-1]
    assert loser["id"] in g["killed"] and kids[0]["id"] in g["spawned"]
    # candidate guard: ≥ 30 trades, t ≥ 3, both halves positive, better than the source
    good = F.score([1.5 if i % 5 else -0.5 for i in range(40)])
    assert F.candidate(good, {"pf": 0.66, "max_dd": 50}) and not F.candidate(good, {"pf": 99, "max_dd": 50})
    assert not F.candidate(F.score([1.0, -0.9] * 15), {"pf": 0.5, "max_dd": 50})


def test_factory_tick_through_the_api_with_a_fake_okx(tmp_path):
    from cnq.api import experiments as E
    web, data = tmp_path / "web", tmp_path / "data"
    web.mkdir(); data.mkdir()
    json.dump({"desk": {"open": [_o("ETH", "LONG", 3000.0, T0)], "closed": []}}, open(web / "whales_live.json", "w"))
    json.dump({"fng": {"value": 30}}, open(web / "markets.json", "w"))
    calls = []

    def get(url):
        calls.append(url)
        if "tickers" in url:
            return {"data": [{"instId": "ETH-USDT-SWAP", "last": "3000"}, {"instId": "BTC-USDT-SWAP", "last": "60000"}]}
        if "candles" in url:
            return {"data": [[str(T0 - i * H), "1", "2", "0.5", str(60000 - i), "0", "0", "0", "1"] for i in range(300)]}
        if "funding-rate" in url:
            return {"data": [{"fundingRate": "0.0001"}]}
        if "long-short" in url:
            return {"data": [[str(T0), "1.2"]]}
        return {"data": []}
    ex = E.Experiments(str(data), str(web), env={})
    hl = []
    ex._hl_post = lambda body: hl.append(body) or {"assetPositions": []}           # the watched wallets: a fake Hyperliquid
    r = ex.factory_tick(T0 + 60_000, get, json.load(open(web / "whales_live.json"))["desk"])
    assert hl and all(b["type"] == "clearinghouseState" for b in hl)
    assert r["opened"] > 5 and r["live_specs"] >= 50
    assert sum("tickers" in u for u in calls) == 1                           # one marks call per pass
    code, doc = ex.factory()
    assert code == 200 and doc["live"] >= 50 and doc["rows"] and doc["cycle_ms"] is not None


def test_watched_hl_wallets_are_paper_sources_with_group_specs():
    from cnq.agents import exp_factory as F
    st = {}
    now = T0 + 10 * H
    F.step(st, now, {}, {}, {"BTC": 60000.0})                                     # seeds the specs
    w = ["0xaaa", "0xbbb"]
    new = F.ensure_watch_specs(st, w, now)
    assert len(new) == 5 and not F.ensure_watch_specs(st, w, now)                 # 2 wallets + 3 group specs, once
    pos = {"0xaaa": [{"coin": "BTC", "side": "LONG", "entry": 59000.0}], "0xbbb": []}
    book, d = F.hl_book(None, w, lambda a: pos[a], now, {"BTC": 60000.0})
    assert d["open"][0]["opened_t"] == 0                                          # already open at the first read: not an event
    pos["0xbbb"] = [{"coin": "BTC", "side": "LONG", "entry": 60000.0}]
    pos["0xaaa"] = [{"coin": "ETH", "side": "SHORT", "entry": 3000.0}, {"coin": "BTC", "side": "LONG", "entry": 59000.0}]
    book, d = F.hl_book(book, w, lambda a: pos[a], now + 300_000, {"BTC": 60000.0, "ETH": 3000.0})
    F.step(st, now + 300_000, {}, {"hlw": d}, {"BTC": 60000.0, "ETH": 3000.0})
    mine = [p for p in st["open"] if p["spec"].startswith("hlw|")]
    assert {(p["coin"], p["spec"].split("|")[-1]) for p in mine if "agree2" not in p["spec"]} >= {("ETH", "0xaaa"), ("BTC", "0xbbb")}
    assert not any(p["coin"] == "BTC" and p["spec"].endswith("0xaaa") for p in mine)   # his old BTC long was not taken
    pos["0xaaa"] = [{"coin": "BTC", "side": "LONG", "entry": 59000.0}]                 # he closed ETH
    book, d = F.hl_book(book, w, lambda a: pos[a], now + 600_000, {"BTC": 60000.0, "ETH": 2900.0})
    assert d["closed"] and d["closed"][0]["coin"] == "ETH" and d["closed"][0]["exit"] == 2900.0
    F.step(st, now + 600_000, {}, {"hlw": d}, {"BTC": 60000.0, "ETH": 2900.0})
    assert any(c["spec"].endswith("0xaaa") and c["coin"] == "ETH" and c["why"] == "источник вышел" and c["pct"] > 3 for c in st["closed"])


def test_postmortem_specs_and_the_watch_list_config():
    from cnq.agents import exp_factory as F
    st = {}
    F.step(st, T0, {}, {}, {"BTC": 60000.0})
    new = F.ensure_specs(st, F.POSTMORTEM_SPECS, T0, "postmortem")
    assert len(new) == len(F.POSTMORTEM_SPECS) and not F.ensure_specs(st, F.POSTMORTEM_SPECS, T0, "postmortem")
    opens = [_o("ETH", "LONG", 3000.0, T0 + 1), _o("SOL", "LONG", 70.0, T0 + 2, whale="bn:2"), _o("ARB", "LONG", 0.1, T0 + 3, whale="bn:3")]
    ev = {e["coin"]: e for e in F.desk_events({"open": opens}, "whales")}
    cap = next(s for s in st["specs"] if s["filt"] == ["cap2side"])
    assert F.passes(cap, "ETH", "LONG", {}, ev["ETH"]) and not F.passes(cap, "ARB", "LONG", {}, ev["ARB"])     # the 3rd long is skipped
    w = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "exp_watch_traders.json")))
    assert 1 <= len(w["hl"]) <= 30 and all(x["addr"].startswith("0x") and len(x["addr"]) == 42 for x in w["hl"])


def test_telegram_calls_become_paper_buys_with_his_target_and_recaps_do_not(tmp_path):
    from cnq.agents import exp_factory as F, influencer as I
    page = ('<div data-post="c/10"><div class="tgme_widget_message_text js-message_text" dir="auto">SUI добавил по 0.75, сейчас 1.03, жду 2x</div>'
            '<time datetime="2026-11-23T09:00:00+00:00"></time></div>'
            '<div data-post="c/11"><div class="tgme_widget_message_text js-message_text" dir="auto">Набираю $VVV по 3.2, цель 6</div>'
            '<time datetime="2026-11-23T10:00:00+00:00"></time></div>')
    posts = I.parse_page(page)
    born = posts[0]["t"] - 1
    ev = F.tg_events(posts, {"SUI", "VVV"}, "A", born)
    assert [o["coin"] for o in ev["open"]] == ["VVV"]                                    # the SUI recap is not a call
    st = {"born": born, "specs": []}
    F.step(st, born, {}, {}, {"BTC": 60000.0})
    F.ensure_specs(st, F.TG_SPECS, born, "tg")
    now = posts[1]["t"] + 60_000
    F.step(st, now, {"vol24": {"VVV": 1e6}}, {"tgc": ev}, {"VVV": 3.0, "BTC": 60000.0})
    mine = [p for p in st["open"] if p["spec"].startswith("tgc|")]
    assert len(mine) == 3 and all(p["entry"] == 3.0 for p in mine)                     # the liquidity filter skips one ($1M a day)
    assert {p["tp_px"] for p in mine} == {6.0, None}
    F.step(st, now + 3_600_000, {}, {"tgc": ev}, {"VVV": 6.1, "BTC": 60000.0})
    hit = [c for c in st["closed"] if c["spec"].startswith("tgc|") and c["why"] == "его цель"]
    assert len(hit) == 2 and all(abs(c["pct"] - (100 - 0.11)) < 0.01 for c in hit)       # 3.0 → his target 6.0 = +100 %


def test_owner_sets_the_channel_only_in_the_server_data_dir(tmp_path):
    from cnq.api import experiments as E
    ex = E.Experiments(str(tmp_path), str(tmp_path), env={})
    assert ex.set_channel("A", "bad handle!")[0] == 400
    code, d = ex.set_channel("A", "some_channel")
    assert code == 200 and d["channels"] == ["A"]
    assert json.load(open(tmp_path / "influencers" / "channels.json"))["channels"][0]["handle"] == "some_channel"
    import inspect
    from cnq.api import server
    assert '"/v2/owner/influencers"' in inspect.getsource(server)


def test_forwarded_instagram_calls_feed_the_same_paper_desk():
    from cnq.agents import exp_factory as F
    ev = F.manual_events([{"t": "2026-11-23T12:00:00+00:00", "coin": "vvv", "entry": 3.1, "targets_x": [2], "source": "instagram"},
                          {"t": 1, "coin": "OLD"}, {"coin": "bad coin!"}], "A", 1795000000000)
    assert [e["coin"] for e in ev] == ["VVV"] and ev[0]["targets_x"] == [2.0] and ev[0]["source"] == "instagram"
    assert os.path.exists(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "influencer_calls_A.jsonl"))
