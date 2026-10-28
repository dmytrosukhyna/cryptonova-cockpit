"""«Разбор портфеля» (build 45, owner 22.10): the parsers on fake OKX / BingX / Binance exports, the review by the system's rules,
the 5 languages, the time budget, the per-user limits and the promise that nothing is kept."""
import json
import os
import re
import shutil
import time

import pytest

from cnq.api import pf_parse as P, pf_review as R
from cnq.api.server import Api, vision_config
from cnq.api.store import Store
from cnq.api.auth import FirebaseVerifier
from tests.test_api import KEY, token

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "portfolio")
EXPECTED = json.load(open(os.path.join(FIX, "expected.json"), encoding="utf-8"))
WEB = os.path.join(ROOT, "web")
DATA_FILES = ("ideas", "quality", "altseason", "portfolio", "markets")
LANGS = ("ru", "en", "uk", "el", "es")
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")
CYR = re.compile("[А-Яа-яЁёЇїІіЄєҐґ]")
RU_ONLY = re.compile("[ЫыЭэЪъЁё]")


def _fixture(name: str) -> bytes:
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


def _known() -> set:
    return R.Data(WEB).known()


def _parse(name: str, known=None) -> dict:
    data, known = _fixture(name), known or _known()
    kind = P.sniff(data)
    if kind == "csv":
        return P.parse_table(P.read_csv(data), known)
    if kind == "xlsx":
        return P.best_table(P.read_xlsx(data), known)
    if kind == "pdf":
        return P.parse_text(P.pdf_text(data), known)
    raise AssertionError(f"{name}: {kind}")


def _correct(got: list[dict], want: dict) -> int:
    g = {h["sym"]: h["amount"] for h in got}
    return sum(1 for s, a in want.items() if s in g and abs(g[s] - a) <= 1e-9 * max(1.0, abs(a)))


class FakeLLM:
    def __init__(self, reply=""):
        self.reply, self.calls = reply, 0

    def available(self):
        return True

    def complete(self, system, user, temperature=0.2):
        self.calls += 1
        return self.reply


def make_api(tmp_path, llm=None):
    web = tmp_path / "web"
    web.mkdir()
    for n in DATA_FILES + ("app_home",):
        src = os.path.join(WEB, f"{n}.json")
        if os.path.exists(src):
            shutil.copy(src, web / f"{n}.json")
    (tmp_path / "data").mkdir()
    api = Api(Store(str(tmp_path / "data" / "api.sqlite")), FirebaseVerifier("crypto-nova", keys={"k1": KEY.public_key()}), str(web),
              llm or FakeLLM())
    return api


def post_file(api, data: bytes, lang="ru", uid="u1"):
    return api.handle("POST", f"/v2/portfolio/parse?lang={lang}", {"authorization": f"Bearer {token(uid)}"}, data)


def post_review(api, holdings, lang="ru", uid="u1"):
    return api.handle("POST", "/v2/portfolio/review", {"authorization": f"Bearer {token(uid)}"},
                      json.dumps({"holdings": holdings, "lang": lang}).encode())


# ------------------------------------------------------------------------------------------------ the parsers
def test_samples_cover_three_exchanges_in_csv_and_xlsx():
    tables = [n for n in EXPECTED if n.endswith((".csv", ".xlsx"))]
    assert len(tables) >= 6
    for ex in ("okx", "bingx", "binance"):
        assert any(n.startswith(ex) and n.endswith(".csv") for n in tables), ex
        assert any(n.startswith(ex) and n.endswith(".xlsx") for n in tables), ex


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_parser_reads_each_sample(name):
    res = _parse(name)
    want = EXPECTED[name]
    ok = _correct(res["holdings"], want)
    extra = {h["sym"] for h in res["holdings"]} - set(want)
    assert ok / len(want) >= 0.95, (name, ok, len(want), res["holdings"])
    assert not extra, (name, extra)


