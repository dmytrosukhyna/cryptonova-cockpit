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
# 28.10 build 46 (owner spec §5): the greeting introduces the product as it is now — the system, the PUBLIC portfolio (the real
# account since 22.10), the demo portfolio, the lessons, NOVA and the connection guide — and says that crypto is a risk. No
# «бесплатно» (the app's rule: no subscriptions, never the word «free»). Male voice → male forms (uk «помічник», el «ο βοηθός»,
# es «el asistente»).
TEXTS = {
    "ru": (f"Здравствуйте! Я {NOVA} — помощник Крипто {NOVA}. "
           f"Крипто {NOVA} — это система, которая круглосуточно следит за рынком криптовалют и работает по правилам, проверенным на истории цен. "
           "На главном экране — публичный портфель: реальный счёт системы, около десяти тысяч долларов, с двадцать второго октября. "
           "Можно завести свой демо-портфель, учиться и спрашивать меня. "
           "А если захотите подключиться к системе — в приложении есть пошаговая инструкция. Помните: крипта — это риск. Поехали!"),
    "en": ("Hi! I'm Nova AI, the assistant of Crypto Nova. "
           "Crypto Nova is a system that watches the crypto market around the clock and works by rules tested on the history of prices. "
           "On the Home screen you'll find the public portfolio: the system's real account, about ten thousand dollars, since October "
           "twenty-second. You can start your own demo portfolio, learn, and ask me anything. "
           "And if you want to connect to the system, there's a step-by-step guide in the app. Remember: crypto is risky. Let's go!"),
    "uk": (f"Вітаю! Я {NOVA} — помічник Крипто {NOVA}. "
           f"Крипто {NOVA} — це система, яка цілодобово стежить за ринком криптовалют і працює за правилами, перевіреними на історії цін. "
           "На головному екрані — публічний портфель: реальний рахунок системи, близько десяти тисяч доларів, з двадцять другого жовтня. "
           "Можна завести свій демо-портфель, навчатися й питати мене. "
           "А якщо захочете підключитися до системи — у застосунку є покрокова інструкція. Пам'ятайте: крипта — це ризик. Поїхали!"),
    "el": ("Γεια σας! Είμαι ο Νόβα AI, ο βοηθός του Crypto Nova. "
           "Το Crypto Nova είναι ένα σύστημα που παρακολουθεί συνεχώς την αγορά κρυπτονομισμάτων και λειτουργεί με κανόνες "
           "ελεγμένους στο ιστορικό των τιμών. Στην αρχική οθόνη θα βρείτε το δημόσιο χαρτοφυλάκιο: τον πραγματικό λογαριασμό του "
           "συστήματος, περίπου δέκα χιλιάδες δολάρια, από τις είκοσι δύο Οκτωβρίου. Μπορείτε να φτιάξετε το δικό σας χαρτοφυλάκιο demo, "
           "να μαθαίνετε και να με ρωτάτε. Κι αν θέλετε να συνδεθείτε στο σύστημα, στην εφαρμογή "
           "υπάρχουν οδηγίες βήμα προς βήμα. Να θυμάστε: τα κρύπτο έχουν ρίσκο. Πάμε!"),
    "es": ("¡Hola! Soy Nova AI, el asistente de Crypto Nova. "
           "Crypto Nova es un sistema que vigila el mercado cripto las veinticuatro horas y funciona con reglas probadas en el historial "
           "de precios. En la pantalla de inicio está la cartera pública: la cuenta real del sistema, unos diez mil dólares, desde el "
           "veintidós de octubre. Puedes crear tu propia cartera demo, aprender y preguntarme lo que quieras. "
           "Y si quieres conectarte al sistema, en la app hay una guía paso a paso. Recuerda: las cripto tienen riesgo. ¡Vamos!"),
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
