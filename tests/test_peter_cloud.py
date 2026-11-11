"""11.11 option B: «Питер» read through MetaApi with the INVESTOR password — the same store / desk / contexts as the EA path;
secrets only in a 600 file, never returned; a master password is refused; no trading endpoint is ever called."""
import json
import os
import stat
import tempfile

from cnq.api import peter as P, peter_cloud as C

NOW = 1794300000000          # 10.11.2026 08:40 UTC (Tuesday)
H = 3600_000
FORM = {"token": "m" * 40, "login": "50194988", "server": "ICMarketsSC-MT5", "password": "inv-pass-1"}


class Fake:
    def __init__(self, trade_allowed=False, conn="CONNECTED"):
        self.calls, self.trade_allowed, self.conn, self.deleted = [], trade_allowed, conn, False

    def __call__(self, method, url, headers, body=None, timeout=30):
        self.calls.append((method, url, body))
        assert headers.get("auth-token") == FORM["token"]
        iso = C._iso
        if method == "POST" and url == C.PROV:
            assert body["password"] == FORM["password"] and body["platform"] == "mt5" and len(headers["transaction-id"]) == 32
            return 201, {"id": "acc-1", "state": "DEPLOYED"}, {}
        if method == "DELETE":
            self.deleted = True
            return 204, None, {}
        if url == C.PROV + "/acc-1":
            return 200, {"_id": "acc-1", "state": "DEPLOYED", "connectionStatus": self.conn, "region": "london"}, {}
        assert ".london.agiliumtrade.ai/" in url
        if url.endswith("/account-information"):
            return 200, {"balance": 1048.0, "equity": 1052.5, "currency": "USD", "leverage": 500, "tradeAllowed": self.trade_allowed}, {}
        if url.endswith("/positions"):
            return 200, [{"id": "13", "type": "POSITION_TYPE_BUY", "symbol": "XAUUSD", "time": iso(NOW - 5 * 60_000), "openPrice": 2620,
                          "currentPrice": 2624.5, "stopLoss": 2611, "takeProfit": 2650, "volume": 0.1, "profit": 4.5, "swap": 0}], {}
        if "/history-deals/time/" in url:
            if iso(NOW - 30 * 86400_000)[:10] not in url and iso(NOW)[:10] not in url.split("/time/")[1]:
                return 200, [], {}
            d = lambda i, pos, typ, ent, px, pr, t: {"id": str(i), "positionId": str(pos), "orderId": str(i), "symbol": "XAUUSD", "type": typ,
                                                     "entryType": ent, "volume": 0.1, "price": px, "profit": pr, "swap": 0, "commission": -0.5,
                                                     "time": iso(t)}
            return 200, [{"id": "1", "type": "DEAL_TYPE_BALANCE", "profit": 1000.0, "time": iso(NOW - 40 * H)},
                         d(2, 11, "DEAL_TYPE_BUY", "DEAL_ENTRY_IN", 2600, 0, NOW - 30 * H), d(3, 11, "DEAL_TYPE_SELL", "DEAL_ENTRY_OUT", 2610, 100, NOW - 29 * H),
                         d(4, 12, "DEAL_TYPE_SELL", "DEAL_ENTRY_IN", 2610, 0, NOW - 10 * H), d(5, 12, "DEAL_TYPE_BUY", "DEAL_ENTRY_OUT", 2615, -50, NOW - 9 * H),
                         d(6, 13, "DEAL_TYPE_BUY", "DEAL_ENTRY_IN", 2620, 0, NOW - 5 * 60_000)], {}
        if "/historical-market-data/" in url:
            tf = url.split("/timeframes/")[1].split("/")[0]
            ln = {"5m": 300_000, "1h": H, "1d": 24 * H}[tf]
            n = 400 if tf != "1h" else 300
            if "startTime" in url:
                return 200, [], {}
            end = NOW - NOW % ln
            return 200, [{"time": iso(end - (n - i) * ln), "open": 2600 + i * 0.01, "high": 2605 + i * 0.01, "low": 2595 + i * 0.01,
                          "close": 2601 + i * 0.01} for i in range(n)], {}
        raise AssertionError(url)


def _mk(fake):
    d = tempfile.mkdtemp()
    p = P.Peter(d, env={})
    return d, p, C.Cloud(p, http=fake)


