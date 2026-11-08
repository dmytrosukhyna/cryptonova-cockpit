"""Added whale sources (04.11): Binance lead portfolios read from recorded runner answers; Hyperliquid's 30-day slice."""
import json
import os
import sqlite3

from cnq.agents import whale_sources as WS
from cnq.agents import whale_study as W

FX = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "whale_sources_binance.json"), encoding="utf-8"))
NOW = 1793810000000
DAY, H = W.DAY, W.H1


def fake_bn(hist_rows=None, perf=None, detail=None):
    calls = []

    def get(path, body=None):
        calls.append(path)
        if path.endswith("home-page/query-list"):
            return FX["list_30d"] if body["pageNumber"] == 1 and body["timeRange"] == "30D" else {"list": []}
        pid = path.split("portfolioId=")[-1].split("&")[0] if "portfolioId=" in path else (body or {}).get("portfolioId")
        if "lead-portfolio/detail" in path:
            return (detail or FX["detail"]).get(pid, {"status": "ACTIVE", "positionShow": False})
        if "lead-data/positions" in path:
            return FX["positions"].get(pid, [])
        if "lead-portfolio/performance" in path:
            return (perf or FX["performance"])[path.split("timeRange=")[1]]
        if "position-history" in path:
            assert "/friendly/" in path                     # 04.11: the «public» path answers an empty list
            rows = hist_rows if hist_rows is not None else FX["history_rows"]
            return {"total": len(rows), "list": rows[(body["pageNumber"] - 1) * 50: body["pageNumber"] * 50]}
        raise AssertionError(path)
    get.calls = calls
    return get


def test_binance_candidates_keep_profitable_high_win_portfolios_that_show_positions():
    W.time.sleep = WS.time.sleep = lambda s: None
    rows, why = WS.bn_candidates(fake_bn())
    assert [r["addr"] for r in rows] == ["bn:5278353437811897601"]
    assert why["loss"] == 1 and why["win"] == 1 and why["hidden"] == 1 and why["passed"] == 1
    r = rows[0]
    assert r["src"] == "bn" and r["name"] == "兔子来了" and abs(r["win_ratio"] - 0.755556) < 1e-6 and r["pnl_m"] > 49000


def test_binance_positions_from_recorded_rows():
    st = {"bn": [x for x in FX["positions"]["5278353437811897601"] if float(x["positionAmount"])]}
    pos = WS.bn_positions(st)
    assert [(p["coin"], p["side"], p["lev"]) for p in pos] == [("BTC", "SHORT", 100.0), ("XAU", "LONG", 20.0)]
    assert pos[0]["notional"] == 321618 and pos[0]["entry"] == 80546.1
    assert W.hidden(WS.bn_positions({"bn": None, "hidden": True}))           # hidden → the desk neither copies nor closes
    tr = WS.bn_open_trips(st, NOW)
    assert {t["coin"] for t in tr} == {"BTC", "XAU"} and all(t["open"] for t in tr)


def test_record_gate_adds_only_the_visibility_check():
    # 04.11 17:46: the record bar (60 d, ≥ 20 decisions, ≥ 70 % / PF ≥ 2 incl. open, hold ≥ 4 h) is common — whale_study.profile
    ok = {"win": 0.75, "pf": 2.0, "positions": [{"coin": "BTC"}]}
    assert WS.record_gate(ok, 25) == [] and WS.record_gate({**ok, "win": 0.6}, 5) == []
    assert "скрывает" in WS.record_gate({**ok, "positions": [{"coin": ""}]}, 25)[0]


def test_hyperliquid_candidates_add_the_best_of_the_month():
    def row(addr, al, mo):
        return {"ethAddress": addr, "accountValue": "2000000", "displayName": None,
                "windowPerformances": [["day", {"pnl": "0"}], ["week", {"pnl": "1"}], ["month", {"pnl": str(mo), "roi": "0.2", "vlm": "1e7"}],
                                       ["allTime", {"pnl": str(al)}]]}
    lb = {"leaderboardRows": [row("0xold", 9e7, 1e3), row("0xnew", 6e5, 4e6), row("0xmid", 5e6, 1e4)]}
    rows, why = W.candidates(lb, n=2)
    assert [r["addr"] for r in rows] == ["0xold", "0xmid", "0xnew"] and rows[2]["pick"] == "month" and why["month_slice"] == 1


