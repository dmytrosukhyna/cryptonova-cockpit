"""«Иван тест» monitor: stats, drawdown, host fallback, closed-pnl history, encryption (Python and the JS reference), publish
never carries keys or the passphrase, nothing happens without the IVAN_* env."""
import base64
import json
import os
import shutil
import subprocess
import tempfile

from cnq.execution import ivan_watch as iw

KEY, SECRET, PASS = "IVKEY-abc123", "IVSECRET-xyz789", "лошадь батарейка скрепка 42"
ENV = {"IVAN_BYBIT_KEY": KEY, "IVAN_BYBIT_SECRET": SECRET, "IVAN_PASS": PASS}
NOW = 1_800_000_000_000
H, DAY = 3600_000, 86_400_000
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ok(result):
    return {"retCode": 0, "retMsg": "OK", "result": result}


def row(sym, close_side, entry, exit_, qty, pnl, t, oid, fees=None):
    r = {"symbol": sym + "USDT", "side": close_side, "orderId": oid, "closedSize": str(qty), "qty": str(qty),
         "avgEntryPrice": str(entry), "avgExitPrice": str(exit_), "cumEntryValue": str(entry * qty), "cumExitValue": str(exit_ * qty),
         "closedPnl": str(pnl), "createdTime": str(t), "updatedTime": str(t)}
    if fees:
        r["openFee"], r["closeFee"] = str(fees[0]), str(fees[1])
    return r


def pos(sym, side, size, entry, mark, upnl, created=NOW - 10 * DAY):
    return {"symbol": sym + "USDT", "side": side, "size": str(size), "avgPrice": str(entry), "markPrice": str(mark),
            "unrealisedPnl": str(upnl), "leverage": "10", "liqPrice": "1", "positionValue": str(size * mark), "createdTime": str(created)}


class FakeBybit:
    """Answers like Bybit v5 on one host; the other host rejects the key (retCode 10003)."""
    def __init__(self, host="demo", closed=(), positions=(), spot=(), equity=900.0):
        self.base = iw.HOSTS.get(host, host)
        self.closed, self.positions, self.spot, self.equity, self.calls = list(closed), list(positions), list(spot), equity, []

    def __call__(self, base, path, params):
        self.calls.append((base, path, dict(params)))
        if base != self.base:
            return {"retCode": 10003, "retMsg": "API key is invalid."}
        if path == "/v5/account/wallet-balance":
            assert params["accountType"] == "UNIFIED"
            upnl = sum(float(p["unrealisedPnl"]) for p in self.positions)
            return ok({"list": [{"accountType": "UNIFIED", "totalEquity": str(self.equity), "totalWalletBalance": str(self.equity - upnl),
                                 "totalPerpUPL": str(upnl), "totalAvailableBalance": "500", "coin": []}]})
        if path == "/v5/position/list":
            assert params["category"] == "linear" and params["settleCoin"] == "USDT"
            return ok({"list": self.positions, "nextPageCursor": ""})
        if path == "/v5/position/closed-pnl":
            assert 0 <= params["endTime"] - params["startTime"] < iw.WINDOW
            rows = sorted((r for r in self.closed if params["startTime"] <= int(r["createdTime"]) <= params["endTime"]),
                          key=lambda r: -int(r["createdTime"]))
            off = int(params.get("cursor") or 0)
            page = rows[off:off + params["limit"]]
            return ok({"list": page, "nextPageCursor": str(off + len(page)) if off + len(page) < len(rows) else ""})
        if path == "/v5/execution/list":
            return ok({"list": self.spot, "nextPageCursor": ""})
        if path == "/v5/user/query-api":
            return ok({"readOnly": 1, "expiredAt": ""})
        return {"retCode": 10001, "retMsg": "unknown path"}


def tmp_env():
    return {**ENV, "IVAN_STATE": os.path.join(tempfile.mkdtemp(), "ivan.json")}


def test_ivan_stats():
    trades = [{"symbol": "BTC", "pnl": 10.0, "close_time": 5, "open_time": None}, {"symbol": "BTC", "pnl": -4.0, "close_time": 3, "open_time": 2},
              {"symbol": "ETH", "pnl": 6.0, "close_time": 7}, {"symbol": "ETH", "pnl": 0.0, "close_time": 8},
              {"symbol": "SOL", "pnl": -2.0, "close_time": 9}]
    s = iw.stats(trades)
    assert (s["n"], s["wins"], s["losses"], s["winrate"]) == (5, 2, 2, 0.4)          # breakeven is neither win nor loss
    assert (s["pnl_sum"], s["avg_win"], s["avg_loss"], s["max_loss"], s["best"]) == (10.0, 8.0, -3.0, -4.0, 10.0)
    assert s["by_symbol"] == {"SOL": {"n": 1, "pnl": -2.0}, "BTC": {"n": 2, "pnl": 6.0}, "ETH": {"n": 2, "pnl": 6.0}}
    assert s["first_trade"] == 2
    e = iw.stats([])
    assert e["n"] == 0 and e["winrate"] is None and e["avg_win"] is None and e["first_trade"] is None


