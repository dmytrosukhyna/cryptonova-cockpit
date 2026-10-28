"""POST /v2/tts — NOVA AI's male voice for the 2.0 app (build 46, owner 23.10: «доклад и NOVA AI молчат, а экскурсия говорит»).

The app speaks its daily report and NOVA AI's answers through one voice path: this endpoint first (a male neural voice), then the
phone's own male voice, then text only. Before build 46 the app had only the phone's voice, and an iPhone without a downloaded male
voice stayed silent. Request {"text": ≤ 1500 chars, "lang": en|ru|uk|el|es} → 200 audio/mpeg; 503 {"error": "tts_unavailable"} when
edge-tts is not installed here or Microsoft's voice does not answer, 429 {"error": "rate"} over the limit, 400 on a bad request.

Only male voices (owner 22.10: never a female voice): the same ones as the spoken greeting (scripts/welcome_voice.py) — no female
fallback. Finished phrases are kept on disk (data/tts/<sha1>.mp3, ≤ 500 files, oldest removed), so a repeated report or answer is
instant. Text only, never stored with a user id. Install once on the server: /opt/cnq/venv/bin/pip install edge-tts
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import threading
import time

VOICES = {"ru": "ru-RU-DmitryNeural", "en": "en-US-AndrewNeural", "uk": "uk-UA-OstapNeural", "el": "el-GR-NestorasNeural",
          "es": "es-ES-AlvaroNeural"}
MAX_TEXT = 1500
MAX_FILES = 500
PER_HOUR = 120                      # phrases per caller (uid or IP) per hour
STRESS = "́"


def spoken(lang: str, text: str) -> str:
    """the name said well: «Но́ва» in Russian / Ukrainian (owner 20.10), «Nova» elsewhere; no markdown read aloud"""
    t = re.sub(r"[*_#`]", "", text or "").replace("NOVΛ", "NOVA")
    if lang in ("ru", "uk"):
        t = t.replace("CRYPTO NOVA", f"Крипто Но{STRESS}ва").replace("NOVA AI", f"Но{STRESS}ва").replace("NOVA", f"Но{STRESS}ва")
    else:
        t = t.replace("CRYPTO NOVA", "Crypto Nova").replace("NOVA AI", "Nova AI")
    return re.sub(r"\s+", " ", t).strip()


class Tts:
    def __init__(self, cache_dir: str, synth=None):
        self.dir = cache_dir
        os.makedirs(self.dir, exist_ok=True)
        self._synth = synth or self._edge
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = {}
        self.last: dict | None = None

    def _edge(self, text: str, voice: str, path: str) -> None:
        import edge_tts                                               # ImportError → 503 tts_unavailable

        async def go():
            await edge_tts.Communicate(text, voice, rate="+0%").save(path)
        asyncio.run(go())

    def _allowed(self, who: str) -> bool:
        now = time.time()
        with self._lock:
            h = [t for t in self._hits.get(who, []) if now - t < 3600]
            if len(h) >= PER_HOUR:
                self._hits[who] = h
                return False
            h.append(now)
            self._hits[who] = h
            return True

    def _trim(self) -> None:
        try:
            files = sorted((os.path.join(self.dir, f) for f in os.listdir(self.dir) if f.endswith(".mp3")), key=os.path.getmtime)
            for f in files[:max(0, len(files) - MAX_FILES)]:
                os.remove(f)
        except OSError:
            pass

    def speak(self, text: str, lang: str, who: str) -> tuple[int, dict]:
        lang = lang if lang in VOICES else "ru"
        text = (text or "").strip()[:MAX_TEXT]
        if len(text) < 2:
            return 400, {"error": "bad_text"}
        say = spoken(lang, text)
        key = hashlib.sha1(f"{lang}|{VOICES[lang]}|{say}".encode()).hexdigest()
        path = os.path.join(self.dir, key + ".mp3")
        if os.path.exists(path) and os.path.getsize(path) > 2000:
            os.utime(path, None)
            return 200, {"_audio": open(path, "rb").read()}
        if not self._allowed(who):
            return 429, {"error": "rate"}
        t0, err = time.time(), None
        for attempt in range(3):                                      # Microsoft's free voice sometimes answers nothing: retry, never another voice
            try:
                self._synth(say, VOICES[lang], path + ".tmp")
                if os.path.getsize(path + ".tmp") > 2000:
                    os.replace(path + ".tmp", path)
                    self._trim()
                    self.last = {"ok": True, "lang": lang, "ms": round((time.time() - t0) * 1000), "at": int(time.time())}
                    return 200, {"_audio": open(path, "rb").read()}
            except ImportError:
                err = "edge_tts_missing"
                break
            except Exception as e:  # noqa: BLE001
                err = str(e)[:120]
            time.sleep(0.6 * (attempt + 1))
        try:
            os.remove(path + ".tmp")
        except OSError:
            pass
        self.last = {"ok": False, "lang": lang, "error": err, "at": int(time.time())}
        return 503, {"error": "tts_unavailable"}
