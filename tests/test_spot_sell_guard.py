"""23.11 (owner): no system spot sells — only a sound swap into a better coin in the same pass; exchange stops stay."""
import json
import os

from cnq.execution import spot_sell_guard as SG

C = {"on": True, "rating_gap": 15, "day_cap_pct": 0.03}
R = {"HYPE": 60, "UNI": 50, "TRX": 77, "BTC": 90, "ETH": 85, "ARB": 40}


def S(ccy, usd, act="sell"):
    return {"ccy": ccy, "usd": usd, "act": act, "sz": "1", "lot": "0.001", "min": "0.001"}


def test_defensive_sells_are_blocked_and_logged():
    kept, notes = SG.apply([S("HYPE", 200)], [], 10_000, C, R, lambda c: True, 0, False)
    assert kept == [] and "HYPE: продажа заблокирована владельцем" in notes[0]
    kept, _ = SG.apply([S("HYPE", 200)], [{"ccy": "UNI", "usd": 200}], 10_000, C, R, lambda c: True, 0, False)
    assert kept == []                                                   # UNI is not better rated
    kept, _ = SG.apply([S("UNI", 200)], [{"ccy": "TRX", "usd": 200}], 10_000, C, R, lambda c: False, 0, False)
    assert kept == []                                                   # TRX falling and BTC not bull


def test_a_sound_swap_is_allowed_within_the_day_cap():
    kept, notes = SG.apply([S("UNI", 500)], [{"ccy": "TRX", "usd": 400}], 10_000, C, R, lambda c: True, 0, False)
    (o,) = kept
    assert o["swap_to"] == "TRX" and o["usd"] == 300 and abs(o["_scale"] - 0.6) < 1e-9 and notes[0].startswith("замена: UNI → TRX")
    kept, notes = SG.apply([S("UNI", 500)], [{"ccy": "TRX", "usd": 400}], 10_000, C, R, lambda c: True, 295, False)
    assert kept == [] and "дневной предел" in notes[0]
    kept, _ = SG.apply([S("BTC", 100, "trim")], [{"ccy": "ETH", "usd": 100}], 10_000, C, R, lambda c: True, 0, True)
    assert kept and kept[0]["swap_to"] == "ETH"                         # core ↔ core: pairing only


def test_live_config_and_hooks():
    import inspect
    from cnq.execution import okx_autopilot as ap
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    c = json.load(open(os.path.join(root, "config", "owner_overrides.json")))[SG.KEY]
    assert c["on"] and c["tp_off"] and c["stops_keep"] and c["rating_gap"] == 15 and c["day_cap_pct"] == 0.03
    assert "SG.apply(" in inspect.getsource(ap.build) and "SG.cfg(" in inspect.getsource(ap.spot_stops)


def test_model_keeps_coins_and_mirrors_only_the_exchange_stop(tmp_path):
    import sqlite3
    from cnq.agents import spot_profiles as sp
    T0 = 1_790_000_000_000
    rows = [{"symbol": "BTC", "name": "Bitcoin", "kind": "core", "status": "buy", "status_ru": "можно брать", "why_ru": "ядро", "price": 80000},
            {"symbol": "AAA", "name": "A", "kind": "mid", "status": "buy", "status_ru": "можно брать", "why_ru": "ок", "price": 10.0}]
    ideas = lambda t: {"asof": t, "available": True, "rows": rows}                                   # noqa: E731
    sup = {"sma200": {"AAA": 9.9}, "walls": {}, "btc": {"price": 80000, "ll20": 70000, "mid": "LONG"}}
    conn = sqlite3.connect(":memory:")
    port = sp.IdeasPortfolio(conn)
    port.step({"cg": {"BTC": 80000, "AAA": 10.0}, "meme": {}, "hl": {}}, ideas(T0), T0, support=sup, nosell={"on": False})
    assert "AAA" in {p["symbol"] for p in port.rp.positions()}
    cwd = os.getcwd()
    os.makedirs(tmp_path / "web")
    json.dump({"actions": []}, open(tmp_path / "web" / "public_real.json", "w"))
    os.chdir(tmp_path)
    try:
        st = port.step({"cg": {"BTC": 80000, "AAA": 7.5}, "meme": {}, "hl": {}}, ideas(T0 + 3_600_000), T0 + 3_600_000, support=sup, nosell=C)
        assert "AAA" in {p["symbol"] for p in port.rp.positions()}                       # −25 %: the model would have sold — blocked
        assert any(a["symbol"] == "AAA" and a["action"] == "SKIP" and "заблокирована владельцем" in a["reason"] for a in st["actions"])
        json.dump({"actions": [{"kind": "spot_stop_fired", "sym": "AAA", "t": T0 + 7_000_000}]}, open(tmp_path / "web" / "public_real.json", "w"))
        port.step({"cg": {"BTC": 80000, "AAA": 7.4}, "meme": {}, "hl": {}}, ideas(T0 + 7_200_000), T0 + 7_200_000, support=sup, nosell=C)
        assert "AAA" not in {p["symbol"] for p in port.rp.positions()}                   # the exchange stop fired → the model sells too
    finally:
        os.chdir(cwd)