def _hist(n_win=20, n_loss=3):
    """recorded row shape, repeated: 4-hour BTC longs inside the last 30 days (wins +3 %, losses −1 %)"""
    base = FX["history_rows"][1]
    out = []
    for k in range(n_win + n_loss):
        ot = NOW - 28 * DAY + k * DAY
        win = k < n_win
        out.append({**base, "id": ot, "symbol": "BTCUSDT", "side": "Long", "opened": ot, "closed": ot + 4 * H, "avgCost": 100.0,
                    "avgClosePrice": 103.0 if win else 99.0, "closingPnl": 300.0 if win else -100.0, "maxOpenInterest": 10.0})
    return out


def _hl_post(now):
    base = (now - 33 * DAY) // W.M15 * W.M15

    def post(body):
        t = body["type"]
        if t == "allMids":
            return {"BTC": "105", "ETH": "2400"}
        if t == "candleSnapshot":
            out = []
            for i in range(3300):
                tt = base + i * W.M15
                o = 100 + 0.01 * i                                    # a steady uptrend: a late copy of a long still earns
                c = o + 0.01
                out.append({"t": tt, "o": str(o), "h": str(max(o, c)), "l": str(min(o, c)), "c": str(c)})
            return out
        raise AssertionError(t)
    return post


def test_run_studies_binance_portfolios_and_gives_them_a_30_day_record():
    W.time.sleep = WS.time.sleep = lambda s: None
    conn = sqlite3.connect(":memory:")
    get = fake_bn(hist_rows=_hist())
    d = W.run(conn, now=NOW, post=_hl_post(NOW), get_lb=lambda: {"leaderboardRows": []}, okx_get=lambda p, q: [{"ranks": []}] if "lead-traders" in p else [],
              max_requests=80, budget_s=60, src_get={"bn": get})
    assert d["funnel"]["ext"]["bn"]["after_filters"] == 1 and d["funnel"]["studied"] == 1
    p = (d["top"] + d["others"])[0]
    assert p["src"] == "bn" and p["src_ru"].startswith("Binance") and p["addr"] == "bn:5278353437811897601"
    # the record fields real_why.json reads (record_30d)
    # 23 closed decisions + the 2 open positions at the mark (both in profit now)
    assert p["trips"] == 25 and abs(p["win"] - 22 / 25) < 1e-3 and p["open_trips"] == 2 and p["pf"] >= 1.5 and p["hold_med_h"] == 4.0 and p["style_ru"] == "интрадей"
    assert p["trades_90d"] == 90 and p["eligible"] and p in d["top"]
    assert {x["coin"] for x in p["positions"]} == {"BTC", "XAU"}
    assert d["following"] and d["following"][0]["addr"] == "bn:5278353437811897601"
    q = W.kv_get(conn, "quality")["bn:5278353437811897601"]
    assert q["win"] == p["win"] and q["pf"] == p["pf"]
    # next run: the desk reads his live positions through the same source (a stale look → baseline only)
    d2 = W.run(conn, now=NOW + 1800_000, post=_hl_post(NOW), get_lb=lambda: {"leaderboardRows": []},
               okx_get=lambda p, q: [{"ranks": []}] if "lead-traders" in p else [], max_requests=80, budget_s=60, src_get={"bn": get})
    assert d2["desk"] is not None
    assert any("lead-data/positions" in c for c in get.calls[-6:])
    json.dumps(d2)


def test_run_does_not_follow_a_binance_portfolio_under_the_bar():
    W.time.sleep = WS.time.sleep = lambda s: None
    conn = sqlite3.connect(":memory:")
    perf = {k: {**v, "totalOrder": 12} for k, v in FX["performance"].items()}
    get = fake_bn(hist_rows=_hist(n_win=6, n_loss=6), perf=perf)
    d = W.run(conn, now=NOW, post=_hl_post(NOW), get_lb=lambda: {"leaderboardRows": []}, okx_get=lambda p, q: [{"ranks": []}] if "lead-traders" in p else [],
              max_requests=80, budget_s=60, src_get={"bn": get})
    o = d["others"][0]
    assert o["src"] == "bn" and not d["top"] and not [x for x in d["following"] if not x.get("owner_pick")]
    ex = " ".join(o["excluded_ru"])
    assert "нужно от 70%" in ex and "мало решений за 60 дней (14" in ex


