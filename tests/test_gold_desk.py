"""«Золото · тест» demo desk (cnq/agents/gold_desk.py) — the rule engine, the live step and what desk_select reads."""
import json
import math
import os
import urllib.parse

from cnq.agents import gold_desk as g
from cnq.agents import desk_select as ds

T0 = 1_672_531_200_000                       # 2023-01-01 00:00 UTC (a Sunday)


def _bars(n, f):
    out = []
    for i in range(n):
        c0, c1 = f(i), f(i + 1)
        out.append({"t": T0 + i * g.H4, "o": c0, "h": max(c0, c1) * 1.001, "l": min(c0, c1) * 0.999, "c": c1, "v": 100.0})
    return out


def _okx(bars_by):
    """a fake OKX: /candles and /history-candles, newest first, pages of 100 «after» a timestamp"""
    def get(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        rows = bars_by.get(q["bar"], [])
        after = int(q["after"]) if q.get("after") else None
        sel = [b for b in reversed(rows) if after is None or b["t"] < after][:int(q.get("limit", 100))]
        return {"code": "0", "data": [[str(b["t"]), str(b["o"]), str(b["h"]), str(b["l"]), str(b["c"]), "1", "1", str(b["v"]), "1"] for b in sel]}
    return get


def test_sma100_backtest_goes_long_in_an_uptrend_and_out_in_a_downtrend():
    up = _bars(6 * 400, lambda i: 2000 * (1 + 0.0004 * i) if i < 6 * 250 else 2000 * (1 + 0.0004 * 6 * 250) * (1 - 0.0004 * (i - 6 * 250)))
    s = g.backtest(up, {"name": "sma100", "n": 100})
    assert s.closed, "a trade must close when the price falls under the 100-day average"
    first = s.closed[0]
    assert first["side"] == "LONG" and first["net_usd"] > 0 and first["exit_kind"] in ("RULE", "STOP")
    st = s.stats()
    assert st["trades"] == len(s.closed) and st["pf"] and st["pf"] > 1
    assert all(set(e) == {"time", "equity"} for e in s.history)
    # the fee is 0,05 % a side: a trade's net is its gross minus both fees
    c = s.closed[0]
    assert math.isclose(c["net_usd"], c["pnl_usd"] - c["fee_usd"] - c["qty"] * c["exit"] * g.FEE, abs_tol=0.05)


def test_weekend_fade_trades_against_a_weekend_move_back_to_friday():
    flat = [2000.0] * (6 * 30)
    bars = _bars(len(flat), lambda i: 2000.0)
    # 2023-01-06 is a Friday; its 20:00 bar index: 5 days + 20 h → 5*6 + 5 = 35; Sunday 16:00 bar: 7 days + 16 h → 7*6 + 4 = 46
    for k in range(36, 47):
        bars[k] = {**bars[k], "o": 2030.0, "h": 2031.0, "l": 2029.0, "c": 2030.0}
    bars[47] = {**bars[47], "o": 2030.0, "h": 2031.0, "l": 1999.0, "c": 2000.0}      # COMEX reopens: back to the Friday price
    s = g.backtest(bars, {"name": "weekend_fade"})
    assert s.closed and s.closed[0]["side"] == "SHORT" and s.closed[0]["exit_kind"] == "TP" and s.closed[0]["net_usd"] > 0


def test_live_run_starts_flat_then_applies_new_bars(tmp_path):
    f = lambda i: 2000 * (1 + 0.0003 * i)                            # a steady uptrend → sma100 wants a long
    b4 = _bars(6 * 300, f)
    days = g.daily_from_4h(b4)
    now = b4[-1]["t"] + g.H4 + 60_000
    out = str(tmp_path / "web")
    d = g.run(out, now=now, get=_okx({"4H": b4, "1Dutc": days}), log_path=str(tmp_path / "log.jsonl"))
    assert d["inst_used"] == "XAUT-USDT" and d["eligible_real"] is False and d["eligible_real_why"]
    assert d["closed"] == [] and d["stats"]["trades"] == 0 and d["equity_now"] == g.START   # no backfilled history trades
    assert d["pending"] and d["pending"][0]["side"] == "LONG"
    assert d["since"] == now and d["last_bar"] == b4[-1]["t"] + g.H4
    # 12 hours later: the pending long fills at the next open and is marked to the market
    b4b = _bars(6 * 300 + 3, f)
    d2 = g.run(out, now=now + 12 * 3_600_000, get=_okx({"4H": b4b, "1Dutc": g.daily_from_4h(b4b)}), log_path=str(tmp_path / "log.jsonl"))
    assert d2["since"] == now and len(d2["open"]) == 1 and d2["open"][0]["entry"] == b4b[-3]["o"]
    assert d2["equity_now"] > g.START - 10 and len(d2["equity_history"]) == 4          # the start point + 3 bars
    assert os.path.exists(tmp_path / "log.jsonl")
    # a third run with no new bar changes nothing
    d3 = g.run(out, now=now + 13 * 3_600_000, get=_okx({"4H": b4b, "1Dutc": g.daily_from_4h(b4b)}), log_path=str(tmp_path / "log.jsonl"))
    assert d3["equity_history"] == d2["equity_history"] and d3["open"][0]["id"] == d2["open"][0]["id"]


def test_live_run_without_candles_keeps_the_desk_standing(tmp_path):
    out = str(tmp_path / "web")
    d = g.run(out, now=T0, get=lambda u: {"data": []}, log_path=str(tmp_path / "l.jsonl"))
    assert d["inst_used"] is None and "стол стоит" in d["note"] and d["eligible_real"] is False
    assert json.load(open(os.path.join(out, "gold_desk.json")))["profile"] == "gold"


def test_desk_select_reads_the_gold_feed_as_a_staged_candidate_never_real(tmp_path):
    f = lambda i: 2000 * (1 + 0.0003 * i)
    b4 = _bars(6 * 300, f)
    d = g.run(str(tmp_path), now=b4[-1]["t"] + g.H4 + 1, get=_okx({"4H": b4, "1Dutc": g.daily_from_4h(b4)}), log_path=str(tmp_path / "l"))
    d["closed"] = [{"closed": d["since"] + 1000, "net_usd": 12.5}]
    src = ds.stage_source("gold", {}, None, d)
    assert src and src["key"] == "gold" and src["name"] == "Золото · тест"
    assert src["eligible_real"] is False and src["real"] is False and src["trades"] == [(d["since"] + 1000, 12.5)]
    assert src["start_eq"] == g.START and src["curve"]


def test_engine_owns_the_gold_feed_and_the_pipeline_ignores_it():
    from cnq import engine
    assert "golddesk" in engine.SLOW + engine.SLOW_BG and "gold_desk.json" in engine.OWNED and "gold_desk.jsonl" in engine.DATA_OWNED
    site = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".github", "workflows", "site.yml"), encoding="utf-8").read()
    assert '"web/gold_desk.json"' in site