def test_parser_accuracy_over_all_samples_is_95_pct():
    total = sum(len(w) for w in EXPECTED.values())
    ok = sum(_correct(_parse(n)["holdings"], w) for n, w in EXPECTED.items())
    assert ok / total >= 0.95, (ok, total)


def test_parser_details():
    assert P.num("1 749,52", comma_dec=True) == 1749.52
    assert P.num("2,310.00") == 2310.0 and P.num("1.234,5") == 1234.5 and P.num("0.5 BTC") == 0.5 and P.num("--") is None
    assert P.coin("Bitcoin (BTC)") == "BTC" and P.coin("BTC-USDT") is None and P.coin("Total") is None
    assert P.norm_sym("LDBTC", {"BTC"}) == "BTC" and P.norm_sym("LDO", {"LDO", "O"}) == "LDO"
    assert _parse("okx_bills.csv")["mode"] == "bills" and _parse("binance_transactions.csv")["mode"] == "ledger"
    assert _parse("okx_assets.xlsx")["exchange"] == "okx" and _parse("bingx_spot_assets.csv")["exchange"] == "bingx"
    assert _parse("binance_balances.xlsx")["exchange"] == "binance"
    # dollar values written in the file are kept (an unknown coin can still be counted)
    ton = [h for h in _parse("bingx_spot_assets.csv")["holdings"] if h["sym"] == "TON"][0]
    assert ton["value_usd"] == 121.5


