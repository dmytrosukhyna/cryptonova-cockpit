"""Monday weekend-gap fade for FX (class A family) — server side, for the Telegram channel.

Rule (validated on 11 pairs, 2012–2022: 2 547 trades, WR 66 %, +0.22R, 8 positive years in a row):
  gap = Monday open − Friday close, |gap| ≥ 0.5 × ATR(h1, 240) and ≤ 3 ATR → fade toward Friday close,
  stop = Monday open + 1 × gap beyond, target = Friday close, give up after 8 hours. Gold excluded (fails).
Data: Twelve Data (TWELVE_DATA_KEY). Run shortly after the weekly open (Sunday 22:05 UTC)."""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CAD", "USD/CHF", "EUR/JPY", "GBP/JPY", "EUR/GBP", "AUD/JPY", "EUR/CHF"]


def fetch_h1(pair: str, key: str, n: int = 400) -> list[dict]:
    url = f"https://api.twelvedata.com/time_series?symbol={urllib.parse.quote(pair)}&interval=1h&outputsize={n}&timezone=UTC&apikey={key}"
    with urllib.request.urlopen(url, timeout=20) as r:
        j = json.loads(r.read().decode())
    if j.get("status") == "error":
        raise RuntimeError(j.get("message"))
    vals = list(reversed(j.get("values", [])))
    return [{"t": int(time.mktime(time.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S"))) * 1000, "o": float(v["open"]),
             "h": float(v["high"]), "l": float(v["low"]), "c": float(v["close"])} for v in vals]


def atr(b: list[dict], n: int) -> float:
    tr = [max(x["h"] - x["l"], abs(x["h"] - b[i - 1]["c"]), abs(x["l"] - b[i - 1]["c"])) if i else x["h"] - x["l"] for i, x in enumerate(b)]
    w = tr[-n:]
    return sum(w) / len(w)


def gap_signal(pair: str, h1: list[dict], max_age_h: float = 8.0, min_atr: float = 0.5, max_atr: float = 3.0) -> dict | None:
    i = len(h1) - 1
    while i > 0 and h1[i]["t"] - h1[i - 1]["t"] < 40 * 3_600_000:
        i -= 1
    if i <= 0:
        return None
    fri, mon = h1[i - 1]["c"], h1[i]["o"]; gap = mon - fri; a = atr(h1[:i], 240); g = abs(gap) / a if a else 0
    age_h = (time.time() * 1000 - h1[i]["t"]) / 3_600_000
    if age_h > max_age_h or g < min_atr or g > max_atr:
        return None
    direction = "SHORT" if gap > 0 else "LONG"; last = h1[-1]["c"]
    stop = mon + gap
    if (direction == "LONG" and last >= fri) or (direction == "SHORT" and last <= fri):
        return None  # already filled
    if (direction == "LONG" and last <= stop) or (direction == "SHORT" and last >= stop):
        return None  # gap extended beyond the stop — no trade
    return {"pair": pair, "direction": direction, "entry": last, "stop": mon + gap, "target": fri, "gap_pct": gap / fri * 100,
            "gap_atr": g, "friday_close": fri, "monday_open": mon, "hours_left": round(max_age_h - age_h, 1)}


def text(s: dict) -> str:
    return (f"🅰 {s['pair']} {s['direction']} — гэп понедельника {s['gap_pct']:+.2f}% ({s['gap_atr']:.1f} ATR)\n"
            f"Вход {s['entry']:.5g} · стоп {s['stop']:.5g} · цель {s['target']:.5g} (закрытие пятницы) · окно {s['hours_left']} ч\n"
            f"Правило: закрытие гэпа; на истории 66% попаданий, +0.22R на сделку, плюс 8 лет подряд.")


def run(key: str | None = None, send: bool = False) -> list[dict]:
    key = key or os.environ.get("TWELVE_DATA_KEY", "")
    if not key:
        raise SystemExit("TWELVE_DATA_KEY not set")
    out = []
    for p in PAIRS:
        try:
            s = gap_signal(p, fetch_h1(p, key))
        except Exception as e:  # noqa: BLE001
            print(p, "error:", e); continue
        if s:
            out.append(s); print(text(s))
        time.sleep(8)  # free tier: 8 requests/min
    if send and out:
        from ..terminal.notify import send as tg
        tg("Гэпы понедельника (FX):\n\n" + "\n\n".join(text(s) for s in out))
    return out


if __name__ == "__main__":
    import sys
    run(send="--send" in sys.argv)
