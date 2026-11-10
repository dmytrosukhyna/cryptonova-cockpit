"""05.11.2026 (owner: «всё подтянуть, адаптировать, привести в порядок»): every public text speaks about the system as it works now —
futures of the real account copy the top-5 verified traders («Киты»), spot is the core BTC / ETH / SOL, numbers of the real OKX
account since 22.10; no retired desk as if it traded real money; a short «Киты» block in the daily and weekly posts; the photo
caption fits 1024 characters; nothing goes out 23:00–08:00 Kyiv; no post per trade."""
import datetime as dt
import json
import os
import re
import sqlite3
import tempfile
import time

from cnq.agents import channel, whale_digest, weekly
from test_channel import _web

RETIRED = re.compile(r"автопилот|откуп|Сбалансированн|Агрессивн|Консервативн|Импульс BTC|демо-стол|от стенки", re.I)


def _whales(tmp, now_ms):
    names = ["邢小鸿", "金虎趋势论", "领航财经", "慢就是快Mike8", "荣耀学员"]
    fol = [{"addr": f"bn:{i}", "short": n, "name": n, "src": "bn", "win": 0.9, "pf": 12.0, "decisions": 40} for i, n in enumerate(names)]
    json.dump({"following": fol, "desk": {"name_ru": "Киты · копирование топ-5", "start": 10_000.0, "equity": 10_420.0, "pnl_usd": 420.0,
                                          "trades": 6, "wins": 4, "started": now_ms - 10 * 86400_000,
                                          "open": [{"whale": "bn:4", "whale_ru": "荣耀学员", "coin": "BNB", "side": "SHORT", "entry": 717.0,
                                                    "price": 714.0, "stop": 774.0, "upnl": 21.0, "lev": 5.0, "opened_t": now_ms - 3600_000}],
                                          "closed": [{"whale": "bn:2", "whale_ru": "领航财经", "coin": "ZEC", "side": "SHORT", "pnl": 103.6,
                                                      "closed_t": now_ms - 7200_000}],
                                          "history": [[now_ms - 86400_000, 10_300.0], [now_ms - 3600_000, 10_420.0]]}},
              open(os.path.join(tmp, "whales_top.json"), "w"))
    sig_open = f"whales:W-BNB-S-{now_ms - 3600_000}"
    sig_closed = f"whales:W-ZRO-S-{now_ms - 30 * 3600_000}"
    whale = lambda i: {"name": names[i], "addr": f"bn:{i}", "source": "binance_lead"}
    json.dump({"by_sig": {sig_open: {"desk": "whales", "whale": whale(4)}}, "memory": {sig_closed: {"desk": "whales", "whale": whale(2)}}},
              open(os.path.join(tmp, "real_why.json"), "w"))
    json.dump({"exchange": "OKX", "since": now_ms - 14 * 86400_000, "start_usd": 10_024.4, "equity_usd": 9_650.0, "pnl_usd": -374.4,
               "pnl_pct": -3.73, "stale": False, "fut_pnl_usd": -50.0,
               "spot": [{"sym": "BTC", "share": 36.0}, {"sym": "ETH", "share": 13.0}, {"sym": "SOL", "share": 7.0}],
               "futures": [{"inst": "BNB-USDT-SWAP", "side": "short", "upnl_usd": 1.34, "sig": sig_open, "lev": 5.0}],
               "closed": [{"inst": "ZRO-USDT-SWAP", "side": "short", "open_px": 1.9, "close_px": 1.82, "move_pct": 4.2, "pnl_usd": 2.13,
                           "opened": now_ms - 30 * 3600_000, "closed": now_ms - 5 * 3600_000, "partial": False},
                          {"inst": "SOL-USDT-SWAP", "side": "long", "open_px": 96.5, "close_px": 96.48, "move_pct": -0.04, "pnl_usd": -0.24,
                           "opened": now_ms - 20 * 3600_000, "closed": now_ms - 4 * 3600_000, "partial": False}],
               "curve": [[now_ms - 40 * 3600_000, -420.0], [now_ms, -374.4]], "fut_curve": [[now_ms - 40 * 3600_000, -45.0], [now_ms, -50.0]]},
              open(os.path.join(tmp, "public_real.json"), "w"))
    json.dump({"ark": {"moves": [{"fund": "ARKK", "ticker": "TSLA", "action": "buy", "usd": 5e6}, {"fund": "ARKW", "ticker": "TSLA", "action": "buy", "usd": 1e6},
                                 {"fund": "ARKK", "ticker": "ROKU", "action": "sell", "usd": -2e6}], "date": "2026-11-04"}, "consensus": []},
              open(os.path.join(tmp, "stock_whales.json"), "w"))
    json.dump({"signals": [], "watch": {"tokens": [{"symbol": "AAVE", "status": "накопление",
                                                    "buyers": [{"label": "Jump Trading", "kind": "маркетмейкер"}, {"label": "Galaxy Digital", "kind": "фонд / OTC"}]}]}},
              open(os.path.join(tmp, "smart_money.json"), "w"))


