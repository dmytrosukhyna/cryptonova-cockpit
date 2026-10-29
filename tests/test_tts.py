"""NOVA's voice on the server (cnq/api/tts.py, build 46 #6, owner: «Доклад не говорит», «голос NOVA AI не работает»): POST /v2/tts
and GET /v2/tts/<key>.mp3 with the greeting's male voices, a cache by key, a per-IP limit, JSON errors; /v2/home "report" with
its audio_url; app_home's spoken report in every language. The voice service is never called here (a fake synth)."""
import importlib.util
import json
import os
import sqlite3
import threading
import time
import urllib.request

from cnq.agents import app_home, i18n
from cnq.api import tts
from cnq.api.auth import FirebaseVerifier
from cnq.api.server import Api, make_handler
from cnq.api.store import Store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MP3 = b"ID3" + b"\x00" * 2000


class Synth:
    def __init__(self, fail=False):
        self.calls, self.fail, self.live = [], fail, []

    def __call__(self, text, lang, live=False):
        self.calls.append((text, lang))
        self.live.append(live)
        if self.fail:
            raise RuntimeError("no audio was received")
        return MP3 + text.encode()[:20]


def _tts(tmp_path, ok=True, fail=False, max_mb=200):
    s = Synth(fail)
    return tts.Tts(str(tmp_path / "cache"), synth_fn=s, available_fn=lambda: ok, max_mb=max_mb), s


def test_the_same_male_voices_as_the_greeting_and_the_name_read_well():
    spec = importlib.util.spec_from_file_location("wv", os.path.join(ROOT, "scripts", "welcome_voice.py"))
    wv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wv)
    assert tts.VOICES == wv.VOICES and set(tts.VOICES) == set(i18n.LANGS)
    assert tts.spoken("Публичный портфель CRYPTO NOVA. Я NOVA AI.", "ru") == "Публичный портфель Крипто Но́ва. Я Но́ва."
    assert tts.spoken("The *CRYPTO NOVA* portfolio, NOVA AI here", "en") == "The Crypto Nova portfolio, Nova AI here"
    k = tts.key("Привет", "ru")
    assert len(k) == 24 and k == tts.key("Привет", "ru") and k != tts.key("Привет", "uk") and k != tts.key("Привет!", "ru")
    assert tts.key("x", "zz") == tts.key("x", "ru")                        # an unknown language → Russian


def test_post_caches_limits_and_answers_json_errors(tmp_path):
    t, s = _tts(tmp_path)
    c, a = t.post({"text": "Биткоин стоит 80 000 долларов.", "lang": "ru"}, "1.1.1.1")
    assert c == 200 and isinstance(a, tts.Audio) and a.body.startswith(b"ID3") and a.key == tts.key("Биткоин стоит 80 000 долларов.", "ru")
    c, a2 = t.post({"text": "Биткоин стоит 80 000 долларов.", "lang": "ru"}, "1.1.1.1")
    assert c == 200 and a2.body == a.body and len(s.calls) == 1                # made once, then from the cache
    assert t.post({"text": " ", "lang": "ru"}, "ip")[1] == {"error": "bad_text"}
    c, d = t.post({"text": "я" * (tts.MAX_CHARS + 1), "lang": "ru"}, "ip")
    assert c == 413 and d["error"] == "too_long"
    for i in range(30):
        assert t.post({"text": f"Текст номер {i}", "lang": "en"}, "9.9.9.9")[0] == 200
    c, d = t.post({"text": "Ещё один", "lang": "en"}, "9.9.9.9")
    assert c == 429 and d["error"] == "rate" and d["retry_after_min"] >= 1
    assert t.post({"text": "Текст номер 3", "lang": "en"}, "9.9.9.9")[0] == 200   # a cached text never counts
    assert t.post({"text": "Ещё один", "lang": "en"}, "8.8.8.8")[0] == 200       # another address has its own limit
    off, _ = _tts(tmp_path / "off", ok=False)
    assert off.post({"text": "Новый текст", "lang": "ru"}, "ip") == (503, {"error": "tts_unavailable"})
    bad, _ = _tts(tmp_path / "bad", fail=True)
    assert bad.post({"text": "Новый текст", "lang": "ru"}, "ip") == (502, {"error": "tts_failed"}) and bad.last["result"] == "failed"
    assert t.last["result"] == "ok" and "text" not in t.last                     # health shows the result, never the words


