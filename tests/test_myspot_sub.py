"""«Мой портфель · OKX» + the CRYPTO NOVA sub-account (cnq/execution/myspot_sub.py): read-only (no POST in the source, only GET
requests at run time, no order ever), inside the SAME encrypted blob only, the card unchanged without OKX_API_*, the start of
«результат с начала» from the autopilot's state / log / env, the autopilot's own «совпадает» line, positions with their OKX stops.
Every coin, key and amount is made-up test data; OKX and GitHub are never called."""
import base64
import inspect
import json
import os
import re
import tempfile
import urllib.parse

import okx_fake as F
import test_myspot as TM
from cnq.execution import ivan_watch as iw
from cnq.execution import myspot_sub as sub
from cnq.execution import myspot_watch as ms
from cnq.execution import okx_active as ac
from cnq.execution import okx_autopilot as ap
from cnq.execution import okx_futures as fu

NOW = F.NOW


def _setup():
    """An autopilot account after its first live run: spot by the portfolio, one futures position with its stop, one active coin."""
    tmp = tempfile.mkdtemp()
    log = os.path.join(tmp, "okx_autopilot.jsonl")
    ap.LOG, fu.LOG, ac.LOG = log, os.path.join(tmp, "f.jsonl"), os.path.join(tmp, "a.jsonl")
    e = F.env(tmp, CNQ_LIVE_TRADING="yes", OKX_AUTOPILOT="on")
    fk = F.FakeOkx(demo_key=False, balances={"USDT": 9000.0, "DOGE": 5000.0})
    assert ap.run_once(e, fk, F.portfolio(), NOW, F.nosleep, log_path=log)["ok"]
    assert fu.step(e, fk, desks=F.desks(F.trade()), now=NOW, sleep=F.nosleep)["opened"] == 1
    ac.save_state({"started": NOW, "trades": {"i1": {"ccy": "XRP", "q0": 50.0, "qty": 50.0, "opened": NOW}}}, e)
    fk.bal["XRP"] = 50.0
    return e, fk, log


def _fetch(fk, calls):
    """myspot_watch.make_fetch's shape (GET only) over the fake OKX; every request is recorded."""
    def fetch(host, path, params=None, signed=True):
        calls.append(("GET", path, dict(params or {})))
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        return fk("GET", host + path + q, {"OK-ACCESS-KEY": "x"} if signed else {}, None)
    return fetch


def test_sub_reader_source_has_no_post_and_no_order_path():
    src = inspect.getsource(sub)
    assert "POST" not in src and "make_fetch" in src
    for bad in ("/trade/order\"", "/trade/order'", "cancel-", "amend-", "order-algo\"", "close-position", "transfer", "withdraw",
                "set-leverage", ".order(", "ap.connect", "Okx("):
        assert bad not in src, bad
    paths = set(re.findall(r'"(/api/v5/[a-z0-9/_-]+)"', src))
    assert paths <= set(sub.READS) and "/api/v5/trade/orders-algo-pending" in paths
    assert "/trade/" not in inspect.getsource(ms)                               # the card's own reader stays as it was


def test_sub_reader_places_nothing_and_reads_positions_stops_targets_and_targets():
    e, fk, log = _setup()
    n_orders, n_calls = len(fk.orders), len(fk.calls)
    calls = []
    out = sub.read(e, _fetch(fk, calls), NOW + 60_000)
    assert out["ok"] and len(fk.orders) == n_orders and not fk.cancels[len(fk.cancels):]
    assert all(c[0] == "GET" for c in fk.calls[n_calls:]) and {c[1] for c in calls} <= set(sub.READS)
    (p,) = out["futures"]["positions"]
    assert p["inst"] == "SOL-USDT-SWAP" and p["side"] == "LONG" and p["stop"] == 190.0 and p["tp"] == 230.0 and p["ours"]
    spot = {r["ccy"]: r for r in out["spot"]["rows"]}
    assert set(spot) >= {"BTC", "ETH", "SOL", "AAA", "BBB"} and "XRP" not in spot     # the active coin is not a spot-portfolio coin
    assert abs(spot["BTC"]["target_pct"] - (19.5 + 10 * 30 / 45)) < 0.01 and spot["BTC"]["state"] == "ok"   # + the extra core (28.10)
    assert out["active"]["rows"] == [{"ccy": "XRP", "qty": 50.0, "usd": 100.0, "copy": False, "copy_reason": "ordinary", "copy_pct": 0.0}]
    assert out["start"] == {"usd": 10000.0, "t": out["start"]["t"], "src": out["start"]["src"]} and out["start"]["src"] in ("log", "state")
    assert out["result"]["usd"] == round(out["total_usd"] - 10000.0, 2)
    m = out["match"]
    assert m["line"] == "СОВПАДАЕТ С СИСТЕМОЙ CRYPTO NOVA: НЕТ" and m["same"] is False and any("ZZZ" in d for d in m["diffs"])
    assert out["cash"]["ccy"] == "USDT" and out["cash"]["free"] is not None and out["lease"]["node"] == "mac"
    assert ap.load_state(e)["first_live"]["usd"] == 10000.0                    # the autopilot remembers its first live run


