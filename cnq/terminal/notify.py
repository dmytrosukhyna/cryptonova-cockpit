"""Telegram notifications (server side, stdlib only).

Env: CNQ_TG_TOKEN, CNQ_TG_CHAT. Sends the daily brief and alerts on new events.
Alerts are de-duplicated with a small state file so a cron run never re-sends."""
from __future__ import annotations

import json, re
import os
import urllib.request

STATE = "data/notified.json"


def send(text: str, token: str | None = None, chat: str | None = None, parse_mode: str | None = None) -> bool:
    token = token or os.environ.get("CNQ_TG_TOKEN"); chat = chat or os.environ.get("CNQ_TG_CHAT")
    if not token or not chat:
        return False
    body = {"chat_id": chat, "text": text[:4000], "disable_web_page_preview": "<a href" not in text}   # a source link gets its preview card
    if parse_mode:
        body["parse_mode"] = parse_mode
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage",
                                 data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode()).get("ok", False)


def fit_caption(text: str, limit: int = 1024) -> str:
    """Telegram counts the caption AFTER HTML parsing (tags don't count). 21.10: the raw HTML was cut at 1024 characters, so a
    966-character evening post lost its last line («Долгосрочный тренд би…»). Keep the whole text when it fits; otherwise drop
    whole paragraphs from the end (never cut inside a tag or a word)."""
    vis = lambda t: len(re.sub(r"<[^>]+>", "", t))
    if vis(text) <= limit:
        return text
    paras = text.split("\n\n")
    while len(paras) > 1 and vis("\n\n".join(paras)) > limit:
        paras.pop()
    out = "\n\n".join(paras)
    if vis(out) > limit:                       # one huge paragraph: plain text cut at a word boundary
        plain = re.sub(r"<[^>]+>", "", out)[:limit - 1]
        out = plain[:plain.rfind(" ")] + "…"
    return out


def send_photo(path, caption: str = "", token: str | None = None, chat: str | None = None, parse_mode: str = "HTML") -> bool:
    """sendPhoto with a multipart body (stdlib only). Caption ≤ 1024 chars. A list of paths goes out as one album
    (sendMediaGroup, caption under the album); if the album fails, the first picture is sent alone."""
    token = token or os.environ.get("CNQ_TG_TOKEN"); chat = chat or os.environ.get("CNQ_TG_CHAT")
    if isinstance(path, (list, tuple)):
        paths = [p for p in path if p and os.path.exists(p)][:10]
        if len(paths) >= 2 and token and chat:
            try:
                if send_album(paths, caption, token, chat, parse_mode):
                    return True
            except Exception:  # noqa: BLE001
                pass
        path = paths[0] if paths else ""
    if not token or not chat or not path or not os.path.exists(path):
        return False
    import uuid
    boundary = "----cnq" + uuid.uuid4().hex
    fields = {"chat_id": chat, "caption": fit_caption(caption), "parse_mode": parse_mode}
    body = b""
    for k, v in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"chart.png\"\r\nContent-Type: image/png\r\n\r\n".encode()
    body += open(path, "rb").read() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendPhoto", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode()).get("ok", False)


def send_album(paths: list[str], caption: str, token: str, chat: str, parse_mode: str = "HTML") -> bool:
    import uuid
    boundary = "----cnq" + uuid.uuid4().hex
    media = [{"type": "photo", "media": f"attach://p{i}"} for i in range(len(paths))]
    media[0].update({"caption": fit_caption(caption), "parse_mode": parse_mode})
    body = b""
    for k, v in {"chat_id": chat, "media": json.dumps(media, ensure_ascii=False)}.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for i, p in enumerate(paths):
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"p{i}\"; filename=\"p{i}.png\"\r\nContent-Type: image/png\r\n\r\n".encode()
        body += open(p, "rb").read() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMediaGroup", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode()).get("ok", False)


def _fmt(p: float) -> str:
    return f"{p:,.0f}".replace(",", " ") if p >= 1000 else (f"{p:.2f}" if p >= 1 else f"{p:.4f}")


def event_text(e: dict) -> str:
    sym = e["symbol"].replace("USDT", "")
    if e["kind"] == "SETUP":
        st = {"ATTENTION": "следить", "WAIT_CONFIRMATION": "готовиться", "READY": "готов", "CALL": "СИГНАЛ", "POSITION_OPEN": "в позиции"}.get(e["status"], e["status"])
        risk = abs(e["entry"] - e["stop"]) / e["entry"] * 100
        return (f"⚡ {sym} {e['family']} {e['direction']} — {st}\nВход {_fmt(e['entry'])} · стоп {_fmt(e['stop'])} · риск {risk:.2f}%\n"
                f"Лимитка на краю зоны, одна цель 1–1.5R, только мейкер.")
    if e["kind"] == "LIQUIDATION_CASCADE":
        side = "вынос лонгов" if e["side"] == "LONG_LIQUIDATIONS" else "вынос шортов"
        return (f"💥 {sym}: {side} {e['ret_pct']}% за 15 мин, ликвидации ${e['liq_usd']/1e6:.1f}M. "
                f"Хвост {'есть — кандидат на fade' if e['wick_ok'] else 'слабый — не трогать'}. Диапазон {_fmt(e['low'])}–{_fmt(e['high'])}.")
    if e["kind"] == "FUNDING_EXTREME":
        return (f"📊 {sym}: funding {e['funding_pct']:+.4f}% — {round(e['rank']*100)}-й перцентиль за 90 дней. "
                f"{'Лонги переполнены, риск сквиза вниз.' if e['rank'] >= 0.5 else 'Шорты переполнены, риск сквиза вверх.'}")
    return json.dumps(e, ensure_ascii=False)


def alert_new_events(report: dict, token: str | None = None, chat: str | None = None) -> int:
    """Send every event not yet notified. Returns number sent."""
    try:
        seen = set(json.load(open(STATE)))
    except Exception:  # noqa: BLE001
        seen = set()
    sent = 0
    for e in report.get("events", []):
        key = f"{e['kind']}|{e['symbol']}|{e['time']}|{e.get('status','')}"
        if key in seen:
            continue
        if e["kind"] == "SETUP" and e["status"] not in ("CALL", "READY", "WAIT_CONFIRMATION"):
            continue
        if send(event_text(e), token, chat):
            seen.add(key); sent += 1
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(sorted(seen)[-2000:], open(STATE, "w"))
    return sent
