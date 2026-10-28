"""20.10.2026 — the app speaks en · ru · uk · el · es: the message catalog, the app home in every language from the same data,
?lang= on the public feeds, NOVA's answers, FAQ and facts per language."""
import json
import os
import re
import shutil
import sqlite3
import time

import pytest

from cnq import engine
from cnq.agents import app_home, i18n, panel_header as ph
from cnq.api import nova
from tests.test_api import call, make, token

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA, WEB = os.path.join(ROOT, "data"), os.path.join(ROOT, "web")
OTHER = [x for x in i18n.LANGS if x != i18n.DEFAULT]
CYR = re.compile(r"[А-Яа-яЁёІіЇїЄєҐґ]")
GREEK_LATIN = {"atr", "btc", "binance", "bingx", "bitcoin", "crypto", "nova", "ether", "solana", "rsi", "short", "token", "altcoin",
               "altcoins", "demo", "funding", "future", "futures", "meme", "coins", "pump", "spot", "stop", "top", "r",
               "long", "traders", "okx", "eth", "sol", "usdt",        # 28.10: tickers, the exchange, «long» as Greek crypto texts write it
               "copy", "trading", "my", "copies"}                       # build 47: OKX's own English menu names, quoted as the app guide does


# ------------------------------------------------------------------------------------------------ the catalog
def test_catalog_complete_in_every_language():
    assert i18n.LANGS == ("ru", "en", "uk", "el", "es") and i18n.DEFAULT == "ru"
    for key, row in i18n.T.items():
        assert set(row) == set(i18n.LANGS), key
        ref = i18n.fields(row["ru"])
        for lang, s in row.items():
            assert isinstance(s, str) and s.strip() and s == s.rstrip("\n"), (key, lang)
            assert i18n.fields(s) == ref, (key, lang, i18n.fields(s), ref)          # the same {placeholders}
            if lang in ("en", "el", "es"):
                assert not CYR.search(s), (key, lang)
            if lang == "el":                                  # Latin only for the words Greek crypto texts keep in Latin
                latin = {w.lower() for w in re.findall(r"[A-Za-z]+", re.sub(r"\{[a-z_]+\}", "", s))}
                assert latin <= GREEK_LATIN, (key, latin - GREEK_LATIN)
    for key, row in i18n.P.items():
        assert set(row) == set(i18n.LANGS), key
        for lang, forms in row.items():
            assert len(forms) == (3 if lang in ("ru", "uk") else 2) and all(forms), (key, lang)
    assert i18n.plural("ru", 21, "coin_acc") == "монету" and i18n.plural("ru", 3, "coin_acc") == "монеты"
    assert i18n.plural("uk", 12, "trade") == "угод" and i18n.plural("en", 1, "trade") == "trade" and i18n.plural("el", 2, "hour") == "ώρες"
    assert i18n.t("es", "hold.time", h=12, hours=i18n.plural("es", 12, "hour")).startswith("12 horas")
    for raw, want in (("en", "en"), ("EN-us", "en"), ("uk_UA", "uk"), ("ua", "uk"), ("el-GR", "el"), ("es", "es"), ("ru", "ru"),
                      (None, "ru"), ("", "ru"), ("de", "ru"), (5, "ru"), ("<script>", "ru")):
        assert i18n.norm_lang(raw) == want, raw
    assert i18n.file_name("app_home", "ru") == "app_home.json" and i18n.file_name("app_home", "el") == "app_home.el.json"


def test_nova_fixed_answers_per_language():
    assert nova.BUSY == i18n.t("ru", "nova.busy") and nova.busy("xx") == nova.BUSY
    for lang in i18n.LANGS:
        for f in (nova.busy(lang), nova.bad_question(lang), nova.limit(lang), nova.limit(lang, vip=True)):
            assert f and "VIP" not in f
    # 28.10 (build 46 §11.4): no promise of 100 questions for connecting (an OKX copy is not confirmed in the app); the ready-made
    # questions still work after the limit
    assert "100" not in nova.limit("el") and "έτοιμες ερωτήσεις" in nova.limit("el") and "tomorrow" in nova.limit("en", vip=True)
    assert all("5" in nova.limit(lg) for lg in i18n.LANGS)


# ------------------------------------------------------------------------------------------------ app home in five languages
def _w(d, name, obj):
    with open(os.path.join(d, name), "w", encoding="utf-8") as f:
        json.dump(obj, f)


