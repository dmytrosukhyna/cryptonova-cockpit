"""26.11 (owner 18:24): futures-call parsing / simulation, the «хедж» filter, the futures-channel paper desks and the widened pool."""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

H = 3_600_000
T0 = 1795700000000


def test_futures_calls_are_parsed_in_the_usual_layouts_and_results_are_not_calls():
    from cnq.agents import fut_calls as F
    a = F.parse("#BTC/USDT LONG\nLeverage: 10x\nEntry: 95000 - 94000\nTargets: 1) 96000 2) 97000\nStop Loss: 92000")
    assert a == {"coin": "BTC", "side": "LONG", "entry_lo": 94000.0, "entry_hi": 95000.0, "stop": 92000.0, "targets": [96000.0, 97000.0],
                 "market": False}
    b = F.parse("ETH SHORT · Entry zone 3050-3100 · TP 1: 2980 · TP 2: 2900 · SL 3180")
    assert b["side"] == "SHORT" and b["targets"] == [2980.0, 2900.0] and b["stop"] == 3180.0
    c = F.parse("ЛОНГ SOL вход 140-138, тейки 145 / 150, стоп 133")
    assert c["coin"] == "SOL" and c["entry_lo"] == 138.0 and c["targets"] == [145.0, 150.0]
    assert F.parse("#ETH/USDT Take-Profit target 1 ✅ Profit: 50%") is None                 # a result post
    assert F.parse("#BTC/USDT LONG Entry 95000 Targets 96000 Stop 97000") is None             # the stop above a long's entry
    assert F.parse("I think the market is bullish, buy the dip") is None
    assert F.parse("#BTC/USDT LONG Entry 95000 Targets 96000 Stop 93000", {"ETH"}) is None    # no such swap


def test_futures_call_simulation_is_conservative():
    from cnq.agents import fut_calls as F
    call = {"side": "LONG", "entry_lo": None, "entry_hi": None, "stop": 95.0, "targets": [110.0, 120.0]}
    bars = [[i * H, 100, 101, 99, 100] for i in range(5)] + [[5 * H, 100, 111, 99.5, 110]]
    r = F.sim(call, bars)
    assert r["kind"] == "tp" and abs(r["pct"] - 9.89) < 1e-6 and abs(r["r"] - 9.89 / (100 / 95 - 1) / 100) < 0.01
    both = [[0, 100, 111, 94, 100]]                                       # one bar touches the stop and the target: the stop
    assert F.sim(call, both)["kind"] == "stop"
    zone = {"side": "SHORT", "entry_lo": 105.0, "entry_hi": 106.0, "stop": 110.0, "targets": [100.0]}
    assert F.sim(zone, [[0, 100, 101, 99, 100]] * 50)["kind"] == "no_fill"   # never came back to the zone
    hit = F.sim(zone, [[0, 100, 105.5, 99, 104], [H, 104, 104.5, 99.5, 100]])
    assert hit["kind"] == "tp" and abs(hit["pct"] - ((1 - 100 / 105) * 100 - 0.11)) < 1e-3   # filled at the zone's near edge (105)


def test_hedge_flagged_traders_are_kept_out_of_the_no_hedge_specs():
    from cnq.agents import exp_factory as F
    st = {}
    F.step(st, T0, {}, {}, {"BTC": 60000.0})
    assert len(F.ensure_specs(st, F.HEDGE_SPECS, T0, "hedge")) == 3
    desk = {"open": [{"whale": "bn:1", "coin": "ETH", "side": "LONG", "entry": 3000.0, "opened_t": T0 + 1},
                     {"whale": "bn:2", "coin": "SOL", "side": "LONG", "entry": 140.0, "opened_t": T0 + 2}]}
    F.step(st, T0 + 60_000, {"hedge": {"bn:2"}}, {"whales": desk}, {"BTC": 60000.0, "ETH": 3000.0, "SOL": 140.0})
    nh = [p for p in st["open"] if "nohedge" in p["spec"]]
    assert {p["coin"] for p in nh} == {"ETH"}
    assert any(p["coin"] == "SOL" and "nohedge" not in p["spec"] for p in st["open"])      # the plain stream still takes him


