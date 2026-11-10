"""11.11: «Питер» — MT5 gold trader, observation only: the bridge's token, deals → trades, the notional $10 000 curve scaled by
his % returns, context at entry / exit, owner-only serving, «Питер (MT5)» in «Состояние системы»."""
import json
import os
import tempfile

from cnq.api import peter as P

TOK = "t" * 32
H0 = 1794300000000          # 10.11.2026 08:40 UTC (a Tuesday)
M = 60_000


def _bars(n, px, step=0.5):
    return [[H0 - (n - i) * 3600_000, px, px + 1, px - 1, px + (i - n / 2) * step / n] for i in range(n)]


def _ctx(px):
    return {"M5": _bars(48, px), "H1": _bars(210, px), "D1": _bars(210, px, 50), "day": {"open": px - 5, "high": px + 10, "low": px - 20},
            "calendar": [{"t": H0 + 3600_000, "cur": "USD", "name": "CPI", "imp": 3}], "ext": {"DXY": {"last": 99.1, "chg_d1_pct": -0.2}}}


def _post(p, events, tok=TOK, now=H0):
    return p.ingest({"x-cnq-token": tok}, json.dumps({"v": 1, "acct": "ab" * 32, "events": events}).encode(), "1.2.3.4", now)


def test_token_trades_curve_and_context():
    d = tempfile.mkdtemp()
    p = P.Peter(d, env={})
    assert _post(p, [])[0] == 503                                         # no token on the server yet
    p.env = {P.TOKEN_ENV: TOK}
    assert _post(p, [], tok="wrong" * 6)[0] == 401
    assert p.ingest({}, b"{}", "x", H0)[0] == 401
    deal = lambda n, pos, entry, dtype, px, profit, bal, t, ctx=True: {"type": "deal", "deal": n, "pos": pos, "symbol": "XAUUSD", "dtype": dtype,
                                                                       "entry": entry, "volume": 0.1, "price": px, "sl": px - 10, "tp": px + 20,
                                                                       "profit": profit, "swap": 0, "commission": -0.5, "fee": 0, "t": t,
                                                                       "bal_after": bal, **({"ctx": _ctx(px)} if ctx else {})}
    # trade 1: long, +$99 net (both commissions) on a $1 000 balance → +9.9 %; trade 2: short, −$51 on $1 099 → −4.6 %
    evs = [deal(1, 11, "in", "buy", 2600, 0, 999.5, H0 - 300 * M), deal(2, 11, "out", "sell", 2610, 100, 1099.0, H0 - 240 * M),
           deal(3, 12, "in", "sell", 2610, 0, 1098.5, H0 - 100 * M), deal(4, 12, "out", "buy", 2615, -50, 1048.0, H0 - 10 * M),
           deal(5, 13, "in", "buy", 2620, 0, 1047.5, H0 - 5 * M),
           {"type": "modify", "pos": 13, "sl": 2611, "tp": 2650, "t": H0 - 2 * M},
           {"type": "heartbeat", "t": H0, "balance": 1047.5, "equity": 1052.0, "currency": "USD", "leverage": 500,
            "positions": [{"pos": 13, "symbol": "XAUUSD", "side": "buy", "volume": 0.1, "open_price": 2620, "price": 2624.5, "sl": 2611,
                           "tp": 2650, "profit": 4.5}], "candles": {"XAUUSD": {"M5": _bars(24, 2624)}}}]
    code, r = _post(p, evs)
    assert code == 200 and r["accepted"] == 7
    assert _post(p, evs[:2])[1]["accepted"] == 0                          # a resent deal is not counted twice
    code, D = p.desk(H0)
    L = D["data"]
    s = L["stats"]
    assert code == 200 and s["trades"] == 2 and s["wins"] == 1 and s["winrate"] == 0.5
    t1, t2 = L["closed"][1], L["closed"][0]
    assert t1["side"] == "LONG" and t1["pnl"] == 99.0 and abs(t1["pct"] - 9.9) < 0.01 and t1["hold_min"] == 60
    assert t2["side"] == "SHORT" and t2["pnl"] == -51.0 and abs(t2["pct"] - (-51 / 1099 * 100)) < 0.01
    eq = 10000 * (1 + t1["pct"] / 100) * (1 + t2["pct"] / 100)
    assert abs(L["equity_notional"] - eq) < 0.02 and abs(s["max_dd_pct"] - (-t2["pct"])) < 0.01
    assert L["open"][0]["pos"] == 13 and L["open"][0]["float_usd"] == 4.5 and L["open"][0]["sl"] == 2611
    e = t1["entry_ctx"]
    assert e["weekday"] == "вт" and e["sessions"] == ["Азия"] and e["hour_utc"] == 3 and e["dist_day_high_pct"] < 0 and e["atr14_H1"] > 0
    assert "vs_sma200_D1_pct" in e and e["ext"]["DXY"]["last"] == 99.1 and e["calendar_n"] == 1
    assert L["closed"][0]["entry_ctx"]["since_prev_trade_min"] == 200
    assert "balance" not in json.dumps(L["account"])                      # the desk never shows his balance in $
    assert os.path.exists(os.path.join(d, "peter_live.json"))
    st = p.status()
    assert st["configured"] and st["last_hb"] == H0 and set(st) == {"configured", "last_hb", "last_deal"}
    tv = p.token_view()[1]
    assert tv["token"] == TOK and tv["whitelist"] == "https://app.crypto-capital.app"


