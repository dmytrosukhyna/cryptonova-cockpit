"""Build 53: the real trades' and coins' plain «why», the calm drawdown line and dates said in words."""
from cnq.agents import app_why as aw
from cnq.agents.i18n import LANGS, T, fields
from cnq.api.tts import say_dates, spoken

DESK_D = {"id": "D-OP-498168", "symbol": "OP", "side": "LONG", "grade": "D", "entry": 0.12135, "stop": 0.10677, "tps": [0.182] * 3,
          "reason": "Дисконт фьючерса: фьючерс торгуется на -0.20% ниже спота (среднее за час) — продавцы фьючерсов перегнули."}
ROW = {"sym": "OP", "side": "LONG", "entry": 0.12067, "stop": 0.10677, "target": 0.18199, "pnl_usd": -7.58, "move_pct": -6.15, "lev": 2.0}
CTX = {"since": 1792669322166, "budget_usd": 1500.0, "closed_grades": [("D", -8.43), ("D", 4.48), ("E", 1.0)]}
CAPS = {"lev": 2, "risk_pct": 1.0}


def test_catalog_complete():
    for k, row in aw.W.items():
        assert set(row) == set(LANGS), k
        assert len({frozenset(fields(v)) for v in row.values()}) == 1, k
        assert k in T


def test_trade_why_d_in_every_language():
    for lang in LANGS:
        w = aw.trade_why(ROW, DESK_D, "D", {"d_open": 3}, CAPS, None, CTX, lang, title="x")
        assert "0.20%" in w["signal"] and "OP" in w["signal"]
        assert len(w["checks"]) >= 5 and all(c["ok"] for c in w["checks"])
        assert "394" in w["history"] and "2" in w["live"]
        assert "24" in w["plan"] and "$" in w["risk"]
    ru = aw.trade_why(ROW, DESK_D, "D", {"d_open": 3}, CAPS, None, CTX, "ru")
    assert "дешевле" in ru["signal"] and "закрыто: 2, из них в плюсе: 1" in ru["live"]
    assert "около $14" in ru["risk"] and "$15" in ru["risk"]


def test_no_grade_no_why():
    assert aw.trade_why(ROW, None, "", {}, CAPS, None, CTX, "ru") is None


def test_closed_trade_finds_its_signal():
    acts = [{"kind": "fut_open", "sym": "OP", "t": 1793405390120, "sig": "aggr:D-OP-498168"},
            {"kind": "fut_open", "sym": "TRX", "t": 1793398914686, "sig": "aggr:D-TRX-498166"}]
    assert aw.open_sig_for_closed({"inst": "OP-USDT-SWAP", "opened": 1793405389685}, acts) == "aggr:D-OP-498168"
    assert aw.open_sig_for_closed({"inst": "OP-USDT-SWAP", "opened": 1793405389685 - 3_600_000}, acts) is None
    assert aw.grade_of("aggr:E-ETH-1793305800000") == "E"


def test_calm_only_when_true():
    pp = {"pnl_pct": -5.94, "since": 1792669322166}
    e = {"cycle_sec": 60, "lab_tried": 800, "lab_passed": 28}
    assert "−7.6%" in aw.calm_note(pp, -7.57, e, "ru")["drawdown"]
    assert "drawdown" not in aw.calm_note(pp, -6.0, e, "ru")          # the account did not fall clearly less than bitcoin
    assert "drawdown" not in aw.calm_note({"pnl_pct": 1.0, "since": 1}, -7.0, e, "ru")
    assert "60" in aw.calm_note(pp, None, e, "en")["allday"]


def test_coin_why_from_table():
    row = {"symbol": "ZEC", "kind": "mid", "rating": 51, "consultant": {"mid": 86, "long": 100}, "chg7": -7.92, "from_high30_pct": -8.6,
           "checks": [{"key": "trend200", "t": "за 200 дней +261%", "ok": True}, {"key": "vol", "t": "оборот $985M в сутки", "ok": True},
                      {"key": "regime", "t": "BTC в бычьем режиме", "ok": True}]}
    w = aw.coin_why("ZEC", 5.78, row, {"status": "buy"}, 130, 10, "en")
    assert len(w["checks"]) == 6 and "$985M" in w["checks"][4]["t"] and "130" in w["scale"]


