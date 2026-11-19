"""19.11 (owner): «Мои прогнозы (Дмитрий)» — the owner's paper calls. Made-up prices; no network."""
import json
import os
import shutil
import subprocess
import tempfile

from cnq.api import my_calls as MC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAY = 86_400_000
T0 = 1_795_000_000_000 - 1_795_000_000_000 % DAY + 10 * 3_600_000          # a day at 10:00 UTC


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def fake_get(prices, candles, okx_ok=True):
    def get(url, **q):
        if "okx.com" in url and "ticker" in url:
            return {"code": "0", "data": [{"last": str(prices[q["instId"].split("-")[0]])}]} if okx_ok else {"code": "51001", "data": []}
        if "bingx" in url:
            return {"code": 0, "data": [{"symbol": q["symbol"], "trades": [{"price": str(prices[q["symbol"].split("-")[0]])}]}]}
        if "history-candles" in url:
            start = int(q["after"]) - 60_000
            c = candles.get((q["instId"].split("-")[0], start))
            return {"code": "0", "data": [[str(start), "0", "0", "0", str(c)]] if c else []}
        raise AssertionError(url)
    return get


def test_a_call_is_stored_with_its_time_and_price_and_scored_at_its_end(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "market_view.json").write_text(json.dumps({"asof": T0, "assets": [{"asset": "BTC", "direction": "вверх", "horizon": "24 ч", "text_ru": "покупатели сильнее"}]}))
    clk = Clock(T0)
    end_today = T0 - T0 % DAY + DAY
    prices = {"BTC": 100_000.0, "ETH": 3_000.0}
    candles = {("BTC", end_today - 0): 101_000.0, ("ETH", T0 + DAY): 3_150.0, ("BTC", T0 + 3 * DAY): 99_500.0}
    d = MC.MyCalls(str(tmp_path / "data"), str(web), get=fake_get(prices, candles), clock=clk)
    code, r = d.add({"asset": "btc", "dir": "up", "horizon": "today", "comment": "пробой"})
    assert code == 200 and r["open"][0]["entry_px"] == 100_000.0 and r["open"][0]["entry_src"] == "OKX спот BTC-USDT"
    c = r["open"][0]
    assert c["t"] == T0 and c["end_t"] == end_today and c["asset"] == "BTC" and c["system"]["direction"] == "вверх"
    code, r = d.add({"asset": "ETH", "dir": "down", "horizon": "24h"})
    code, r = d.add({"asset": "BTC", "dir": "flat", "horizon": "3d"})
    assert len(r["open"]) == 3 and r["system_ru"] is None
    assert d.add({"asset": "BT C$", "dir": "up", "horizon": "24h"})[0] == 200          # cleaned to «BTC»
    assert d.add({"asset": "BTC", "dir": "sideways", "horizon": "24h"})[0] == 400
    # no edit route exists; delete only before the end, and the deletion stays visible
    cid = r["open"][-1]["id"] if r["open"][-1]["horizon"] == "24h" else None
    code, r = d.delete([x for x in d.view()["open"] if x["dir"] == "up" and x["horizon"] == "24h"][0]["id"])
    assert code == 200 and len(r["deleted"]) == 1 and r["deleted"][0]["deleted_t"] == T0
    # the ends come: exit = the 1-minute candle's close at the end time
    clk.t = T0 + 3 * DAY + 60_000
    assert d.settle() == 3
    v = d.view()
    by = {(c["asset"], c["horizon"]): c for c in v["closed"]}
    a = by[("BTC", "today")]
    assert a["exit_px"] == 101_000.0 and a["hit"] and a["move_pct"] == 1.0 and a["pnl_usd"] == round(1000 * 0.01 - 1, 2)
    e = by[("ETH", "24h")]                                    # «вниз», the price went up 5 % → miss, a short loses on paper
    assert not e["hit"] and e["towards_pct"] == -5.0 and e["pnl_usd"] == -51.0
    f = by[("BTC", "3d")]                                     # «нейтрально»: −0.5 % inside ±2 % → hit, no position
    assert f["hit"] and f["pnl_usd"] == 0.0 and f["band_pct"] == 2.0
    assert v["stats"]["all"] == {"n": 3, "hits": 2, "pct": 66.7, "pnl_usd": -42.0}
    assert v["stats"]["by_asset"]["ETH"]["hits"] == 0 and v["stats"]["by_horizon"]["3d"]["pct"] == 100.0
    assert v["curve"][-1][1] == -42.0 and v["real_account"] is False and v["paper"] is True
    assert d.delete(a["id"])[0] == 409                       # a closed call cannot be deleted


