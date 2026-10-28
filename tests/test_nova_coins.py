"""NOVA on a coin (22.10.2026, owner: «что ты думаешь о монете Саи» — SUI said by voice — got a vague, rambling answer):
the coin resolver (cnq/api/coins.py) and the system's own verdict (cnq/api/coin_verdict.py), wired into POST /v2/agent and the
closed system's POST /v2/admin/nova."""
import json
import os
import time

import pytest

from cnq.api import coin_verdict as V
from cnq.api import coins as C
from cnq.api import pf_review
from tests.test_api import call, make, token

NFA = V.T["nfa"]


def _row(sym, name, status, why, checks, **kw):
    return {"symbol": sym, "name": name, "status": status, "status_ru": pf_review.STATUS_LABEL[status]["ru"], "why_ru": why,
            "checks": [{"key": k, "t": t, "ok": ok} for k, t, ok in checks], **kw}


@pytest.fixture()
def web(tmp_path):
    w = tmp_path / "web"
    w.mkdir()
    near = _row("NEAR", "NEAR Protocol", "buy", "Анкета 45, консультант 93, тренд вверх, BTC бычий — можно брать", [
        ("rating", "анкета 45/100 (нужно от 40)", True), ("consultant", "консультант: тренд 92/100, фундамент 93/100 (нужно от 60)", True),
        ("overheat", "за 7 дней +8.5%, за 30 дней +11.9%", True), ("dilution", "в обращении ~99% токенов", True),
        ("ath", "от исторического максимума −76% (предел −90%)", True)], price=5.12, support={"score": 66, "lines": []})
    sui = _row("SUI", "Sui", "wait", "Анкета 25/100 ниже порога 40 — ждём", [
        ("rating", "анкета 25/100 (нужно от 40)", False), ("consultant", "консультант: тренд 51/100, фундамент 77/100", None),
        ("overheat", "за 7 дней −3.9%, за 30 дней −28.0%", True), ("dilution", "в обращении ~38% токенов", False),
        ("ath", "от исторического максимума −82% (предел −90%)", True)], price=2.31)
    apt = _row("APT", "Aptos", "avoid", "−96% от исторического максимума — монета «мёртвая», не берём", [
        ("ath", "от исторического максимума −96% (предел −90%)", False)], price=0.7706)
    statuses = {r["symbol"]: {k: r[k] for k in ("status", "status_ru", "why_ru", "price")} for r in (near, sui, apt)}
    statuses.update({
        "AVAX": {"status": "avoid", "status_ru": "не брать", "why_ru": "−94% от исторического максимума — монета «мёртвая», не берём", "price": 9.1},
        "ETH": {"status": "add", "status_ru": "можно добрать", "why_ru": "Уже в портфеле, цена в зоне поддержки — можно добрать", "price": 2404.82},
        "BTC": {"status": "buy", "status_ru": "можно брать", "why_ru": "Ядро, анкета 55/100, BTC бычий — можно брать частями", "price": 81631},
        "WLD": {"status": "avoid", "status_ru": "не брать", "why_ru": "−95% от исторического максимума — монета «мёртвая», не берём", "price": 0.6},
        "WLFI": {"status": "wait", "status_ru": "ждём", "why_ru": "Консультант не выделил — тренд не подтверждён", "price": 0.14},
    })
    (w / "ideas.json").write_text(json.dumps({"rows": [near, sui, apt], "statuses": statuses,
                                              "support": {"SUI": {"score": 70, "lines": [{"k": "exch", "t": "спот: Binance, OKX"}]}}}))
    (w / "markets.json").write_text(json.dumps({"top": [
        {"symbol": "BTC", "name": "Bitcoin", "price": 81631, "ath_chg": -35.3}, {"symbol": "AVAX", "name": "Avalanche", "price": 9.1, "ath_chg": -94.2},
        {"symbol": "SUI", "name": "Sui", "price": 2.31, "ath_chg": -82.0}, {"symbol": "TON", "name": "Toncoin", "price": 2.1, "ath_chg": -75.0},
        {"symbol": "WLD", "name": "Worldcoin"}, {"symbol": "WLFI", "name": "World Liberty Financial"}]}))
    (w / "portfolio.json").write_text(json.dumps({"portfolios": {"ideas": {"positions": [
        {"symbol": "BTC", "weight": 0.3309, "entry": 81571.43, "status": "OPEN", "levels": {"stop": None, "take_half": None}},
        {"symbol": "NEAR", "weight": 0.075, "entry": 4.98, "status": "OPEN", "levels": {"stop": 3.98, "take_half": 7.5}}]}}}))
    (w / "app_home.json").write_text(json.dumps({"asof": int(time.time() * 1000), "total": {"usd": 1}, "spot": {}, "futures": {"open": [
        {"sym": "APT", "side": "LONG", "entry": 0.79155828, "price": 0.7706, "stop": 0.743916, "target": 0.93448512, "pct": -2.6}]}}))
    return w


