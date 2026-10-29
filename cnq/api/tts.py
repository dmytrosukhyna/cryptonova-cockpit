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
FALLBACK = {"ru": "en-US-BrianMultilingualNeural"}
RETRY_PAUSE_S = 1.0
# the app waits 8 s for the server's voice, then reads with the phone (NovaSpeech.kt). Dmitry's refusals take ~2.6 s each (probe) or
# hang until TIMEOUT_S (seen live), so a voice with a fallback behind it that sends no audio within FIRST_AUDIO_S counts as refused:
# Dmitry 2 × ≤ 2 s + 1 s pause + Brian ~1 s stays inside the 8 s. A healthy Dmitry made a whole sentence in ~0.9 s (median, probe);
# only the wait for the FIRST audio is cut — once the voice speaks, it has the full TIMEOUT_S.
FIRST_AUDIO_S = 2.0
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
    """the voices tried for a language, in order: its own male voice, then its male fallback (Russian only) — never a female one"""
    lang = norm_lang(lang)
    return [VOICES[lang]] + [v for v in [FALLBACK.get(lang)] if v and v != VOICES[lang]]


_used = threading.local()        # the voice that made the last mp3 in this thread (for /v2/health "tts_last")


def synth(text: str, lang: str, timeout: float = TIMEOUT_S) -> bytes:
    """the mp3 of the text in that language's male voice (2 attempts), then in its male fallback voice (Russian, 29.10; 2 attempts);
    raises when every voice failed"""
    import asyncio
    edge_tts = importlib.import_module("edge_tts")
    lang = norm_lang(lang)
    say = spoken(text, lang)

    async def run(voice: str, first_s: float | None) -> bytes:
        """the voice's mp3; first_s = no audio by then → refused (only for a voice with a fallback behind it)"""
        buf = bytearray()
        loop = asyncio.get_running_loop()
        end = loop.time() + first_s if first_s else None
        it = edge_tts.Communicate(say, voice, rate=RATE).stream().__aiter__()
        while True:
            try:
                nxt = it.__anext__()
                chunk = await (asyncio.wait_for(nxt, max(0.01, end - loop.time())) if end is not None and not buf else nxt)
            except StopAsyncIteration:
                break
            if chunk.get("type") == "audio" and chunk.get("data"):
                buf += chunk["data"]
        return bytes(buf)
    err = None
    _used.voice = None
    vs = voices(lang)
    for i, voice in enumerate(vs):
        first_s = FIRST_AUDIO_S if i + 1 < len(vs) else None
        for attempt in range(2):
            if attempt:
                time.sleep(RETRY_PAUSE_S)                    # a pause before the voice's second attempt; none before a new voice
            try:
                data = asyncio.run(asyncio.wait_for(run(voice, first_s), timeout))
                if len(data) > 1000:
                    _used.voice = voice
                    return data
                err = RuntimeError("no audio was received")
            except Exception as e:  # noqa: BLE001
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

    def make(self, text: str, lang: str) -> tuple[str, bytes]:
        """(key, mp3) from the cache or made now"""
        lang = norm_lang(lang)
        k = key(text, lang)
        data = self.get(k)
        if data is not None:
            return k, data
        t0 = time.time()
        _used.voice = None
        try:
            data = self.synth(text, lang)
        except Exception as e:  # noqa: BLE001
            self.last = {"result": "failed", "ms": round((time.time() - t0) * 1000), "lang": lang, "chars": len(text), "at": int(t0),
                         "error": str(e)[:120]}
            raise
        self.last = {"result": "ok", "ms": round((time.time() - t0) * 1000), "lang": lang, "chars": len(text), "at": int(t0),
                     "bytes": len(data)}
        if getattr(_used, "voice", None):                 # 29.10: which voice spoke (the Russian fallback shows here)
            self.last["voice"] = _used.voice
        self.put(k, data)
        return k, data

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
            k, mp3 = self.make(text, lang)
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
            k, mp3 = self.make(text, lang)
        except Exception:  # noqa: BLE001
            return 502, {"error": "tts_failed"}
        return 200, Audio(mp3, k)


class Audio:
    """a binary answer of the API (cnq/api/server.py make_handler sends it as audio/mpeg)"""
    ctype = "audio/mpeg"

    def __init__(self, body: bytes, k: str):
        self.body, self.key = body, k