def test_futures_channel_desk_waits_for_its_zone_and_uses_its_own_stop_and_target():
    from cnq.agents import exp_factory as F, influencer as I
    page = ('<div data-post="c/5"><div class="tgme_widget_message_text js-message_text" dir="auto">#ETH/USDT SHORT<br>Entry: 3050 - 3100'
            '<br>Targets: 1) 2950 2) 2900<br>Stop: 3200</div><time datetime="2026-11-26T10:00:00+00:00"></time></div>')
    posts = I.parse_page(page)
    born = posts[0]["t"] - 1
    ev = F.fut_events(posts, {"ETH", "BTC"}, "B", born)
    assert ev["open"][0]["zone"] == [3050.0, 3100.0] and ev["open"][0]["side"] == "SHORT"
    st = {"born": born, "specs": [], "fut_budget": 1000.0}
    F.step(st, born, {}, {}, {"BTC": 60000.0})
    F.ensure_specs(st, F.fut_specs(["B", "C"]), born, "tgf")
    t = posts[0]["t"] + 60_000
    F.step(st, t, {}, {"tgf": ev}, {"ETH": 3000.0, "BTC": 60000.0})
    assert not [p for p in st["open"] if p["spec"].startswith("tgf|")] and st["pending"]     # below the zone: waits
    F.step(st, t + H, {}, {"tgf": ev}, {"ETH": 3060.0, "BTC": 60000.0})
    mine = [p for p in st["open"] if p["spec"].startswith("tgf|")]
    assert len(mine) == 2 and all(p["tp_px"] == 2950.0 and abs(p["stop_pct"] - (3200 / 3060 - 1) * 100) < 1e-3 for p in mine)
    assert all(abs(p["usd"] - 10.0 / ((3200 / 3060 - 1))) < 0.5 for p in mine)                # risk 1 % of $1000 at its stop
    F.step(st, t + 2 * H, {}, {"tgf": ev}, {"ETH": 2940.0, "BTC": 60000.0})
    assert len([c for c in st["closed"] if c["spec"].startswith("tgf|") and c["why"] == "его цель"]) == 2


def test_widened_pool_bn_okx_positions_feed_the_paper_source():
    from cnq.agents import exp_factory as F
    st = {}
    F.step(st, T0, {}, {}, {"BTC": 60000.0})
    new = F.ensure_watch_specs(st, ["bn:7", "okx:AB"], T0, src="wsw", family="широкий пул")
    assert len(new) == 5 and all(s.startswith("wsw|") for s in new)
    pos = {"bn:7": [], "okx:AB": []}
    book, d = F.hl_book(None, ["bn:7", "okx:AB"], lambda a: pos[a], T0, {})
    pos["bn:7"] = [{"coin": "SOL", "side": "LONG", "entry": 140.0}]
    book, d = F.hl_book(book, ["bn:7", "okx:AB"], lambda a: pos[a], T0 + 300_000, {"SOL": 140.0})
    F.step(st, T0 + 300_000, {}, {"wsw": d}, {"SOL": 140.0, "BTC": 60000.0})
    assert any(p["spec"].startswith("wsw|") and p["spec"].endswith("bn:7") and p["coin"] == "SOL" for p in st["open"])


def test_okx_positions_reader_for_the_widened_pool():
    from cnq.api import experiments as E
    rows = [{"instId": "SOL-USDT-SWAP", "posSide": "long", "subPos": "3", "openAvgPx": "140", "margin": "10", "lever": "5", "upl": "0"}]
    got = E.Experiments._ws_positions("okx:AB", lambda url: {"data": rows} if "uniqueCode=AB" in url else {"data": []})
    assert got == [{"coin": "SOL", "side": "LONG", "entry": 140.0}]


def test_real_channel_layouts_market_calls_and_listed_targets():
    from cnq.agents import fut_calls as F
    a = F.parse("🔥 VIP SIGNAL 🔥\n\n🔴 SOL/USD: SELL (market)\n\n🎯 Entry: 71.19\n\n🛑 Stop loss: 74.39\n\n✅ TP1: 67.99\n✅ TP2: 64.79\n\n"
                "⏱ Timeframe: 24h\n\n📊 Signal score: 86.5/100")
    assert a["side"] == "SHORT" and a["targets"] == [67.99, 64.79] and a["stop"] == 74.39            # «✅ TP1:» is a call, not a result
    b = F.parse("#AKE Short setup \n\nTp 0.010$-0.005$ \n\nUse 2X\n\nStop lose 0.013$")
    assert b["market"] and b["targets"] == [0.01, 0.005] and b["stop"] == 0.013
    assert F.parse("✅ XRP/USD SELL from 1.0625: TP1 HIT at 1.0266 (+3.4%). Trade closed.") is None


def test_nohedge_real_gate_owner_key_drops_flagged_traders_and_blocks_only_new_copies(monkeypatch):
    import inspect
    from cnq.agents import hedge_gate as HG, whale_study as W
    from cnq.execution import okx_futures as OF, owner_overrides as ov
    on = {HG.KEY: {"armed": True}}
    fl = {"bn:1": ["offset"]}
    assert HG.reason("bn:1", on, fl).startswith("хедж: двусторонняя книга")
    assert HG.reason("bn:2", on, fl) is None and HG.reason("bn:1", {}, fl) is None              # key off → nothing changes
    assert HG.armed(ov.load()) and "bn:5219234690161308161" in HG.flags()                       # the owner's «да» of 27.11 is on
    monkeypatch.setattr(ov, "load", lambda *a, **k: on)
    monkeypatch.setattr(HG, "flags", lambda path=None: fl)
    assert W.lock_hard("bn:1", {"positions": [], "pnl_60d": 5.0}, {}, None, 0, 0.0, 0)[0].startswith("хедж")
    assert W.lock_hard("bn:2", {"positions": [], "pnl_60d": 5.0}, {}, None, 0, 0.0, 0) == []
    src = inspect.getsource(OF)
    assert "HG.reason(t.get(\"whale\"), ov_all, hfl)" in src and "continue" in src.split("HG.reason(t.get(\"whale\")")[1][:300]
    assert "close" not in inspect.getsource(HG).split('"""', 2)[2]                              # the gate never closes anything
