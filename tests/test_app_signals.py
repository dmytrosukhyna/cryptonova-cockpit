"""CRYPTO NOVΛ app signals: tolerant parsing, path simulation on synthetic 15m candles, exit-policy maths, history upsert,
features/profile/proposals and the shape of web/app_signals.json — no network (API and candles are injected)."""
import json
import math
import os
import sqlite3
import tempfile

from cnq.agents import app_signals as aps

M15, H1, H4, DAY = aps.M15, aps.H1, aps.H4, aps.DAY
T0 = 1_790_000_100_000 // M15 * M15          # aligned to 15m
TS = T0 + 5 * 60_000                           # signal 5 minutes into the candle → first full candle = T0 + 15m
P0 = T0 + M15
COST = aps.COST_PCT


def _bars(path, start=P0):
    """path = list of (o, h, l, c) → [[t, o, h, l, c], …] 15m candles from `start`."""
    return [[start + i * M15, *c] for i, c in enumerate(path)]


def _flat(px, n, start):
    return [[start + i * M15, px, px * 1.001, px * 0.999, px] for i in range(n)]


def _sig(side="LONG", lo=100.0, hi=100.0, sl=95.0, tps=(105.0, 110.0, 115.0), lev=10.0, t=TS, sym="BTC-USDT"):
    return {"side": side, "entry_lo": lo, "entry_hi": hi, "sl": sl, "tps": list(tps), "t": t, "leverage": lev, "symbol": sym}


# ------------------------------------------------------------------------------------------------ parsing
def test_parse_numbers_ranges_sides_symbols_times():
    assert aps.parse_num("65000") == 65000.0
    assert aps.parse_num("65 000,5") == 65000.5
    assert aps.parse_num("1,234.5") == 1234.5
    assert aps.parse_num("0,1234") == 0.1234
    assert aps.parse_num("1.234.567") == 1234567.0
    assert aps.parse_num("$0.52") == 0.52 and aps.parse_num("+12.5%") == 12.5
    assert aps.parse_num("") is None and aps.parse_num("abc") is None and aps.parse_num(None) is None
    assert aps.parse_entry("0.1234 - 0.1250") == (0.1234, 0.125)
    assert aps.parse_entry("100–105") == (100.0, 105.0)
    assert aps.parse_entry("100-105") == (100.0, 105.0)
    assert aps.parse_entry("1 234.5 - 1 240") == (1234.5, 1240.0)
    assert aps.parse_entry("0,1234 - 0,1250") == (0.1234, 0.125)
    assert aps.parse_entry("105 / 100") == (100.0, 105.0)
    assert aps.parse_entry(["1.5", "1.6"]) == (1.5, 1.6)
    assert aps.parse_entry("64250.5") == (64250.5, 64250.5)
    assert aps.parse_entry(64250) == (64250.0, 64250.0)
    assert aps.parse_side("Short") == "SHORT" and aps.parse_side("LONG") == "LONG" and aps.parse_side("buy") == "LONG"
    assert aps.parse_side("Лонг") == "LONG" and aps.parse_side("") is None
    for name, want in (("BTC/USDT", "BTC-USDT"), ("ETHUSDT", "ETH-USDT"), ("#SOL/USDT", "SOL-USDT"), ("1000PEPE-USDT.P", "1000PEPE-USDT"),
                       ("btc_usdt", "BTC-USDT"), ("XRPUSDT PERP", "XRP-USDT"), ("DOGE", "DOGE-USDT"), ("BTC/USDT:USDT", "BTC-USDT")):
        assert aps.parse_symbol(name) == want, name
    assert aps.parse_time(1_790_000_000_000) == 1_790_000_000_000
    assert aps.parse_time("1790000000") == 1_790_000_000_000
    assert aps.parse_time("2026-10-18T10:00:00Z") == 1_792_317_600_000
    assert aps.parse_leverage("20x") == 20 and aps.parse_leverage("Cross (25X)") == 25 and aps.parse_leverage(None) == 1