def test_engine_runs_the_db_free_slow_steps_in_the_background_one_batch_at_a_time(monkeypatch):
    """11.11 (cycle over its 60 s budget): buy_now / gold desk do not hold up the minute loop; a batch is never started twice"""
    import threading
    from cnq import engine
    gate = threading.Event()
    ran = []

    def fake_sh(args, cwd, timeout=120, env=None):
        gate.wait(5)
        ran.append(args[-3])
        return True, 0.1, "ok"
    monkeypatch.setattr(engine, "sh", fake_sh)
    engine._BG.update(thread=None, steps={})
    assert engine._bg_start(["buynow", "golddesk"], {}) is True
    assert engine._bg_start(["buynow", "golddesk"], {}) is False          # still running → not started again
    gate.set()
    engine._BG["thread"].join(5)
    assert ran == ["buynow", "golddesk"] and set(engine._BG["steps"]) == {"buynow", "golddesk"}
    assert "buynow" in engine.SLOW_BG and "buynow" not in engine.SLOW and "quality" in engine.SLOW


def test_engine_spreads_the_hourly_funding_over_six_cycles(monkeypatch):
    """11.11: every core coin's funding still once an hour, in 6 slices of ~4 s instead of one ~25 s step"""
    from cnq import engine
    calls = []
    monkeypatch.setattr(engine, "sh", lambda args, cwd, timeout=120, env=None: (calls.append(((env or {}).get("CNQ_REFRESH"),
                                                                                          (env or {}).get("CNQ_REFRESH_SLICE"))) or (True, 0.0, "")))
    monkeypatch.setattr(engine, "FAST", [])
    monkeypatch.setattr(engine, "_bg_start", lambda cmds, env: True)
    for n in range(1, 61):
        engine.cycle(n, {})
    sl = [s for k, s in calls if k == "funding"]
    assert sorted(sl) == [f"{k}/6" for k in range(6)]
