"""«Мой портфель · OKX» (cnq/execution/myspot_watch.py): read-only OKX client, merge of balances, the model's plan, encryption
(Python and the browser's ivanDecrypt), the publisher refuses plaintext, nothing happens without the MYSPOT_* env.
All coins and amounts here are made-up test data."""
import base64
import inspect
import json
import os
import shutil
import subprocess
import tempfile

from cnq.execution import ivan_watch as iw
from cnq.execution import myspot_watch as ms

KEY, SECRET, PHRASE, PASS = "MSKEY-test-1", "MSSECRET-test-2", "MSPHRASE-test-3", "тестовый пароль панели 7"
ENV = {"MYSPOT_OKX_KEY": KEY, "MYSPOT_OKX_SECRET": SECRET, "MYSPOT_OKX_PASSPHRASE": PHRASE, "MYSPOT_PASS": PASS}
NOW = 1_800_000_000_000
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MODEL = {"asof": NOW, "gauge": {"on": True, "bull": True, "breadth_pct": 68},
         "targets_pct": {"BTC": 40.0, "Фьючерсы (сигналы)": 15.0, "USDT (Earn)": 10.0, "ETH": 10.0, "SOL": 5.0, "AAA": 10.0, "BBB": 10.0},
         "basket": ["AAA", "BBB"]}
QUALITY = {"asof": NOW, "universe": 50, "basket": ["CCC", "AAA"], "evidence": {"btc_regime_x": 2.19},
           "rows": [{"symbol": "CCC", "rank": 1, "score": 1.3, "age": 2000, "m3": -0.2, "vt": 0.5, "rev": True, "rev30": 2_500_000},
                    {"symbol": "AAA", "rank": 2, "score": 1.2, "age": 900, "m3": -0.1, "vt": 0.8, "rev": True, "rev30": 40_000},
                    {"symbol": "BBB", "rank": 9, "score": 0.9, "age": 500, "m3": 0.35, "vt": 1.6, "rev": False},
                    {"symbol": "EEE", "rank": 3, "score": 1.1, "age": 3000, "m3": -0.05, "vt": 0.7, "rev": True, "rev30": 9000}]}
PROFILES = {"coins": {"BTC": {"name": "Bitcoin", "sector": "цифровое золото"}}}


def tick(inst, last, open24):
    return {"instId": inst, "last": str(last), "open24h": str(open24)}


class FakeOkx:
    """Answers like OKX v5 on one host; the other hosts say «API key doesn't exist» (50119)."""
    def __init__(self, host="https://eea.okx.com", perm="read_only"):
        self.host, self.perm, self.calls = host, perm, []

    def __call__(self, host, path, params, signed):
        self.calls.append((host, path, dict(params or {}), signed))
        if signed and host != self.host:
            return {"code": "50119", "msg": "API key doesn't exist", "data": []}
        if path == "/api/v5/account/balance":
            return {"code": "0", "data": [{"details": [
                {"ccy": "BTC", "eq": "0.05", "eqUsd": "5000", "accAvgPx": "80000", "spotUpl": "1000"},
                {"ccy": "USDT", "eq": "1000", "eqUsd": "1000"},
                {"ccy": "DDD", "eq": "100", "eqUsd": "1500"},
                {"ccy": "DUST", "eq": "3", "eqUsd": "0.3"}]}]}
        if path == "/api/v5/asset/balances":
            return {"code": "0", "data": [{"ccy": "ETH", "bal": "0.5"}, {"ccy": "BTC", "bal": "0.01"}]}
        if path == "/api/v5/finance/savings/balance":
            return {"code": "0", "data": [{"ccy": "USDT", "amt": "500"}]}
        if path == "/api/v5/account/config":
            return {"code": "0", "data": [{"perm": self.perm}]}
        if path == "/api/v5/market/tickers":
            assert not signed and params == {"instType": "SPOT"}
            return {"code": "0", "data": [tick("BTC-USDT", 100000, 98000), tick("ETH-USDT", 2000, 2100), tick("DDD-USDT", 15, 15),
                                          tick("DUST-USDT", 0.1, 0.1), tick("AAA-USDT", 1, 1)]}
        raise AssertionError(path)


def test_myspot_client_is_read_only_and_finds_the_host():
    src = inspect.getsource(ms)
    assert "POST" not in src and "/trade/" not in src and "withdraw" not in src.replace("withdraw\" in perm", "")
    fb = FakeOkx("https://eea.okx.com")
    host, bal = ms.probe(fb)
    assert host == "https://eea.okx.com" and bal[0]["details"]
    assert [c[0] for c in fb.calls] == ["https://www.okx.com", "https://eea.okx.com"]
    fb.calls.clear()
    assert ms.probe(fb, "https://eea.okx.com")[0] == "https://eea.okx.com" and len(fb.calls) == 1   # the remembered host first


