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
RETRY_PAUSE_S = (2, 5, 9)          # after a refused attempt of a voice; 10.11: 3 attempts a voice (Dmitry, then Svetlana) before English
NAME = os.environ.get("CNQ_OWNER_NAME") or "Дмитрий"
# 30.10 (owner, build 51: «голос говорит достаточно медленно — хотелось бы быстрее»): the one tempo of every spoken text — the brief,
# NOVA AI answers and the app's report (cnq/api/tts.py RATE) and the app's bundled recordings (CryptoNova scripts/app_voice.py)
try:
    from cnq.api.tts import RATE
except Exception:  # noqa: BLE001
    RATE = "+6%"                   # 04.11 (build 54): one middle tempo


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


def nova_result(paper: dict, out: str = "web") -> tuple[float | None, str]:
    """CRYPTO NOVA AI futures as the panel shows it (novaRes): the desks of the mix averaged into one $10 000 account, each desk counted since its own
    start (equity + open P&L − start), dated by the FIRST desk's start. Returns (result in $, «dd.mm»)."""
    pr = paper.get("profiles") or {}
    from . import panel_header as ph
    try:
        from .capital import fut_keys
        keys = fut_keys(out)            # 28.10: the main desk the selector chose (web/desk_select.json) + imp, + own after its gate
    except Exception:  # noqa: BLE001
        keys = ph.nova_base(ph.load_sel(out))
    res, base, first = 0.0, 0.0, None
    for k in keys:
        d = pr.get(k) or {}
        if d.get("equity") is None:
            continue
        st = float(d.get("start_equity") or 10_000)
        res += float(d["equity"]) + sum(o.get("open_pnl_usd") or 0 for o in d.get("open") or []) - st
        base += st
        h = d.get("equity_history") or []
        if h and h[0].get("time") and (first is None or h[0]["time"] < first):
            first = h[0]["time"]
    if not base:
        return None, ""
    since = (dt.datetime.utcfromtimestamp(first / 1000) + dt.timedelta(hours=3)).strftime("%d.%m") if first else ""
    return res / base * 10_000, since


def spoken(text: str) -> str:
    """What the neural voice reads: the Latin brand is read badly («Новa» / letters), so it gets the Cyrillic name with the stress mark."""
    try:                                       # build 53 (owner 04.11): «с 22.10» is said «с 22 октября», never «с 22,1»
        from ..api.tts import say_dates
        text = say_dates(text, "ru")
    except Exception:  # noqa: BLE001
        pass
    return text.replace("CRYPTO NOVA AI", "Крипто Но́ва Эй-Ай").replace("CRYPTO NOVA", "Крипто Но́ва")


def real_line(out: str = "web") -> str | None:
    """Final QA 28.10 (owner): the brief starts with the REAL account — the public portfolio, CRYPTO NOVA AI's own OKX account since
    22.10 (web/public_real.json, the autopilot's sub-account only): its value, its result since the start, its futures. None without
    the file."""
    r = _load(out, "public_real.json") or {}
    if r.get("equity_usd") is None or r.get("since") is None:
        return None
    since = (dt.datetime.utcfromtimestamp(int(r["since"]) / 1000) + dt.timedelta(hours=3)).strftime("%d.%m")
    pnl = float(r.get("pnl_usd") or 0)
    n_open = len([x for x in r.get("futures") or [] if isinstance(x, dict)])
    fut = f"в работе {deals(n_open)}" if n_open else "открытых сделок нет"
    if int(r.get("closed_n") or 0):
        fut += f"; закрыто с начала: {int(r['closed_n'])}"
    if r.get("fut_pnl_usd") is not None:
        fp = float(r["fut_pnl_usd"])
        fut += "; итог около нуля" if abs(fp) < 5 else f"; итог — {result(fp)}"
    stale = " Данные счёта могли устареть." if r.get("stale") else ""
    return (f"Реальный счёт CRYPTO NOVA AI на OKX — {usd(float(r['equity_usd']))}: с {since} "
            f"{'около нуля' if abs(pnl) < 5 else result(pnl)}. Фьючерсы реального счёта: {fut}.{stale}")