def _tmp():
    tmp = tempfile.mkdtemp(); _web(tmp)
    now_ms = int(time.time() * 1000)
    _whales(tmp, now_ms)
    return tmp, now_ms


METHOD = re.compile(r"кит|копи|трейдер|лид-|Binance|邢|荣耀|领航|金虎|慢就是快", re.I)     # owner 05.11 09:52: never the method in public


def test_system_block_is_short_russian_and_never_names_the_method():
    tmp, now_ms = _tmp()
    b = whale_digest.block(tmp, now_ms, slot="evening")
    assert b.startswith("<b>Система CRYPTO NOVA AI</b>\n— За сутки закрыто сделок: 2, итог <b>+$1.89</b>.")
    assert "— В работе 1: BNB шорт; у каждой позиции стоп на бирже." in b
    assert "В блокчейне крупные кошельки набирают AAVE (покупают: маркетмейкер и фонд)." in b
    assert not METHOD.search(b) and "Jump" not in b and not any(channel.latin_heavy(ln) for ln in b.splitlines())
    assert len(re.sub(r"<[^>]+>", "", b)) < 420
    b2 = whale_digest.block(tmp, now_ms, slot="morning", highlights=2)
    assert "За сутки" not in b2 and "Фонд ARK (Кэти Вуд) за день: купил TSLA; продал ROKU." in b2 and not METHOD.search(b2)
    c = whale_digest.copies(tmp, now_ms)                         # internally the copies are still known (for the counts)
    assert [o["whale"] for o in c["open_copies"]] == ["荣耀学员"] and c["copies_closed"][0]["whale"] == "领航财经"
    assert whale_digest.block(tempfile.mkdtemp(), now_ms) == ""                      # no real account file → no section


def test_quiet_hours_23_to_8_kyiv():
    q = lambda h, m=0: whale_digest.quiet(dt.datetime(2026, 11, 5, h, m, tzinfo=whale_digest.KYIV))
    assert q(23) and q(2) and q(7, 59) and not q(8) and not q(12) and not q(22, 59)


def test_channel_posts_follow_the_whale_model_and_fit_the_caption():
    # 11.11: the evening lists the Kyiv day's trades — the clock is fixed at 21:40 Kyiv so the trades closed «4–5 h ago» are today's
    real_time = channel.time.time
    t0 = channel.kyiv_now().replace(hour=21, minute=40, second=0, microsecond=0).timestamp()
    channel.time.time = lambda: t0
    try:
        _channel_posts_body()
    finally:
        channel.time.time = real_time


