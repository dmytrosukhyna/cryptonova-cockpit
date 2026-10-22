"""«Главные новости рынка» on the app's Home (cnq/api/home_news.py, GET /v2/home "news")."""
import json
import os
import time

from cnq.api.home_news import HomeNews, check_answer, pick

H = 3_600_000
NOW = 1_792_650_000_000


def row(nid, mag, hours_ago, asset="MARKET", direction="bull", category="etf_flows", title_en=None, title=None, source="coindesk",
        link=None, reasoning="Приток денег в фонды поддерживает спрос."):
    return {"news_id": nid, "asset": asset, "category": category, "direction": direction, "horizon": "days", "magnitude": mag,
            "confidence": 0.7, "reasoning": reasoning, "confirm": "", "created": NOW - int(hours_ago * H),
            "title": title or f"Новость {nid}", "source": source, "link": link or f"https://www.coindesk.com/{nid}?utm_source=rss&id=7",
            "title_en": title_en or f"Headline number {nid} about something"}


def sample():
    return [
        row("a", 3, 1, "BTC", title_en="Bitcoin ETFs attract $1.08 billion in 3 days", title="Bitcoin ETF привлекли $1,08 млрд за 3 дня"),
        row("a", 3, 1, "MARKET", title_en="Bitcoin ETFs attract $1.08 billion in 3 days", title="Bitcoin ETF привлекли $1,08 млрд за 3 дня"),
        row("b", 4, 30, "BTC", title_en="Swiss Financial Regulator Gives Green Light to Country’s First Bitcoin ETF", source="bitcoinmagazine"),
        row("c", 3, 20, "BTC", title_en="Swiss regulator FINMA approves country’s first spot bitcoin ETF for retail investors", source="theblock"),
        row("d", 2, 2, "ETH", "bear", "hack", title_en="Lending protocol on Ethereum loses $12 million in exploit"),
        row("e", 5, 60, "BTC", title_en="Very old story outside the window of two days"),
        row("f", 1, 1, title_en="Small story nobody needs", category="tech"),
        row("g", 2, 1, category="other", title_en="Some story of the other kind here"),
        row("h", 4, 1, source="trump_truth", title_en="A post from a personal feed about tariffs"),
        row("i", 3, 1, title_en="RT @someone: retweeted words about markets"),
        row("j", 3, 1, title_en="TO THE GREAT PEOPLE OF TEXAS, GET OUT AND VOTE TODAY"),
        row("k", 3, 3, "SOL", "neutral", "tech", title_en="Solana network upgrade goes live after testing"),
        row("l", 2, 5, title_en="Mock story", reasoning="mock: keyword rule"),
        row("m", 2, 4, "XRP", "bull", "regulation", title_en="Court gives Ripple more time in appeal"),
        row("n", 2, 6, "DOGE", "bull", "adoption", title_en="Dogecoin payments added by a big shop"),
    ]


def write_decision(web, impacts):
    os.makedirs(web, exist_ok=True)
    with open(os.path.join(web, "decision.json"), "w", encoding="utf-8") as f:
        json.dump({"time": NOW, "news": {"impacts": impacts}}, f, ensure_ascii=False)


def test_pick_merges_ranks_and_skips():
    out = pick(sample(), NOW)
    ids = [x["id"] for x in out]
    assert len(out) == 5
    # one item per story (BTC + MARKET rows of «a» → one, with the coin); Swiss ETF from two sites → once (the stronger «b»)
    assert ids.count("a") == 1 and "c" not in ids and "b" in ids
    # ranked by magnitude − hours/24: a (3 − 0.04) > k (3 − 0.13) > b (4 − 1.25) > d (2 − 0.08) > m (2 − 0.17); n is 6th
    assert ids == ["a", "k", "b", "d", "m"]
    # skipped: older than 48 h, magnitude 1, «other» below 3, personal feeds, retweets, all-caps posts, the mock analyser
    assert not {"e", "f", "g", "h", "i", "j", "l"} & set(ids)
    a = out[0]
    assert a["asset"] == "BTC" and a["direction"] == "up" and a["magnitude"] == 3 and a["time"] == NOW - H
    assert a["link"] == "https://www.coindesk.com/a?id=7" and a["source"] == "CoinDesk"         # utm tags gone, the site's name
    d = next(x for x in out if x["id"] == "d")
    assert d["direction"] == "down" and d["asset"] == "ETH"
    k = next(x for x in out if x["id"] == "k")
    assert k["direction"] == "neutral"
    assert pick([], NOW) == [] and pick(None, NOW) == [] and pick([{"news_id": "x", "magnitude": "?"}], NOW) == []


