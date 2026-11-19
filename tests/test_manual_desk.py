"""19.11 (owner): «Ручной стол (Дмитрий)» stage 1 — paper only. A fake OKX; no network, no exchange order."""
import json
import os
import shutil
import subprocess
import tempfile

import pytest

from cnq.api import manual_desk as MD

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
H = 3_600_000
T0 = 1_795_000_000_000 - 1_795_000_000_000 % H


def h1_bars(low_at=None):
    """300 hourly bars around 100 with one clear swing low (97) 20 bars ago and a swing high (106) 30 bars ago"""
    out = []
    for i in range(300):
        t = T0 - (300 - i) * H
        lo, hi = 98.0 + i * 0.006, 104.5 - i * 0.006                 # no swing anywhere but the two below
        if i == 280:
            lo = 97.0
        if i == 270:
            hi = 106.0
        out.append([str(t), "100", str(hi), str(lo), "100", "0", "0", "0", "1"])
    return out[::-1]                                              # OKX: newest first


class Fake:
    def __init__(self):
        self.last = {"ADA-USDT-SWAP": 100.0, "BTC-USDT-SWAP": 90_000.0}
        self.m1 = []

    def __call__(self, path, **q):
        if path.endswith("/instruments"):
            return [{"instId": "ADA-USDT-SWAP", "state": "live"}, {"instId": "BTC-USDT-SWAP", "state": "live"}, {"instId": "ADA-USD-SWAP"}]
        if path.endswith("/ticker"):
            return [{"last": str(self.last[q["instId"]])}]
        if path.endswith("/candles"):
            if q["bar"] == "1m":
                return [[str(t), str(o), str(h), str(l), str(c), "0", "0", "0", "1"] for t, o, h, l, c in self.m1][::-1]
            return h1_bars()
        raise AssertionError(path)


def make(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "coin_profiles.json").write_text(json.dumps({"coins": {"ADA": {"name": "Cardano"}, "BTC": {"name": "Bitcoin"}}}))
    (web / "market_view.json").write_text(json.dumps({"assets": [{"asset": "ADA", "direction": "вниз"}]}))
    f = Fake()
    clk = {"t": T0}
    return MD.ManualDesk(str(tmp_path / "data"), str(web), get=f, clock=lambda: clk["t"]), f, clk


def test_resolve_by_ticker_or_name_and_refuse_unknown(tmp_path):
    d, f, _ = make(tmp_path)
    assert d.resolve("ada")[1]["inst"] == "ADA-USDT-SWAP" and d.resolve("Cardano")[1]["sym"] == "ADA"
    code, r = d.resolve("NOPE")
    assert code == 404 and r["message_ru"] == "нет такой монеты на OKX"


def test_preview_open_ladder_breakeven_trailing_and_stats(tmp_path):
    d, f, clk = make(tmp_path)
    code, p = d.preview({"sym": "ADA", "side": "long"})
    assert code == 200 and p["entry"] == pytest.approx(100.05)
    assert p["stop"] == pytest.approx(97 * 0.999) and "минимумом H1 97" in p["stop_why"]                 # beyond the H1 swing low
    dist = (100.05 - 97 * 0.999) / 100.05
    assert p["risk_usd"] == pytest.approx(300.0, abs=0.05) and p["lev"] == pytest.approx(300 / dist / 10_000, abs=0.01)
    assert [t["r"] for t in p["tps"]] == [1.0, 2.0, 3.0] and p["tps"][0]["px"] == pytest.approx(100.05 + (100.05 - 96.903))
    assert d.open({"sym": "ADA", "side": "long"})[0] == 400                                                # one confirm tap needed
    code, v = d.open({"sym": "ADA", "side": "long", "confirm": True})
    pos = v["open"][0]
    assert code == 200 and pos["system"]["direction"] == "вниз" and pos["ctx"]["swings_h1"]["lows"] and pos["ctx"]["session"]
    r1 = pos["entry"] - pos["stop0"]
    tp1, tp2 = pos["tps"][0]["px"], pos["tps"][1]["px"]
    # TP1 → stop to entry; TP2 → trailing at 1R from the best price; then the trailing stop closes the rest
    f.m1 = [[T0, 100, tp1 + 0.01, 100, tp1], [T0 + 60_000, tp1, tp2 + 0.01, tp1, tp2], [T0 + 120_000, tp2, tp2 + 1, tp2, tp2 + 1],
            [T0 + 180_000, tp2 + 1, tp2 + 1, tp2 + 1 - r1 - 0.01, tp2]]
    clk["t"] = T0 + 240_000
    d.tick()
    v = d.view()
    assert v["open"] == [] and len(v["closed"]) == 1
    c = v["closed"][0]
    kinds = [x["kind"] for x in c["fills"]]
    assert kinds == ["open", "tp", "tp", "stop"] and c["why"] == "трейлинг-стоп" and c["pnl_usd"] > 0 and c["r_mult"] > 1
    assert v["stats"]["trades"] == 1 and v["stats"]["win_pct"] == 100.0 and v["real_account"] is False


