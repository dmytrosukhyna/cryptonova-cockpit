"""NOVA's voice on the server (28.10.2026, build 46 #6, owner: «Доклад не говорит», «голос NOVA AI не работает»). The app spoke
with the phone's own voices — an iPhone without a male Russian voice stayed silent. Now the server reads the text with the same
male Microsoft neural voices as the spoken greeting (scripts/welcome_voice.py VOICES, edge-tts) and the app plays an mp3.

  POST /v2/tts {"text", "lang"}  → 200 audio/mpeg (X-TTS-Key: the cache key) or a JSON error:
        400 bad_text · 413 too_long (> MAX_CHARS) · 429 rate (per IP; a cached text never counts) ·
        503 tts_unavailable (edge-tts is not installed on the server) · 502 tts_failed (the voice service did not answer)
  GET  /v2/tts/<key>.mp3          → the cached mp3; a key that is today's report of /v2/home (app_home "report") is made on the spot
  /v2/home "report"               → {"text", "key", "lang", "audio_url" (null while the voice is unavailable)} — cnq/agents/app_home.py
                                    writes the text and its key, the API adds the URL; a new text → a new key → a new mp3

The cache: <run>/data/tts_cache/<key>.mp3, oldest files removed above MAX_CACHE_MB. The key is a hash of the voice, the rate and the
spoken text, so the same words are made once. Nothing about the caller is stored. Stdlib + edge-tts (optional).

29.10.2026 (build 50, owner: «если люди заходят с другим языком на телефоне, будет ли голосовой помощник говорить на другом
языке?»): the server installs its voice by itself. The deploy (git pull) never runs pip, and edge-tts was never installed, so every
/v2/tts answered 503 and the app fell back to the phone's voice. Now, when the voice is needed and edge-tts is not importable, the API
starts ONE background `python -m pip install "edge-tts>=7,<8"` into its own Python (the venv; binary wheels only, nothing is
compiled or run from a source package): at most once per 6 hours (the last try's time is kept in <run>/data/tts_install.json, so a
restart never retries sooner), logged to the journal and shown in /v2/health "tts_install", never blocking a request. edge-tts
needs aiohttp, certifi, tabulate, typing-extensions — none of them clash with the API's own packages (numpy, pillow, websockets,
pyjwt, cryptography). The next availability check (≤ 60 s) picks the voice up without a restart."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time

from cnq.agents.i18n import DEFAULT, norm_lang

# one male voice per language — the same as scripts/welcome_voice.py VOICES (tests/test_tts.py keeps them equal)
VOICES = {"ru": "ru-RU-DmitryNeural", "en": "en-US-AndrewNeural", "uk": "uk-UA-OstapNeural", "el": "el-GR-NestorasNeural",
          "es": "es-ES-AlvaroNeural"}
RATE = "+0%"                 # 21.10 owner: the −4% of the greeting sounded «еле-еле» for reports
# 29.10 (owner: male voices only, never a robotic female voice): ru-RU-DmitryNeural, the one Russian male voice, is refused most of
# the time («No audio was received», on the server and on fresh runners). After Dmitry's 2 attempts the Russian text goes to a male
# MULTILINGUAL voice, which reads Russian itself. Never Svetlana / Dariya, never gTTS. The cache key stays Dmitry's (key() below),
# so an mp3 made by the fallback is kept and served like any other. The probe on a runner (29.10, 3 Russian sentences of the report
# and of NOVA AI answers × 10 spaced rounds, one bare call each; faster-whisper small → word error rate vs the words given):
# Dmitry 12/30 (the report sentence 0/10), WER 0.053 where it spoke; en-US-BrianMultilingualNeural 30/30, median 0.8 s, WER 0.029.
# 29.10 15:42 (owner: «если женский голос хороший и правильно читает — не проблема … задержки хуже, чем пол диктора»): a good
# Microsoft NEURAL voice of either gender is fine — never gTTS or the phone's robotic voice. Probe on a runner (13:45 UTC, the same
# 3 sentences × 10): Svetlana 30/30, first audio 0.18 s, WER 0.034 — not refused like Dmitry; ru-RU-DariyaNeural is not in Edge's
# list. The server's own timings (api-health): every voice is slower there (first audio 1.2–8.6 s), and en's Andrew took 10.6 s
# (uk 3.3 s, el 3.9 s, es 2.3 s stay as they are). Each language's fallbacks, in order (the background order; live races them).
# 14:10: with Svetlana racing, 6 of 9 fresh Russian answers on the server came under 6 s (Svetlana's first audio 2.3–7.3 s there);
# Florian Multilingual (probe 60/60, WER 0.04; the fastest multilingual voice at midday) races as a third fallback.
FALLBACK = {"ru": ["ru-RU-SvetlanaNeural", "en-US-BrianMultilingualNeural", "de-DE-FlorianMultilingualNeural"],
            "en": ["en-US-ChristopherNeural", "en-US-JennyNeural"]}
RETRY_PAUSE_S = 1.0
# the app waits 8 s for the server's voice, then reads with the phone (NovaSpeech.kt). Dmitry's refusals take ~2.6 s each (probe) or
# hang until TIMEOUT_S (seen live), so a voice with a fallback behind it that sends no audio within FIRST_AUDIO_S counts as refused:
# a request the app waits for (POST /v2/tts, a report mp3 made on the spot) is LIVE: the main voice ONCE (≤ 2 s) raced against all
# its fallbacks; the background report (prewarm) keeps the patient order, each voice 2 × (≤ 2 s when another follows). A healthy Dmitry made a whole
# sentence in ~0.9 s (median, probe); only the wait for the FIRST audio is cut — once the voice speaks, it has the full TIMEOUT_S.
# /v2/health tts_last shows each attempt: voice, outcome (ok / no_audio / silent / timeout / …), start, connects, first audio, total.
FIRST_AUDIO_S = 2.0
# 29.10 (later): the server's timings showed DNS + TLS ~23 ms and the cache write ~1 ms — the time is Microsoft's (a voice's first
# audio 0.2 s on a runner, 1.2–8.6 s on the server at the same minute). So a LIVE request starts the main voice and ALL its fallbacks
# together; the main voice keeps priority when it speaks within FIRST_AUDIO_S, else the first fallback to finish wins. A fallback
# request that fails gets one successor (FALLBACK_REQUESTS per voice). The twin of a silent Brian (12:30) is gone: with several voices
# racing it rarely won.
FALLBACK_REQUESTS = 2
MAX_CHARS = 1500
MAX_CACHE_MB = 200
TIMEOUT_S = 25.0
STRESS = "́"
KEY_RE = re.compile(r"^[0-9a-f]{24}$")
PIP_SPEC = "edge-tts>=7,<8"
# 29.10 (build 50): Microsoft's free voice refuses about every other request now and then («No audio was received», seen on the
# server and on a runner for plain Russian sentences that pass a minute later). The daily report is made in the background, so it
# is tried patiently: up to PREWARM_TRIES makes (each = synth's own 2 attempts) with growing pauses; words that still fail rest
# for PREWARM_REST_S (no hammering the service on every /v2/home). The report's mp3 address waits a little for a make that runs.
PREWARM_TRIES = 4
PREWARM_PAUSE_S = 4.0
PREWARM_REST_S = 300
ROUTE_WAIT_S = 6.0
# the report's words change about every minute (the live numbers): one background make per language at most every 2 minutes, and
# after a make that gave up the language waits 2, 4, 8 … up to 30 minutes (the API keeps serving the last report whose voice is ready)
PREWARM_SPACING_S = 120
PREWARM_BACKOFF_MAX_S = 1800
INSTALL_EVERY_S = 6 * 3600
INSTALL_TIMEOUT_S = 600


def spoken(text: str, lang: str) -> str:
    """what the voice reads: the Latin brand is read badly by the Russian / Ukrainian voices («Новa», letters), so they get the
    Cyrillic name with the stress (Но́ва); the other voices get «Crypto Nova» / «Nova AI» in normal case; Markdown stars go"""
    s = re.sub(r"\s+", " ", str(text or "")).replace("*", "").replace("NOVΛ", "NOVA").replace("\u2212", "-")   # NBSP → space first
    if lang in ("ru", "uk"):
        s = s.replace("CRYPTO NOVA", f"Крипто Но{STRESS}ва").replace("NOVA AI", f"Но{STRESS}ва").replace("NOVA", f"Но{STRESS}ва")
    else:
        s = s.replace("CRYPTO NOVA", "Crypto Nova").replace("NOVA AI", "Nova AI").replace("NOVA", "Nova")
    return re.sub(r"\s+", " ", s).strip()


def key(text: str, lang: str) -> str:
    lang = norm_lang(lang)
    return hashlib.sha256(f"{VOICES[lang]}|{RATE}|{spoken(text, lang)}".encode()).hexdigest()[:24]


def available() -> bool:
    """edge-tts is importable (a pip install on the server works without a restart)"""
    try:
        importlib.invalidate_caches()
        return importlib.util.find_spec("edge_tts") is not None
    except Exception:  # noqa: BLE001
        return False


def voices(lang: str) -> list[str]:
    """the voices tried for a language, in order: its own voice, then its fallbacks (FALLBACK) — Microsoft neural voices only"""
    lang = norm_lang(lang)
    return [VOICES[lang]] + [v for v in FALLBACK.get(lang, []) if v and v != VOICES[lang]]


_used = threading.local()        # the voice that made the last mp3 in this thread, and the attempts (for /v2/health "tts_last")


class _Silent(Exception):
    """the voice sent no audio within its first-audio deadline (FIRST_AUDIO_S)"""


_CONNECTOR = None
_INFLIGHT = [0]                  # syntheses running right now in this process (tts_last "inflight": how many others were running)
_INFLIGHT_LOCK = threading.Lock()


def _timed_connector():
    """29.10: an aiohttp connector that times its connects (DNS + TCP + TLS, a proxy if any) for tts_last; edge-tts opens a session
    per text chunk and again on its 403 retry, so the session's close leaves it open and synth closes it (shut). None → none."""
    global _CONNECTOR
    if _CONNECTOR is None:
        try:
            import aiohttp

            class Timed(aiohttp.TCPConnector):
                def __init__(self, *a, **kw):
                    super().__init__(*a, **kw)
                    self.connect_ms: list[int] = []

                async def connect(self, *a, **kw):
                    t = time.perf_counter()
                    try:
                        return await super().connect(*a, **kw)
                    finally:
                        self.connect_ms.append(round((time.perf_counter() - t) * 1000))

                def close(self, *a, **kw):             # the session's close: kept open for the next chunk / the 403 retry
                    import asyncio
                    return asyncio.sleep(0)

                async def shut(self) -> None:
                    await aiohttp.TCPConnector.close(self)
            _CONNECTOR = Timed
        except Exception:  # noqa: BLE001
            _CONNECTOR = False
    try:
        return _CONNECTOR() if _CONNECTOR else None
    except Exception:  # noqa: BLE001
        return None