def test_start_from_state_log_env_and_default():
    tmp = tempfile.mkdtemp()
    log = os.path.join(tmp, "l.jsonl")
    with open(log, "w") as f:
        f.write(json.dumps({"t": 5, "ev": "plan", "mode": "демо-торговля OKX", "total_usd": 1.0}) + "\n")
        f.write("not json\n")
        f.write(json.dumps({"t": 10, "ev": "plan", "mode": "РЕАЛЬНЫЕ ДЕНЬГИ", "total_usd": 9990.5}) + "\n")
        f.write(json.dumps({"t": 20, "ev": "plan", "mode": "РЕАЛЬНЫЕ ДЕНЬГИ", "total_usd": 5.0}) + "\n")
    assert sub.start({}, {}, log) == {"usd": 9990.5, "t": 10, "src": "log"}
    assert sub.start({}, {"first_live": {"t": 8, "usd": 10001.0}}, log) == {"usd": 10001.0, "t": 8, "src": "state"}   # the earlier one
    assert sub.start({}, {"first_live": {"t": 30, "usd": 10001.0}}, log)["src"] == "log"
    none = os.path.join(tmp, "none.jsonl")
    assert sub.start({"OKX_START_USD": "10000", "OKX_START_AT": "2026-10-22"}, {}, none) == {"usd": 10000.0, "t": 1792627200000, "src": "env"}
    assert sub.start({}, {}, none, {"first_live": 77}) == {"usd": 10000.0, "t": 77, "src": "default"}


def test_card_blob_carries_the_sub_account_only_encrypted_and_nothing_changes_without_its_key():
    e, fk, log = _setup()
    env = {**TM.ENV, **e}
    sent = []
    out = ms.run({}, NOW, fetch=TM.FakeOkx(), put=lambda b: sent.append(b) or True, env=env, model=(TM.MODEL, TM.QUALITY, TM.PROFILES),
                 sub_fetch=_fetch(fk, []))
    assert out["sub"]["ok"] and out["plan"]["total_usd"] == 10000.0 and out["sub"]["total_usd"] > 9000
    (blob,) = sent
    assert set(blob) == iw.BLOB_KEYS
    text = json.dumps(blob) + base64.b64decode(blob["ct"]).decode("latin-1")
    for s in ("SOL-USDT-SWAP", "СОВПАДАЕТ", '"sub"', "APKEY-test", "APSECRET-test", "APPHRASE-test", "MSKEY-test"):
        assert s not in text, s
    assert iw.decrypt(blob, TM.PASS)["sub"]["futures"]["positions"][0]["stop"] == 190.0
    # without OKX_API_* the card is exactly the old one: no «sub», nothing read with the autopilot's key
    calls = []
    old = ms.run({}, NOW, fetch=TM.FakeOkx(), put=lambda b: True, env=dict(TM.ENV), model=(TM.MODEL, TM.QUALITY, TM.PROFILES),
                 sub_fetch=_fetch(fk, calls))
    assert "sub" not in old and "autopilot" not in old and calls == []
    # a failing OKX read is shown as one line, the personal part still comes
    def broken(host, path, params=None, signed=True):
        return {"code": "50110", "msg": "IP not whitelisted", "data": []}
    out = ms.run({}, NOW, fetch=TM.FakeOkx(), put=lambda b: True, env=env, model=(TM.MODEL, TM.QUALITY, TM.PROFILES), sub_fetch=broken)
    assert out["plan"]["total_usd"] == 10000.0 and not out["sub"]["ok"] and "другому IP" in out["sub"]["error"]


