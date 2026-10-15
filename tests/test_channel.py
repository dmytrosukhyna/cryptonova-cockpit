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
    assert ev["sent"] and "Итоги дня" in sent[-1] and "утром ставили сценарий A (78%)" in sent[-1]
    # number guard: an invented figure is rejected, faithful text passes
    facts = ch.facts(ch.ctx(), now)
    assert not ch.guard("<b>Рынок:</b> биткоин $80 984, а завтра будет 95 000. " + "x" * 200, facts)
    assert ch.guard("<b>Рынок:</b> биткоин $80 984 (-2.9%), страх 53. " + "я" * 200, facts)


def test_llm_output_used_when_faithful():
    tmp = tempfile.mkdtemp(); _web(tmp)
    conn = sqlite3.connect(":memory:"); ch = channel.Channel(conn, tmp)
    class Good:
        def complete(self, s, u, temperature=0.2): return "<b>Доброе утро, дорогие подписчики!</b> Биткоин $80 984 (-2.9%), эфир $2 366. " + "Рынок нервный. " * 20
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