def _outcome(e: BaseException) -> str:
    import asyncio
    if isinstance(e, _Silent):
        return "silent"                                      # cut: no audio within FIRST_AUDIO_S
    if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
        return "timeout"                                     # TIMEOUT_S
    n = type(e).__name__
    return "no_audio" if n == "NoAudioReceived" else n[:24]


def synth(text: str, lang: str, timeout: float = TIMEOUT_S, live: bool = False) -> bytes:
    """the mp3 of the text in that language's voice (2 attempts), then in each of its fallbacks (2 attempts each); raises when every
    voice failed. live (29.10: a request the app waits for, 8 s at most) → the voice and all its fallbacks start together (race
    below): one request of the voice, up to FALLBACK_REQUESTS of each fallback. Every attempt is timed into _used.attempts (voice, outcome, connect / first audio / total ms)."""
    t00 = time.perf_counter()
    edge_tts = importlib.import_module("edge_tts")
    lang = norm_lang(lang)
    say = spoken(text, lang)
    ms = lambda t: round((time.perf_counter() - t) * 1000)  # noqa: E731
    attempts: list[dict] = []
    with _INFLIGHT_LOCK:
        _used.inflight = _INFLIGHT[0]
        _INFLIGHT[0] += 1
    _used.voice, _used.attempts, _used.prep_ms = None, attempts, ms(t00)
    try:
        return _synth_attempts(edge_tts, say, lang, timeout, live, attempts, ms, t00)
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT[0] -= 1