def test_ivan_max_drawdown_and_thinning():
    assert iw.max_drawdown([[0, 100], [1, 120], [2, 90], [3, 110], [4, 130], [5, 104]]) == 0.25
    assert iw.max_drawdown([]) == 0 and iw.max_drawdown([[0, 1], [1, 2], [2, 3]]) == 0
    h = [[i, 100.0 + i] for i in range(iw.MAX_HIST + 1)]
    t = iw._thin(h)
    assert len(t) <= iw.MAX_HIST and t[0] == h[0] and t[-1] == h[-1]


def test_ivan_closed_side():
    assert iw.norm_closed(row("BTC", "Sell", 100, 110, 1, 10, NOW, "a"))["side"] == "LONG"    # closing Sell = long
    assert iw.norm_closed(row("BTC", "Buy", 100, 90, 1, 10, NOW, "b"))["side"] == "SHORT"
    t = iw.norm_closed(row("BTC", "Buy", 100, 110, 1, 9.8, NOW, "c", fees=(0.1, 0.1)))     # fees known: exact check wins
    assert t["side"] == "LONG" and t["fee"] == 0.2 and t["symbol"] == "BTC" and t["close_time"] == NOW


def test_ivan_host_fallback_main_to_demo():
    fb = FakeBybit("demo", positions=[pos("ETH", "Buy", 1, 2000, 1900, -100)])
    st = {}
    out = iw.run(st, NOW, fetch=fb, put=lambda b: True, env=ENV)
    assert out["host"] == "demo" and st["host"] == "demo" and fb.calls[0][0] == iw.HOSTS["main"]
    fb.calls.clear()
    out = iw.run(st, NOW + 300_000, fetch=fb, put=lambda b: True, env=ENV)
    assert out["host"] == "demo" and fb.calls and all(c[0] == iw.HOSTS["demo"] for c in fb.calls)   # remembered, not re-probed
    fm = FakeBybit("main")
    assert iw.run({}, NOW, fetch=fm, put=lambda b: True, env=ENV)["host"] == "main"
    assert all(c[0] == iw.HOSTS["main"] for c in fm.calls)
    bad = FakeBybit("https://nowhere")
    try:
        iw.run({}, NOW, fetch=bad, put=lambda b: True, env=ENV)
        raise AssertionError("expected KeyRejected")
    except iw.KeyRejected:
        pass
    env = tmp_env()
    assert "ключ не принят" in iw.check(env=env, fetch=bad)
    assert iw.check(env=env, fetch=FakeBybit("demo")) == "Иван тест: ключ работает (demo), баланс $900.00"
    assert iw.load_state(env)["host"] == "demo"