@pytest.fixture()
def data(web):
    return pf_review.Data(str(web))


def home(web):
    return json.loads((web / "app_home.json").read_text())


# ------------------------------------------------------------------------------------------------ aliases
ALIAS_CASES = [
    # the owner's list (ru, as voice input writes it)
    ("что ты думаешь о монете Саи", "SUI"), ("сай", "SUI"), ("суи", "SUI"), ("биток", "BTC"), ("биткоин", "BTC"), ("BTC", "BTC"),
    ("эфир", "ETH"), ("эфирчик", "ETH"), ("эфириум", "ETH"), ("кефир", "ETH"), ("ада", "ADA"), ("кардано", "ADA"),
    ("авакс", "AVAX"), ("аваланч", "AVAX"), ("сол", "SOL"), ("солана", "SOL"), ("рипл", "XRP"), ("иксарпи", "XRP"),
    ("икс эр пи", "XRP"), ("догик", "DOGE"), ("доги", "DOGE"), ("тон", "TON"), ("линк", "LINK"), ("чейнлинк", "LINK"),
    ("хайп", "HYPE"), ("гиперликвид", "HYPE"), ("пепе", "PEPE"), ("ниар", "NEAR"), ("апт", "APT"), ("аптос", "APT"),
    # Russian cases
    ("что думаешь о биткоине", "BTC"), ("стоит ли брать солану", "SOL"), ("а с эфиром что", "ETH"), ("про аваксы", "AVAX"),
    # uk / en / el / es
    ("що думаєш про біткоїн", "BTC"), ("чи варто брати ефір", "ETH"), ("what do you think about Sui", "SUI"),
    ("should I buy NEAR?", "NEAR"), ("is ethereum worth buying", "ETH"), ("τι πιστεύεις για το σολάνα", "SOL"),
    ("αξίζει το μπιτκόιν;", "BTC"), ("¿Qué opinas de cardano?", "ADA"), ("¿vale la pena comprar solana?", "SOL"),
    # speech-to-text spellings
    ("етериум стоит брать", "ETH"), ("что думаешь о монете кардона", "ADA"), ("аваланче", "AVAX"), ("etherium", "ETH"),
    ("solona", "SOL"), ("биткойн кэш", "BCH"),
]


@pytest.mark.parametrize("text,sym", ALIAS_CASES)
def test_aliases(text, sym, data):
    u = C.Universe.build(data)
    assert C.resolve(text, u).coins == [sym]
    assert C.resolve(text).coins == [sym]                   # the dictionary alone, without the system's files


@pytest.mark.parametrize("text", ["a ton of money", "Сколько я заработал?", "стоит ли брать кредит", "Что такое плечо?",
                                  "I'm near the end", "Это безопасно?", "Как вывести деньги?", "Что по рынку?"])
def test_not_a_coin(text, data):
    assert C.resolve(text, C.Universe.build(data)).coins == []


def test_verdict_intent(data):
    u = C.Universe.build(data)
    yes = ["что ты думаешь о монете Саи", "стоит ли брать биток", "эфир перспективный?", "Солана?", "SUI", "догик норм монета?",
           "should I buy near?", "¿Qué opinas de cardano?", "τι πιστεύεις για το σολάνα", "що думаєш про біткоїн"]
    no = ["Почему вошли в AVAX?", "Что такое биткоин?", "Что такое копилка и почему биткоин?", "как купить биткоин"]
    for q in yes:
        assert C.is_verdict_question(q, C.resolve(q, u)), q
    for q in no:
        assert not C.is_verdict_question(q, C.resolve(q, u)), q


# ------------------------------------------------------------------------------------------------ the ask-back
def test_ask_back_on_two_coins(data, web):
    out = V.answer("что думаешь о ворлд", "ru", data, home(web))
    assert out["source"] == "clarify" and out["coins"] == ["WLD", "WLFI"]
    assert out["answer"] == "Вы про WLD (Worldcoin) или про WLFI (World Liberty Financial)?"
    assert V.answer("what do you think about world", "en", data, home(web))["answer"].startswith("Do you mean WLD (Worldcoin) or")
    # a fuzzy word close to two coins asks too
    u = C.Universe.build(data)
    u._fuzz += [("sunaa", "AAA"), ("sunab", "BBB")]
    assert C.resolve("что думаешь о сунах", u).ambiguous in (("AAA", "BBB"), ("BBB", "AAA"))


