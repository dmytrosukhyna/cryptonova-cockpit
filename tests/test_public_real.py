"""«Реальный счёт CRYPTO NOVA на OKX» (cnq/execution/public_real.py → web/public_real.json → app_home public.real, the panel's line):
the sub-account only (never the personal account), exactly the agreed keys, no key / IP / UID / order id, real numbers or
"stale": true, a compact curve, today from a real day-start value, closed trades from OKX's own history.
Every coin, key and amount is made-up test data; OKX and GitHub are never called."""
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile

import okx_fake as F
import test_myspot as TM
import test_myspot_sub as TS
from cnq.agents import app_home
from cnq.execution import okx_lease as L
from cnq.execution import public_real as PR
from test_public_home import T0, _cases

NOW = F.NOW
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(e, fk, now, state, **kw):
    sent = []
    doc = PR.run(e, TS._fetch(fk, kw.pop("calls", [])), now, put=lambda d: sent.append(json.loads(json.dumps(d))) or True, state=state, **kw)
    return doc, sent


def test_public_summary_is_the_sub_account_only_with_exactly_the_agreed_keys():
    e, fk, _log = TS._setup()
    fk.hist = [{"instId": "BTC-USDT-SWAP", "uTime": str(NOW + 1000), "realizedPnl": "3", "posId": "P1"},
               {"instId": "ETH-USDT-SWAP", "uTime": "1000", "realizedPnl": "1", "posId": "P0"}]
    env = {**TM.ENV, **e, "OKX_PUBLIC_STATE": os.path.join(tempfile.mkdtemp(), "pr.json")}   # the personal key is there too
    calls = []
    doc, sent = _run(env, fk, NOW + 60_000, {}, calls=calls)
    (pub,) = sent
    assert list(pub) == list(PR.KEYS) and pub == doc
    assert pub["exchange"] == "OKX" and pub["start_usd"] == 10000.0 and pub["closed_n"] == 1
    assert pub["equity_usd"] == round(sum(x["usd"] for x in pub["spot"]) + pub["usdt_usd"], 2) or abs(
        pub["equity_usd"] - sum(x["usd"] for x in pub["spot"]) - pub["usdt_usd"]) < 0.05
    assert pub["pnl_usd"] == round(pub["equity_usd"] - 10000.0, 2) and abs(pub["pnl_pct"] - pub["pnl_usd"] / 100) < 0.01
    assert {x["sym"] for x in pub["spot"]} >= {"BTC", "ETH", "SOL", "AAA", "BBB", "XRP"} and "DDD" not in {x["sym"] for x in pub["spot"]}
    assert all(list(x) == list(PR.SPOT_KEYS) for x in pub["spot"]) and abs(sum(x["share"] for x in pub["spot"]) + pub["usdt_pct"] - 100) < 0.1
    (f,) = pub["futures"]
    assert list(f) == list(PR.FUT_KEYS) and f["inst"] == "SOL-USDT-SWAP" and f["side"] == "long" and f["stop"] == 190.0 and f["target"] == 230.0
    text = json.dumps(pub, ensure_ascii=False)
    for bad in ("APKEY", "APSECRET", "APPHRASE", "MSKEY", "203.0.113.7", "okx.com", "ordId", "algoId", "posId", "P1", "uid", "host",
                "perm", "DDD"):
        assert bad not in text, bad                                                 # no key, IP, UID, order id, personal coin
    assert all(c[0] == "GET" for c in calls) and not any("/trade/order" == c[1] for c in calls)
    assert not pub["stale"]                                                         # the autopilot holds its lease (it just ran)
    al = pub["alloc"]
    assert list(al) == list(PR.sub.ALLOC_KEYS) and abs(al["spot_cash_usd"] + al["fut_margin_usd"] + al["fut_reserve_usd"] + al["active_wait_usd"]
                                                       + al["other_usd"] - pub["usdt_usd"]) <= 1.0


def test_stale_when_the_autopilot_does_not_run_and_the_last_numbers_when_okx_is_unreadable():
    e, fk, _log = TS._setup()
    e = {**e, "OKX_PUBLIC_STATE": os.path.join(tempfile.mkdtemp(), "pr.json")}
    st = {}
    doc, _ = _run(e, fk, NOW + 60_000, st)
    L.set_stop(e, True)                                                             # the global stop → not running
    doc2, _ = _run(e, fk, NOW + 20 * 60_000, st)
    assert doc2["stale"] and doc2["equity_usd"] == doc["equity_usd"] and doc2["asof"] == NOW + 20 * 60_000
    broken = lambda host, path, params=None, signed=True: {"code": "50110", "msg": "IP", "data": []}  # noqa: E731
    sent = []
    doc3 = PR.run(e, broken, NOW + 40 * 60_000, put=lambda d: sent.append(d) or True, state=st)
    assert doc3["stale"] and doc3["asof"] == doc2["asof"] and doc3["equity_usd"] == doc2["equity_usd"]   # old asof, never new numbers
    assert sent == []                                                               # identical to the last one → nothing to publish
    assert PR.run(e, broken, NOW, put=lambda d: True, state={}) is None             # nothing real yet → nothing at all
    assert PR.run({**e, "OKX_PUBLIC": "off"}, broken, NOW, put=lambda d: True, state={}) is None
    assert PR.enabled({}) is False and PR.enabled(e) is True


