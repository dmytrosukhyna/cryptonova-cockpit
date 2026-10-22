"""The spoken greeting of the 2.0 app (first launch, «Послушать»), ~30 seconds, voice of NOVA, in the app's five languages:
web/welcome.mp3 (Russian) and web/welcome.<lang>.mp3 (en, uk, el, es). Run by .github/workflows/voice-welcome.yml (edge-tts needs the
internet). Change a text and run the workflow again.  usage: welcome_voice.py [all | <lang> <out.mp3>]"""
import asyncio
import os
import sys

# 20.10 (owner: «плохо выговаривает слово "Нова"»): in Russian / Ukrainian the name is written in Cyrillic with the stress on the first
# syllable (Но́ва), «CRYPTO NOVA» as «Крипто Но́ва», so the voice does not read it as the adjective «нова́».
STRESS = "́"
NOVA = f"Но{STRESS}ва"

# 22.10 owner (TestFlight 43: «он сказал, что 38 уроков»): 52 lessons; the name is «NOVA AI» (EN/EL/ES say it, RU/UK keep the
# stressed Cyrillic «Но́ва», which the voice reads well).
# 21.10 owner (app spec §5, §10): one clear MALE voice everywhere the app speaks; the female voice is only a fallback
VOICES = {"ru": "ru-RU-DmitryNeural", "en": "en-US-AndrewNeural", "uk": "uk-UA-OstapNeural", "el": "el-GR-NestorasNeural",
          "es": "es-ES-AlvaroNeural"}
# build 45 (owner 22.10): only the male NOVA AI voice — no female fallback; if the male voice fails, the run fails and the
# greeting already on the server stays as it is
TEXTS = {
    "ru": (f"Привет! Я {NOVA} — помощник Крипто {NOVA}. "
           "Наша система каждую минуту следит за рынком криптовалют и проверяет каждую идею на истории цен. "
           "Здесь всё бесплатно: демо-счёт на десять тысяч долларов, копилка в биткоин и пятьдесят два коротких урока простыми словами. "
           "Деньги в демо учебные, так что можно спокойно смотреть и учиться. "
           "А если что-то непонятно — нажмите на светящийся шар внизу экрана и спросите меня. Поехали!"),
    "en": ("Hi! I'm Nova AI, the assistant of Crypto Nova. "
           "Our system watches the crypto market every minute and tests every idea on the history of prices. "
           "Everything here is free: a ten-thousand-dollar demo account, a bitcoin piggy bank, and fifty-two short lessons in plain words. "
           "The money in the demo is virtual, so you can calmly watch and learn. "
           "And if something isn't clear, tap the glowing sphere at the bottom of the screen and ask me. Let's go!"),
    "uk": (f"Привіт! Я {NOVA} — помічниця Крипто {NOVA}. "
           "Наша система щохвилини стежить за ринком криптовалют і перевіряє кожну ідею на історії цін. "
           "Тут усе безкоштовно: демо-рахунок на десять тисяч доларів, скарбничка в біткоїні та п'ятдесят два короткі уроки простими словами. "
           "Гроші в демо навчальні, тож можна спокійно дивитися й навчатися. "
           "А якщо щось незрозуміло — натисніть на сяючу кулю внизу екрана й запитайте мене. Поїхали!"),
    "el": ("Γεια σου! Είμαι η Νόβα AI, η βοηθός του Crypto Nova. "
           "Το σύστημά μας παρακολουθεί κάθε λεπτό την αγορά κρυπτονομισμάτων και ελέγχει κάθε ιδέα στο ιστορικό των τιμών. "
           "Εδώ όλα είναι δωρεάν: ένας λογαριασμός demo δέκα χιλιάδων δολαρίων, ένας κουμπαράς σε bitcoin και πενήντα δύο σύντομα μαθήματα με απλά λόγια. "
           "Τα χρήματα στο demo είναι εικονικά, οπότε μπορείς να παρακολουθείς και να μαθαίνεις με ηρεμία. "
           "Κι αν κάτι δεν είναι ξεκάθαρο, πάτησε τη φωτεινή σφαίρα στο κάτω μέρος της οθόνης και ρώτα με. Πάμε!"),
    "es": ("¡Hola! Soy Nova AI, la asistente de Crypto Nova. "
           "Nuestro sistema vigila el mercado cripto cada minuto y pone a prueba cada idea con el historial de precios. "
           "Aquí todo es gratis: una cuenta demo de diez mil dólares, una hucha en bitcoin y cincuenta y dos lecciones cortas con palabras sencillas. "
           "El dinero de la demo es virtual, así que puedes mirar y aprender con calma. "
           "Y si algo no está claro, toca la esfera brillante en la parte de abajo de la pantalla y pregúntame. ¡Vamos!"),
}


def out_path(lang: str) -> str:
    return "web/welcome.mp3" if lang == "ru" else f"web/welcome.{lang}.mp3"


async def make(lang: str, out: str) -> None:
    import edge_tts
    first = os.environ.get("CNQ_WELCOME_VOICE_" + lang.upper(), VOICES[lang])
    # the male voice sometimes fails once in a run: retry it with a pause (never another voice)
    for voice in [first, first, first, first]:
        try:
            await edge_tts.Communicate(TEXTS[lang], voice, rate="-4%").save(out)
            print(out, voice, os.path.getsize(out), "bytes")
            return
        except Exception as e:  # noqa: BLE001
            print("retry", lang, voice, e)
            await asyncio.sleep(4)
    raise SystemExit(f"no voice worked for {lang}")


if __name__ == "__main__":
    a = sys.argv[1:] or ["all"]
    if a[0] == "all":
        for lg in TEXTS:
            asyncio.run(make(lg, out_path(lg)))
    elif a[0] in TEXTS:
        asyncio.run(make(a[0], a[1] if len(a) > 1 else out_path(a[0])))
    else:                                   # old call: welcome_voice.py web/welcome.mp3 → Russian
        asyncio.run(make("ru", a[0]))