def test_myspot_assets_merge_places_value_24h_and_cost():
    fb = FakeOkx()
    rows = ms.assets(ms.read_raw(fb, fb.host))
    by = {r["ccy"]: r for r in rows}
    assert set(by) == {"BTC", "USDT", "DDD", "ETH"}                       # DUST ($0.30) is skipped
    assert abs(by["BTC"]["qty"] - 0.06) < 1e-12 and by["BTC"]["usd"] == 6000.0 and by["BTC"]["where"] == ["торговый счёт", "счёт финансирования"]
    assert by["BTC"]["ch24"] == round((100000 / 98000 - 1) * 100, 2) and by["BTC"]["pnl_pct"] == 25.0 and by["BTC"]["pnl_usd"] == 1000.0 and by["BTC"]["pnl_part"]
    assert by["USDT"]["usd"] == 1500.0 and by["USDT"]["cash"] and "pnl_usd" not in by["USDT"] and "Earn" in by["USDT"]["where"]
    assert by["ETH"]["usd"] == 1000.0 and "pnl_usd" not in by["ETH"]     # no cost data from the funding account → no P/L shown
    assert abs(sum(r["share"] for r in rows) - 100) < 0.05 and rows[0]["ccy"] == "BTC"


def test_myspot_plan_math_reasons_and_order():
    rows = [{"ccy": "BTC", "usd": 3000.0, "cash": False}, {"ccy": "ETH", "usd": 1500.0, "cash": False},
            {"ccy": "BBB", "usd": 200.0, "cash": False}, {"ccy": "DDD", "usd": 1500.0, "cash": False},
            {"ccy": "CCC", "usd": 800.0, "cash": False}, {"ccy": "USDT", "usd": 3000.0, "cash": True}]
    p = ms.plan(rows, MODEL, QUALITY, PROFILES)
    T = 10_000.0
    assert p["total_usd"] == T and p["regime"] == "рост" and p["altseason"]
    by = {r["ccy"]: r for r in p["rows"]}
    assert by["BTC"]["act"] == "buy" and by["BTC"]["gap_usd"] == 1000.0 and "×2.19" in by["BTC"]["why"] and "цифровое золото" in by["BTC"]["why"]
    assert by["ETH"]["act"] == "trim" and by["ETH"]["gap_usd"] == -500.0
    assert by["SOL"]["act"] == "buy" and by["SOL"]["gap_usd"] == 500.0 and by["SOL"]["tag"] == "ядро"
    assert by["AAA"]["act"] == "buy" and by["AAA"]["gap_usd"] == 1000.0 and by["AAA"]["tag"] == "не перегрета"
    assert "2 место из 50" in by["AAA"]["quality"]["text"] and "$40 тыс. за 30 дней" in by["AAA"]["quality"]["text"]
    assert by["BBB"]["act"] == "buy" and "обогнала BTC на 35%" in by["BBB"]["tag"] and "объём торгов вырос ×1.6" in by["BBB"]["quality"]["text"]
    assert by["DDD"]["act"] == "sell" and by["DDD"]["quality"] is None and "нет" in by["DDD"]["why"]   # no screen row → nothing invented
    assert by["CCC"]["act"] == "keep" and "10 лучших по анкете качества" in by["CCC"]["why"]
    # undervalued first: the calm alt, then the core by gap, then the overheated alt
    assert [r["ccy"] for r in p["buy"]] == ["AAA", "BTC", "SOL", "BBB"]
    assert [r["ccy"] for r in p["trim"]] == ["DDD", "ETH"]
    assert p["cash"]["target_pct"] == 25.0 and p["cash"]["gap_usd"] == -500.0 and "15% — резерв под фьючерсы" in p["cash"]["why"] and "Ядро модели — 40% портфеля" in by["BTC"]["why"]
    assert [i["ccy"] for i in p["ideas"]] == ["EEE"]                       # calm, with revenue, not held, not in the model
    assert by["AAA"]["basket"] and not by["AAA"]["held"] and by["BTC"]["held"] and "биткоин обгоняют 68% альтов" in p["alt_note"]
    warm = ms.plan([{"ccy": "USDT", "usd": 10_000.0, "cash": True}], MODEL, {"universe": 9, "rows": [
        {"symbol": "AAA", "rank": 3, "age": 800, "m3": -0.1, "vt": 1.4, "rev": False}]})
    assert {r["ccy"]: r.get("tag") for r in warm["buy"]}["AAA"] == "объём торгов растёт — докупать частями"   # a row exists → never «нет оценки»
    small = ms.plan([{"ccy": "BTC", "usd": 3995.0, "cash": False}, {"ccy": "USDT", "usd": 6005.0, "cash": True}], MODEL)
    assert {r["ccy"]: r["act"] for r in small["rows"]}["BTC"] == "hold"    # within ±1 p.p. of 40 % → no order


