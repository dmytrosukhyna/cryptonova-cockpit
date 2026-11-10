import json
import sqlite3

from cnq.agents import whale_study as W

H, M = W.H1, 60_000
T0 = 1_790_000_000_000


def _stop8(fn):
    """These tests pin the desk mechanics (sizing, joins, leverage vs liquidation) at the old −8 % protective stop; the stop
    itself (−16 % since 05.11) is pinned by test_protective_stop_is_16pct_and_keeps_liquidation_behind_it."""
    import functools

    @functools.wraps(fn)
    def run(*a, **k):
        old = W.DESK_STOP
        W.DESK_STOP = 0.08
        try:
            return fn(*a, **k)
        finally:
            W.DESK_STOP = old
    return run


def test_protective_stop_is_16pct_and_keeps_liquidation_behind_it():
    # 05.11 (owner «проверь, исследуй и сделай как можно лучше»; docs/research/whale-stops-2026-11-05.md)
    assert W.DESK_STOP == 0.16
    p, src = W.stop_for("LONG", 100.0)
    assert abs(p - 84.0) < 1e-9 and "−16%" in src
    p, src = W.stop_for("SHORT", 100.0)
    assert abs(p - 116.0) < 1e-9
    assert W.stop_for("LONG", 100.0, 95.0) == (95.0, "стоп кита")          # the whale's own stop still wins when it is shown
    assert int(W.LIQ_ROOM / W.DESK_STOP + 1e-9) == 5                        # 20× whale → 5× copy: liquidation never before the stop


def f(t, coin, side, px, sz, start, pnl=0.0, fee=0.0):
    return {"t": t, "coin": coin, "side": side, "px": px, "sz": sz, "dir": "", "start_pos": start, "closed_pnl": pnl, "fee": fee}


def test_round_trips_add_reduce_flip_and_unknown_start():
    fills = [
        f(T0 - 5 * H, "ETH", "A", 2000, 5, 5, 10),          # window starts with an open position → unknown, skipped
        f(T0 - 4 * H, "ETH", "A", 2010, 0.0001, 0.0),        # (tiny noise after flat: ETH known from here, opens short 0.0001)
        f(T0, "BTC", "B", 100, 1, 0, fee=0.1),                # open long 1 @100
        f(T0 + H, "BTC", "B", 110, 1, 1, fee=0.1),            # add → 2 @105
        f(T0 + 2 * H, "BTC", "A", 120, 1, 2, 15, 0.1),        # reduce 1 @120
        f(T0 + 3 * H, "BTC", "A", 130, 1, 1, 25, 0.1),        # close @130 → trip 1
        f(T0 + 4 * H, "BTC", "B", 100, 1, 0, fee=0.1),        # open long 1
        f(T0 + 5 * H, "BTC", "A", 90, 3, 1, -10, 0.3),        # close long, flip to short 2 @90
        f(T0 + 8 * H, "BTC", "B", 80, 2, -2, 20, 0.2),        # close short
    ]
    trips, total = W.round_trips(fills, since=T0)
    btc = [t for t in trips if t["coin"] == "BTC"]
    assert [t["side"] for t in btc] == ["LONG", "LONG", "SHORT"]
    a, b, c = btc
    assert abs(a["entry"] - 105) < 1e-9 and abs(a["exit"] - 125) < 1e-9 and a["hold_h"] == 3.0
    assert abs(a["pnl"] - (40 - 0.4)) < 1e-6 and a["ret_pct"] > 0
    assert b["pnl"] < 0 and abs(b["exit"] - 90) < 1e-9          # only 1 of the 3 sold closed the long
    assert abs(c["entry"] - 90) < 1e-9 and abs(c["exit"] - 80) < 1e-9 and c["pnl"] > 0 and c["hold_h"] == 3.0
    assert abs(total - (40 - 0.4 - 0.1 - 10 - 0.3 + 20 - 0.2)) < 1e-6       # only fills inside the window
    assert all(t["coin"] != "ETH" or t["open_t"] >= T0 - 4 * H for t in trips)


def candles(coin_px, start, n, step=0.0):
    out, px = [], coin_px
    for i in range(n):
        o = px; px = px * (1 + step); out.append([start + i * W.M15, o, max(o, px), min(o, px), px])
    return out