# ------------------------------------------------------------------------------------------------ verdicts
def _shape(text, lang="ru"):
    lines = [ln for ln in text.split("\n") if ln.strip()]
    assert not lines[0].startswith("•") and lines[-1] == NFA[lang]
    assert 3 <= sum(ln.startswith("•") for ln in lines) <= 5
    return lines


def test_verdict_buy_held(data, web):
    out = V.answer("стоит ли брать ниар?", "ru", data, home(web))
    assert out["source"] == "coin" and out["coins"] == ["NEAR"] and out["llm"] is False
    lines = _shape(out["answer"])
    assert lines[0] == "NEAR (NEAR Protocol): по правилам CRYPTO NOVA сейчас — подходит для покупки."
    body = out["answer"]
    assert "оценка проекта 45 из 100 (минимум 40)" in body and "консультант системы: тренд 92 из 100, фундамент 93 из 100" in body
    assert "В споте CRYPTO NOVA: доля 7.5%, вход $4.98, стоп $3.98, цель $7.5" in body
    assert "Поддержка проекта: 66 из 100." in body and "анкета" not in body.lower() and "можно брать" not in body


def test_verdict_wait(data, web):
    out = V.answer("что ты думаешь о монете Саи", "ru", data, home(web))
    lines = _shape(out["answer"])
    assert lines[0] == "SUI: по правилам CRYPTO NOVA сейчас — пока ждём."
    body = out["answer"]
    assert "Не пройдено: оценка проекта 25 из 100 (минимум 40); разлоки: в обращении ~38% токенов." in body
    assert "• Почему: оценка проекта 25 из 100 — ниже минимума системы (40), пока не покупаем." in body
    assert "В портфеле CRYPTO NOVA её сейчас нет." in body and "Поддержка проекта: 70 из 100." in body


def test_verdict_avoid_dead_coin(data, web):
    out = V.answer("апт стоит покупать?", "ru", data, home(web))
    lines = _shape(out["answer"])
    assert lines[0].startswith("APT (Aptos): не покупаем. Цена на 96% ниже исторического максимума") and "мёртв" not in lines[0]
    assert "обещать возврат цены я не буду" in lines[0]
    assert "• Фьючерсы: открыта сделка лонг (на рост), вход $0.7916, стоп $0.7439, цель $0.9345, сейчас −2.6%." in lines
    for w in ("вырастет", "вернётся", "отскоч", "гарант"):
        assert w not in out["answer"].lower()
    # a coin the table rates only by its status (no row): the ATH comes from web/markets.json
    av = V.answer("авакс?", "ru", data, home(web))["answer"]
    assert av.startswith("AVAX (Avalanche): не покупаем. Цена на 94% ниже") and av.endswith(NFA["ru"])


def test_no_data_said_honestly(data, web):
    out = V.answer("тон перспективный?", "ru", data, home(web))
    assert out["source"] == "coin" and out["answer"].startswith("По TON (Toncoin) у CRYPTO NOVA данных нет")
    assert "нет в списке, который система проверяет" in out["answer"]
    out = V.answer("что думаешь о монете Зюзя", "ru", data, home(web))
    assert out["answer"].startswith("По «Зюзя» у CRYPTO NOVA данных нет")
    assert V.answer("стоит ли брать кредит", "ru", data, home(web)) is None          # not a coin question: NOVA as before
    assert V.answer("Почему вошли в AVAX?", "ru", data, home(web)) is None


# ------------------------------------------------------------------------------------------------ the model only phrases
class Phrase:
    """a model that rephrases the draft (or answers with whatever it is given)"""

    def __init__(self, fn):
        self.fn, self.calls, self.last_finish, self.seen = fn, 0, "stop", []

    def available(self):
        return True

    def complete(self, system, user, temperature=0.2):
        self.calls += 1
        self.seen.append((system, user))
        payload = json.loads(user.split("JSON:\n", 1)[1])
        return self.fn(payload)


def _nice(p):
    d = p["draft"].replace("по правилам CRYPTO NOVA сейчас", "смотрите, по нашим правилам сейчас").replace("Почему:", "Причина простая:")
    return d.rsplit("\n\n", 1)[0] + "\n\nЭто не финансовый совет от CRYPTO NOVA."