def test_curve_one_point_per_15_minutes_compact_and_today_from_a_real_day_start():
    e, fk, _log = TS._setup()
    e = {**e, "OKX_PUBLIC_STATE": os.path.join(tempfile.mkdtemp(), "pr.json")}
    st = {}
    t = NOW + 60_000
    doc, _ = _run(e, fk, t, st)
    since = doc["since"]
    assert doc["curve"][0] == [since, 0.0] and doc["curve"][-1][0] == t
    doc, _ = _run(e, fk, t + 5 * 60_000, st)
    assert doc["curve"][-1][0] == t                                                 # 5 minutes later: no new point
    day0 = t - t % PR.DAY_MS
    assert doc["today_usd"] == (doc["pnl_usd"] if since >= day0 else doc["today_usd"])
    st["curve"] = [[since + i * PR.STEP_MS, float(i)] for i in range(2000)]
    c = PR.compact(st["curve"])
    assert len(c) <= PR.CURVE_MAX and c[-PR.CURVE_RECENT:] == st["curve"][-PR.CURVE_RECENT:]
    assert all(b[0] - a[0] >= PR.STEP_MS for a, b in zip(c, c[1:]))
    nxt = day0 + PR.DAY_MS + 3_600_000                                              # the next day: today = change since the last point before 00:00 UTC
    st2 = {"curve": [[since, 0.0], [day0 + PR.DAY_MS - 60_000, -50.0]]}
    doc, _ = _run(e, fk, nxt, st2)
    assert doc["today_usd"] == round(doc["pnl_usd"] + 50.0, 2) and doc["today_pct"] == round((doc["pnl_usd"] + 50.0) / 9950.0 * 100, 2)
    st3 = {"curve": [[since, 0.0]]}
    doc, _ = _run(e, fk, nxt + PR.DAY_MS, {**st3, "curve": [[nxt + PR.DAY_MS - 1000, 1.0]]})
    assert doc["today_usd"] is None or isinstance(doc["today_usd"], float)


def test_app_home_public_real_in_every_language_and_stale_by_age(tmp_path):
    d = str(tmp_path)
    c = _cases()[0]
    for name, obj in (("paper.json", {**c["paper"], "profile_list": [{"key": "bal", "risk": 0.01, "max_open": 4}]}),
                      ("portfolio.json", {"portfolios": {"ideas": c["ideas"]}})):
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            json.dump(obj, f)
    now = T0 + 70 * 3_600_000
    real = {"exchange": "OKX", "since": T0, "start_usd": 10000.0, "equity_usd": 10012.5, "pnl_usd": 12.5, "pnl_pct": 0.13, "today_usd": 2.0,
            "today_pct": 0.02, "spot": [{"sym": "BTC", "share": 19.5, "usd": 1952.4, "extra": "x"}], "usdt_pct": 40.0, "usdt_usd": 4005.0,
            "alloc": {"spot_coins_usd": 5000.0, "spot_cash_usd": 650.0, "fut_budget_usd": 2500.0, "fut_margin_usd": 300.0, "fut_reserve_usd": 2200.0,
                      "active_coins_usd": 0.0, "active_wait_usd": 1000.0, "other_usd": -145.0, "spot_pct": 65.0, "fut_pct": 25.0, "active_pct": 10.0,
                      "secret": "x"},
            "futures": [{"inst": "SOL-USDT-SWAP", "side": "long", "entry": 200.0, "stop": 190.0, "target": 230.0, "upnl_pct": 0.5,
                         "upnl_usd": 1.2, "ordId": "123"}], "closed_n": 3, "curve": [[T0, 0.0], [now - 60_000, 12.5]],
            "asof": now - 60_000, "stale": False, "host": "www.okx.com"}
    with open(os.path.join(d, "public_real.json"), "w", encoding="utf-8") as f:
        json.dump(real, f)
    doc = app_home.run(sqlite3.connect(":memory:"), d, now=now)
    r = doc["public"]["real"]
    assert list(r) == list(app_home.REAL_KEYS) + list(app_home.REAL_OPT) and r["equity_usd"] == 10012.5 and not r["stale"]
    assert list(r["alloc"]) == list(app_home.REAL_ALLOC) and r["alloc"]["fut_margin_usd"] == 300.0
    assert r["spot"] == [{"sym": "BTC", "share": 19.5, "usd": 1952.4, "copy": None, "copy_reason": None, "copy_pct": None}] and "ordId" not in r["futures"][0] and "host" not in r
    for lang in ("en", "uk", "el", "es"):
        other = json.load(open(os.path.join(d, f"app_home.{lang}.json"), encoding="utf-8"))["public"]["real"]
        assert other == r, lang
    assert app_home.run(sqlite3.connect(":memory:"), d, now=now + 3 * 3_600_000)["public"]["real"]["stale"]   # older than 2 h
    os.remove(os.path.join(d, "public_real.json"))
    assert "real" not in app_home.run(sqlite3.connect(":memory:"), d, now=now)["public"]