def test_panel_card_renders_the_total_and_both_accounts():
    """web/app.html: «Всего: $X = личный $A + CRYPTO NOVA $B», the sub-account section and the old card when there is no D.sub."""
    import shutil
    import subprocess
    node = shutil.which("node")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    html = open(os.path.join(root, "web", "app.html"), encoding="utf-8").read()
    for s in ("totalHtml(T,SUB)", "Всего: <b class=\"big\">", "= личный <b>", "+ CRYPTO NOVA <b>", "CRYPTO NOVA · субаккаунт OKX",
              "Результат с начала", "Личный счёт · OKX", "SUB?totalHtml"):
        assert s in html, s
    if not node:
        return
    a = html.index("const fnum=v=>")
    b = html.index("function okHtml(D)")
    helpers = html[html.index("const MS={d:null"):html.index("const COL=")]
    js = ("const plur=(n,a,b,c)=>{ n=Math.abs(Math.round(n)); return n%10===1&&n%100!==11?a:(n%10>=2&&n%10<=4&&(n%100<12||n%100>14))?b:c; };"
          + helpers + html[a:b] + "\nconst S=JSON.parse(process.argv[2]);process.stdout.write(totalHtml(7700,S)+'\\n@@\\n'+subHtml(S,'<div>AP</div>'));")
    e, fk, log = _setup()
    s = sub.read(e, _fetch(fk, []), NOW)
    jsf = os.path.join(tempfile.mkdtemp(), "card.js")
    with open(jsf, "w", encoding="utf-8") as f:
        f.write(js)
    r = subprocess.run([node, jsf, json.dumps(s)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    tot, body = r.stdout.split("\n@@\n")
    assert re.search(r"Всего: <b class=\"big\">\$[\d\s]+</b> = личный <b>\$7\s700</b> \+ CRYPTO NOVA <b>\$[\d\s]+</b>", tot), tot
    for x in ("SOL-USDT-SWAP", "СОВПАДАЕТ С СИСТЕМОЙ CRYPTO NOVA: НЕТ", "Результат с начала", "Импульс BTC", "XRP", "190", "230"):
        assert x in body, x
    r = subprocess.run([node, jsf, json.dumps({"ok": False, "error": "ключ привязан к другому IP"})], capture_output=True, text=True, timeout=60)
    assert "Всего: —" in r.stdout and "не прочитался: ключ привязан к другому IP" in r.stdout


def test_dollars_are_allocated_to_their_parts_and_add_up_to_the_usdt():
    """22.10 (owner read «43% USDT» as idle cash): spot cash, futures margin + reserve, active spot waiting, «прочее» = the USDT."""
    e, fk, log = _setup()
    s = sub.read(e, _fetch(fk, []), NOW + 60_000)
    a, T, usdt = s["alloc"], s["total_usd"], s["cash"]["usd"]
    assert list(a) == list(sub.ALLOC_KEYS) and (a["spot_pct"], a["core_pct"], a["fut_pct"], a["active_pct"]) == (65.0, 10.0, 15.0, 10.0)
    parts = a["spot_cash_usd"] + a["fut_margin_usd"] + a["fut_reserve_usd"] + a["active_wait_usd"] + a["other_usd"]
    assert abs(parts - usdt) <= 1.0 and min(a[k] for k in ("spot_cash_usd", "fut_margin_usd", "fut_reserve_usd", "active_wait_usd")) >= 0
    st = ap.load_state(e)
    assert abs(a["spot_cash_usd"] - st["pf_cash"] * T * 0.65) < 0.02                 # the portfolio's own cash share × the spot budget
    assert a["fut_budget_usd"] == fu.status(e)["budget_usd"]                         # the futures budget of okx_futures itself
    assert abs(a["fut_margin_usd"] + a["fut_reserve_usd"] - a["fut_budget_usd"]) < 0.02 and a["fut_margin_usd"] > 0
    assert abs(a["active_coins_usd"] - 100.0) < 0.01 and abs(a["active_wait_usd"] - (0.10 * T - 100.0)) < 0.02
    # the order the USDT is handed out in: a short account never shows more than it has; owner's decision of 28.10: the futures
    # part is its own money, so its reserve comes before the spot cash
    x = sub.allocate(1000.0, 50.0, 900.0, 0.0, [{"margin": 30.0, "upl": -2.0}], {"spot": 0.65, "futures": 0.25, "active": 0.1}, 0.1, 250.0)
    assert x["fut_margin_usd"] == 28.0 and x["fut_reserve_usd"] == 22.0 and x["spot_cash_usd"] == 0.0 and x["active_wait_usd"] == 0.0
    assert x["other_usd"] == 0.0
    y = sub.allocate(1000.0, 400.0, 600.0, 0.0, [], {"spot": 0.65, "futures": 0.25, "active": 0.1}, None, None, 55.0)
    assert y["spot_cash_usd"] == round((1 - 55 / 65) * 650, 2) and y["fut_budget_usd"] == 250.0 and abs(sum(
        y[k] for k in ("spot_cash_usd", "fut_margin_usd", "fut_reserve_usd", "active_wait_usd", "other_usd")) - 400.0) < 0.01
    html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "app.html"), encoding="utf-8").read()
    card = html[html.index("function subHtml(S,apBox)"):html.index("function okHtml(D)")]
    for w in ("распределены: спот ${sh(AL.spot_pct)}${AL.core_pct?`, доп. ядро BTC/ETH/SOL ${sh(AL.core_pct)}`:''}, фьючерсы ${sh(AL.fut_pct)} "
              "(в позициях ${sh(fp)}), активный спот ${sh(AL.active_pct)}",
              "Спот · запас под новые монеты", "в позициях (маржа)", "резерв под новые сделки", "ждёт сигнала «Импульс BTC»", "Прочее"):
        assert w in card, w
    assert "свободн" not in card                                                     # never «свободный кэш»