def whales_line(out: str) -> str | None:
    """05.11: the «Киты» desk in spoken words — how many traders we copy, open copies, the day's closed copies; the demo copy desk
    on $10 000 as a reference. No Chinese nicknames (the Russian voice cannot read them)."""
    from . import whale_digest as wd
    fol = wd.following(out)
    if not fol:
        return None
    c = wd.copies(out)
    oc, cc = c["open_copies"], c["copies_closed"]
    # 05.11 (owner 09:52): spoken words say «система CRYPTO NOVA AI», never the method (whales, copies, traders)
    s = "Фьючерсы ведёт система CRYPTO NOVA AI" + ("" if oc else ", открытых сделок сейчас нет")
    if cc:
        s += f"; за сутки закрыли {deals(len(cc), acc=True)}, итог {'около нуля' if abs(c['copies_usd']) < 5 else result(c['copies_usd'])}"
    s += "."
    d = wd.desk(out)
    if d:
        s += f" Демо-счёт системы на 10 тысяч долларов: {'около нуля' if abs(d['pnl_usd']) < 5 else result(d['pnl_usd'])}."
    return s


def compose(out: str = "web") -> str:
    """Executive report in plain words: the real account first (final QA 28.10), then the demo desks — labelled demo, never as
    CRYPTO NOVA AI's result — how many trades are open, market direction."""
    hour = (dt.datetime.utcnow() + dt.timedelta(hours=3)).hour
    hello = "Доброй ночи" if hour < 5 else "Доброе утро" if hour < 12 else "Добрый день" if hour < 18 else "Добрый вечер"
    L = [f"{hello}, {NAME}. Система работает штатно."]
    try:
        rl = real_line(out)
    except Exception:  # noqa: BLE001 — the brief never fails because of one file
        rl = None
    if rl:
        L.append(rl)
    paper = _load(out, "paper.json") or {}
    from . import panel_header as ph
    prof = (paper.get("profiles") or {}).get(ph.main_desk(ph.load_sel(out))) or paper      # 28.10: the main desk (panel: hdrVals P)
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
            parts.append("открытых демо-сделок нет")
        # 18.10: our futures result = CRYPTO NOVA = the desks in the mix (CNPTS «Сбалансированная» + «Импульс BTC»); own signals join after
        # their live gate. 21.10: the panel shows the mix as ONE $10 000 account — the AVERAGE of its desks (novaRes) — and the brief must say
        # the same number, or the panel refuses the neural audio and falls back to the robotic browser voice (owner: «голос робота снова»)
        fut_res, since = nova_result(paper, out)
        # 05.11 (owner: the whole real futures budget copies the top-5 whales since 04.11): the retired rule desks are no longer
        # spoken — their numbers stay in the keys only, so the panel still recognises this brief as matching its screen
    try:
        wl = whales_line(out)
    except Exception:  # noqa: BLE001
        wl = None
    if wl:
        L.append(wl)
    spot_res = None
    pf = _load(out, "portfolio.json") or {}
    # 18.10 (owner): the header and the brief show OUR strategies (CNPTS «Сбалансированная» + the AI portfolio), not the leader
    sp_key = "ideas" if (pf.get("portfolios") or {}).get("ideas") else "mod"
    sp = (pf.get("portfolios") or {}).get(sp_key) or pf.get("portfolio") or {}
    if sp.get("equity_usd") is not None and sp.get("start_usd"):
        spot_res = float(sp["equity_usd"]) - float(sp["start_usd"])
    elif sp.get("index"):
        spot_res = 10_000 * sp["index"] / 100 - 10_000
    L.append("Спот — это ядро: биткоин, эфир и солана, держим в долгую.")
    # every desk counts in «Заработано всего»: funding carry (real demo), arbitrage and triangle (virtual estimates)
    extra = {}
    fd = _load(out, "funding.json") or {}
    if fd.get("start_equity"):
        extra["funding"] = float(fd.get("equity_mtm") if fd.get("equity_mtm") is not None else fd.get("equity", fd["start_equity"])) - float(fd["start_equity"])
    for k in ("arb", "tri"):
        w = ((_load(out, f"{k}.json") or {}).get("week") or {}).get("virtual_pnl_usd")
        if w is not None:
            extra[k] = float(w)
    # 19.10 (owner: «всё простым языком»): «Заработано всего» = futures + spot, exactly the two accounts of the panel's «Доход онлайн»;
    # funding, arbitrage and triangle are on probation (tab «Заработок») and are not in the total — so they are not in the keys either
    tot = fut_res + spot_res if fut_res is not None and spot_res is not None else None
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
    if hour >= 18:                         # 09.11 (owner 21:40): the evening digest names what changed on ADA / AVAX / LIT
        try:
            from . import owner_watch
            ow = owner_watch.owner_line(out)
        except Exception:  # noqa: BLE001
            ow = None
        if ow:
            L.append(ow)
    L.append("Если понадоблюсь — на связи.")
    # the same shape as the panel's briefKeys(): fut 'nova', spot 'ideas', open trades of «Сбалансированная», total = futures + spot
    compose.keys = {"fut": "nova" if fut_res is not None else "bal", "spot": sp_key, "fut_open": len(prof.get("open") or []) if prof else None,
                    "total": round(tot) if tot is not None else None, "mode": "desks",
                    "parts": {"fut": round(fut_res) if fut_res is not None else None, "spot": round(spot_res) if spot_res is not None else None},
                    "btc": btc.get("price") if btc else None, "probation": {k: round(v, 2) for k, v in extra.items()}}
    return " ".join(L)