def test_panel_line_and_the_light_publish():
    html = open(os.path.join(ROOT, "web", "app.html"), encoding="utf-8").read()
    assert 'id="realLine"' in html and "Реальный счёт CRYPTO NOVA на OKX · с ${d}" in html and "./public_real.json" in html
    assert "web/public_real.json" in open(os.path.join(ROOT, ".github", "workflows", "publish.yml")).read()
    assert '"web/public_real.json"' in open(os.path.join(ROOT, ".github", "workflows", "site.yml")).read()   # no heavy pipeline run
    node = shutil.which("node")
    if not node:
        return
    a = html.index("const money=v=>", html.index("«Публичный портфель» = the REAL CRYPTO NOVA account on OKX"))
    b = html.index("async function load(){ const el=document.getElementById('realLine')")
    js = html[a:b] + "\nconst R=JSON.parse(process.argv[2]);process.stdout.write(line(R,+process.argv[3]));"
    p = os.path.join(tempfile.mkdtemp(), "l.js")
    open(p, "w", encoding="utf-8").write(js)
    R = {"since": 1792627200000, "equity_usd": 10012.5, "pnl_usd": 12.5, "pnl_pct": 0.13, "asof": 1792630000000, "stale": False,
         "alloc": {"spot_pct": 65.0, "fut_pct": 25.0, "active_pct": 10.0, "fut_margin_usd": 400.5}}
    out = subprocess.run([node, p, json.dumps(R), str(1792630000000 + 60_000)], capture_output=True, text=True, timeout=30).stdout
    assert "Реальный счёт CRYPTO NOVA на OKX · с 22.10" in out and "+$12,5" in out and "+0,13%" in out and "устарели" not in out
    assert "распределены: спот 65% · фьючерсы 25% (в позициях 4,0%) · активный спот 10%" in out
    out = subprocess.run([node, p, json.dumps(R), str(1792630000000 + 3 * 3_600_000)], capture_output=True, text=True, timeout=30).stdout
    assert "данные устарели" in out


def test_one_writer_the_mac_mirror_or_the_server_service_never_both():
    import inspect
    from cnq.execution import okx_autopilot as ap
    e, fk, _log = TS._setup()
    e = {**e, "OKX_PUBLIC_STATE": os.path.join(tempfile.mkdtemp(), "pr.json")}
    srv = {**e, "OKX_NODE": "server", "OKX_LEASE_ID": "server"}
    assert PR.writer(e) and not PR.writer(srv)                                     # the autopilot runs on the Mac: the Mac writes
    calls = []
    assert PR.run(srv, TS._fetch(fk, calls), NOW, put=lambda d: True, state={}) is None and calls == []
    L.release(e)
    assert L.switch(srv, "server", NOW + 60_000)[0]                                # install moved it to the server
    assert PR.writer(srv) and not PR.writer(e)
    assert PR.run(e, TS._fetch(fk, calls), NOW, put=lambda d: True, state={}) is None and calls == []   # the Mac mirror skips
    sent = []
    assert PR.run(srv, TS._fetch(fk, []), NOW + 120_000, put=lambda d: sent.append(d) or True, state={}) and len(sent) == 1
    assert not PR.writer({"OKX_NODE": "mac"})                                     # no token / no lease → nobody writes
    loop = inspect.getsource(ap._main)
    assert 'lease.node_of() == "server"' in loop and "public_real.run()" in loop and ap.PUBLIC_EVERY_S == PR.EVERY_S
    doc = open(os.path.join(ROOT, "docs", "okx_server_mode.md"), encoding="utf-8").read()
    for s in ("91.99.193.20", "188.4.179.38", "не удалять", "cp /opt/cnq/repo/deploy/okx/cnq-okx.env.example /etc/cnq-okx.env",
              "scp ~/.cryptonova_okx_*.json ~/.cryptonova_public_real.json root@91.99.193.20:/root/",
              "bash /opt/cnq/repo/scripts/okx_autopilot_server.sh install", "**ДА**"):
        assert s in doc, s
    for p in ("deploy/okx/cnq-okx.env.example", "scripts/okx_autopilot_server.sh"):
        assert os.path.exists(os.path.join(ROOT, p))


def test_closed_swaps_futures_curve_and_the_desk_trade_behind_a_position():
    """28.10 (build 46): the real account's own closed futures (OKX positions history, no ids), a futures curve at the curve's own
    timestamps (spot = total − futures), and each open position names the desk trade it mirrors."""
    e, fk, _log = TS._setup()
    e = {**e, "OKX_PUBLIC_STATE": os.path.join(tempfile.mkdtemp(), "pr.json")}
    fk.hist = [{"instId": "APT-USDT-SWAP", "direction": "long", "openAvgPx": "0.7747", "closeAvgPx": "0.7812", "realizedPnl": "0.27",
                "lever": "2", "cTime": str(NOW + 500), "uTime": str(NOW + 2000), "posId": "P9", "type": "2"},
               {"instId": "ETH-USDT-SWAP", "direction": "short", "openAvgPx": "4000", "closeAvgPx": "4100", "realizedPnl": "-5.5",
                "lever": "2", "cTime": str(NOW + 100), "uTime": str(NOW + 1000), "posId": "P8"},
               {"instId": "BTC-USDT-SWAP", "direction": "long", "openAvgPx": "1", "closeAvgPx": "2", "realizedPnl": "9",
                "uTime": "1000", "posId": "P0"}]                                              # before the start: not ours
    st = {}
    doc, (pub,) = _run(e, fk, NOW + 60_000, st)
    assert doc["closed_n"] == 2 and [c["inst"] for c in pub["closed"]] == ["APT-USDT-SWAP", "ETH-USDT-SWAP"]   # newest first
    apt, eth = pub["closed"]
    assert list(apt) == list(PR.CLOSED_KEYS) and apt["side"] == "long" and apt["pnl_usd"] == 0.27 and apt["lev"] == 2.0
    assert apt["move_pct"] == round((0.7812 / 0.7747 - 1) * 100, 2) and eth["move_pct"] == -2.5 and eth["side"] == "short"
    assert apt["opened"] == NOW + 500 and apt["closed"] == NOW + 2000 and apt["partial"] is False
    assert "P9" not in json.dumps(pub) and "posId" not in json.dumps(pub)
    (f,) = pub["futures"]
    assert f["sig"] and f["sig"].startswith("bal:") and f["sig"].endswith("T1") and list(f) == list(PR.FUT_KEYS)
    # the futures curve: the start (0 by definition) and every curve point from now on, at the same timestamps
    assert pub["fut_curve"][0] == [doc["since"], 0.0] and {t for t, _ in pub["fut_curve"]} <= {t for t, _ in pub["curve"]}
    assert pub["fut_curve"][-1] == [NOW + 60_000, pub["fut_pnl_usd"]]
    doc2, _ = _run(e, fk, NOW + 60_000 + PR.STEP_MS, st)
    assert len(doc2["fut_curve"]) == len(doc["fut_curve"]) + 1 and doc2["fut_curve"][-1][0] == doc2["curve"][-1][0]
    # clean() keeps only the known shapes
    bad = {**doc2, "closed": [{"inst": "<x>", "pnl_usd": 1}, {**apt, "posId": "P9", "pnl_usd": "1"}],
           "futures": [{**f, "sig": "x; drop", "mark": "1", "lev": True}], "fut_curve": [[1, 2.0], ["a", 1], [2]]}
    c = PR.clean(bad)
    assert len(c["closed"]) == 1 and c["closed"][0]["pnl_usd"] is None and "posId" not in c["closed"][0]
    assert c["futures"][0]["sig"] is None and c["futures"][0]["mark"] is None and c["futures"][0]["lev"] is None
    assert c["fut_curve"] == [[1, 2.0]]