def test_dates_said_in_words():
    assert say_dates("с 22.10 по 04.11.2026", "ru") == "с 22 октября по 4 ноября 2026 года"
    assert say_dates("since 22.10", "en") == "since October 22"
    for s in ("+1.05%", "$2.10", "×1.10", "0.1207", "3.14"):
        assert say_dates(s, "en") == s
    assert "22 de octubre" in spoken("desde el 22.10", "es")


METHOD = r"кит|копи|трейдер|whale|copy|trader|ballena|φάλαιν|Luckyboytt|SilkBtc|OKX lead|Hyperliquid"


def test_system_signal_explains_itself_with_a_target_and_never_the_method():
    """05.11 (owner 09:52): a real futures trade is «Сигнал системы CRYPTO NOVA AI» — the market at the entry, the plan (entry, stop,
    target) and the exit rule; never the whale, the copying or a trader's name, in any language; the build 55 shape stays"""
    import json, re
    desk = {"started": 1792316209001, "trades": 8, "wins": 6,
            "open": [{"whale": "okx:821389378923984847", "coin": "kPEPE", "side": "LONG", "opened_t": 1793773985528, "whale_entry": 0.004839,
                      "whale_lev": 20.0, "whale_notional": 7931734, "stop": 0.0000046, "whale_ru": "Luckyboytt"}]}
    wd = aw.whale_trade("whales:W-1000PEPE-L-1793773985528", desk)
    rec = aw.whale_record("okx:821389378923984847", {"top": [{"addr": "okx:821389378923984847", "src": "okx", "trips": 410, "win": 0.81,
                                                              "pnl_30d": 38488.2, "pf": 7.9}]})
    why = aw.flat_real_why({"whale": {"name": "W1", "source": "okx_lead"}, "copy": {"stop": 108.0}, "target": {"ref_px": 84.0},
                            "market": {"btc_24h_pct": 2.9, "funding_8h_pct": 0.0036, "support": 96.0, "resistance": 104.0}})
    row = {"sym": "PEPE", "side": "SHORT", "entry": 100.0, "stop": 108.0, "lev": 5, "pnl_usd": -1.0, "move_pct": -1.0}
    ctx = {"since": 1792627200000, "budget_usd": 1500, "trend": {"PEPE": {"long": "BEAR", "mid": "SHORT"}}}
    keys = {"title", "signal", "checked_title", "checks", "history", "live", "plan", "risk", "result", "context", "copy", "target_ref",
            "summary", "target_line", "sections", "text"}
    for lang in aw.L5:
        d = aw.copy_why(row, wd, rec, why, desk, {"lev": 5, "risk_pct": 2.0}, ctx, lang)
        assert set(d) == keys and d["copy"] is True
        txt = json.dumps([v for k, v in d.items() if k != "copy"], ensure_ascii=False)     # «copy» is the build 55 render flag
        assert not re.search(METHOD, txt, re.I), (lang, re.search(METHOD, txt, re.I))
        assert "CRYPTO NOVA" in d["title"] and "CRYPTO NOVA" in d["summary"]
        assert d["target_ref"] == 84.0 and "84.00" in d["plan"] and "84.00" in d["target_line"] and "108.00" in d["plan"]
        assert "2%" in d["risk"] and "$30" in d["risk"]
        assert len(d["sections"]) >= 5 and all(x["title"] and x["text"] for x in d["sections"])
    ru = aw.copy_why(row, wd, rec, why, desk, {"lev": 5, "risk_pct": 2.0}, ctx, "ru")
    assert ru["title"] == "Сигнал системы CRYPTO NOVA AI" and ru["summary"].startswith("Сигнал системы CRYPTO NOVA AI: шорт PEPE. Цель 84.00")
    assert "при развороте сигнала или по стопу" in ru["plan"]
    assert any("Тренд PEPE: цена ниже средней за 200 дней, среднесрочно вниз" == c["t"] for c in ru["checks"])
    assert any("поддержка 96.00, сопротивление 104.00" in c["t"] for c in ru["checks"])
    assert "биткоин за сутки +2.9%" in ru["context"] and "фандинг" in ru["context"]
    assert aw._num(20.0, 0) == "20" and aw._price(0.0000049781) == "0.000004978"