def test_a_candle_touching_stop_and_tp_takes_the_stop_and_short_mirror(tmp_path):
    d, f, clk = make(tmp_path)
    code, v = d.open({"sym": "ADA", "side": "short", "confirm": True})
    pos = v["open"][0]
    assert pos["stop"] > pos["entry"] and "максимумом H1 106" in pos["stop_why"]
    f.m1 = [[T0, 100, pos["stop"] + 0.1, pos["tps"][0]["px"] - 0.1, 100]]
    clk["t"] = T0 + 60_000
    d.tick()
    c = d.view()["closed"][0]
    assert c["why"] == "стоп" and c["r_mult"] == pytest.approx(-1.0, abs=0.05) and c["pnl_usd"] < 0


def test_settings_close_move_stop_and_the_16_pct_cap(tmp_path):
    d, f, clk = make(tmp_path)
    code, v = d.set_settings({"risk_pct": 50, "stop_rule": "pct", "stop_pct": 30, "max_lev": 9, "tp_r": [1.5, 3], "tp_split": [1, 1]})
    s = v["settings"]
    assert s["risk_pct"] == 10.0 and s["stop_pct"] == 16.0 and s["max_lev"] == 5.0 and s["tp_r"] == [1.5, 3.0] and s["tp_split"] == [50.0, 50.0]
    code, p = d.preview({"sym": "ADA", "side": "long"})
    assert p["stop_dist_pct"] == pytest.approx(16.0, abs=0.01) and p["lev"] <= 5.0
    code, v = d.open({"sym": "ADA", "side": "long", "confirm": True})
    pid = v["open"][0]["id"]
    assert d.move_stop(pid, 101)[0] == 400                                      # above the price for a long → refused
    assert d.move_stop(pid, 95)[1]["open"][0]["stop"] == 95
    code, v = d.close(pid)
    assert code == 200 and v["closed"][0]["why"] == "закрыто вручную" and v["open"] == []


