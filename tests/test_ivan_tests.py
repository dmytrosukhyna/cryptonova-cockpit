"""13.11 (owner 22:10): «Иван · тест-стратегии» — Ivan's Bybit demo accounts read with READ-ONLY keys, a paper copy, owner-only.
A key with trade / withdraw rights is refused and never stored; secrets never come back; the key goes only to the chosen host."""
import json
import os
import stat
import tempfile

from cnq.api import ivan_tests as IT
from cnq.execution import ivan_watch as iw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = 1794600000000
KEY, SEC = "AbCdEfGh123456", "s" * 36


def fake_bybit(read_only=1, wallet=(), log=None):
    def make(key, secret):
        def fetch(base, path, params):
            if log is not None:
                log.append((base, path))
            if base != iw.HOSTS["demo"]:
                return {"retCode": 10003, "retMsg": "API key is invalid."}
            if path == "/v5/user/query-api":
                return {"retCode": 0, "result": {"readOnly": read_only, "permissions": {"ContractTrade": ["Order", "Position"], "Wallet": list(wallet)}}}
            if path == "/v5/account/wallet-balance":
                return {"retCode": 0, "result": {"list": [{"accountType": "UNIFIED", "totalEquity": "1050", "totalWalletBalance": "1040",
                                                           "totalPerpUPL": "10", "totalAvailableBalance": "900"}]}}
            if path == "/v5/position/list":
                if params.get("settleCoin") != "USDT":
                    return {"retCode": 0, "result": {"list": []}}
                return {"retCode": 0, "result": {"list": [{"symbol": "ETHUSDT", "side": "Buy", "size": "1", "avgPrice": "2000", "markPrice": "2010",
                                                           "unrealisedPnl": "10", "leverage": "10", "positionValue": "2010"}], "nextPageCursor": ""}}
            if path == "/v5/position/closed-pnl":
                if int(params.get("endTime", 0)) < NOW - 7 * 86400_000:
                    return {"retCode": 0, "result": {"list": []}}
                return {"retCode": 0, "result": {"list": [
                    {"symbol": "ETHUSDT", "side": "Sell", "closedSize": "1", "avgEntryPrice": "1900", "avgExitPrice": "1995", "closedPnl": "95",
                     "orderId": "o1", "createdTime": str(NOW - 3600_000)},
                    {"symbol": "BTCUSDT", "side": "Buy", "closedSize": "0.01", "avgEntryPrice": "100000", "avgExitPrice": "102000", "closedPnl": "-20",
                     "orderId": "o2", "createdTime": str(NOW - 7200_000)}], "nextPageCursor": ""}}
            return {"retCode": 0, "result": {"list": []}}
        return fetch
    return make


def test_read_only_key_is_stored_secretly_and_read_on_the_chosen_host_only():
    log = []
    d = tempfile.mkdtemp()
    t = IT.IvanTests(d, make_fetch=fake_bybit(log=log))
    assert t.add({"name": "", "key": KEY, "secret": SEC})[0] == 400
    code, r = t.add({"name": "Иван A", "key": KEY, "secret": SEC, "demo": True})
    assert code == 200 and r["ok"] and "только для чтения" in r["message_ru"]
    assert stat.S_IMODE(os.stat(os.path.join(d, "accounts.json")).st_mode) == 0o600
    v = t.view()
    blob = json.dumps(v, ensure_ascii=False)
    assert KEY not in blob and SEC not in blob and v["accounts"][0]["key"] == "AbCd…456" and v["accounts"][0]["host"] == "demo"
    assert {b for b, _ in log} == {iw.HOSTS["demo"]}                     # the key never went to the mainnet host
    a = v["accounts"][0]["data"]
    s = a["summary"]
    assert s["trades"] == 2 and s["winrate"] == 0.5 and s["open_n"] == 1 and a["account"]["positions"][0]["leverage"] == 10
    p = a["paper"]
    assert p["trades"] == 2 and p["wins"] == 1 and p["start"] == 10000.0
    assert t.add({"name": "dup", "key": KEY, "secret": SEC, "demo": True})[0] == 409
    code, r = t.remove(v["accounts"][0]["id"])
    assert r["ok"] and t.view()["accounts"] == [] and not [f for f in os.listdir(d) if f.startswith(("state_", "out_"))]