# ------------------------------------------------------------------------------------------------ 04.11 21:14: wider pools, same bar
def test_okx_pool_reads_all_pages_and_both_sorts_then_takes_pnl_best_plus_win_best():
    W.time.sleep = lambda s: None
    calls = []

    def okx_get(path, params):
        calls.append((params["sortType"], params["page"]))
        if params["sortType"] == "pnl":
            base = (params["page"] - 1) * 20
            ranks = [{"uniqueCode": f"P{base + i}", "nickName": f"p{base + i}", "pnl": str(1e6 - (base + i) * 1000), "winRatio": "0.6"}
                     for i in range(20 if params["page"] < 4 else 7)]
            ranks[0]["winRatio"] = "0.3"                                     # cannot reach 70 % decisions
            return [{"totalPage": "4", "ranks": ranks}]
        return [{"totalPage": "1", "ranks": [{"uniqueCode": "WIN", "nickName": "w", "pnl": "100", "winRatio": "0.95"},
                                             {"uniqueCode": "P5", "nickName": "dup", "pnl": "1", "winRatio": "0.6"}]}]
    rows, why = W.okx_candidates(okx_get)
    assert ("pnl", 4) in calls and ("pnl", 5) not in calls and ("win_ratio", 1) in calls
    assert why["rows"] == 68 and why["win"] == 4 and why["studied"] == len(rows) == W.OKX_PRE_N
    assert [r["code"] for r in rows[:2]] == ["P1", "P2"] and rows[W.OKX_PRE_PNL]["code"] == "WIN"


def test_binance_pool_reads_pnl_and_roi_sorts():
    get = fake_bn()
    WS.bn_candidates(get)
    assert sum(1 for c in get.calls if c.endswith("home-page/query-list")) == 4      # PNL/ROI × 30D/90D, empty pages stop early


def test_run_reports_per_source_counts_and_the_sources_not_connected():
    W.time.sleep = WS.time.sleep = lambda s: None
    conn = sqlite3.connect(":memory:")
    d = W.run(conn, now=NOW, post=_hl_post(NOW), get_lb=lambda: {"leaderboardRows": []}, okx_get=lambda p, q: [{"ranks": []}] if "lead-traders" in p else [],
              max_requests=80, budget_s=60, src_get={"bn": fake_bn(hist_rows=_hist())})
    assert d["by_source"]["bn"] == {"studied": 1, "passed": 1, "following": 1} and d["by_source"]["okx"]["studied"] == 0
    assert {"bybit", "bitget", "gate", "mexc", "bingx"} <= set(d["sources_off"])


def test_a_whale_dropped_by_our_bug_is_reinstated_once_if_he_passes_the_bar_with_his_join_date(tmp_path, monkeypatch):
    """08.11: 乾坤策略量化 was dropped as «нет данных» by our candidate-cut bug — back once, only if he passes the full bar now"""
    W.time.sleep = WS.time.sleep = lambda s: None
    addr = "bn:5278353437811897601"
    cfg = tmp_path / "re.json"
    cfg.write_text(json.dumps({"id": "re-1", "why_ru": "ошибка системы", "addrs": {addr: {"name": "X", "joined": NOW - 2 * DAY}}}))
    monkeypatch.setattr(W, "REINSTATE", str(cfg))
    conn = sqlite3.connect(":memory:")
    W.ensure_schema(conn)
    W.kv_set(conn, "study", {"t": NOW - 3600_000, "rule": W.LOCK_RULE, "lock_t": NOW - 3600_000, "top": [], "passing": {}, "joined": {}})
    kw = dict(post=_hl_post(NOW), get_lb=lambda: {"leaderboardRows": []}, okx_get=lambda p, q: [{"ranks": []}] if "lead-traders" in p else [],
              max_requests=80, budget_s=60)
    d = W.run(conn, now=NOW, src_get={"bn": fake_bn(hist_rows=_hist())}, **kw)
    assert d["reinstate"][addr]["ok"] and d["reinstate"][addr]["joined"] == NOW - 2 * DAY
    assert addr in [x["addr"] for x in d["following"]] and d["follow_lock"]["joined"][addr] == NOW - 2 * DAY
    assert W.reinstate_pending(conn, W.reinstate_cfg()) == {}                         # once only
    # a whale who fails the bar is not reinstated (and stays pending)
    conn2 = sqlite3.connect(":memory:")
    W.ensure_schema(conn2)
    W.kv_set(conn2, "study", {"t": NOW - 3600_000, "rule": W.LOCK_RULE, "lock_t": NOW - 3600_000, "top": [], "passing": {}, "joined": {}})
    d2 = W.run(conn2, now=NOW, src_get={"bn": fake_bn(hist_rows=_hist(n_win=6, n_loss=6))}, **kw)
    assert not d2["reinstate"][addr]["ok"] and addr not in [x["addr"] for x in d2["following"]]
    assert W.reinstate_pending(conn2, W.reinstate_cfg()) != {}
