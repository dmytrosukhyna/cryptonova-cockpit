import json
import os
import sqlite3

from cnq.agents import okx_signal as OS
from cnq import engine as E

T0 = 1_792_700_000_000
TOKEN = "tok-SECRET-1234567890abcdef"
URL = "https://www.okx.com/algo/signal/trigger"
ON = {"CNQ_OKX_SIGNAL": "on", "CNQ_OKX_SIGNAL_URL": URL, "CNQ_OKX_SIGNAL_TOKEN": TOKEN}
SWAPS = {"BTC-USDT-SWAP", "ETH-USDT-SWAP", "APT-USDT-SWAP", "SOL-USDT-SWAP", "PEPE-USDT-SWAP"}


def trade(sym="APT", side="LONG", opened=T0, qty=100.0, qty_left=None, tid=None, **kw):
    return {"id": tid or f"F-{sym}-{opened}", "symbol": sym, "side": side, "entry": 10.0, "stop": 9.4, "qty": qty,
            "qty_left": qty if qty_left is None else qty_left, "opened": opened, **kw}


def paper(tmp, bal_open=(), bal_closed=(), imp_open=(), imp_closed=(), main="bal", aggr_open=()):
    """bal_* = the trades of «Сбалансированная»; 28.10: the main desk of the header is the one web/desk_select.json chose (`main`)"""
    doc = {"profiles": {"bal": {"equity": 10000.0, "open": list(bal_open), "closed": list(bal_closed)},
                        "imp": {"equity": 10000.0, "open": list(imp_open), "closed": list(imp_closed)},
                        "aggr": {"equity": 10000.0, "open": list(aggr_open), "closed": []},
                        "cons": {"equity": 10000.0, "open": [trade("SOL", opened=T0 + 1)], "closed": []}}}
    with open(os.path.join(tmp, "paper.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f)
    with open(os.path.join(tmp, "desk_select.json"), "w", encoding="utf-8") as f:
        json.dump({"current": main}, f)


class Sender:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def __call__(self, url, body):
        self.calls.append((url, json.loads(json.dumps(body))))
        return (True, "HTTP 200") if self.ok else (False, "HTTP 500")


def go(conn, tmp, now, sender, env=ON):
    return OS.run(conn, str(tmp), now=now, env=env, data_dir=str(tmp), sender=sender, sleep=lambda s: None,
                  fetch_swaps=lambda: set(SWAPS))


def test_off_by_default_sends_nothing_and_says_why(tmp_path):
    conn, s = sqlite3.connect(":memory:"), Sender()
    paper(tmp_path, bal_open=[trade(opened=T0)])
    d = go(conn, tmp_path, T0 + 60_000, s, env={})
    assert d["on"] is False and "CNQ_OKX_SIGNAL" in d["why_off"] and s.calls == []
    assert json.load(open(tmp_path / "okx_signal.json"))["on"] is False
    # switched on but a value missing → still off; a non-OKX URL → off (the token is never sent elsewhere)
    assert OS.config({"CNQ_OKX_SIGNAL": "on", "CNQ_OKX_SIGNAL_URL": URL})["on"] is False
    assert OS.config({**ON, "CNQ_OKX_SIGNAL_URL": "https://evil.example.com/okx.com"})["on"] is False
    assert OS.config({**ON, "CNQ_OKX_SIGNAL_URL": "http://www.okx.com/algo/signal/trigger"})["on"] is False
    assert OS.config(ON)["on"] is True


def test_message_format_matches_okx_spec():
    m = OS.message("ENTER_LONG", "BTC-USDT-SWAP", TOKEN, 1_792_700_000_123, "percentage_investment", 12.5)
    assert m == {"action": "ENTER_LONG", "instrument": "BTC-USDT-SWAP", "signalToken": TOKEN,
                 "timestamp": "2026-10-22T20:13:20.123Z", "maxLag": "60", "orderType": "market",
                 "investmentType": "percentage_investment", "amount": "12.5"}
    x = OS.message("EXIT_SHORT", "ETH-USDT-SWAP", TOKEN, T0, "percentage_position", 100.0)
    assert x["action"] == "EXIT_SHORT" and x["investmentType"] == "percentage_position" and x["amount"] == "100"


def test_symbol_mapping_to_okx_swaps():
    assert OS.inst_of("APT") == "APT-USDT-SWAP"
    assert OS.inst_of("BTCUSDT") == "BTC-USDT-SWAP"
    assert OS.inst_of("ETH-USDT") == "ETH-USDT-SWAP"
    assert OS.inst_of("1000PEPE") == "PEPE-USDT-SWAP"
    assert OS.inst_of("1000PEPEUSDT") == "PEPE-USDT-SWAP"
    assert OS.listed("APT-USDT-SWAP", SWAPS, "okx") is True
    assert OS.listed("XYZ-USDT-SWAP", SWAPS, "okx") is False
    assert OS.listed("XYZ-USDT-SWAP", OS.CORE_SWAPS, "core") is None      # unknown, not «not listed»


def test_full_trade_cycle_is_idempotent_and_skips_old_trades(tmp_path):
    db = str(tmp_path / "agents.sqlite")
    conn, s = sqlite3.connect(db), Sender()
    old = trade("SOL", opened=T0 - 3_600_000, tid="OLD")
    paper(tmp_path, bal_open=[old])
    go(conn, tmp_path, T0, s)                                       # switched on now: the old trade is never sent
    assert s.calls == []
    t = trade("APT", opened=T0 + 60_000)
    paper(tmp_path, bal_open=[old, t])
    go(conn, tmp_path, T0 + 120_000, s)
    assert len(s.calls) == 1
    url, b = s.calls[0]
    assert url == URL and b["action"] == "ENTER_LONG" and b["instrument"] == "APT-USDT-SWAP" and b["signalToken"] == TOKEN
    assert b["investmentType"] == "percentage_investment" and b["amount"] == "5"     # $1 000 of a $20 000 account
    # restart: a new connection on the same DB file → nothing is sent twice
    conn.close()
    conn = sqlite3.connect(db)
    go(conn, tmp_path, T0 + 180_000, s)
    assert len(s.calls) == 1
    # a partial exit: half of the position, then a quarter of the original = half of what is left
    paper(tmp_path, bal_open=[old, dict(t, qty_left=50.0)])
    go(conn, tmp_path, T0 + 240_000, s)
    paper(tmp_path, bal_open=[old, dict(t, qty_left=25.0)])
    go(conn, tmp_path, T0 + 300_000, s)
    go(conn, tmp_path, T0 + 360_000, s)
    assert [(c[1]["action"], c[1]["investmentType"], c[1]["amount"]) for c in s.calls[1:]] == [
        ("EXIT_LONG", "percentage_position", "50"), ("EXIT_LONG", "percentage_position", "50")]
    # the close: 100 % of what is left, once
    paper(tmp_path, bal_open=[old], bal_closed=[dict(t, qty_left=0.0, closed=T0 + 400_000, exit_kind="TIME")])
    go(conn, tmp_path, T0 + 420_000, s)
    go(conn, tmp_path, T0 + 480_000, s)
    assert [c[1]["action"] for c in s.calls] == ["ENTER_LONG", "EXIT_LONG", "EXIT_LONG", "EXIT_LONG"]
    assert s.calls[-1][1]["amount"] == "100"
    # the old trade closes: its entry was never sent → no exit
    paper(tmp_path, bal_closed=[dict(old, closed=T0 + 500_000), dict(t, qty_left=0.0, closed=T0 + 400_000)])
    go(conn, tmp_path, T0 + 540_000, s)
    assert len(s.calls) == 4
    d = json.load(open(tmp_path / "okx_signal.json"))
    assert d["on"] and d["sent"] == 4 and d["last"]["coin"] == "APT" and d["last"]["desk"] == "bal"


def test_unlisted_coin_and_busy_instrument_are_skipped_with_reason(tmp_path):
    conn, s = sqlite3.connect(":memory:"), Sender()
    paper(tmp_path)
    go(conn, tmp_path, T0, s)
    paper(tmp_path, bal_open=[trade("XYZ", opened=T0 + 10), trade("BTC", opened=T0 + 20)],
          imp_open=[trade("BTC", side="SHORT", opened=T0 + 30, tid="IMP-BTC")])
    d = go(conn, tmp_path, T0 + 60_000, s)
    assert [(c[1]["action"], c[1]["instrument"]) for c in s.calls] == [("ENTER_LONG", "BTC-USDT-SWAP")]
    notes = {(x["coin"], x["desk"]): x for x in d["items"]}
    assert notes[("XYZ", "bal")]["status"] == "skipped" and "OKX не торгует" in notes[("XYZ", "bal")]["note"]
    assert notes[("BTC", "imp")]["status"] == "skipped" and "уже идёт сигнал" in notes[("BTC", "imp")]["note"]
    assert not any(x["desk"] == "cons" for x in d["items"])                    # only the header desks


def test_failing_webhook_retries_then_never_breaks_the_run(tmp_path):
    conn, bad = sqlite3.connect(":memory:"), Sender(ok=False)
    paper(tmp_path)
    go(conn, tmp_path, T0, bad)
    paper(tmp_path, bal_open=[trade("ETH", opened=T0 + 10)])
    d = go(conn, tmp_path, T0 + 60_000, bad)
    assert len(bad.calls) == OS.TRIES and d["pending"] == 1 and "error" not in d
    good = Sender()
    go(conn, tmp_path, T0 + 120_000, good)                           # the next minute it goes out
    assert len(good.calls) == 1 and good.calls[0][1]["action"] == "ENTER_LONG"
    # a sender that raises is caught too
    paper(tmp_path, bal_open=[trade("ETH", opened=T0 + 10)], imp_open=[trade("SOL", opened=T0 + 130_000)])

    def boom(url, body):
        raise OSError("network down " + TOKEN)
    d = go(conn, tmp_path, T0 + 180_000, boom)
    assert d["pending"] == 1 and TOKEN not in json.dumps(d)
    # an entry that could not go out in time is dropped, not sent late
    go(conn, tmp_path, T0 + 130_000 + 31 * 60_000, Sender(ok=False))
    assert json.load(open(tmp_path / "okx_signal.json"))["failed"] == 1


def test_token_and_url_never_in_published_json(tmp_path):
    conn = sqlite3.connect(":memory:")
    paper(tmp_path)
    go(conn, tmp_path, T0, Sender())
    paper(tmp_path, bal_open=[trade("APT", opened=T0 + 10)])

    def leaky(url, body):
        return False, f"rejected {body['signalToken']} at {url}"
    go(conn, tmp_path, T0 + 60_000, leaky)
    go(conn, tmp_path, T0 + 120_000, Sender())
    txt = open(tmp_path / "okx_signal.json", encoding="utf-8").read()
    assert TOKEN not in txt and URL not in txt and json.loads(txt)["sent"] == 1
    assert TOKEN not in json.dumps(conn.execute("SELECT * FROM okx_signal_events").fetchall())


def test_engine_runs_the_step_and_publishes_the_log():
    assert "okxsignal" in E.FAST and E.FAST.index("paper") < E.FAST.index("okxsignal") and "okxsignal" in E.STEP_TIMEOUT
    assert "okx_signal.json" in E.OWNED


def test_a_switched_main_desk_keeps_exits_of_its_live_trades_and_opens_nothing_new(tmp_path):
    """28.10: desk_select moved the main desk bal → aggr while a bal trade was live on the signal: its exit still goes out, a new
    bal trade is not sent, aggr trades are"""
    conn, s = sqlite3.connect(":memory:"), Sender()
    paper(tmp_path)
    go(conn, tmp_path, T0, s)                                              # switched on
    t = trade("APT", opened=T0 + 10)
    paper(tmp_path, bal_open=[t])
    go(conn, tmp_path, T0 + 60_000, s)
    assert [c[1]["action"] for c in s.calls] == ["ENTER_LONG"]
    paper(tmp_path, bal_open=[t, trade("ETH", opened=T0 + 100_000)], aggr_open=[trade("SOL", opened=T0 + 110_000)], main="aggr")
    d = go(conn, tmp_path, T0 + 120_000, s)
    assert [(c[1]["action"], c[1]["instrument"]) for c in s.calls] == [("ENTER_LONG", "APT-USDT-SWAP"), ("ENTER_LONG", "SOL-USDT-SWAP")]
    assert d["desks"] == ["aggr", "imp"]
    paper(tmp_path, bal_closed=[dict(t, qty_left=0.0, closed=T0 + 150_000)], aggr_open=[trade("SOL", opened=T0 + 110_000)], main="aggr")
    go(conn, tmp_path, T0 + 180_000, s)
    assert [(c[1]["action"], c[1]["instrument"]) for c in s.calls][-1] == ("EXIT_LONG", "APT-USDT-SWAP")