def test_sniff_and_bad_files():
    assert P.sniff(_fixture("statement.pdf")) == "pdf" and P.sniff(_fixture("okx_assets.xlsx")) == "xlsx"
    assert P.sniff(b"\x89PNG\r\n\x1a\n....") == "image" and P.sniff(b"\xff\xd8\xff\xe0..") == "image"
    assert P.sniff(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1") == "xls"
    assert P.sniff(b"PK\x03\x04garbage") is None and P.sniff(b"\x00\x01\x02\x00binary") is None


# ------------------------------------------------------------------------------------------------ the review
def _portfolios():
    known = _known()
    out = {n: _parse(n, known)["holdings"] for n in EXPECTED}
    out["all_cash"] = [{"sym": "USDT", "amount": 5000}]
    out["all_btc"] = [{"sym": "BTC", "amount": 1}]
    out["memes"] = [{"sym": s, "amount": a} for s, a in (("PEPE", 1e9), ("BONK", 1e9), ("WIF", 5000), ("PENGU", 100000), ("DOGE", 20000))]
    out["unknown"] = [{"sym": "ZZZQ", "amount": 10, "value_usd": 500}, {"sym": "QWERTY", "amount": 3}, {"sym": "BTC", "amount": 0.01}]
    return out


@pytest.mark.parametrize("lang", LANGS)
def test_every_recommendation_cites_a_rule(lang):
    data = R.Data(WEB)
    sources = {R.SRC[k][lang] for k in R.SRC}
    for name, hs in _portfolios().items():
        out = R.review(hs, data, lang)
        assert "error" not in out, (name, out)
        assert 3 <= len(out["recs"]) <= 5, (name, out["recs"])
        for rec in out["recs"]:
            assert rec["rule"] and rec["rule_id"] in R.RULE and any(s in rec["rule"] for s in sources), (name, rec)
            assert rec["text"] and "{" not in rec["text"], (name, rec)
        assert out["disclaimer"] == R.TXT["disclaimer"][lang]
        txt = json.dumps(out, ensure_ascii=False)
        assert not EMOJI.search(txt), name
        assert not re.search(r"купите|buy now|compra ya|αγοράστε|купуйте", txt, re.I), name
        assert not re.search(r"\bVPN\b", txt), name
        if lang in ("en", "el", "es"):
            texts = [out["intro"], out["unpriced_note"], out["disclaimer"], out["compare"]["title"], out["compare"]["hold"],
                     out["compare"]["alt"], out["compare"]["match"]] + [r["reason"] + r["status_label"] for r in out["rows"]] \
                + [r["rule"] + r["text"] for r in out["recs"]] + [r["label"] for r in out["compare"]["rows"]]
            bad = [t for t in texts if CYR.search(t)]
            assert not bad, (name, bad)
        if lang == "uk":
            assert not RU_ONLY.search(txt.replace("«мёртвая»", "")), name


def test_review_numbers_and_comparison():
    out = R.review([{"sym": "BTC", "amount": 0.1}, {"sym": "USDT", "amount": 1000}, {"sym": "ADA", "amount": 1000}], R.Data(WEB), "ru")
    assert abs(sum(r["share_pct"] for r in out["rows"]) - 100) < 0.05
    assert {r["sym"]: r["status"] for r in out["rows"]}["USDT"] == "cash"
    assert [r["key"] for r in out["compare"]["rows"]] == ["core", "buy", "small", "other", "cash"]
    assert 0 <= out["compare"]["match_pct"] <= 100 and out["compare"]["coins"]
    assert out["total_usd"] > 1000 and out["total"].startswith("$")


def test_reasons_are_translated():
    samples = ["Анкета 16/100 ниже порога 40 — ждём", "Оборот $107K в сутки — цену легко сдвинуть",
               "−100% от исторического максимума — монета «мёртвая», не берём", "Уже +130% за 30 дней — не догоняем, ждём отката",
               "В обращении ~40% токенов — впереди разлоки, ждём", "Мем-деск: красный флаг: топ-10 кошельков держат 81%",
               "Наш портфель «Умеренный» у стопа (−12%) — не добираем", "Через 5 дн. разлок 12% монет в обращении — до разлока не берём: в 2024",
               "Рывок +45% — ядро тоже не догоняем, ждём", "Ниже, чем 200 дней назад (−12.5%) — тренда нет"]
    for s in samples:
        for lang in ("en", "uk", "el", "es"):
            txt, key, _f = R.reason(s, "wait", lang)
            assert key is not None, s
            assert not (CYR.search(txt) and lang != "uk"), (s, lang, txt)
        ru = R.reason(s, "wait", "ru")[0]                  # 28.10 (build 46 QA): Russian in plain words too, not the table's jargon
        assert ru and not any(w in ru.lower() for w in ("анкета", "мёртв", "не берём", "/100")), (s, ru)
    assert R.reason(samples[0], "wait", "ru")[0] == "Оценка проекта 16 из 100 — ниже минимума системы (40), пока не покупаем"
    assert R.reason("Уже +130% за 30 дней — не догоняем, ждём отката", "wait", "en")[2] == "overheat"
    assert R.reason("В обращении ~40% токенов — впереди разлоки, ждём", "wait", "en")[2] == "dilution"
    assert R.reason("−93% от исторического максимума — монета «мёртвая», не берём", "avoid", "en")[2] == "ath"
    assert R.reason("Что-то новое", "wait", "en")[0] == R.GENERIC["wait"]["en"]


def test_thirty_coins_within_30_seconds(tmp_path):
    api = make_api(tmp_path)
    t0 = time.time()
    c, parsed = post_file(api, _fixture("bingx_assets_30.xlsx"), "en")
    assert c == 200 and len(parsed["holdings"]) == 30, parsed
    c, out = post_review(api, parsed["holdings"], "en")
    took = time.time() - t0
    assert c == 200 and out["count"] == 30 and len(out["rows"]) == 30
    assert took < 30, took
    assert took < 5, took                      # deterministic: in practice well under a second


# ------------------------------------------------------------------------------------------------ the API: inputs, limits, privacy
def test_api_inputs_and_errors(tmp_path):
    api = make_api(tmp_path)
    c, caps = api.handle("GET", "/v2/portfolio/caps", {}, b"")
    assert c == 200 and caps["image"] is False and caps["csv"] and caps["xlsx"] and caps["pdf"] and caps["max_mb"] == 10
    assert api.handle("POST", "/v2/portfolio/parse", {}, b"x")[0] == 401
    for name in ("okx_bills.csv", "binance_balances.xlsx", "statement.pdf"):
        c, d = post_file(api, _fixture(name), "ru", uid="u-" + name)
        assert c == 200 and d["holdings"] and d["note"], (name, d)
    c, d = post_file(api, b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, "es", uid="u-img")
    assert d["error"] == "image_unsupported" and d["message"] == R.MSG["image_unsupported"]["es"]
    c, d = post_file(api, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64, "en", uid="u-xls")
    assert d["error"] == "xls"
    c, d = post_file(api, b"just,some\nwords,here\n", "uk", uid="u-empty")
    assert d["error"] == "no_rows" and d["message"] == R.MSG["no_rows"]["uk"]
    c, d = post_file(api, b"a" * (P.MAX_FILE + 1), "el", uid="u-big")
    assert c == 413 and d["message"] == R.MSG["too_large"]["el"]
    c, d = post_review(api, [], "ru", uid="u-rev")
    assert d["error"] == "bad_holdings"
    c, d = post_review(api, [{"sym": "BTC", "amount": "x"}, {"sym": "<script>", "amount": 1}], "ru", uid="u-rev")
    assert d["error"] == "bad_holdings"


def test_rate_limit_per_user(tmp_path):
    api = make_api(tmp_path)
    data = _fixture("okx_bills.csv")
    codes = [post_file(api, data, "ru", uid="busy")[0] for _ in range(7)]
    assert codes[:6] == [200] * 6 and codes[6] == 429
    assert post_file(api, data, "ru", uid="other")[0] == 200          # another user is not affected
    lim = R.Limiter(per_window=2, window_s=60, per_day=3)
    assert lim.hit("a", 0) == 0 and lim.hit("a", 1) == 0 and lim.hit("a", 2) > 0
    assert lim.hit("a", 61) == 0 and lim.hit("a", 125) > 0            # the day's limit (3)


def test_screenshot_with_a_vision_model(tmp_path):
    api = make_api(tmp_path)
    seen = {}

    def reader(cfg, data, known):
        seen["cfg"], seen["bytes"] = cfg, len(data)
        return P.from_model({"holdings": [{"sym": "btc", "amount": "0.5"}, {"sym": "ETH", "amount": 2, "value_usd": 4800},
                                          {"sym": "??", "amount": 1}, {"sym": "SOL", "amount": -1}]}, known)
    api.vision, api.vision_reader = {"provider": "gemini", "model": "gemini-3.5-flash-lite", "key": "k", "url": "u", "style": "openai"}, reader
    assert api.handle("GET", "/v2/portfolio/caps", {}, b"")[1]["image"] is True
    c, d = post_file(api, b"\xff\xd8\xff\xe0" + b"\x00" * 2000, "en")
    assert c == 200 and [h["sym"] for h in d["holdings"]] == ["ETH", "BTC"] and d["kind"] == "image"
    assert d["note"] == R.MSG["note_model"]["en"] and seen["bytes"] == 2004


def test_vision_config(monkeypatch):
    from cnq.agents.llm import LLM
    monkeypatch.delenv("CNQ_VISION_LLM_KEY", raising=False)
    assert vision_config(LLM(api_key="k", provider="groq", model="openai/gpt-oss-120b")) is None      # NOVA's model on 22.10
    assert vision_config(LLM(api_key="k", provider="gemini"))["style"] == "openai"
    assert vision_config(None) is None
    monkeypatch.setenv("CNQ_VISION_LLM_KEY", "vk")
    cfg = vision_config(LLM(api_key="k", provider="groq", model="openai/gpt-oss-120b"))
    assert cfg["provider"] == "gemini" and cfg["key"] == "vk"


def test_pdf_without_lines_goes_to_the_text_model(tmp_path):
    reply = json.dumps({"holdings": [{"sym": "BTC", "amount": 0.25}, {"sym": "LINK", "amount": 10}]})
    api = make_api(tmp_path, FakeLLM(reply))
    # a statement that names coins only in words: the line reader finds nothing, the text model reads it
    text_pdf = (b"%PDF-1.4\n1 0 obj\n<< /Length 60 >>\nstream\nBT /F1 10 Tf 50 700 Td (Bitcoin balance zero point two five) Tj ET\n"
                b"endstream\nendobj\ntrailer\n<< >>\n%%EOF\n")
    c, d = post_file(api, text_pdf, "ru")
    assert c == 200 and {h["sym"] for h in d["holdings"]} == {"BTC", "LINK"} and d["mode"] == "model", d
    assert api.llm.calls == 1


def test_nothing_is_persisted(tmp_path, capsys):
    api = make_api(tmp_path)

    def snapshot():
        out = {}
        for root, _dirs, files in os.walk(tmp_path):
            for f in files:
                p = os.path.join(root, f)
                out[p] = (os.path.getsize(p), os.path.getmtime(p))
        return out
    post_review(api, [{"sym": "BTC", "amount": 1}], "ru", uid="priv")          # the user row exists before (as for any call)
    before = snapshot()
    for name in EXPECTED:
        c, d = post_file(api, _fixture(name), "ru", uid="priv-" + name)
        assert c == 200
        c, out = post_review(api, d["holdings"], "en", uid="priv-" + name)
        assert c == 200 and out["rows"]
    after = snapshot()
    new = set(after) - set(before)
    assert not [p for p in new if "sqlite" not in p], new                      # no file was written
    db = b"".join(open(p, "rb").read() for p in after if "sqlite" in p)
    for marker in (b"0.02134", b"12500", b"25000000", b"1069", b"PENGU", b"ZEC"):
        assert marker not in db, marker                                     # no coin or amount in the database
    logs = capsys.readouterr()
    for marker in ("0.02134", "PENGU", "BTC", "USDT"):
        assert marker not in logs.out + logs.err, marker
    pulse = json.dumps(api.pf_last)
    assert "BTC" not in pulse and "amount" not in pulse
    c, h = api.handle("GET", "/v2/health", {}, b"")
    assert h["portfolio"]["vision"] is False and "BTC" not in json.dumps(h["portfolio"])


def _cid_pdf(lines: list[str]) -> bytes:
    """a statement drawn with a Type0 font whose text is glyph ids (hex) + a ToUnicode map — as browsers print PDFs"""
    chars = sorted({ch for l in lines for ch in l})
    gid = {ch: i + 3 for i, ch in enumerate(chars)}
    cmap = ("/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /X def 1 begincodespacerange <0000> <FFFF> "
            f"endcodespacerange {len(chars)} beginbfchar " + " ".join(f"<{gid[c]:04X}> <{ord(c):04X}>" for c in chars)
            + " endbfchar endcmap CMapName currentdict /CMap defineresource pop end end").encode()
    ops = ["BT", "/C0 9 Tf"]
    y = 800
    for l in lines:
        ops.append(f"1 0 0 1 40 {y} Tm <{''.join(f'{gid[c]:04X}' for c in l)}> Tj")
        y -= 14
    ops.append("ET")
    content = "\n".join(ops).encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /C0 5 0 R >> >> /Contents 4 0 R >>",
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type0 /BaseFont /Arial /Encoding /Identity-H /ToUnicode 6 0 R >>",
            b"<< /Length %d >>\nstream\n" % len(cmap) + cmap + b"\nendstream"]
    out = b"%PDF-1.5\n"
    for i, o in enumerate(objs, 1):
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    return out + b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"