def _channel_posts_body():
    tmp, now_ms = _tmp()
    ch = channel.Channel(sqlite3.connect(":memory:"), tmp)
    now = channel.kyiv_now(now_ms)
    for slot, h in (("morning", 8), ("evening", 21)):
        p = ch.post(slot, now=now.replace(hour=h, minute=40))
        p = ch.shrink(p) if len(re.sub(r"<[^>]+>", "", p)) > 1024 else p
        assert "<b>Система CRYPTO NOVA AI</b>" in p and not METHOD.search(p), (slot, METHOD.search(p))
        assert len(re.sub(r"<[^>]+>", "", p)) <= 1024 and ch.lint(p, {}) == [], slot
        assert not RETIRED.search(p), (slot, RETIRED.search(p))
        assert p.index("<b>Система CRYPTO NOVA AI</b>") < p.index("Вывод CRYPTO NOVA AI")
    ev = ch.post("evening", now=now.replace(hour=21, minute=40))
    assert "— В плюсе: ZRO шорт +$2.13." in ev and "— В минусе: SOL лонг −$0.24." in ev     # 09.11: one compact list
    assert "закрыто сделок" not in ev and ev.count("Закрыто 2 сделки, итог <b>+$1.89</b>") == 1      # counted once
    assert "BNB шорт: в работе" not in ev                         # the system block lists the open positions; not listed twice
    sl = ch.facts(ch.ctx(), now)["system_line"]
    assert "Фьючерсы ведёт система CRYPTO NOVA AI" in sl and "Спот — ядро BTC, ETH и SOL" in sl and not RETIRED.search(sl) and not METHOD.search(sl)
    it = ch.intro()
    assert not RETIRED.search(it) and not METHOD.search(it) and "apps.apple.com" in it and "Google Play" not in it and "play.google" not in it
    assert "CRYPTO NOVA AI" in it and len(re.sub(r"<[^>]+>", "", it)) < 1200


def test_channel_is_silent_at_night_and_has_no_per_trade_posts():
    tmp, now_ms = _tmp()
    ch = channel.Channel(sqlite3.connect(":memory:"), tmp)
    sent = []
    r = ch.run(lambda t: sent.append(t) or True, now=dt.datetime(2026, 11, 5, 23, 30, tzinfo=channel.KYIV))
    assert r["sent"] is False and "quiet" in r["why"] and not sent
    r = ch.run(lambda t: sent.append(t) or True, now=dt.datetime(2026, 11, 5, 6, 0, tzinfo=channel.KYIV))
    assert r["sent"] is False and not sent
    assert channel.Channel.TRADE_POSTS is False


def test_weekly_post_speaks_about_the_real_account_and_the_whales():
    tmp, now_ms = _tmp()
    rep = json.load(open(os.path.join(tmp, "terminal.json")))
    for c in rep["coins"]:
        for lv in c["levels"]:
            lv["kind"] = "support" if lv["hi"] <= c["price"] else "resistance"
    doc = weekly.run(sqlite3.connect(":memory:"), rep, os.path.join(tmp, "weekly.json"), now_ms)
    post, text = doc["post"], doc["text"]
    assert post.startswith("📊") and "Реальный счёт CRYPTO NOVA AI на OKX: 9 650 $" in post and "С 22.10.2026: −$374.40 (−3.73%)" in post
    assert "Система CRYPTO NOVA AI\n— За неделю закрыто сделок: 2" in post and not METHOD.search(post) and not METHOD.search(text)
    assert "Спот — ядро: BTC 36%, ETH 13%, SOL 7% счёта" in post and "<b>" not in post
    for s in (post, text):
        assert not RETIRED.search(s) and "Демо-счета" not in s and "три стратегии" not in s, RETIRED.search(s)
    assert "## Система CRYPTO NOVA AI" in text and "Как работает система: фьючерсы и спот реального счёта ведёт система CRYPTO NOVA AI" in text


def test_retired_desks_signals_are_not_posted_to_telegram(monkeypatch):
    from cnq.agents import signal_feed
    tmp = tempfile.mkdtemp()
    now = int(time.time() * 1000)
    desk = {"open": [{"id": "E-BTC-1", "symbol": "BTC", "side": "LONG", "grade": "E", "entry": 80000, "stop": 76000, "opened": now}], "closed": []}
    json.dump({"profiles": {"bal": desk, "imp": {"open": [], "closed": []}}}, open(os.path.join(tmp, "paper.json"), "w"))
    calls = []
    monkeypatch.setattr(signal_feed, "_send", lambda *a, **k: calls.append(a) or 1)
    monkeypatch.setattr(signal_feed, "signals_chat", lambda conn=None: "-100")
    conn = sqlite3.connect(":memory:")
    signal_feed.ensure(conn)
    conn.execute("INSERT INTO signal_feed(id, symbol, side, opened, seeded, source) VALUES('old', 'X', 'LONG', 1, 1, 'bal')")   # not the first run
    doc = signal_feed.run(conn, tmp, now)
    assert doc["tg_posts"] is False and not calls                       # recorded for the panel, not posted
    assert any(i["id"] == "E-BTC-1" and not i["posted"] for i in doc["items"])