def test_market_story_has_no_asset_and_bad_links_are_dropped():
    out = pick([row("z", 3, 1, "MARKET", link="javascript:alert(1)")], NOW)
    assert len(out) == 1 and "asset" not in out[0] and "link" not in out[0]


def test_no_model_untranslated_titles_and_empty_meaning(tmp_path):
    web = str(tmp_path / "web")
    write_decision(web, sample())
    news = HomeNews(web, str(tmp_path / "data" / "news_i18n.json"), llm=None)
    ru, en, uk = news.feed("ru", NOW), news.feed("en", NOW), news.feed("uk", NOW)
    assert ru[0]["title"] == "Bitcoin ETF привлекли $1,08 млрд за 3 дня"                    # the desk's Russian headline
    assert en[0]["title"] == "Bitcoin ETFs attract $1.08 billion in 3 days"                    # the original
    assert uk[0]["title"] == "Bitcoin ETFs attract $1.08 billion in 3 days"                    # never Russian for Ukrainian
    assert all(x["meaning"] == "" for x in ru + en + uk)
    assert not os.path.exists(tmp_path / "data" / "news_i18n.json")


ANSWER = {
    "ru": {"title": "Bitcoin ETF привлекли $1,08 млрд за 3 дня", "meaning": "Крупные фонды покупают биткоин — это поддерживает спрос."},
    "en": {"title": "Bitcoin ETFs drew $1.08 billion in 3 days", "meaning": "Big funds are buying bitcoin, which supports demand."},
    "uk": {"title": "Bitcoin ETF залучили $1,08 млрд за 3 дні", "meaning": "Великі фонди купують біткоїн — це підтримує попит."},
    "el": {"title": "Τα Bitcoin ETF άντλησαν $1,08 δισ. σε 3 ημέρες", "meaning": "Μεγάλα κεφάλαια αγοράζουν bitcoin και αυτό στηρίζει τη ζήτηση."},
    "es": {"title": "Los ETF de bitcoin captan $1,08 mil millones en 3 días", "meaning": "Grandes fondos compran bitcoin y eso sostiene la demanda."},
}


class FakeLLM:
    """the API copies its model per call (its own max_tokens / last_finish): the list of questions is shared by the copies"""
    def __init__(self, answer=None, fail=False):
        self.asked, self.answer, self.fail, self.max_tokens, self.timeout = [], answer or ANSWER, fail, 800, 20.0

    @property
    def calls(self):
        return len(self.asked)

    def available(self):
        return True

    def complete(self, system, user, temperature=0.2):
        self.asked.append(user)
        assert self.max_tokens >= 2000                       # room for 5 languages
        if self.fail:
            raise RuntimeError("HTTP 500: provider down")
        assert "ONLY facts from the input" in system and json.loads(user)["original"]
        return "```json\n" + json.dumps(self.answer, ensure_ascii=False) + "\n```"


def test_translated_once_per_story_and_cached_by_id_and_lang(tmp_path):
    web, cache = str(tmp_path / "web"), str(tmp_path / "data" / "news_i18n.json")
    write_decision(web, sample()[:2])                         # one story «a» (two asset rows)
    llm = FakeLLM()
    news = HomeNews(web, cache, llm)
    first = news.feed("uk", NOW, background=False)
    assert first[0]["title"] == "Bitcoin ETFs attract $1.08 billion in 3 days" and first[0]["meaning"] == ""
    assert news.translate(pick(sample()[:2], NOW), NOW) == 1 and llm.calls == 1   # one call gives all 5 languages
    for lang in ("ru", "en", "uk", "el", "es"):
        x = news.feed(lang, NOW, background=False)[0]
        assert x["title"] == ANSWER[lang]["title"] and x["meaning"] == ANSWER[lang]["meaning"]
    assert news.translate(pick(sample()[:2], NOW), NOW) == 0 and llm.calls == 1   # nothing left to translate
    saved = json.load(open(cache, encoding="utf-8"))["items"]["a"]
    assert set(saved) == {"ru", "en", "uk", "el", "es", "t"} and saved["uk"]["title"] == ANSWER["uk"]["title"]
    # a restart reads the cache from disk: no model call
    llm2 = FakeLLM()
    again = HomeNews(web, cache, llm2)
    assert again.feed("el", NOW, background=False)[0]["title"] == ANSWER["el"]["title"]
    assert again.translate(pick(sample()[:2], NOW), NOW) == 0 and llm2.calls == 0