def test_myspot_run_publishes_only_the_encrypted_blob():
    sent = []
    st = {}
    out = ms.run(st, NOW, fetch=FakeOkx(), put=lambda b: sent.append(b) or True, env=ENV, model=(MODEL, QUALITY, PROFILES))
    assert out["host"] == "eea.okx.com" and out["plan"]["total_usd"] == 10000.0 and st["host"] == "https://eea.okx.com"
    assert out["hist"] == [[NOW, 10000.0]]
    (blob,) = sent
    assert set(blob) == iw.BLOB_KEYS and blob["asof"] == NOW
    text = json.dumps(blob)
    for s in (KEY, SECRET, PHRASE, PASS, '"assets"', '"plan"'):
        assert s not in text and s not in base64.b64decode(blob["ct"]).decode("latin-1")
    assert iw.decrypt(blob, PASS)["assets"][0]["ccy"] == "BTC"
    try:
        ms.publish_myspot({"assets": [], "ct": "x", "salt": "x", "nonce": "x", "tag": "x"}, "t", "o/r", http=lambda *a: (201, {}))
        raise AssertionError("plaintext published")
    except ValueError:
        pass
    calls = []
    assert ms.publish_myspot(blob, "ghtok", "o/r", http=lambda m, u, h, d=None: calls.append((m, u)) or ((200, {"sha": "s"}) if m == "GET" else (201, {})))
    assert calls[-1] == ("PUT", "https://api.github.com/repos/o/r/contents/web/myspot.enc.json")
    assert ms.publish_myspot(blob, "", "o/r") is False


def test_myspot_does_nothing_without_env_and_warns_about_trade_permission():
    called = []
    spy = lambda *a, **k: called.append(a) or {"code": "0", "data": []}  # noqa: E731
    for k in ENV:
        env = {kk: vv for kk, vv in ENV.items() if kk != k}
        assert ms.run({}, NOW, fetch=spy, put=spy, env=env) is None and ms.check(env=env, fetch=spy, save=False) is None
    assert called == []
    tmp = tempfile.mkdtemp()
    env = {**ENV, "MYSPOT_STATE": os.path.join(tmp, "st.json")}
    assert ms.check(env=env, fetch=FakeOkx(), save=False) == "Мой портфель OKX: ключ работает (eea.okx.com), монет на торговом счёте: 3"
    assert "право торговли" in ms.check(env=env, fetch=FakeOkx(perm="read_only,trade"), save=False)
    assert ms.describe_error(ms.OkxError("50105", "x")).startswith("Мой портфель OKX: неверная фраза ключа")


def test_myspot_js_decrypt_matches_python():
    """The panel decrypts web/myspot.enc.json with ivanDecrypt (scripts/ivan_decrypt.js is its copy)."""
    node = shutil.which("node")
    if not node:
        return
    obj = {"asof": NOW, "assets": [{"ccy": "TEST", "usd": 1.5, "why": "проверка — ×2,19"}], "plan": {"note": ms.NOTE}}
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


def test_myspot_panel_decrypt_is_the_reference_copy():
    """The panel card (web/app.html) carries its own msDecrypt between /*msDecrypt>*/ and /*<msDecrypt*/: it must decrypt what
    Python encrypted, and the card must only ever read the encrypted file."""
    html = open(os.path.join(ROOT, "web", "app.html"), encoding="utf-8").read()
    a, b = html.index("/*msDecrypt>*/") + len("/*msDecrypt>*/"), html.index("/*<msDecrypt*/")
    fn = html[a:b]
    assert fn.strip().startswith("async function msDecrypt(blob, pass)")
    assert "myspot.enc.json" in html and "myspot.json'" not in html
    node = shutil.which("node")
    if not node:
        return
    obj = {"asof": NOW, "assets": [{"ccy": "TEST", "usd": 2.5}]}
    tmp = tempfile.mkdtemp()
    p, js = os.path.join(tmp, "blob.json"), os.path.join(tmp, "dec.js")
    with open(p, "w") as f:
        json.dump(iw.encrypt(obj, PASS), f)
    with open(js, "w", encoding="utf-8") as f:
        f.write(fn + "\nmodule.exports={msDecrypt};\n")
    code = ("const {msDecrypt}=require(process.argv[1]);const b=JSON.parse(require('fs').readFileSync(process.argv[2],'utf8'));"
            "msDecrypt(b,process.argv[3]).then(o=>console.log(JSON.stringify(o)),e=>console.log('ERR:'+e.message))")
    r = subprocess.run([node, "-e", code, js, p, PASS], capture_output=True, text=True, timeout=120)
    assert json.loads(r.stdout) == obj, r.stderr
    r = subprocess.run([node, "-e", code, js, p, "wrong"], capture_output=True, text=True, timeout=120)
    assert r.stdout.strip() == "ERR:Неверный пароль", r.stdout + r.stderr