def test_a_last_summary_from_before_28_10_is_republished_stale_with_the_new_keys_empty():
    e, fk, _log = TS._setup()
    e = {**e, "OKX_PUBLIC_STATE": os.path.join(tempfile.mkdtemp(), "pr.json")}
    st = {}
    doc, _ = _run(e, fk, NOW + 60_000, st)
    st["last"] = {k: v for k, v in doc.items() if k not in ("closed", "fut_curve")}     # an older «last»
    broken = lambda host, path, params=None, signed=True: {"code": "50110", "msg": "IP", "data": []}  # noqa: E731
    sent = []
    d = PR.run(e, broken, NOW + 40 * 60_000, put=lambda x: sent.append(x) or True, state=st)
    assert d["stale"] and d["closed"] == [] and d["fut_curve"] == [] and list(d) == list(PR.KEYS) and len(sent) == 1


def test_actions_are_the_autopilots_own_filled_orders_without_ids():
    """28.10 (build 46 §12.4 / §14): «what the system did» on the real account comes from the autopilot's own logs — spot orders,
    the futures open — newest first, no order id; nothing is shown that did not fill."""
    from cnq.execution import okx_active as ac, okx_autopilot as ap, okx_futures as fu
    e, fk, _log = TS._setup()
    e = {**e, "OKX_PUBLIC_STATE": os.path.join(tempfile.mkdtemp(), "pr.json")}
    ap.log({"ev": "order", "ccy": "QQQ", "side": "sell", "sz": "1", "px": "0.1", "filled": 0.0, "usd": 0.0, "ordId": "X1"}, ap.LOG)
    ap.log({"ev": "buy", "id": "i9", "inst": "XRP-USDT", "filled": 50.0, "avg_px": 2.5, "usd": 125.0, "ordId": "X2"}, ac.LOG)
    fu.log({"ev": "gone", "id": "bal:T0", "inst": "ETH-USDT-SWAP", "note": "закрыта на OKX (стоп или цель)"}, fu.LOG)
    doc, (pub,) = _run(e, fk, NOW + 60_000, {})
    A = pub["actions"]
    kinds = {a["kind"] for a in A}
    assert {"spot_buy", "spot_sell", "fut_open", "active_buy", "fut_gone"} <= kinds and "QQQ" not in {a["sym"] for a in A}   # unfilled: not there
    assert all(list(a) == list(PR.ACTION_KEYS) for a in A) and [a["t"] for a in A] == sorted((a["t"] for a in A), reverse=True)
    op = next(a for a in A if a["kind"] == "fut_open")
    assert op["sym"] == "SOL" and op["side"] == "long" and op["sig"] and op["sig"].endswith("T1")
    xrp = next(a for a in A if a["kind"] == "active_buy")
    assert xrp["sym"] == "XRP" and xrp["usd"] == 125.0 and xrp["sig"] is None
    assert next(a for a in A if a["kind"] == "fut_gone")["sym"] == "ETH"
    text = json.dumps(A)
    assert "ordId" not in text and "X2" not in text and "X1" not in text
    assert PR.actions(NOW + 10 ** 9) == []                                        # nothing before the start counts
    assert PR._tail(os.path.join(tempfile.mkdtemp(), "none.jsonl")) == []


def _pp_files(d, real, paper=None):
    c = _cases()[0]
    p = paper or {**c["paper"], "profile_list": [{"key": "bal", "risk": 0.01, "max_open": 4}]}
    for name, obj in (("paper.json", p), ("portfolio.json", {"portfolios": {"ideas": c["ideas"]}}), ("public_real.json", real)):
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            json.dump(obj, f)


