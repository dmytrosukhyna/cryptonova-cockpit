"""build 46: POST /v2/tts — NOVA AI's male voice (cnq/api/tts.py): male voices only, the cache, the limit, an honest 503."""
from cnq.api import tts as T


def _fake(calls, fail=0):
    def synth(text, voice, path):
        calls.append((text, voice))
        if len(calls) <= fail:
            raise RuntimeError("No audio was received")
        open(path, "wb").write(b"ID3" + b"x" * 4000)
    return synth


def test_male_voice_cache_and_name(tmp_path):
    calls = []
    t = T.Tts(str(tmp_path), synth=_fake(calls))
    code, doc = t.speak("Публичный портфель CRYPTO NOVA сейчас 9 904 доллара.", "ru", "ip:1")
    assert code == 200 and doc["_audio"].startswith(b"ID3")
    assert calls[0][1] == "ru-RU-DmitryNeural" and "Крипто Но́ва" in calls[0][0]
    code, _ = t.speak("Публичный портфель CRYPTO NOVA сейчас 9 904 доллара.", "ru", "ip:1")
    assert code == 200 and len(calls) == 1                        # from the cache, no second call
    assert set(T.VOICES.values()) == {"ru-RU-DmitryNeural", "en-US-AndrewNeural", "uk-UA-OstapNeural", "el-GR-NestorasNeural", "es-ES-AlvaroNeural"}


def test_retry_then_honest_503(tmp_path):
    calls = []
    t = T.Tts(str(tmp_path), synth=_fake(calls, fail=1))
    assert t.speak("Hello there", "en", "ip:2")[0] == 200 and len(calls) == 2      # one failure, then the same male voice again
    calls2 = []
    t2 = T.Tts(str(tmp_path / "b"), synth=_fake(calls2, fail=10))
    code, doc = t2.speak("Γεια σας", "el", "ip:3")
    assert code == 503 and doc == {"error": "tts_unavailable"} and {v for _, v in calls2} == {"el-GR-NestorasNeural"}


def test_bad_text_and_limit(tmp_path):
    t = T.Tts(str(tmp_path), synth=_fake([]))
    assert t.speak(" ", "es", "ip:4")[0] == 400
    for i in range(T.PER_HOUR):
        assert t.speak(f"frase {i}", "es", "ip:5")[0] == 200
    assert t.speak("una más", "es", "ip:5") == (429, {"error": "rate"})
    assert t.speak("una más", "es", "ip:6")[0] == 200                # another caller is not limited
    assert T.spoken("en", "**NOVA AI** says") == "Nova AI says"
