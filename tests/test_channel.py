import json, os, sqlite3, tempfile, datetime as dt
from cnq.agents import channel


def _web(tmp):
    json.dump({"coins": [{"symbol": "BTC-USDT", "price": 81000, "chg24": -1.2, "regime": {"long": "BULL", "mid": "SHORT", "short": "DOWN", "atr_pct": 2.5},
                           "levels": [{"lo": 78500, "hi": 79000, "center": 78750, "touches": 4, "status": "ACTIVE", "dist_atr": 0.8},
                                      {"lo": 83000, "hi": 83400, "center": 83200, "touches": 3, "status": "ACTIVE", "dist_atr": 1.1}]}]},
              open(os.path.join(tmp, "terminal.json"), "w"))
    json.dump({"global": {"total_mcap": 2.7e12, "mcap_chg24": -5.9, "btc_dom": 60.0}, "fng": {"value": 53, "label": "нейтрально", "yesterday": 62, "week": 59},
               "top": [{"symbol": "BTC", "price": 80984, "chg24": -2.9, "chg7": -2.5, "ath_chg": -36, "rank": 1, "volume": 4e10},
                       {"symbol": "ETH", "price": 2366, "chg24": -2.3, "chg7": -4.1, "rank": 2, "volume": 2e10},
                       {"symbol": "USDGO", "price": 1, "chg24": 0.0, "rank": 50, "volume": 1e9}, {"symbol": "SOL", "price": 102, "chg24": -4.0, "rank": 5, "volume": 2e9}]},
              open(os.path.join(tmp, "markets.json"), "w"))
    json.dump({"open": {"BTC": {"p_up": 0.22, "up": 82331, "dn": 80141}}, "stats": {"n": 5, "hit_rate": 0.0, "brier": 0.61}}, open(os.path.join(tmp, "calibration.json"), "w"))
    now = int(dt.datetime.now().timestamp() * 1000)
    json.dump({"news": {"items": [{"id": "n2", "summary": "Fund inflows reached a record this week."}],
                        "impacts": [{"news_id": "n1", "title": "Mock one", "asset": "BTC", "category": "hack", "direction": "bear", "magnitude": 4, "reasoning": "mock: keyword rule", "created": now, "link": "https://x/1"},
                                    {"news_id": "n2", "title": "Приток в ETF обновил рекорд", "asset": "BTC", "category": "etf_flows", "direction": "bull", "magnitude": 4, "confidence": .7,
                                     "reasoning": "Фонды обязаны докупать под новые деньги.", "horizon": "days", "confirm": "закрепление выше 83 400", "created": now, "link": "https://www.coindesk.com/x", "source": "coindesk"}]}},
              open(os.path.join(tmp, "decision.json"), "w"))
    json.dump({"available": True, "summary": {"n_whales": 20, "total_long_usd": 5.3e7, "total_short_usd": 2.7e7, "bias": "LONG"}, "coins": [], "changes": []}, open(os.path.join(tmp, "whales.json"), "w"))
    json.dump({"profiles": {"cons": {"pnl_pct": 0.0, "open": []}, "bal": {"pnl_pct": 1.25, "open": [1, 2]}}, "closed": [], "stress": {"level": 2}}, open(os.path.join(tmp, "paper.json"), "w"))


