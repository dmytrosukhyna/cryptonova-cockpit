"""17.11 (owner 17:27, real money): «Иван Тест 2 / 3 / 4» (Bybit sub-accounts connected in the owner form) are copied to the real
OKX futures account like «ivan:» — ONLY after the owner switches an account on (default off), under ids «ivan2:» / «ivan3:» /
«ivan4:», inside the existing limits, with ALL Ivan accounts counted as one trader. And every entry / add / exit is stored with
its market context for the study. Every key, coin and amount here is made-up test data; no real API is called."""
import json
import os
import tempfile

import okx_fake as F
from cnq.agents import app_home
from cnq.agents import ivan_study as IS
from cnq.agents import whale_study as W
from cnq.api import ivan_tests as IT
from cnq.execution import ivan_watch as iw
from cnq.execution import okx_autopilot as ap
from cnq.execution import okx_futures as fu
from cnq.execution import public_real
from cnq.execution import whale_live as WL
from test_ivan_tests import KEY, SEC, fake_bybit
from test_whale_live import Feed, MIN, NOW, wtop

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROW = {"symbol": "SOLUSDT", "side": "Buy", "size": "10", "avgPrice": "100.2", "positionValue": "1002", "unrealisedPnl": "0",
       "leverage": "10", "stopLoss": "95", "createdTime": str(NOW + 20_000)}


