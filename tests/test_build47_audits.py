"""Build 47 (28.10.2026) — the audits turned into display data, no trading rule touched:
§6.4 public_portfolio.moved (the «перевод между спотом и фьючерсами» is a re-split of one USDT balance, not a transfer or P&L),
§6.5 the leverage notes (real: okx_futures caps; demo: the signals' own labels), §12.1 the real state of every «Если покупать
сегодня» coin in the spot portfolio, §15 the spoken report says when a part of the system is not working, §13.6 the connection
states, §8.4 NOVA keeps tickers / pairs / Long · Short · Stop Loss · Take Profit and the meaning of numbers in every language.
Every amount is made-up test data shaped like web/public_real.json of 28.10."""
import json
import os
import sqlite3

from cnq.agents import app_home, i18n
from cnq.api import coins as C
from cnq.api import nova
from test_public_home import T0
from test_public_real import _pp_files

H = 3_600_000
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _real(since, now, **kw):
    """the 28.10 real account in miniature: start $10 024,40, value $9 925,07, futures +$0,27, no open position"""
    d = {"exchange": "OKX", "since": since, "start_usd": 10024.4, "equity_usd": 9925.07, "pnl_usd": -99.33, "pnl_pct": -0.99,
         "today_usd": 50.91, "today_pct": 0.52, "spot": [{"sym": "BTC", "share": 22.07, "usd": 2190.84}, {"sym": "ZEC", "share": 26.21, "usd": 2601.02}],
         "usdt_pct": 51.72, "usdt_usd": 5133.21,
         "alloc": {"spot_coins_usd": 4791.86, "spot_cash_usd": 1693.47, "fut_budget_usd": 2499.07, "fut_margin_usd": 0.0,
                   "fut_reserve_usd": 2447.24, "active_coins_usd": 0, "active_wait_usd": 992.51, "other_usd": -0.01, "spot_pct": 65.0,
                   "fut_pct": 25.0, "active_pct": 10.0},
         "futures": [], "closed_n": 1, "curve": [[since, 0.0], [now - 60_000, -99.33]], "asof": now - 60_000, "stale": False,
         "copiers": 0, "copy_url": None, "spot_pnl_usd": -99.6, "fut_pnl_usd": 0.27,
         "closed": [{"inst": "APT-USDT-SWAP", "side": "long", "open_px": 0.7747, "close_px": 0.776, "move_pct": 0.17, "pnl_usd": 0.27,
                     "lev": 2.0, "opened": since - H, "closed": since + 18 * H, "partial": False}],
         "fut_curve": [[now - 60_000, 0.27]], "actions": [],
         "flows": {"in_usd": 0.0, "out_usd": 0.0, "net_usd": 0.0, "n": 0, "last_t": None, "events": [], "checked_t": now - 60_000}}
    d.update(kw)
    return d


def _w(d, name, obj):
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _lang(d, lang):
    return json.load(open(os.path.join(d, i18n.file_name("app_home", lang)), encoding="utf-8"))