def test_bingx_when_okx_has_no_price_and_no_price_means_no_call(tmp_path):
    d = MC.MyCalls(str(tmp_path / "data"), None, get=fake_get({"XYZ": 2.5}, {}, okx_ok=False), clock=Clock(T0))
    code, r = d.add({"asset": "XYZ", "dir": "up", "horizon": "7d"})
    assert code == 200 and r["open"][0]["entry_src"] == "BingX спот XYZ-USDT" and r["system_ru"]
    def none(url, **q):
        raise OSError("down")
    d2 = MC.MyCalls(str(tmp_path / "d2"), None, get=none, clock=Clock(T0))
    code, r = d2.add({"asset": "BTC", "dir": "up", "horizon": "7d"})
    assert code == 502 and "не записан" in r["message_ru"] and d2.view()["open"] == []


def test_owner_only_paper_and_never_near_the_real_account():
    src = open(os.path.join(ROOT, "cnq", "api", "my_calls.py"), encoding="utf-8").read()
    assert "cnq.execution" not in src and "from cnq" not in src and "order" not in src.split('"""', 2)[2]
    srv = open(os.path.join(ROOT, "cnq", "api", "server.py"), encoding="utf-8").read()
    j = srv.index('if path == "/v2/admin/my_calls" or path.startswith("/v2/admin/my_calls/"):')
    assert srv.index("deny = self.console.check(headers)", j) < srv.index("self._my_calls(", j)
    assert srv.count("self._my_calls(") == 1 and "my_calls" not in srv[srv.index("def _owner("):srv.index("def _owner(") + 200]
    adm = open(os.path.join(ROOT, "web", "admin.html"), encoding="utf-8").read()
    assert "Мои прогнозы (Дмитрий)" in adm and '<script src="my_calls.js?v=1"></script>' in adm and "api('/v2/admin/my_calls'+p" in adm
    for f in ("app.html", "vip.html", "index.html"):
        assert "my_calls" not in open(os.path.join(ROOT, "web", f), encoding="utf-8").read(), f
    assert not os.path.exists(os.path.join(ROOT, "web", "my_calls.json"))


def test_the_panel_shows_open_closed_rates_and_the_system_next_to_dmytro():
    node = shutil.which("node")
    if not node:
        return
    D = {"label_ru": "ТЕСТ", "rule_ru": "ПРАВИЛО", "horizons": {"today": "до 00:00 UTC", "24h": "24 ч", "3d": "3 дня", "7d": "7 дней"},
         "open": [{"id": "o1", "asset": "BTC", "dir": "up", "horizon": "24h", "t": T0, "end_t": T0 + DAY, "entry_px": 100000, "entry_src": "OKX спот BTC-USDT",
                   "comment": "ПОЧЕМУ", "system": {"direction": "вниз", "horizon": "24 ч"}}],
         "closed": [{"id": "c1", "asset": "ETH", "dir": "down", "horizon": "3d", "t": T0, "end_t": T0, "entry_px": 3000, "exit_px": 3150, "move_pct": 5,
                     "hit": False, "pnl_usd": -51, "exit_src": "OKX", "system": None}],
         "deleted": [{"id": "d1", "asset": "SOL", "dir": "up", "horizon": "7d", "t": T0, "deleted_t": T0}],
         "stats": {"all": {"n": 1, "hits": 0, "pct": 0, "pnl_usd": -51}, "by_asset": {"ETH": {"n": 1, "hits": 0, "pct": 0, "pnl_usd": -51}},
                   "by_horizon": {"3d": {"n": 1, "hits": 0, "pct": 0, "pnl_usd": -51}}}, "curve": [[T0, -51]], "system_ru": None}
    p = os.path.join(tempfile.mkdtemp(), "m.js")
    open(p, "w", encoding="utf-8").write(f"global.window={{}};require({json.dumps(os.path.join(ROOT, 'web', 'my_calls.js'))});"
                                        f"process.stdout.write(window.CNMyCalls.html({json.dumps(D, ensure_ascii=False)},{{}}))")
    h = subprocess.run([node, p], capture_output=True, text=True, timeout=30).stdout
    for w in ("Записать", "вверх", "нейтрально", "до 00:00 UTC", "Система CRYPTO NOVA AI сказала", "Дмитрий сказал", "ПОЧЕМУ", "мимо",
              "По активам", "По срокам", "Удалённые до срока", "удалён", "ПРАВИЛО", 'data-mc="del"'):
        assert w in h, w
    assert "Изменить" not in h and "edit" not in h                     # no edit