def test_trading_or_withdraw_keys_are_refused_and_not_kept():
    d = tempfile.mkdtemp()
    for ro, wallet in ((0, ()), (1, ("Withdraw",)), (1, ("AccountTransfer",))):
        t = IT.IvanTests(d, make_fetch=fake_bybit(read_only=ro, wallet=wallet))
        code, r = t.add({"name": "X", "key": KEY, "secret": SEC, "demo": True})
        assert code == 200 and not r["ok"] and "не сохранён" in r["message_ru"] and t.view()["accounts"] == []
    t = IT.IvanTests(d, make_fetch=fake_bybit())
    t.add({"name": "Y", "key": KEY, "secret": SEC, "demo": True})
    aid = t.view()["accounts"][0]["id"]
    t.make_fetch = fake_bybit(read_only=0)                                 # the key later gained trade rights → disconnected
    assert not t.check(aid)[1]["ok"] and t.view()["accounts"] == []
    t2 = IT.IvanTests(tempfile.mkdtemp(), make_fetch=fake_bybit())
    assert not t2.add({"name": "Z", "key": KEY, "secret": SEC, "demo": False})[1]["ok"]   # a demo key on mainnet: rejected, not stored


def test_paper_copy_math():
    closed = [{"symbol": "ETH", "side": "LONG", "entry": 100.0, "exit": 110.0, "close_time": 1},
              {"symbol": "BTC", "side": "SHORT", "entry": 100.0, "exit": 150.0, "close_time": 2}]
    p = IT.paper(closed, {"ETH|LONG": 3, "BTC|SHORT": 50})
    # ETH: margin 1000 × 3× × (10 % − 0.2 %) = 294; BTC: 50× capped at 5×, −50 % move → loss capped at the margin (10 % of 10 294)
    assert p["last"][1]["pnl"] == 294.0 and p["last"][1]["lev"] == 3 and p["last"][0]["lev"] == 5.0 and p["last"][0]["pnl"] == -1029.4
    assert p["equity"] == 9264.6 and p["trades"] == 2 and p["wins"] == 1 and p["max_dd_pct"] == 10.0


def test_routes_and_panels_are_owner_only():
    r = lambda *p: open(os.path.join(ROOT, *p), encoding="utf-8").read()
    srv = r("cnq", "api", "server.py")
    i = srv.index("def _owner(")
    for route in ('"/v2/owner/ivan_tests"', '"/v2/owner/ivan_tests/add"', '"/v2/owner/ivan_tests/check"', '"/v2/owner/ivan_tests/remove"'):
        assert route in srv[i:] and srv.index("self.owner_auth.require(headers)", i) < srv.index(route, i), route
        assert route not in srv[:i], route
    core, app, vip = r("web", "vip", "core.js"), r("web", "app.html"), r("web", "vip.html")
    assert "{ id: 'ivan_tests', name: 'Иван · тест-стратегии', short: 'Иван ×3', icon: 'strategies', owner: true }" in core
    assert '<script src="vip/ivan_tests.js"></script>' in vip and 'id="ivanTestsCard" hidden' in app
    j = app.index("async function renderIvanTests")
    assert app.index("CNAuth.state.isOwner", j) < app.index("fetchOwner('/v2/owner/ivan_tests')", j)
    js = r("web", "ivan_tests.js")
    assert "Только ключ «Read-Only»" in js and 'type="password"' in js and "ivan_tests.json" not in js + app
    doc = r("docs", "ivan-bybit-readonly-key.md")
    assert "Read-Only" in doc and "Demo Trading" in doc and "never post" in doc.lower() and "общих чатах" in doc
