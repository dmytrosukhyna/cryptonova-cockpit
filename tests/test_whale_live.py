"""04.11 22:35 (owner: «Фьючерсы должны моментально копироваться»): the server's live whale desk (cnq/execution/whale_live.py)."""
import json

from cnq.agents import whale_study as W
from cnq.execution import whale_live as WL

NOW = 1_793_820_000_000
MIN = 60_000


class Feed:
    """fake sources: one Hyperliquid whale and one OKX lead trader whose positions the test changes between passes"""
    def __init__(self):
        self.hl = []
        self.okx = []
        self.calls = 0

    def post(self, body):
        self.calls += 1
        if body["type"] == "allMids":
            return {"BTC": "100000", "ETH": "2500", "SOL": "100"}
        if body["type"] == "clearinghouseState":
            return {"assetPositions": [{"position": {"coin": c, "szi": str(sz), "positionValue": str(abs(sz) * 100), "entryPx": str(e),
                                                     "unrealizedPnl": "0", "leverage": {"value": 10}}} for c, sz, e in self.hl]}
        raise AssertionError(body)

    def okx_get(self, path, params):
        assert path.endswith("public-current-subpositions")
        return [{"instId": f"{c}-USDT-SWAP", "posSide": s, "openAvgPx": str(e), "markPx": str(e), "openTime": str(t), "margin": "100",
                 "lever": "5", "upl": "0", "subPos": "1"} for c, s, e, t in self.okx]


def wtop(asof=NOW, desk=None):
    return {"asof": asof, "following": [{"addr": "0x" + "a" * 40, "name": "HL кит", "per_day": 4, "win": 0.8, "pf": 3.0},
                                        {"addr": "okx:Q", "name": "Queen", "per_day": 2, "win": 0.75, "pf": 2.5}],
            "desk": desk or {"start": 10000.0, "equity": 10000.0, "open": [], "closed": [], "history": []}}


def test_a_new_whale_position_is_copied_on_the_next_pass_and_its_close_on_the_one_after(tmp_path):
    f = Feed()
    env = {"OKX_WHALE_LIVE_STATE": str(tmp_path / "live.json")}
    kw = dict(env=env, post=f.post, okx_get=f.okx_get, put=lambda doc: True, sleep=lambda s: None)
    f.hl = [("BTC", 1.0, 99000)]
    d = WL.step(wtop(), now=NOW, **kw)
    assert d["open"] == [] and d["live"]["polled"] == 2                       # the first look is a baseline
    f.okx = [("ETH", "long", 2500, NOW + 30_000)]                               # the OKX whale opens ETH 30 s after our pass
    WL.step(wtop(), now=NOW + 40_000, **kw)  # 10.11: an OKX side is copied after 2 polls in a row
    d = WL.step(wtop(), now=NOW + MIN, **kw)
    assert [t["symbol"] for t in d["open"]] == ["ETH"] and d["live"]["lag_s_last"] == 30      # copied in the next pass, 30 s later
    assert d["live"]["lag_by_src"] == {"okx": {"med": 30, "n": 1}}                # the lag is told per source
    t = d["open"][0]
    assert t["copy_lev"] == 5.0 and t["stop"] < t["entry"] and t["id"].startswith("W-ETH-L-")
    f.okx = []                                                                   # the whale closes
    d = WL.step(wtop(), now=NOW + 2 * MIN, **kw)
    assert d["open"] == [] and d["closed"][0]["exit_kind"] == "кит закрыл"
    st = json.loads((tmp_path / "live.json").read_text())
    assert st["desk"]["closed"][0]["coin"] == "ETH" and st["lags"] == [30] and st["lags_src"] == {"okx": [30]}


def test_the_first_pass_keeps_the_published_copies_with_the_same_ids(tmp_path):
    """the switch from the GitHub desk: positions already held on OKX keep their «whales:W-…» ids"""
    o = {"whale": "okx:Q", "coin": "SOL", "side": "LONG", "entry": 100.0, "opened_t": NOW - 3_600_000, "margin": 1000.0, "lev": 5.0,
         "notional": 5000.0, "stop": 92.0, "stop_src": "защитный стоп −8%", "upnl": 0.0, "whale_ru": "Queen"}
    pub = {"start": 10000.0, "equity": 10500.0, "started": NOW - 86_400_000, "open": [o], "closed": [], "history": [[NOW - MIN, 10500.0]]}
    f = Feed()
    f.okx = [("SOL", "long", 99.0, NOW - 3_700_000)]
    d = WL.step(wtop(desk=pub), now=NOW, env={"OKX_WHALE_LIVE_STATE": str(tmp_path / "s.json")}, post=f.post, okx_get=f.okx_get,
                put=lambda doc: True, sleep=lambda s: None)
    assert [t["id"] for t in d["open"]] == [W.paper_trade(o)["id"]] and d["equity"] == 10500.0