def test_paper_only_owner_only_and_the_panel():
    src = open(os.path.join(ROOT, "cnq", "api", "manual_desk.py"), encoding="utf-8").read()
    assert "cnq.execution" not in src and "/trade/" not in src and "/api/v5/trade" not in src and "api_key" not in src.lower()
    srv = open(os.path.join(ROOT, "cnq", "api", "server.py"), encoding="utf-8").read()
    j = srv.index('if path == "/v2/admin/manual" or path.startswith("/v2/admin/manual/"):')
    assert srv.index("deny = self.console.check(headers)", j) < srv.index("self._manual(", j) and srv.count("self._manual(") == 1
    adm = open(os.path.join(ROOT, "web", "admin.html"), encoding="utf-8").read()
    assert "Ручной стол (Дмитрий)" in adm and '<script src="manual_desk.js?v=2"></script>' in adm and "mdCall('/open',{sym,side,confirm:true})" in adm
    for f in ("app.html", "vip.html", "index.html"):
        assert "/v2/admin/manual" not in open(os.path.join(ROOT, "web", f), encoding="utf-8").read(), f
    node = shutil.which("node")
    if not node:
        return
    D = {"label_ru": "ТЕСТ", "settings": MD.DEFAULTS, "start": 10000, "equity": 10012, "open": [{"id": "p1", "sym": "ADA", "side": "long", "t": T0,
         "lev": 1.2, "entry": 100, "mark": 101, "stop": 97, "trail": False, "tps": [{"px": 103, "done": True}, {"px": 106, "done": False}], "upnl": 12, "realised": 0, "fees": 1}],
         "closed": [{"sym": "BTC", "side": "short", "t": T0, "closed_t": T0, "why": "стоп", "pnl_usd": -300, "r_mult": -1, "system": {"direction": "вверх"}}],
         "stats": {"trades": 1, "win_pct": 0, "avg_r": -1, "pnl_usd": -300, "max_dd_pct": 3}, "stage_ru": "ЭТАП"}
    P = {"sym": "ADA", "side": "long", "entry": 100.05, "last": 100, "stop": 96.9, "stop_dist_pct": 3.1, "stop_why": "за ближайшим минимумом H1 97",
         "notional": 9600, "lev": 0.96, "risk_usd": 300, "equity": 10000, "fees_est": 9.6, "tps": [{"r": 1, "px": 103, "pct": 40}], "settings": MD.DEFAULTS}
    p = os.path.join(tempfile.mkdtemp(), "m.js")
    open(p, "w", encoding="utf-8").write(f"global.window={{}};require({json.dumps(os.path.join(ROOT, 'web', 'manual_desk.js'))});"
                                        f"process.stdout.write(window.CNManual.html({json.dumps(D, ensure_ascii=False)},{{preview:{json.dumps(P, ensure_ascii=False)}}}))")
    h = subprocess.run([node, p], capture_output=True, text=True, timeout=30).stdout
    for w in ("LONG", "SHORT", "Подтвердить", "за ближайшим минимумом H1 97", "TP1", "Настройки", "Закрыть", "Стоп…", "Что говорила система", "вверх", "ЭТАП", "✓TP1"):
        assert w in h, w


# ---------------------------------------------------------------------------------------------- stage 2 (owner 19.11 23:13)
from cnq.api import manual_market as MM


class Fake2(Fake):
    def __call__(self, path, **q):
        now = T0
        if path.endswith("/instruments"):
            return [{"instId": "ADA-USDT-SWAP", "state": "live", "ctVal": "10"}, {"instId": "BTC-USDT-SWAP", "state": "live", "ctVal": "0.01"}]
        if path.endswith("/books"):                                   # mid 100; contracts of 10 ADA
            return [{"bids": [["99.9", "100"], ["99.5", "100"], ["98.5", "100"], ["97", "100"]], "asks": [["100.1", "50"], ["100.9", "50"], ["101.5", "50"], ["103", "50"]]}]
        if "taker-volume" in path:
            return [[str(now - i * H), "1000", str(2000 + i)] for i in range(24)]                   # newest first: ts, sell, buy
        if "long-short" in path:
            return [[str(now), "1.8"], [str(now - H), "1.7"]]
        if "funding-rate" in path:
            return [{"fundingRate": "0.0001", "nextFundingTime": str(now + 3 * H)}]
        if "open-interest-history" in path:
            return [[str(now - i * H), "0", "0", str(1_000_000 * (1 + (24 - i) / 100))] for i in range(25)]
        if "liquidation-orders" in path:
            return [{"details": [{"ts": str(now - H), "bkPx": "95", "sz": "20", "side": "sell", "posSide": "long"},
                                 {"ts": str(now - H), "bkPx": "95", "sz": "1", "side": "sell", "posSide": "long"},
                                 {"ts": str(now - 2 * 86_400_000), "bkPx": "95", "sz": "50", "side": "buy", "posSide": "short"}]}]
        if path.endswith("/ticker"):
            return [{"last": str(self.last[q["instId"]]), "volCcy24h": "30000"}]
        if path.endswith("/candles") and q.get("bar") == "1D" and q.get("limit") == "9":
            return [[str(now - i * 86_400_000), "100", "101", "99", "100", "0", "0", "2000000", "1" if i else "0"] for i in range(9)]
        return super().__call__(path, **q)