def _synth_attempts(edge_tts, say: str, lang: str, timeout: float, live: bool, attempts: list, ms, t00: float) -> bytes:
    """synth's attempts (voice order, first-audio cut, timings) — split out so synth can count the syntheses in flight"""
    import asyncio

    async def run(voice: str, first_s: float | None, rec: dict) -> bytes:
        """the voice's mp3; first_s = no audio by then → _Silent (only for a voice with a fallback behind it)"""
        buf = bytearray()
        loop = asyncio.get_running_loop()
        t0 = time.perf_counter()
        end = loop.time() + first_s if first_s else None
        conn = _timed_connector()
        try:
            try:
                comm = edge_tts.Communicate(say, voice, rate=RATE, **({"connector": conn} if conn is not None else {}))
            except TypeError:                                # an edge-tts without the connector argument
                if conn is not None:
                    await conn.shut()
                conn, comm = None, edge_tts.Communicate(say, voice, rate=RATE)
            it = comm.stream().__aiter__()
            while True:
                try:
                    nxt = it.__anext__()
                    chunk = await (asyncio.wait_for(nxt, max(0.01, end - loop.time())) if end is not None and not buf else nxt)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    if end is not None and not buf:
                        raise _Silent(f"no audio within {first_s} s") from None
                    raise
                if chunk.get("type") == "audio" and chunk.get("data"):
                    if not buf:
                        rec["first_audio_ms"] = ms(t0)
                    buf += chunk["data"]
            return bytes(buf)
        finally:
            if conn is not None:
                rec["connect_ms"] = list(conn.connect_ms)
                try:
                    await conn.shut()
                except BaseException:  # noqa: BLE001
                    pass
    async def race(first: str, fbs: list) -> tuple[str, bytes]:
        """live (29.10): the main voice and ALL its fallbacks start TOGETHER. The main voice wins when it speaks (it is cut when
        silent for FIRST_AUDIO_S; a fallback already done waits for that verdict, ≤ 2 s); else the fallback request that finished
        first. A fallback voice whose requests all failed gets a successor (at most FALLBACK_REQUESTS a voice)."""
        recs, voice_of, finished = {}, {}, []

        def start(voice: str, first_s: float | None):
            rec = {"voice": voice, "at_ms": ms(t00)}
            attempts.append(rec)
            t0 = time.perf_counter()

            async def go() -> bytes:
                try:
                    data = await asyncio.wait_for(run(voice, first_s, rec), timeout)
                except asyncio.CancelledError:
                    rec["ms"], rec["outcome"] = ms(t0), "cancelled"
                    raise
                except Exception as e:  # noqa: BLE001
                    rec["ms"], rec["outcome"] = ms(t0), _outcome(e)
                    raise
                rec["ms"] = ms(t0)
                if len(data) <= 1000:
                    rec["outcome"] = "short"
                    raise RuntimeError("no audio was received")
                rec["outcome"] = "ok"
                return data
            task = asyncio.ensure_future(go())
            task.add_done_callback(finished.append)           # the order they finished in
            recs[task], voice_of[task] = rec, voice
            return task

        ok = lambda t: t.done() and not t.cancelled() and t.exception() is None  # noqa: E731
        d = start(first, FIRST_AUDIO_S)
        reqs = {v: [start(v, None)] for v in fbs}
        err = None
        try:
            while True:
                if ok(d):
                    return first, d.result()
                win = next((t for t in finished if t is not d and ok(t)), None)
                if win is not None and d.done():
                    return voice_of[win], win.result()
                for t in finished:
                    if not t.cancelled() and t.exception() is not None:
                        err = t.exception()
                if win is None:
                    more = [v for v, ts in reqs.items() if len(ts) < FALLBACK_REQUESTS and all(t.done() for t in ts)]
                    for v in more:
                        reqs[v].append(start(v, None))        # a failed fallback voice's successor
                    if more:
                        continue
                waiting = [t for t in recs if not t.done()]
                if not waiting:
                    raise RuntimeError(f"tts failed: {str(err)[:160]}")
                await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
        finally:
            rest = [t for t in recs if not t.done()]
            for t in rest:
                t.cancel()
            if rest:
                await asyncio.gather(*rest, return_exceptions=True)

    err = None
    vs = voices(lang)
    if live and len(vs) > 1:
        voice, data = asyncio.run(race(vs[0], vs[1:]))
        _used.voice = voice
        return data
    for i, voice in enumerate(vs):
        has_fallback = i + 1 < len(vs)
        first_s = FIRST_AUDIO_S if has_fallback else None
        for attempt in range(2):
            if attempt:
                time.sleep(RETRY_PAUSE_S)                    # a pause before the voice's second attempt; none before a new voice
            rec = {"voice": voice, "at_ms": ms(t00)}
            attempts.append(rec)
            t0 = time.perf_counter()
            try:
                data = asyncio.run(asyncio.wait_for(run(voice, first_s, rec), timeout))
                rec["ms"] = ms(t0)
                if len(data) > 1000:
                    rec["outcome"] = "ok"
                    _used.voice = voice
                    return data
                rec["outcome"] = "short"
                err = RuntimeError("no audio was received")
            except Exception as e:  # noqa: BLE001
                rec["ms"], rec["outcome"] = ms(t0), _outcome(e)
                err = e
    raise RuntimeError(f"tts failed: {str(err)[:160]}")