def test_public_portfolio_is_the_real_account_only_since_its_start(tmp_path):
    """28.10 (build 46): app_home "public_portfolio" = the real OKX account (web/public_real.json) — not the $20 000 demo header
    from 17.10. Periods, allocation, the spot / futures parts and actions all come from that one file and add up."""
    d = str(tmp_path)
    H = 3_600_000
    since = T0 + 100 * H
    now = since + 6 * 24 * H + 5 * H
    day0 = now - now % (24 * H)
    curve = [[since, 0.0], [since + 24 * H, -40.0], [day0 - 2 * H, -60.0], [day0 + H, -50.0], [now - 60_000, -30.0]]
    fcurve = [[day0 - 2 * H, -5.0], [day0 + H, -4.0], [now - 60_000, -2.0]]
    paper = json.loads(json.dumps(_cases()[0]["paper"]))
    paper["profiles"]["bal"]["open"].append({"id": "F-SOL-1", "symbol": "SOL", "side": "LONG", "grade": "F", "kind": "OI_FLUSH",
                                             "entry": 200.0, "stop": 190.0, "tps": [230.0], "price": 201.0, "opened": since + H})
    real = {"exchange": "OKX", "since": since, "start_usd": 10000.0, "equity_usd": 9970.0, "pnl_usd": -30.0, "pnl_pct": -0.3,
            "today_usd": 30.0, "today_pct": 0.3, "spot": [{"sym": "BTC", "share": 40.0, "usd": 3988.0}, {"sym": "ZEC", "share": 10.0, "usd": 997.0}],
            "usdt_pct": 50.0, "usdt_usd": 4985.0,
            "alloc": {"spot_coins_usd": 4985.0, "spot_cash_usd": 1500.0, "fut_budget_usd": 2492.5, "fut_margin_usd": 100.0, "fut_reserve_usd": 2392.5,
                      "active_coins_usd": 0.0, "active_wait_usd": 990.0, "other_usd": 2.5, "spot_pct": 65.0, "fut_pct": 25.0, "active_pct": 10.0},
            "futures": [{"inst": "SOL-USDT-SWAP", "side": "long", "entry": 200.0, "stop": 190.0, "target": 230.0, "upnl_pct": 0.5,
                         "upnl_usd": 1.5, "copy": False, "copy_reason": "ordinary", "mark": 201.0, "lev": 2.0, "opened": since + H, "sig": "bal:F-SOL-1"}],
            "closed_n": 3, "curve": curve, "asof": now - 60_000, "stale": False, "copiers": 0, "copy_url": None, "spot_pnl_usd": -28.0,
            "fut_pnl_usd": -2.0,
            "closed": [{"inst": "SOL-USDT-SWAP", "side": "long", "open_px": 199.0, "close_px": 201.0, "move_pct": 1.01, "pnl_usd": 0.5,
                        "lev": 2.0, "opened": since + H, "closed": since + 2 * H, "partial": True},
                       {"inst": "APT-USDT-SWAP", "side": "long", "open_px": 0.77, "close_px": 0.78, "move_pct": 1.3, "pnl_usd": 2.0,
                        "lev": 2.0, "opened": since, "closed": since + 3 * H, "partial": False},
                       {"inst": "ETH-USDT-SWAP", "side": "short", "open_px": 4000.0, "close_px": 4100.0, "move_pct": -2.5, "pnl_usd": -6.0,
                        "lev": 2.0, "opened": since, "closed": since + 4 * H, "partial": False}],
            "fut_curve": fcurve,
            "actions": [{"t": now - 2 * H, "kind": "fut_open", "sym": "SOL", "side": "long", "usd": None, "px": 200.0, "sig": "bal:F-SOL-1"},
                        {"t": now - 3 * H, "kind": "spot_buy", "sym": "BTC", "side": None, "usd": 1234.5, "px": 80000.0, "sig": None}]}
    _pp_files(d, real, paper)
    doc = app_home.run(sqlite3.connect(":memory:"), d, now=now)
    pp = doc["public_portfolio"]
    assert pp["kind"] == "real" and pp["since"] == since and pp["start_usd"] == 10000.0 and pp["value_usd"] == 9970.0
    assert doc["public"]["base_usd"] == 20000.0                       # the demo header stays as it was (older builds, DEMO mode)
    assert "22" not in pp["title"] and pp["note"].startswith("Реальный счёт CRYPTO NOVA на OKX")
    P = pp["periods"]
    assert P["all"] == {"from": since, "clipped": False, "available": True, "base_usd": 10000.0, "usd": -30.0, "pct": -0.3}
    assert P["day"]["from"] == day0 and P["day"]["usd"] == 30.0 and P["day"]["base_usd"] == 9940.0          # = today_usd
    assert P["week"]["clipped"] and P["week"]["from"] == since and P["week"]["usd"] == -30.0               # 6 days of history
    assert pp["curve"][0] == [since, 10000.0] and pp["curve"][-1] == [now - 60_000, 9970.0]
    # the allocation: the groups add up to the value, the categories too, from the same file
    al = pp["alloc"]
    assert abs(sum(g["usd"] for g in al["groups"]) - 9970.0) < 0.01 and abs(sum(c["usd"] for c in al["categories"]) - 9970.0) < 0.01
    assert [c["key"] for c in al["categories"]] == ["core", "alt", "cash"] and al["assets"][-1]["sym"] == "USDT"
    # the parts: their bases are the system's shares of the starting capital; spot + futures = the whole result
    S, F = pp["spot"], pp["futures"]
    assert S["start_usd"] == 7500.0 and F["start_usd"] == 2500.0 and S["pnl_usd"] + F["pnl_usd"] == pp["pnl_usd"]
    assert S["value_usd"] + F["value_usd"] == pp["value_usd"] and F["pct"] == round(-2.0 / 2500 * 100, 2)
    assert F["periods"]["day"]["available"] and F["periods"]["day"]["usd"] == 3.0                 # −2 now vs −5 before 00:00
    assert S["periods"]["day"]["usd"] == round(-28.0 - (-60.0 + 5.0), 2)                         # spot = total − futures
    assert pp["split_note"] and S["curve"][-1] == [now - 60_000, 7500.0 - 28.0]
    assert abs(sum(c["share_spot"] for c in S["coins"]) + (S["cash_usd"] / S["holdings_usd"] * 100) - 100) < 0.1
    # futures: the open SOL with the desk's own «why», the partly closed SOL stays under «open», stats from the closed ones
    (o,) = F["open"]
    assert o["sym"] == "SOL" and o["price"] == 201.0 and o["lev"] == 2.0 and o["why"] and o["why_title"] and o["closed_part_usd"] == 0.5
    assert [c["sym"] for c in F["closed"]] == ["APT", "ETH"] and F["closed"][1]["side"] == "SHORT"
    st = F["stats"]
    assert (st["n"], st["wins"], st["losses"], st["win_rate"], st["complete"]) == (3, 1, 1, 50.0, True)
    assert st["realized_usd"] == -3.5 and st["avg_usd"] == round(-3.5 / 3, 2)                     # fut result − open P/L
    # actions in plain words, in every language
    assert pp["actions"][0]["text"] == "Открыта фьючерсная сделка SOL на рост (LONG) по цене 200.00; стоп сразу стоит на бирже."
    assert pp["actions"][1]["text"] == "Куплено BTC на $1 234,50: спот-портфель приведён к плану системы."
    en = json.load(open(os.path.join(d, "app_home.en.json"), encoding="utf-8"))["public_portfolio"]
    assert en["actions"][1]["text"].startswith("Bought BTC for $1,234.50") and " Oct 2026 " in en["note"]
    for lang in ("uk", "el", "es"):
        other = json.load(open(os.path.join(d, f"app_home.{lang}.json"), encoding="utf-8"))["public_portfolio"]
        assert other["periods"] == pp["periods"] and other["alloc"]["groups"][0]["usd"] == al["groups"][0]["usd"], lang
        assert other["note"] != pp["note"] and other["actions"][0]["text"] != pp["actions"][0]["text"], lang
    # no real file → no public portfolio (the app shows its empty state, never the demo numbers instead)
    os.remove(os.path.join(d, "public_real.json"))
    assert app_home.run(sqlite3.connect(":memory:"), d, now=now)["public_portfolio"] is None


