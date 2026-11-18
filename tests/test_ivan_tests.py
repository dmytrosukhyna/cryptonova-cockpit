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
    # 18.11: one handler (_ivan_tests) reached only after an owner check — the owner session (_owner) or, for «Управление»
    # (admin.html), the same check as every /v2/admin/* call (console.check)
    i = srv.index("def _owner(")
    assert srv.count("self._ivan_tests(") == 2
    assert srv.index("self.owner_auth.require(headers)", i) < srv.index("self._ivan_tests(", i)
    j = srv.index('if path == "/v2/admin/ivan_tests" or path.startswith("/v2/admin/ivan_tests/"):')
    assert srv.index("deny = self.console.check(headers)", j) < srv.index("self._ivan_tests(", j) < srv.index("def _ivan_tests(")
    core, app, vip = r("web", "vip", "core.js"), r("web", "app.html"), r("web", "vip.html")
    assert "{ id: 'ivan_tests', name: 'Иван · тест-стратегии', short: 'Иван ×3', icon: 'strategies', owner: true }" in core
    assert '<script src="vip/ivan_tests.js"></script>' in vip and 'id="ivanTestsCard" hidden' in app
    j = app.index("async function renderIvanTests")
    assert app.index("CNAuth.state.isOwner", j) < app.index("fetchOwner('/v2/owner/ivan_tests')", j)
    js = r("web", "ivan_tests.js")
    assert "Только ключ «Read-Only»" in js and 'type="password"' in js and "ivan_tests.json" not in js + app
    doc = r("docs", "ivan-bybit-readonly-key.md")
    assert "Read-Only" in doc and "Demo Trading" in doc and "never post" in doc.lower() and "общих чатах" in doc


def test_the_real_copy_switch_is_the_owners_own_click_with_a_plain_confirm():
    """17.11: the panel's switch «Копировать на реальный счёт» of one account — POST /v2/owner/ivan_tests/copy_real {id, on} from the
    owner's session only, a confirm that says «реальные деньги» with the limits before ON, the server's refusal shown as it came;
    the held loss and the drawdown stand next to the win rate; ids ivan2: / ivan3: / ivan4: read «копия: Иван · тест N»."""
    import shutil
    import subprocess
    import tempfile
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rd = lambda *p: open(os.path.join(root, *p), encoding="utf-8").read()                                      # noqa: E731
    js, vip, app = rd("web", "ivan_tests.js"), rd("web", "vip", "ivan_tests.js"), rd("web", "app.html")
    for src in (vip, app):
        assert "/copy_real" in src and "fetchOwner(" in src
    assert "copyReal: (id, on) => call('/copy_real', { id, on })" in vip
    assert "copyReal:(id,on)=>CNAuth.fetchOwner('/v2/owner/ivan_tests/copy_real',{method:'POST',body:{id,on}})" in app
    j = app.index("async function renderIvanTests")
    assert app.index("CNAuth.state.isOwner", j) < app.index("fetchOwner('/v2/owner/ivan_tests')", j)         # still owner-only
    assert "window.confirm(on ? confirmOn(" in js and js.index("window.confirm(on ? confirmOn(") < js.index("api.copyReal(b.dataset.id, on)")
    assert "err.data.message_ru" in js and "Иван Тест 2 / 3 / 4" in js
    assert "copy_real" not in js.split("function mount")[1].split("data-it=\"copy\"")[0]                     # nothing switches by itself
    node = shutil.which("node")
    if not node:
        return
    acc = {"id": "a1", "name": "Иван Тест 3", "host": "demo", "key": "ab…yz", "copy_real": True, "slot": 3, "copy_id": "ivan3",
           "copy_since": 1794900000000,
           "real": {"open_n": 1, "open_pnl_usd": -4.2, "open": [], "closed_n": 2, "closed_pnl_usd": 3.1, "wins": 1, "total_pnl_usd": -1.1},
           "study": {"since": 1, "closed_pnl_usd": 120, "open_pnl_usd": -340.5, "total_pnl_usd": -220.5, "worst_open_usd": -512,
                     "max_dd_usd": 600, "max_dd_pct": 5.5, "open_n": 3, "open_averaged_n": 2, "open_max_steps": 4, "entries": 5, "adds": 7,
                     "averagings": 6, "reduces": 1, "exits": 2, "winrate": 1, "closed_n": 2, "note_ru": "N",
                     "ladders": [{"key": "ETH|LONG", "steps": 4, "adds": 3, "avg_downs": 3, "avg": 3000, "first_px": 3200, "worst_upnl": -300}],
                     "last_events": [{"t": 1, "symbol": "ETH", "side": "LONG", "kind": "add", "averaging": True, "step": 4, "upnl": -280}]},
           "data": {"account": {"equity": 10000, "positions": [], "closed": []}, "paper": {}, "summary": {}}}
    doc = {"asof": 1, "label_ru": "ЯРЛЫК", "paper_rule_ru": "P", "copy_rule_ru": "ПРАВИЛО КОПИИ", "copy_real_any": True,
           "accounts": [acc, {"id": "a2", "name": "Иван Тест 2", "host": "demo", "key": "k", "copy_real": False, "slot": None, "data": None}]}
    a, b = app.index("// WHALE_LABEL_START"), app.index("// WHALE_LABEL_END")
    script = f"""global.window={{}};require({json.dumps(os.path.join(root, "web", "ivan_tests.js"))});const C=window.CNIvanTests;
{app[a:b]}
const names={{}}, kindRu=g=>g, r=(sig,st,guest)=>realDeskOf(sig,st,guest,[],names,kindRu);
process.stdout.write(JSON.stringify({{html:C.html({json.dumps(doc, ensure_ascii=False)},{{msg:'ОТВЕТ СЕРВЕРА'}}),on:C.confirmOn('Иван Тест 3',3),off:C.confirmOff('Иван Тест 3'),
  labels:[r('ivan2:W-ETH-L-1',null,false),r('ivan3:W-ETH-L-1',null,false),r('ivan4:W-X-S-2',null,false),r('x:1','ivan4',false),r('ivan:W-ETH-L-1',null,false),
          r('ivan5:W-ETH-L-1',null,false),r('ivan3:W-ETH-L-1',null,true),r('whales:W-1',null,false)]}}));"""
    p = os.path.join(tempfile.mkdtemp(), "t.js")
    open(p, "w", encoding="utf-8").write(script)
    out = json.loads(subprocess.run([node, p], capture_output=True, text=True, timeout=30).stdout)
    h = out["html"]
    assert "ВКЛЮЧЕНО" in h and "«Иван · тест 3»" in h and "id ivan3:" in h and 'aria-checked="true"' in h and 'data-on="0"' in h
    assert 'aria-checked="false"' in h and 'data-on="1"' in h and "Включить…" in h                    # the second account is off
    assert "ЯРЛЫК" in h and "ПРАВИЛО КОПИИ" in h and "ОТВЕТ СЕРВЕРА" in h
    for words in ("Открытый результат (не закрыт)", "Худший открытый убыток", "Макс. просадка", "В плюсе (только закрытые)",
                  "Доливки / усреднения", "Открытые лесенки", "Наши реальные копии этого счёта", "усреднение"):
        assert words in h, words
    import re
    for rx in (r"−\$340[.,]50", r"−\$512[.,]00", r"−\$600[.,]00", r"5[.,]50% от пика"):       # node may run without the ru locale
        assert re.search(rx, h), rx
    k = h.index('class="it-k"')                                    # the held loss and the drawdown come BEFORE the win rate, same tiles
    assert h.index("Открытый результат", k) < h.index("Худший открытый убыток", k) < h.index("Макс. просадка", k) < h.index("В плюсе (только", k)
    on, off = out["on"], out["off"]
    for words in ("РЕАЛЬНЫЙ счёт", "реальные деньги", "не больше 50% бюджета фьючерсов", "стоп −16%", "дневной стоп", "не больше 15 копий",
                  "только НОВЫЕ копии", "по нашему стопу", "не гарантирует"):
        assert words in on, words
    assert "Новых копий не будет" in off and "по нашему стопу" in off
    assert out["labels"] == ["копия: Иван · тест 2", "копия: Иван · тест 3", "копия: Иван · тест 4", "копия: Иван · тест 4", "копия: Иван",
                             "W", "сигнал системы CRYPTO NOVA AI", "копия трейдера"]      # ivan5: is not a copy id


