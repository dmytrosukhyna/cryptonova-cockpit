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
    assert list(r) == list(app_home.REAL_KEYS) and r["equity_usd"] == 10012.5 and not r["stale"]
    assert list(r["alloc"]) == list(app_home.REAL_ALLOC) and r["alloc"]["fut_margin_usd"] == 300.0
    assert r["spot"] == [{"sym": "BTC", "share": 19.5, "usd": 1952.4}] and "ordId" not in r["futures"][0] and "host" not in r
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