def test_model_swaps_only_into_a_better_rising_coin_within_the_day_cap():
    import sqlite3
    from cnq.agents import spot_profiles as sp
    T0 = 1_790_000_000_000
    base = [{"symbol": "BTC", "name": "Bitcoin", "kind": "core", "status": "buy", "status_ru": "", "why_ru": "", "price": 80000, "rating": 78},
            {"symbol": "LOW", "name": "Low", "kind": "mid", "status": "buy", "status_ru": "", "why_ru": "", "price": 10.0, "rating": 40}]
    sup = {"sma200": {"LOW": 9.9, "TOP": 4.9}, "walls": {}, "btc": {"price": 80000, "ll20": 70000, "mid": "LONG"}}
    port = sp.IdeasPortfolio(sqlite3.connect(":memory:"))
    port.step({"cg": {"BTC": 80000, "LOW": 10.0}, "meme": {}, "hl": {}}, {"asof": T0, "available": True, "rows": base}, T0, support=sup)
    rows = [dict(base[0]), {**base[1], "status": "wait"},
            {"symbol": "TOP", "name": "Top", "kind": "mid", "status": "buy", "status_ru": "", "why_ru": "", "price": 5.0, "rating": 70}]
    src = {"cg": {"BTC": 80000, "LOW": 10.0, "TOP": 5.0}, "meme": {}, "hl": {}}
    mv = port.value({"BTC": 80000, "LOW": 10.0}) - port.rp.cash()
    port.rp._set_cash(0.10 / 0.90 * mv)                                            # cash at the floor: no ordinary buy is possible
    st = port.step(src, {"asof": T0 + 3_600_000, "available": True, "rows": rows, "btc_bull": False}, T0 + 3_600_000, support=sup,
                   tradeable={"BTC", "LOW", "TOP"}, nosell={**C, "day_cap_pct": 0.5}, trend_of=lambda c: False)
    assert "TOP" not in {p["symbol"] for p in port.rp.positions()}                 # falling and BTC not bull → no swap
    st = port.step(src, {"asof": T0 + 7_200_000, "available": True, "rows": rows, "btc_bull": False}, T0 + 7_200_000, support=sup,
                   tradeable={"BTC", "LOW", "TOP"}, nosell={**C, "day_cap_pct": 0.5}, trend_of=lambda c: True)
    held = {p["symbol"] for p in port.rp.positions()}
    assert "TOP" in held                                                            # sold LOW (≤ the plan size of TOP) for TOP
    assert any(a["action"] in ("SELL", "TRIM") and a["symbol"] == "LOW" and a["reason"].startswith("Замена: LOW → TOP") for a in st["actions"])
    assert any(a["action"] == "BUY" and a["symbol"] == "TOP" for a in st["actions"])


def test_problem_sells_let_a_non_core_problem_exit_through_and_keep_the_rest_blocked():
    """26.11 (owner «если есть проблемы — продавать»): «problem_sells» — the model's problem exit of a non-core coin is repeated on
    the account; trims, time / trailing exits and the core stay blocked"""
    from cnq.execution import spot_sell_guard as G
    assert G.problem("UNI", "Смарт-мани: кошельки продают UNI — выходим вместе с ними, −8%.")
    assert G.problem("PEPE", "Мемы: стоп −25% — PEPE −26%.") and G.problem("X", "Таблица поставила «не брать»: тонкий оборот")
    assert not G.problem("UNI", "Смарт-мани: 30 дней прошло — выходим по времени, UNI +3%.")
    assert not G.problem("X", "Трейлинг: X на 20% ниже пика") and not G.problem("BTC", "Стоп −30%")
    c = {"on": True, "problem_sells": True}
    sells = [{"ccy": "UNI", "usd": 185.0, "act": "sell", "why": "портфель CRYPTO NOVA: " + G.PROBLEM_MARK + " (решение владельца 26.11): кошельки"},
             {"ccy": "HYPE", "usd": 200.0, "act": "sell", "why": "этой монеты нет в портфеле CRYPTO NOVA"},
             {"ccy": "BTC", "usd": 50.0, "act": "trim", "why": "портфель CRYPTO NOVA: " + G.PROBLEM_MARK}]
    kept, notes = G.apply(sells, [], 11000.0, c, {}, lambda y: False, 0.0, False)
    assert [o["ccy"] for o in kept] == ["UNI"] and "продажа при проблеме" in notes[0]
    kept2, _ = G.apply(sells[:1], [], 11000.0, {"on": True}, {}, lambda y: False, 0.0, False)
    assert kept2 == []                                                     # without the flag: blocked as before