def make2(tmp_path):
    d, _f, clk = make(tmp_path)
    f = Fake2()
    d.get = f
    web = tmp_path / "web"
    (web / "decision.json").write_text(json.dumps({"news": {"impacts": [
        {"asset": "ADA", "title": "ADA news", "magnitude": 3, "direction": "bull", "created": T0 - H, "source": "x", "link": "https://x.io/a"},
        {"asset": "ADA", "title": "old", "magnitude": 5, "created": T0 - 3 * 86_400_000},
        {"asset": "MARKET", "title": "Fed", "magnitude": 4, "direction": "bear", "created": T0 - 2 * H}]}}))
    (web / "calendar.json").write_text(json.dumps({"events": [{"time": T0 + 5 * H, "name": "CPI", "importance": 3}, {"time": T0 + 4 * 86_400_000, "name": "far"}]}))
    return d, f, clk, MM.Market(d, str(web))


def test_flow_side_and_candles_come_from_okx_public_data(tmp_path):
    d, f, clk, m = make2(tmp_path)
    code, F = m.flow({"sym": "Cardano"})
    D1, D2 = F["depth"]["1pct"], F["depth"]["2pct"]
    assert code == 200 and D1["bid_usd"] == round(99.9 * 1000 + 99.5 * 1000) and D1["ask_usd"] == round(100.1 * 500 + 100.9 * 500)
    assert D2["bid_usd"] == round(99.9 * 1000 + 99.5 * 1000 + 98.5 * 1000) and D2["complete"] and D1["imbalance"] > 0
    assert F["taker"]["h1"] == {"buy_usd": 2000, "sell_usd": 1000} and F["taker"]["h24"]["sell_usd"] == 24000
    assert F["long_short"]["ratio"] == 1.8 and F["funding"]["rate_pct"] == 0.01 and F["oi"]["chg_24h_pct"] == round((1.24 / 1.0 - 1) * 100, 2)
    code, S = m.side({"sym": "ADA"})
    assert S["volume"]["usd_24h"] == 3_000_000 and S["volume"]["avg_7d_usd"] == 2_000_000 and S["volume"]["vs_avg_pct"] == 50.0
    assert [x["usd"] for x in S["liquidations"]] == [round(95 * 20 * 10)] and S["liquidations"][0]["who"] == "лонг"
    assert [x["title"] for x in S["news"]["coin"]] == ["ADA news"] and S["news"]["market"][0]["title"] == "Fed"
    assert [e["name"] for e in S["calendar"]] == ["CPI"] and S["facts"]["name"] == "Cardano"
    code, C = m.candles({"sym": "ADA", "bar": "4h"})
    kinds = {x["kind"] for x in C["levels"]}
    assert code == 200 and C["bar"] == "4H" and {"day", "week", "round"} <= kinds
    assert any(x["px"] == 97.0 and "мин. недели" in x["label"] and "дно H1" in x["label"] for x in C["levels"])          # one line, both names
    assert m.flow({"sym": "NOPE"})[0] == 404


