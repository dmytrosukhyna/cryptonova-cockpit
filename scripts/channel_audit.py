"""An honest check of a public Telegram trading channel (18.10.2026, owner: «этот трейдер максимально точные прогнозы даёт, за
полгода ни разу не ошибался — изучи, откуда он всё знает, и учитывай его информацию»).

Runs in GitHub Actions only (.github/workflows/research-channel.yml): reads the PUBLIC web preview t.me/s/<channel> back N days
(nothing private, no login), keeps posts only in memory and publishes AGGREGATE numbers (annotations) — no post texts are stored
or committed.

What it measures:
  • posts by kind: calls (a #COIN or coin name + long/short/buy/entry words + a price), «result» posts («сейчас», «X», «тейк»,
    «профит»), loss posts («стоп», «минус», «убыток», «закрыл в минус»), news/ads;
  • calls with a stated stop and with stated targets;
  • every call checked against the market (Binance archive 1h klines, spot USDT pair, futures as fallback): did the price
    trade at the entry within 3 days; the move in the call's direction after 1 / 7 / 30 days from the post; the worst move
    against it in the first 7 days (what a holder had to sit through); if a stop is stated — was it hit before the first target;
  • retro posts («купил по 72, сейчас 97») — counted separately: a gain claimed after the fact is not a forecast;
  • gaps in post numbers inside the window — deleted posts (service messages also use numbers, so it is an upper bound).
Stdlib + numpy."""
import argparse
import datetime as dt
import html
import io
import json
import os
import re
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from fetch_alt100 import http, note  # noqa: E402

LONG_W = r"(лонг|long|купил|покупк|покупаю|добрал|докупил|набира|вход в лонг|buy)"
SHORT_W = r"(шорт|short|продал в шорт|вход в шорт|sell short)"
RESULT_W = r"(сейчас|уже|тейк|профит|отработал|забрали|\b\d+(?:[.,]\d+)?\s?[xх]\b|\+\d+\s?%)"
LOSS_W = r"(стоп выбил|выбило стоп|по стопу|минус|убыт|закрыл в -|закрыли в -|не отработал|ошибся|loss)"
AD_W = r"(промокод|promo|скидк|discount|реферал|fpr=|бонус|подписк|курс|обучени)"
COINS = None


def fetch_posts(channel: str, days: int) -> list[dict]:
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    posts, before, seen = [], None, set()
    for _ in range(600):
        url = f"https://t.me/s/{channel}" + (f"?before={before}" if before else "")
        s, b = http(url, min_interval=1.0)
        if s != 200 or not b:
            note("channel", f"HTTP {s} at {url}", "warning")
            break
        blocks = re.split(r'<div class="tgme_widget_message_wrap', b)[1:]
        page = []
        for bl in blocks:
            m = re.search(r'data-post="[^"/]+/(\d+)"', bl)
            t = re.search(r'<time datetime="([^"]+)"', bl)
            if not m or not t:
                continue
            pid = int(m.group(1))
            if pid in seen:
                continue
            seen.add(pid)
            tx = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', bl, re.S)
            text = html.unescape(re.sub(r"<br\s*/?>", "\n", tx.group(1))) if tx else ""
            text = re.sub(r"<[^>]+>", "", text)
            when = dt.datetime.fromisoformat(t.group(1).replace("Z", "+00:00"))
            page.append({"id": pid, "t": when, "text": text, "edited": "edited" in bl[:4000],
                         "media": bool(re.search(r"tgme_widget_message_(photo|video|voice|document)", bl))})
        if not page:
            break
        posts += page
        oldest = min(page, key=lambda p: p["id"])
        if oldest["t"] < since:
            break
        before = oldest["id"]
    posts = [p for p in posts if p["t"] >= since]
    posts.sort(key=lambda p: p["id"])
    return posts


def num(s: str) -> float | None:
    try:
        return float(s.replace(",", ".").replace(" ", ""))
    except ValueError:
        return None