def test_normalize_app_shape_and_variants():
    raw = {"signal_id": 77, "name": "BTC/USDT", "entry_point": "100 - 102", "stop_lost": "95,5", "actual_price": "101",
           "actual_precent": "+1.2", "leverage_x": "20", "date": TS, "position_side": "Long", "icon_url": "", "is_vip": False,
           "targets": [{"level": 2, "price": "110", "precent": "200", "is_completed": False},
                       {"level": 1, "price": "105", "precent": "100", "is_completed": True}]}
    s = aps.normalize(raw)
    assert s["signal_id"] == "77" and s["symbol"] == "BTC-USDT" and s["side"] == "LONG"
    assert (s["entry_lo"], s["entry_hi"], s["sl"], s["leverage"]) == (100.0, 102.0, 95.5, 20.0)
    assert [x["price"] for x in s["targets"]] == [105.0, 110.0] and s["app_best_tp"] == 1 and s["problems"] == []
    # camelCase keys, seconds, a side label that contradicts the prices, an unknown field
    raw2 = {"signalId": "a1", "name": "ETHUSDT", "entryPoint": 2000, "stopLost": "2100", "leverageX": "x10", "date": TS // 1000,
            "positionSide": "LONG", "targets": [{"level": 1, "price": "1900", "precent": "-50"}], "strategy": "x"}
    s2 = aps.normalize(raw2)
    assert s2["side"] == "SHORT" and "side_conflict" in s2["problems"] and s2["t"] == TS // 1000 * 1000
    assert s2["extra_keys"] == ["strategy"]
    s3 = aps.normalize({"name": "SOL/USDT", "entry_point": "?", "stop_lost": "", "targets": []})
    assert {"entry", "stop", "targets", "no_id"} <= set(s3["problems"])


def test_extract_list_variants():
    assert aps.extract_list({"code": 1, "body": [{"a": 1}], "error_message": None, "date": 1})[0] == [{"a": 1}]
    assert aps.extract_list([{"a": 1}])[0] == [{"a": 1}]
    assert aps.extract_list({"code": 1, "body": {"signals": [{"a": 2}]}})[0] == [{"a": 2}]
    items, err = aps.extract_list({"code": 0, "body": None, "error_message": "Network error"})
    assert items is None and "Network error" in err


# ------------------------------------------------------------------------------------------------ simulation
def test_tp1_then_stop():
    cs = _bars([(100.5, 100.8, 99.8, 100.2),      # fill at 100 (touched), opened above → no TP on this candle
                (100.2, 105.5, 100.1, 105.0),     # TP1
                (105.0, 105.2, 101.0, 101.5),
                (101.5, 101.6, 94.0, 94.5)])      # back through entry → stop
    sim = aps.simulate(_sig(), cs, P0 + 10 * DAY)
    assert sim["state"] == "closed" and sim["ended"] == "sl" and sim["best_tp"] == 1 and sim["first"] == "tp1"
    assert sim["fill"] == 100.0 and abs(sim["risk_pct"] - 5.0) < 1e-9
    assert sim["time_to_tp1_h"] == round((P0 + M15 - TS) / H1, 2)
    p = sim["policies"]
    assert abs(p["app"]["net"] - (5.0 - COST)) < 1e-9                        # farthest TP reached = TP1
    assert abs(p["tp1"]["net"] - (5.0 - COST)) < 1e-9
    assert abs(p["last"]["net"] - (-5.0 - COST)) < 1e-9
    assert abs(p["ladder"]["net"] - (5.0 / 3 + 2 / 3 * -5.0 - COST)) < 1e-3   # 1/3 at TP1, 2/3 at the stop
    assert abs(p["ladder_be"]["net"] - (5.0 / 3 - COST)) < 1e-3               # 1/3 at TP1, rest at entry
    assert sim["be_t"] == P0 + 3 * M15 and abs(p["tp1"]["r"] - (5.0 - COST) / 5.0) < 1e-9
    assert abs(p["tp1"]["lev"] - (5.0 - COST) * 10) < 1e-6
    assert sim["after_tp1"] is None                                             # 7 days after the stop not covered yet


