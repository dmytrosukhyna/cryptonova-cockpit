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


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n)); return one if n % 10 == 1 and n % 100 != 11 else few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def deals(n: int, acc: bool = False) -> str:
    """'одна сделка' / 'две сделки' / '5 сделок' (acc=True → 'одну сделку')."""
    w = {1: "одну" if acc else "одна", 2: "две", 3: "три", 4: "четыре"}.get(n, str(n))
    return f"{w} {plural(n, 'сделку' if acc else 'сделка', 'сделки', 'сделок')}"


def usd(x: float) -> str:
    v = round(abs(x)); return f"{v} {plural(v, 'доллар', 'доллара', 'долларов')}"


def result(x: float) -> str:
    return "в нуле" if abs(x) < 5 else (f"в плюсе на {usd(x)}" if x > 0 else f"в минусе на {usd(x)}")


def compose(out: str = "web") -> str:
    """Executive report in plain words: what we closed, where we stand, how many trades are open, market direction."""
    hour = (dt.datetime.utcnow() + dt.timedelta(hours=3)).hour
    hello = "Доброй ночи" if hour < 5 else "Доброе утро" if hour < 12 else "Добрый день" if hour < 18 else "Добрый вечер"
    L = [f"{hello}, {NAME}. Система работает штатно."]
    paper = _load(out, "paper.json") or {}
    prof = (paper.get("profiles") or {}).get("bal") or paper
    fut_res = None
    if prof:
        opened = prof.get("open") or []
        closed = [t for t in (prof.get("closed") or []) if (t.get("closed") or 0) > time.time() * 1000 - 86400_000]
        day = sum(t.get("net_usd", 0) for t in closed)
        parts = []
        if closed:
            parts.append(f"за сутки закрыли {deals(len(closed), acc=True)}, итог {result(day)}")
        n = len(opened)
        if n:
            upnl = sum(o.get("open_pnl_usd") or 0 for o in opened)
            parts.append(f"в работе {deals(n)}, {'она' if n == 1 else 'они'} сейчас {result(upnl)}")
        else:
            parts.append("открытых сделок нет — автопилот ждёт свой сигнал")
        eq = prof.get("equity")
        if eq:
            fut_res = eq + sum(o.get("open_pnl_usd") or 0 for o in opened) - 10_000
            # 18.10: our futures result = CRYPTO NOVA = the desks in the mix (CNPTS «Сбалансированная» + «Импульс BTC») + the app's
            # selected signals («умный разворот», live part)
            imp = (paper.get("profiles") or {}).get("imp") or {}
            if imp.get("equity") is not None:
                fut_res += imp["equity"] + sum(o.get("open_pnl_usd") or 0 for o in imp.get("open") or []) - 10_000
            sm = (((_load(out, "app_signals.json") or {}).get("accounts") or {}).get("since_live") or {}).get("smart") or {}
            if sm.get("equity") is not None:
                fut_res += sm.get("equity_with_open", sm["equity"]) - 10_000
        L.append("По фьючерсам, CRYPTO NOVA: " + "; ".join(parts) + "."
                 + (f" Итог {result(fut_res)}." if fut_res is not None else ""))
    spot_res = None
    pf = _load(out, "portfolio.json") or {}
    # 18.10 (owner): the header and the brief show OUR strategies (CNPTS «Сбалансированная» + the AI portfolio), not the leader
    sp_key = "ideas" if (pf.get("portfolios") or {}).get("ideas") else "mod"
    sp = (pf.get("portfolios") or {}).get(sp_key) or pf.get("portfolio") or {}
    if sp.get("equity_usd") is not None and sp.get("start_usd"):
        spot_res = float(sp["equity_usd"]) - float(sp["start_usd"])
    elif sp.get("index"):
        spot_res = 10_000 * sp["index"] / 100 - 10_000
    if spot_res is not None:
        nm = "портфель CRYPTO NOVA" if sp_key == "ideas" else (sp.get("name_short") or "Умеренный")
        L.append(f"По споту, {nm}: {'в плюсе на ' + usd(spot_res) if spot_res > 5 else 'в минусе на ' + usd(spot_res) if spot_res < -5 else 'около нуля'}.")
    # every desk counts in «Заработано всего»: funding carry (real demo), arbitrage and triangle (virtual estimates)
    extra = {}
    fd = _load(out, "funding.json") or {}
    if fd.get("start_equity"):
        extra["funding"] = float(fd.get("equity_mtm") if fd.get("equity_mtm") is not None else fd.get("equity", fd["start_equity"])) - float(fd["start_equity"])
    for k in ("arb", "tri"):
        w = ((_load(out, f"{k}.json") or {}).get("week") or {}).get("virtual_pnl_usd")
        if w is not None:
            extra[k] = float(w)
    if fut_res is not None and spot_res is not None:
        tot = fut_res + spot_res + sum(extra.values())
        L.append(f"Заработано всего — {'около нуля' if abs(tot) < 5 else ('плюс ' if tot >= 0 else 'минус ') + usd(tot)}"
                 + (", вместе с фандингом и арбитражем." if extra else "."))
    markets = _load(out, "markets.json") or {}
    btc = next((t for t in markets.get("top", []) if t.get("symbol") == "BTC"), None)
    stress = (paper.get("stress") or {}).get("level") if isinstance(paper.get("stress"), dict) else None
    if btc:
        ch = btc.get("chg24") or 0
        trend = "растёт" if ch >= 0.5 else "снижается" if ch <= -0.5 else "почти не изменился"
        L.append(f"{'Рынок в панике. ' if (stress or 0) >= 2 else ''}Биткоин {spoken_price(btc['price'])}, за сутки {trend}"
                 + (f" ({'+' if ch > 0 else '−'}{abs(ch):.1f}%)".replace(".", ",") if abs(ch) >= 0.1 else "") + ".")
    dec = _load(out, "decision.json") or {}
    imp = next((i for i in (dec.get("news") or {}).get("impacts", []) if (i.get("magnitude") or 0) >= 4 and i.get("reasoning") and not str(i["reasoning"]).startswith("mock")
                and time.time() * 1000 - (i.get("created") or 0) < 24 * 3600_000 and not _latin(i["reasoning"])), None)
    if imp:
        L.append("Из новостей стоит отметить: " + re.sub(r"\.$", "", imp.get("title") or imp["reasoning"]) + ".")
    L.append("Если понадоблюсь — на связи.")
    compose.keys = {"fut": "cryptonova", "spot": sp_key, "fut_open": len(prof.get("open") or []) if prof else None,
                    "total": round((fut_res or 0) + (spot_res or 0) + sum(extra.values())) if fut_res is not None and spot_res is not None else None,
                    "parts": {"fut": round(fut_res) if fut_res is not None else None, "spot": round(spot_res) if spot_res is not None else None,
                              **{k: round(v, 2) for k, v in extra.items()}},
                    "btc": btc.get("price") if btc else None}
    return " ".join(L)