def test_spot_futures_transfer_is_a_reallocation_not_a_transfer_or_pnl(tmp_path):
    d = str(tmp_path)
    since = T0 + 100 * H
    now = since + 6 * 24 * H
    _pp_files(d, _real(since, now))
    pp = app_home.run(sqlite3.connect(":memory:"), d, now=now)["public_portfolio"]
    m = pp["moved"]
    # futures money now 2 447,24 vs its base 25 % × 10 024,40 = 2 506,10 + its result 0,27 → −59,13 =
    #   rebase 25 % × 9 925,07 − 2 506,37 = −25,10 (its share of the account's change) + drift 2 447,24 − 2 481,27 = −34,03
    assert (m["usd"], m["dir"], m["fut_usd"], m["spot_usd"]) == (59.13, "futures→spot", -59.13, 59.13)
    assert (m["rebase_usd"], m["drift_usd"]) == (-25.1, -34.03) and abs(m["rebase_usd"] + m["drift_usd"] - m["fut_usd"]) <= 0.01
    assert m["is_pnl"] is False and m["kind"] == "allocation" and m["t"] == now - 60_000
    assert m["why_ru"].startswith("Это не перевод денег, не комиссия и не прибыль или убыток.")
    assert "на $59,13 меньше" in m["why_ru"] and "−$25,10" in m["why_ru"] and "−$34,03" in m["why_ru"] and "спот 65%" in m["why_ru"]
    # the parts: the trading result stays each part's own, the moved money is no one's result
    F, S = pp["futures"], pp["spot"]
    assert F["pnl_usd"] == 0.27 and S["pnl_usd"] == -99.6 and F["moved_usd"] == -59.13 and S["moved_usd"] == 59.13
    assert "Перевод" not in F["moved_note"] and "не прибыль или убыток" in F["moved_note"]
    for lang in ("en", "uk", "el", "es"):
        o = _lang(d, lang)["public_portfolio"]["moved"]
        assert {k: o[k] for k in ("usd", "dir", "rebase_usd", "drift_usd", "is_pnl")} == {k: m[k] for k in ("usd", "dir", "rebase_usd", "drift_usd", "is_pnl")}
        assert o["why_ru"] != m["why_ru"] and o["label"] != m["label"], lang
    assert "$59.13 less" in _lang(d, "en")["public_portfolio"]["moved"]["why_ru"]
    # futures holding MORE than its base: the other direction; under 50 cents: nothing to explain
    al = dict(_real(since, now)["alloc"], fut_reserve_usd=2540.0, spot_cash_usd=1600.71)
    _pp_files(d, _real(since, now, alloc=al))
    m2 = app_home.run(sqlite3.connect(":memory:"), d, now=now)["public_portfolio"]["moved"]
    assert m2["dir"] == "spot→futures" and m2["fut_usd"] == round(2540.0 - 2506.37, 2) and "больше" in m2["why_ru"].split(".")[3]
    al = dict(al, fut_reserve_usd=2506.5)
    _pp_files(d, _real(since, now, alloc=al))
    assert app_home.run(sqlite3.connect(":memory:"), d, now=now)["public_portfolio"]["moved"] is None


def test_leverage_notes_real_cap_and_demo_signal_labels(tmp_path):
    d = str(tmp_path)
    since = T0 + 100 * H
    now = since + 6 * 24 * H
    _pp_files(d, _real(since, now))
    doc = app_home.run(sqlite3.connect(":memory:"), d, now=now)
    F = doc["public_portfolio"]["futures"]
    assert F["lev_cap"] == {"cap_default": 2, "max": 3, "set": 2, "risk_pct": 1.0, "now": 2, "seen": [2]}
    assert F["lev_note"].startswith("Плечо на реальном счёте — 2x, предел системы — 3x.") and "1% фьючерсного бюджета" in F["lev_note"]
    assert doc["futures"]["lev_range"] == [2, 5] and doc["futures"]["lev_note"].startswith("Плечо в сигналах — от 2x до 5x")
    el = _lang(d, "el")
    assert "2×" in el["public_portfolio"]["futures"]["lev_note"] and "5×" in el["futures"]["lev_note"]
    assert _lang(d, "en")["public_portfolio"]["futures"]["lev_note"].startswith("Leverage on the real account is 2x; the system's cap is 3x.")


def _buy(d, now, syms):
    _w(d, "buy_now.json", {"asof": now - H, "available": True, "checked": 40,
                           "items": [{"sym": s, "price": 1.0, "chg24": 1.0, "score": 50, "grade": "possible",
                                      "why": [{"k": "sma200"}]} for s in syms]})