def _mask(text: str) -> str:
    """credentials in a URL (an index with user:password@) never reach the journal or /v2/health"""
    return re.sub(r"://[^/@\s]+@", "://***@", text)


def _pip() -> tuple[int, str]:
    """pip install the voice into this very Python (the API's venv): binary wheels only, quiet, no questions"""
    cmd = [sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", "--no-input", "--only-binary=:all:", PIP_SPEC]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=INSTALL_TIMEOUT_S, env={**os.environ, "PIP_NO_INPUT": "1"})
    return r.returncode, (r.stderr or r.stdout or "").strip()


class Installer:
    """edge-tts missing → ONE background pip install, at most once per INSTALL_EVERY_S (the last try is kept in a file, so a restart
    does not retry sooner). Never blocks: maybe_start() only starts a thread. run_fn / now_fn are for the tests."""

    def __init__(self, state_path: str, run_fn=None, now_fn=None, on_done=None, every_s: float = INSTALL_EVERY_S):
        self.path, self._run, self._now, self.on_done, self.every = state_path, run_fn or _pip, now_fn or time.time, on_done, every_s
        self._lock = threading.Lock()
        self.running = False
        self.state = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.state, f)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def maybe_start(self) -> bool:
        """start the install now unless one runs or the last try was less than `every` seconds ago; True = started"""
        with self._lock:
            if self.running:
                return False
            try:
                last = float(self.state["at"]) if self.state.get("at") is not None else None
            except (TypeError, ValueError):
                last = None
            if last is not None and self._now() - last < self.every:
                return False
            self.running = True
            self.state = {"at": int(self._now()), "result": "running", "spec": PIP_SPEC}
            self._save()
        print(f"tts: edge-tts is not installed — pip install {PIP_SPEC} in the background", flush=True)
        threading.Thread(target=self._go, daemon=True, name="tts-pip").start()
        return True

    def _go(self) -> None:
        t0 = time.time()
        try:
            code, tail = self._run()
            res = "ok" if code == 0 else "failed"
        except Exception as e:  # noqa: BLE001 — a timeout, no pip, no network: logged, tried again after `every`
            code, tail, res = -1, f"{type(e).__name__}: {e}", "failed"
        importlib.invalidate_caches()
        sec = round(time.time() - t0, 1)
        with self._lock:
            self.state = {**self.state, "result": res, "code": code, "sec": sec, "tail": _mask(str(tail))[-300:]}
            self._save()
            self.running = False
        print(f"tts: pip install {PIP_SPEC} → {res} (exit {code}, {sec} s) {_mask(str(tail))[-200:]}", flush=True)
        if self.on_done:
            try:
                self.on_done(res == "ok")
            except Exception:  # noqa: BLE001
                pass