def azure_to(text: str, path: str, errs: list) -> str | None:
    """the brief / morning text spoken by Azure Speech in Dmitry's voice into path; the voice name, or None (no key / it failed)"""
    try:
        from ..api import tts
        if not tts.azure_cfg():
            return None
        data = tts.azure_mp3(spoken(text), VOICE)
        with open(path + ".az", "wb") as f:
            f.write(data)
        os.replace(path + ".az", path)
        return "azure:" + VOICE
    except Exception as e:  # noqa: BLE001
        errs.append(str(e)[:80])
        return None


def brief_voices() -> list[str]:
    """the voices tried for the brief, in order: Dmitry, then the API's Russian fallbacks (cnq/api/tts.py FALLBACK["ru"]: 29.10
    owner 15:42 — a good Microsoft NEURAL voice of either gender is fine, delays are worse; Dmitry is refused most of the time).
    Only Microsoft neural voices — never gTTS or the browser's robotic voice"""
    try:
        from ..api.tts import FALLBACK
        fbs = list(FALLBACK.get("ru") or [])
    except Exception:  # noqa: BLE001
        fbs = ["en-US-BrianMultilingualNeural"]
    return [VOICE] + [v for v in fbs if v and v != VOICE]


def run(out: str = "web") -> dict:
    text = compose(out)
    os.makedirs(out, exist_ok=True)
    res = {"asof": int(time.time() * 1000), "text": text, "voice": None, "audio": None, "keys": getattr(compose, "keys", None)}
    # edge-tts sometimes answers «No audio was received» → retry, then the next neural voice (brief_voices: Dmitry, then the API's
    # Russian fallbacks; Google's gTTS is gone). The mp3 is written to a temp file and moved in only when a voice spoke, so a failure
    # leaves the previous brief.mp3 untouched; brief.json then has the new text with audio null — the panel never plays an mp3 whose
    # numbers differ from the text it shows.
    errs = []
    path = os.path.join(out, "brief.mp3")
    tmp = path + ".tmp"
    v = azure_to(text, path, errs)                       # 10.11: Azure Speech first when its key is set
    if v:
        res["audio"], res["voice"] = "brief.mp3", v
    try:
        import asyncio
        import edge_tts
        for voice in ([] if res["audio"] else brief_voices()):
            for attempt in range(len(RETRY_PAUSE_S)):
                try:
                    async def synth(v=voice):
                        c = edge_tts.Communicate(spoken(text), v, rate=RATE)    # 04.11: the API's +6 % (owner: one middle tempo); 30.10: +12 %
                        await c.save(tmp)
                    asyncio.run(synth())
                    if os.path.getsize(tmp) > 2000:
                        os.replace(tmp, path)
                        res["audio"], res["voice"] = "brief.mp3", voice
                        break
                    errs.append(f"{voice}#{attempt + 1}: short audio")
                except Exception as e:  # noqa: BLE001
                    errs.append(f"{voice}#{attempt + 1}: {str(e)[:80]}")
                    time.sleep(RETRY_PAUSE_S[attempt])
            if res["audio"]:
                break
    except Exception as e:  # noqa: BLE001
        errs.append(str(e)[:120])
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    if errs:
        res["error"] = " | ".join(errs)[:300]
    json.dump(res, open(os.path.join(out, "brief.json"), "w", encoding="utf-8"), ensure_ascii=False)
    return res