def test_channel_templates_slots_and_guard():
    tmp = tempfile.mkdtemp(); _web(tmp)
    conn = sqlite3.connect(":memory:"); ch = channel.Channel(conn, tmp)
    m = ch.post("morning")
    assert "Доброе утро, дорогие подписчики" in m and "$80 984" in m and "Вывод CRYPTO NOVA" in m and "http" not in m
    n = ch.post("news1")
    assert n and "Приток в ETF" in n and "Mock one" not in n and 'href="https://www.coindesk.com/x"' in n and "CoinDesk" in n
    sent = []
    now = dt.datetime(2026, 10, 15, 8, 30, tzinfo=channel.KYIV)
    r = ch.run(lambda t: sent.append(t) or True, now=now)
    assert r["sent"] and "Утренний обзор" in sent[-1]
    assert not ch.run(lambda t: sent.append(t) or True, now=now)["sent"]              # once per slot
    r2 = ch.run(lambda t: sent.append(t) or True, now=now.replace(hour=11))
    assert r2["sent"] and "Приток в ETF" in sent[-1]
    r3 = ch.run(lambda t: sent.append(t) or True, now=now.replace(hour=15))
    assert not r3["sent"] and r3["why"] == "nothing worth posting"                    # the only strong news is used up
    ev = ch.run(lambda t: sent.append(t) or True, now=now.replace(hour=21, minute=40))   # 20.10: the evening slot opens at 21:30
    # 21.10 owner: a morning scenario that missed its target is not written about (BTC 81 000 never reached the 80 141 support)
    assert ev["sent"] and "Итоги дня" in sent[-1] and "Утром ставили" not in sent[-1] and "не сработал" not in sent[-1]
    # number guard: an invented figure is rejected, faithful text passes
    facts = ch.facts(ch.ctx(), now)
    assert not ch.guard("<b>Рынок:</b> биткоин $80 984, а завтра будет 95 000. " + "x" * 200, facts)
    assert ch.guard("<b>Рынок:</b> биткоин $80 984 (-2.9%). " + "я" * 200, facts)
    assert not ch.guard("<b>Рынок:</b> биткоин $80 984 (-2.9%), страх 53. " + "я" * 200, facts)   # 16.10: fear & greed no longer in facts


def test_llm_output_used_when_faithful():
    tmp = tempfile.mkdtemp(); _web(tmp)
    conn = sqlite3.connect(":memory:"); ch = channel.Channel(conn, tmp)
    class Good:
        def complete(self, s, u, temperature=0.2): return "<b>Доброе утро, дорогие подписчики!</b> Биткоин $80 984 (-2.9%), эфир $2 366.\n\n" + "Рынок нервный.\n" * 20
    class Bad:
        def complete(self, s, u, temperature=0.2): return "<b>Доброе утро!</b> Биткоин пробьёт 120 000 уже завтра. " + "Покупайте. " * 30
    assert "Биткоин $80 984" in ch.post("morning", llm=Good())
    assert "120 000" not in ch.post("morning", llm=Bad()) and "Утренний обзор рынка" in ch.post("morning", llm=Bad())


def test_english_news_translated_or_dropped():
    items = [{"title": "Arrest of HEX founder triggers panic selling", "mechanism": "Holders are forced to sell to cut losses.", "confirms_if": "", "summary": "Local media reported the arrest."}]
    assert channel.russify([dict(items[0])], None) == []                     # no translator → English item is not published
    class FakeLLM:
        def json(self, system, user):
            src = json.loads(user)
            ru = {"title": "Арест основателя HEX вызвал панические продажи", "mechanism": "Держатели вынуждены продавать, чтобы сократить убытки.",
                  "summary": "Местные СМИ сообщили об аресте."}
            return {k: ru[k.split(".", 1)[1]] for k in src}
    out = channel.russify([dict(items[0])], FakeLLM())
    assert out and out[0]["title"].startswith("Арест") and not channel.latin_heavy(out[0]["summary"])
    assert not channel.Channel.guard(None, "1. Mastercard platform lowers friction for banks and enterprises to hold stablecoins\n" + "а" * 300, {})


def test_factcheck_rejects_then_accepts():
    tmp = tempfile.mkdtemp(); _web(tmp)
    ch = channel.Channel(sqlite3.connect(":memory:"), tmp)
    facts = ch.facts(ch.ctx(), dt.datetime(2026, 10, 15, 20, 30, tzinfo=channel.KYIV))
    good = "<b>Итоги дня</b>\n\n" + "Биткоин закрыл день у $80 984, рынок нервный, работаем от зон, стоп заранее. " * 4
    good = good.strip()
    class LLM:
        def __init__(self): self.n = 0; self.prompts = []
        def complete(self, system, user, temperature=0.2):
            self.n += 1; self.prompts.append(user)
            return good.replace("Итоги дня", "Шапка дня") if self.n == 1 else good     # 1st draft has a service word
        def json(self, system, user):
            return {"ok": "завтра ждём 70 000" not in user, "problems": []}
    llm = LLM()
    out = ch.write("evening", facts, llm)
    assert out == good and llm.n == 2 and "Исправь" in llm.prompts[1] and ch.last_review[0]["problems"]
    class Picky(LLM):
        def json(self, system, user):
            return {"ok": False, "problems": ["зона подана как цель на завтра"]}
    p = Picky()
    assert ch.write("evening", facts, p) is None and p.n == 3          # never publishes a draft the fact-checker rejected