def test_copy_test_delay_misses_short_trips_and_prices_inside_bars():
    cs = {"BTC": candles(100.0, T0 - W.DAY, 400, 0.001)}         # steady uptrend
    trips = [{"coin": "BTC", "side": "LONG", "open_t": T0, "close_t": T0 + 10 * H, "ret_pct": 3.0},
             {"coin": "BTC", "side": "SHORT", "open_t": T0, "close_t": T0 + 20 * M, "ret_pct": 1.0},      # shorter than 30 min → missed
             {"coin": "XYZ", "side": "LONG", "open_t": T0, "close_t": T0 + 5 * H, "ret_pct": 1.0}]        # no candles
    r = W.copy_test(trips, cs, 30)
    assert r["n"] == 1 and r["missed"] == 1 and r["no_data"] == 1 and r["avg_pct"] > 0 and r["win"] == 1.0
    assert abs(W.price_at(cs["BTC"], cs["BTC"][5][0] + W.M15 // 2) - (cs["BTC"][5][1] + cs["BTC"][5][4]) / 2) < 1e-9
    assert W.price_at(cs["BTC"], T0 + 1000 * H) is None


def test_candidates_filter_drops_deposit_artefacts_and_market_makers():
    def row(addr, av, al, mo, roi, vlm):
        return {"ethAddress": addr, "accountValue": str(av), "displayName": None,
                "windowPerformances": [["day", {"pnl": "0", "roi": "0", "vlm": "0"}], ["week", {"pnl": "1", "roi": "0", "vlm": "0"}],
                                       ["month", {"pnl": str(mo), "roi": str(roi), "vlm": str(vlm)}], ["allTime", {"pnl": str(al), "roi": "1", "vlm": "0"}]]}
    lb = {"leaderboardRows": [row("0xgood", 2e6, 5e6, 3e5, 0.15, 1e8), row("0xart", 3.7e6, 3.7e6, 3.7e6, 37239.0, 1e6),
                              row("0xmm", 5e6, 9e6, 1e5, 0.02, 9e10), row("0xsmall", 1e5, 2e6, 1e5, 1.0, 1e6), row("0xneg", 2e6, 5e6, -1, -0.1, 1e6)]}
    rows, why = W.candidates(lb)
    assert [r["addr"] for r in rows] == ["0xgood"]
    assert why["roi_artefact"] == 1 and why["mm"] == 1 and why["small"] == 1 and why["month"] == 1


@_stop8
def test_desk_copies_only_new_positions_and_closes_with_the_whale():
    mids = {"BTC": "100", "ETH": "2000"}
    d = W.desk_step(None, ["0xa"], {"0xa": [{"coin": "ETH", "side": "LONG", "lev": 10, "entry": 1900}]}, mids, T0)
    assert d["open"] == [] and d["seen"]["0xa"] == {"ETH": "LONG"}           # already open when we started watching → not copied
    q = {"0xa": {"per_day": 40}}                         # ~16 copies still expected today → each takes 1/16 of the free margin
    d = W.desk_step(d, ["0xa"], {"0xa": [{"coin": "ETH", "side": "LONG", "lev": 10}, {"coin": "BTC", "side": "SHORT", "lev": 20}]}, mids, T0 + H, quality=q)
    # 05.11 14:12: risk-sized like the real account — 2 % of 10 000 from the 8 % stop = notional 2500, at 10× margin 250
    m = min(round(0.75 * 10_000 / W.expected_slots(q, ["0xa"], T0 + H), 2), 250.0)
    assert len(d["open"]) == 1 and d["open"][0]["coin"] == "BTC" and d["open"][0]["lev"] == 10.0 and d["open"][0]["margin"] == m
    assert d["util_pct"] == round(m / 100, 1)
    mids2 = {"BTC": "90", "ETH": "2000"}
    d = W.desk_step(d, ["0xa"], {"0xa": [{"coin": "ETH", "side": "LONG", "lev": 10}]}, mids2, T0 + 2 * H)
    assert d["open"] == [] and len(d["closed"]) == 1
    c = d["closed"][0]
    assert c["why"] == "кит закрыл" and abs(c["pnl"] - (10 * m * 0.1 - 10 * m * W.COST_PCT / 100)) < 0.01
    assert abs(d["equity"] - (10_000 + c["pnl"])) < 1e-6
    pub = W.desk_public(d, {"0xa": "Кит"})
    assert pub["trades"] == 1 and pub["wins"] == 1 and pub["closed"][0]["whale_ru"] == "Кит"


def test_run_end_to_end_with_fake_api():
    conn = sqlite3.connect(":memory:")
    W.time.sleep = lambda s: None
    now = T0 + 40 * W.DAY
    addr = "0x" + "a" * 40
    lb = {"leaderboardRows": [{"ethAddress": addr, "accountValue": "2000000", "displayName": "Ace",
                               "windowPerformances": [["day", {"pnl": "1"}], ["week", {"pnl": "1"}], ["month", {"pnl": "300000", "roi": "0.2", "vlm": "1e8"}],
                                                      ["allTime", {"pnl": "4000000"}]]}]}
    # 22 winning 4-hour longs on BTC in an uptrend, one every 2 days (04.11: ≥ 20 decisions in 60 days, hold ≥ 4 h)
    fl = []
    for k in range(22):
        t = now - 49 * W.DAY + k * 2 * W.DAY
        fl.append({"time": t, "coin": "BTC", "side": "B", "px": "100", "sz": "10", "startPosition": "0", "closedPnl": "0", "fee": "1", "tid": f"o{k}"})
        fl.append({"time": t + 4 * H, "coin": "BTC", "side": "A", "px": "103", "sz": "10", "startPosition": "10", "closedPnl": "30", "fee": "1", "tid": f"c{k}"})
    fl.append({"time": now - 3 * W.DAY, "coin": "@107", "side": "B", "px": "1", "sz": "1", "startPosition": "0", "closedPnl": "0", "fee": "0", "tid": "spot"})
    base = (now - 33 * W.DAY) // W.M15 * W.M15

    def post(body):
        t = body["type"]
        if t == "allMids":
            return {"BTC": "105", "kPEPE": "0.01"}
        if t == "userFillsByTime":
            return fl
        if t == "portfolio":
            return [["allTime", {"pnlHistory": [[now - 120 * W.DAY, "0"], [now - 90 * W.DAY, "1000000"], [now - 60 * W.DAY, "800000"],
                                                [now - 30 * W.DAY, "2000000"], [now, "4000000"]], "accountValueHistory": [[now, "2000000"]]}]]
        if t == "clearinghouseState":
            return {"assetPositions": [{"position": {"coin": "BTC", "szi": "5", "positionValue": "525", "entryPx": "104", "unrealizedPnl": "5",
                                                     "leverage": {"value": 3}}}]}
        if t == "candleSnapshot":
            out = []
            for i in range(3300):
                tt = base + i * W.M15
                ph = (i % 192) / 192                                  # saw-tooth: rises 100→103.5 over each 2 days
                o = 100 + 3.5 * ph; c = 100 + 3.5 * ((i + 1) % 192) / 192 if (i + 1) % 192 else o
                out.append({"t": tt, "o": str(o), "h": str(max(o, c)), "l": str(min(o, c)), "c": str(c)})
            return out
        raise AssertionError(t)

    def okx_get(path, params):
        if path.endswith("public-lead-traders"):
            return [{"ranks": [{"uniqueCode": "OKX1", "nickName": "Bull", "pnl": "50000", "pnlRatio": "1.2", "winRatio": "0.6", "copyTraderNum": "120",
                                "leadDays": "200", "aum": "300000"}]}]
        if path.endswith("public-subpositions-history"):
            return [{"subPosId": f"s{k}", "instId": "PEPE-USDT-SWAP", "posSide": "short", "openAvgPx": "0.00001", "closeAvgPx": "0.0000099",
                     "openTime": str(now - 20 * W.DAY + k * W.DAY), "closeTime": str(now - 20 * W.DAY + k * W.DAY + 2 * H), "pnl": "10", "margin": "100", "lever": "10"}
                    for k in range(3)]
        if path.endswith("public-current-subpositions"):
            return []
        raise AssertionError(path)

    W.ensure_schema(conn)                                     # a whale followed under the old rules, gone from the candidates
    W.kv_set(conn, "study", {"t": 0, "top": [{"addr": "0x" + "e" * 40, "short": "old", "name": "Old"}]})
    d = W.run(conn, now=now, post=post, get_lb=lambda: lb, okx_get=okx_get, max_requests=50, budget_s=60,
              src_get={"bn": lambda path, body=None: {"list": []}})
    assert d["complete"] and d["funnel"]["studied"] == 2
    assert [x["name"] for x in d["follow_dropped"]] == ["Old"] and d["follow_dropped"][0]["why_ru"] == ["не изучен в этом прогоне"]
    top = d["top"]
    assert len(top) == 1 and top[0]["addr"] == addr and top[0]["trips"] == 22 and top[0]["win"] == 1.0
    assert top[0]["style_ru"] == "интрадей" and top[0]["coins"][0]["coin"] == "BTC" and "BTC" in top[0]["desc_ru"]
    assert top[0]["copy"]["30"]["n"] >= 10 and top[0]["portfolio"]["months"] >= 3 and top[0]["lev_now"] == 3.0
    okx = next(o for o in d["others"] if o["src"] == "okx")
    assert okx["trips"] == 3 and "мало решений за 60 дней (3" in okx["excluded_ru"][0]
    assert d["following"] and d["following"][0]["addr"] == addr
    # next run: the desk starts watching the followed whale (no copy of positions it already had)
    d2 = W.run(conn, now=now + 1800_000, post=post, get_lb=lambda: lb, okx_get=okx_get, max_requests=50, budget_s=60,
              src_get={"bn": lambda path, body=None: {"list": []}})
    assert d2["desk"] and d2["desk"]["open"] == [] and d2["desk"]["equity"] == 10_000
    json.dumps(d2)


@_stop8
def test_desk_stops_per_whale_cap_paper_shape_and_daily_record():
    """04.11 (owner, real money): every copy has a stop (the whale's own when usable, else −8 %), one whale ≤ 30 % of the equity as
    margin, the copies are published in the paper-desk shape the real OKX sleeve follows, per-whale results by UTC day"""
    mids = {"BTC": "100", "ETH": "2000", "SOL": "50", "XRP": "1", "kPEPE": "0.01"}
    d = W.desk_step(None, ["0xa", "0xb"], {"0xa": [], "0xb": []}, mids, T0)
    pos = [{"coin": c, "side": "LONG", "lev": 3} for c in ("BTC", "ETH", "SOL", "XRP")]
    pos[0]["sl"] = 95.0                                                       # the whale's own stop, 5 % below
    q = {"0xa": {"per_day": 20}, "0xb": {"per_day": 20}}
    d = W.desk_step(d, ["0xa", "0xb"], {"0xa": pos, "0xb": [{"coin": "kPEPE", "side": "SHORT", "lev": 20}]}, mids, T0 + H, quality=q)
    mine = [o for o in d["open"] if o["whale"] == "0xa"]
    assert len(mine) == 4 and sum(o["margin"] for o in mine) <= 0.5 * 10_000
    btc = next(o for o in d["open"] if o["coin"] == "BTC")
    assert btc["stop"] == 95.0 and btc["stop_src"] == "стоп кита"
    eth = next(o for o in d["open"] if o["coin"] == "ETH")
    assert abs(eth["stop"] - 2000 * 0.92) < 1e-6 and "−8%" in eth["stop_src"]
    pepe = next(o for o in d["open"] if o["coin"] == "kPEPE")
    assert pepe["lev"] == 10.0 and pepe["lev_note"] and abs(pepe["stop"] - 0.0108) < 1e-9
    # the stop closes a copy at the current mid even while the whale still holds
    d = W.desk_step(d, ["0xa", "0xb"], {"0xa": pos, "0xb": [{"coin": "kPEPE", "side": "SHORT", "lev": 20}]}, {**mids, "ETH": "1800"}, T0 + 2 * H, quality=q)
    assert not any(o["coin"] == "ETH" for o in d["open"]) and d["closed"][-1]["why"].startswith("стоп")
    pub = W.desk_public(d, {"0xa": "Кит А"})
    p = pub["paper"]
    t = next(x for x in p["open"] if x["symbol"] == "1000PEPE")
    assert t["side"] == "SHORT" and t["copy_lev"] == 10.0 and t["stop"] > t["entry"] and abs(t["risk_usd"] - t["qty"] * (t["stop"] - t["entry"])) < 0.01
    assert all(x["id"].startswith("W-") and x["stop"] for x in p["open"]) and p["closed"][0]["net_usd"] < 0
    bw = {w["whale"]: w for w in pub["by_whale"]}
    assert bw["0xa"]["trades"] == 1 and bw["0xa"]["whale_ru"] == "Кит А" and bw["0xa"]["days"][0]["n"] == 1 and bw["0xb"]["open"] == 1


def test_a_whale_whose_positions_okx_hides_is_neither_closed_nor_copied():
    """04.11: 30.10 the top OKX lead traders hid their positions (no instId / size / entry): our copy must not be closed as
    «кит закрыл» because the coin vanished from the feed, and nothing new is opened from a hidden feed"""
    mids = {"BTC": "100"}
    d = W.desk_step(None, ["0xa"], {"0xa": []}, mids, T0)
    d = W.desk_step(d, ["0xa"], {"0xa": [{"coin": "BTC", "side": "SHORT", "lev": 20}]}, mids, T0 + H)
    assert len(d["open"]) == 1
    d = W.desk_step(d, ["0xa"], {"0xa": [{"coin": "", "side": "SHORT", "lev": 20, "notional": 5e5}]}, mids, T0 + 2 * H)
    assert len(d["open"]) == 1 and d["closed"] == [] and d["events"][-1]["kind"] == "hidden"
    assert W.hidden([{"coin": ""}]) and not W.hidden([{"coin": "BTC"}]) and not W.hidden([])


def test_owner_override_closes_the_copy_and_blocks_new_shorts_of_that_coin():
    """04.11 (owner): the «Киты» desk closes its HYPE short copy and never re-opens a HYPE short while the override lasts"""
    from cnq.execution import owner_overrides as ov
    mids = {"HYPE": 85.0, "ETH": 2000.0}
    d = W.desk_step(None, ["0xa"], {"0xa": []}, mids, T0)
    d = W.desk_step(d, ["0xa"], {"0xa": [{"coin": "HYPE", "side": "SHORT", "lev": 8}]}, mids, T0 + H)
    assert [o["coin"] for o in d["open"]] == ["HYPE"]
    a = ov.active({"set": T0 + 2 * H, "until": T0 + 30 * H, "close": ["HYPE"], "no_short": ["HYPE"], "note_ru": "тест"}, T0 + 2 * H)
    d = W.desk_step(d, ["0xa"], {"0xa": [{"coin": "HYPE", "side": "SHORT", "lev": 8}]}, {**mids, "HYPE": 88.0}, T0 + 2 * H, a)
    assert d["open"] == [] and "решению владельца" in d["closed"][-1]["why"]
    d = W.desk_step(d, ["0xa"], {"0xa": []}, mids, T0 + 3 * H, a)
    d = W.desk_step(d, ["0xa"], {"0xa": [{"coin": "HYPE", "side": "SHORT", "lev": 8}, {"coin": "ETH", "side": "LONG", "lev": 3}]}, mids, T0 + 4 * H, a)
    assert [o["coin"] for o in d["open"]] == ["ETH"]


def test_a_stale_look_at_a_whale_is_only_a_baseline_no_late_copies():
    """04.11: SilkBtc came back into the follow list with a days-old «seen» → his old HYPE/ZRO shorts were copied as «new»"""
    mids = {"HYPE": 85.0, "ZRO": 1.9, "ETH": 2000.0}
    d = W.desk_step(None, ["0xa"], {"0xa": []}, mids, T0)
    d = W.desk_step(d, [], {}, mids, T0 + H)                                     # not followed / not looked at for a day
    d = W.desk_step(d, ["0xa"], {"0xa": [{"coin": "HYPE", "side": "SHORT", "lev": 8}]}, mids, T0 + 26 * H)
    assert d["open"] == []                                                       # a stale look → baseline only
    d = W.desk_step(d, ["0xa"], {"0xa": [{"coin": "HYPE", "side": "SHORT", "lev": 8}, {"coin": "ETH", "side": "LONG", "lev": 3}]}, mids, T0 + 26 * H + H // 2)
    assert [o["coin"] for o in d["open"]] == ["ETH"]                             # a really new position is copied


@_stop8
def test_join_an_already_open_position_only_within_1pct_of_the_whale_entry():
    """04.11 17:37 (owner: «Входить и в их открытые»): a followed whale's open position is joined while our price is ≤ 1 % worse
    than his own entry; 1.5 % worse → wait (logged once), joined later if the price comes back"""
    pos = [{"coin": "ETH", "side": "LONG", "lev": 10, "entry": 2000.0}, {"coin": "SOL", "side": "LONG", "lev": 3, "entry": 100.0}]
    d = W.desk_step(None, ["0xa"], {"0xa": pos}, {"ETH": 2015.0, "SOL": 101.5}, T0)
    assert d["open"] == []                                                    # the first look is a baseline
    d = W.desk_step(d, ["0xa"], {"0xa": pos}, {"ETH": 2015.0, "SOL": 101.5}, T0 + H // 2, quality={"0xa": {"per_day": 30}})
    assert [o["coin"] for o in d["open"]] == ["ETH"]                          # 0.75 % → joined; 1.5 % → not
    eth = d["open"][0]
    assert eth["join"] and eth["gap_pct"] == 0.75 and eth["lev"] == 10.0 and abs(eth["stop"] - 2015 * 0.92) < 1e-6
    ev = [e for e in d["events"] if e["kind"] == "join"]
    assert ev and ev[0]["gap_pct"] == 0.75 and ev[0]["whale_entry"] == 2000.0
    assert sum(e["kind"] == "join_wait" for e in d["events"]) == 1 and d["join_wait"] == {"0xa|SOL|LONG": 1.5}
    d = W.desk_step(d, ["0xa"], {"0xa": pos}, {"ETH": 2015.0, "SOL": 101.5}, T0 + H)
    assert sum(e["kind"] == "join_wait" for e in d["events"]) == 1             # not logged again
    d = W.desk_step(d, ["0xa"], {"0xa": pos}, {"ETH": 2015.0, "SOL": 100.4}, T0 + 3 * H // 2, quality={"0xa": {"per_day": 30}})
    assert sorted(o["coin"] for o in d["open"]) == ["ETH", "SOL"] and d["join_wait"] == {}
    t = next(x for x in W.desk_public(d, {})["paper"]["open"] if x["symbol"] == "SOL")
    assert t["join"] and t["gap_pct"] == 0.4 and t["whale_entry"] == 100.0


def test_join_a_short_and_no_join_without_the_whale_entry():
    pos = [{"coin": "BTC", "side": "SHORT", "lev": 4, "entry": 100.0}, {"coin": "ETH", "side": "SHORT", "lev": 4, "entry": 2000.0},
           {"coin": "XRP", "side": "SHORT", "lev": 4}]
    mids = {"BTC": 99.2, "ETH": 1975.0, "XRP": 1.0}                         # BTC 0.8 % below his short entry, ETH 1.25 % below
    d = W.desk_step(None, ["0xa"], {"0xa": pos}, mids, T0)
    d = W.desk_step(d, ["0xa"], {"0xa": pos}, mids, T0 + H // 2)
    assert [o["coin"] for o in d["open"]] == ["BTC"] and d["open"][0]["side"] == "SHORT" and d["open"][0]["gap_pct"] == 0.8
    assert d["open"][0]["stop"] > 99.2
    assert W.join_gap("SHORT", 101.0, 100.0) < 0 and abs(W.join_gap("LONG", 101.0, 100.0) - 0.01) < 1e-12


@_stop8
def test_stale_look_is_a_baseline_then_the_next_fresh_look_joins_within_1pct():
    pos = [{"coin": "ZRO", "side": "SHORT", "lev": 8, "entry": 1.90}]
    d = W.desk_step(None, ["0xa"], {"0xa": []}, {"ZRO": 1.9}, T0)
    d = W.desk_step(d, [], {}, {"ZRO": 1.9}, T0 + H)
    d = W.desk_step(d, ["0xa"], {"0xa": pos}, {"ZRO": 1.89}, T0 + 26 * H)        # stale look → baseline only
    assert d["open"] == []
    d = W.desk_step(d, ["0xa"], {"0xa": pos}, {"ZRO": 1.89}, T0 + 26 * H + H // 2)
    assert [(o["coin"], o["join"], o["lev"]) for o in d["open"]] == [("ZRO", True, 8.0)]


def test_join_refuses_an_owner_blocked_hype_short_and_prefers_the_strong_whale():
    from cnq.execution import owner_overrides as ov
    a = ov.active({"set": T0 - H, "until": T0 + 30 * H, "close": ["HYPE"], "no_short": ["HYPE"], "note_ru": "тест"}, T0)
    hype = [{"coin": "HYPE", "side": "SHORT", "lev": 8, "entry": 85.0}]
    d = W.desk_step(None, ["0xa"], {"0xa": hype}, {"HYPE": 84.9}, T0, a)
    d = W.desk_step(d, ["0xa"], {"0xa": hype}, {"HYPE": 84.9}, T0 + H // 2, a)
    assert d["open"] == [] and any(e["kind"] == "skip" and "HYPE" in e["why"] for e in d["events"])
    # two whales hold ETH long, both within 1 %: one coin = one copy → the whale with ≥ 70 % wins and PF ≥ 1.5 gets it
    q = {"0xweak": {"win": 0.55, "pf": 1.3}, "0xsilk": {"win": 0.77, "pf": 2.1}}
    st_ = {"0xweak": [{"coin": "ETH", "side": "LONG", "lev": 3, "entry": 2010.0}], "0xsilk": [{"coin": "ETH", "side": "LONG", "lev": 3, "entry": 1995.0}]}
    d = W.desk_step(None, ["0xweak", "0xsilk"], st_, {"ETH": 2005.0}, T0, None, q)
    d = W.desk_step(d, ["0xweak", "0xsilk"], st_, {"ETH": 2005.0}, T0 + H // 2, None, q)
    assert [o["whale"] for o in d["open"]] == ["0xsilk"]


def test_no_join_when_the_whale_is_down_more_than_3pct_on_price():
    """04.11 17:41 (owner: «цена откатилась в другую сторону — возможно, тренд уже изменился»): join window is 3 % better … 1 % worse"""
    pos = [{"coin": "ETH", "side": "LONG", "lev": 3, "entry": 2000.0}, {"coin": "BTC", "side": "SHORT", "lev": 3, "entry": 100.0},
           {"coin": "SOL", "side": "LONG", "lev": 3, "entry": 100.0}]
    mids = {"ETH": 1930.0, "BTC": 103.5, "SOL": 97.5}          # ETH long −3.5 %, BTC short −3.5 % → no; SOL long −2.5 % → join
    d = W.desk_step(None, ["0xa"], {"0xa": pos}, mids, T0)
    d = W.desk_step(d, ["0xa"], {"0xa": pos}, mids, T0 + H // 2)
    assert [(o["coin"], o["gap_pct"]) for o in d["open"]] == [("SOL", -2.5)]
    w = [e for e in d["events"] if e["kind"] == "join_wait"]
    assert sorted(e["coin"] for e in w) == ["BTC", "ETH"] and all("в минусе" in e["why"] for e in w)


# ------------------------------------------------------------------------------------------------ 04.11 17:46 whale selection (60 days)
def _trip(t0, coin="BTC", side="LONG", hold_h=6.0, pnl=100.0, open_=False, now=None, hidden=False, notional=1000.0):
    ct = now if open_ else t0 + int(hold_h * H)
    x = {"coin": "" if hidden else coin, "side": side, "open_t": t0, "close_t": ct, "hold_h": round((ct - t0) / H, 2), "entry": 100.0,
         "exit": 101.0 if pnl > 0 else 99.0, "ret_pct": None if hidden else (1.0 if pnl > 0 else -1.0), "notional": notional, "pnl": pnl, "fills": 1}
    if open_:
        x["open"] = True
    if hidden:
        x["hidden"] = True
    return x


def _prof(trips, now):
    return W.profile({"addr": "okx:X", "src": "okx", "name": "X"}, trips, sum(t["pnl"] for t in trips if not t.get("open")), {}, [], {}, now, False)


def test_parts_of_one_trade_within_4h_are_one_decision():
    now = T0 + 70 * W.DAY
    t0 = now - 10 * W.DAY
    parts = [_trip(t0), _trip(t0 + 1 * H), _trip(t0 + 3 * H, pnl=-20.0), _trip(t0 + 5 * H)]   # 3 within 4 h of the first + 1 later
    d = W.decisions(parts)
    assert len(d) == 2 and d[0]["parts"] == 3 and d[0]["pnl"] == 180.0 and d[1]["parts"] == 1
    assert len(W.decisions([_trip(t0), _trip(t0 + H, side="SHORT")])) == 2          # another side = another decision


def test_a_good_record_passes_and_ranks_by_pf_including_open():
    now = T0 + 70 * W.DAY
    tr = [_trip(now - 55 * W.DAY + k * 2 * W.DAY, pnl=100.0 if k % 5 else -50.0) for k in range(25)]
    p = _prof(tr, now)
    assert p["decisions"] == 25 and p["win"] == 0.8 and p["pf"] == 8.0 and p["hold_med_h"] == 6.0 and p["eligible"], p["excluded_ru"]
    assert p["score"] == p["pf"] and p["window_days"] == 60
    # trades older than 60 days do not count
    old = [_trip(now - 70 * W.DAY + k * H * 5, pnl=100.0) for k in range(5)]
    assert _prof(tr + old, now)["decisions"] == 25


def test_hidden_losers_in_open_positions_are_losses_and_exclude_the_whale():
    """AGD88888 pattern (research 04.11): 90 % of closed trades in profit, but many open positions in loss"""
    now = T0 + 70 * W.DAY
    tr = [_trip(now - 50 * W.DAY + k * 2 * W.DAY, pnl=100.0 if k % 10 else -50.0) for k in range(20)]      # 18 wins, 2 losses
    p0 = _prof(tr, now)
    assert p0["eligible"] and p0["win"] == 0.9
    hid = [_trip(now - 5 * W.DAY + k * H, pnl=-60.0, open_=True, now=now, hidden=True) for k in range(8)]
    p = _prof(tr + hid, now)
    assert p["decisions"] == 28 and abs(p["win"] - 18 / 28) < 1e-3 and p["win_closed"] == 0.9 and p["hidden_loss_n"] == 8
    ex = " ".join(p["excluded_ru"])
    assert not p["eligible"] and "прячет убытки" in ex and "нужно от 70%" in ex
    # a hidden position in profit is not counted as a decision (we cannot see what it is)
    assert _prof(tr + [_trip(now - W.DAY, pnl=50.0, open_=True, now=now, hidden=True)], now)["decisions"] == 20


def test_visible_open_losses_over_20pct_of_closed_profit_exclude():
    now = T0 + 70 * W.DAY
    tr = [_trip(now - 50 * W.DAY + k * 2 * W.DAY, pnl=100.0) for k in range(22)]               # closed profit $2200
    small = [_trip(now - 2 * W.DAY, coin="ETH", pnl=-400.0, open_=True, now=now)]              # 18 % → passes
    big = [_trip(now - 2 * W.DAY, coin="ETH", pnl=-500.0, open_=True, now=now)]                # 23 % → out
    assert _prof(tr + small, now)["eligible"]
    p = _prof(tr + big, now)
    assert not p["eligible"] and any("прячет убытки" in w for w in p["excluded_ru"]) and p["open_loss"] == 500.0


def test_short_holds_pass_and_thin_records_are_dropped():
    now = T0 + 70 * W.DAY
    sc = _prof([_trip(now - 50 * W.DAY + k * W.DAY, hold_h=1.5) for k in range(30)], now)
    assert sc["eligible"], sc["excluded_ru"]                 # 04.11 22:35 (owner): no hold rule — copying takes ~1–2 minutes
    th = _prof([_trip(now - 50 * W.DAY + k * 2 * W.DAY) for k in range(12)], now)
    assert not th["eligible"] and any("мало решений за 60 дней (12" in w for w in th["excluded_ru"])
    lowpf = _prof([_trip(now - 50 * W.DAY + k * 2 * W.DAY, pnl=100.0 if k % 4 else -200.0) for k in range(24)], now)  # 75 %, PF 1.5
    assert not lowpf["eligible"] and any("прибыль/убыток 1.5" in w for w in lowpf["excluded_ru"])


def test_okx_hidden_sub_positions_become_hidden_open_trips():
    now = T0 + 70 * W.DAY
    st_ = {"okx": [{"instId": "BTC-USDT-SWAP", "posSide": "long", "openAvgPx": "100", "markPx": "99", "openTime": str(now - 5 * H), "margin": "10",
                    "lever": "10", "upl": "-1"},
                   {"instId": "", "posSide": "long", "openAvgPx": "", "markPx": "", "openTime": "", "margin": "50", "lever": "20", "upl": "-30"}]}
    tr = W.okx_open_trips(st_, now)
    assert len(tr) == 2 and tr[0]["coin"] == "BTC" and not tr[0].get("hidden") and tr[1]["hidden"] and tr[1]["pnl"] == -30.0


def test_daily_archive_writes_each_closed_trade_once(tmp_path):
    conn = sqlite3.connect(":memory:")
    W.ensure_schema(conn)
    now = T0 + 70 * W.DAY
    rows = [("okx", "okx:A", f"s{k}", "BTC", "LONG", now - (10 - k) * W.DAY, now - (10 - k) * W.DAY + 6 * H, 100.0, 101.0, 10.0, 1000.0, 10.0)
            for k in range(5)]
    conn.executemany("INSERT INTO ws_trips VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.execute("INSERT INTO ws_whale (addr, lb, state) VALUES (?,?,?)", ("okx:A", "{}", json.dumps(
        {"okx": [{"instId": "ETH-USDT-SWAP", "posSide": "short", "openAvgPx": "2000", "margin": "100", "lever": "5", "upl": "3", "subPos": "-1"}]})))
    cands = [{"addr": "okx:A", "name": "Alpha", "src": "okx"}]
    a = W.archive_trades(conn, now, cands, str(tmp_path))
    assert a["closed"] == 5 and a["open"] == 1
    assert W.archive_trades(conn, now + H, cands, str(tmp_path)) is None                   # once per UTC day
    W.kv_set(conn, "archive_v", 1)                                                           # an old version → everything again
    assert W.archive_trades(conn, now + 2 * H, cands, str(tmp_path))["closed"] == 5
    for f_ in tmp_path.iterdir():
        f_.unlink()
    conn.execute("INSERT INTO ws_trips VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("okx", "okx:A", "s9", "SOL", "SHORT", now, now + 5 * H, 10.0, 9.0, 5.0, 100.0, 5.0))
    b = W.archive_trades(conn, now + W.DAY, cands, str(tmp_path))
    assert b["closed"] == 1                                                                  # only the new one
    lines = [json.loads(x) for f in sorted(tmp_path.iterdir()) for x in f.read_text().splitlines()]
    closed = [x for x in lines if x["kind"] == "closed"]
    assert len(closed) == 1 and closed[0]["id"] == "s9" and closed[0]["name"] == "Alpha"
    assert {x["coin"] for x in lines if x["kind"] == "open"} == {"ETH"} and len(list(tmp_path.iterdir())) == 1


@_stop8
def test_dynamic_sizing_free_margin_over_expected_slots_and_the_soft_whale_cap():
    """04.11 22:38 (owner: «в основном бюджет должен работать»): a copy takes free ÷ the copies still expected today (≥ 2); one whale
    at most 50 %; an active whale may use more than 1/N while the others are quiet"""
    mids = {"BTC": "100", "ETH": "2000", "SOL": "50"}
    fol = ["0xa", "0xb", "0xc", "0xd", "0xe"]
    d = W.desk_step(None, fol, {a: [] for a in fol}, mids, T0)
    pos = [{"coin": c, "side": "LONG", "lev": 3} for c in ("BTC", "ETH", "SOL")]
    d = W.desk_step(d, fol, {"0xa": pos, **{a: [] for a in fol[1:]}}, mids, T0 + H)          # no record → 2 slots: 50 %, then the cap
    a = [o for o in d["open"] if o["whale"] == "0xa"]
    # 05.11 14:12: each copy is risk-sized (2 % of equity from the 8 % stop at 3× → margin 833.33), so all three fit
    assert [o["margin"] for o in a] == [833.33] * 3
    assert d["util_pct"] == 25.0 and W.desk_public(d, {})["util_pct"] == 25.0 and W.desk_public(d, {})["util_cap_pct"] == 75
    assert W.expected_slots({x: {"per_day": 1} for x in fol}, fol, T0 - T0 % W.DAY) == 5
    assert W.expected_slots({}, fol, T0) == W.MIN_SLOTS


def test_a_rolled_back_database_never_republishes_an_older_desk_or_follow_list(tmp_path):
    """04.11 (19:28 UTC: a lost cache → a 15-day-old database republished an old «Киты» desk and follow list that the REAL account
    follows): the database's desk / follow list are used only when at least as new as the published whales_top.json"""
    now = T0 + 40 * W.DAY
    conn = sqlite3.connect(":memory:")
    W.ensure_schema(conn)
    old = {"start": 10000.0, "cash": 10141.0, "open": [{"whale": "okx:OLD", "coin": "BTC", "side": "SHORT", "entry": 1.0, "opened_t": now - 15 * W.DAY,
                                                     "margin": 1.0, "lev": 5.0, "notional": 5.0, "stop": 1.1}],
           "closed": [], "seen": {}, "history": [[now - 15 * W.DAY, 10141.0]], "events": [], "by_whale_day": {}, "started": now - 20 * W.DAY}
    W.kv_set(conn, "desk", old)
    W.kv_set(conn, "study", {"t": now - 15 * W.DAY, "top": [{"addr": "okx:OLD", "name": "Old"}]})
    good_open = {"whale": "okx:NEW", "coin": "ETH", "side": "LONG", "entry": 2500.0, "opened_t": now - W.DAY, "margin": 1000.0, "lev": 5.0,
                 "notional": 5000.0, "stop": 2300.0, "upnl": 50.0, "whale_ru": "New"}
    pub = {"asof": now - 3600_000, "study_t": now - 7200_000, "following": [{"addr": "okx:NEW", "name": "New"}], "follow_dropped": [],
           "desk": {"start": 10000.0, "equity": 11293.0, "started": now - 20 * W.DAY, "open": [good_open], "closed": [],
                    "history": [[now - 3600_000, 11293.0]]}}
    p = tmp_path / "whales_top.json"
    p.write_text(json.dumps(pub))
    d = W.run(conn, now=now, post=lambda body: {"BTC": "1", "ETH": "2500"} if body["type"] == "allMids" else {"assetPositions": []},
              get_lb=lambda: {"leaderboardRows": []}, okx_get=lambda path, q: [{"ranks": []}] if "lead-traders" in path else [],
              max_requests=5, budget_s=5, src_get={"bn": lambda path, body=None: {"list": []}}, pub_path=str(p))
    assert [x["addr"] for x in d["following"] if not x.get("owner_pick")] == ["okx:NEW"]
    assert d["desk"]["equity"] >= 11000 and [o["coin"] for o in d["desk"]["open"]] in (["ETH"], [])
    assert not any(o["whale"] == "okx:OLD" for o in d["desk"]["open"])
    assert any("старее опубликованного" in n for n in d["notes_ru"])
    # a database that is newer than the published file keeps its own state
    desk2, study2, notes = W.never_backwards({**old, "history": [[now, 12000.0]]}, {"t": now, "top": []}, pub, now)
    assert desk2["history"][-1][1] == 12000.0 and study2 == {"t": now, "top": []} and not notes


@_stop8
def test_hidden_leverage_uses_the_whales_median_and_utilisation_adapts_to_the_14_day_record():
    """05.11 09:52 (owner): «Какое у них плечо, такое и у нас» — hidden leverage → the median of his record; utilisation up to 90 %
    while the 14-day record is good (PF > 1.5, drawdown < 5 %), 50 % when it is bad"""
    mids = {"BTC": "100", "ETH": "2000"}
    q = {"0xa": {"per_day": 1, "lev_med": 7.0}}
    d = W.desk_step(None, ["0xa"], {"0xa": []}, mids, T0)
    d = W.desk_step(d, ["0xa"], {"0xa": [{"coin": "BTC", "side": "LONG"}]}, mids, T0 + H, quality=q)
    o = d["open"][0]
    assert o["lev"] == 7.0 and o["lev_src"] == "медиана кита" and o["lev_note"] is None
    good = {"closed": [{"closed_t": T0, "pnl": 100.0}] * 6 + [{"closed_t": T0, "pnl": -50.0}], "history": [[T0, 10000.0], [T0 + 1, 10400.0]]}
    bad = {"closed": [{"closed_t": T0, "pnl": -100.0}] * 6 + [{"closed_t": T0, "pnl": 50.0}], "history": [[T0, 10000.0], [T0 + 1, 9000.0]]}
    assert W.util_cap(good, T0 + H)[0] == 0.90 and W.util_cap(bad, T0 + H)[0] == 0.50
    assert W.util_cap({"closed": [], "history": []}, T0)[0] == 0.75
    assert d["util_cap_pct"] == 75 and "мало сделок" in d["util_rule"]["why_ru"]


def test_util_counts_risk_sized_margin_so_old_oversized_copies_do_not_block_new_ones_and_owner_closed_copies_stay_closed():
    """05.11 14:12 (owner: «$3 000 лежит»): util 97 % from 9 old copies of 10–13 % margin each, while the real account held 23 % —
    utilisation is the margin a copy needs at 2 % risk; a copy the owner closed by hand is closed on the desk and never re-joined"""
    old = [{"whale": "0xa", "coin": c, "side": "LONG", "entry": 100.0, "stop": 92.0, "lev": 5.0, "margin": 1100.0, "notional": 5500.0,
            "opened_t": T0} for c in ("A", "B", "C", "D", "E", "F", "G", "H", "I")]
    assert round(sum(W.risk_margin(o, 11000.0) for o in old) / 11000 * 100, 1) == 45.0       # not 90 %
    mids = {c: "100" for c in "ABCDEFGHIJ"}
    d = {"start": 10000.0, "cash": 11000.0, "open": old, "closed": [], "seen": {"0xa": {c: "LONG" for c in "ABCDEFGHI"}},
         "seen_t": {"0xa": T0}, "history": [], "events": [], "by_whale_day": {}, "started": T0}
    pos = [{"coin": c, "side": "LONG", "lev": 5} for c in "ABCDEFGHIJ"]
    hid = W.paper_trade(old[0])["id"]
    owner = {"close": set(), "no_short": set(), "no_long": set(), "set": 0, "closed_ids": {hid}}
    d = W.desk_step(d, ["0xa"], {"0xa": pos}, mids, T0 + 1800_000, owner, {"0xa": {"per_day": 1}})
    assert "J" in {o["coin"] for o in d["open"]}                                              # a new copy fits again
    assert "A" not in {o["coin"] for o in d["open"]} and d["closed"][-1]["why"] == "закрыта владельцем вручную на OKX"
    d = W.desk_step(d, ["0xa"], {"0xa": pos}, mids, T0 + 3600_000, owner, {"0xa": {"per_day": 1}})
    assert "A" not in {o["coin"] for o in d["open"]}                                          # not re-joined


def _w(a, pf=5.0):
    return {"addr": a, "short": a, "name": a, "src": "bn", "win": 0.9, "pf": pf, "pnl_60d": 1000.0}


def test_the_list_is_fixed_for_3_days_and_a_whale_leaves_only_for_a_hard_reason():
    now = 1_800_000_000_000
    ranked = [_w(f"bn:{i}") for i in range(10)]
    prof = {x["addr"]: {**x, "positions": []} for x in ranked}
    top, why, lock_t, passing = W.lock_follow({}, ranked, prof, {"equity": 7000.0}, now)       # the first lock: the best 7 now
    assert [x["addr"] for x in top] == [f"bn:{i}" for i in range(7)] and lock_t == now and not why
    st = {"rule": W.LOCK_RULE, "top": top, "lock_t": lock_t, "passing": passing, "joined": {x["addr"]: now for x in top}}
    # 12 h later the ranking is different and bn:0 is not studied at all: nobody leaves (no «не изучен», no PF shuffle)
    later = now + W.DAY // 2
    re = [_w(f"bn:{i}") for i in (9, 8, 7, 6, 5, 4, 3, 2, 1)]
    top2, why2, lt2, p2 = W.lock_follow(st, re, {x["addr"]: {**x, "positions": []} for x in re}, {"equity": 7000.0}, later)
    assert [x["addr"] for x in top2] == [x["addr"] for x in top] and lt2 == now and not why2
    # hard reason: 60 days negative. 09.11 (owner): copies on our desk down > 10 % of his share (7000 / 7 = 1000 → −$110 here) is
    # NOT a reason any more — he stays until the review
    bad = {x["addr"]: {**x, "positions": []} for x in re}
    bad["bn:1"]["pnl_60d"] = -5.0
    desk = {"equity": 7000.0, "closed": [{"whale": "bn:2", "pnl": -80.0, "closed_t": later}], "open": [{"whale": "bn:2", "upnl": -30.0}]}
    top3, why3, _, _ = W.lock_follow({**st, "passing": p2}, re, bad, desk, later + 1)
    assert set(why3) == {"bn:1"} and "ушёл в минус" in why3["bn:1"][0]
    assert {x["addr"] for x in top3} == {f"bn:{i}" for i in (0, 2, 3, 4, 5, 6)}


def test_at_the_review_a_newcomer_needs_3_days_in_a_row_in_the_bar():
    now = 1_800_000_000_000
    old = [_w(f"bn:{i}") for i in range(7)]
    passing = {x["addr"]: [now - 4 * W.DAY, now - 600_000] for x in old}
    passing["bn:new_old"] = [now - 4 * W.DAY, now - 600_000]          # passed the bar for 4 days
    passing["bn:new_fresh"] = [now - W.DAY, now - 600_000]             # only for 1 day
    st = {"rule": W.LOCK_RULE, "top": old, "lock_t": now - 3 * W.DAY, "passing": passing}
    ranked = [_w("bn:new_fresh", 50), _w("bn:new_old", 40)] + [_w(f"bn:{i}") for i in range(1, 7)]   # bn:0 studied and failed
    prof = {x["addr"]: {**x, "positions": []} for x in ranked + [_w("bn:0")]}
    top, why, lock_t, _ = W.lock_follow(st, ranked, prof, None, now)
    assert lock_t == now and "bn:0" not in {x["addr"] for x in top}
    assert [x["addr"] for x in top] == [f"bn:{i}" for i in range(1, 7)] + ["bn:new_old"]


def test_a_leavers_copies_run_to_his_exit_and_only_blind_ones_close():
    """09.11 (owner, backlog 117): a whale who left the fixed list is not copied any more, but copies he already holds run to his own
    exit (or our stop); only a blind copy closes — he hides his positions, or we have not seen them for UNFOLLOW_BLIND_MS"""
    now = 1_800_000_000_000
    o = {"whale": "bn:gone", "coin": "SOL", "side": "SHORT", "entry": 100.0, "notional": 500.0, "margin": 100.0, "opened_t": now - W.DAY,
         "stop": 120.0}
    d = {"start": 10000.0, "cash": 10000.0, "open": [dict(o)], "closed": [], "seen": {"bn:gone": {"SOL": "SHORT"}}, "history": [],
         "started": now - 2 * W.DAY, "seen_t": {"bn:gone": now - 60_000}}
    cp = lambda: json.loads(json.dumps(d))  # noqa: E731
    pos = [{"coin": "SOL", "side": "SHORT", "entry": 101.0}]
    held = W.desk_step(cp(), ["bn:a"], {"bn:gone": pos}, {"SOL": 90.0}, now, close_unfollowed=True)
    assert [x["coin"] for x in held["open"]] == ["SOL"]                                      # he still holds it → so do we
    gone = W.desk_step(cp(), ["bn:a"], {"bn:gone": []}, {"SOL": 90.0}, now, close_unfollowed=True)
    assert not gone["open"] and gone["closed"][-1]["why"] == "кит закрыл" and gone["closed"][-1]["pnl"] > 0
    hid = W.desk_step(cp(), ["bn:a"], {"bn:gone": [{"coin": None}]}, {"SOL": 90.0}, now, close_unfollowed=True)
    assert not hid["open"] and "скрыл позиции" in hid["closed"][-1]["why"]
    short_blind = W.desk_step(cp(), ["bn:a"], {}, {"SOL": 90.0}, now, close_unfollowed=True)
    assert [x["coin"] for x in short_blind["open"]] == ["SOL"]                               # one missed poll is not blind
    late = cp(); late["seen_t"]["bn:gone"] = now - W.UNFOLLOW_BLIND_MS - 1
    blind = W.desk_step(late, ["bn:a"], {}, {"SOL": 90.0}, now, close_unfollowed=True)
    assert not blind["open"] and "не видно больше часа" in blind["closed"][-1]["why"]
    late2 = cp(); late2["seen_t"]["bn:gone"] = 0
    empty = W.desk_step(late2, [], {}, {"SOL": 90.0}, now, close_unfollowed=True)
    assert [x["coin"] for x in empty["open"]] == ["SOL"]                                     # an empty list never closes anything
    newer = W.desk_step(cp(), ["bn:a"], {"bn:gone": pos + [{"coin": "ETH", "side": "LONG", "entry": 2000.0}]}, {"SOL": 90.0, "ETH": 2000.0},
                        now, close_unfollowed=True)
    assert [x["coin"] for x in newer["open"]] == ["SOL"]                                     # his new position is not copied


def test_locked_whale_outside_leaderboard_slice_is_still_studied():
    """08.11 (backlog 111): a locked whale missing from the day's candidates is studied by address, not dropped as «нет данных»"""
    conn = sqlite3.connect(":memory:")
    W.ensure_schema(conn)
    conn.execute("INSERT INTO ws_whale (addr, lb) VALUES (?,?)",
                 ("bn:111", json.dumps({"src": "bn", "addr": "bn:111", "code": "111", "name": "Mike", "win_ratio": 0.9})))
    study = {"rule": W.LOCK_RULE, "top": [{"addr": "bn:111", "name": "Mike"}, {"addr": "bn:222", "name": "In"},
                                          {"addr": "okx:ABC", "short": "Okx"}, {"addr": "0xdead", "name": "Hl"}]}
    lo, le = W.locked_rows(conn, study, [{"addr": "bn:222"}])
    assert [r["addr"] for r in le] == ["bn:111"] and le[0]["code"] == "111" and le[0]["win_ratio"] == 0.9 and le[0]["locked"]
    assert [(r["addr"], r["code"], r["src"]) for r in lo] == [("okx:ABC", "ABC", "okx")]
    assert W.locked_rows(conn, {**study, "rule": "old"}, []) == ([], [])


def test_loss_watch_whale_is_benched_and_returns_after_3_profitable_days_and_the_bar(tmp_path):
    """10.11 (owner, real money): a whale dropped by the loss rule is watched (not copied, still studied); he returns at the next
    check after his own decisions closed in profit 3 full UTC days in a row since the drop AND he passes the bar"""
    import json as _j
    import sqlite3 as _sq
    now = (1_800_000_000_000 // W.DAY) * W.DAY + 3600_000
    t_drop = now - 5 * W.DAY
    d0 = t_drop // W.DAY
    q = {"day_pnl": {str(d0 + 1): 50.0, str(d0 + 2): -10.0, str(d0 + 3): 20.0, str(d0 + 4): 30.0}}
    c = W.rejoin_check(q, t_drop, now)                                 # full days d0+1 … d0+4: the last two positive → 2 of 3
    assert not c["ok"] and c["streak"] == 2 and len(c["days"]) == 4
    q["day_pnl"][str(d0 + 2)] = 5.0
    assert W.rejoin_check(q, t_drop, now)["ok"]
    q2 = {"day_pnl": {str(d0 + 1): 50.0, str(d0 + 2): 10.0, str(d0 + 4): 30.0}}  # a day with nothing closed breaks the run
    assert not W.rejoin_check(q2, t_drop, now)["ok"]
    assert W.rejoin_check({"day_pnl": {}}, now - 3600_000, now)["days"] == []   # dropped today: no full day yet
    # between reviews: a watched whale in the list leaves it; a qualified one returns into a free place at this check
    old = [_w(f"bn:{i}") for i in range(7)]
    st = {"rule": W.LOCK_RULE, "top": old, "lock_t": now - W.DAY, "passing": {x["addr"]: [now - 4 * W.DAY, now - 600_000] for x in old}}
    prof = {x["addr"]: {**x, "positions": []} for x in old}
    watch = {"bn:6": {"t": t_drop}}
    top, why, _, _ = W.lock_follow(st, old, prof, None, now, watch, set())
    assert "bn:6" not in {x["addr"] for x in top} and why["bn:6"] == [W.LOSS_WATCH_RU]
    st2 = {**st, "top": top}
    top2, why2, _, _ = W.lock_follow(st2, old, prof, None, now + 600_000, watch, {"bn:6"})
    assert "bn:6" in {x["addr"] for x in top2} and not why2
    # at a review a watched whale who does not qualify is not a newcomer either, even after 3 days in the bar
    st3 = {**st2, "lock_t": now - 3 * W.DAY}
    top3, _, _, _ = W.lock_follow(st3, old, prof, None, now, watch, set())
    assert "bn:6" not in {x["addr"] for x in top3}
    # the config adds a whale once
    cfg = tmp_path / "lw.json"
    cfg.write_text(_j.dumps({"id": "x1", "addrs": {"bn:6": {"t": t_drop, "name": "Six"}}}))
    conn = _sq.connect(":memory:")
    W.ensure_schema(conn)
    w = W.loss_watch(conn, now, str(cfg))
    assert w["bn:6"]["t"] == t_drop and w["bn:6"]["name"] == "Six"
    W.kv_set(conn, "loss_watch", {})
    assert W.loss_watch(conn, now, str(cfg)) == {}                     # applied once: not re-added after he left the watch


def test_profile_day_pnl_groups_closed_decisions_by_utc_day():
    now = (1_800_000_000_000 // W.DAY) * W.DAY + 3600_000
    closed = [{"close_t": now - W.DAY, "pnl": 10.0}, {"close_t": now - W.DAY + 60_000, "pnl": -4.0}, {"close_t": now - 30 * W.DAY, "pnl": 99.0}]
    assert W.day_pnl(closed, now) == {str(now // W.DAY - 1): 6.0}