def test_a_stale_follow_list_opens_nothing_but_closes_still_follow(tmp_path):
    f = Feed()
    env = {"OKX_WHALE_LIVE_STATE": str(tmp_path / "s.json")}
    kw = dict(env=env, post=f.post, okx_get=f.okx_get, put=lambda doc: True, sleep=lambda s: None)
    WL.step(wtop(), now=NOW, **kw)
    f.okx = [("ETH", "long", 2500, NOW + 1000)]
    WL.step(wtop(), now=NOW + 40_000, **kw)  # 10.11: an OKX side is copied after 2 polls in a row
    assert len(WL.step(wtop(), now=NOW + MIN, **kw)["open"]) == 1
    f.okx = [("ETH", "long", 2500, NOW + 1000), ("SOL", "long", 100, NOW + MIN + 1000)]
    old = wtop(asof=NOW - 7 * 3_600_000)
    d = WL.step(old, now=NOW + 2 * MIN, **kw)
    assert [t["symbol"] for t in d["open"]] == ["ETH"]                          # the held copy is still watched, no new SOL
    f.okx = []
    assert WL.step(old, now=NOW + 3 * MIN, **kw)["open"] == []


def test_publish_only_on_change_and_at_most_every_two_minutes(tmp_path):
    f = Feed()
    docs = []
    env = {"OKX_WHALE_LIVE_STATE": str(tmp_path / "s.json")}
    kw = dict(env=env, post=f.post, okx_get=f.okx_get, put=lambda doc: docs.append(doc) or True, sleep=lambda s: None)
    WL.step(wtop(), now=NOW, **kw)
    assert len(docs) == 1 and "seen" not in docs[0]["desk"]
    WL.step(wtop(), now=NOW + MIN, **kw)
    assert len(docs) == 1                                                        # nothing changed
    f.okx = [("ETH", "long", 2500, NOW + MIN)]
    WL.step(wtop(), now=NOW + 2 * MIN, **kw)  # 10.11: an OKX side is copied after 2 polls in a row (first sight: nothing to publish)
    WL.step(wtop(), now=NOW + 2 * MIN + 1000, **kw)
    assert len(docs) == 2 and docs[-1]["desk"]["open"][0]["coin"] == "ETH"


def test_a_failed_poll_changes_nothing_for_that_whale(tmp_path):
    f = Feed()
    env = {"OKX_WHALE_LIVE_STATE": str(tmp_path / "s.json")}
    kw = dict(env=env, post=f.post, put=lambda doc: True, sleep=lambda s: None)
    WL.step(wtop(), now=NOW, okx_get=f.okx_get, **kw)
    f.okx = [("ETH", "long", 2500, NOW + 1000)]
    WL.step(wtop(), now=NOW + 40_000, okx_get=f.okx_get, **kw)  # 10.11: an OKX side is copied after 2 polls in a row
    WL.step(wtop(), now=NOW + MIN, okx_get=f.okx_get, **kw)

    def down(path, params):
        raise OSError("timeout")
    d = WL.step(wtop(), now=NOW + 2 * MIN, okx_get=down, **kw)
    assert [t["symbol"] for t in d["open"]] == ["ETH"] and d["live"]["errors"]


def test_the_github_study_publishes_the_fresh_live_desk_instead_of_its_own(tmp_path):
    p = tmp_path / "whales_live.json"
    desk = {"start": 10000.0, "cash": 9000.0, "equity": 10100.0, "open": [{"whale": "okx:Q", "coin": "ETH", "side": "LONG", "entry": 2500.0,
                                                                         "opened_t": NOW, "margin": 1000.0, "lev": 5.0, "notional": 5000.0,
                                                                         "stop": 2300.0}], "closed": [], "history": [], "util_pct": 9.9}
    p.write_text(json.dumps({"asof": NOW, "desk": desk}))
    assert W.live_desk(str(p), NOW + 5 * MIN)["open"][0]["coin"] == "ETH"
    assert W.live_desk(str(p), NOW + 20 * MIN) is None and W.live_desk(str(tmp_path / "none.json"), NOW) is None



