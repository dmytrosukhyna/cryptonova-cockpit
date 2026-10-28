import json
import os
import sqlite3

import pytest

from cnq.agents import app_home, panel_header as ph


def _w(d, name, obj):
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        json.dump(obj, f)


def _main(d, key):
    """28.10: the main futures desk is chosen by web/desk_select.json — a fixture says which one instead of relying on a default"""
    _w(d, "desk_select.json", {"current": key})


@pytest.mark.parametrize("main", [None, "cons", "bal", "aggr"])
def test_app_home_numbers(tmp_path, main):
    d = str(tmp_path)
    t0 = 1792300000000
    key = main or ph.NOVA_BASE[0]                              # 28.10: no desk_select.json → the default main desk
    hist = lambda k: [{"time": t0, "equity": 10000.0}, {"time": t0 + 60000, "equity": 10000.0 + 100 * k}]
    ks = {p: (1.0 if p == key else 0.5 + 0.5 * i) for i, p in enumerate(("cons", "bal", "aggr"))}
    prof = {p: {"equity": 10000.0, "open_pnl_usd": 100.0 * k, "equity_history": hist(k), "open": [], "closed": [], "stats": {}}
            for p, k in ks.items()}
    # 22.10: the open P&L is the panel's futEq — (price − entry) × qty_left + the realised part, like every desk row stores it
    prof[key]["open"] = [{"symbol": "ARB", "side": "LONG", "entry": 0.2, "price": 0.22, "stop": 0.18, "tps": [0.3],
                          "qty_left": 5000.0, "pnl_usd": 0.0, "open_pnl_usd": 100.0, "kind": "CLIMAX_LONG"}]
    _w(d, "paper.json", {"since": t0, "profiles": prof})
    if main:
        _main(d, main)
    _w(d, "app_portfolio.json", {"index": 99.0, "cash_pct": 15, "history": [{"t": t0, "v": 100.0}, {"t": t0 + 60000, "v": 99.0}],
                                 "positions": [{"symbol": "BTC", "weight": 0.5, "entry": 100, "price": 110, "kind_ru": "ядро"},
                                               {"symbol": "PENGU", "weight": 0.35, "entry": 1, "price": 0.9, "kind_ru": "мем"}],
                                 "recent_actions": [{"time": t0, "symbol": "SOL", "action": "TRIM", "why_ru": "лишнее продали"}]})
    _w(d, "markets.json", {"top": [{"symbol": "BTC", "price": 81000, "chg24": 1.234}], "fng": {"value": 59, "label": "жадность"},
                           "btc_bull": True})
    doc = app_home.run(sqlite3.connect(":memory:"), d, now=t0 + 120000)
    assert doc["futures"]["usd"] == 100.0                      # only the main desk (+$100); the other two sizes of it are not counted
    assert doc["spot"]["usd"] == -100.0                        # index 99 → −1 % of $10 000
    assert doc["total"]["usd"] == 0.0
    assert doc["futures"]["open"][0]["pct"] == 10.0 and doc["futures"]["open"][0]["target"] == 0.3
    assert doc["market"]["regime"] == "рост" and doc["market"]["btc"]["chg24"] == 1.23
    names = [g["name"] for g in doc["spot"]["alloc"]]
    assert names[0] == "Биткоин" and "мем-монеты" in names and names[-1] == "доллары"
    assert doc["spot"]["movers"][0] == {"sym": "BTC", "pct": 10.0}
    assert os.path.exists(os.path.join(d, "app_home.json"))


