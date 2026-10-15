"""Spoken situation report (server side). Composes a short executive brief from the department JSON and renders it
with a neural Russian voice (edge-tts, Microsoft neural voices, free) into web/brief.mp3 + web/brief.json. The panel
plays the file at login when it is fresh (< 45 min) instead of the browser's robotic TTS. Optional: without
edge-tts the text is still written and the panel falls back to its own voice."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import time

VOICE = os.environ.get("CNQ_TTS_VOICE", "ru-RU-DmitryNeural")
NAME = os.environ.get("CNQ_OWNER_NAME") or "Дмитрий"


def _load(out: str, name: str):
    try:
        return json.load(open(os.path.join(out, name), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _latin(s: str) -> bool:
    lat = sum(len(w) for w in re.findall(r"\b[A-Za-z][a-z]{2,}\b", s or "")); cyr = len(re.findall(r"[А-Яа-яЁё]", s or ""))
    return lat > 12 and lat > 0.25 * (lat + cyr)


def spoken_price(p: float) -> str:
    if p >= 1000:
        k = round(p / 1000)
        return f"около {k} {'тысячи' if k % 10 == 1 and k % 100 != 11 else 'тысяч'}"
    return str(round(p)) if p >= 1 else f"{p:.4f}"


def compose(out: str = "web") -> str:
    hour = (dt.datetime.utcnow() + dt.timedelta(hours=3)).hour
    hello = "Доброй ночи" if hour < 5 else "Доброе утро" if hour < 12 else "Добрый день" if hour < 18 else "Добрый вечер"
    L = [f"{hello}, {NAME}. Система работает штатно."]
    paper = _load(out, "paper.json") or {}
    prof = (paper.get("profiles") or {}).get("bal") or paper
    if prof:
        n = len(prof.get("open") or [])
        closed = [t for t in (prof.get("closed") or []) if (t.get("closed") or 0) > time.time() * 1000 - 86400_000]
        won = sum(t.get("net_usd", 0) for t in closed if t.get("net_usd", 0) > 0)
        lost = -sum(t.get("net_usd", 0) for t in closed if t.get("net_usd", 0) < 0)
        parts = []
        if closed:
            parts.append(f"за сутки закрыли {len(closed)} {'сделку' if len(closed) == 1 else 'сделки' if len(closed) < 5 else 'сделок'}" +
                         (f", заработали {round(won)} долларов" if won else "") + (f", отдали {round(lost)} долларов" if lost else ""))
        parts.append(f"в работе {n}" if n else "открытых позиций нет — ждём цену у стенки")
        L.append("По фьючерсам " + ", ".join(parts) + ".")
    markets = _load(out, "markets.json") or {}
    btc = next((t for t in markets.get("top", []) if t.get("symbol") == "BTC"), None)
    stress = (paper.get("stress") or {}).get("level") if isinstance(paper.get("stress"), dict) else None
    fng = (markets.get("fng") or {}).get("value")
    mood = "" if fng is None else ("на рынке страх" if fng <= 30 else "на рынке жадность" if fng >= 70 else "настроение нейтральное")
    L.append(f"Рынок сегодня {'в панике — покупки закрыты, ждём откупа' if (stress or 0) >= 2 else 'нервный' if stress == 1 else 'спокойный'}"
             + (f", биткоин {spoken_price(btc['price'])}" if btc else "") + (f", {mood}" if mood else "") + ".")
    cal = _load(out, "calibration.json") or {}
    op = (cal.get("open") or {}).get("BTC")
    if op and op.get("p_up") is not None:
        up = op["p_up"] >= 0.5
        L.append(f"Базовый сценарий — {'движение к сопротивлению' if up else 'отход к поддержке'}; если подтвердится, работаем от зоны.")
    term = _load(out, "terminal.json") or {}
    sigs = sum(1 for c in term.get("coins", []) for s in c.get("setups", []) if s.get("status") in ("CALL", "READY"))
    L.append(f"Готовых входов {sigs} — все на панели." if sigs else "Готовых входов пока нет, система ждёт цену у зоны.")
    dec = _load(out, "decision.json") or {}
    imp = next((i for i in (dec.get("news") or {}).get("impacts", []) if (i.get("magnitude") or 0) >= 4 and i.get("reasoning") and not str(i["reasoning"]).startswith("mock")
                and time.time() * 1000 - (i.get("created") or 0) < 24 * 3600_000 and not _latin(i["reasoning"])), None)
    if imp:
        L.append("Из новостей важно одно: " + re.sub(r"\.$", "", imp["reasoning"]) + ".")
    L.append("Если понадоблюсь — на связи.")
    return " ".join(L)


def run(out: str = "web") -> dict:
    text = compose(out)
    os.makedirs(out, exist_ok=True)
    res = {"asof": int(time.time() * 1000), "text": text, "voice": VOICE, "audio": None}
    try:
        import asyncio
        import edge_tts

        async def synth():
            c = edge_tts.Communicate(text, VOICE, rate="-4%")
            await c.save(os.path.join(out, "brief.mp3"))
        asyncio.run(synth())
        res["audio"] = "brief.mp3"
    except Exception as e:  # noqa: BLE001
        res["error"] = str(e)[:200]
    json.dump(res, open(os.path.join(out, "brief.json"), "w", encoding="utf-8"), ensure_ascii=False)
    return res