def classify(p: dict, coins: set) -> dict:
    tx = p["text"]
    low = tx.lower()
    tags = [c for c in re.findall(r"#([A-Za-z0-9]{2,12})", tx) if c.upper() in coins]
    if not tags:
        tags = [w for w in re.findall(r"\b([A-Z]{2,10})\b", tx) if w in coins and w not in ("USD", "USDT", "CEO", "ETF", "SEC")]
    coin = tags[0].upper() if tags else None
    ml, ms = re.search(LONG_W, low), re.search(SHORT_W, low)
    side = ("long" if not ms or (ml and ml.start() < ms.start()) else "short") if (ml or ms) else None
    nums = [num(x) for x in re.findall(r"(?<![\w#])(\d+(?:[.,]\d+)?)(?![\w%])", tx)]
    nums = [x for x in nums if x and x > 0]
    stop = re.search(r"(стоп|stop|sl)\D{0,12}(\d+(?:[.,]\d+)?)", low)
    tps = re.findall(r"(тейк|цель|цели|tp\d?|target)\D{0,12}(\d+(?:[.,]\d+)?)", low)
    retro = bool(re.search(r"(купил|вход|взяли|добрал|entry)[^\n]{0,40}(сейчас|now|уже)", low))
    kind = ("ad" if re.search(AD_W, low) else "loss" if re.search(LOSS_W, low) else
            "retro" if retro else "call" if (coin and side and nums) else "result" if (coin and re.search(RESULT_W, low)) else
            "other")
    entry = None
    if kind == "call":
        m = re.search(LONG_W + r"\D{0,25}(\d+(?:[.,]\d+)?)", low) or re.search(SHORT_W + r"\D{0,25}(\d+(?:[.,]\d+)?)", low)
        entry = num(m.group(2)) if m else (nums[0] if nums else None)
    return {"coin": coin, "side": side, "kind": kind, "entry": entry, "stop": num(stop.group(2)) if stop else None,
            "tps": [num(x[1]) for x in tps if num(x[1])]}


def klines_1h(sym: str, t0: dt.datetime, days: int = 32) -> tuple | None:
    """1h closes / highs / lows from the Binance archive (spot, then USDT-M futures) from t0 for `days` days."""
    for base in ("https://data.binance.vision/data/spot", "https://data.binance.vision/data/futures/um"):
        rows = []
        for k in range(days + 2):
            d = (t0 + dt.timedelta(days=k)).date()
            if d >= dt.datetime.now(dt.timezone.utc).date():
                break
            s, b = http(f"{base}/daily/klines/{sym}/1h/{sym}-1h-{d:%Y-%m-%d}.zip", binary=True)
            if s != 200:
                if k == 0:
                    break
                continue
            with zipfile.ZipFile(io.BytesIO(b)) as z:
                for line in z.read(z.namelist()[0]).decode().splitlines():
                    x = line.split(",")
                    if x[0].isdigit():
                        t = int(x[0]); t = t // 1000 if t > 10 ** 14 else t
                        rows.append((t, float(x[2]), float(x[3]), float(x[4])))
        if rows:
            rows.sort()
            a = np.array(rows)
            a = a[a[:, 0] >= t0.timestamp() * 1000]
            return a if len(a) else None
    return None


def check(call: dict, p: dict) -> dict | None:
    a = klines_1h(f"{call['coin']}USDT", p["t"])
    if a is None or not call["entry"]:
        return None
    t, hi, lo, cl = a[:, 0], a[:, 1], a[:, 2], a[:, 3]
    px0 = cl[0]
    e = call["entry"]
    if not (0.33 < e / px0 < 3):              # the number is not a price of this coin (a percent, a date, a target...)
        return None
    sgn = 1 if call["side"] == "long" else -1
    first3 = slice(0, 72)
    filled = bool((lo[first3] <= e).any() and (hi[first3] >= e).any()) or abs(px0 / e - 1) < 0.01
    def ret(h):
        return float(sgn * (cl[min(h, len(cl) - 1)] / e - 1)) if len(cl) > h else None
    w7 = slice(0, min(168, len(cl)))
    mae = float(min(sgn * (lo[w7] / e - 1)) if sgn > 0 else min(sgn * (hi[w7] / e - 1)))
    stop_first = None
    if call["stop"] and call["tps"]:
        st, tp = call["stop"], call["tps"][0]
        hs = np.where((lo <= st) if sgn > 0 else (hi >= st))[0]
        ht = np.where((hi >= tp) if sgn > 0 else (lo <= tp))[0]
        if len(hs) or len(ht):
            stop_first = bool(len(hs) and (not len(ht) or hs[0] < ht[0]))
    return {"filled": filled, "r1d": ret(24), "r7d": ret(168), "r30d": ret(720), "mae7": mae, "stop_first": stop_first}


