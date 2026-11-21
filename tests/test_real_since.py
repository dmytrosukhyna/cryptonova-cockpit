"""21.11 (owner): the real account was restarted on 21.11.2026 — every public «с …» reads public_real.json; the public start sum is
«≈ $10 000», never the exact one; no public text claims a result from before the start."""
import os
import re

from cnq.agents import real_since as RS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SINCE = 1795240455789                                       # 21.11.2026 05:54 UTC


def test_the_helper():
    i = RS.info({"since": SINCE, "start_usd": 10276.07, "label_ru": "Реальный счёт CRYPTO NOVA AI · с 21.11.2026"})
    assert (i["short"], i["full"], i["words_ru"], i["short_en"]) == ("21.11", "21.11.2026", "21 ноября 2026 года", "21 Nov")
    assert i["start_approx"] == "≈ $10 000" and i["start_label_ru"] == "стартовый капитал ≈ $10 000"
    assert RS.since_ru(i) == "с 21.11" and RS.since_ru(i, True) == "с 21.11.2026"
    assert RS.since_ru(RS.info({})) == "с начала счёта" and RS.approx_start(10_276.07) == "≈ $10 000" and RS.approx_start(None) == ""


def test_morning_and_app_note_read_the_start():
    from cnq.agents import morning as M
    F = {"total": 10.0, "futures": 5.0, "spot": 5.0, "equity": 10_286.0, "since_usd": 10.0, "since_pct": 0.1, "since_ms": SINCE, "open": 1}
    for lang in ("ru", "en", "uk", "el", "es"):
        for a in range(3):
            r = M.render(F, {"t": 0, "r": 0, "rk": "up", "a": a}, lang)
            txt = r["body"] + " " + r["owner"]["body"]
            assert "22.10" not in txt and "22 Oct" not in txt, (lang, a)
            if a < 3 and lang in ("ru", "uk", "el", "es"):
                assert "21.11" in txt or "начала" in txt, (lang, a, txt)
    assert "21 Nov" in M.render(F, {"t": 0, "r": 0, "rk": "up", "a": 0}, "en")["body"]
    from cnq.agents import app_home as A
    import json
    real = json.load(open(os.path.join(ROOT, "web", "public_real.json"), encoding="utf-8"))
    note = A._real_portfolio({**real, "since": SINCE, "start_usd": 10276.07}, {}, SINCE + 3_600_000, "ru")["note"]
    assert "21.11.2026" in note and "≈ $10 000" in note and "10 276" not in note


def test_no_public_page_or_text_keeps_the_old_start():
    pages = {f: open(os.path.join(ROOT, *f.split("/")), encoding="utf-8").read() for f in
             ("web/app.html", "web/fut_owner.js", "web/vip/core.js", "web/vip/demo.js", "web/vip/overview.js", "web/vip/spot.js",
              "cnq/agents/channel.py", "cnq/agents/weekly.py", "cnq/agents/morning.py", "cnq/agents/broadcast.py", "cnq/agents/vip_feeds.py")}
    for f, s in pages.items():
        for m in re.finditer(r"22\.10|22 октября|22 Oct|since 22", s):
            line = s[s.rfind("\n", 0, m.start()) + 1:s.find("\n", m.start())]
            assert re.match(r"\s*(#|//|/\*|\*)", line) or "//" in line[:m.start() - s.rfind("\n", 0, m.start())] or '"""' in s[:m.start()].split("\n")[-1] \
                or re.search(r"# .*22\.10|// .*22\.10|/\* .*22\.10|\(owner|decision|решение владельца 22\.10|sales went into|«no OKX pair»|правило владельца с 22\.10|активный спот \(22\.10\)", line), (f, line.strip()[:120])   # dates of rules, not of results
    app = pages["web/app.html"]
    assert "window.cnSince=" in app and "fetch('./public_real.json?" in app and "старт ${cnApproxUsd(R.start_usd)}" in app
    assert "старт ${usd2(R.start_usd)}" not in app and "V.approxUsd(M.start)" in pages["web/vip/overview.js"]
    assert "V.PUBLIC_START_LABEL = '22.10.2026'" not in pages["web/vip/core.js"] and "V.setStart(since)" in pages["web/vip/overview.js"]