def test_background_worker_fills_the_cache(tmp_path):
    web, cache = str(tmp_path / "web"), str(tmp_path / "data" / "news_i18n.json")
    write_decision(web, sample()[:2])
    llm = FakeLLM()
    news = HomeNews(web, cache, llm)
    assert news.feed("es", NOW)[0]["meaning"] == ""           # served at once, the worker starts
    for _ in range(100):
        if news.feed("es", NOW, background=False)[0]["meaning"]:
            break
        time.sleep(0.02)
    assert news.feed("es", NOW, background=False)[0]["meaning"] == ANSWER["es"]["meaning"] and llm.calls == 1


def test_model_failure_keeps_the_fallback_and_is_not_hammered(tmp_path):
    web, cache = str(tmp_path / "web"), str(tmp_path / "data" / "news_i18n.json")
    write_decision(web, sample()[:2])
    llm = FakeLLM(fail=True)
    news = HomeNews(web, cache, llm)
    stories = pick(sample()[:2], NOW)
    assert news.translate(stories, NOW) == 1 and news.last["result"] == "llm_error"
    assert news.translate(stories, NOW) == 0                   # not asked again within 15 minutes
    x = news.feed("ru", NOW, background=False)[0]
    assert x["title"] == "Bitcoin ETF привлекли $1,08 млрд за 3 дня" and x["meaning"] == ""


def test_answers_that_invent_or_use_the_wrong_alphabet_are_not_used():
    story = pick(sample()[:2], NOW)[0]
    bad = json.loads(json.dumps(ANSWER, ensure_ascii=False))
    bad["el"]["title"] = "Bitcoin ETFs drew money"                      # Greek field in Latin letters
    bad["es"]["title"] = "Los ETF de bitcoin captan $5 mil millones"    # a number that is not in the input
    bad["en"]["meaning"] = "Analysts expect 20% more by 2027 🚀"          # invented numbers → the meaning is dropped
    bad["uk"]["title"] = "🚀 «Bitcoin ETF залучили $1,08 млрд за 3 дні»\n"  # emoji, quotes and the line break are cleaned
    got = check_answer(bad, story)
    assert "el" not in got and "es" not in got
    assert got["en"] == {"title": ANSWER["en"]["title"], "meaning": ""}
    assert got["uk"]["title"] == "Bitcoin ETF залучили $1,08 млрд за 3 дні"
    assert check_answer(None, story) == {} and check_answer({"ru": "x"}, story) == {}


def test_home_route_carries_news_in_the_request_language(tmp_path):
    from cnq.api.auth import FirebaseVerifier
    from cnq.api.server import Api
    from cnq.api.store import Store
    web = str(tmp_path / "web")
    os.makedirs(web)
    with open(os.path.join(web, "app_home.json"), "w") as f:
        json.dump({"asof": int(time.time() * 1000), "total": {"usd": 1}}, f)
    api = Api(Store(str(tmp_path / "api.sqlite")), FirebaseVerifier("crypto-nova", keys={}), web, None)
    assert api.news.path == str(tmp_path / "data" / "news_i18n.json")      # next to the API's data, git-ignored
    c, d = api.handle("GET", "/v2/home?lang=en", {}, b"")
    assert c == 200 and "news" not in d                        # no decision.json → no key, the app hides the block
    write_decision(web, [dict(r, created=int(time.time() * 1000) - H) for r in sample()[:2]])
    c, d = api.handle("GET", "/v2/home?lang=en", {}, b"")
    assert c == 200 and d["news"][0]["title"] == "Bitcoin ETFs attract $1.08 billion in 3 days" and d["news"][0]["asset"] == "BTC"
    c, d = api.handle("GET", "/v2/home", {}, b"")
    assert d["news"][0]["title"] == "Bitcoin ETF привлекли $1,08 млрд за 3 дня"
    with open(os.path.join(web, "decision.json"), "w") as f:  # a broken file never breaks the home feed
        f.write("{not json")
    os.utime(os.path.join(web, "decision.json"), (time.time() + 5, time.time() + 5))
    assert api.handle("GET", "/v2/home", {}, b"")[0] == 200