def test_stop_first_and_same_candle_is_cautious():
    cs = _bars([(100.0, 100.5, 99.5, 100.2), (100.2, 100.4, 94.9, 95.5)])
    sim = aps.simulate(_sig(), cs, P0 + DAY)
    assert sim["ended"] == "sl" and sim["first"] == "sl" and sim["best_tp"] == 0
    assert all(abs(sim["policies"][k]["net"] - (-5.0 - COST)) < 1e-9 for k in ("app", "tp1", "ladder", "ladder_be", "last"))
    cs2 = _bars([(100.0, 100.5, 99.5, 100.2), (100.2, 106.0, 94.0, 100.0)])   # one candle touches TP1 and the stop
    sim2 = aps.simulate(_sig(), cs2, P0 + DAY)
    assert sim2["ended"] == "sl" and sim2["best_tp"] == 0 and sim2["ambiguous"] == 1


def test_never_filled_within_48h_and_missed_to_tp1():
    cs = _flat(103.0, 200, P0)                         # long entry 100, price stays at 103 → no touch
    sim = aps.simulate(_sig(), cs, P0 + 3 * DAY)
    assert sim["state"] == "not_filled" and sim["reason"] == "no_touch" and sim["policies"] == {}
    sim_early = aps.simulate(_sig(), cs[:50], P0 + 50 * M15)
    assert sim_early["state"] == "pending"
    cs2 = _bars([(103.0, 103.5, 102.5, 103.0), (103.0, 106.0, 102.8, 105.5)])
    assert aps.simulate(_sig(), cs2, P0 + DAY)["reason"] == "tp1_before_entry"


def test_range_entry_fill_and_all_targets():
    sig = _sig(lo=98.0, hi=100.0, sl=94.0, tps=(104.0, 108.0))
    cs = _bars([(101.0, 101.2, 100.5, 101.0),      # above the range
                (100.8, 100.9, 99.0, 99.5),        # comes down into the range → fill at its top (100)
                (99.5, 104.5, 99.4, 104.0),        # TP1
                (104.0, 108.2, 103.9, 108.0)])     # TP2 = last target
    sim = aps.simulate(sig, cs, P0 + DAY)
    assert sim["fill"] == 100.0 and sim["ended"] == "last_tp" and sim["best_tp"] == 2
    p = sim["policies"]
    assert abs(p["last"]["net"] - (8.0 - COST)) < 1e-9 and abs(p["app"]["net"] - (8.0 - COST)) < 1e-9
    assert abs(p["ladder"]["net"] - (6.0 - COST)) < 1e-9 and abs(p["ladder_be"]["net"] - (6.0 - COST)) < 1e-9
    assert abs(p["tp1"]["net"] - (4.0 - COST)) < 1e-9
    # price opens inside the range → filled at the open
    sim2 = aps.simulate(sig, _bars([(99.0, 99.2, 98.8, 99.1), (99.1, 104.1, 99.0, 104.0)]), P0 + DAY)
    assert sim2["fill"] == 99.0 and sim2["best_tp"] == 1


def test_short_signal_and_timeout():
    sig = _sig(side="SHORT", lo=100.0, hi=100.0, sl=105.0, tps=(95.0, 90.0), lev=5)
    cs = _bars([(99.8, 100.2, 99.7, 100.0)]) + _flat(97.0, 14 * 96 + 5, P0 + M15)
    sim = aps.simulate(sig, cs, P0 + 20 * DAY)
    assert sim["fill"] == 100.0 and sim["ended"] == "timeout" and sim["best_tp"] == 0
    assert abs(sim["policies"]["last"]["pct"] - 3.0) < 1e-9          # short from 100, closed at 97 after 14 days


def test_liquidation_caps_leveraged_loss():
    sig = _sig(sl=80.0, tps=(110.0,), lev=10)             # stop 20 % away with 10x → liquidation (~9.5 %) comes first
    cs = _bars([(100.0, 100.3, 99.9, 100.1), (100.1, 100.2, 89.0, 90.0), (90.0, 90.0, 79.0, 79.5)])
    sim = aps.simulate(sig, cs, P0 + DAY)
    assert sim["liq_risk"] and sim["liquidated"] and sim["ended"] == "sl"
    assert sim["policies"]["last"]["lev"] == -100.0 and sim["policies"]["last"]["net"] < -20