def test_a_new_live_seed_token_restarts_the_desk_from_the_published_one_once(tmp_path):
    f = Feed()
    env = {"OKX_WHALE_LIVE_STATE": str(tmp_path / "s.json")}
    kw = dict(env=env, post=f.post, okx_get=f.okx_get, put=lambda doc: True, sleep=lambda s: None)
    bad = {"start": 10000.0, "equity": 10100.0, "open": [{"whale": "okx:Q", "coin": "BTC", "side": "SHORT", "entry": 100000.0, "opened_t": NOW,
                                                         "margin": 500.0, "lev": 5.0, "notional": 2500.0, "stop": 108000.0, "upnl": 0.0}]}
    f.okx = [("BTC", "short", 100000, NOW - MIN)]
    assert WL.step(wtop(desk=bad), now=NOW, **kw)["open"][0]["symbol"] == "BTC"
    good = {"start": 10000.0, "equity": 11000.0, "open": [], "closed": []}
    f.okx = []
    d = WL.step({**wtop(desk=good), "live_seed": "recover-1"}, now=NOW + MIN, **kw)
    assert d["open"] == [] and d["equity"] == 11000.0
    f.okx = [("ETH", "long", 2500, NOW + MIN)]
    WL.step({**wtop(desk=good), "live_seed": "recover-1"}, now=NOW + 2 * MIN, **kw)
    assert len(WL.step({**wtop(desk=good), "live_seed": "recover-1"}, now=NOW + 3 * MIN, **kw)["open"]) == 1     # not re-seeded again


def test_a_lead_trader_gone_from_okx_for_30_min_has_his_copies_closed(tmp_path):
    """05.11 (Luckyboytt): OKX answered «60004 Trader doesn't exist» — the BTC copy was held blind; after 30 min it is closed"""
    f = Feed()
    env = {"OKX_WHALE_LIVE_STATE": str(tmp_path / "live.json")}
    kw = dict(env=env, post=f.post, okx_get=f.okx_get, put=lambda doc: True, sleep=lambda s: None)
    WL.step(wtop(), now=NOW, **kw)
    f.okx = [("ETH", "long", 2500, NOW + 30_000)]
    WL.step(wtop(), now=NOW + 40_000, **kw)  # 10.11: an OKX side is copied after 2 polls in a row
    d = WL.step(wtop(), now=NOW + MIN, **kw)
    assert [t["symbol"] for t in d["open"]] == ["ETH"]
    dropped = wtop()
    dropped["following"] = dropped["following"][:1]                              # Queen leaves the list, her ETH copy is kept
    gone_get = lambda path, params: (_ for _ in ()).throw(RuntimeError("OKX 60004: Trader doesn't exist"))  # noqa: E731
    kw2 = {**kw, "okx_get": gone_get}
    d = WL.step(dropped, now=NOW + 2 * MIN, **kw2)
    assert [t["symbol"] for t in d["open"]] == ["ETH"] and "60004" in d["live"]["errors"][0]   # not yet: maybe a blip
    d = WL.step(dropped, now=NOW + 20 * MIN, **kw2)
    assert [t["symbol"] for t in d["open"]] == ["ETH"]
    d = WL.step(dropped, now=NOW + 33 * MIN, **kw2)
    assert d["open"] == [] and d["closed"][0]["exit_kind"].startswith("кит исчез с OKX")


def test_a_followed_whale_or_a_plain_error_never_closes_copies(tmp_path):
    f = Feed()
    env = {"OKX_WHALE_LIVE_STATE": str(tmp_path / "live.json")}
    kw = dict(env=env, post=f.post, okx_get=f.okx_get, put=lambda doc: True, sleep=lambda s: None)
    WL.step(wtop(), now=NOW, **kw)
    f.okx = [("ETH", "long", 2500, NOW + 30_000)]
    WL.step(wtop(), now=NOW + 40_000, **kw)  # 10.11: an OKX side is copied after 2 polls in a row
    WL.step(wtop(), now=NOW + MIN, **kw)
    gone_get = lambda path, params: (_ for _ in ()).throw(RuntimeError("OKX 60004: Trader doesn't exist"))  # noqa: E731
    timeout = lambda path, params: (_ for _ in ()).throw(RuntimeError("timeout"))  # noqa: E731
    for t in (2, 40, 80):                                                        # still on the follow list → held
        d = WL.step(wtop(), now=NOW + t * MIN, **{**kw, "okx_get": gone_get})
    assert [x["symbol"] for x in d["open"]] == ["ETH"]
    dropped = wtop()
    dropped["following"] = dropped["following"][:1]
    for t in (90, 130, 170):                                                     # a plain error never closes
        d = WL.step(dropped, now=NOW + t * MIN, **{**kw, "okx_get": timeout})
    assert [x["symbol"] for x in d["open"]] == ["ETH"]


