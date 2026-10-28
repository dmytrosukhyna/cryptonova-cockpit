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
