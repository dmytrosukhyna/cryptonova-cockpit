"""10.11 (owner: NOVA voice yellow — edge-tts refuses the Russian voices now and then): Azure Speech first when its key is set,
edge-tts behind it; the key never appears in an error; the regime text names the average the rule really uses."""
import io
import os

from cnq.api import tts
from cnq.terminal.report import long_text_ru


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_azure_request_ssml_and_errors(monkeypatch):
    monkeypatch.delenv("AZURE_SPEECH_KEY", raising=False)
    assert tts.azure_cfg() is None
    monkeypatch.setenv("AZURE_SPEECH_KEY", "secret-123")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "northeurope")
    seen = {}

    def opener(req, timeout):
        seen.update(url=req.full_url, body=req.data.decode(), h=dict(req.header_items()))
        return _Resp(b"\xff" * 4000)
    data = tts.azure_mp3("Крипто Но́ва & «рынок»", "ru-RU-DmitryNeural", opener=opener)
    assert len(data) == 4000 and seen["url"] == "https://northeurope.tts.speech.microsoft.com/cognitiveservices/v1"
    assert "<voice name='ru-RU-DmitryNeural'>" in seen["body"] and "&amp;" in seen["body"] and f"rate='{tts.RATE}'" in seen["body"]
    assert seen["h"]["Ocp-apim-subscription-key"] == "secret-123" and "mp3" in seen["h"]["X-microsoft-outputformat"]

    def bad(req, timeout):
        raise OSError("secret-123 leaked?")
    try:
        tts.azure_mp3("текст", "ru-RU-DmitryNeural", opener=bad)
        assert False
    except RuntimeError as e:
        assert "secret" not in str(e)
    assert tts.available()


def test_brief_uses_azure_first(monkeypatch, tmp_path):
    from cnq.agents import voice
    monkeypatch.setenv("AZURE_SPEECH_KEY", "k")
    monkeypatch.setattr(tts, "azure_mp3", lambda text, v, **k: b"\x01" * 5000)
    errs = []
    assert voice.azure_to("Добрый вечер.", str(tmp_path / "b.mp3"), errs) == "azure:ru-RU-DmitryNeural" and not errs
    assert os.path.getsize(tmp_path / "b.mp3") == 5000
    monkeypatch.delenv("AZURE_SPEECH_KEY")
    assert voice.azure_to("x", str(tmp_path / "c.mp3"), errs) is None and not os.path.exists(tmp_path / "c.mp3")


def test_regime_text_names_the_real_average():
    r = {"long": "BEAR", "sma200": 76651.48, "sma_days": 105}
    assert long_text_ru(r) == "цена ниже средней цены за 105 дней ($76 651)"
    assert long_text_ru(dict(r, sma_days=200, long="BULL")) == "цена выше 200-дневной средней ($76 651) и выше, чем 90 дней назад"
    assert "ниже, чем 90 дней назад" in long_text_ru(dict(r, long="MIXED"))


def test_short_window_is_named_everywhere():
    from cnq.agents import app_why
    from cnq.agents.i18n import t
    assert t("ru", "sy.tr.bear", d=106) == "цена ниже средней за 106 дней" and t("en", "sy.tr.bull", d=106) == "the price is above its 106-day average"
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for f in ("cnq/agents/turbo_desk.py", "cnq/agents/sweep_desk.py", "cnq/terminal/report.py"):
        src = open(os.path.join(root, f), encoding="utf-8").read()
        assert "SMA200 =" not in src and "выше 200-дневной средней и за 60" not in src and "выше SMA200" not in src, f
    assert app_why  # imported: the catalog registers sy.tr.* with {d}