def test_cache_is_capped_oldest_first(tmp_path):
    t, _ = _tts(tmp_path, max_mb=1)
    big = b"x" * (400 * 1024)
    for i, k in enumerate(("a" * 24, "b" * 24, "c" * 24)):
        t.put(k.replace("a", "0").replace("b", "1").replace("c", "2"), big)
        time.sleep(0.01)
    left = sorted(os.listdir(tmp_path / "cache"))
    assert len(left) == 2 and "0" * 24 + ".mp3" not in left                      # 1 MB cap: the oldest went
    assert t.get("../../etc/passwd") is None and t.get("Z" * 24) is None


def _home(lang, report):
    return {"v": 1, "asof": int(time.time() * 1000), "lang": lang, "total": {"usd": 1}, "futures": {}, "spot": {}, "report": report}


def test_home_report_audio_url_and_the_mp3_route(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    reports = {}
    for lang in i18n.LANGS:
        text = f"Отчёт {lang}: портфель 9 896 долларов."
        reports[lang] = {"kind": "public", "lang": lang, "text": text, "key": tts.key(text, lang), "audio_url": None}
        (web / i18n.file_name("app_home", lang)).write_text(json.dumps(_home(lang, reports[lang]), ensure_ascii=False), encoding="utf-8")
    api = Api(Store(str(tmp_path / "api.sqlite")), FirebaseVerifier("crypto-nova", keys={}), str(web))
    api.tts, synth = _tts(tmp_path)
    c, d = api.handle("GET", "/v2/home?lang=en", {}, b"")
    k = reports["en"]["key"]
    assert c == 200 and d["report"]["audio_url"] == f"https://app.crypto-capital.app/v2/tts/{k}.mp3" and d["report"]["text"] == reports["en"]["text"]
    for _ in range(100):                                                        # made in the background for an instant first play
        if api.tts.get(k):
            break
        time.sleep(0.02)
    assert api.tts.get(k) and synth.calls == [(reports["en"]["text"], "en")]
    c, a = api.handle("GET", f"/v2/tts/{k}.mp3", {}, b"")
    assert c == 200 and isinstance(a, tts.Audio) and len(synth.calls) == 1
    k_es = reports["es"]["key"]
    c, a = api.handle("GET", f"/v2/tts/{k_es}.mp3", {}, b"")                    # today's report, not made yet → made now
    assert c == 200 and synth.calls[-1] == (reports["es"]["text"], "es")
    assert api.handle("GET", "/v2/tts/" + "f" * 24 + ".mp3", {}, b"") == (404, {"error": "not_found"})   # never a text it was not given
    h = api.handle("GET", "/v2/health", {}, b"")[1]
    assert h["ready"]["tts"] is True and h["tts_last"]["result"] == "ok"
    api.tts, _ = _tts(tmp_path / "off", ok=False)
    assert api.handle("GET", "/v2/home?lang=en", {}, b"")[1]["report"]["audio_url"] is None   # no voice → the phone reads the text
    # over HTTP: the mp3 with its type; a JSON error stays JSON
    api.tts, _ = _tts(tmp_path)
    srv = __import__("http.server", fromlist=["ThreadingHTTPServer"]).ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        req = urllib.request.Request(base + "/v2/tts", data=json.dumps({"text": "Привет, я NOVA AI.", "lang": "ru"}).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            body = r.read()
            assert r.status == 200 and r.headers["Content-Type"] == "audio/mpeg" and body.startswith(b"ID3")
            assert r.headers["X-TTS-Key"] == tts.key("Привет, я NOVA AI.", "ru") and "immutable" in r.headers["Cache-Control"]
        req = urllib.request.Request(base + "/v2/tts", data=b'{"text": ""}', method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 400 and json.loads(e.read()) == {"error": "bad_text"}
    finally:
        srv.shutdown()


def test_app_home_spoken_report_in_every_language(tmp_path):
    from test_public_real import _pp_files, T0
    d = str(tmp_path)
    H = 3_600_000
    since, now = T0 + 100 * H, T0 + 100 * H + 30 * H
    real = {"exchange": "OKX", "since": since, "start_usd": 10000.0, "equity_usd": 9871.2, "pnl_usd": -128.8, "pnl_pct": -1.29,
            "today_usd": 21.0, "today_pct": 0.21, "spot": [], "usdt_pct": 100.0, "usdt_usd": 9871.2, "alloc": None, "futures": [],
            "closed_n": 0, "curve": [[since, 0.0], [now - 60_000, -128.8]], "asof": now - 60_000, "stale": False,
            "actions": [{"t": since + H, "kind": "spot_sell", "sym": "AAVE", "side": None, "usd": 431.41, "px": 136.2, "sig": None}]}
    _pp_files(d, real)
    with open(os.path.join(d, "markets.json"), "w") as f:
        json.dump({"btc_bull": True, "top": [{"symbol": "BTC", "price": 80900, "chg24": 1.7}]}, f)
    with open(os.path.join(d, "system_status.json"), "w") as f:      # build 47: every part ok (a missing file is said aloud)
        json.dump({"asof": now - 60_000, "components": [{"key": "public_real", "status": "ok"}, {"key": "engine", "status": "ok"}]}, f)
    doc = app_home.run(sqlite3.connect(":memory:"), d, now=now)
    r = doc["report"]
    assert r["status"] == "ok" and r["issues"] == []
    assert r["text"].startswith("Публичный портфель CRYPTO\u00a0NOVA сейчас — 9 871 доллар.") and "$" not in r["text"]
    assert "С 22 октября — минус 129 долларов, это минус 1,3 процента." in r["text"] or "минус 129 долларов" in r["text"]
    assert "продажа AAVE" in r["text"] and r["text"].endswith("Биткоин стоит 80 900 долларов, за сутки плюс 1,7 процента.")
    assert r["key"] == tts.key(r["text"], "ru") and r["audio_url"] is None                  # the API adds the address
    for lang in ("en", "uk", "el", "es"):
        o = json.load(open(os.path.join(d, f"app_home.{lang}.json"), encoding="utf-8"))["report"]
        assert o["lang"] == lang and o["text"] != r["text"] and o["key"] == tts.key(o["text"], lang) and "$" not in o["text"], lang
    en = json.load(open(os.path.join(d, "app_home.en.json"), encoding="utf-8"))["report"]["text"]
    assert "9,871 dollars" in en and "a sale of AAVE" in en and "Bitcoin is at 80,900 dollars, plus 1.7 percent over the day." in en
    assert app_home._say_money(21, "ru") == "21 доллар" and app_home._say_money(-3, "uk", True) == "мінус 3 долари"
    assert app_home._say_pct(2.0, "ru") == "плюс 2 процента" and app_home._say_pct(-5, "ru") == "минус 5 процентов"
    assert app_home._say_pct(0.25, "es") in ("más 0,2 por ciento", "más 0,3 por ciento")


def test_the_server_installs_its_own_voice_once_in_the_background(tmp_path):
    """build 50: edge-tts missing → ONE background pip install (a fake here), at most once per 6 hours, also across a restart;
    a request never waits for it; a finished install is looked at again at once"""
    runs, done = [], threading.Event()
    now = [1_000_000.0]

    def run():
        runs.append(1)
        done.set()
        return 0, "Successfully installed edge-tts-7.2.3"
    state = str(tmp_path / "data" / "tts_install.json")
    ins = tts.Installer(state, run_fn=run, now_fn=lambda: now[0])
    t = tts.Tts(str(tmp_path / "data" / "tts_cache"), synth_fn=Synth(), available_fn=lambda: False, installer=ins)
    t0 = time.time()
    c, d = t.post({"text": "Привет, это NOVA AI.", "lang": "ru"}, "1.1.1.1")
    assert c == 503 and d == {"error": "tts_unavailable"} and time.time() - t0 < 1.0       # never blocks the request
    assert done.wait(5) and runs == [1]
    for _ in range(50):
        if not ins.running:
            break
        time.sleep(0.02)
    st = t.install_state()
    assert st["result"] == "ok" and st["code"] == 0 and st["spec"] == tts.PIP_SPEC and "error" not in st
    assert t._avail_at == 0.0                                                          # looked at again on the next request
    t.available()
    assert runs == [1]                                                                 # once per 6 hours, not per request
    # a restart: a new installer reads the last try from its file and does not run again before 6 hours
    ins2 = tts.Installer(state, run_fn=run, now_fn=lambda: now[0] + 3600)
    assert ins2.maybe_start() is False and runs == [1]
    ins3 = tts.Installer(state, run_fn=run, now_fn=lambda: now[0] + tts.INSTALL_EVERY_S + 1)
    done.clear()
    assert ins3.maybe_start() is True and done.wait(5) and runs == [1, 1]


def test_a_failed_install_is_reported_without_credentials_and_tests_never_run_pip(tmp_path):
    ins = tts.Installer(str(tmp_path / "s.json"), run_fn=lambda: (1, "ERROR: could not reach https://user:secret@pypi.example/simple"),
                        now_fn=lambda: 5.0)
    t = tts.Tts(str(tmp_path / "cache"), synth_fn=Synth(), available_fn=lambda: False, installer=ins)
    t.available()
    for _ in range(100):
        if ins.state.get("result") not in (None, "running"):
            break
        time.sleep(0.02)
    st = t.install_state()
    assert st["result"] == "failed" and st["code"] == 1 and "secret" not in st["error"] and "***@" in st["error"]
    # a Tts with a fake availability and no installer never starts pip
    t2, _s = _tts(tmp_path, ok=False)
    assert t2.installer is None and t2.install_state() is None
    assert t2.post({"text": "Привет", "lang": "ru"}, "ip")[0] == 503


def test_the_report_is_made_patiently_and_the_route_waits_for_it(tmp_path):
    """build 50: the voice service refuses now and then — the background make tries again with pauses, failed words rest, and the
    report's mp3 address waits for a make that is running instead of asking the service twice"""
    class Flaky(Synth):
        def __init__(self, fails):
            super().__init__()
            self.fails = fails

        def __call__(self, text, lang, live=False):
            self.calls.append((text, lang))
            if len(self.calls) <= self.fails:
                raise RuntimeError("No audio was received")
            time.sleep(0.3)
            return MP3 + text.encode()[:20]
    s = Flaky(fails=2)
    t = tts.Tts(str(tmp_path / "c1"), synth_fn=s, available_fn=lambda: True)
    t.prewarm_pause = 0.01
    text = "Публичный портфель CRYPTO NOVA сейчас — 9 759 долларов."
    k = tts.key(text, "ru")
    t.prewarm(text, "ru")
    time.sleep(0.05)
    c, a = t.get_route(k, {k: (text, "ru")})                       # waits for the running make, never a second request
    assert c == 200 and a.key == k and len(s.calls) == 3
    # words that fail every time rest after the last try (no hammering on every /v2/home)
    bad = Synth(fail=True)
    t2 = tts.Tts(str(tmp_path / "c2"), synth_fn=bad, available_fn=lambda: True)
    t2.prewarm_pause = 0.0
    t2.prewarm(text, "ru")
    for _ in range(100):
        if not t2._busy:
            break
        time.sleep(0.02)
    assert len(bad.calls) == tts.PREWARM_TRIES and k in t2._failed
    t2.prewarm(text, "ru")
    time.sleep(0.05)
    assert len(bad.calls) == tts.PREWARM_TRIES


def test_home_serves_the_last_report_whose_voice_is_ready(tmp_path):
    """build 50: the report's words change with the live numbers; while the newest one is being made (or the voice service refuses
    it), /v2/home serves the last report whose mp3 is ready — its own words with its own mp3 — and one language is made at most
    every 2 minutes in the background"""
    web = tmp_path / "web"
    web.mkdir()

    def feed(text):
        r = {"kind": "public", "lang": "ru", "text": text, "key": tts.key(text, "ru"), "audio_url": None}
        (web / i18n.file_name("app_home", "ru")).write_text(json.dumps(_home("ru", r), ensure_ascii=False), encoding="utf-8")
        return r
    api = Api(Store(str(tmp_path / "api.sqlite")), FirebaseVerifier("crypto-nova", keys={}), str(web))
    s = Synth()
    api.tts = tts.Tts(str(tmp_path / "cache"), synth_fn=s, available_fn=lambda: True)
    api.tts.prewarm_pause = 0.0
    a = feed("Публичный портфель CRYPTO NOVA сейчас — 9 742 доллара.")
    api.handle("GET", "/v2/home?lang=ru", {}, b"")
    for _ in range(100):
        if api.tts.has(a["key"]):
            break
        time.sleep(0.02)
    d = api.handle("GET", "/v2/home?lang=ru", {}, b"")[1]["report"]
    assert d["key"] == a["key"] and d["audio_url"].endswith(a["key"] + ".mp3")
    s.fail = True                                                   # the service refuses the next words
    b = feed("Публичный портфель CRYPTO NOVA сейчас — 9 759 долларов.")
    d = api.handle("GET", "/v2/home?lang=ru", {}, b"")[1]["report"]
    assert d["key"] == a["key"] and d["text"] == a["text"] and d["audio_url"].endswith(a["key"] + ".mp3")   # words and voice together
    assert len(s.calls) == 1                                        # spacing: no second background make within 2 minutes
    api.tts._lang_next.clear()
    s.fail = False
    api.handle("GET", "/v2/home?lang=ru", {}, b"")
    for _ in range(100):
        if api.tts.has(b["key"]):
            break
        time.sleep(0.02)
    d = api.handle("GET", "/v2/home?lang=ru", {}, b"")[1]["report"]
    assert d["key"] == b["key"] and d["text"] == b["text"]          # the newest words, once their voice is ready


def test_russian_falls_back_to_a_male_multilingual_voice_never_a_female_one(tmp_path):
    """29.10 (owner: male voices only, never a robotic female voice): ru-RU-DmitryNeural is refused most of the time — after its
    2 attempts the Russian text is read by FALLBACK["ru"], a male multilingual voice; the other languages keep their one voice;
    the cache key stays Dmitry's; /v2/health tts_last names the voice that spoke. The voice service is a fake here."""
    import sys
    import types
    fb = tts.FALLBACK["ru"]
    assert set(tts.FALLBACK) == {"ru"} and fb.endswith("MultilingualNeural")
    assert tts.voices("ru") == ["ru-RU-DmitryNeural", fb] and tts.voices("uk") == ["uk-UA-OstapNeural"] and tts.voices("zz")[0] == "ru-RU-DmitryNeural"
    female = ("Svetlana", "Dariya", "Polina", "Ava", "Emma", "Seraphina", "Vivienne", "Thalita", "Jenny", "Aria", "Katja", "Denise")
    assert not [v for v in (*tts.VOICES.values(), *tts.FALLBACK.values()) if any(f in v for f in female)]
    calls, refuse = [], {"ru-RU-DmitryNeural"}

    class Communicate:
        def __init__(self, text, voice, rate=None):
            self.voice = voice
            calls.append(voice)

        async def stream(self):
            if self.voice in refuse:
                raise RuntimeError("No audio was received. Please verify that your parameters are correct.")
            yield {"type": "audio", "data": MP3}
    old_mod, old_pause = sys.modules.get("edge_tts"), tts.RETRY_PAUSE_S
    sys.modules["edge_tts"], tts.RETRY_PAUSE_S = types.SimpleNamespace(Communicate=Communicate), 0.0
    try:
        assert tts.synth("Я NOVA AI. Биткоин стоит 80 058 долларов.", "ru") == MP3
        assert calls == ["ru-RU-DmitryNeural", "ru-RU-DmitryNeural", fb]          # Dmitry's 2 attempts first, then the fallback
        calls.clear()
        assert tts.synth("Привіт, я NOVA AI.", "uk") == MP3 and calls == ["uk-UA-OstapNeural"]
        refuse.add("uk-UA-OstapNeural")
        calls.clear()
        try:
            tts.synth("Привіт", "uk")
            assert False
        except RuntimeError:
            assert calls == ["uk-UA-OstapNeural"] * 2                               # no fallback outside Russian
        # through the API: Dmitry's cache key, the fallback's mp3, the voice in tts_last; a cached text is never made again
        t = tts.Tts(str(tmp_path / "cache"), available_fn=lambda: True)
        text = "Публичный портфель CRYPTO NOVA сейчас — 9 773 доллара."
        calls.clear()
        c, a = t.post({"text": text, "lang": "ru"}, "1.1.1.1")
        assert c == 200 and a.key == tts.key(text, "ru") and t.has(a.key) and t.last["voice"] == fb and t.last["live"] is True
        assert calls == ["ru-RU-DmitryNeural", fb]                                  # live (the app waits 8 s): Dmitry once, then Brian
        assert [(x["voice"], x["outcome"]) for x in t.last["attempts"]] == [("ru-RU-DmitryNeural", "RuntimeError"), (fb, "ok")]
        assert t.last["prep_ms"] >= 0 and "put_ms" in t.last and all("ms" in x and "at_ms" in x for x in t.last["attempts"])
        assert t.post({"text": text, "lang": "ru"}, "1.1.1.1")[0] == 200 and len(calls) == 2
        # Dmitry answers → Dmitry speaks
        refuse.discard("ru-RU-DmitryNeural")
        calls.clear()
        k, _mp3 = t.make("Биткоин стоит 80 058 долларов.", "ru")
        assert calls == ["ru-RU-DmitryNeural"] and t.last["voice"] == "ru-RU-DmitryNeural"
        # every male voice refuses → an error (the app reads the text itself), never a female voice
        refuse.update({"ru-RU-DmitryNeural", fb})
        calls.clear()
        assert t.post({"text": "Новый текст", "lang": "ru"}, "1.1.1.1") == (502, {"error": "tts_failed"})
        assert calls == ["ru-RU-DmitryNeural", fb, fb] and t.last["result"] == "failed" and len(t.last["attempts"]) == 3
        # the background report (prewarm) keeps the patient order: Dmitry twice, then Brian
        refuse.discard(fb)
        calls.clear()
        t.prewarm("Публичный портфель CRYPTO NOVA сейчас — 9 781 доллар.", "ru")
        for _ in range(200):
            if not t._busy and calls:
                break
            time.sleep(0.02)
        assert calls == ["ru-RU-DmitryNeural", "ru-RU-DmitryNeural", fb] and t.last["live"] is False
    finally:
        tts.RETRY_PAUSE_S = old_pause
        if old_mod is None:
            sys.modules.pop("edge_tts", None)
        else:
            sys.modules["edge_tts"] = old_mod


def test_a_silent_dmitry_hands_over_within_the_apps_8_seconds(tmp_path):
    """29.10: the app waits 8 s for the server's voice. A voice with a fallback behind it (Dmitry) that sends no audio within
    FIRST_AUDIO_S counts as refused, so a hanging Dmitry never eats the 8 s; once a voice speaks, it is never cut"""
    import asyncio
    import sys
    import types
    fb = tts.FALLBACK["ru"]
    calls, mode = [], {"dmitry": "hang"}

    class Communicate:
        def __init__(self, text, voice, rate=None):
            self.voice = voice
            calls.append(voice)

        async def stream(self):
            if self.voice == "ru-RU-DmitryNeural" and mode["dmitry"] == "hang":
                yield {"type": "WordBoundary", "offset": 0}                  # metadata is not audio
                await asyncio.sleep(30)
            if self.voice == "ru-RU-DmitryNeural" and mode["dmitry"] == "slow":
                yield {"type": "audio", "data": MP3[:600]}                  # speaks at once, then takes its time
                await asyncio.sleep(0.5)
                yield {"type": "audio", "data": MP3[600:]}
                return
            yield {"type": "audio", "data": MP3}
    old = sys.modules.get("edge_tts"), tts.RETRY_PAUSE_S, tts.FIRST_AUDIO_S
    sys.modules["edge_tts"], tts.RETRY_PAUSE_S, tts.FIRST_AUDIO_S = types.SimpleNamespace(Communicate=Communicate), 0.0, 0.2
    try:
        t0 = time.time()
        assert tts.synth("Я NOVA AI.", "ru") == MP3 and calls == ["ru-RU-DmitryNeural", "ru-RU-DmitryNeural", fb]
        assert time.time() - t0 < 2.0 and tts._used.voice == fb                    # 2 × 0.2 s, not 2 × 25 s
        calls.clear()
        mode["dmitry"] = "slow"
        assert tts.synth("Я NOVA AI.", "ru") == MP3 and calls == ["ru-RU-DmitryNeural"] and tts._used.voice == "ru-RU-DmitryNeural"
    finally:
        tts.RETRY_PAUSE_S, tts.FIRST_AUDIO_S = old[1], old[2]
        if old[0] is None:
            sys.modules.pop("edge_tts", None)
        else:
            sys.modules["edge_tts"] = old[0]


def test_live_requests_try_dmitry_once_and_every_attempt_is_timed(tmp_path):
    """29.10: a request the app waits for (8 s) — POST /v2/tts and a report mp3 made on the spot — is LIVE: Dmitry once (silent for
    FIRST_AUDIO_S → cut), then Brian; /v2/health tts_last shows each attempt (voice, outcome, start, first audio, total)"""
    import asyncio
    import sys
    import types
    fb = tts.FALLBACK["ru"]
    calls = []

    class NoAudioReceived(Exception):
        pass

    class Communicate:
        def __init__(self, text, voice, rate=None):
            self.voice = voice
            calls.append(voice)

        async def stream(self):
            if self.voice == "ru-RU-DmitryNeural":
                await asyncio.sleep(30)                                         # hangs: cut at FIRST_AUDIO_S
            if self.voice == "uk-UA-OstapNeural":
                raise NoAudioReceived("No audio was received.")
            yield {"type": "audio", "data": MP3}
    old = sys.modules.get("edge_tts"), tts.RETRY_PAUSE_S, tts.FIRST_AUDIO_S
    sys.modules["edge_tts"], tts.RETRY_PAUSE_S, tts.FIRST_AUDIO_S = types.SimpleNamespace(Communicate=Communicate), 0.0, 0.2
    try:
        t = tts.Tts(str(tmp_path / "cache"), available_fn=lambda: True)
        t0 = time.time()
        c, a = t.post({"text": "Я NOVA AI. Биткоин стоит 80 058 долларов.", "lang": "ru"}, "1.1.1.1")
        assert c == 200 and calls == ["ru-RU-DmitryNeural", fb] and time.time() - t0 < 1.5
        at = t.last["attempts"]
        assert [(x["voice"], x["outcome"]) for x in at] == [("ru-RU-DmitryNeural", "silent"), (fb, "ok")]
        assert 150 <= at[0]["ms"] < 1000 and "first_audio_ms" not in at[0] and at[1]["first_audio_ms"] <= at[1]["ms"]
        assert at[1]["at_ms"] >= at[0]["ms"] and t.last["voice"] == fb and t.last["live"] is True
        assert t.last["inflight"] == 0 and tts._INFLIGHT[0] == 0                   # no other synthesis was running; none is left
        # the report's mp3 address, made on the spot, is live too
        text = "Публичный портфель CRYPTO NOVA сейчас — 9 781 доллар."
        k = tts.key(text, "ru")
        calls.clear()
        c, a = t.get_route(k, {k: (text, "ru")})
        assert c == 200 and calls == ["ru-RU-DmitryNeural", fb]
        # a language without a fallback keeps its 2 attempts; the outcome is named
        calls.clear()
        assert t.post({"text": "Привіт, я NOVA AI.", "lang": "uk"}, "1.1.1.1") == (502, {"error": "tts_failed"})
        assert calls == ["uk-UA-OstapNeural"] * 2 and [x["outcome"] for x in t.last["attempts"]] == ["no_audio", "no_audio"]
        h = json.dumps(t.last)                                                  # health stays JSON
        assert "attempts" in h
    finally:
        tts.RETRY_PAUSE_S, tts.FIRST_AUDIO_S = old[1], old[2]
        if old[0] is None:
            sys.modules.pop("edge_tts", None)
        else:
            sys.modules["edge_tts"] = old[0]
