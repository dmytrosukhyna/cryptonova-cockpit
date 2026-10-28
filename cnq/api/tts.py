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
spoken text, so the same words are made once. Nothing about the caller is stored. Stdlib + edge-tts (optional)."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import os
import re
import threading
import time

from cnq.agents.i18n import DEFAULT, norm_lang

# one male voice per language — the same as scripts/welcome_voice.py VOICES (tests/test_tts.py keeps them equal)
VOICES = {"ru": "ru-RU-DmitryNeural", "en": "en-US-AndrewNeural", "uk": "uk-UA-OstapNeural", "el": "el-GR-NestorasNeural",
          "es": "es-ES-AlvaroNeural"}
RATE = "+0%"                 # 21.10 owner: the −4% of the greeting sounded «еле-еле» for reports
MAX_CHARS = 1500
MAX_CACHE_MB = 200
TIMEOUT_S = 25.0
STRESS = "́"
KEY_RE = re.compile(r"^[0-9a-f]{24}$")


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


def synth(text: str, lang: str, timeout: float = TIMEOUT_S) -> bytes:
    """the mp3 of the text in that language's male voice; raises on failure (one retry)"""
    import asyncio
    edge_tts = importlib.import_module("edge_tts")
    lang = norm_lang(lang)
    say = spoken(text, lang)

    async def run() -> bytes:
        buf = bytearray()
        async for chunk in edge_tts.Communicate(say, VOICES[lang], rate=RATE).stream():
            if chunk.get("type") == "audio" and chunk.get("data"):
                buf += chunk["data"]
        return bytes(buf)
    err = None
    for attempt in range(2):
        try:
            data = asyncio.run(asyncio.wait_for(run(), timeout))
            if len(data) > 1000:
                return data
            err = RuntimeError("no audio was received")
        except Exception as e:  # noqa: BLE001
            err = e
        time.sleep(1.0 + attempt)
    raise RuntimeError(f"tts failed: {str(err)[:160]}")


class Tts:
    """the cache, the per-IP limit and the background making of the report's mp3"""

    def __init__(self, cache_dir: str, synth_fn=None, available_fn=None, max_mb: int = MAX_CACHE_MB):
        from .pf_review import Limiter
        self.dir, self.max_bytes = cache_dir, max_mb * 1024 * 1024
        self.synth, self._avail = synth_fn or synth, available_fn or available
        self._avail_at, self._avail_v = 0.0, False
        self.limit = Limiter(per_window=30, window_s=600, per_day=300)
        self._busy: set[str] = set()
        self._lock = threading.Lock()
        self.last: dict | None = None            # the last synthesis: result, ms, lang, chars — for /v2/health (no text)

    def available(self) -> bool:
        """edge-tts installed? Looked up at most once a minute (a pip install is picked up without a restart)"""
        now = time.time()
        if now - self._avail_at > 60:
            self._avail_at, self._avail_v = now, bool(self._avail())
        return self._avail_v

    def path(self, k: str) -> str:
        return os.path.join(self.dir, f"{k}.mp3")

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
        try:
            data = self.synth(text, lang)
        except Exception as e:  # noqa: BLE001
            self.last = {"result": "failed", "ms": round((time.time() - t0) * 1000), "lang": lang, "chars": len(text), "at": int(t0),
                         "error": str(e)[:120]}
            raise
        self.last = {"result": "ok", "ms": round((time.time() - t0) * 1000), "lang": lang, "chars": len(text), "at": int(t0),
                     "bytes": len(data)}
        self.put(k, data)
        return k, data

    def prewarm(self, text: str, lang: str) -> None:
        """make the report's mp3 in the background (once at a time per key) so the app's first play is instant"""
        k = key(text, lang)
        if self.get(k) is not None or not self.available():
            return
        with self._lock:
            if k in self._busy:
                return
            self._busy.add(k)

        def run():
            try:
                self.make(text, lang)
            except Exception:  # noqa: BLE001 — the GET makes it again on demand
                pass
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