def test_connect_poll_desk_and_secrets():
    fake = Fake()
    d, p, c = _mk(fake)
    assert c.connect({"login": "1", "server": "S-1", "password": "pppp"})[1]["error"] == "need_token"      # asked once
    assert c.connect({"token": "m" * 40, "login": "abc", "server": "", "password": ""})[1]["error"] == "bad_form"
    code, r = c.connect(FORM)
    assert code == 200 and r["ok"] and r["provisioned"] and r["login"] == "•••••988" and r["server"] == "ICMarketsSC-MT5"
    cfg = json.load(open(os.path.join(d, "cloud.json")))
    assert "password" not in cfg and cfg["account_id"] == "acc-1"            # the investor password is not kept after provisioning
    assert stat.S_IMODE(os.stat(os.path.join(d, "cloud.json")).st_mode) == 0o600
    blob = json.dumps([c.view(), r], ensure_ascii=False)
    assert FORM["token"] not in blob and FORM["password"] not in blob and FORM["login"] not in blob
    st = c.poll(NOW)
    assert st["ok"] and st["new"] == 5, st
    L = p.desk(NOW)[1]["data"]
    s = L["stats"]
    assert L["source"].startswith("MetaTrader 5 · облако") and s["trades"] == 2 and s["wins"] == 1
    t1, t2 = L["closed"][1], L["closed"][0]
    # balances walked back from today's 1048: after deal 5 → 1048.5 (deal 6's commission), so trade 2 = −51 on 1099.5; trade 1 = +99 on 1000
    assert t1["pnl"] == 99.0 and abs(t1["pct"] - 9.9) < 0.01 and t2["pnl"] == -51.0 and abs(t2["pct"] - (-51 / 1099.5 * 100)) < 0.01
    assert L["open"][0]["pos"] == 13 and L["open"][0]["float_usd"] == 4.5 and L["open"][0]["tp"] == 2650
    e = t2["entry_ctx"]
    assert e["sessions"] and "vs_sma200_D1_pct" in e and e["atr14_H1"] > 0 and "dist_day_high_pct" in e and e["since_prev_trade_min"] == 1200
    assert c.poll(NOW + 60_000)["new"] == 0                                 # deals are deduplicated by id
    stt = p.status()
    assert stt["source"] == "cloud" and stt["cloud_set"] and stt["cloud_last_ok"] == NOW + 60_000 and stt["last_hb"] == NOW + 60_000
    for m, u, b in fake.calls:                                              # observation only: never a trading call
        assert m in ("GET", "POST") and "/trade" not in u and (m == "GET" or u == C.PROV), (m, u)
    code, r = c.disconnect()
    assert r["ok"] and fake.deleted and not os.path.exists(os.path.join(d, "cloud.json")) and not c.view()["set"] and not p.status()["cloud_set"]


def test_master_password_is_refused_and_failures_stay_quiet():
    fake = Fake(trade_allowed=True)
    d, p, c = _mk(fake)
    c.connect(FORM)
    st = c.poll(NOW)
    assert not st["ok"] and "ОСНОВНОЙ" in st["msg"] and fake.deleted
    left = json.load(open(os.path.join(d, "cloud.json")))
    assert left == {"refused": "master_password", "token": FORM["token"]} and not c.view()["provisioned"] and p.status()["last_hb"] is None
    assert c.connect({k: FORM[k] for k in ("login", "server", "password")})[1]["provisioned"]          # the retry is 3 fields
    fake2 = Fake(conn="DISCONNECTED_FROM_BROKER")
    d2, p2, c2 = _mk(fake2)
    c2.connect(FORM)
    st = c2.poll(NOW)
    assert not st["ok"] and "пароль инвестора" in st["msg"] and p2.status()["last_hb"] is None and p2.status()["cloud_set"]

    def boom(*a, **k):
        raise OSError("https://secret-url?auth-token=" + FORM["token"])
    c2.http = boom
    st = c2.poll(NOW)
    assert not st["ok"] and FORM["token"] not in json.dumps(st) and "OSError" in st["msg"]


def test_health_row_cloud_and_docs():
    from cnq.agents import owner_health as OH
    d = tempfile.mkdtemp()
    w = lambda o: json.dump({"components": [{"key": "api", "status": "ok", "last_ok": NOW, "peter": o}]}, open(os.path.join(d, "system_status.json"), "w"))
    base = {"configured": True, "token_set": True, "last_deal": None, "source": "cloud", "cloud_set": True}
    for hb, want in ((None, "info"), (NOW - 5 * 60_000, "ok"), (NOW - 45 * 60_000, "delay")):
        w({**base, "last_hb": hb, "cloud_last_ok": hb})
        row = {r["key"]: r for r in OH.build(d, None, NOW)["rows"]}["peter"]
        assert row["status"] == want and row["source"] == "облако MetaApi", (hb, row)
        if want == "delay":
            assert "облако MetaApi не отдаёт данные 45 мин" in row["note"]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    en = open(os.path.join(root, "docs", "peter-investor-setup.md"), encoding="utf-8").read()
    assert "investor" in en.lower() and "never" in en.lower() and "master" in en.lower()
    js = open(os.path.join(root, "web", "peter.js"), encoding="utf-8").read()
    assert "Подключение: логин, сервер, пароль инвестора" in js and "Golden Donut" in js and 'type="password"' in js and "Только пароль ИНВЕСТОРА" in js
    src = open(os.path.join(root, "cnq", "api", "server.py"), encoding="utf-8").read()
    assert '"/v2/owner/peter/cloud/disconnect"' in src and "api.peter_cloud.start()" in src
