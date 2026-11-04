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
    assert "дешевле" in ru["signal"] and "закрыто 2 сделки, в плюсе 1" in ru["live"]
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