def test_own_levels_and_take_profits_by_levels(tmp_path):
    d, f, clk, m = make2(tmp_path)
    code, r = d.level({"sym": "ADA", "px": 103.5})
    assert code == 200 and r["own"][0]["px"] == 103.5 and m.candles({"sym": "ADA"})[1]["own"][0]["px"] == 103.5
    lid = r["own"][0]["id"]
    d.set_settings({"tp_rule": "levels"})
    code, p = d.preview({"sym": "ADA", "side": "long"})
    whys = [t.get("why") for t in p["tps"]]
    assert code == 200 and len(p["tps"]) == 3 and "свой уровень" in whys
    pxs = [t["px"] for t in p["tps"]]
    r1 = p["entry"] - p["stop"]
    assert pxs == sorted(pxs) and all(x - p["entry"] >= 0.8 * r1 - 1e-9 for x in pxs)
    assert d.level({"sym": "ADA", "remove": lid})[1]["own"] == []
    # the rule itself: levels first, R where none
    got = MM.tps_by_levels("short", 100, 102, [{"px": 99.5, "label": "a"}, {"px": 98, "label": "b"}, {"px": 97.9, "label": "c"}], 3, [1, 2, 3])
    assert got == [(98, "b"), (97.9, "c"), (96, "2R (уровня нет)")]                     # the first R beyond the last level


def test_the_screen_renders_three_columns_and_the_chart_is_local():
    js = open(os.path.join(ROOT, "web", "manual_desk.js"), encoding="utf-8").read()
    adm = open(os.path.join(ROOT, "web", "admin.html"), encoding="utf-8").read()
    assert "https://" not in js.split("function sideHtml")[0] and "<script src=\"https://" not in adm          # no external chart library
    assert "script-src 'self' 'unsafe-inline'" in adm                                                      # the CSP was not widened
    assert "candles:(sym,bar)=>mdCall('/candles',{sym,bar}),flow:sym=>mdCall('/flow',{sym}),side:sym=>mdCall('/side',{sym}),level:f=>mdCall('/level',f)" in adm
    node = shutil.which("node")
    if not node:
        return
    p = os.path.join(tempfile.mkdtemp(), "s.js")
    F = {"depth": {"1pct": {"bid_usd": 200000, "ask_usd": 100000, "imbalance": 33.3, "complete": True}, "2pct": {"bid_usd": 300000, "ask_usd": 150000, "complete": False}},
         "taker": {"h1": {"buy_usd": 2000, "sell_usd": 1000}, "h24": {"buy_usd": 50000, "sell_usd": 24000}, "h24_hours": 24},
         "long_short": {"ratio": 1.8}, "funding": {"rate_pct": 0.01}, "oi": {"usd": 1240000, "chg_24h_pct": 24, "hours": 24}}
    S = {"sym": "ADA", "volume": {"usd_24h": 3e6, "avg_7d_usd": 2e6, "vs_avg_pct": 50, "days": 7}, "liquidations": [{"t": T0, "px": 95, "usd": 19000, "who": "лонг"}],
         "facts": {"name": "Cardano"}, "news": {"coin": [{"title": "ADA news", "direction": "bull", "magnitude": 3, "created": T0, "source": "x"}], "market": []},
         "calendar": [{"time": T0, "name": "CPI", "importance": 3}]}
    open(p, "w", encoding="utf-8").write(f"global.window={{}};require({json.dumps(os.path.join(ROOT, 'web', 'manual_desk.js'))});const M=window.CNManual;"
        f"M._scr.sym='ADA';M._scr.f={json.dumps(F)};M._scr.s={json.dumps(S, ensure_ascii=False)};M._scr.c={{candles:[],levels:[],own:[{{id:'a',px:103.5}}],last:100}};"
        f"process.stdout.write(M.html({{settings:{{}},open:[],closed:[],stats:{{}}}},{{sym:'ADA'}}))")
    h = subprocess.run([node, p], capture_output=True, text=True, timeout=30).stdout
    for w in ("Покупатели против продавцов", "Стакан ±1%", "стакан OKX глубже не отдаёт", "Лонг / шорт", "Фандинг", "Открытый интерес", "Объём", "+50,0%",
              "Крупные ликвидации", "Новости ADA", "ADA news", "Календарь, 48 ч", "CPI", 'data-md="chart"', 'data-bar="4h"', "103,5000", 'data-md="lvdel"'):
        assert w in h, w