def test_ivan_closed_history_backfill_and_incremental():
    old_out, iw.MAX_CLOSED_OUT = iw.MAX_CLOSED_OUT, 200   # the panel gets the last 100; this test checks the full ordering
    rows = [row("BTC", "Sell", 100, 110, 1, 10, NOW - 50 * DAY, "o1"), row("ETH", "Buy", 100, 90, 2, 20, NOW - 3 * DAY, "o2"),
            row("ADA", "Sell", 1, 1.2, 10, 2, NOW - 60_000, "o4"), row("XRP", "Sell", 1, 1.1, 10, 1, NOW - 70 * DAY, "too-old")]
    rows += [row("SOL", "Sell", 10, 11, 1, 1, NOW - DAY + i * 1000, f"p{i}") for i in range(150)]     # 150 in one window → 2 pages
    fb = FakeBybit("main", closed=rows)
    st = {}
    out = iw.run(st, NOW, fetch=fb, put=lambda b: True, env=ENV)
    first = [c[2] for c in fb.calls if c[1] == "/v5/position/closed-pnl" and "cursor" not in c[2]]
    assert len(first) == 9 and min(c["startTime"] for c in first) == NOW - iw.BACKFILL      # 60 days in 7-day windows
    assert out["stats"]["n"] == 153 and len(out["closed"]) == 153                            # XRP (70 days ago) not fetched
    assert out["closed"][0]["symbol"] == "ADA" and out["closed"][0]["close_time"] >= out["closed"][-1]["close_time"]  # newest first
    sides = {t["symbol"]: t["side"] for t in out["closed"]}
    assert sides["BTC"] == "LONG" and sides["ETH"] == "SHORT" and "id" not in out["closed"][0]
    assert out["stats"]["by_symbol"]["SOL"] == {"n": 150, "pnl": 150.0} and out["stats"]["first_trade"] == NOW - 50 * DAY
    # run 2: a new position appears; run 3: it is closed → open_time = first seen; overlap rows are not duplicated
    t2, t3 = NOW + 300_000, NOW + 600_000
    fb.positions = [pos("DOT", "Buy", 5, 4, 3.9, -0.5)]
    fb.calls.clear()
    out = iw.run(st, t2, fetch=fb, put=lambda b: True, env=ENV)
    inc = [c[2] for c in fb.calls if c[1] == "/v5/position/closed-pnl"]
    assert len(inc) == 1 and inc[0]["startTime"] == NOW - iw.OVERLAP and inc[0]["endTime"] == t2
    assert out["stats"]["n"] == 153 and out["positions"][0]["seen"] == t2 and out["positions"][0]["created"] == NOW - 10 * DAY
    fb.positions = []
    fb.closed.append(row("DOT", "Sell", 4, 3.8, 5, -1, t3 - 1000, "o5"))
    out = iw.run(st, t3, fetch=fb, put=lambda b: True, env=ENV)
    dot = out["closed"][0]
    assert out["stats"]["n"] == 154 and dot["symbol"] == "DOT" and dot["open_time"] == t2 and dot["pnl"] == -1.0
    assert [p[0] for p in out["equity_hist"]] == [NOW, t2, t3]
    iw.MAX_CLOSED_OUT = old_out


def test_ivan_hidden_risk_with_100pct_winrate():
    """All closed trades win, but the averaged loser stays open: winrate 1.0, the risk shows in max_dd and worst_open."""
    fb = FakeBybit("main", closed=[row("BTC", "Sell", 100, 101, 1, 1, NOW - (i + 1) * H, f"w{i}") for i in range(10)],
                   positions=[pos("ETH", "Buy", 5, 2000, 1800, -1000)], equity=2000)
    st = {}
    iw.run(st, NOW, fetch=fb, put=lambda b: True, env=ENV)
    fb.equity = 1500
    iw.run(st, NOW + 300_000, fetch=fb, put=lambda b: True, env=ENV)
    fb.equity = 1700
    out = iw.run(st, NOW + 600_000, fetch=fb, put=lambda b: True, env=ENV)
    assert out["stats"]["winrate"] == 1.0 and out["stats"]["losses"] == 0
    assert out["max_dd"] == 0.25 and out["worst_open"] == round(-1000 / 1700, 4)
    assert out["equity"] == 1700 and out["upnl"] == -1000 and out["wallet"] == 2700 and out["available"] == 500
    assert "только закрытые" in out["note"]
    for k in ("asof", "host", "equity", "wallet", "upnl", "available", "positions", "closed", "stats", "equity_hist", "max_dd", "worst_open", "note"):
        assert k in out
    p = out["positions"][0]
    assert set(p) >= {"symbol", "side", "size", "entry", "mark", "upnl", "leverage", "liq", "value", "created"}
    assert set(out["closed"][0]) >= {"symbol", "side", "qty", "entry", "exit", "pnl", "fee", "open_time", "close_time"}


def test_ivan_encrypt_roundtrip_and_wrong_password():
    obj = {"asof": NOW, "note": iw.NOTE, "equity": 1234.56, "hist": [[NOW + i, 1000 + i / 7] for i in range(50)]}
    blob = iw.encrypt(obj, PASS)
    assert set(blob) == iw.BLOB_KEYS and blob["asof"] == NOW and blob["v"] == 1 and blob["alg"] == "pbkdf2-sha256-200k/hmac-ctr/hmac"
    assert len(base64.b64decode(blob["salt"])) == 16 and len(base64.b64decode(blob["nonce"])) == 16 and len(base64.b64decode(blob["tag"])) == 32
    assert iw.decrypt(blob, PASS) == obj and iw.decrypt(json.dumps(blob), PASS) == obj
    assert "equity" not in json.dumps(blob) and iw.encrypt(obj, PASS)["ct"] != blob["ct"]     # random salt/nonce every time
    for bad in ("wrong", PASS + " "):
        try:
            iw.decrypt(blob, bad)
            raise AssertionError("wrong password accepted")
        except iw.BadPassword:
            pass
    ct = bytearray(base64.b64decode(blob["ct"]))
    ct[0] ^= 1
    try:
        iw.decrypt({**blob, "ct": base64.b64encode(bytes(ct)).decode()}, PASS)
        raise AssertionError("tampered file accepted")
    except iw.BadPassword:
        pass