class Tts:
    """the cache, the per-IP limit and the background making of the report's mp3"""

    def __init__(self, cache_dir: str, synth_fn=None, available_fn=None, max_mb: int = MAX_CACHE_MB, installer=None):
        from .pf_review import Limiter
        self.dir, self.max_bytes = cache_dir, max_mb * 1024 * 1024
        self.synth, self._avail = synth_fn or synth, available_fn or available
        self._avail_at, self._avail_v = 0.0, False
        # build 50: the running server (cnq.api.server main) attaches the installer; tests and tools never run pip
        self.installer = None
        if installer is not None:
            self.attach_installer(installer)
        self.limit = Limiter(per_window=30, window_s=600, per_day=300)
        self._busy: set[str] = set()
        self._failed: dict[str, float] = {}      # key → when its background make last gave up (build 50)
        self._lang_next: dict[str, float] = {}   # language → no background make before this time (build 50)
        self._lang_fails: dict[str, int] = {}    # language → background makes that gave up in a row
        self.prewarm_pause = PREWARM_PAUSE_S
        self._lock = threading.Lock()
        self.last: dict | None = None            # the last synthesis: result, ms, lang, chars — for /v2/health (no text)

    def available(self) -> bool:
        """edge-tts installed? Looked up at most once a minute (a pip install is picked up without a restart); missing → the
        background install (build 50)"""
        now = time.time()
        if now - self._avail_at > 60:
            self._avail_at, self._avail_v = now, bool(self._avail())
            if not self._avail_v and self.installer is not None:
                self.installer.maybe_start()
        return self._avail_v

    def attach_installer(self, installer: "Installer | None" = None) -> "Installer":
        """the server's own voice install (build 50): state next to the cache (<run>/data/tts_install.json)"""
        if installer is None:
            installer = Installer(os.path.join(os.path.dirname(os.path.abspath(self.dir)), "tts_install.json"))
        if installer.on_done is None:
            installer.on_done = self._installed
        self.installer = installer
        return installer

    def _installed(self, ok: bool) -> None:
        """the install finished: look again at the next request instead of waiting for the minute"""
        if ok:
            self._avail_at = 0.0

    def install_state(self) -> dict | None:
        """for /v2/health: the last install try (when, result, exit code, seconds; pip's last words only when it failed, with any
        credentials in a URL masked)"""
        if self.installer is None:
            return None
        st = dict(self.installer.state)
        tail = str(st.pop("tail", "") or "")
        if st.get("result") == "failed" and tail:
            st["error"] = _mask(tail)[-200:]
        return st or None

    def path(self, k: str) -> str:
        return os.path.join(self.dir, f"{k}.mp3")

    def has(self, k: str) -> bool:
        """the mp3 of this key is in the cache (no read)"""
        return bool(KEY_RE.match(k or "")) and os.path.exists(self.path(k))

    def get(self, k: str) -> bytes | None:
        if not KEY_RE.match(k or ""):
            return None
        try:
            with open(self.path(k), "rb") as f:
                data = f.read()
            os.utime(self.path(k))               # recently used files stay in the cache longest
            return data
        except OSError:
            return None

    def put(self, k: str, data: bytes) -> None:
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.path(k) + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, self.path(k))
        self._trim()

    def _trim(self) -> None:
        try:
            files = [os.path.join(self.dir, n) for n in os.listdir(self.dir) if n.endswith(".mp3")]
            st = sorted(((os.path.getmtime(p), os.path.getsize(p), p) for p in files))
            total = sum(s for _m, s, _p in st)
            for _m, s, p in st:
                if total <= self.max_bytes:
                    break
                os.remove(p)
                total -= s
        except OSError:
            pass

    def make(self, text: str, lang: str, live: bool = False) -> tuple[str, bytes]:
        """(key, mp3) from the cache or made now; live = the app is waiting (8 s at most) → synth's quick order (29.10)"""
        lang = norm_lang(lang)
        k = key(text, lang)
        data = self.get(k)
        if data is not None:
            return k, data
        t0 = time.time()
        _used.voice, _used.attempts, _used.prep_ms, _used.inflight = None, None, None, None
        try:
            data = self.synth(text, lang, live=True) if live else self.synth(text, lang)
        except Exception as e:  # noqa: BLE001
            self.last = {"result": "failed", "ms": round((time.time() - t0) * 1000), "lang": lang, "chars": len(text), "at": int(t0),
                         "live": live, "error": str(e)[:120], **self._timings()}
            raise
        self.last = {"result": "ok", "ms": round((time.time() - t0) * 1000), "lang": lang, "chars": len(text), "at": int(t0),
                     "live": live, "bytes": len(data)}
        if getattr(_used, "voice", None):                 # 29.10: which voice spoke (the Russian fallback shows here)
            self.last["voice"] = _used.voice
        self.last.update(self._timings())
        t1 = time.perf_counter()
        self.put(k, data)
        self.last["put_ms"] = round((time.perf_counter() - t1) * 1000)
        return k, data

    @staticmethod
    def _timings() -> dict:
        """29.10: where the time went (for /v2/health tts_last): before the first attempt, and each attempt — voice, outcome, its start,
        connects (DNS + TCP + TLS), first audio, total (ms)"""
        out = {}
        if getattr(_used, "prep_ms", None) is not None:
            out["prep_ms"] = _used.prep_ms
        if getattr(_used, "inflight", None) is not None:
            out["inflight"] = _used.inflight
        if getattr(_used, "attempts", None):
            out["attempts"] = [dict(a) for a in _used.attempts]
        return out

    def prewarm(self, text: str, lang: str) -> None:
        """make the report's mp3 in the background (once at a time per key) so the app's first play is instant; build 50: patient
        retries with growing pauses, and words that still fail rest for PREWARM_REST_S"""
        lang = norm_lang(lang)
        k = key(text, lang)
        if self.has(k) or not self.available():
            return
        now = time.time()
        with self._lock:
            if k in self._busy or now - self._failed.get(k, 0.0) < PREWARM_REST_S or now < self._lang_next.get(lang, 0.0):
                return
            self._busy.add(k)
            self._lang_next[lang] = now + PREWARM_SPACING_S

        def run():
            try:
                for i in range(PREWARM_TRIES):
                    try:
                        self.make(text, lang)
                        with self._lock:
                            self._failed.pop(k, None)
                            self._lang_fails[lang] = 0
                        return
                    except Exception:  # noqa: BLE001 — the voice service refused: a pause, then again
                        if i + 1 < PREWARM_TRIES:
                            time.sleep(self.prewarm_pause * (i + 1))
                with self._lock:
                    self._failed[k] = time.time()
                    n = self._lang_fails.get(lang, 0) + 1
                    self._lang_fails[lang] = n
                    self._lang_next[lang] = time.time() + min(PREWARM_BACKOFF_MAX_S, PREWARM_SPACING_S * 2 ** (n - 1))
                    if len(self._failed) > 200:
                        for old in sorted(self._failed, key=self._failed.get)[:100]:
                            self._failed.pop(old, None)
            finally:
                with self._lock:
                    self._busy.discard(k)
        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------------ the routes
    def post(self, data: dict, ip: str) -> tuple[int, object]:
        text = str((data or {}).get("text") or "").strip()
        lang = norm_lang((data or {}).get("lang") or DEFAULT)
        if len(text) < 2:
            return 400, {"error": "bad_text"}
        if len(text) > MAX_CHARS:
            return 413, {"error": "too_long", "max_chars": MAX_CHARS}
        k = key(text, lang)
        cached = self.get(k)
        if cached is not None:
            return 200, Audio(cached, k)
        if not self.available():
            return 503, {"error": "tts_unavailable"}
        wait = self.limit.hit(ip)
        if wait:
            return 429, {"error": "rate", "retry_after_min": wait}
        try:
            k, mp3 = self.make(text, lang, live=True)
        except Exception:  # noqa: BLE001
            return 502, {"error": "tts_failed"}
        return 200, Audio(mp3, k)

    def get_route(self, k: str, reports: dict) -> tuple[int, object]:
        """GET /v2/tts/<key>.mp3 — reports = {key: (text, lang)} of the current /v2/home reports"""
        cached = self.get(k)
        if cached is not None:
            return 200, Audio(cached, k)
        if k not in reports:
            return 404, {"error": "not_found"}
        if not self.available():
            return 503, {"error": "tts_unavailable"}
        # build 50: the background make of these words is running — wait a little for it instead of asking the service twice
        end = time.time() + ROUTE_WAIT_S
        while k in self._busy and time.time() < end:
            time.sleep(0.25)
            cached = self.get(k)
            if cached is not None:
                return 200, Audio(cached, k)
        text, lang = reports[k]
        try:
            k, mp3 = self.make(text, lang, live=True)
        except Exception:  # noqa: BLE001
            return 502, {"error": "tts_failed"}
        return 200, Audio(mp3, k)


class Audio:
    """a binary answer of the API (cnq/api/server.py make_handler sends it as audio/mpeg)"""
    ctype = "audio/mpeg"

    def __init__(self, body: bytes, k: str):
        self.body, self.key = body, k
