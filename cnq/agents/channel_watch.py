"""Проверка каналов — a live, honest record of a public Telegram trader's forecasts (18.10.2026, owner: «этот трейдер за полгода
ни разу не ошибался — подключиться к его группе, анализировать его информацию, учитывать»).

Why live: the 200-day audit of t.me/s/Mr_Trader_Pro (scripts/channel_audit.py, 18.10) found ~20 posts a day, 147 posts that look
like calls but 0 % with a stop and 1 % with targets, gains mostly claimed after the fact («купил по 72 — сейчас 97»), 157 ads, and
771 gaps in post numbers (deleted posts or albums — can't tell). A format without stops can't be «wrong», so the only fair test
is to write every forecast down WHEN it is posted and check it later against the market.

Every server run (≈ 30 min): read the channel's public web preview (no login), take posts not seen before, and pull out simple
directional forecasts — a coin (#TAG / ticker, or «альты») + a direction word (рост / вверх / лонг / откуп … or падение / вниз /
шорт / коррекция / откат …; both directions in one post → skipped as ambiguous). The price at that moment comes from this run's
markets.json (CoinGecko). After the horizon (1 day if the post says «сегодня / локально / ближайшие часы», else 3 days) the
forecast is resolved: a move ≥ 0.5 % in the stated direction = сбылся, ≥ 0.5 % against = не сбылся, else ≈ на месте.
«альты» = the average of ETH, SOL, XRP, DOGE, ADA. Baseline: «всегда вверх» over the same windows — a forecaster is worth
something only above it. Stored: coin, direction, times, prices, the result and the post number (a link) — NOT the post text.
Output web/channel_watch.json. Stdlib only, fetchers injectable for tests."""
from __future__ import annotations

import html
import json
import os
import re
import sqlite3
import time
import urllib.request

CHANNELS = {"Mr_Trader_Pro": "Mr. Trader Pro"}
UP = r"(рост|вырастет|вверх|лонг|long|откуп|бычь|пробой вверх|разворот вверх|ракет|памп|к хаям|новые хаи|покупаю|покупка|набираю)"
DOWN = r"(падени|упасть|упадёт|упадет|вниз|шорт|short|коррекц|откат|медвеж|снижени|пролив|слив|дамп|к лоям|продаю)"
SHORT_H = r"(сегодня|локальн|ближайшие час|в течение дня|интрадей|до конца дня)"
ALTS = ("ETH", "SOL", "XRP", "DOGE", "ADA")
EPS = 0.005
H = 3_600_000


