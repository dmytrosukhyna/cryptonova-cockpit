"""The spoken greeting of the 2.0 app (first launch, «Послушать»): web/welcome.mp3, ~30 seconds, voice of NOVA.
Run by .github/workflows/voice-welcome.yml (edge-tts needs the internet). Change TEXT and run the workflow again."""
import asyncio
import os
import sys

VOICE = os.environ.get("CNQ_WELCOME_VOICE", "ru-RU-SvetlanaNeural")
TEXT = (
    "Привет! Я Нова — помощник CRYPTO NOVA. "
    "Наша система каждую минуту следит за рынком криптовалют и проверяет каждую идею на истории цен. "
    "Здесь всё бесплатно: демо-счёт на десять тысяч долларов, копилка в биткоин и тридцать восемь уроков простыми словами. "
    "Деньги в демо учебные, так что можно спокойно смотреть и учиться. "
    "А если что-то непонятно — нажмите на светящийся шар внизу экрана и спросите меня. Поехали!"
)


async def main(out: str) -> None:
    import edge_tts
    await edge_tts.Communicate(TEXT, VOICE, rate="-4%").save(out)


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "web/welcome.mp3"
    asyncio.run(main(path))
    print(path, os.path.getsize(path), "bytes")