# ------------------------------------------------------------------------------------------------ storage + run
def _api_item(sid, t=TS, entry="100", sl="95", tps=("105", "110", "115"), side="LONG", name="BTC/USDT", done=()):
    return {"signal_id": sid, "name": name, "entry_point": entry, "stop_lost": sl, "leverage_x": "10", "date": t,
            "position_side": side, "icon_url": "", "is_vip": False,
            "targets": [{"level": i + 1, "price": p, "precent": "0", "is_completed": (i + 1) in done} for i, p in enumerate(tps)]}


def test_upsert_keeps_history_when_api_drops_old_signals():
    conn = sqlite3.connect(":memory:")
    aps.upsert(conn, {"active": [_api_item(1)], "closed": [_api_item(2), _api_item(3)]}, TS, active_ok=True)
    aps.upsert(conn, {"active": [_api_item(4)], "closed": [_api_item(3)]}, TS + H1, active_ok=True)   # 1 left /active, 2 vanished
    rows = dict(conn.execute("SELECT signal_id, status FROM app_signals"))
    assert rows == {"1": "closed", "2": "closed", "3": "closed", "4": "active"}
    cs = conn.execute("SELECT closed_seen, first_seen, last_seen FROM app_signals WHERE signal_id='1'").fetchone()
    assert cs == (TS + H1, TS, TS)
    # a failed /active fetch must not close active signals
    aps.upsert(conn, {"closed": []}, TS + 2 * H1, active_ok=False)
    assert conn.execute("SELECT status FROM app_signals WHERE signal_id='4'").fetchone()[0] == "active"


def _market(sym_paths):
    """Fake klines: 15m from explicit bars per symbol (flat around them otherwise), 1h/4h synthetic uptrend."""
    def klines(symbol, iv, start, end):
        ms = aps.IV_MS[iv]
        if iv == "15m":
            bars = {b[0]: b for b in sym_paths.get(symbol, [])}
            out = []
            t = (start + ms - 1) // ms * ms
            last_px = None
            while t <= end:
                if t in bars:
                    out.append(list(bars[t])); last_px = bars[t][4]
                elif bars and t > max(bars):
                    px = bars[max(bars)][4]
                    out.append([t, px, px * 1.0005, px * 0.9995, px])
                t += ms
            return out
        out, t = [], (start + ms - 1) // ms * ms
        while t <= end:
            i = (t - (T0 - 400 * H4)) / ms
            px = 80.0 + 0.02 * i * (ms / H1)
            out.append([t, px, px * 1.004, px * 0.996, px * 1.001])
            t += ms
        return out
    klines.used = {"fake": 1}
    return klines