def test_server_routes_and_health_row():
    from cnq.agents import owner_health as OH
    from cnq.api.peter import gold_market_open
    assert gold_market_open(H0) and not gold_market_open(H0 + 4 * 86400_000)      # Tuesday open; Saturday closed
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cnq", "api", "server.py"), encoding="utf-8").read()
    assert 'path == "/v2/peter/ingest"' in src and 'path == "/v2/owner/peter"' in src and '"peter": self.peter.status()' in src
    d = tempfile.mkdtemp()
    w = lambda n, o: json.dump(o, open(os.path.join(d, n), "w"))
    for hb, want in ((H0 - 5 * M, "ok"), (H0 - 45 * M, "delay"), (None, "delay")):
        w("system_status.json", {"components": [{"key": "api", "status": "ok", "last_ok": H0,
                                                 "peter": {"configured": True, "last_hb": hb, "last_deal": None}}]})
        row = {r["key"]: r for r in OH.build(d, None, H0)["rows"]}["peter"]
        assert row["name"] == "Питер (MT5)" and row["status"] == want and (want == "ok" or row["note"]), (hb, row)


def test_ea_is_read_only_and_never_holds_secrets():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ea = open(os.path.join(root, "tools", "mt5", "CryptoNovaBridge.mq5"), encoding="utf-8").read()
    for bad in ("OrderSend", "PositionClose", "CTrade", "OrderModify", "PositionModify"):
        assert bad not in ea, bad
    assert 'input string InpToken        = "";' in ea and "OnTradeTransaction" in ea and "WebRequest" in ea and "CRYPT_HASH_SHA256" in ea
    doc = open(os.path.join(root, "docs", "peter-mt5-setup.md"), encoding="utf-8").read()
    assert "https://app.crypto-capital.app" in doc and "пароль" in doc.lower()


def test_panels_owner_only():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = lambda *p: open(os.path.join(root, *p), encoding="utf-8").read()
    core, app, vip = r("web", "vip", "core.js"), r("web", "app.html"), r("web", "vip.html")
    assert "{ id: 'peter', name: 'Питер', short: 'Питер', icon: 'strategies', owner: true }" in core and "peter: '/v2/owner/peter'" in core
    assert '<script src="vip/peter.js"></script>' in vip and '<script src="peter.js?v=1"></script>' in vip
    assert 'id="peterCard" hidden' in app and "CNAuth.state.isOwner" in app and "/v2/owner/peter" in app
    for f in os.listdir(os.path.join(root, "web")):         # the desk is never a public file
        assert not f.startswith("peter_live"), f
