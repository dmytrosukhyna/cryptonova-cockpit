import json
import sqlite3

from cnq.agents import whale_study as W

H, M = W.H1, 60_000
T0 = 1_790_000_000_000


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


def test_desk_copies_only_new_positions_and_closes_with_the_whale():
    mids = {"BTC": "100", "ETH": "2000"}
    d = W.desk_step(None, ["0xa"], {"0xa": [{"coin": "ETH", "side": "LONG", "lev": 10, "entry": 1900}]}, mids, T0)
    assert d["open"] == [] and d["seen"]["0xa"] == {"ETH": "LONG"}           # already open when we started watching → not copied
    d = W.desk_step(d, ["0xa"], {"0xa": [{"coin": "ETH", "side": "LONG", "lev": 10}, {"coin": "BTC", "side": "SHORT", "lev": 20}]}, mids, T0 + H)
    assert len(d["open"]) == 1 and d["open"][0]["coin"] == "BTC" and d["open"][0]["lev"] == 5.0 and d["open"][0]["margin"] == 1000.0
    mids2 = {"BTC": "90", "ETH": "2000"}
    d = W.desk_step(d, ["0xa"], {"0xa": [{"coin": "ETH", "side": "LONG", "lev": 10}]}, mids2, T0 + 2 * H)
    assert d["open"] == [] and len(d["closed"]) == 1
    c = d["closed"][0]
    assert c["why"] == "кит закрыл" and abs(c["pnl"] - (5000 * 0.1 - 5000 * W.COST_PCT / 100)) < 1e-6
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
    # 10 winning 4-hour longs on BTC in an uptrend, one every 2 days
    fl = []
    for k in range(10):
        t = now - 25 * W.DAY + k * 2 * W.DAY
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

    d = W.run(conn, now=now, post=post, get_lb=lambda: lb, okx_get=okx_get, max_requests=50, budget_s=60)
    assert d["complete"] and d["funnel"]["studied"] == 2
    top = d["top"]
    assert len(top) == 1 and top[0]["addr"] == addr and top[0]["trips"] == 10 and top[0]["win"] == 1.0
    assert top[0]["style_ru"] == "интрадей" and top[0]["coins"][0]["coin"] == "BTC" and "BTC" in top[0]["desc_ru"]
    assert top[0]["copy"]["30"]["n"] == 10 and top[0]["portfolio"]["months"] >= 3 and top[0]["lev_now"] == 3.0
    okx = next(o for o in d["others"] if o["src"] == "okx")
    assert okx["trips"] == 3 and "мало закрытых сделок" in okx["excluded_ru"][0]
    assert d["following"] and d["following"][0]["addr"] == addr
    # next run: the desk starts watching the followed whale (no copy of positions it already had)
    d2 = W.run(conn, now=now + 1800_000, post=post, get_lb=lambda: lb, okx_get=okx_get, max_requests=50, budget_s=60)
    assert d2["desk"] and d2["desk"]["open"] == [] and d2["desk"]["equity"] == 10_000
    json.dumps(d2)