def test_market_view_spot_actions_and_demo_stats_in_plain_words(tmp_path):
    """28.10 (build 46): §10.4 the market in plain words from the app's own data (no forecast, no closed-system outlook); §12.4 the
    spot portfolio's actions said simply; §10.6 the demo futures' closed trades in the one-account scale (they add up)."""
    d = str(tmp_path)
    c = _cases()[0]
    _pp_files(d, {"exchange": "OKX"})                                     # an unusable real file: no public portfolio
    now = T0 + 70 * 3_600_000
    mk = {"asof": now, "btc_bull": True, "fng": {"value": 51, "label": "нейтрально", "week": 68},
          "top": [{"symbol": "BTC", "price": 80895, "chg24": -6.2, "chg7": -2.06, "chg30": -2.85, "chg1y": -28.88, "ath_chg": -35.84}]}
    acts = [{"time": now - 3_600_000, "action": "SELL", "symbol": "AAVE", "price": 136.34,
             "why_ru": "Недельный пересмотр: в таблице «ждём» (Анкета 38/100 ниже порога 40 — ждём), позиция −11% от входа — освобождаем место."},
            {"time": now - 2 * 3_600_000, "action": "BUY", "symbol": "HYPE", "price": 78.43,
             "why_ru": "Исключение владельца (22.10): HYPE из списка доверия — на небольшую сумму, хотя таблица пишет «Анкета 24/100 ниже "
                       "порога 40 — ждём». 2.5% счёта, стоп −35%, на ×2 продаём половину"},
            {"time": now - 3 * 3_600_000, "action": "SELL", "symbol": "XMR", "price": 609.84,
             "why_ru": "Правило с 22.10: портфель держит только монеты со спот-парой на OKX — его повторяет реальный счёт CRYPTO NOVA. "
                       "XMR на споте OKX нет — продаём, итог +9% от входа; деньги — в ядро"},
            {"time": now - 4 * 3_600_000, "action": "ADD", "symbol": "BTC", "price": 81527.0,
             "why_ru": "Замена: монет без спот-пары на OKX в портфеле больше нет — их деньги в ядро по плану BTC 30 / ETH 10 / SOL 5, BTC +8.4% счёта."}]
    for name, obj in (("markets.json", mk), ("app_portfolio.json", {"index": 99.0, "cash_pct": 20, "history": [], "positions": [],
                                                                       "recent_actions": acts})):
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            json.dump(obj, f)
    doc = app_home.run(sqlite3.connect(":memory:"), d, now=now)
    mv = doc["market_view"]
    assert mv["now"] == "Биткоин сейчас $80 895, за сутки −6,2%." and mv["short"] == "За неделю −2,1%: небольшое снижение."
    assert "режим «рост»" in mv["mid"] and "разворота не обещает" in mv["mid"] and mv["long"] == "За год −28,9%; биткоин на 35,8% ниже своего рекорда."
    assert mv["factors"] == ["Индекс страха и жадности — 51 (нейтрально), неделю назад был 68."]
    assert mv["risks"][0].startswith("Сильное движение за сутки (−6,2%)") and "стоп −20% от покупки" in mv["risks"][-1]
    assert mv["system"] == [] and mv["limits"].startswith("Это описание того, что уже произошло") and "прогноз" in mv["limits"]
    text = json.dumps(mv, ensure_ascii=False).lower()
    for bad in ("вырастет", "упадёт до", "гарант", "коридор", "сценарий", "вероятн"):                  # no forecast words
        assert bad not in text, bad
    en = json.load(open(os.path.join(d, "app_home.en.json"), encoding="utf-8"))
    assert en["market_view"]["now"] == "Bitcoin is at $80,895 now, −6.2% over the past day." and en["market_view"]["btc"] == mv["btc"]
    A = [a["text"] for a in doc["spot"]["actions"]]
    assert A[0].startswith("Продано AAVE: монета перестала проходить проверку качества системы (оценка 38 из 100 при пороге 40)")
    assert A[1].startswith("Куплено HYPE на небольшую долю (2,5% портфеля) по решению владельца") and A[1].endswith("Стоп −35%.")
    assert A[2] == "Продано XMR: этой монеты нет на споте OKX, а портфель повторяет реальный счёт CRYPTO NOVA на OKX. Итог +9% от входа."
    assert A[3] == "Докуплено BTC (+8,4% портфеля): деньги проданных монет переложены в ядро портфеля."
    for t_ in A:
        for jargon in ("Исключение владельца", "списка доверия", "Анкета", "ниже порога 40 — ждём"):
            assert jargon not in t_, (jargon, t_)
    for lang in ("en", "uk", "el", "es"):
        o = json.load(open(os.path.join(d, f"app_home.{lang}.json"), encoding="utf-8"))
        assert len(o["spot"]["actions"]) == 4 and all(x["text"] for x in o["spot"]["actions"]), lang
        assert [x["sym"] for x in o["spot"]["actions"]] == ["AAVE", "HYPE", "XMR", "BTC"], lang
    # demo futures: the closed trades in the one-account scale add up to the stats; the % has the $10 000 account as its base
    st = doc["futures"]["stats"]
    assert st["base_usd"] == 10000.0 and st["n"] == st["wins"] + st["losses"] + sum(
        1 for k in doc["futures"]["in_result"] for x in c["paper"]["profiles"][k].get("closed") or [] if not x.get("pnl_usd"))
    for r in doc["futures"]["closed"]:
        assert r["acct_usd"] == round(r["pnl_usd"] * 10000 / (10000 * len(doc["futures"]["in_result"])), 2)