def test_ivan_tests_in_upravlenie_need_the_owner_and_go_through_admin_html_api(tmp_path, monkeypatch):
    """18.11 (owner): «Иван Тест» is in «Управление» (admin.html?embed=1). Its calls (/v2/admin/ivan_tests*) need the same owner
    credential as the rest of the admin panel — no token / a wrong token → refused; the owner's CNQ_ADMIN_TOKEN → the view."""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import test_api as TA
    monkeypatch.setenv("CNQ_ADMIN_TOKEN", "t" * 40)
    api, _ = TA.make(tmp_path)
    for tok in (None, "x" * 40):
        code, _d = TA.call(api, "GET", "/v2/admin/ivan_tests", tok=tok)
        assert code == 401
        code, _d = TA.call(api, "POST", "/v2/admin/ivan_tests/copy_real", {"id": "a", "on": True}, tok=tok)
        assert code == 401
    code, d = TA.call(api, "GET", "/v2/admin/ivan_tests", tok="t" * 40)
    assert code == 200 and d["accounts"] == [] and d["owner_only"] is True
    code, d = TA.call(api, "POST", "/v2/admin/ivan_tests/copy_real", {"id": "nope", "on": True}, tok="t" * 40)
    assert code == 404
    code, d = TA.call(api, "GET", "/v2/owner/ivan_tests", tok="t" * 40)      # the owner area itself still wants the owner session
    assert code == 401
    adm = open(os.path.join(ROOT, "web", "admin.html"), encoding="utf-8").read()
    assert "Иван Тест (2 / 3 / 4) — ключи и копирование" in adm and '<script src="ivan_tests.js?v=2"></script>' in adm
    assert "const ivtCall=async(p,body)=>{ const r=await api('/v2/admin/ivan_tests'+p" in adm and "fetch(" not in adm.split("const ivtCall")[1].split("async function ivtLoad")[0]
    assert "copyReal:(id,on)=>ivtCall('/copy_real',{id,on})" in adm and "loaders.overview=async()=>{\n  ivtLoad();" in adm