def test_copies_carry_the_follow_flag_and_whales_are_polled_concurrently(tmp_path):
    f = Feed()
    kw = dict(env={"OKX_WHALE_LIVE_STATE": str(tmp_path / "s.json")}, post=f.post, okx_get=f.okx_get, put=lambda doc: True, sleep=lambda s: None)
    WL.step(wtop(), now=NOW, **kw)
    f.okx = [("ETH", "long", 2500, NOW + 1000)]
    WL.step(wtop(), now=NOW + 40_000, **kw)  # 10.11: an OKX side is copied after 2 polls in a row
    d = WL.step(wtop(), now=NOW + MIN, **kw)
    assert d["open"][0]["follow"] is True and d["live"]["poll_s"] is not None


def test_ivan_is_followed_from_his_bybit_positions_with_his_stop_and_an_ivan_sig(tmp_path):
    """06.11 17:33 (owner: «по ивану да»): Ivan's Bybit positions read every pass; his stop when usable; copies carry sig «ivan:…»"""
    f = Feed()
    rows = []
    reader = lambda: rows                                                         # noqa: E731
    kw = dict(env={"OKX_WHALE_LIVE_STATE": str(tmp_path / "s.json")}, post=f.post, okx_get=f.okx_get, put=lambda doc: True,
              sleep=lambda s: None, src_get={"ivan": reader})
    wt = wtop()
    wt["following"] = wt["following"] + [{"addr": WL.IVAN_ADDR, "name": "Иван", "owner_pick": True}]
    WL.step(wt, now=NOW, **kw)
    rows.append({"symbol": "SOLUSDT", "side": "Buy", "size": "10", "avgPrice": "100.2", "positionValue": "1002", "unrealisedPnl": "0",
                 "leverage": "10", "stopLoss": "95", "createdTime": str(NOW + 20_000)})
    d = WL.step(wt, now=NOW + MIN, **kw)
    t = next(x for x in d["open"] if x["symbol"] == "SOL")
    assert t["whale"] == WL.IVAN_ADDR and t["sig"].startswith("ivan:W-SOL-L-") and t["stop"] == 95.0 and t["follow"] is True
    assert t["whale_entry"] == 100.2
    rows.clear()                                                                  # Ivan closes → our copy closes
    d = WL.step(wt, now=NOW + 2 * MIN, **kw)
    assert not [x for x in d["open"] if x["symbol"] == "SOL"]


def test_okx_futures_holds_ivans_copies_as_their_own_desk_with_the_whale_part():
    from cnq.execution import okx_futures as fu
    ds = fu.split_ivan([("whales", {"equity": 1e4, "open": [{"id": "W-A", "whale": "okx:Q"}, {"id": "W-B", "whale": "ivan:bybit"}]})])
    assert [k for k, _ in ds] == ["whales", "ivan"] and [t["id"] for t in dict(ds)["ivan"]["open"]] == ["W-B"]
    parts = fu.desk_parts(ds, 2500.0, {"stages": {"alloc": {"whales": 1.0, "aggr": 0.0}}})
    assert parts == {"whales": 2500.0, "ivan": 2500.0}


def test_ivans_averaging_down_is_never_added_to_and_his_first_entry_fixes_the_window(tmp_path):
    """06.11 (owner): never copy Ivan's adds or averaging-down; the join window is around his avgPrice at first sight"""
    f = Feed()
    rows = [{"symbol": "SOLUSDT", "side": "Buy", "size": "10", "avgPrice": "100", "positionValue": "1000", "unrealisedPnl": "0",
             "leverage": "10", "stopLoss": "", "createdTime": str(NOW - 3_600_000)}]
    kw = dict(env={"OKX_WHALE_LIVE_STATE": str(tmp_path / "s.json")}, post=f.post, okx_get=f.okx_get, put=lambda doc: True,
              sleep=lambda s: None, src_get={"ivan": lambda: rows})
    wt = {**wtop(), "following": [{"addr": WL.IVAN_ADDR, "name": "Иван", "owner_pick": True}]}
    f_post = f.post
    f.post = lambda body: {**f_post(body), "SOL": "96"} if body["type"] == "allMids" else f_post(body)
    kw["post"] = f.post
    WL.step(wt, now=NOW, **kw)                                   # first sight: his entry 100, price 96 (he is −4 %) → no join
    rows[0].update(size="30", avgPrice="97.3")                   # he averages down: 30 SOL at avg 97.3
    d = WL.step(wt, now=NOW + MIN, **kw)
    assert not d["open"]                                         # 96 vs his FIRST entry 100 → −4 %, outside −3 %…+1 % → still no join
    st = json.loads((tmp_path / "s.json").read_text())
    assert st["desk"]["first_entry"][f"{WL.IVAN_ADDR}|SOL|LONG"] == 100.0