def _flow_fetch(fk, fund=(), trade=(), candle=None, fail=False):
    """the fake OKX + the bills and candles of the money flows (made-up)"""
    base = TS._fetch(fk, [])

    def fetch(host, path, params=None, signed=True):
        if fail and path in (PR.FUND_BILLS, PR.TRADE_BILLS):
            return {"code": "50011", "msg": "rate limit", "data": []}
        if path == PR.FUND_BILLS:
            return {"code": "0", "data": list(fund)}
        if path == PR.TRADE_BILLS:
            return {"code": "0", "data": list(trade)}
        if path == "/api/v5/market/history-candles":
            return {"code": "0", "data": [[str(int(params["after"]) - 60_000), "1", "1", "1", str(candle), "1"]] if candle else []}
        return base(host, path, params, signed)
    return fetch


def test_deposits_withdrawals_and_transfers_are_not_a_result():
    """28.10 (owner): money moved in or out of the account is not profit or loss. A master → sub transfer of $1 000 and a withdrawal
    of 0.05 ETH (valued at its own time) are flows; the account's own funding ↔ trading move is not. The result, today, the curve and
    the % (Modified Dietz) leave the flows out; no bill id is published; a failed read keeps what is known."""
    e, fk, _log = TS._setup()
    e = {**e, "OKX_PUBLIC_STATE": os.path.join(tempfile.mkdtemp(), "pr.json")}
    st = {}
    t = NOW + 60_000
    put = lambda d: True  # noqa: E731
    doc0 = PR.run(e, _flow_fetch(fk), t, put=put, state=st)
    since, p0 = doc0["since"], doc0["pnl_usd"]
    assert doc0["flows"]["n"] == 0 and doc0["flows"]["net_usd"] == 0.0
    t1 = t + PR.STEP_MS
    fk.bal["USDT"] += 1000.0                                                   # the owner moves $1 000 in from the main account
    fund = [{"billId": "B1", "type": "23", "ccy": "USDT", "balChg": "1000", "ts": str(t1 - 30_000)},
            {"billId": "B2", "type": "131", "ccy": "USDT", "balChg": "-1000", "ts": str(t1 - 20_000)},        # funding → trading (own)
            {"billId": "B3", "type": "2", "ccy": "ETH", "balChg": "-0.05", "ts": str(t1 - 10_000)},           # a withdrawal of ETH
            {"billId": "B4", "type": "150", "ccy": "USDT", "balChg": "3", "ts": str(t1 - 5_000)}]             # a reward: a result, not a flow
    trade = [{"billId": "C1", "type": "1", "subType": "11", "ccy": "USDT", "balChg": "1000", "ts": str(t1 - 19_000)}]  # the twin of B2
    fk.bal["ETH"] = max(0.0, fk.bal.get("ETH", 0.0))
    doc1 = PR.run(e, _flow_fetch(fk, fund, trade, candle=4000.0), t1, put=put, state=st)
    F = doc1["flows"]
    assert F["n"] == 2 and F["in_usd"] == 1000.0 and F["out_usd"] == 200.0 and F["net_usd"] == 800.0 and F["last_t"] == t1 - 10_000
    assert F["by"] == {"deposit": [0, 0.0], "withdrawal": [1, -200.0], "transfer": [1, 1000.0]}
    assert doc1["equity_usd"] == round(doc0["equity_usd"] + 1000.0, 2)                    # the money is really there …
    assert abs(doc1["pnl_usd"] - (doc1["equity_usd"] - doc1["start_usd"] - 800.0)) < 0.011     # … but the result leaves the flows out
    assert abs(doc1["pnl_usd"] - (p0 + 200.0)) < 0.02                       # the ETH that left was in the equity before: +200 back in
    cap = PR.dietz(doc1["start_usd"], [(t1 - 30_000, 1000.0), (t1 - 10_000, -200.0)], since, t1)
    assert doc1["pnl_pct"] == round(doc1["pnl_usd"] / cap * 100, 2)
    assert doc1["curve"][-1] == [t1, doc1["pnl_usd"]] and doc1["curve"][0] == [since, 0.0]    # no jump at the deposit
    assert st["curve"][-1][1] == round(doc1["equity_usd"] - doc1["start_usd"], 2)             # the state keeps the raw value
    text = json.dumps(doc1)
    for bad in ("B1", "B2", "B3", "C1", "billId"):
        assert bad not in text, bad
    # a failed read keeps the flows already known; the same bills are never counted twice
    doc2 = PR.run(e, _flow_fetch(fk, fail=True), t1 + 60_000, put=put, state=st)
    assert doc2["flows"]["n"] == 2 and doc2["flows"]["net_usd"] == 800.0
    doc3 = PR.run(e, _flow_fetch(fk, fund, trade, candle=4000.0), t1 + 120_000, put=put, state=st)
    assert doc3["flows"]["n"] == 2
    # a coin whose price can not be read now is not counted yet (tried again next time), never valued at a guess
    st2 = {}
    PR.read_flows(_flow_fetch(fk, [{"billId": "X", "type": "1", "ccy": "ABC", "balChg": "5", "ts": str(t1)}]), "https://www.okx.com", since, st2)
    assert st2["flows"] == {}
    assert PR.clean({**doc1, "flows": {"n": "x", "by": {"deposit": "y"}, "events": [[1, "z"]]}})["flows"]["n"] == 0