def fetch_posts(channel: str, timeout: int = 20) -> list[dict]:
    req = urllib.request.Request(f"https://t.me/s/{channel}", headers={"User-Agent": "Mozilla/5.0 cryptonova/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        b = r.read().decode("utf-8", "replace")
    out = []
    for bl in re.split(r'<div class="tgme_widget_message_wrap', b)[1:]:
        m = re.search(r'data-post="[^"/]+/(\d+)"', bl)
        t = re.search(r'<time datetime="([^"]+)"', bl)
        if not m or not t:
            continue
        tx = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', bl, re.S)
        text = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<br\s*/?>", "\n", tx.group(1)))) if tx else ""
        out.append({"id": int(m.group(1)), "text": text})
    return out


def forecasts(text: str, coins: set) -> list[dict]:
    low = text.lower()
    up, down = bool(re.search(UP, low)), bool(re.search(DOWN, low))
    if up == down:                       # no direction, or both → ambiguous
        return []
    side = "up" if up else "down"
    tags = [c.upper() for c in re.findall(r"#([A-Za-z0-9]{2,12})", text)]
    tags += [w for w in re.findall(r"\b([A-Z]{2,10})\b", text)]
    syms = [s for s in dict.fromkeys(tags) if s in coins and s not in ("USDT", "USDC", "USD")]
    if not syms:
        if re.search(r"\b(биткоин|битко|btc|бтс|битка)", low):
            syms = ["BTC"]
        elif re.search(r"\b(эфир|эфириум|eth)\b", low):
            syms = ["ETH"]
        elif re.search(r"(альт|альткоин)", low):
            syms = ["ALTS"]
    horizon = 24 if re.search(SHORT_H, low) else 72
    return [{"coin": s, "side": side, "horizon_h": horizon} for s in syms[:3]]


def _basket(markets: dict) -> dict:
    return {r.get("symbol"): float(r["price"]) for r in (markets or {}).get("top") or [] if r.get("symbol") in ALTS and r.get("price")}


def step(conn: sqlite3.Connection, markets: dict, now: int | None = None, fetch=fetch_posts) -> dict:
    now = now or int(time.time() * 1000)
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS chw_seen(channel TEXT, post INTEGER, PRIMARY KEY(channel, post));
      CREATE TABLE IF NOT EXISTS chw_fc(channel TEXT, post INTEGER, coin TEXT, side TEXT, t INTEGER, horizon_h INTEGER,
        px REAL, basket TEXT, due INTEGER, px_end REAL, move REAL, result TEXT, PRIMARY KEY(channel, post, coin));""")
    coins = {r.get("symbol") for r in (markets or {}).get("top") or [] if r.get("symbol")}
    px_now = {r.get("symbol"): float(r["price"]) for r in (markets or {}).get("top") or [] if r.get("symbol") and r.get("price")}
    bask_now = _basket(markets)
    out, errors = {}, {}
    for ch, title in CHANNELS.items():
        try:
            posts = fetch(ch)
        except Exception as e:  # noqa: BLE001
            posts, errors[ch] = [], f"{type(e).__name__}: {str(e)[:80]}"
        first = conn.execute("SELECT COUNT(*) FROM chw_seen WHERE channel=?", (ch,)).fetchone()[0] == 0
        for p in posts:
            if conn.execute("SELECT 1 FROM chw_seen WHERE channel=? AND post=?", (ch, p["id"])).fetchone():
                continue
            conn.execute("INSERT INTO chw_seen VALUES(?,?)", (ch, p["id"]))
            if first:                    # the first run only remembers the page — older posts were not seen in time
                continue
            for f in forecasts(p["text"], coins):
                if f["coin"] == "ALTS":
                    if len(bask_now) < 3:
                        continue
                    px, bk = None, json.dumps(bask_now)
                else:
                    px, bk = px_now.get(f["coin"]), None
                    if not px:
                        continue
                conn.execute("INSERT OR IGNORE INTO chw_fc VALUES(?,?,?,?,?,?,?,?,?,NULL,NULL,NULL)",
                             (ch, p["id"], f["coin"], f["side"], now, f["horizon_h"], px, bk, now + f["horizon_h"] * H))
        # resolve
        for post, coin, side, t, hz, px, bk in conn.execute(
                "SELECT post, coin, side, t, horizon_h, px, basket FROM chw_fc WHERE channel=? AND result IS NULL AND due<=?", (ch, now)).fetchall():
            if coin == "ALTS":
                b0 = json.loads(bk)
                common = [s for s in b0 if s in bask_now]
                if len(common) < 3:
                    continue
                mv = sum(bask_now[s] / b0[s] - 1 for s in common) / len(common)
                end = None
            else:
                end = px_now.get(coin)
                if not end:
                    continue
                mv = end / px - 1
            d = mv if side == "up" else -mv
            res = "hit" if d >= EPS else "miss" if d <= -EPS else "flat"
            conn.execute("UPDATE chw_fc SET px_end=?, move=?, result=? WHERE channel=? AND post=? AND coin=?", (end, mv, res, ch, post, coin))
        conn.commit()
        rows = conn.execute("SELECT post, coin, side, t, horizon_h, move, result FROM chw_fc WHERE channel=? ORDER BY t DESC", (ch,)).fetchall()
        done = [r for r in rows if r[6]]
        n = len(done)
        hits = sum(1 for r in done if r[6] == "hit")
        miss = sum(1 for r in done if r[6] == "miss")
        up_base = sum(1 for r in done if (r[5] or 0) >= EPS)            # «always up» on the same windows
        dirs = [(r[5] if r[2] == "up" else -r[5]) for r in done if r[5] is not None]
        out[ch] = {"title": title, "link": f"https://t.me/{ch}", "forecasts": n, "open": len(rows) - n, "hits": hits, "misses": miss,
                   "flat": n - hits - miss, "hit_rate_pct": round(hits / n * 100) if n else None,
                   "always_up_pct": round(up_base / n * 100) if n else None,
                   "avg_move_in_direction_pct": round(sum(dirs) / len(dirs) * 100, 2) if dirs else None,
                   "recent": [{"post": r[0], "url": f"https://t.me/{ch}/{r[0]}", "coin": r[1], "side": r[2], "t": r[3], "horizon_h": r[4],
                               "move_pct": round(r[5] * 100, 2) if r[5] is not None else None, "result": r[6]} for r in rows[:30]],
                   "error": errors.get(ch)}
    return {"asof": now, "channels": out,
            "about_ru": ("Записываем каждый прогноз канала в момент публикации (монета и направление) и через 1–3 дня сверяем с ценой: "
                         "сбылся — движение 0.5% и больше в сторону прогноза. Для сравнения — «всегда вверх» на тех же отрезках. "
                         "Проверка за 200 дней (18.10) нашла у канала Mr. Trader Pro 0% сделок со стопом и 1% с целями; большинство «попаданий» "
                         "написаны задним числом («купил по 72 — сейчас 97»), поэтому честно оценить можно только так — вперёд.")}


def run(conn: sqlite3.Connection, out: str = "web", now: int | None = None, fetch=fetch_posts) -> dict:
    try:
        markets = json.load(open(os.path.join(out, "markets.json"), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        markets = {}
    doc = step(conn, markets, now, fetch)
    with open(os.path.join(out, "channel_watch.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    return doc