def test_lint_allows_repeated_bold_values_but_not_repeated_sections():
    """17.10: the evening post was blocked all evening because «+$0» was bold twice (futures and total after the reset)."""
    tmp = tempfile.mkdtemp()
    ch = channel.Channel(sqlite3.connect(":memory:"), tmp)
    ok = ("<b>Итоги дня · 17.10</b>\n\n<b>Итог дня по счетам:</b>\n— Фьючерсы (автопилот): <b>+$0</b>\n— Спот: <b>+$8</b>\n"
          "— Всего за день: <b>+$0</b>\n\n<b>Что дальше:</b>\n— Сценарий A: рост")
    assert "повторяющиеся разделы" not in ch.lint(ok, {})
    bad = ok + "\n\n<b>Что дальше:</b>\n— ещё раз"
    assert "повторяющиеся разделы" in ch.lint(bad, {})


def test_news_post_may_not_claim_a_fall_the_market_does_not_show():
    from cnq.agents.channel import Channel
    C = Channel.__new__(Channel)
    txt = "<b>Вывод CRYPTO NOVA:</b>\nСистема фиксирует падение. Страх приводит к массовой продаже, давление ощущается на биткоине."
    assert any("нет падения" in p for p in C.lint(txt, {"btc": {"chg24": "+0.4%"}}))
    assert not any("нет падения" in p for p in C.lint(txt, {"btc": {"chg24": "-3.2%"}}))
    ok = "<b>Вывод CRYPTO NOVA:</b>\nНовость может добавить осторожности, но на цене это пока не видно."
    assert not any("падения" in p for p in C.lint(ok, {"btc": {"chg24": "+0.4%"}}))
    n = {"title": "Китайский подозреваемый в $75 млн крипто‑преступной сети арестован в Бангкоке", "strength": "3/5"}
    assert Channel.minor_enforcement(n, {"title_en": "Chinese suspect in $75M crime network arrested"})
    assert not Channel.minor_enforcement({"title": "ETF на биткоин одобрен", "strength": "4/5"}, {})
    assert not Channel.minor_enforcement({"title": "Взлом биржи на $1.4 млрд", "strength": "3/5"}, {"title_en": "Exchange hacked, $1.4B stolen, suspect arrested"})


def test_evening_slot_opens_at_2130_kyiv():
    import datetime as dt
    from cnq.agents.channel import KYIV, slot_for
    at = lambda h, m: dt.datetime(2026, 10, 20, h, m, tzinfo=KYIV)
    assert slot_for(at(20, 30)) is None and slot_for(at(21, 29)) is None
    assert slot_for(at(21, 30)) == "evening" and slot_for(at(22, 59)) == "evening" and slot_for(at(23, 0)) is None
    assert slot_for(at(8, 0)) == "morning" and slot_for(at(11, 0)) == "news1"


def test_posts_show_levels_not_scenarios():
    # 21.10 owner chose option 2: the channel gives the day's levels, no A/B scenarios and no probabilities
    tmp = tempfile.mkdtemp(); _web(tmp)
    conn = sqlite3.connect(":memory:"); ch = channel.Channel(conn, tmp)
    m = ch.post("morning")
    assert "Уровни на день" in m and "Сопротивление: <b>82 331</b>" in m and "Поддержка: <b>80 141</b>" in m
    assert "Сценарий" not in m and "%)" not in m.split("Уровни на день")[1].split("Вывод")[0]
    now = dt.datetime(2026, 10, 15, 8, 30, tzinfo=channel.KYIV)
    ev = ch.post("evening", now.replace(hour=21, minute=40))
    assert "Уровни на завтра: сопротивление <b>82 331</b>, поддержка <b>80 141</b>" in ev and "Сценарий" not in ev
    assert ch.lint("— Сценарий A (основной): 56% — к сопротивлению 84 206", {})       # an LLM draft with scenarios is sent back