def _desks(t0, main=None):
    """one open trade of every rule with the facts its desk stores, the own desk's daily scan, a closed trade; the main strategy's
    trades sit on the MAIN desk (28.10: the one web/desk_select.json chose — `main`, or the default main desk)"""
    key = main or ph.NOVA_BASE[0]
    btc = {"close": 110.0, "sma200": 100.0, "c60": 95.0}
    own = {"P": {"close": 10.4, "sma50": 10.0, "sma50_5": 9.8, "rsi2": 4.2, "chg2": -0.031, "btc": btc, "vol": 1.8, "rank": 7, "of": 40,
                 "scan": {"coins": 40, "checks": 8, "signals": 2, "passed": 1, "day": t0 // 86_400_000 * 86_400_000}},
           "C": {"close": 21.0, "hh20": 20.0, "sma20": 19.0, "btc": btc, "vol": 2.1, "rank": 3, "of": 40,
                 "scan": {"coins": 40, "checks": 8, "signals": 1, "passed": 1, "day": 0, "bull": False}}}
    bal = {"E": {"breadth": 0.62, "need_breadth": 0.4, "drop": -0.04, "need_drop": -0.015, "vol_x": 5.0, "need_vol": 2,
                 "scan": {"coins": 18, "rules": 4, "fired": 1}},
           "B": {"ret": -0.05, "sigma_x": -4.2, "need_sigma": -3.5, "wick": 0.6, "need_wick": 0.5, "scan": {"coins": 18, "rules": 4, "fired": 2}},
           "F": {"oi4": -0.15, "need_oi": -0.12, "px4": -0.06, "need_px": -0.05, "scan": {"coins": 18, "rules": 4, "fired": 1}},
           "D": {"basis": -0.0031, "need_basis": -0.002, "scan": {"coins": 18, "rules": 4, "fired": 1}}}
    row = lambda sym, g, f, n, **kw: {"symbol": sym, "side": "LONG", "grade": g, "entry": 10.0, "stop": 9.0, "price": 10.2, "qty": 50,
                                      "opened": t0 + n, "facts": f, "open_pnl_usd": 10.0, **kw}
    prof = {p: {"equity": 10000.0, "open_pnl_usd": 0.0, "equity_history": [{"time": t0, "equity": 10000.0}], "open": [],
                "closed": [{"symbol": "ARB", "side": "LONG", "pnl_usd": 191.6, "closed": t0}], "stats": {"trades": 1, "winrate": 1.0}}
            for p in ph.FAMILY if p != key}
    prof[key] = {"equity": 10000.0, "equity_history": [{"time": t0, "equity": 10000.0}], "stats": {"trades": 1, "winrate": 1.0},
                   "closed": [{"symbol": "ARB", "side": "LONG", "pnl_usd": 191.6, "closed": t0}],
                   "open": [row(s, g, f, i, kind="CLIMAX_LONG" if g == "E" else "") for i, (s, (g, f)) in
                            enumerate(zip(("SOL", "ETH", "LINK", "BNB"), bal.items()))]}
    prof["own"] = {"open": [row("AVAX", "P", own["P"], 10, vol=1.8), row("NEAR", "C", own["C"], 11, vol=2.1),
                            row("SEI", "P", {}, 12, vol=None)],
                   "scan": own["P"]["scan"], "live_by_rule": {"P": {"n": 2, "wins": 2, "avg_pct": 6.0}}}
    prof["imp"] = {"open": [row("SUI", "K", {"btc_ret": 0.027, "need_btc": 0.025, "rank": 2, "slots": 5, "scan": {"coins": 60}}, 20)]}
    prof["lst"] = {"open": [dict(row("NEWX", "LST", {"age_h": 4.5, "move": 0.03, "skip_pump": 0.1, "qvol": 3.2e6, "min_qvol": 2e6,
                                                     "watching": 23}, 30), side="SHORT", entry=1.0, stop=1.3, price=0.9)],
                   "stats": {"trades": 0}}
    return {"since": t0, "profiles": prof}


def _texts(doc: dict) -> list[str]:
    """every human-readable string the app shows from app_home (not the regime / fear & greed labels: the app translates them)"""
    f, s = doc["futures"], doc["spot"]
    out = [f["about_ru"], doc["note_ru"], (f["scan"] or {}).get("text"), (s.get("last_action") or {}).get("text"),
           *[x.get("label") for x in doc["sources"]], f.get("status_ru"), *[a["name"] for a in s["alloc"]]]
    for o in f["open"]:
        out += [o["why"], o["why_title"], o["src_ru"], o["work"], o["note"], (o.get("risk") or {}).get("hold")]
        out += [(o.get("history") or {}).get(k) for k in ("text", "years_plus")]
        out += [c[k] for c in o.get("checks") or [] for k in ("t", "v")]
    return [x for x in out if x]


def _numbers(doc):
    """the document without its words: what must be identical in every language"""
    if isinstance(doc, dict):
        return {k: _numbers(v) for k, v in doc.items() if (not isinstance(v, str) or k in ("sym", "side", "src", "action", "mode", "regime"))
                and not (v is None and k.endswith("_ru"))}          # a Russian-only text left empty is a word, not a number
    if isinstance(doc, list):
        return [_numbers(v) for v in doc if not isinstance(v, str)]          # a list of sentences is words too (market_view)
    return doc


@pytest.mark.parametrize("main", [None, "bal"])
def test_app_home_in_every_language_from_the_same_data(tmp_path, main):
    d, t0 = str(tmp_path), 1792300000000
    _w(d, "paper.json", _desks(t0, main))
    if main:
        _w(d, "desk_select.json", {"current": main})
    _w(d, "app_portfolio.json", {"index": 99.0, "cash_pct": 15, "history": [{"t": t0, "v": 100.0}],
                                 "positions": [{"symbol": "BTC", "weight": 0.3, "entry": 100, "price": 110, "kind_ru": "ядро"},
                                               {"symbol": "PENGU", "weight": 0.1, "entry": 1, "price": 0.9, "kind_ru": "мем"},
                                               {"symbol": "LINK", "weight": 0.2, "entry": 1, "price": 1.1, "kind_ru": "крупный альт"},
                                               {"symbol": "XYZ", "weight": 0.05, "entry": 1, "price": 1.0}],
                                 "recent_actions": [{"time": t0, "symbol": "SOL", "action": "TRIM",
                                                     "why_ru": "Ядро: SOL 10% счёта — больше плана 5% в 2.0 раза; лишнее продаём (недельный пересмотр)."}]})
    _w(d, "markets.json", {"top": [{"symbol": "BTC", "price": 81000, "chg24": 1.2}], "fng": {"value": 59, "label": "жадность"}, "btc_bull": True})
    ru = app_home.run(sqlite3.connect(":memory:"), d, now=t0 + 120000)
    docs = {lang: json.load(open(os.path.join(d, i18n.file_name("app_home", lang)), encoding="utf-8")) for lang in i18n.LANGS}
    assert docs["ru"] == json.loads(json.dumps(ru, ensure_ascii=False)) and {x: docs[x]["lang"] for x in docs} == {x: x for x in i18n.LANGS}
    assert not os.path.exists(os.path.join(d, "app_home.json.tmp"))
    f = ru["futures"]
    assert {o["sym"] for o in f["open"]} == {"SOL", "ETH", "LINK", "BNB", "AVAX", "NEAR", "SEI", "SUI", "NEWX"}
    assert ru["sources"][3]["label"] == "арбитраж, рынки прогнозов" and ru["spot"]["last_action"]["text"].startswith("SOL выросла до 10% счёта")
    every = {lang: _texts(doc) for lang, doc in docs.items()}
    for lang, doc in docs.items():
        assert _numbers(doc) == _numbers(docs["ru"]), lang                                   # the same numbers, only the words differ
        assert doc["market"]["regime"] == "рост" and doc["market"]["fng"]["label"] == "жадность"   # the app translates these itself
        assert len(every[lang]) == len(every["ru"]) >= 60, lang                              # every text exists in every language
        rows = {o["sym"]: o for o in doc["futures"]["open"]}
        assert all(len(rows[s]["checks"]) >= 2 for s in ("SOL", "ETH", "LINK", "BNB", "AVAX", "NEAR", "SUI", "NEWX")), lang
        assert rows["SEI"]["checks"] is None and rows["SEI"]["note"], lang                   # an old trade: a note, nothing invented
        if lang != "ru":                                                                     # translated, not copied
            same = {x for x in set(every[lang]) & set(every["ru"]) if len(x) > 12}
            assert not same - {"CRYPTO NOVA"}, (lang, same)
        if lang in ("en", "el", "es"):
            assert not [s for s in every[lang] if CYR.search(s)], lang
    en = {o["sym"]: o for o in docs["en"]["futures"]["open"]}
    assert docs["en"]["futures"]["status_ru"] is None or "main strategy" in docs["en"]["futures"]["status_ru"]
    assert en["AVAX"]["why_title"] == "Pullback in an uptrend" and en["AVAX"]["src_ru"] == "trend pullback"
    assert en["AVAX"]["work"] == "Today the system checked 40 coins against 8 conditions: the rules gave 2 signals, 1 passed every condition."
    assert docs["en"]["futures"]["scan"]["text"] == en["AVAX"]["work"]
    assert en["NEAR"]["work"].endswith("the market is not in uptrend mode — no buys, the money waits in dollars.")
    assert en["SOL"]["work"] == "At entry the system was checking 18 coins against 4 rules — “panic buy” fired on 1 of them."
    assert en["SOL"]["risk"]["hold"] == "24 hours — time-based exit, the target is only a guide" and en["SOL"]["why"].startswith("The market was sold off")
    assert en["NEWX"]["work"] == "The system watches 23 BingX futures contracts: only a new listing that passes every filter gets a short."
    assert [c["t"] for c in en["NEWX"]["checks"]] == ["New listing", "No pump", "Turnover", "Token"]
    assert docs["en"]["spot"]["last_action"]["text"] == "SOL grew to 10% of the portfolio, above its 5% plan — the excess is sold."
    assert [a["name"] for a in docs["en"]["spot"]["alloc"]] == ["Bitcoin", "Ether", "Solana", "large altcoins", "meme coins", "other", "dollars"]
    uk = {o["sym"]: o for o in docs["uk"]["futures"]["open"]}
    assert uk["AVAX"]["work"].startswith("Сьогодні система перевірила 40 монет за 8 умовами") and "угод" not in uk["AVAX"]["why"]
    assert uk["BNB"]["checks"][0]["v"].startswith("ф'ючерс на 0.31% нижче спота")
    el = {o["sym"]: o for o in docs["el"]["futures"]["open"]}
    assert el["SUI"]["work"].startswith("Τη στιγμή της εισόδου το σύστημα έλεγχε 60 altcoins") and el["SUI"]["risk"]["hold"].startswith("12 ώρες")
    es = {o["sym"]: o for o in docs["es"]["futures"]["open"]}
    assert es["ETH"]["checks"][0]["t"] == "Desplome de la vela" and docs["es"]["note_ru"].startswith("Cuentas demo de CRYPTO\u00a0NOVA")


def test_app_home_status_and_actions_per_language(tmp_path):
    d, t0 = str(tmp_path), 1792300000000
    prof = {p: {"equity": 10000.0, "equity_history": [{"time": t0, "equity": 10000.0}], "open": [],
                "closed": [{"symbol": "ARB", "side": "LONG", "pnl_usd": 191.6, "closed": t0}], "stats": {"trades": 1}}
            for p in ("cons", "bal", "aggr")}
    prof["own"] = {"open": [{"symbol": "AVAX", "side": "LONG", "grade": "P", "entry": 10.0, "stop": 8.5, "price": 10.5, "opened": t0 + 5}]}
    _w(d, "paper.json", {"since": t0, "profiles": prof})
    for act, want in (("BUY", "Bought XMR: the coin passed the portfolio's checks."), ("SELL", "Sold XMR by the portfolio's rules"),
                      ("ADD", "Topped up XMR"), ("SKIP", "Portfolio change: XMR.")):
        _w(d, "app_portfolio.json", {"index": 100.0, "recent_actions": [{"time": t0, "symbol": "XMR", "action": act, "why_ru": "Стоп −30%."}]})
        app_home.run(None, d, now=t0 + 60000)
        en = json.load(open(os.path.join(d, "app_home.en.json"), encoding="utf-8"))
        assert en["spot"]["last_action"]["text"].startswith(want), act
    ru = json.load(open(os.path.join(d, "app_home.json"), encoding="utf-8"))
    # 28.10 (build 46 QA): Russian gets the same plain sentence, never the engine's own words
    assert ru["spot"]["last_action"]["text"] == "Изменение в портфеле: XMR." and "1 сделка других" in ru["futures"]["status_ru"]
    texts = {lang: json.load(open(os.path.join(d, i18n.file_name("app_home", lang)), encoding="utf-8"))["futures"]["status_ru"]
             for lang in i18n.LANGS}
    assert texts["en"].endswith("Last trade: ARB, +$192, 18.10. Other CRYPTO\u00a0NOVA signals in progress: 1 trade.")
    assert "Остання угода: ARB, +$192" in texts["uk"] and "1 угода інших сигналів" in texts["uk"]
    assert "Τελευταία συναλλαγή: ARB" in texts["el"] and "1 συναλλαγή" in texts["el"]
    assert texts["es"].endswith("En curso: 1 operación de otras señales de CRYPTO\u00a0NOVA.")


def test_engine_owns_every_app_home_language():
    for lang in i18n.LANGS:
        assert i18n.file_name("app_home", lang) in engine.OWNED, lang
    site = open(os.path.join(ROOT, ".github", "workflows", "site.yml"), encoding="utf-8").read()
    for lang in OTHER:
        assert site.count(f"web/app_home.{lang}.json") == 2, lang       # paths-ignore (the engine pushes it) + the CI commit list


# ------------------------------------------------------------------------------------------------ ?lang= on the public feeds
def test_feeds_follow_lang(tmp_path):
    api, _ = make(tmp_path)
    web = api.web
    for lang in i18n.LANGS:
        _w(web, i18n.file_name("app_home", lang), {"asof": int(time.time() * 1000), "lang": lang, "futures": {}, "spot": {}})
    shutil.copy(os.path.join(WEB, "nova.json"), os.path.join(web, "nova.json"))
    for lang in OTHER:
        shutil.copy(os.path.join(WEB, f"nova.{lang}.json"), os.path.join(web, f"nova.{lang}.json"))
    _w(web, "academy.json", {"title": "Обучение"})
    _w(web, "academy.es.json", {"title": "Aprender"})
    _w(web, "app_btc.json", {"start_day": 1, "closes": [1.0]})
    get = lambda path: call(api, "GET", path)
    assert get("/v2/home")[1]["lang"] == "ru" and get("/v2/home?lang=en")[1]["lang"] == "en"
    assert get("/v2/home/?lang=uk&x=1")[1]["lang"] == "uk" and get("/v2/home?lang=EL")[1]["lang"] == "el"
    assert get("/v2/home?lang=de")[1]["lang"] == "ru" and get("/v2/home?lang=")[1]["lang"] == "ru"
    assert get("/v2/feed/app_home?lang=es")[1]["lang"] == "es"
    assert get("/v2/feed/nova?lang=el")[1]["greeting"].startswith("Γεια σας!") and get("/v2/feed/nova")[1]["greeting"].startswith("Здравствуйте")
    assert get("/v2/feed/academy?lang=es")[1]["title"] == "Aprender" and get("/v2/feed/academy?lang=en")[1]["title"] == "Обучение"
    assert get("/v2/feed/app_btc?lang=uk")[1]["closes"] == [1.0]
    assert get("/v2/feed/secrets?lang=en")[0] == 404 and get("/v2/feed/nova?lang=../../etc")[1]["lang"] == "ru"
    # a language file the engine stopped writing is not served once it is 10+ minutes older than the Russian one
    old = time.time() - 3600
    os.utime(os.path.join(web, "app_home.en.json"), (old, old))
    assert get("/v2/home?lang=en")[1]["lang"] == "ru"
    os.utime(os.path.join(web, "academy.es.json"), (old, old))                # static feeds: served whatever their age
    assert get("/v2/feed/academy?lang=es")[1]["title"] == "Aprender"
    os.remove(os.path.join(web, "app_home.uk.json"))
    assert get("/v2/home?lang=uk")[1]["lang"] == "ru"
    _w(web, "app_home.el.json", {})
    with open(os.path.join(web, "app_home.el.json"), "w") as f:
        f.write("{half")
    assert get("/v2/home?lang=el")[1]["lang"] == "ru"                         # unreadable → the Russian file


def test_nova_feed_in_every_language():
    k = nova.Knowledge(DATA)
    titles = {}
    for lang in i18n.LANGS:
        d = json.load(open(os.path.join(WEB, i18n.file_name("nova", lang)), encoding="utf-8"))
        assert d["lang"] == lang and "5" in d["greeting"] and "100" in d["greeting_vip"], lang
        assert "VIP" not in d["greeting"] + d["greeting_vip"], lang
        chips = d["suggest"] + [q for t in d["topics"] for q in t["qs"]]
        assert 6 <= len(d["suggest"]) <= 8 and len(d["topics"]) == 5 and all(3 <= len(t["qs"]) <= 4 for t in d["topics"]), lang
        assert len(set(chips)) == len(chips) and all(len(q) <= 60 for q in chips), lang
        faq = {i["q"]: i for i in k.faq(lang)}
        for q in chips:                                       # every chip has an exact FAQ answer in its language (the model may be down)
            assert q in faq and k.match(q, lang) is faq[q], (lang, q)
        titles[lang] = [t["title"] for t in d["topics"]]
    assert titles["en"] == ["How CRYPTO\u00a0NOVA works", "Portfolio & trades", "Money & safety", "Crypto & the market", "Learn"]
    connected = {"ru": "реальный счёт подключён", "en": "real account is connected", "uk": "реальний рахунок підключено",
                 "el": "πραγματικός σας λογαριασμός είναι συνδεδεμένος", "es": "cuenta real está conectada"}
    for lang, words in connected.items():
        assert words in json.load(open(os.path.join(WEB, i18n.file_name("nova", lang)), encoding="utf-8"))["greeting_vip"], lang


# ------------------------------------------------------------------------------------------------ FAQ and facts per language
NO_PROMISE = {"ru": "гарантирован", "en": "guaranteed", "uk": "гарантован", "el": "εγγυημέν", "es": "garantizad"}


def test_faq_and_facts_in_every_language():
    k = nova.Knowledge(DATA)
    ru = k.faq("ru")
    ids = [i["id"] for i in ru]
    for lang in i18n.LANGS:
        faq = k.faq(lang)
        assert [i["id"] for i in faq] == ids, lang                                   # the same items, the same order
        assert len({nova._norm(i["q"]) for i in faq}) == len(faq), lang
        for i in faq:
            a = i["a"]
            assert len(a) <= 650 and 2 <= len(re.findall(r"[.!?;](?=\s|$)", a)) <= 7, (lang, i["id"])
            assert not any(m in a for m in ("**", "##", "http", "VIP")) and a == nova.tidy(a), (lang, i["id"])
            assert NO_PROMISE[lang] not in a.lower(), (lang, i["id"])
            assert i["tags"] and all(isinstance(t, str) and t for t in i["tags"]), (lang, i["id"])
            assert k.match(i["q"], lang) is i, (lang, i["id"])
            for alt in i.get("alt") or []:
                assert k.match(alt, lang)["id"] == i["id"], (lang, alt)
        if lang in ("en", "el", "es"):
            assert not [i["id"] for i in faq if CYR.search(i["q"] + i["a"])], lang
        # 28.10 (build 46 QA): «CRYPTO NOVA» never wraps, thousands with a no-break space (ru / uk / el / es)
        assert not [i["id"] for i in faq if "CRYPTO NOVA" in i["q"] + i["a"]], lang
        by = {i["id"]: i["a"].replace("\u00a0", " ") for i in faq}
        assert "PUBLIC" in by["how_much"] and "demo" not in by["how_much"].lower() and "демо" not in by["how_much"], lang
        assert "$10 000" in by["how_works"] and "18" in by["vip"] and "BingX" in by["vip"] and "UID" in by["uid"], lang
        assert "126 210" in by["crash"] and "1 293 000" in by["etf"] and "21" in by["bitcoin"], lang
    facts = {f["id"]: f for f in k.facts("ru")}
    for lang in OTHER:
        tr = json.load(open(os.path.join(DATA, f"nova_facts.{lang}.json"), encoding="utf-8"))["facts"]
        assert [f["id"] for f in tr] == list(facts), lang                           # every fact translated
        mine = k.facts(lang)
        assert [f["id"] for f in mine] == list(facts) and all(f["source"] == facts[f["id"]]["source"] for f in mine), lang
        assert all(f["fact"] != facts[f["id"]]["fact"] for f in mine), lang
        assert "126 210" in next(f["fact"] for f in mine if f["id"] == "btc_ath"), lang
        system = k.system(lang)
        assert all(f["fact"] in system for f in mine) and nova.LANG[lang]["native"] in system and "ЯЗЫК ОТВЕТА" in system, lang
        assert nova.LANG[lang]["real"] in system and "{" not in system, lang
    assert k.ath() == (126210.0, "06.10.2025")


def test_faq_cross_language_and_fuzzy_match():
    k = nova.Knowledge(DATA)
    assert k.match("what's leverage", "en")["id"] == "leverage" and k.match("τι ειναι η μοχλευση", "el")["id"] == "leverage"
    assert k.match("que es el apalancamiento", "es")["id"] == "leverage"
    hit = k.match("Что такое плечо?", "uk")                       # a Ukrainian phone, the question typed in Russian → the Ukrainian answer
    assert hit["id"] == "leverage" and hit["q"] == "Що таке плече?"
    assert k.match("How much does it cost?", "es")["q"] == "¿Cuánto cuesta?"
    assert k.match("What is VIP?", "en")["id"] == "vip" and k.match("Что такое VIP и реальный счёт?")["q"] == "Что такое реальный счёт?"
    for lang, q in (("en", "What's the weather today?"), ("es", "¿Qué tiempo hace hoy?"), ("el", "Τι καιρό κάνει σήμερα;"),
                    ("uk", "Яка сьогодні погода?"), ("en", "Should I buy Solana now?")):
        assert k.match(q, lang) is None, q


# ------------------------------------------------------------------------------------------------ POST /v2/agent in the app's language
class Capture:
    available = True

    def __init__(self, answer="Answer.", exc=None):
        self.answer, self.exc, self.seen = answer, exc, []

    def complete(self, system, user, temperature=0.2):
        self.seen.append((system, user))
        if self.exc:
            raise self.exc
        return self.answer


def test_agent_answers_in_the_requested_language(tmp_path):
    api, _ = make(tmp_path)
    home_el = {"asof": int(time.time() * 1000), "futures": {"open": [{"sym": "AVAX", "side": "LONG", "src_ru": "υποχώρηση σε τάση",
                                                                     "why": "Ένα νόμισμα σε ανοδική τάση υποχώρησε."}]}}
    _w(api.web, "app_home.el.json", home_el)
    api.llm = cap = Capture()
    t = token("lang")
    c, d = call(api, "POST", "/v2/agent", {"q": "Τι είναι η μόχλευση;", "lang": "el"}, t)
    assert c == 200 and d["answer"] == "Answer." and d["agent"]["left"] == 4
    system, user = cap.seen[-1]
    assert "ЯЗЫК ОТВЕТА — греческий" in system and "Απάντα πάντα στα ελληνικά" in system and "«Κουμπαράς»" in system
    assert "Στις 10 Ιανουαρίου 2024" in system and "είμαι χαρούμενος" in system and "VIP" in system      # «don't say VIP» rule only
    assert "В: Τι είναι η μόχλευση;" in user and "ЯЗЫК ОТВЕТА: греческий" in user and user.endswith("ВОПРОС ПОЛЬЗОВАТЕЛЯ:\nΤι είναι η μόχλευση;")
    assert "«Ο λογαριασμός demo μου» на αρχική οθόνη" in user
    call(api, "POST", "/v2/agent", {"q": "Γιατί αγοράσατε AVAX;", "lang": "el"}, t)
    assert "Ένα νόμισμα σε ανοδική τάση υποχώρησε." in cap.seen[-1][1]           # the trade texts of app_home.el.json
    for body, want in (({"q": "What is leverage?", "lang": "en-US"}, "English"), ({"q": "Що таке плече?", "lang": "uk"}, "українською"),
                       ({"q": "¿Qué es el apalancamiento?", "lang": "es"}, "español")):
        call(api, "POST", "/v2/agent", body, t)
        assert want in cap.seen[-1][0], body
    for body in ({"q": "Что такое плечо?"}, {"q": "Что такое плечо?", "lang": "fr"}, {"q": "Что такое плечо?", "lang": ["en"]}):
        call(api, "POST", "/v2/agent", body, token("ru"))
        system, user = cap.seen[-1]
        assert "ЯЗЫК ОТВЕТА" not in system + user and "По-русски, на «вы»" in system and "В: Что такое плечо?" in user, body


def test_agent_fallbacks_in_the_requested_language(tmp_path):
    api, _ = make(tmp_path)
    t = token("fb")
    api.llm = Capture(exc=RuntimeError("HTTP 500: upstream"))
    c, d = call(api, "POST", "/v2/agent", {"q": "Τι είναι η μόχλευση;", "lang": "el"}, t)       # the model is down → the Greek FAQ answer
    assert (c, d["source"], d["agent"]["left"]) == (200, "faq", 5) and d["answer"].startswith("Μόχλευση είναι χρήματα")
    c, d = call(api, "POST", "/v2/agent", {"q": "What is a real account?", "lang": "en"}, t)
    assert d["source"] == "faq" and "VIP" not in d["answer"] and d["answer"].startswith("A “Real account”")
    c, d = call(api, "POST", "/v2/agent", {"q": "Что такое плечо?", "lang": "uk"}, t)
    assert d["source"] == "faq" and d["answer"].startswith("Плече — це гроші в борг")
    c, d = call(api, "POST", "/v2/agent", {"q": "¿Qué tiempo hace hoy?", "lang": "es"}, t)
    assert (d["error"], d["answer"]) == ("busy", nova.busy("es")) and api.agent_last["lang"] == "es"
    assert call(api, "POST", "/v2/agent", {"q": " ", "lang": "en"}, t)[1]["answer"] == nova.bad_question("en")
    assert call(api, "POST", "/v2/agent", {"q": " ", "lang": "zz"}, t)[1]["answer"] == nova.BAD_QUESTION
    api.llm = Capture()
    t2 = token("lim")
    for _ in range(5):
        call(api, "POST", "/v2/agent", {"q": "What is leverage?", "lang": "en"}, t2)
    for lang in i18n.LANGS:
        c, d = call(api, "POST", "/v2/agent", {"q": "One more?", "lang": lang}, t2)
        assert (c, d["error"], d["answer"]) == (200, "limit", nova.limit(lang)), lang
    from cnq.api.server import _body_lang
    assert _body_lang(b'{"q":"x","lang":"el"}') == "el" and _body_lang(b"{bad") == "ru" and _body_lang(b"[1]") == "ru"


def test_api_health_checks_languages():
    wf = open(os.path.join(ROOT, ".github", "workflows", "api-health.yml"), encoding="utf-8").read()
    assert "/v2/home?lang=en" in wf and "/v2/feed/nova?lang=el" in wf


@pytest.mark.parametrize("lang", OTHER)
def test_gitignore_keeps_the_translations(lang):
    import subprocess
    for p in (f"data/nova_faq.{lang}.json", f"data/nova_facts.{lang}.json"):
        r = subprocess.run(["git", "-C", ROOT, "check-ignore", "-q", p])
        assert r.returncode == 1, p                                                   # 1 = not ignored


def test_polish_numbers_brand_and_minus_per_language():
    """28.10 (build 46 QA): every server text as the app shows it — «CRYPTO NOVA» never wraps, the true minus, the language's
    decimal comma and a no-break thousands space (ru / uk / el / es), English keeps its dot; dates and versions stay"""
    N = "\u00a0"
    assert i18n.polish("CRYPTO NOVA: −3.5% и $1 234.50, цена 0.7760.", "ru") == f"CRYPTO{N}NOVA: −3,5% и $1{N}234,50, цена 0,7760."
    assert i18n.polish("CRYPTO NOVA: -3.5% and $1,234.50", "en") == f"CRYPTO{N}NOVA: \u22123.5% and $1,234.50"
    assert i18n.polish("с 22.10.2026, на 22.10. Версия 2.0, 1.2.3, 2025-2026", "uk") == "с 22.10.2026, на 22.10. Версия 2.0, 1.2.3, 2025-2026"
    assert i18n.polish("https://x.io/a-1.5", "ru") == "https://x.io/a-1.5" and i18n.polish(7.5, "ru") == 7.5
    doc = i18n.polish_doc({"sym": "BTC-1.5", "text": "7.5%", "rows": [{"url": "a 1.5", "why": "1.5 раза"}], "n": 1.5}, "es")
    assert doc == {"sym": "BTC-1.5", "text": "7,5%", "rows": [{"url": "a 1.5", "why": "1,5 раза"}], "n": 1.5}