def test_pdf_reader_without_pypdf(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "pypdf", None)          # the server's venv has no pypdf: the stdlib reader
    known = _known()
    got = P.parse_text(P.pdf_text(_fixture("statement.pdf")), known)["holdings"]
    assert _correct(got, EXPECTED["statement.pdf"]) == len(EXPECTED["statement.pdf"])
    cid = _cid_pdf(["Asset balances", "Coin Total Available Value (USD)", "BTC 0.42 0.40 34,432.86", "SOL 12.5 12.5 1,304.50",
                    "USDC 950 950 950.00"])
    text = P.pdf_text(cid)
    assert "Coin Total Available" in text, text
    got = {h["sym"]: h["amount"] for h in P.parse_text(text, known)["holdings"]}
    assert got == {"BTC": 0.42, "SOL": 12.5, "USDC": 950.0}, got


# ------------------------------------------------------------------------------------------------ «Топ-10 монет на заметку»
def test_spot_top_ten(tmp_path):
    from cnq.api import spot_top
    data = R.Data(WEB)
    ideas = data.get("ideas")
    lists = {"okx": {"BTC", "ETH", "SOL", "LINK"}, "bingx": {"BTC", "ETH", "NEAR"}}
    for lang in LANGS:
        d = spot_top.top(data, lang, lists)
        assert 0 < len(d["rows"]) <= 10 and d["prices"]
        assert all(r["status"] in ("buy", "add", "small") and r["reason"] and r["status_label"] for r in d["rows"])
        if lang in ("en", "el", "es"):
            assert not [r["reason"] for r in d["rows"] if CYR.search(r["reason"] + r["status_label"])]
    d = spot_top.top(data, "ru", lists)
    order = [r["symbol"] for r in ideas["rows"] if r["status"] in ("buy", "add", "small")]
    assert [r["sym"] for r in d["rows"]][:len(order[:10])] == order[:10]                 # the table's own order
    btc = [r for r in d["rows"] if r["sym"] == "BTC"]
    if btc:
        assert btc[0]["okx"] is True and btc[0]["bingx"] is True
    held = {p["symbol"] for p in ((data.get("portfolio").get("portfolios") or {}).get("ideas") or {}).get("positions") or []}
    assert all((r["held"] is not None) == (r["sym"] in held) for r in d["rows"])
    assert all(r["okx"] is None for r in spot_top.top(data, "ru", {})["rows"])            # lists not loaded yet → unknown
    # the «Поддержка проекта» score is passed on only when the table has it
    assert spot_top._support({"support": {"score": 72, "why_ru": "x"}}) == {"score": 72.0}
    assert spot_top._support({"support": 55}) == {"score": 55.0} and spot_top._support({}) is None
    api = make_api(tmp_path)
    api.listings = spot_top.Listings({"okx": lambda: {"BTC"}, "bingx": lambda: {"ETH"}})
    api.listings.get(background=False)
    c, d = api.handle("GET", "/v2/spot/top?lang=el", {}, b"")
    assert c == 200 and d["lang"] == "el" and d["rows"]


def test_exchange_lists_parse():
    from cnq.api import spot_top
    okx = {"data": [{"baseCcy": "BTC", "quoteCcy": "USDT", "state": "live"}, {"baseCcy": "XYZ", "quoteCcy": "EUR"},
                    {"baseCcy": "ETH", "quoteCcy": "USDC", "state": "suspend"}]}
    bx = {"code": 0, "data": {"symbols": [{"symbol": "BTC-USDT", "status": 1}, {"symbol": "OLD-USDT", "status": 0}, {"symbol": "SOL-USDC", "status": 1}]}}
    assert spot_top.okx_bases(lambda u: okx) == {"BTC"}
    assert spot_top.bingx_bases(lambda u: bx) == {"BTC", "SOL"}
    L = spot_top.Listings({"okx": lambda: (_ for _ in ()).throw(RuntimeError("down")), "bingx": lambda: {"BTC"}})
    assert L.get(background=False) == {"bingx": {"BTC"}}