def test_run_end_to_end_json_shape(tmp_path=None):
    tmp = tempfile.mkdtemp() if tmp_path is None else str(tmp_path)
    conn = sqlite3.connect(":memory:")
    win = _bars([(100.5, 100.8, 99.8, 100.2), (100.2, 105.5, 100.1, 105.0), (105.0, 110.4, 104.9, 110.0), (110.0, 115.5, 109.9, 115.0)])
    paths = {"BTC-USDT": win, "ETH-USDT": [[b[0], b[1] * 20, b[2] * 20, b[3] * 20, b[4] * 20] for b in
                                           _bars([(100.0, 100.5, 99.5, 100.2), (100.2, 100.4, 94.0, 95.0)])]}
    api = {aps.API_ACTIVE: {"code": 1, "body": [_api_item(10, t=P0 + 20 * M15 + 60_000, name="BTC/USDT")], "error_message": None, "date": 0},
           aps.API_HISTORY: {"code": 1, "body": [_api_item(1, done=(1, 2, 3)),
                                                 _api_item(2, entry="2000", sl="1900", tps=("2100", "2200"), name="ETHUSDT", done=(1,))],
                             "error_message": None, "date": 0}}
    now = P0 + 9 * DAY
    d = aps.write(tmp, conn, now=now, fetch_json=lambda u: api[u], klines=_market(paths), budget_s=30, max_requests=500)
    on_disk = json.load(open(os.path.join(tmp, "app_signals.json"), encoding="utf-8"))
    for k in ("asof", "source", "counts", "stats", "active", "recent", "profile", "proposals", "summary_ru", "notes_ru", "policies"):
        assert k in on_disk, k
    c = d["counts"]
    assert c["total"] == 3 and c["active"] == 1 and c["closed"] == 2 and c["resolved"] == 2
    st = d["stats"]
    assert set(st["policies"]) == {"app", "tp1", "ladder", "ladder_be", "last"}
    for k in ("n", "winrate", "avg_pct", "avg_r", "sum_pct", "best", "worst"):
        assert k in st["policies"]["app"]
    assert st["policies"]["app"]["n"] == 2 and st["tp_reach"]["tp1"] == 0.5 and st["sl_share"] == 0.5
    assert st["app_flags"]["n"] == 2 and st["app_flags"]["app_more"] == 1        # the app says TP1 for ETH, prices say stop
    assert {"LONG", "SHORT"} == set(st["by_side"]) and st["by_coin"]
    r1 = next(r for r in d["recent"] if r["id"] == "1")
    assert r1["state_ru"] == "все цели (TP3)" and abs(r1["policies"]["last"]["pct"] - (15.0 - COST)) < 1e-9
    a = d["active"][0]
    assert a["id"] == "10" and a["price"] is not None and "to_sl_pct" in a and a["targets"][0]["hit"] in (True, False)
    assert d["profile"]["n_signals"] == 3 and d["profile"]["summary_ru"]
    assert any(p["id"] == "exit_policy" for p in d["proposals"])
    assert all("рано судить" in p["verdict_ru"] for p in d["proposals"])          # 2 resolved signals < 30
    assert "меньше 30" in d["summary_ru"]
    f1 = json.loads(conn.execute("SELECT features FROM app_signals WHERE signal_id='1'").fetchone()[0])
    assert f1["trend4h"] == "up" and f1["with_trend"] is True and f1["rsi1h"] is not None and f1["archetype"].startswith("Покупка")
    # second run: no new requests for finished signals, history kept although the API now returns nothing
    api2 = {aps.API_ACTIVE: {"code": 1, "body": []}, aps.API_HISTORY: {"code": 1, "body": []}}
    d2 = aps.run(conn, now=now + H1, fetch_json=lambda u: api2[u], klines=_market(paths), budget_s=30, max_requests=500)
    assert d2["counts"]["total"] == 3 and d2["counts"]["active"] == 0


def test_api_down_is_reported_not_fatal():
    conn = sqlite3.connect(":memory:")

    def boom(u):
        raise OSError("unreachable")
    d = aps.run(conn, now=TS, fetch_json=boom, klines=_market({}), budget_s=5, max_requests=5)
    assert d["counts"]["total"] == 0 and d["source"]["api_ok"]["active"] is False
    assert any("API active" in n for n in d["notes_ru"])


def test_proposals_with_enough_history():
    """35 synthetic resolved signals: half go to TP1 then back to the stop, half straight to the stop when against BTC."""
    rows = []
    for i in range(36):
        with_btc = i % 2 == 0
        win = with_btc
        sim = {"state": "closed", "n_tps": 2, "best_tp": 1 if win else 0, "first": "tp1" if win else "sl",
               "ended": "sl", "after_tp1": False, "mfe_pct": 3, "mae_pct": 2, "time_to_tp1_h": 2.0 if win else None,
               "policies": {k: {"closed": True, "net": (2.0 if win else -2.0), "pct": 0, "lev": (20.0 if win else -20.0),
                                "r": (1.0 if win else -1.0)} for k in ("app", "tp1", "ladder", "last")}}
        sim["policies"]["ladder_be"] = {"closed": True, "net": 1.0 if win else -2.0, "pct": 0, "lev": 0, "r": 0.5 if win else -1.0}
        rows.append({"id": str(i), "side": "LONG", "coin": "BTC", "leverage": 20.0 if i % 3 else 5.0, "targets": [], "app_best_tp": 0,
                     "status": "closed", "sim": sim, "feat": {"with_btc": with_btc, "with_trend": True, "rsi_wrong": False,
                                                               "hour_utc": i % 24, "side": "LONG", "leverage": 20.0}})
    props = {p["id"]: p for p in aps.build_proposals(rows)}
    b = props["skip_vs_btc"]
    assert b["n"] == 36 and b["skipped"] == 18 and b["before"]["avg_r"] == 0.0 and b["after"]["avg_r"] == 1.0
    assert b["verdict_ru"].startswith("помогает")
    assert props["be_after_tp1"]["verdict_ru"].startswith("вредит")      # 1R winners cut to 0.5R, losers unchanged
    assert props["skip_rsi_wrong"]["skipped"] == 0 and "почти нечего" in props["skip_rsi_wrong"]["verdict_ru"]
    assert props["lev_cap"]["over_cap"] == 24
    assert "проверять на новых" in props["time_filter"]["verdict_ru"] or "рано" not in props["time_filter"]["verdict_ru"]