def test_public_portfolio_periods_leave_the_flows_out(tmp_path):
    """28.10 (owner): a $1 000 deposit during the week is not a result in any period; the % divides by the money that was in
    (Modified Dietz); the note says so in every language; the app's chart (start + result) has no jump."""
    d = str(tmp_path)
    H = 3_600_000
    since = T0 + 100 * H
    now = (since + 6 * 24 * H) // (24 * H) * (24 * H) + 12 * H             # 12:00 UTC
    day0 = now - now % (24 * H)
    tf = day0 + 2 * H                                                     # the deposit: today, after 00:00 UTC
    curve = [[since, 0.0], [day0 - H, -50.0], [day0 + 3 * H, -45.0], [now - 60_000, -40.0]]   # already without the flow
    real = {"exchange": "OKX", "since": since, "start_usd": 10000.0, "equity_usd": 10960.0, "pnl_usd": -40.0, "pnl_pct": -0.39,
            "today_usd": 10.0, "today_pct": 0.1, "spot": [], "usdt_pct": 100.0, "usdt_usd": 10960.0, "alloc": None, "futures": [],
            "closed_n": 0, "curve": curve, "asof": now - 60_000, "stale": False,
            "flows": {"in_usd": 1000.0, "out_usd": 0.0, "net_usd": 1000.0, "n": 1, "last_t": tf, "events": [[tf, 1000.0]],
                      "by": {"deposit": [0, 0.0], "withdrawal": [0, 0.0], "transfer": [1, 1000.0]}}}
    _pp_files(d, real)
    doc = app_home.run(sqlite3.connect(":memory:"), d, now=now)
    pp = doc["public_portfolio"]
    P = pp["periods"]
    asof = now - 60_000
    assert P["day"]["usd"] == 10.0 and P["day"]["base_usd"] == 9950.0                        # −40 now vs −50 before 00:00
    cap_day = 9950.0 + 1000.0 * (asof - tf) / (asof - day0)
    assert P["day"]["pct"] == round(10.0 / cap_day * 100, 2)
    assert P["all"]["usd"] == -40.0 and P["all"]["pct"] == round(-40.0 / (10000.0 + 1000.0 * (asof - tf) / (asof - since)) * 100, 2)
    assert pp["value_usd"] == 10960.0 and pp["capital_usd"] == 11000.0 and pp["curve"][-1] == [asof, 9960.0]
    assert pp["flows"] == {"in_usd": 1000.0, "out_usd": 0.0, "net_usd": 1000.0, "n": 1, "last_t": tf}
    assert pp["flows_note"] == "Пополнения и выводы не считаются результатом: +$1 000,00."
    en = json.load(open(os.path.join(d, "app_home.en.json"), encoding="utf-8"))["public_portfolio"]
    assert en["flows_note"] == "Deposits and withdrawals don't count as the result: +$1,000.00." and en["periods"] == P
    from cnq.api import nova
    assert "пополнения и выводы (+$1 000) не считаются результатом" in nova.Knowledge(os.path.join(ROOT, "data")).demo_block(doc)