def test_ivan_publish_body_has_no_secrets():
    sent = []

    def http(method, url, headers, data=None):
        sent.append((method, url, headers, data))
        return (200, {"sha": "abc123"}) if method == "GET" else (201, {})

    fb = FakeBybit("demo", closed=[row("BTC", "Sell", 100, 110, 1, 10, NOW - H, "o1")], positions=[pos("ETH", "Buy", 1, 2000, 1900, -100)])
    out = iw.run({}, NOW, fetch=fb, put=lambda b: iw.publish_ivan(b, "ghtok", "o/r", http=http), env=ENV)
    assert out["equity"] == 900
    get, put = sent
    assert get[0] == "GET" and get[1] == "https://api.github.com/repos/o/r/contents/web/ivan.enc.json?ref=main"
    assert put[0] == "PUT" and put[1] == "https://api.github.com/repos/o/r/contents/web/ivan.enc.json"
    body = json.loads(put[3])
    assert body["message"] == "ivan: update" and body["sha"] == "abc123" and body["branch"] == "main"
    content = base64.b64decode(body["content"]).decode()
    for s in (KEY, SECRET, PASS):
        assert s not in put[3].decode() and s not in content and s not in json.dumps(put[2], ensure_ascii=False)
    blob = json.loads(content)
    assert set(blob) == iw.BLOB_KEYS and '"positions"' not in content and blob["asof"] == NOW
    assert iw.decrypt(blob, PASS)["positions"][0]["symbol"] == "ETH"
    try:
        iw.publish_ivan({"equity": 1, "ct": "x", "salt": "x", "nonce": "x", "tag": "x"}, "t", "o/r", http=http)
        raise AssertionError("plaintext field published")
    except ValueError:
        pass
    assert iw.publish_ivan(blob, "", "o/r", http=http) is False


def test_ivan_does_nothing_without_env():
    called = []
    spy = lambda *a, **k: called.append(a)  # noqa: E731
    assert iw.run({}, NOW, fetch=spy, put=spy, env={}) is None
    assert iw.run({}, NOW, fetch=spy, put=spy, env={"IVAN_BYBIT_KEY": "k", "IVAN_BYBIT_SECRET": "s"}) is None
    assert iw.run({}, NOW, fetch=spy, put=spy, env={**ENV, "IVAN_PASS": ""}) is None
    assert iw.check(env={"IVAN_PASS": "x"}, fetch=spy) is None
    assert not called


def test_mirror_ivan_tick_never_raises():
    from cnq.execution import mirror
    orig = iw.run

    def boom(*a, **k):
        raise iw.KeyRejected(10003, "API key is invalid.")
    try:
        iw.run = boom
        assert mirror.ivan_tick() is False
        iw.run = lambda *a, **k: None
        assert mirror.ivan_tick() is False
    finally:
        iw.run = orig
    assert iw.describe_error(iw.BybitError(10002, "ts")).startswith("Иван тест: часы")


def test_ivan_js_decrypt_matches_python():
    """scripts/ivan_decrypt.js (WebCrypto) decrypts what Python encrypted; a wrong password throws «Неверный пароль»."""
    node = shutil.which("node")
    if not node:
        return
    obj = {"asof": NOW, "note": iw.NOTE, "equity": 1234.56, "closed": [{"symbol": "BTC", "pnl": -0.1 * i, "t": NOW - i} for i in range(80)]}
    p = os.path.join(tempfile.mkdtemp(), "blob.json")
    with open(p, "w") as f:
        json.dump(iw.encrypt(obj, PASS), f)
    code = ("const {ivanDecrypt}=require(process.argv[1]);const b=JSON.parse(require('fs').readFileSync(process.argv[2],'utf8'));"
            "ivanDecrypt(b,process.argv[3]).then(o=>console.log(JSON.stringify(o)),e=>console.log('ERR:'+e.message))")
    js = os.path.join(ROOT, "scripts", "ivan_decrypt.js")
    r = subprocess.run([node, "-e", code, js, p, PASS], capture_output=True, text=True, timeout=120)
    assert json.loads(r.stdout) == obj, r.stderr
    r = subprocess.run([node, "-e", code, js, p, "wrong"], capture_output=True, text=True, timeout=120)
    assert r.stdout.strip() == "ERR:Неверный пароль", r.stdout + r.stderr