def test_app_demo_futures_is_the_whale_desk(tmp_path):
    from cnq.agents import app_home
    tmp, now_ms = _tmp()
    card, curve = app_home._whale_futures(tmp, now_ms, "ru")
    assert card["in_result"] == ["system"] and card["usd"] == 420.0 and card["pct"] == 4.2 and card["closed_n"] == 1
    assert card["open"][0]["why"].startswith("Сигнал системы CRYPTO NOVA AI") and card["open"][0]["src_ru"] == "Система CRYPTO NOVA AI"
    assert "сигналы системы CRYPTO NOVA AI" in card["about_ru"] and curve[-1][1] == 420.0
    assert not METHOD.search(json.dumps(card, ensure_ascii=False))
    for lang in ("en", "uk", "el", "es"):
        en, _ = app_home._whale_futures(tmp, now_ms, lang)
        assert "CRYPTO NOVA AI" in en["about_ru"] and not re.search(r"whale|copy|trader|ballena|φάλαιν|кит|копі|трейдер", json.dumps(en, ensure_ascii=False), re.I)
    assert app_home._whale_futures(str(tmp_path), now_ms, "ru") is None    # no desk → the old card stays


def test_capital_demo_follows_the_whale_desk_and_the_core():
    """05.11: the CRYPTO NOVA AI Портфолио demo = the real model — futures follow the «Киты» desk, spot the core BTC/ETH/SOL 30/10/5;
    no active spot or listing short; switching from the old desks model restarts the demo once (no jump in the numbers)"""
    from cnq.agents import capital as K
    from test_capital import world, T0
    d = tempfile.mkdtemp()
    conn = sqlite3.connect(":memory:")
    world(d)
    old = K.run(conn, d, T0)
    assert old["model"] == "desks" and any(s["key"] == "active" for s in old["sleeves"])
    px = lambda b, e, s: json.dump({"top": [{"symbol": "BTC", "price": b}, {"symbol": "ETH", "price": e}, {"symbol": "SOL", "price": s}]},
                                   open(os.path.join(d, "markets.json"), "w"))
    px(80_000.0, 2_400.0, 96.0)
    json.dump({"following": [{"addr": "bn:1", "name": "邢小鸿"}], "desk": {"start": 10_000.0, "equity": 10_500.0, "history": []}},
              open(os.path.join(d, "whales_top.json"), "w"))
    a = K.run(conn, d, T0 + 3_600_000)
    w = {s["key"]: s["weight"] for s in a["sleeves"]}
    assert a["model"] == "system" and a["value_usd"] == 10_000 and "Новая модель" in a["events"][0] and a["previous"]["model"] == "desks"
    assert "active" not in w and "listings" not in w and w["spot"] == 0.525 and w["futures"] == 0.175
    names = {s["key"]: s["name_ru"] for s in a["sleeves"]}
    assert names["futures"] == "Фьючерсы · система CRYPTO NOVA AI" and names["spot"] == "Спот · ядро BTC / ETH / SOL"
    px(88_000.0, 2_400.0, 96.0)                                          # BTC +10 % → the core +6.67 %
    json.dump({"following": [{"addr": "bn:1", "name": "邢小鸿"}], "desk": {"start": 10_000.0, "equity": 11_550.0, "history": []}},
              open(os.path.join(d, "whales_top.json"), "w"))                # the whale desk +10 %
    b = K.run(conn, d, T0 + 2 * 3_600_000)
    v = {s["key"]: s["value_usd"] for s in b["sleeves"]}
    assert abs(v["spot"] - 5250 * (1 + 0.1 * 30 / 45)) < 0.1 and abs(v["futures"] - 1925) < 0.1 and b["events"] == []
    txt = json.dumps(b, ensure_ascii=False)
    assert "бесплатно" not in txt and "Импульс BTC" not in " ".join(b["rules_ru"])
    assert "Как реальный счёт" in b["models"]["balanced"]["about_ru"]