def test_buy_now_items_carry_their_real_state_never_recommended_when_watched(tmp_path):
    d = str(tmp_path)
    since = T0 + 100 * H
    now = since + 6 * 24 * H
    _pp_files(d, _real(since, now))
    _buy(d, now, ["BTC", "ETH", "BNB", "NEAR", "FIL", "XMR", "DOGE", "SUI", "TAO", "AVAX", "TON", "KAS", "APT"])
    coin = lambda s, stage, **kw: {"sym": s, "stage": stage, "why_ru": "", "passed": [], "okx_pair": True, "in_real_account": False, **kw}  # noqa: E731
    _w(d, "spot_pipeline.json", {"asof": now - 10 * 60_000, "coins": [
        coin("BTC", "bought", in_real_account=True), coin("ETH", "bought"),
        coin("BNB", "first_check", table_status="wait"),
        coin("NEAR", "waiting", why_ru="Таблица: «можно брать». Портфель не покупает: продана 24.10 — снова не раньше 07.11."),
        coin("FIL", "rejected", table_status="avoid"),
        coin("XMR", "rejected", okx_pair=False, why_ru="Портфель не покупает: на споте OKX нет пары — портфель повторяет реальный счёт"),
        coin("SUI", "recommended"), coin("TAO", "all_filters"), coin("AVAX", "found"),
        coin("TON", "waiting", why_ru="Портфель не покупает: все 8 мест для «можно брать» заняты."),
        coin("KAS", "waiting", why_ru="Портфель не покупает: кэш не опускаем ниже 10%."),
        coin("APT", "waiting", passed=["found", "first_check", "all_filters", "recommended"], why_ru="Портфель не покупает: цена не сверилась.")]})
    doc = app_home.run(sqlite3.connect(":memory:"), d, now=now)
    b = doc["buy_now"]
    st = {x["sym"]: (x["status"], x["status_ru"], x["status_note"]) for x in b["items"]}
    assert st["BTC"][:2] == ("open", "позиция открыта") and st["ETH"][0] == "pending" and st["SUI"][:2] == ("signal", "сигнал сформирован")
    assert st["TAO"][:2] == ("analysis", "анализ") and st["APT"][:2] == ("pending", "ожидается исполнение")
    assert {s for s, v in st.items() if v[0] == "not_met"} == {"BNB", "NEAR", "FIL", "XMR", "TON", "KAS"}
    assert st["NEAR"][2].endswith("не раньше 07.11.") and "«не брать»" in st["FIL"][2] and "нет пары" in st["XMR"][2]
    assert "заняты" in st["TON"][2] and "минимума 10%" in st["KAS"][2]
    assert st["DOGE"][:2] == ("watch", "наблюдение") and "только кандидат по графику" in st["DOGE"][2].lower()
    assert st["AVAX"][0] == "watch"
    assert b["note"].startswith("Система проверила 40 монет по графику") and "а не покупки" in b["note"]
    for lang in i18n.LANGS:                                 # no watched coin is ever called «recommended», in any language
        for x in _lang(d, lang)["buy_now"]["items"]:
            assert x["status"] == st[x["sym"]][0] and x["status_ru"] and x["status_note"], (lang, x["sym"])
            assert not any(w in (x["status_ru"] + x["status_note"]).lower() for w in ("рекоменд", "recommend", "рекомендов", "recomend", "συνιστ"))
    assert "no earlier than 7 Nov" in {x["sym"]: x for x in _lang(d, "en")["buy_now"]["items"]}["NEAR"]["status_note"]
    block = nova.Knowledge(os.path.join(ROOT, "data")).demo_block(doc)          # NOVA answers «почему не покупает BNB» from the same data
    assert "кандидаты по графику из 40 монет, а не покупки" in block and "BTC — позиция открыта" in block and "BNB — условия не выполнены" in block
    # no fresh pipeline: nothing is guessed — the real account's coins are «open», the rest «watch» with the reason
    _w(d, "spot_pipeline.json", {"asof": now - 5 * H, "coins": []})
    st2 = {x["sym"]: x["status"] for x in app_home.run(sqlite3.connect(":memory:"), d, now=now)["buy_now"]["items"]}
    assert st2["BTC"] == "open" and st2["ETH"] == "watch" and set(st2.values()) == {"open", "watch"}


def _status(d, now, **bad):
    comps = [{"key": k, "status": bad.get(k, "ok")} for k in ("state", "engine", "engine_sync", "autopilot", "public_real", "site_pipeline",
                                                            "spot_table", "conveyor", "autolab", "researcher", "mac_mirror", "voice", "api")]
    _w(d, "system_status.json", {"asof": now - 10 * 60_000, "overall": "stale", "components": comps})