def test_model_phrases_but_never_invents_numbers(data, web):
    good = Phrase(_nice)
    out = V.answer("стоит ли брать ниар?", "ru", data, home(web), llm=good)
    assert out["llm"] is True and "смотрите, по нашим правилам сейчас — подходит для покупки" in out["answer"]
    assert out["answer"].endswith("\n\n" + NFA["ru"]) and "Это не финансовый совет от CRYPTO NOVA." not in out["answer"]
    system, user = good.seen[-1]
    assert "только из JSON" in system and json.loads(user.split("JSON:\n", 1)[1])["coins"][0]["status_label"] == "подходит для покупки"

    def invents(p):
        return _nice(p).replace("• Причина простая:", "• К 2027 году может стоить $12. Причина простая:")
    out = V.answer("стоит ли брать ниар?", "ru", data, home(web), llm=Phrase(invents))
    assert out["llm"] is False and "$12" not in out["answer"] and out["answer"] == V.template(
        [V.card("NEAR", data, home(web), "ru", C.Universe.build(data))], "ru")
    for bad in (lambda p: _nice(p).replace("подходит для покупки", "отличная покупка"),           # the status changed
                lambda p: _nice(p).replace("Причина простая:", "Гарантированно растёт:"),    # a promise
                lambda p: p["draft"].split("\n")[0],                                       # no bullets
                lambda p: (_ for _ in ()).throw(RuntimeError("timeout"))):                  # the model failed
        out = V.answer("стоит ли брать ниар?", "ru", data, home(web), llm=Phrase(bad))
        assert out["llm"] is False and out["answer"].startswith("NEAR (NEAR Protocol): по правилам CRYPTO NOVA сейчас — подходит для покупки.")


def test_number_check():
    payload = {"coins": [{"entry": "$81 571", "checks": [{"text": "анкета 41/100 (нужно от 40)"}], "pct": -12.7, "p": "$0.7916"}]}
    assert V.invented_numbers("анкета 41 из 100, порог 40, вход $81 571, за месяц −12.7%, цена 0,7916", payload) == []
    assert V.invented_numbers("вход $81 571 и к 2026 году вдвое", payload) == ["2026"]
    assert V.invented_numbers("около $81 600", payload) == ["81 600"]
    assert V.invented_numbers("цена около $0.79", payload) == []                         # a rounding of a number in the data


# ------------------------------------------------------------------------------------------------ language
@pytest.mark.parametrize("lang,q,head,label", [
    ("en", "should I buy near?", "NEAR (NEAR Protocol): by CRYPTO NOVA's rules, right now — worth buying.", "In the CRYPTO NOVA portfolio: 7.5% share"),
    ("uk", "чи варто брати ніар", "NEAR (NEAR Protocol): за правилами CRYPTO NOVA зараз — можна брати.", "У портфелі CRYPTO NOVA: частка 7.5%"),
    ("el", "αξίζει το NEAR;", "NEAR (NEAR Protocol): με τους κανόνες του CRYPTO NOVA, τώρα — για αγορά.", "Στο χαρτοφυλάκιο CRYPTO NOVA: μερίδιο 7.5%"),
    ("es", "¿vale la pena comprar NEAR?", "NEAR (NEAR Protocol): según las reglas de CRYPTO NOVA, ahora — para comprar.", "En la cartera de CRYPTO NOVA: peso 7.5%"),
    ("ru", "стоит ли брать NEAR", "NEAR (NEAR Protocol): по правилам CRYPTO NOVA сейчас — подходит для покупки.", "В споте CRYPTO NOVA: доля 7.5%"),
])
def test_reply_language(lang, q, head, label, data, web):
    out = V.answer(q, lang, data, home(web))
    lines = _shape(out["answer"], lang)
    assert lines[0] == head and label in out["answer"]
    if lang != "ru":
        assert not any(w in out["answer"] for w in ("анкета", "Пройдено")) or lang == "uk"


def test_language_of_the_question_does_not_matter(data, web):
    """the app's language decides: a Russian voice question on an English phone gets English"""
    out = V.answer("что ты думаешь о монете Саи", "en", data, home(web))
    assert out["answer"].startswith("SUI: by CRYPTO NOVA's rules, right now — wait.") and out["answer"].endswith(NFA["en"])
    assert V.answer("тон?", "es", data, home(web))["answer"].startswith("CRYPTO NOVA no tiene datos sobre TON (Toncoin)")
    assert V.answer("что думаешь о ворлд", "el", data, home(web))["answer"].endswith(";")