def test_app_home_shows_every_mix_source(tmp_path):
    """20.10: the own daily signals were open but the app showed only the main desk"""
    d = str(tmp_path)
    t0 = 1792300000000
    prof = {p: {"equity": 10000.0, "open_pnl_usd": 0.0, "equity_history": [{"time": t0, "equity": 10000.0}], "open": [],
                "closed": [{"symbol": "ARB", "side": "LONG", "pnl_usd": 191.6, "closed": t0}], "stats": {"trades": 1}}
            for p in ("cons", "bal", "aggr")}
    prof["own"] = {"equity": 10000.0, "open": [{"id": "OWN-P-AVAX-1", "symbol": "AVAX", "side": "LONG", "grade": "P", "kind": "OWN_P",
                                                "entry": 10.0, "stop": 8.5, "price": 10.5, "open_pnl_usd": 12.0, "opened": t0 + 5,
                                                "vol": 1.8}]}
    _w(d, "paper.json", {"since": t0, "profiles": prof})
    doc = app_home.run(sqlite3.connect(":memory:"), d, now=t0 + 120000)
    f = doc["futures"]
    assert len(f["open"]) == 1 and f["open"][0]["src"] == "own" and f["open"][0]["src_ru"] == "откат в тренде"
    assert f["open"][0]["target"] == 14.5 and f["open"][0]["pct"] == 5.0 and "×1,8" in f["open"][0]["why"]
    assert f["trades"] == 1                                          # the main account's count, not the other desks'
    assert "ARB" in f["status_ru"] and "1 сделка других" in f["status_ru"]


EXPLAIN_KEYS = {"why_title", "checks", "history", "risk", "work", "note"}
HIST_KEYS = {"n", "win_pct", "avg_pct", "period", "years_plus", "text", "src", "live"}
RISK_KEYS = {"stop_pct", "target_pct", "rr", "lev", "risk_usd", "risk_pct", "hold"}


def _shape(row):
    assert EXPLAIN_KEYS <= set(row)
    for c in row["checks"] or []:
        assert set(c) == {"t", "v", "ok"} and isinstance(c["t"], str) and isinstance(c["v"], str) and c["ok"] is True
    if row["history"]:
        assert set(row["history"]) == HIST_KEYS
    if row["risk"]:
        assert set(row["risk"]) == RISK_KEYS and row["risk"]["stop_pct"] < 0


def _daily(closes, start, last_vol=1000.0):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append([start + i * 86_400_000, prev, max(prev, c) * 1.01, min(prev, c) * 0.99, c, last_vol if i == len(closes) - 1 else 1000.0])
        prev = c
    return out


def test_own_signal_explains_itself_with_the_entry_numbers(tmp_path):
    """20.10 (owner: «более серьёзное объяснение, почему мы вошли в сделку»): a new own-desk trade stores what the rule saw at
    entry and the day's scan counters; the app row shows them with this trade's numbers"""
    from cnq.agents import own_desk as od
    d, DAY = str(tmp_path), od.DAY
    D0 = 1_790_000_000_000 // DAY * DAY
    up = [100 * 1.004 ** i for i in range(260)]
    btc, eth = up[:200], up[:199] + [up[198] * 1.06]          # ETH: a 20-day breakout on the last closed day

    def daily(sym):
        src = {"BTC": btc, "ETH": eth}.get(sym)
        if src is None:
            raise RuntimeError("no data")
        rows = _daily(src, D0, last_vol=3000.0)                 # the signal day on ×3 volume
        rows.append([D0 + 200 * DAY, rows[-1][4], rows[-1][4], rows[-1][4], rows[-1][4], 10.0])
        return rows
    _w(d, "paper.json", {"profiles": {}})
    conn = sqlite3.connect(":memory:")
    now = D0 + 200 * DAY + 10 * 60_000
    od.run(conn, out=d, now=now, daily=daily, prices=lambda: {})
    doc = app_home.run(conn, d, now=now + 60_000)
    f = doc["futures"]
    assert f["scan"]["coins"] == 2 and f["scan"]["checks"] == od.N_CONDITIONS == 8
    assert f["scan"]["signals"] == 1 and f["scan"]["passed"] == 1 and f["scan"]["opened"] == 1
    assert f["scan"]["text"].startswith("Сегодня система проверила 2 монеты по 8 условиям")
    row = f["open"][0]
    _shape(row)
    assert row["sym"] == "ETH" and row["why_title"] == "Пробой максимума 20 дней" and row["note"] is None
    checks = {c["t"]: c["v"] for c in row["checks"]}
    assert list(checks) == ["Пробой", "Тренд", "Рынок", "Объём", "Ликвидность"]
    assert "выше максимума 20 дней" in checks["Пробой"] and "режим роста" in checks["Рынок"]
    stored = json.loads(conn.execute("SELECT facts FROM own_trades").fetchone()[0])
    assert stored["vol"] > 1.5 and stored["rank"] in (1, 2) and stored["of"] == 2 and stored["btc"]["sma200"] > 0
    assert checks["Объём"] == f"×{stored['vol']:.1f} к среднему за 30 дней (нужно от ×1.5)"
    assert checks["Ликвидность"] == f"№{stored['rank']} из 2 самых торгуемых монет за 30 дней"
    h = row["history"]
    assert (h["n"], h["win_pct"], h["avg_pct"]) == (403, 53, 6.3) and h["live"]["n"] == 0
    r = row["risk"]
    assert -15.0 <= r["stop_pct"] <= -3.0 and r["rr"] == 3.0 and r["lev"] == 2 and 49 <= r["risk_usd"] <= 51
    assert r["target_pct"] == pytest.approx(-3 * r["stop_pct"], abs=0.2) and "14 дней" in r["hold"]     # the +3R step
    assert row["work"] == "Сегодня система проверила 2 монеты по 8 условиям: правила дали 1 сигнал, все условия прошли 1."