def test_deals_plural_and_caption_fit():
    from cnq.terminal.notify import fit_caption
    assert [channel.Channel._deals(n) for n in (1, 3, 9, 11, 21, 25)] == ["1 сделку", "3 сделки", "9 сделок", "11 сделок", "21 сделку", "25 сделок"]
    post = "<b>Итоги дня</b>\n\n" + "\n".join(f"— строка <b>{i}</b>" for i in range(60)) + "\n\n<b>Вывод CRYPTO NOVA</b>\nДолгосрочный тренд биткоина вверх."
    assert len(post) > 1024 >= len(__import__("re").sub(r"<[^>]+>", "", post))
    assert fit_caption(post) == post                                                   # tags don't count: nothing is cut
    long = post + "\n\n" + "я" * 400
    out = fit_caption(long)
    assert out == post and out.count("<b>") == out.count("</b>")                       # whole paragraphs dropped, never cut mid-tag


def test_post_picture_board_has_the_live_tiles():
    # 21.10 owner: «более интересный график … разное совмещать» — BTC chart on top, ETH / SOL / fear & greed / result tiles below
    from cnq.agents import postchart
    if postchart.Image is None:
        return
    tmp = tempfile.mkdtemp()
    base = os.path.join(tmp, "c.png"); postchart.Image.new("RGB", (1280, 720), (6, 11, 22)).save(base)
    candles = [[1_760_000_000_000 + i * 900_000, 100 + i % 7, 101 + i % 7, 99 + i % 7, 100 + (i % 9), 10] for i in range(400)]
    out = postchart.board(base, os.path.join(tmp, "b.png"), eth={"candles": candles, "price": 2447}, sol=None, eth_chg=1.6,
                          fng={"value": 63, "label": "Greed", "yesterday": 55}, result=[("Спот", "+$230", postchart.UP)])
    im = postchart.Image.open(out)
    assert im.size == (1280, 1000)


def test_post_album_wave_map_and_engines():
    # 21.10 owner: «либо в режиме волна … придумай что и показывай … продемонстрировать нашу технологичность» —
    # the wave look renders, the market map sizes tiles by cap, the engine card shows six engines with live numbers
    from cnq.agents import postchart
    if postchart.Image is None:
        return
    tmp = tempfile.mkdtemp()
    candles = [[1_760_000_000_000 + i * 900_000, 100 + i % 7, 101 + i % 7, 99 + i % 7, 100 + (i % 9), 10 + i % 5] for i in range(400)]
    coin = {"candles": candles, "price": 104, "levels": []}
    op = {"p_up": 0.6, "up": 108, "dn": 97}
    w = postchart.render(coin, op, None, os.path.join(tmp, "w.png"), conclusion=["Сценарий A (60%): рост к сопротивлению 108."], mode="wave")
    assert w and postchart.Image.open(w).size == (1280, 720)
    top = [{"symbol": f"C{i}", "mcap": 1e9 * (40 - i), "chg24": (i % 5) - 2.0} for i in range(30)] + [{"symbol": "USDT", "mcap": 1e11, "chg24": 0, "stable": True}]
    m = postchart.heatmap({"top": top, "global": {"total_mcap": 2.8e12, "btc_dom": 59.5}}, os.path.join(tmp, "m.png"))
    assert m and postchart.Image.open(m).size == (1280, 720)
    rects = postchart._squarify([(4.0, "a"), (2.0, "b"), (1.0, "c"), (1.0, "d")], 0, 0, 80, 100)
    assert abs(sum(r[2] * r[3] for r in rects) - 8000) < 1 and len(rects) == 4
    home = {"engine": {"cycle_sec": 60, "signals_checked": 327, "lab_tried": 800, "lab_tested": 260, "lab_passed": 30, "lab_reliable": 3},
            "portfolio": {"value_usd": 10193, "pnl_pct": 1.93, "curve": [[i, i * 1.5] for i in range(50)]}}
    risk = {"limits": [{"key": "leverage", "pct_of_limit": 66.7, "status": "ok"}, {"key": "coin", "pct_of_limit": 95, "status": "ok"}]}
    e = postchart.engine_card(home, {"counts": {"lab": 33, "paper": 13, "ready": 0}}, risk,
                              {"headline": {"recorded": 1615}, "chain": {"ledger": {"lines": 1615, "head": "39a07d3b"}}}, os.path.join(tmp, "e.png"))
    assert e and postchart.Image.open(e).size == (1280, 720)
    assert postchart.heatmap({"top": top[:3]}, os.path.join(tmp, "x.png")) is None       # too few coins → no map, post keeps one picture