# ------------------------------------------------------------------------------------------------ the API
def test_agent_route_answers_a_coin(tmp_path, web):
    (tmp_path / "x").mkdir()
    api, fake = make(tmp_path / "x")
    api.web = str(web)
    api.pf_data = pf_review.Data(str(web))
    t = token("coin")
    c, d = call(api, "POST", "/v2/agent", {"q": "что ты думаешь о монете Саи", "lang": "ru"}, t)
    assert c == 200 and d["source"] == "coin" and d["answer"].startswith("SUI: по правилам CRYPTO\u00a0NOVA сейчас — пока ждём.")
    assert d["agent"]["left"] == 5 and api.agent_last["result"] == "coin_coin"         # the draft (the fake model refused) is free
    api.llm = Phrase(_nice)
    c, d = call(api, "POST", "/v2/agent", {"q": "стоит ли брать ниар", "lang": "ru"}, t)
    assert c == 200 and "source" not in d and "смотрите, по нашим правилам" in d["answer"] and d["agent"]["left"] == 4
    c, d = call(api, "POST", "/v2/agent", {"q": "что думаешь о ворлд", "lang": "en"}, t)
    assert d["source"] == "clarify" and d["answer"].startswith("Do you mean WLD") and d["agent"]["left"] == 4
    api.llm = fake
    c, d = call(api, "POST", "/v2/agent", {"q": "Сколько я заработал?", "lang": "ru"}, t)   # any other question: NOVA as before
    assert c == 200 and d["answer"] == "Ответ простыми словами." and "source" not in d
    c, d = call(api, "POST", "/v2/agent", {"q": "Είναι αργά για να αγοράσω bitcoin;", "lang": "el"}, t)   # an app chip: its FAQ
    assert c == 200 and d["answer"] == "Ответ простыми словами." and "source" not in d


def test_admin_nova_answers_a_coin(tmp_path, web):
    (tmp_path / "x").mkdir()
    api, _fake = make(tmp_path / "x")
    api.web, api.pf_data = str(web), pf_review.Data(str(web))
    keep = os.environ.pop("CNQ_ADMIN_TOKEN", None)
    os.environ["CNQ_ADMIN_TOKEN"] = "t" * 40
    try:
        body = json.dumps({"system": "sys", "user": 'Контекст панели (JSON):\n{"coins": []}\n\nВопрос: апт стоит брать?'}).encode()
        c, d = api.handle("POST", "/v2/admin/nova", {"authorization": "Bearer " + "t" * 40}, body)
        assert c == 200 and d["source"] == "coin" and d["answer"].startswith("APT (Aptos): не покупаем.") and isinstance(d["ms"], int)
    finally:
        os.environ.pop("CNQ_ADMIN_TOKEN", None)
        if keep is not None:
            os.environ["CNQ_ADMIN_TOKEN"] = keep


def test_core_coin_verdict_uses_the_real_accounts_live_share(data, web):
    """28.10 (build 46 QA): a core coin (BTC, ETH, SOL) is the core rule — no stop line, held and topped up by its plan — and the
    share is the coin's share of the REAL account's spot now (app_home public_portfolio), not the model's old weight"""
    h = home(web)
    pf = json.loads((web / "portfolio.json").read_text())
    pf["portfolios"]["ideas"]["positions"][0].update({"target_weight": 0.3, "bucket": "core"})
    (web / "portfolio.json").write_text(json.dumps(pf))
    h["public_portfolio"] = {"spot": {"coins": [{"sym": "BTC", "share_spot": 29.27}, {"sym": "SOL", "share_spot": 5.42}]}}
    out = V.answer("стоит ли брать биткоин", "ru", pf_review.Data(str(web)), h)
    lines = _shape(out["answer"])
    assert lines[0] == "BTC (Bitcoin) — в ядре портфеля: система держит монету и сейчас не докупает."
    assert "Доля в споте CRYPTO NOVA: 29.3% (план 30%); ядро система держит без стопа и без цели." in out["answer"]
    assert "стоп $" not in out["answer"] and "можно брать" not in out["answer"]
    h["public_portfolio"]["spot"]["coins"][0]["share_spot"] = 12.0                  # far below ¾ of the plan → the system tops it up
    assert _shape(V.answer("биткоин?", "ru", pf_review.Data(str(web)), h)["answer"])[0] == \
        "BTC (Bitcoin) — в ядре портфеля: доля ниже плана, система докупит монету по своим правилам."
