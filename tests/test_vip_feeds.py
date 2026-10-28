"""28.10.2026 — the closed panel's VIP feeds (cnq/agents/vip_feeds.py), the pipeline's state guard (scripts/ci_state_guard.py) and the
stats-only «Иван тест» summary (cnq/execution/ivan_watch.public_stats)."""
import importlib.util
import json
import pathlib
import re
import sqlite3

from cnq.agents import vip_feeds as vf
from cnq.execution import ivan_watch as iw

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOW = 1793180000000          # 28.10.2026 ~08:13 UTC
DAY = 86_400_000


def _guard():
    spec = importlib.util.spec_from_file_location("ci_state_guard", ROOT / "scripts" / "ci_state_guard.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _pf(positions, not_bought, actions, cash=25.7, fresh=True):
    return {"asof": NOW, "portfolios": {"ideas": {
        "positions": positions, "not_bought": not_bought, "recent_actions": actions, "cash_pct": round(cash),
        "sleeves": {"cash": {"now_pct": cash, "target_pct": 10.0}}, "ideas": {"fresh": fresh, "note_ru": None if fresh else "таблица устарела"}}}}


POS = [{"symbol": "BTC", "weight": 0.33, "status": "OPEN", "role": "buy", "bucket": "core", "entry_time": NOW - 10 * DAY,
        "reco": {"t": "Держать", "why": "ядро"}},
       {"symbol": "PENGU", "weight": 0.026, "status": "OPEN", "role": "small", "bucket": "meme", "entry_time": NOW - 10 * DAY}]
NB = [{"symbol": "XMR", "status": "buy", "why_ru": "на споте OKX нет пары — портфель повторяет реальный счёт CRYPTO NOVA на OKX"},
      {"symbol": "NEAR", "status": "buy", "why_ru": "продана 24.10 — снова не раньше 07.11"}]
ACTS = [{"time": NOW - 4 * DAY, "symbol": "NEAR", "action": "SELL", "reason": "Таблица поставила «не брать»: итог −14% от входа."},
        {"time": NOW - 6 * DAY, "symbol": "XMR", "action": "SELL", "reason": "Правило с 22.10: … спот-парой на OKX …"}]
IDEAS = {"asof": NOW, "rows": [{"symbol": "BTC", "status": "buy", "status_ru": "можно брать", "why_ru": "ядро", "kind": "core"},
                               {"symbol": "XMR", "status": "buy", "status_ru": "можно брать", "why_ru": "анкета 52", "kind": "mid"},
                               {"symbol": "NEAR", "status": "buy", "status_ru": "можно брать", "why_ru": "анкета 43", "kind": "mid"},
                               {"symbol": "LTC", "status": "buy", "status_ru": "можно брать", "why_ru": "анкета 45", "kind": "mid"},
                               {"symbol": "WIF", "status": "avoid", "status_ru": "не брать", "why_ru": "мёртвая", "kind": "meme"}],
         "statuses": {"PUMP": {"status": "wait", "why_ru": "Мем без проверки мем-деска — не берём вслепую", "kind": "meme"},
                      "AAVE": {"status": "wait", "why_ru": "Анкета 39/100 ниже порога 40 — ждём", "kind": "mid"},
                      "PENGU": {"status": "wait", "why_ru": "−3% от максимума", "kind": "meme"}}}
REAL = {"asof": NOW, "since": NOW - 6 * DAY, "spot": [{"sym": "BTC", "usd": 2188.6}, {"sym": "PENGU", "usd": 165.7}],
        "alloc": {"spot_coins_usd": 4779.37, "spot_cash_usd": 1691.33, "active_wait_usd": 991.26, "fut_reserve_usd": 2450.62, "fut_margin_usd": 0.0},
        "usdt_pct": 51.78, "actions": [{"t": NOW - 4 * DAY + 60_000, "kind": "spot_sell", "sym": "NEAR", "usd": 431.24, "px": 4.394}]}


def _by(doc):
    return {c["sym"]: c for c in doc["coins"]}


def test_spot_pipeline_stages_and_reasons_come_from_the_data():
    d = vf.spot_pipeline(_pf(POS, NB, ACTS), IDEAS, {}, REAL, None, {"BTC", "PENGU", "NEAR", "LTC", "PUMP", "AAVE", "WIF"}, [], NOW)
    c = _by(d)
    assert c["BTC"]["stage"] == "bought" and c["BTC"]["in_real_account"] and c["BTC"]["since"] == NOW - 10 * DAY
    assert c["XMR"]["stage"] == "rejected" and c["XMR"]["okx_pair"] is False and "на споте OKX нет пары" in c["XMR"]["why_ru"]
    assert c["NEAR"]["stage"] == "waiting" and "07.11" in c["NEAR"]["why_ru"]
    assert c["NEAR"]["since"] == NOW - 4 * DAY                                    # the sale, not the first time the feed saw it
    assert c["NEAR"]["last_action"]["src"] == "real"                               # the real account's sale is the newest
    assert c["LTC"]["stage"] == "recommended" and c["LTC"]["passed"][-1] == "recommended"
    assert c["WIF"]["stage"] == "rejected" and c["PUMP"]["stage"] == "found" and c["AAVE"]["stage"] == "first_check"
    assert sum(d["counts"].values()) == len(d["coins"])
    assert [s["key"] for s in d["stages"]] == list(vf.STAGES)


def test_spot_pipeline_stale_table_is_not_a_recommendation():
    d = vf.spot_pipeline(_pf(POS, [], [], fresh=False), IDEAS, {}, REAL, None, None, [], NOW)
    assert _by(d)["LTC"]["stage"] == "all_filters" and "таблица устарела" in _by(d)["LTC"]["why_ru"]


def test_spot_pipeline_since_is_kept_while_the_stage_stays():
    prev = {"coins": [{"sym": "AAVE", "stage": "first_check", "since": NOW - 3 * DAY}, {"sym": "PUMP", "stage": "rejected", "since": 1}]}
    c = _by(vf.spot_pipeline(_pf(POS, NB, ACTS), IDEAS, {}, REAL, prev, None, [], NOW))
    assert c["AAVE"]["since"] == NOW - 3 * DAY and c["PUMP"]["since"] == NOW                       # PUMP changed stage → now


def test_cash_block_floor_not_target_and_real_numbers():
    d = vf.spot_pipeline(_pf(POS, NB, ACTS), IDEAS, {}, REAL, None, None, [], NOW)
    assert d["cash_pct_real"] == 26.1 and d["cash_pct_portfolio"] == 25.7
    assert d["cash_target_pct"] == 10.0 and d["cash_target_kind"] == "minimum"
    assert d["cash_free_slots"] == {"buy": 7, "buy_total": 8, "small": 2, "small_total": 3}
    w = d["cash_why_ru"]
    assert "нижняя граница" in w and "NEAR" in w and "07.11" in w
    assert "XMR 2" not in w.split("Кэш вырос после продаж")[-1].split("Кандидаты")[0]   # the 22.10 OKX-rule sale went to the core
    assert d["account_usdt_parts"]["active_wait_usd"]["usd"] == 991.26


def test_unknown_reason_is_said_plainly():
    d = vf.spot_pipeline(_pf(POS, [{"symbol": "ZZZ", "status": "buy", "why_ru": ""}], []), {"asof": NOW, "rows": []}, {}, REAL, None, None, [], NOW)
    assert vf.UNKNOWN_RU in _by(d)["ZZZ"]["why_ru"]


PAPER = {"asof": NOW, "profiles": {
    "aggr": {"name": "Агрессивная", "start_equity": 10000, "equity": 10215.05, "open": [], "stats": {"trades": 8}, "since": NOW - 10 * DAY},
    "imp": {"name": "Импульс BTC", "start_equity": 10000, "equity": 10000, "open": [], "stats": {"trades": 0}},
    "bal": {"name": "Сбалансированная", "start_equity": 10000, "equity": 10133.19, "open": [], "stats": {"trades": 8}},
    "brk": {"name": "Пробой", "start_equity": 10000, "equity": 9591.99, "open": [], "stats": {"trades": 5}},
    "lst": {"name": "Шорт листингов", "start_equity": 10000, "equity": 10000, "open": [], "stats": {"trades": 0}}}}
CONV = {"desks": [{"key": "aggr", "trades": 8, "winrate_pct": 62.5, "mean_pct": 0.773, "max_dd_pct": 2.75, "since": NOW - 10 * DAY,
                   "reason_ru": "мало сделок: 8 из 30", "evidence": "partial", "evidence_ru": "частичная"},
                  {"key": "bal", "trades": 8, "winrate_pct": 62.5, "mean_pct": 0.773, "max_dd_pct": 2.11},
                  {"key": "brk", "trades": 5, "winrate_pct": 0.0, "mean_pct": -4.079, "max_dd_pct": 5.38, "evidence": "failed",
                   "evidence_ru": "по текущему правилу лаборатории НЕ проходит"},
                  {"key": "imp", "trades": 0, "winrate_pct": None, "mean_pct": None, "max_dd_pct": 0.0}],
        "lab": [{"id": "doc:x", "name_ru": "Гэп FX", "refuted_by": None, "recommend_paper": False, "reason_ru": "старше 2 лет"}]}
SEL = {"current": "aggr", "name": "Агрессивная", "since": NOW - 3600_000, "by": "owner", "decision": "hold", "short_ru": "ждёт 20 сделок",
       "candidates": [{"key": "bal", "why": "мало сделок: 8 из 20"}, {"key": "aggr", "why": "текущий основной стол"}]}


def test_strategies_one_status_set_and_real_money_only_for_active():
    d = vf.strategies(PAPER, CONV, SEL, _pf(POS, [], []), {}, {}, {}, {}, {}, {"asof": NOW - 6 * DAY}, None, REAL, NOW)
    S = {x["key"]: x for x in d["strategies"]}
    assert S["aggr"]["status"] == "Active" and S["aggr"]["real_money"] and S["aggr"]["category"] == "used"
    assert S["imp"]["status"] == "Active" and S["imp"]["win_rate"] is None and S["imp"]["avg_trade_pct"] is None
    assert S["bal"]["status"] == "Validation" and not S["bal"]["real_money"]
    assert S["brk"]["status"] == "Testing" and any("НЕ проходит" in i for i in S["brk"]["issues_ru"])
    assert S["lst"]["status"] == "Testing" and S["lst"]["sample_note_ru"].startswith("Закрытых сделок ещё нет")
    assert S["spot_ideas"]["status"] == "Active" and S["spot_ideas"]["max_dd_pct"] is None      # no full ledger → no drawdown
    assert S["ivan"]["status"] == "Testing" and S["ivan"]["trades"] is None and not S["ivan"]["real_money"]
    assert S["lab_doc:x"]["status"] == "In Development"
    assert {x["status"] for x in d["strategies"]} <= set(vf.STATUS_RU)
    cats = [vf._C_ORDER[x["category"]] for x in d["strategies"]]
    assert cats == sorted(cats)                                                                      # stable category order
    assert d["real_account"]["source"] == "public_real.json"


def test_strategies_ivan_stats_only_summary_shows_the_sample():
    st = {"v": 1, "asof": NOW, "closed_n": 3, "wins": 3, "losses": 0, "win_rate": 100.0, "net_closed_usd": 12.5, "open_n": 2}
    S = {x["key"]: x for x in vf.strategies(PAPER, CONV, SEL, {}, {}, {}, {}, {}, {}, {}, st, REAL, NOW)["strategies"]}
    assert S["ivan"]["trades"] == 3 and S["ivan"]["win_rate"] == 100.0
    assert "100% · 3 закрытых сделок" in S["ivan"]["sample_note_ru"] and "открыто 2" in S["ivan"]["sample_note_ru"]


def test_spot_drawdown_from_the_full_ledger():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE spot_ideas_value(time INTEGER PRIMARY KEY, index_value REAL)")
    conn.executemany("INSERT INTO spot_ideas_value VALUES(?,?)", [(NOW - 5 * DAY, 100.0), (NOW - 4 * DAY, 110.0), (NOW - 3 * DAY, 99.0),
                                                                   (NOW - 2 * DAY, 105.0)])
    pf = {"asof": NOW, "portfolios": {"ideas": {"index": 105.0, "start_usd": 10000, "started": NOW - 5 * DAY, "positions": []}}}
    S = {x["key"]: x for x in vf.strategies({}, {}, {}, pf, {}, {}, {}, {}, {}, {}, None, {}, NOW, conn)["strategies"]}
    assert S["spot_ideas"]["max_dd_pct"] == 10.0 and S["spot_ideas"]["net_usd"] == 500.0


def test_opportunities_never_mix_test_and_real():
    d = vf.opportunities({"week": {"virtual_pnl_usd": 101.99, "windows": 117}, "note": "выборка"}, {"week": {"virtual_pnl_usd": 41.07}},
                         {"start_equity": 10000, "equity_mtm": 10002.52, "positions": [1, 2]}, {}, {}, PAPER,
                         {"copiers": 0, "asof": NOW}, {"on": False, "why_off": "CNQ_OKX_SIGNAL не равен on"}, NOW)
    O = {x["key"]: x for x in d["items"]}
    assert O["tri"]["result_test_usd"] == 101.99 and O["tri"]["result_real_usd"] is None and O["tri"]["status"] == "Monitoring"
    assert O["funding"]["result_test_usd"] == 2.52 and O["funding"]["result_real_usd"] is None
    assert O["polymarket"]["status"] == "Not Active" and O["polymarket"]["result_test_usd"] is None
    assert O["okx_signal"]["status"] == "Not Active" and "CNQ_OKX_SIGNAL" in O["okx_signal"]["why_not_used_ru"]
    assert O["okx_copy"]["status"] == "Active" and O["okx_copy"]["ops"] == 0
    assert all(x["status"] in vf.OPP_STATUS_RU for x in d["items"])


def test_system_status_from_real_timestamps(tmp_path):
    w = tmp_path / "web"
    w.mkdir()
    (w / "engine.json").write_text(json.dumps({"asof": NOW - 2 * 60_000, "cycle": 5, "cycle_sec": 58.0, "interval_sec": 60, "errors": [],
                                               "steps": {"candles": {"ok": True, "sec": 30}}}))
    (w / "public_real.json").write_text(json.dumps({"asof": NOW - 50 * 60_000}))
    (w / "ivan.enc.json").write_text(json.dumps({"asof": NOW - 6 * DAY}))
    lease = {"node": "server", "holder": "server", "until": NOW + 10 * 60_000, "t": NOW - 60_000, "stop": False}
    d = vf.system_status(str(w), NOW, lease, (200, {"ok": True}), None, (NOW - 30 * 60_000, "28.10 — тихо"),
                         {"stale": True, "why_ru": "база отстаёт", "db_t": NOW - 8 * DAY, "checked": NOW})
    C = {c["key"]: c for c in d["components"]}
    assert C["engine"]["status"] == "ok" and "почти не укладывается" in C["engine"]["note_ru"]
    assert C["autopilot"]["status"] == "ok" and C["api"]["status"] == "ok" and C["api"]["last_ok"] == NOW
    assert C["public_real"]["status"] == "stale" and C["mac_mirror"]["status"] == "stopped"
    assert C["state"]["status"] == "error" and C["researcher"]["status"] == "ok"
    assert C["site_pipeline"]["status"] == "error"                       # no status.json / version.json at all → no timestamp
    assert d["overall"] == "error" and all(c["status"] in ("ok", "stale", "error", "stopped") for c in d["components"])
    stopped = vf.system_status(str(w), NOW, {**lease, "stop": True}, None, None, (None, ""))
    assert {c["key"]: c for c in stopped["components"]}["autopilot"]["status"] == "stopped"


def test_run_writes_the_four_files(tmp_path):
    w = tmp_path / "web"
    w.mkdir()
    for name, doc in (("portfolio.json", _pf(POS, NB, ACTS)), ("ideas.json", IDEAS), ("public_real.json", REAL), ("paper.json", PAPER),
                      ("conveyor.json", CONV), ("desk_select.json", SEL)):
        (w / name).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    docs = vf.run(str(w), None, NOW, http=False, watch_path=str(tmp_path / "none.md"), guard_path=str(tmp_path / "none.json"))
    for name in vf.FILES:
        assert json.loads((w / name).read_text(encoding="utf-8"))["asof"] == NOW
    assert docs["spot_pipeline.json"]["cash_pct_real"] == 26.1


# ── the state guard ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_state_guard_blocks_a_rolled_back_database(tmp_path):
    g = _guard()
    assert g.verdict(NOW - 30 * 60_000, NOW, NOW)["stale"] is False                 # one run behind is normal
    v = g.verdict(NOW - 69 * 3_600_000, NOW, NOW)                                   # the 23.10 incident: 20.10 copy vs a fresh model
    assert v["stale"] is True and v["lag_h"] == 69.0
    assert g.verdict(None, NOW, NOW)["stale"] is True                               # no ledger but a published portfolio
    assert g.verdict(None, None, NOW)["stale"] is False                             # nothing published yet
    db = tmp_path / "a.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE spot_ideas_value(time INTEGER PRIMARY KEY, index_value REAL)")
    c.execute("INSERT INTO spot_ideas_value VALUES(?, 100)", (NOW - DAY,))
    c.commit()
    c.close()
    pf = tmp_path / "portfolio.json"
    pf.write_text(json.dumps({"portfolios": {"ideas": {"history": [{"time": NOW, "v": 99}], "history_all": [{"time": NOW - 2 * DAY, "v": 100}]}}}))
    assert g.db_last(str(db)) == NOW - DAY and g.published_last(str(pf)) == NOW