def test_features_indicators():
    closes = [100 + i for i in range(30)]
    assert aps.rsi(closes) == 100.0
    assert abs(aps.ema([1.0] * 60, 50) - 1.0) < 1e-12
    rows = [[i * H1, 100, 101, 99, 100] for i in range(40)]
    assert abs(aps.atr(rows) - 2.0) < 1e-9
    f = aps.archetype({"side": "SHORT", "trend4h": "up", "rsi1h": 75, "dist_hi24_pct": -0.5, "dist_lo24_pct": 6})
    assert f == "Шорт на перекупленности против тренда"
    assert aps.archetype({"side": "LONG", "trend4h": "up", "rsi1h": 45, "dist_hi24_pct": -4, "dist_lo24_pct": 1}) == \
        "Покупка отката в восходящем тренде"


def test_budget_exhaustion_resumes_next_run_and_plan_change_resets():
    conn = sqlite3.connect(":memory:")
    paths = {"BTC-USDT": _bars([(100.5, 100.8, 99.8, 100.2), (100.2, 115.5, 100.1, 115.0)])}
    api = {aps.API_ACTIVE: {"code": 1, "body": []}, aps.API_HISTORY: {"code": 1, "body": [_api_item(1), _api_item(2, t=TS + H1)]}}
    now = P0 + 3 * DAY
    d1 = aps.run(conn, now=now, fetch_json=lambda u: api[u], klines=_market(paths), budget_s=30, max_requests=1)
    assert d1["source"]["candles"]["requests"] == 1 and d1["counts"]["resolved"] < 2
    d2 = aps.run(conn, now=now + H1, fetch_json=lambda u: api[u], klines=_market(paths), budget_s=30, max_requests=50)
    assert d2["counts"]["resolved"] == 1 and d2["counts"]["not_filled"] == 1          # #2 starts after the jump → never touches 100
    assert conn.execute("SELECT done FROM app_signals WHERE signal_id='1'").fetchone()[0] == 1
    api[aps.API_HISTORY]["body"][0] = _api_item(1, sl="90")                              # the server edits the stop → recompute
    aps.upsert(conn, {"closed": api[aps.API_HISTORY]["body"]}, now + 2 * H1, active_ok=False)
    assert conn.execute("SELECT done, sim FROM app_signals WHERE signal_id='1'").fetchone() == (0, None)


def test_stop_then_back_to_tp1_within_7_days():
    conn = sqlite3.connect(":memory:")
    bars = _bars([(100.0, 100.5, 99.5, 100.2), (100.2, 100.4, 94.0, 95.0)]) + _flat(96.0, 96 * 2, P0 + 2 * M15)
    bars += _bars([(96.0, 106.0, 95.9, 105.5)], start=bars[-1][0] + M15)               # day ~2: back above TP1 = 105
    api = {aps.API_ACTIVE: {"code": 1, "body": []}, aps.API_HISTORY: {"code": 1, "body": [_api_item(1)]}}
    d = aps.run(conn, now=P0 + 5 * DAY, fetch_json=lambda u: api[u], klines=_market({"BTC-USDT": bars}), budget_s=30, max_requests=50)
    assert d["counts"]["resolved"] == 1
    sim = json.loads(conn.execute("SELECT sim FROM app_signals").fetchone()[0])
    assert sim["ended"] == "sl" and sim["after_tp1"] is True
    assert conn.execute("SELECT done FROM app_signals").fetchone()[0] == 1           # answer known before the 7 days end
    assert d["profile"]["stop_then_tp1"] == {"n": 1, "tp1_after_sl": 1}