def stat(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    v = np.array(xs)
    return {"n": len(v), "median_pct": round(float(np.median(v)) * 100, 1), "mean_pct": round(float(v.mean()) * 100, 1),
            "share_pos_pct": round(float((v > 0).mean()) * 100)}


def audit(a):
    global COINS
    s, b = http("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=1")
    COINS = {c["symbol"].upper() for c in json.loads(b)} if s == 200 else set()
    COINS |= {"BTC", "ETH", "SOL", "HYPE", "ARB", "SUI", "AAVE", "UNI", "BNB", "XRP", "DOGE", "LINK", "ONDO", "TON", "NEAR", "ENA", "PEPE"}
    posts = fetch_posts(a.channel, a.days)
    if not posts:
        note("channel", "no posts read", "error")
        return
    ids = [p["id"] for p in posts]
    gaps = (ids[-1] - ids[0] + 1) - len(ids)
    cls = [(p, classify(p, COINS)) for p in posts]
    kinds = {}
    for _, c in cls:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    calls = [(p, c) for p, c in cls if c["kind"] == "call"]
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda pc: check(pc[1], pc[0]), calls))
    checked = [(pc, r) for pc, r in zip(calls, res) if r]
    filled = [r for _, r in checked if r["filled"]]
    out = {
        "channel": a.channel, "days": a.days, "posts": len(posts), "first": posts[0]["t"].strftime("%Y-%m-%d"),
        "last": posts[-1]["t"].strftime("%Y-%m-%d"), "id_range": [ids[0], ids[-1]], "id_gaps_max_deleted": gaps,
        "edited_share_pct": round(sum(p["edited"] for p in posts) / len(posts) * 100), "kinds": kinds,
        "calls": len(calls), "calls_with_stop_pct": round(sum(1 for _, c in calls if c["stop"]) / max(1, len(calls)) * 100),
        "calls_with_targets_pct": round(sum(1 for _, c in calls if c["tps"]) / max(1, len(calls)) * 100),
        "calls_checked": len(checked), "calls_entry_reached": len(filled),
        "in_direction_1d": stat([r["r1d"] for r in filled]), "in_direction_7d": stat([r["r7d"] for r in filled]),
        "in_direction_30d": stat([r["r30d"] for r in filled]),
        "worst_against_in_7d": stat([r["mae7"] for r in filled]),
        "stop_before_first_target": sum(1 for r in filled if r["stop_first"] is True),
        "target_before_stop": sum(1 for r in filled if r["stop_first"] is False),
        "sides": {"long": sum(1 for _, c in calls if c["side"] == "long"), "short": sum(1 for _, c in calls if c["side"] == "short")},
        "coins_top": sorted({c["coin"] for _, c in calls if c["coin"]}, key=lambda x: -sum(1 for _, c in calls if c["coin"] == x))[:15],
    }
    # the same move for a random entry: BTC held over the same days (what the market did anyway)
    note(f"channel {a.channel} · posts", json.dumps({k: out[k] for k in ("posts", "first", "last", "id_range", "id_gaps_max_deleted", "edited_share_pct", "kinds")}, ensure_ascii=False))
    note(f"channel {a.channel} · calls", json.dumps({k: out[k] for k in ("calls", "calls_with_stop_pct", "calls_with_targets_pct", "calls_checked", "calls_entry_reached", "sides", "coins_top")}, ensure_ascii=False))
    note(f"channel {a.channel} · outcome", json.dumps({k: out[k] for k in ("in_direction_1d", "in_direction_7d", "in_direction_30d", "worst_against_in_7d", "stop_before_first_target", "target_before_stop")}, ensure_ascii=False))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write("## Проверка канала\n```\n" + json.dumps(out, ensure_ascii=False, indent=1) + "\n```\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="Mr_Trader_Pro")
    ap.add_argument("--days", type=int, default=200)
    audit(ap.parse_args())