def test_old_own_trade_gets_facts_from_kept_closes_only(tmp_path):
    """a trade from before 20.10: trend / pullback / market recomputed from the closes the desk kept, up to the signal day;
    no volume, no rank, no scan sentence — and nothing at all when the closes are missing"""
    from cnq.agents import own_desk as od
    d, DAY = str(tmp_path), od.DAY
    conn = sqlite3.connect(":memory:")
    od.ensure(conn)
    day = 1_790_000_000_000 // DAY * DAY
    sig = day - DAY
    up = [100 * 1.004 ** i for i in range(260)]
    coin = up[:198] + [up[197] * 0.985, up[197] * 0.97]        # two down days in an uptrend → RSI(2) 0
    rows = [("AVAX", sig - (len(coin) - 1 - i) * DAY, c) for i, c in enumerate(coin)]
    rows += [("BTC", sig - (259 - i) * DAY, c) for i, c in enumerate(up)]
    rows += [("AVAX", day, 1.0)]                                # a later day must not leak in
    conn.executemany("INSERT INTO own_closes VALUES(?,?,?)", rows)
    for sym in ("AVAX", "SEI"):
        conn.execute("INSERT INTO own_trades(id, symbol, rule, side, opened, opened_day, entry, stop, qty, best) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (f"OWN-P-{sym}-1", sym, "P", "LONG", day + 300_000, day, 10.0, 8.5, 33.3, 10.0))
    _w(d, "paper.json", {"profiles": {"own": od.doc(conn, day + 600_000, {"AVAX": 10.2, "SEI": 9.9}, [])}})
    rows = {o["sym"]: o for o in app_home.run(conn, d, now=day + 600_000)["futures"]["open"]}
    a, s = rows["AVAX"], rows["SEI"]
    _shape(a)
    _shape(s)
    assert [c["t"] for c in a["checks"]] == ["Тренд", "Тренд растёт", "Откат", "Рынок"]
    assert "RSI(2) = 0" in a["checks"][2]["v"] and "−3.0% за 2 дня" in a["checks"][2]["v"]
    assert a["work"] is None and "первой версии правил" in a["note"]
    f = json.loads(conn.execute("SELECT facts FROM own_trades WHERE symbol='AVAX'").fetchone()[0])
    assert f["src"] == "closes" and "vol" not in f and "rank" not in f and f["close"] == pytest.approx(coin[-1], rel=1e-5)
    assert s["checks"] is None and s["work"] is None                          # no closes kept for SEI → nothing invented
    assert s["why_title"] == "Откат в растущем тренде" and s["history"]["n"] is None and s["history"]["avg_pct"] == 3.1
    assert s["risk"]["stop_pct"] == -15.0 and s["risk"]["risk_usd"] == 50


def test_main_strategy_panic_buy_explains_itself(tmp_path):
    """the main strategy (bal): the panic candle's numbers, the breadth, the scan at entry, the rule's history"""
    from cnq.agents import paper_desk as pd
    d = str(tmp_path)
    conn = sqlite3.connect(":memory:")
    t0 = 1_700_000_000_000
    cs = [[t0 + i * 900_000, 10.0, 10.04, 9.96, 10.0, 100.0] for i in range(301)]
    cs[-2] = [cs[-2][0], 10.0, 10.0, 9.2, 9.6, 500.0]           # −4 % on ×5 volume, long lower wick
    cs[-1] = [cs[-1][0], 9.6, 9.65, 9.55, 9.6, 100.0]
    res = pd.PaperDesk(conn, "bal").step([{"symbol": "SOL-USDT", "candles": cs, "levels": [], "regime": {"long": "BULL"}}], True, cs[-1][0] + 1)
    assert res["open"][0]["grade"] == "E" and res["open"][0]["facts"]["scan"] == {"coins": 1, "rules": 4, "fired": 1}
    _w(d, "paper.json", {"profiles": {"bal": res}})
    _main(d, "bal")
    row = app_home.run(conn, d, now=cs[-1][0] + 60_000)["futures"]["open"][0]
    _shape(row)
    assert row["why_title"] == "Откуп паники на большом объёме"
    assert [c["v"] for c in row["checks"]] == ["за сутки упали больше чем на 3%: 100% монет (нужно от 40%)",
                                                "−4.0% за 15 минут (нужно −1.5% и ниже)", "×5.0 к среднему за сутки (нужно от ×2)",
                                                "одна из 1 основных монет, на которых правило проверено на истории"]
    h = row["history"]
    assert (h["n"], h["win_pct"], h["avg_pct"], h["period"]) == (3414, 60, 1.26, "2023–2026") and h["live"]["n"] == 0
    r = row["risk"]
    assert r["stop_pct"] == -12.0 and r["target_pct"] == 36.1 and r["rr"] == 3.0 and r["lev"] == 3   # entry carries 0.02 % slippage
    assert 238 <= r["risk_usd"] <= 242 and r["hold"].startswith("24 часа")
    assert row["target"] == pytest.approx(row["entry"] * 1.36, rel=1e-3)      # the Telegram ladder, not the desk's +50 % placeholder
    assert row["work"] == "В момент входа система проверяла 1 монету по 4 правилам — «откуп паники» сработал у 1 из них."


def test_missing_data_is_never_filled_in(tmp_path):
    """rows without stored facts or size: only what the row itself backs; research numbers the reports lack stay null"""
    d = str(tmp_path)
    t0 = 1792300000000
    prof = {"bal": {"open": [{"symbol": "ARB", "side": "LONG", "grade": "E", "kind": "CLIMAX_LONG", "entry": 0.2, "stop": 0.176,
                              "price": 0.21, "tps": [0.3] * 3, "opened": t0}], "stats": {"by_grade": {"E": {"n": 7, "winrate": 0.714}}}},
            "imp": {"open": [{"symbol": "SUI", "side": "LONG", "grade": "K", "entry": 2.0, "stop": 1.8, "price": 2.1, "qty_left": 1000,
                              "opened": t0 + 1, "facts": {"btc_ret": 0.027, "need_btc": 0.025, "rank": 2, "slots": 5,
                                                          "scan": {"coins": 60, "rules": 1, "fired": 60}}}]},
            "lst": {"open": [{"symbol": "NEWX", "side": "SHORT", "grade": "LST", "entry": 1.0, "stop": 1.3, "price": 0.9, "qty": 500,
                              "opened": t0 + 2, "facts": {"age_h": 4.5, "move": 0.03, "skip_pump": 0.1, "min_qvol": 2e6}}],
                    "stats": {"trades": 0, "winrate": None}}}
    _w(d, "paper.json", {"profiles": prof})
    _main(d, "bal")
    rows = {o["sym"]: o for o in app_home.run(sqlite3.connect(":memory:"), d, now=t0 + 60_000)["futures"]["open"]}
    for r in rows.values():
        _shape(r)
    e, k, lst = rows["ARB"], rows["SUI"], rows["NEWX"]
    assert e["checks"] is None and e["work"] is None and e["risk"]["risk_usd"] is None and e["risk"]["stop_pct"] == -12.0
    assert e["history"]["live"] == {"n": 7, "wins": 5, "avg_pct": None, "since": None} and e["why"]
    assert [c["t"] for c in k["checks"]] == ["Рывок BTC", "Ликвидность"] and k["checks"][0]["v"] == "+2.7% за час (нужно от +2.5%)"
    assert k["history"]["n"] is None and k["history"]["win_pct"] is None and k["history"]["avg_pct"] is None
    assert k["risk"]["risk_usd"] == 200 and k["work"].startswith("В момент входа система проверяла 60 альткоинов")
    assert [c["t"] for c in lst["checks"]] == ["Новый листинг", "Без пампа", "Токен"]           # no turnover kept → no «Оборот»
    assert lst["risk"]["stop_pct"] == -30.0 and lst["risk"]["risk_usd"] == 150 and lst["work"] is None
    assert lst["history"]["n"] == 729 and lst["history"]["live"]["n"] == 0
    assert app_home.run(sqlite3.connect(":memory:"), d, now=t0 + 60_000)["futures"]["scan"] is None      # no daily scan recorded


def test_app_home_config_link(tmp_path):
    d = str(tmp_path)
    _w(d, "app_config.json", {"exchange_name": "BingX", "exchange_ref": "https://bingx.com/invite/ABC123", "exchange_trade_url": "x"})
    doc = app_home.run(sqlite3.connect(":memory:"), d, now=1792300000000)
    assert doc["config"]["exchange_ref"] == "https://bingx.com/invite/ABC123"
    _w(d, "app_config.json", {"exchange_ref": "javascript:alert(1)", "okx_ref": "javascript:x"})
    cfg = app_home.run(sqlite3.connect(":memory:"), d, now=1792300000000)["config"]
    assert cfg["exchange_ref"] is None and cfg["okx_ref"] is None
    _w(d, "app_config.json", {"okx_ref": "https://www.okx.com/eu/join/X", "bingx_barred": ["us", "GB", "xyz", "ca"]})
    cfg = app_home.run(sqlite3.connect(":memory:"), d, now=1792300000000)["config"]
    assert cfg["okx_ref"] == "https://www.okx.com/eu/join/X" and cfg["bingx_barred"] == ["CA", "GB", "US"]
    # build 44 «Подключить»: copy pages and the exchanges' pages pass only as https; deep links only as scheme://
    _w(d, "app_config.json", {"okx_copy_url": "https://www.okx.com/en-eu/copy-trading/account/X", "bingx_copy_url": "",
                              "bingx_kyc_url": "http://bingx.com/account/kyc/", "okx_kyc_url": " https://www.okx.com/en-eu/account/kyc ",
                              "app_links": {"okx_copy_url": "okx://copy", "bingx_kyc_url": "javascript:alert(1)", "x": "https://a.b"}})
    cfg = app_home.run(sqlite3.connect(":memory:"), d, now=1792300000000)["config"]
    assert cfg["okx_copy_url"] == "https://www.okx.com/en-eu/copy-trading/account/X" and cfg["bingx_copy_url"] is None
    assert cfg["bingx_kyc_url"] is None and cfg["okx_kyc_url"] == "https://www.okx.com/en-eu/account/kyc"
    assert cfg["app_links"] == {"okx_copy_url": "okx://copy"} and cfg["okx_deposit_url"] is None


def test_app_config_okx_regions(tmp_path):
    # build 44: OKX for everyone by default; okx_barred / okx_regions pass through cleaned (the app decides UA/GR/RU/US by them)
    d = str(tmp_path)
    _w(d, "app_config.json", {})
    cfg = app_home.run(sqlite3.connect(":memory:"), d, now=1792300000000)["config"]
    assert cfg["okx_regions"] == "ALL" and cfg["okx_barred"] is None
    _w(d, "app_config.json", {"okx_regions": ["gr", "ES", "xyz"], "okx_barred": ["us", "CA", "Crimea"]})
    cfg = app_home.run(sqlite3.connect(":memory:"), d, now=1792300000000)["config"]
    assert cfg["okx_regions"] == ["ES", "GR"] and cfg["okx_barred"] == ["CA", "US"]
    _w(d, "app_config.json", {"okx_copy_barred": ["gr", "DE", "x"], "okx_signal_url": "https://www.okx.com/x/signal", "okx_regions": "ALL"})
    cfg = app_home.run(sqlite3.connect(":memory:"), d, now=1792300000000)["config"]
    assert cfg["okx_copy_barred"] == ["DE", "GR"] and cfg["okx_signal_url"] == "https://www.okx.com/x/signal"
    _w(d, "app_config.json", {"okx_regions": "all", "okx_barred": "US"})
    cfg = app_home.run(sqlite3.connect(":memory:"), d, now=1792300000000)["config"]
    assert cfg["okx_regions"] == "ALL" and cfg["okx_barred"] is None


def test_app_config_in_git_is_valid():
    # the real web/app_config.json: every connect link is https (or empty) and the copy pages stay empty until approved
    here = os.path.join(os.path.dirname(__file__), "..", "web", "app_config.json")
    c = json.load(open(here, encoding="utf-8"))
    for k in app_home.CONNECT_KEYS:
        assert c.get(k) is None or str(c[k]).startswith("https://"), k
    # OKX is the main exchange (owner 22.10): every country unless OKX serves no one there; UA, GR, RU → OKX, US → not OKX
    assert c["okx_regions"] == "ALL"
    assert "US" in c["okx_barred"] and not {"UA", "GR", "RU"} & set(c["okx_barred"])
    assert "US" in c["bingx_barred"] and not {"UA", "RU"} & set(c["bingx_barred"])
    # final QA 28.10 (owner): the copy button is shown everywhere — one calm «i» note about regions instead of a country list
    assert c["okx_copy_barred"] == [] and c.get("okx_signal_url") is None


def test_spot_coins_explain_themselves_in_russian_only(tmp_path):
    # 21.10 owner's app spec §8: «Почему этот актив в портфеле?» — numbers in every language, the engine's Russian
    # explanations only in the Russian feed (other languages never show Russian text)
    w = tmp_path
    (w / "app_portfolio.json").write_text(json.dumps({"index": 103, "cash_pct": 10, "positions": [
        {"symbol": "ZEC", "name": "Zcash", "weight": 0.08, "entry": 1150.0, "price": 1260.0, "kind_ru": "крупный альт", "horizon_ru": "1–3 месяца"}]}))
    (w / "portfolio.json").write_text(json.dumps({"portfolios": {"ideas": {"positions": [
        {"symbol": "ZEC", "reason": "План: ZEC 8% счёта.", "table_status": "buy", "table_status_ru": "можно брать",
         "table_why_ru": "Анкета 51, консультант 99, тренд вверх, BTC бычий — можно брать", "target_weight": 0.075,
         "levels": {"stop": 920.7}, "reco": {"t": "Держать", "why": "Стоп −20% от входа, продаём и при «не брать»"}}]}}}))
    (w / "tokenomics.json").write_text(json.dumps({"tokens": {}, "unlocks_next": {"ZEC": {"date": "2026-11-02", "pct_circ": 1.5}}}))
    (w / "coin_profiles.json").write_text(json.dumps({"coins": {"ZEC": {"what_ru": "Приватные платежи.", "risks_ru": "Регуляторы ЕС."}}}))
    ru = app_home.run(None, str(w))
    c = ru["spot"]["coins"][0]
    assert c["sym"] == "ZEC" and c["weight"] == 8.0 and c["stop"] == 920.7 and abs(c["pct"] - 9.6) < 0.1
    # 28.10 (build 46 QA): plain words — not the engine's «План: ZEC 8% счёта», «анкета 51 … — можно брать»
    assert c["why_ru"].startswith("ZEC прошла отбор системы: оценка проекта 51 из 100, оценка консультанта 99 из 100.")
    assert "7,5%" in c["why_ru"] and "План" not in c["why_ru"] and "анкета" not in c["mark_ru"].lower()
    assert c["mark_ru"].startswith("Подходит для покупки: оценка проекта 51 из 100") and "можно брать" not in c["mark_ru"]
    assert c["reco_ru"] == "Держать: стоп −20% от покупки; продадим и тогда, когда монета перестанет проходить проверки системы."
    assert "2026-11-02" in c["unlock_ru"] and c["about_ru"] and c["live_share"] is None
    en = json.loads((w / "app_home.en.json").read_text())["spot"]["coins"][0]
    assert en["sym"] == "ZEC" and en["weight"] == 8.0 and not any(k.endswith("_ru") for k in en)