def accounts(tmp, *rows):
    p = os.path.join(str(tmp), "accounts.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(list(rows), f)
    return p


def acc(slot, on, name=None, key="K" * 14):
    return {"id": f"a{slot}", "name": name or f"Иван Тест {slot}", "key": key + str(slot), "secret": "s" * 30, "host": "demo",
            **({"slot": slot} if slot else {}), "copy_real": on}


# ── the switch ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_switch_is_off_by_default_owner_only_and_gives_the_slot_from_the_name():
    d = tempfile.mkdtemp()
    t = IT.IvanTests(d, make_fetch=fake_bybit(), kline=lambda *a: [])
    t.add({"name": "Иван Тест 3", "key": KEY, "secret": SEC, "demo": True})
    v = t.view()
    a = v["accounts"][0]
    assert a["copy_real"] is False and a["slot"] is None and v["copy_real_any"] is False and "ничего не попадает" in v["label_ru"]
    assert WL.test_accounts({"IVAN_TESTS_FILE": os.path.join(d, "accounts.json")}) == []           # nothing for the autopilot yet
    code, r = t.set_copy(a["id"], True)
    a = r["accounts"][0]
    assert code == 200 and r["ok"] and a["copy_real"] is True and a["slot"] == 3 and a["copy_id"] == "ivan3" and r["copy_real_any"]
    assert KEY not in json.dumps(r, ensure_ascii=False) and SEC not in json.dumps(r, ensure_ascii=False)
    got = WL.test_accounts({"IVAN_TESTS_FILE": os.path.join(d, "accounts.json")})
    assert [(x["addr"], x["on"], x["host"]) for x in got] == [("ivan3:bybit", True, "demo")]
    code, r = t.set_copy(a["id"], False)                                  # off: the slot stays (the ids of held copies do not change)
    assert r["accounts"][0]["copy_real"] is False and r["accounts"][0]["slot"] == 3
    assert [x["on"] for x in WL.test_accounts({"IVAN_TESTS_FILE": os.path.join(d, "accounts.json")})] == [False]
    assert t.set_copy("nope", True)[0] == 404
    t.make_fetch = fake_bybit(read_only=0)                                # the key gained trade rights → the switch refuses
    code, r = t.set_copy(a["id"], True)
    assert not r["ok"] and "не включено" in r["message_ru"] and r["accounts"][0]["copy_real"] is False
    assert IT.slot_for("Иван Тест 2", set()) == 2 and IT.slot_for("Иван Тест 2", {2}) == 3 and IT.slot_for("Демо", {2, 3}) == 4
    assert IT.slot_for("Иван Тест 7", set()) == 2 and IT.slot_for("x", {2, 3, 4}) is None
    srv = open(os.path.join(ROOT, "cnq", "api", "server.py"), encoding="utf-8").read()
    i = srv.index("def _owner(")
    assert '"/v2/owner/ivan_tests/copy_real"' in srv[i:] and '"/v2/owner/ivan_tests/copy_real"' not in srv[:i]
    assert srv.index("self.owner_auth.require(headers)", i) < srv.index('"/v2/owner/ivan_tests/copy_real"', i)


# ── the live copy loop ───────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_switched_off_account_is_never_followed_and_a_switched_on_one_is_copied_with_its_own_id(tmp_path):
    f = Feed()
    rows2, rows3 = [dict(ROW)], [{**ROW, "symbol": "ETHUSDT", "avgPrice": "2500", "stopLoss": ""}]
    readers = {"ivan2:bybit": lambda: rows2, "ivan3:bybit": lambda: rows3}
    env = {"OKX_WHALE_LIVE_STATE": str(tmp_path / "s.json"), "IVAN_TESTS_FILE": accounts(tmp_path, acc(2, False), acc(3, False))}
    kw = dict(env=env, post=f.post, okx_get=f.okx_get, put=lambda doc: True, sleep=lambda s: None, src_get=readers)
    for i in range(3):                                                    # both switches off → nothing, however long
        d = WL.step(wtop(), now=NOW + i * MIN, **kw)
    assert d["open"] == [] and d["live"]["whales"] == 2
    accounts(tmp_path, acc(2, False), acc(3, True))                       # the owner switches «Тест 3» on
    rows3.clear()
    WL.step(wtop(), now=NOW + 3 * MIN, **kw)                              # first look = a baseline
    rows3.append({**ROW, "symbol": "ETHUSDT", "avgPrice": "2500", "stopLoss": ""})
    d = WL.step(wtop(), now=NOW + 4 * MIN, **kw)
    assert [(t["whale"], t["symbol"], t["follow"]) for t in d["open"]] == [("ivan3:bybit", "ETH", True)]
    assert d["open"][0]["sig"].startswith("ivan3:W-ETH-L-")
    ds = fu.split_ivan([("whales", d)])
    assert [k for k, _ in ds] == ["whales", "ivan3"] and dict(ds)["ivan3"]["_share_of"] == "whales" and dict(ds)["whales"]["open"] == []
    assert list(fu.want_map(ds)) == ["ivan3:" + d["open"][0]["id"]]
    accounts(tmp_path, acc(2, False), acc(3, False))                      # off again: no new copies, the held one runs to his exit
    rows3.append({**ROW, "symbol": "BTCUSDT", "avgPrice": "100000", "stopLoss": ""})
    d = WL.step(wtop(), now=NOW + 5 * MIN, **kw)
    assert [(t["symbol"], t["follow"]) for t in d["open"]] == [("ETH", False)]
    del rows3[0]                                                          # he closes ETH → our copy closes
    d = WL.step(wtop(), now=NOW + 6 * MIN, **kw)
    assert d["open"] == []


def test_a_stale_follow_feed_or_a_missing_file_adds_nobody_and_a_test_account_never_uses_another_key(tmp_path):
    f = Feed()
    env = {"OKX_WHALE_LIVE_STATE": str(tmp_path / "s.json"), "IVAN_TESTS_FILE": accounts(tmp_path, acc(2, True), acc(2, True, key="Z" * 14),
                                                                                         acc(5, True), {"slot": 3, "copy_real": True})}
    got = WL.test_accounts(env)
    assert [x["addr"] for x in got] == ["ivan2:bybit"] and got[0]["key"] == "K" * 14 + "2"       # one per slot; bad rows ignored
    assert WL.test_accounts({"IVAN_TESTS_FILE": str(tmp_path / "none.json")}) == []
    main = lambda: [dict(ROW)]                                                   # noqa: E731
    try:                                                                  # «ivan2:» has no reader of its own → never Ivan's main key
        WL.poll("ivan2:bybit", {}, f.post, f.okx_get, {"ivan": main})
        raise AssertionError("ivan2 was read with the main key")
    except RuntimeError as e:
        assert "тест-счёт" in str(e)
    kw = dict(env=env, post=f.post, okx_get=f.okx_get, put=lambda doc: True, sleep=lambda s: None, src_get={"ivan2:bybit": lambda: []})
    d = WL.step(wtop(asof=NOW - 7 * 3_600_000), now=NOW, **kw)              # a follow list older than 6 h → nobody is added
    assert d["live"]["whales"] == 0


def test_all_ivan_accounts_share_one_per_trader_cap_on_the_desk():
    """the desk's ≤ 50 % per trader: copies of «ivan:» and «ivan2:» are counted together"""
    assert [W.ivan_key(a) for a in ("ivan:bybit", "ivan2:bybit", "ivan4:bybit", "ivan5:bybit", "okx:ivan", "bn:1", None)] == \
        ["ivan", "ivan2", "ivan4", None, None, None, None]
    assert W.trader_of("ivan3:bybit") == "ivan" == W.trader_of("ivan:bybit") and W.trader_of("okx:Q") == "okx:Q"
    mids = {"SOL": "100", "ETH": "2500", "XRP": "2"}
    pos = lambda c, e: {"coin": c, "side": "LONG", "size": 1.0, "notional": 1000, "entry": e, "upnl": 0, "lev": 5, "sl": None}   # noqa: E731
    held = lambda c, e: {"whale": "ivan:bybit", "coin": c, "side": "LONG", "entry": e, "opened_t": NOW - MIN, "margin": 2450.0, "lev": 1.0,   # noqa: E731
                         "notional": 2450.0, "stop": e * 0.999, "stop_src": "стоп кита"}
    ivan = [pos("SOL", 100.0), pos("ETH", 2500.0)]

    def xrp_copy(second):                                 # Ivan's main account already holds 49 % of the desk; `second` opens XRP
        d = {"start": 10000.0, "cash": 10000.0, "open": [held("SOL", 100.0), held("ETH", 2500.0)], "closed": [], "history": [],
             "seen": {"ivan:bybit": {"SOL": "LONG", "ETH": "LONG"}, second: {}}, "seen_t": {"ivan:bybit": NOW, second: NOW}}
        d = W.desk_step(d, ["ivan:bybit", second], {"ivan:bybit": ivan, second: [pos("XRP", 2.0)]}, mids, NOW + MIN)
        return [o for o in d["open"] if o["whale"] == second], [e.get("why") for e in d["events"] if e.get("coin") == "XRP"]
    got, _ = xrp_copy("0x" + "b" * 40)
    assert len(got) == 1 and got[0]["margin"] >= W.MIN_COPY_MARGIN * 10000        # another trader has his own 50 %
    got, why = xrp_copy("ivan2:bybit")
    assert got == [] and "предел 50% на кита" in why[-1]                          # «ivan2» is the same trader as «ivan» → refused
    got, _ = xrp_copy("ivan4:bybit")
    assert got == []


# ── the real gate ────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_real_gate_lets_through_only_whales_and_the_four_ivan_ids():
    assert fu.COPY_PREFIXES == ("whales:", "ivan:", "ivan2:", "ivan3:", "ivan4:")
    for ok in ("whales:W-1", "ivan:W-1", "ivan2:W-1", "ivan3:W-1", "ivan4:W-1"):
        assert fu.copy_sig(ok) and fu.copy_sig(ok, {})
    for bad in ("ivan5:W-1", "ivan1:W-1", "ivan22:W-1", "ivan:", "ivanx:W", "ivan_tests:1", "aggr:A", "bal:B", "imp:I", "own:O", "reversal:R",
                "listing_short:L", "smart_alts:S", "Ivan2:W", " ivan2:W", "whales", "ivan2", "", None, "xivan2:W"):
        if bad in ("ivan:",):
            continue
        assert not fu.copy_sig(bad) and not fu.copy_sig(bad, {}) and not fu.copy_sig(bad, {"OKX_FUT_ALLOW_DESKS": ""}), bad
    fk = F.FakeOkx()
    e = F.env(OKX_FUT_ALLOW_DESKS="")
    cl = ap.connect(e, fk, {}, F.nosleep)
    t2 = {**F.trade("W2", "SOL"), "whale": "ivan2:bybit", "follow": True, "copy_lev": 5.0}
    r = fu.step(e, desks=[("ivan5", {"equity": 10000.0, "open": [F.trade("X1", "BTC", "SHORT", 100000.0, 102000.0, 90000.0)]}),
                          ("ivan_tests", {"equity": 10000.0, "open": [F.trade("X2", "ETH", "LONG", 3000.0, 2900.0, 3300.0)]}),
                          ("ivan2", {"equity": 10000.0, "open": [t2]})], now=F.NOW, sleep=F.nosleep, cl=cl)
    st = fu.load_state(e)
    assert list(st["trades"]) == ["ivan2:W2"] and r["opened"] == 1 and st["trades"]["ivan2:W2"]["desk"] == "ivan2"
    assert st["skip"]["ivan5:X1"] == fu.NOT_COPY and st["skip"]["ivan_tests:X2"] == fu.NOT_COPY
    assert "ivan2" in fu.held_desks(st)
    # nothing in the repository turns the test-only bypass on for the server
    for p in ("deploy/okx/cnq-okx.env.example", "deploy/okx/cnq-okx-autopilot.service", "scripts/okx_autopilot_server.sh"):
        assert "OKX_FUT_ALLOW_DESKS" not in open(os.path.join(ROOT, p), encoding="utf-8").read(), p


def test_all_ivan_copies_together_hold_at_most_half_of_the_futures_budget_on_the_real_account():
    fk = F.FakeOkx()
    e = F.env(OKX_FUT_ALLOW_DESKS="")
    cl = ap.connect(e, fk, {}, F.nosleep)
    mk = lambda tid, sym, whale, entry, stop: {**F.trade(tid, sym, "LONG", entry, stop, entry * 1.2), "whale": whale, "follow": True,   # noqa: E731
                                               "copy_lev": 1.0}
    a = mk("WA", "SOL", "ivan:bybit", 200.0, 190.0)
    r = fu.step(e, desks=[("ivan", {"equity": 10000.0, "open": [a]})], now=F.NOW, sleep=F.nosleep, cl=cl)
    st = fu.load_state(e)
    bud = st["budget"]["usd"]
    m0 = fu.ivan_margin(st["trades"])
    assert r["opened"] == 1 and m0 > fu.IVAN_CAP * bud          # one copy alone is never blocked (1×, a 5 % stop: $900 of $1 500)
    # the next Ivan copies come from his OTHER accounts: all of them together are already past 50 % → none is opened
    more = [mk("WB", "ETH", "ivan2:bybit", 3000.0, 2850.0), mk("WC", "LINK", "ivan3:bybit", 20.0, 19.0), mk("WD", "AVAX", "ivan4:bybit", 30.0, 28.5),
            mk("WE", "DOT", "ivan:bybit", 4.0, 3.8)]
    ds = [("ivan", {"equity": 10000.0, "open": [a, more[3]]}), ("ivan2", {"equity": 10000.0, "open": [more[0]]}),
          ("ivan3", {"equity": 10000.0, "open": [more[1]]}), ("ivan4", {"equity": 10000.0, "open": [more[2]]})]
    r = fu.step(e, desks=ds, now=F.NOW + 60_000, sleep=F.nosleep, cl=cl)
    st = fu.load_state(e)
    capped = [x for x in r["skipped"] if "все его счета вместе" in x["why"]]
    assert list(st["trades"]) == ["ivan:WA"] and r["opened"] == 0 and len(capped) == 4 and fu.ivan_margin(st["trades"]) == m0
    assert all(x["id"] not in st["skip"] for x in capped)                     # not final: re-checked every pass
    assert {x["sig"] for x in st["copy_skips"]} >= {"ivan2:WB", "ivan3:WC", "ivan4:WD", "ivan:WE"}
    # with leverage the same copies are small: several Ivan accounts fit, and the sum never passes 50 %
    fk2, e2 = F.FakeOkx(), F.env(OKX_FUT_ALLOW_DESKS="")
    cl2 = ap.connect(e2, fk2, {}, F.nosleep)
    for _, dd in ds:
        for t in dd["open"]:
            t["copy_lev"] = 5.0
    r = fu.step(e2, desks=ds, now=F.NOW, sleep=F.nosleep, cl=cl2)
    st2 = fu.load_state(e2)
    assert r["opened"] >= 2 and len({m["desk"] for m in st2["trades"].values()}) >= 2
    assert fu.ivan_margin(st2["trades"]) <= fu.IVAN_CAP * st2["budget"]["usd"] + 1e-6
    assert r["opened"] + len([x for x in r["skipped"] if "все его счета вместе" in x["why"]]) == 5
    # a whale's copy is not touched by Ivan's cap
    w = {**F.trade("WW", "XRP", "LONG", 2.0, 1.9, 2.4), "whale": "bn:1", "follow": True, "copy_lev": 1.0}
    r = fu.step(e, desks=ds + [("whales", {"equity": 10000.0, "open": [w]})], now=F.NOW + 120_000, sleep=F.nosleep, cl=cl)
    assert not any("все его счета" in x["why"] for x in r["skipped"] if x["id"] == "whales:WW")


def test_public_files_keep_the_new_ids_neutral():
    assert public_real.SIG_RE.match("ivan2:W-SOL-L-1794872097313") and public_real.SIG_RE.match("whales:W-AAVE-S-1")
    assert not public_real.SIG_RE.match("Иван Тест 2:W") and not public_real.SIG_RE.match("2ivan:W")
    doc = app_home.public_edge({"rows": [{"sig": "ivan2:W-SOL-L-1"}, {"sig": "ivan:W-SOL-L-1"}, {"sig": "whales:W-SOL-L-1"}]})
    blob = json.dumps(doc)
    assert "ivan" not in blob and "whales" not in blob and all(r["sig"].startswith("cn:") for r in doc["rows"])
    names = {a["name"] for a in WL.test_accounts({"IVAN_TESTS_FILE": accounts(tempfile.mkdtemp(), acc(2, True, name="Секретное имя счёта"))})}
    assert names == {"Иван · тест 2"}                                     # the name the owner typed never reaches the published desk


# ── the study ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def candles(n, step, px0=100.0, t_end=NOW):
    t0 = t_end - t_end % step - (n - 1) * step
    out = []
    for i in range(n):
        c = px0 + i * 0.1
        out.append([t0 + i * step, c - 0.05, c + 0.5, c - 0.5, c])
    return out


def test_context_has_levels_volatility_trend_session_and_btc_regime():
    h1, d1 = candles(400, IS.H1), candles(260, IS.DAY)
    px = h1[-1][4]
    c = IS.context(px, NOW, h1, d1, candles(400, IS.H1, 90000.0), candles(260, IS.DAY, 90000.0))
    assert c["session"] == IS.session(NOW) and c["range"]["day"]["high"] >= px >= c["range"]["day"]["low"]
    assert c["range"]["week"]["to_low_pct"] < 0 < c["range"]["week"]["to_high_pct"] and 0 <= c["range"]["week"]["pos"] <= 1
    assert c["atr_h1_pct"] > 0 and c["atr_d1_pct"] > 0
    assert c["trend_h1"]["sma20_pct"] > 0 and c["trend_h1"]["sma200_pct"] > c["trend_h1"]["sma50_pct"] > 0 and c["trend_d1"]["sma200_pct"] > 0
    assert c["btc"]["label"] == "bull" and c["btc"]["d1_sma200_pct"] > 0
    r = IS.round_levels(97_300.0)
    assert r["big"]["below"] == 90_000 and r["big"]["above"] == 100_000 and r["half"]["nearest"] == 95_000 and r["tenth"]["nearest"] == 97_000
    assert IS.round_levels(0.47)["tenth"]["nearest"] == 0.47 or abs(IS.round_levels(0.47)["tenth"]["nearest"] - 0.47) < 1e-9
    assert [IS.session(h * IS.H1) for h in (0, 6, 7, 12, 13, 20, 21, 23)] == ["asia", "asia", "europe", "europe", "us", "us", "late", "late"]
    sw = IS.swings([[i, 0, h, h - 2, h - 1] for i, h in enumerate([1, 2, 3, 9, 3, 2, 1, 2, 3, 4, 5, 4, 3, 2, 1])])
    assert [p for _, p in sw["highs"]] == [5, 9]
    assert IS.context(100.0, NOW, [], [])["trend_h1"]["sma20_pct"] is None            # no candles → the event is still stored


def test_the_ladder_tells_entries_adds_averaging_reduces_and_exits():
    p = lambda size, entry, mark, upnl=0.0: [{"symbol": "SOL", "side": "LONG", "size": size, "entry": entry, "mark": mark, "upnl": upnl,   # noqa: E731
                                              "leverage": 10, "stop": None, "tp": None, "value": size * mark}]
    op = {}
    e = IS.diff(op, p(10, 100.0, 100.0), NOW)
    assert [x["kind"] for x in e] == ["entry"] and e[0]["step"] == 1
    assert IS.diff(op, p(10.01, 100.0, 99.0, -10.0), NOW + MIN) == []                # a rounding-size change is not an add
    e = IS.diff(op, p(20.02, 97.5, 95.0, -50.0), NOW + 30 * MIN)                       # +10 at 95 → averaging down
    a = e[0]
    assert a["kind"] == "add" and a["averaging"] is True and a["step"] == 2 and a["size_ratio"] == 1.0 and abs(a["fill_px"] - 95.0) < 1e-6
    assert a["step_from_last_pct"] == -5.0 and a["min_since_last"] == 30.0 and a["avg_before"] == 100.0
    e = IS.diff(op, p(30.03, 98.0, 99.0, 30.0), NOW + 60 * MIN)                        # +10 at 99 → an add with the move, not averaging
    assert e[0]["kind"] == "add" and e[0]["averaging"] is False and e[0]["step"] == 3 and e[0]["min_since_last"] == 30.0
    e = IS.diff(op, p(15.015, 98.0, 101.0, 45.0), NOW + 90 * MIN)
    assert e[0]["kind"] == "reduce" and e[0]["cut_ratio"] == 0.5
    e = IS.diff(op, [], NOW + 120 * MIN)
    x = e[0]
    assert x["kind"] == "exit" and x["steps"] == 3 and x["adds"] == 2 and x["avg_downs"] == 1 and x["worst_upnl"] == -50.0 and x["min_held"] == 120.0
    assert op == {}
    st = {"open": {"SOL|LONG": {"adds": 2, "steps": 3}}}
    IS.count(st, [{"kind": "entry"}, {"kind": "add", "averaging": True}, {"kind": "add"}, {"kind": "exit"}])
    out = {"stats": {"pnl_sum": 300.0, "winrate": 1.0, "n": 4}, "upnl": -50.0, "equity": 10_250.0, "positions": [1], "max_dd": 0.02}
    IS.track(st, out, NOW)
    card = IS.track(st, {**out, "upnl": -900.0, "equity": 9_400.0}, NOW + MIN)       # a 100 % win rate with a held loss
    assert card["winrate"] == 1.0 and card["closed_pnl_usd"] == 300.0 and card["open_pnl_usd"] == -900.0 and card["total_pnl_usd"] == -600.0
    assert card["worst_open_usd"] == -900.0 and card["max_dd_usd"] == 850.0 and card["max_dd_pct"] == round(850 / 10_250 * 100, 2)
    assert card["adds"] == 2 and card["averagings"] == 1 and card["entries"] == 1 and card["open_averaged_n"] == 1 and card["open_max_steps"] == 3


def test_every_event_is_stored_raw_with_context_and_the_card_shows_open_loss_and_our_real_copies():
    d, web = tempfile.mkdtemp(), tempfile.mkdtemp()
    pos = [{"symbol": "ETHUSDT", "side": "Buy", "size": "1", "avgPrice": "2000", "markPrice": "2010", "unrealisedPnl": "10", "leverage": "10",
            "positionValue": "2010"}]
    base = fake_bybit()

    def make(key, secret):
        f = base(key, secret)
        def fetch(b, path, params):
            if path == "/v5/position/list" and b == iw.HOSTS["demo"]:
                return {"retCode": 0, "result": {"list": pos if params.get("settleCoin") == "USDT" else [], "nextPageCursor": ""}}
            return f(b, path, params)
        return fetch
    asked = []
    kl = lambda sym, itv, n: asked.append((sym, itv)) or candles(300, IS.H1 if itv == "60" else IS.DAY, 2000.0 if sym == "ETHUSDT" else 90000.0)   # noqa: E731
    t = IT.IvanTests(d, make_fetch=make, web_dir=web, kline=kl)
    t.add({"name": "Иван Тест 2", "key": KEY, "secret": SEC, "demo": True})
    aid = t.view()["accounts"][0]["id"]
    ctx = os.path.join(d, f"ctx_{aid}.jsonl")
    rows = [json.loads(x) for x in open(ctx, encoding="utf-8")]
    assert [(r["kind"], r.get("baseline")) for r in rows] == [("entry", True)]            # already open at the first look → a baseline
    pos[0].update(size="3", avgPrice="1900", markPrice="1850", unrealisedPnl="-150")     # he averages down: +2 at 1850
    t.watch(now=NOW + 30_000)
    pos.clear()
    t.watch(now=NOW + 60_000)
    rows = [json.loads(x) for x in open(ctx, encoding="utf-8")]
    assert [r["kind"] for r in rows] == ["entry", "add", "exit"] and rows[1]["averaging"] is True and rows[1]["step"] == 2
    assert abs(rows[1]["fill_px"] - 1850.0) < 1e-6 and rows[1]["ctx"]["btc"]["label"] == "bull" and rows[1]["ctx"]["range"]["week"]["high"]
    assert rows[1]["ctx"]["round"]["big"]["below"] == 1000 and rows[1]["ctx"]["swings_h1"] is not None and rows[2]["ctx"]["px"] == rows[2]["px"]
    assert {("ETHUSDT", "60"), ("ETHUSDT", "D"), ("BTCUSDT", "60"), ("BTCUSDT", "D")} <= set(asked) and len(asked) == 4      # cached
    assert KEY not in open(ctx, encoding="utf-8").read() and SEC not in open(ctx, encoding="utf-8").read()
    t.poll(now=NOW + 90_000)
    t.set_copy(aid, True)
    with open(os.path.join(web, "public_real.json"), "w", encoding="utf-8") as f:
        json.dump({"asof": NOW, "futures": [{"inst": "ETH-USDT-SWAP", "side": "long", "upnl_usd": -4.5, "sig": "ivan2:W-ETH-L-1", "opened": 1},
                                            {"inst": "SOL-USDT-SWAP", "side": "long", "upnl_usd": 9.0, "sig": "whales:W-SOL-L-1"},
                                            {"inst": "XRP-USDT-SWAP", "side": "long", "upnl_usd": 7.0, "sig": "ivan:W-XRP-L-1"}],
                   "closed": [{"inst": "BTC-USDT-SWAP", "closed": 50, "pnl_usd": 12.0}, {"inst": "ZEC-USDT-SWAP", "closed": 60, "pnl_usd": -3.0},
                              {"inst": "ADA-USDT-SWAP", "closed": 70, "pnl_usd": 100.0}]}, f)
    with open(os.path.join(web, "real_why.json"), "w", encoding="utf-8") as f:
        json.dump({"closed": [{"inst": "BTC-USDT-SWAP", "closed": 50, "sig": "ivan2:W-BTC-L-1"}, {"inst": "ZEC-USDT-SWAP", "closed": 60, "sig": "ivan2:W-ZEC-L-2"},
                              {"inst": "ADA-USDT-SWAP", "closed": 70, "sig": "whales:W-ADA-L-3"}]}, f)
    a = t.view()["accounts"][0]
    s = a["study"]
    assert a["copy_real"] and a["copy_id"] == "ivan2" and s["adds"] == 1 and s["averagings"] == 1 and s["exits"] == 1 and s["entries"] == 0
    assert s["worst_open_usd"] == 0.0 and "open_pnl_usd" in s and "max_dd_usd" in s and s["last_events"][0]["kind"] == "exit"
    assert a["real"] == {"open_n": 1, "open_pnl_usd": -4.5, "open": [{"inst": "ETH-USDT-SWAP", "side": "long", "upnl_usd": -4.5, "opened": 1}],
                         "closed_n": 2, "closed_pnl_usd": 9.0, "wins": 1, "total_pnl_usd": 4.5, "asof": NOW}
    assert KEY not in json.dumps(t.view(), ensure_ascii=False) and SEC not in json.dumps(t.view(), ensure_ascii=False)
    assert IT.real_copies(None, "ivan2") is None and IT.real_copies(tempfile.mkdtemp(), "ivan2") is None