def _site():
    return (ROOT / ".github" / "workflows" / "site.yml").read_text(encoding="utf-8")


def _paths_ignore() -> set:
    s = _site()
    block = s[s.index("paths-ignore:"):s.index("schedule:")]
    return set(re.findall(r'"([^"]+)"', block))


def test_site_pipeline_skips_the_portfolio_on_a_stale_state():
    s = _site()
    i_restore, i_guard = s.index("- name: Restore candle store"), s.index("- name: State guard")
    step = s[s.index("- name: Spot portfolio department"):]
    step = step[:step.index("- name:", 10)]
    assert i_restore < i_guard and "id: state" in s[i_guard:i_guard + 300] and "scripts/ci_state_guard.py" in s[i_guard:i_guard + 300]
    assert "steps.state.outputs.stale != 'true'" in step
    assert {"web/okx_signal.json", "web/buy_now.json"} <= _paths_ignore()          # engine-owned files never restart this pipeline


def test_engine_owned_files_never_restart_the_site_pipeline():
    from cnq import engine
    ign = _paths_ignore()
    assert {"web/" + f for f in engine.OWNED} <= ign
    assert {"data/" + f for f in engine.DATA_OWNED if "." in f} <= ign


# ── «Иван тест»: stats only ─────────────────────────────────────────────────────────────────────────────────────────
def test_ivan_public_stats_has_no_account_information():
    out = {"asof": NOW, "since": NOW - 9 * DAY, "host": "demo", "account": "UNIFIED", "equity": 10234.5, "wallet": 10100.0, "available": 9000.0,
           "positions": [{"symbol": "SOL", "size": 5, "entry": 150, "upnl": -40.0}], "upnl": -40.0, "max_dd": 0.031,
           "closed": [{"symbol": "BTC", "pnl": 5.0}],
           "stats": iw.stats([{"symbol": "BTC", "pnl": 5.0, "fee": 0.2, "close_time": NOW - DAY},
                              {"symbol": "ETH", "pnl": 3.0, "fee": 0.1, "close_time": NOW - DAY},
                              {"symbol": "ETH", "pnl": 0.0, "fee": None, "close_time": NOW - DAY}])}
    s = iw.public_stats(out)
    assert set(s) <= iw.STATS_KEYS
    assert (s["closed_n"], s["wins"], s["losses"], s["breakeven"], s["win_rate"]) == (3, 2, 0, 1, 66.7)   # breakeven in the denominator
    assert s["fees_usd"] == 0.3 and s["fees_known"] == 2 and s["open_n"] == 1 and s["max_dd_pct"] == 3.1
    txt = json.dumps(s, ensure_ascii=False)
    for secret in ("10234.5", "UNIFIED", "demo", "SOL", "available", "equity"):
        assert secret not in txt
    try:
        iw.publish_stats({**s, "equity": 1}, "tok", "o/r", http=lambda *a: (200, {}))
        raise AssertionError("a field outside the summary must be refused")
    except ValueError:
        pass
    sent = []

    def http(method, url, hdr, data=None):
        sent.append((method, url, data))
        return (200, {"sha": "x"}) if method == "GET" else (201, {})
    assert iw.publish_stats(s, "tok", "o/r", http=http) is True and sent[-1][1].endswith("/contents/web/ivan_stats.json")