def test_report_says_plainly_when_a_part_is_not_working(tmp_path):
    d = str(tmp_path)
    since = T0 + 100 * H
    now = since + 6 * 24 * H
    _pp_files(d, _real(since, now))
    _status(d, now, mac_mirror="stopped", researcher="stale", voice="stale")          # the owner's private parts only
    r = app_home.run(sqlite3.connect(":memory:"), d, now=now)["report"]
    assert r["status"] == "ok" and r["issues"] == [] and r["text"].startswith("Публичный портфель CRYPTO NOVA сейчас")
    _status(d, now, public_real="error", engine="stale")
    r = app_home.run(sqlite3.connect(":memory:"), d, now=now)["report"]
    assert r["status"] == "degraded" and r["issues"] == ["account", "signals"]
    assert r["text"].startswith("Внимание: сейчас есть перебои — данные и автопилот реального счёта, расчёт сигналов.")
    en = _lang(d, "en")["report"]
    assert en["status"] == "degraded" and en["text"].startswith("Heads-up: there are interruptions right now")
    os.remove(os.path.join(d, "system_status.json"))                                   # nothing can be confirmed → said so
    r = app_home.run(sqlite3.connect(":memory:"), d, now=now)["report"]
    assert r["issues"] == ["check"] and "проверка состояния системы" in r["text"]
    # a stale account: the amount is said as the last confirmed one, never as today's
    _status(d, now)
    _pp_files(d, _real(since, now, stale=True))
    r = app_home.run(sqlite3.connect(":memory:"), d, now=now)["report"]
    assert r["status"] == "degraded" and r["issues"] == ["account"] and "По последним подтверждённым данным на" in r["text"]
    assert "сейчас —" not in r["text"] and "За сегодня" not in r["text"]


def test_connect_states_every_language_and_no_confirmed_without_a_check(tmp_path):
    d = str(tmp_path)
    since = T0 + 100 * H
    now = since + 6 * 24 * H
    _pp_files(d, _real(since, now))
    app_home.run(sqlite3.connect(":memory:"), d, now=now)
    for lang in i18n.LANGS:
        cs = _lang(d, lang)["config"]["connect_states"]
        assert cs["can_verify"] is False and cs["after_guide"] == "unverifiable"
        assert list(cs["states"]) == ["not_started", "action_on_exchange", "waiting", "confirmed", "error", "unverifiable"]
        assert all(s["title"] and s["text"] and s["next"] for s in cs["states"].values())
        assert [k for k, s in cs["states"].items() if not s["available"]] == ["confirmed"]
    ru = _lang(d, "ru")["config"]["connect_states"]["states"]["unverifiable"]
    assert "нет технической связи" in ru["text"] and "«Мои копирования»" in ru["next"]


def test_nova_keeps_tickers_terms_and_number_meaning():
    # the probe of 28.10: «BTC‑USDT» with a no-break hyphen, «–$104» / «‑1,04 %» with dashes for minus
    assert nova.tidy("Long — покупка BTC‑USDT; итог –$104, ‑1,04 %, за 2023–2026.") == \
        "Long — покупка BTC-USDT; итог −$104, −1,04 %, за 2023–2026."
    k = nova.Knowledge(os.path.join(ROOT, "data"))
    for lang in i18n.LANGS:
        s = k.system(lang)
        assert "Long, Short, Stop Loss, Take Profit пиши латиницей" in s and "BTC-USDT" in s and "OKX, BingX" in s, lang
    # «Πόσο αξίζει …;» is «how much is … worth», not «is … worth buying» — never the coin verdict
    res = C.resolve("Πόσο αξίζει τώρα το δημόσιο χαρτοφυλάκιο; BTC-USDT", C.Universe.build(None, None, set()))
    assert not C.is_verdict_question("Τι σημαίνουν Long και Short σε BTC-USDT; Πόσο αξίζει τώρα το δημόσιο χαρτοφυλάκιο;", res)
    assert C._INTENT.search(nova._norm("αξίζει να αγοράσω BTC;")) and C._INTENT.search(nova._norm("αξίζει το μπιτκόιν;"))