def test_send_photo_album_falls_back_to_one_picture():
    from cnq.terminal import notify
    calls = []
    orig_album = notify.send_album
    try:
        notify.send_album = lambda paths, cap, tok, chat, pm="HTML": (calls.append(("album", len(paths))), False)[1]
        tmp = tempfile.mkdtemp(); a = os.path.join(tmp, "a.png"); open(a, "wb").write(b"x")
        import urllib.request as ur
        orig = ur.urlopen

        class R:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"ok": true}'
        ur.urlopen = lambda req, timeout=30: (calls.append(("photo", req.full_url.rsplit("/", 1)[-1])), R())[1]
        try:
            assert notify.send_photo([a, a, os.path.join(tmp, "missing.png")], "текст", token="t", chat="c")
        finally:
            ur.urlopen = orig
    finally:
        notify.send_album = orig_album
    assert calls == [("album", 2), ("photo", "sendPhoto")]


def test_closed_trade_gets_its_own_post_and_the_evening_lists_it():
    """Owner 21.10: results of the trades go to the channel — a post per closed trade (bold date, picture card) and a
    «Сделки за день» block in the evening. Losses are posted like wins; old trades and night hours are skipped."""
    tmp = tempfile.mkdtemp(); _web(tmp)
    now = dt.datetime.now(channel.KYIV).replace(hour=14, minute=5)
    ms = int(now.timestamp() * 1000)
    win = {"id": "E-ARB-1", "symbol": "ARB", "side": "LONG", "grade": "E", "entry": 0.17552, "qty": 11396.0, "pnl_usd": 191.6, "net_usd": 190.59,
           "opened": ms - 25 * 3600_000, "closed": ms - 3600_000, "exit_kind": "TIME"}
    loss = {"id": "E-DOT-1", "symbol": "DOT", "side": "LONG", "grade": "E", "entry": 1.1121, "qty": 299.79, "pnl_usd": -7.64, "net_usd": -7.81,
            "opened": ms - 26 * 3600_000, "closed": ms - 2 * 3600_000, "exit_kind": "STOP"}
    old = dict(win, id="E-SOL-old", symbol="SOL", closed=ms - 3 * 86400_000)
    pj = json.load(open(os.path.join(tmp, "paper.json")))
    pj["profiles"]["bal"] = {"pnl_pct": 1.86, "pnl_usd": 186.34, "open": [], "closed": [old, loss, win], "stats": {"trades": 7, "winrate": 0.714}}
    json.dump(pj, open(os.path.join(tmp, "paper.json"), "w"))
    conn = sqlite3.connect(":memory:"); ch = channel.Channel(conn, tmp)
    sent, photos = [], []
    out = ch.trade_posts(lambda t: sent.append(t) or True, lambda p, c: photos.append((p, c)) or True, now)
    assert len(out) == 2 and not any("SOL" in t for _, t in out)
    t_loss, t_win = out[0][1], out[1][1]
    d = now.strftime("%d.%m.%Y")
    assert f"<b>Сделка закрыта · {d}</b>" in t_win and "<b>+$191 (+9.6%)</b>" in t_win and "Демо-счёт $10 000" in t_win
    assert "<b>−$8 (−2.3%)</b>" in t_loss and "по стопу" in t_loss
    assert "в плюсе 5" in t_win and "+$186" in t_win
    assert len(photos) == 2 or len(sent) == 2                                # picture card when Pillow is there, text otherwise
    assert ch.trade_posts(lambda t: sent.append(t) or True, None, now) == []    # once per trade
    ch2 = channel.Channel(sqlite3.connect(":memory:"), tmp)
    assert ch2.trade_posts(lambda t: True, None, now.replace(hour=3)) == []     # no posts at night
    blk = ch.trades_block(ch.ctx())
    assert blk.startswith("<b>Сделки за день:</b>") and "ARB лонг: <b>+$191 (+9.6%)</b>" in blk and "DOT" in blk and "SOL" not in blk
    ev = ch.post("evening", now=now.replace(hour=21, minute=40))
    assert "<b>Сделки за день:</b>" in ev and ev.index("Сделки за день") < ev.index("Вывод CRYPTO NOVA")
    assert ch.lint(ev, {}) == []
