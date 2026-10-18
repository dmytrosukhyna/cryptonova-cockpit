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
    ev = ch.run(lambda t: sent.append(t) or True, now=now.replace(hour=20))
    assert ev["sent"] and "Итоги дня" in sent[-1] and "Утром ставили сценарий A (78%)" in sent[-1]
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