def run(out: str = "web") -> dict:
    text = compose(out)
    os.makedirs(out, exist_ok=True)
    res = {"asof": int(time.time() * 1000), "text": text, "voice": VOICE, "audio": None, "keys": getattr(compose, "keys", None)}
    # edge-tts sometimes answers «No audio was received» → retry, then the second Microsoft voice, then Google (gTTS).
    # Without audio the panel falls back to the browser voice, which sounds robotic — so try hard.
    errs = []
    path = os.path.join(out, "brief.mp3")
    try:
        import asyncio
        import edge_tts
        for voice in (VOICE, "ru-RU-SvetlanaNeural"):
            for attempt in range(3):
                try:
                    async def synth(v=voice):
                        c = edge_tts.Communicate(text, v, rate="-4%")
                        await c.save(path)
                    asyncio.run(synth())
                    if os.path.getsize(path) > 2000:
                        res["audio"], res["voice"] = "brief.mp3", voice
                        break
                except Exception as e:  # noqa: BLE001
                    errs.append(f"{voice}#{attempt + 1}: {str(e)[:80]}")
                    time.sleep(2 + 3 * attempt)
            if res["audio"]:
                break
    except Exception as e:  # noqa: BLE001
        errs.append(str(e)[:120])
    if not res["audio"]:
        try:
            from gtts import gTTS
            gTTS(text, lang="ru").save(path)
            res["audio"], res["voice"] = "brief.mp3", "google-ru"
        except Exception as e:  # noqa: BLE001
            errs.append("gtts: " + str(e)[:80])
    if errs:
        res["error"] = " | ".join(errs)[:300]
    json.dump(res, open(os.path.join(out, "brief.json"), "w", encoding="utf-8"), ensure_ascii=False)
    return res
