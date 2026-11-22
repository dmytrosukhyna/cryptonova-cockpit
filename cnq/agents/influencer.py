"""Public Telegram channels of traders the owner wants checked (22.11.2026 22:36) — reading, parsing calls, scoring. PAPER / RESEARCH.
The channel handle is never written into the repository or a public feed: the research run gets it as a run input, the server from
its owner-only data dir (<run>/data/influencers/channels.json). Stdlib only.

t.me/s/<handle> is Telegram's public web preview: ~20 posts a page, «?before=<id>» pages back. A post = {id, t (ms), text}.
A CALL is a post that names a coin and a buy / sell intent NOW («покупаю», «набираю», «вход», «беру», «лонг» …) without a result
already reached; a RECAP is a post that shows an earlier entry against the price now («вход был 0.09, сейчас 0.24», «уже x2», «+150 %
от входа») — recaps are not calls (they are counted separately, with the entry they claim, to look for calls we never saw)."""
from __future__ import annotations

import html
import re
import time

BUY = re.compile(r"(покупа|купил|куплю|набира|набрал|добира|добрал|докуп|закупа|закуп|беру|взял|входим|захожу|зашёл|зашел|вход по|"
                 r"вход\b|точка входа|открыл лонг|лонг\b|long\b|в портфель|добавил|accumulat|buy\b|entry\b)", re.I)
SELL = re.compile(r"(шорт|short\b|продаю|продал|фиксирую|зафиксировал|закрыл|выхожу|вышел)", re.I)
RECAP = re.compile(r"(сейчас\s*[~≈]?\s*\$?\d|уже\s*[~≈]?\s*\$?\d|от входа|от точки|дал[аио]?\s|отработ|как и писал|напомина|"
                   r"\+\s?\d{2,4}\s?%|\d+(?:[.,]\d+)?\s?[xхXХ]\b|[xхXХ]\s?\d\b|иксы|результат)", re.I)
TARGET = re.compile(r"(?:цел[ьи]|таргет|target|тейк\w*|тп\d?|tp\d?|take\s?profit|фиксаци\w*\s+на|ожидаю|жду|до)\s*[:\-–—]?\s*\$?(\d+(?:[.,]\d+)?)(?!\d)(?!\s?[%xхXХ×])"
                    r"|(?:\b|^)(\d(?:[.,]\d)?)\s?[xхXХ×]\b|[xхXХ×]\s?(\d(?:[.,]\d)?)\b", re.I)
STOP = re.compile(r"стоп\s*[:\-–]?\s*\$?(\d+(?:[.,]\d+)?)", re.I)
PRICE = re.compile(r"(?:по|at|@|вход\w*|цен\w*)\s*\$?(\d+(?:[.,]\d+)?)", re.I)
HORIZON = re.compile(r"(долгосрок|среднесрок|краткосрок|на год|на месяц|на неделю|до конца года|в цикл|холд|hold)", re.I)
TICK = re.compile(r"(?:\$|#)?\b([A-Z][A-Z0-9]{1,9})\b")
NOT_COINS = {"BTC.D", "USD", "USDT", "USDC", "ETF", "SEC", "CEO", "NFT", "ATH", "ATL", "TA", "OI", "RSI", "EMA", "MA", "TP", "SL",
             "DCA", "PNL", "ROI", "APR", "APY", "TVL", "AI", "DEX", "CEX", "US", "USA", "EU", "UTC", "AMA", "P3", "IPO", "CPI", "FOMC",
             "FED", "GDP", "X", "OK", "TG", "VIP", "PRO", "TOP", "NEW"}


def parse_page(page: str) -> list:
    """posts of one t.me/s/ page: [{id, t, text}] oldest first"""
    out = []
    for m in re.finditer(r'data-post="[^"/]+/(\d+)"(.*?)(?=data-post="|\Z)', page, re.S):
        pid, body = int(m.group(1)), m.group(2)
        tm = re.search(r'<time[^>]*datetime="([^"]+)"', body)
        tx = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', body, re.S)
        text = ""
        if tx:
            text = re.sub(r"<br\s*/?>", "\n", tx.group(1))
            text = html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
        t = None
        if tm:
            try:
                s = tm.group(1).replace("Z", "+00:00")
                from datetime import datetime
                t = int(datetime.fromisoformat(s).timestamp() * 1000)
            except ValueError:
                t = None
        out.append({"id": pid, "t": t, "text": text})
    return sorted(out, key=lambda p: p["id"])


def _num(s):
    try:
        return float(str(s).replace(",", "."))
    except ValueError:
        return None


def classify(post: dict, coins: set) -> list:
    """→ [{kind: call | recap, coin, side, entry?, targets_x, targets_px, stop?, horizon?}] (a post may name several coins)"""
    text = post.get("text") or ""
    if not text:
        return []
    found = []
    for m in TICK.finditer(text):
        c = m.group(1).upper()
        if c in coins and c not in NOT_COINS and c not in found:
            found.append(c)
    if not found:
        return []
    recap = bool(RECAP.search(text))
    buy, sell = bool(BUY.search(text)), bool(SELL.search(text))
    if not (buy or sell or recap):
        return []
    side = "SHORT" if re.search(r"шорт|short\b", text, re.I) else "LONG"
    tx, tp = [], []
    for m in TARGET.finditer(text):
        if m.group(1):
            v = _num(m.group(1))
            if v:
                tp.append(v)
        else:
            v = _num(m.group(2) or m.group(3))
            if v and 1.1 <= v <= 20:
                tx.append(v)
    for m in re.finditer(r"цел[ьи]\s*[:\-–—]?\s*((?:\$?\d+(?:[.,]\d+)?\s*[/;,]\s*)+\$?\d+(?:[.,]\d+)?)", text, re.I):   # «цели: 120 / 150»
        tp += [v for v in (_num(x) for x in re.split(r"\s*[/;,]\s*", m.group(1).replace("$", ""))) if v]
    st = STOP.search(text)
    pr = PRICE.search(text)
    hz = HORIZON.search(text)
    kind = "recap" if recap and not re.search(r"(покупаю|набираю|добираю|беру|захожу|входим|открываю)", text, re.I) else ("call" if (buy or sell) else "recap")
    if sell and not buy and side == "LONG" and kind == "call":
        kind = "exit"
    return [{"kind": kind, "coin": c, "side": side, "entry": _num(pr.group(1)) if pr else None, "targets_x": sorted(set(tx)),
             "targets_px": sorted(set(tp)), "stop": _num(st.group(1)) if st else None, "horizon": hz.group(1).lower() if hz else None}
            for c in found[:5]]


def fetch_channel(handle: str, get, max_pages: int = 80, stop_before_ms: int | None = None, sleep=time.sleep) -> list:
    """every post we can page back to (newest page first, then ?before=<oldest id>); get(url) → html text"""
    posts, before = {}, None
    for _ in range(max_pages):
        url = f"https://t.me/s/{handle}" + (f"?before={before}" if before else "")
        try:
            page = get(url)
        except Exception:  # noqa: BLE001
            break
        got = parse_page(page or "")
        if not got:
            break
        new = [p for p in got if p["id"] not in posts]
        for p in got:
            posts[p["id"]] = p
        oldest = min(p["id"] for p in got)
        if not new or (before is not None and oldest >= before):
            break
        before = oldest
        if stop_before_ms and any(p["t"] and p["t"] < stop_before_ms for p in got):
            break
        sleep(1.0)
    return sorted(posts.values(), key=lambda p: p["id"])
