"""Портфель «Ядро + альтсезон» — a model spot portfolio with an altseason switch, and its signals to the CRYPTO NOVA Signals
channel (19.10.2026, owner: «все рассказывают про альтсезон … с альтами есть перспектива на X5, X7 … предложи правильный,
самый лучший вариант»; «туда же и портфель надо законнектить наш»).

Evidence (weekly Binance prices 2022-01 → 2026-10, 0.2 % of the turnover per rebalance, idle dollars at 4 %):
  • holding alts always: ×0.29 (−91 %); alts only while the BTC regime says «рост»: ×2.1 (−63 %), 2025 −49 %;
  • the core with the regime + alts (variant C): ×2.9 (−32 %), 2025 −17 %;
  • core 40 % + an alt part 60 % that holds 10–15 quality alts ONLY while the altseason gauge is on (else BTC):
    ×2.7, drawdown −33 %, 2023 +128 %, 2025 +2 % — it was in alts in Dec 2023 – Mar 2024 and Dec 2024, and out of them in
    the 2025 bleed. A short gauge (8 weeks) was much worse (×1.5) — the gauge is a filter, not a forecast.
Altseason gauge (daily closes of the 40 coins of the own desk, BTC and ETH excluded from the alts):
  ON = the equal-weight alt index against BTC is above its 91-day average AND ≥ 60 % of the alts beat BTC over 91 days
  AND the BTC regime is «рост» (BTC above its 200-day average and up over 60 days).
Model (of the whole portfolio; the owner's real one is about $19 000 — amounts are never posted, only shares):
  futures 15 % (our signals) · dollars 10 % (Earn) · core BTC 15.8 / ETH 6.3 / SOL 4.7 / LINK 2.4 / NEAR 2.4 %
  · alt part 43.4 %: SOL, LINK, NEAR, AVAX, SUI, ONDO, AAVE, HBAR, PENDLE, INJ equally while the gauge is on, BTC when off;
  regime not «рост» → the coins shrink to 35 % of their size, the rest waits in dollars.
Posts to the signals channel (signal_feed.signals_chat): when the gauge or the regime changes, and a Monday status.
Writes web/altseason.json (a generic market gauge + the model's shares). Stdlib only."""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import time

DAY = 86_400_000
WIN = 91
BREADTH_MIN = 0.60
ALT_BASKET = ["SOL", "LINK", "NEAR", "AVAX", "SUI", "ONDO", "AAVE", "HBAR", "PENDLE", "INJ"]
CORE = {"BTC": 15.8, "ETH": 6.3, "SOL": 4.7, "LINK": 2.4, "NEAR": 2.4}
ALT_PART, FUTURES, CASH = 43.4, 15.0, 10.0
BEAR_KEEP = 0.35
# How the alt part leaves the alts (19.10 daily re-check of this exact model, since 2022, with the futures part and costs):
#   "gauge"  — out of alts when the gauge turns off:          ×2.88, drawdown −26 %, 2023 +101 %, 2025 +1 %, 2026 +9 %
#   "regime" — alts whenever the market is «рост» (gauge = info): ×3.54, drawdown −36 %, 2023 +162 %, 2025 −17 %, 2026 +29 %
#   (for reference, the alt part always in BTC: ×3.08, −25 %). Today both hold the alts; the owner picks the exit rule.
EXIT = "gauge"
NAME = "Портфель CRYPTO NOVA · ядро + альтсезон"


def gauge(conn: sqlite3.Connection) -> dict | None:
    """From own_closes → {on, breadth, ratio_vs_avg_pct, alt_vs_btc_91d_pct, bull, day} or None when there is not enough data."""
    rows = conn.execute("SELECT symbol, day, close FROM own_closes ORDER BY day").fetchall()
    by: dict[str, dict[int, float]] = {}
    for s, d, c in rows:
        by.setdefault(s, {})[d] = c
    btc = by.get("BTC") or {}
    days = sorted(btc)
    if len(days) < 2 * WIN + 1:
        return None
    alts = [s for s in by if s not in ("BTC", "ETH")]
    # equal-weight alt index (rebalanced daily) divided by BTC
    idx, ratio = 1.0, []
    for i, d in enumerate(days):
        if i:
            rs = [by[s][d] / by[s][days[i - 1]] - 1 for s in alts if d in by[s] and days[i - 1] in by[s] and by[s][days[i - 1]] > 0]
            idx *= 1 + (sum(rs) / len(rs) if rs else 0.0)
        ratio.append(idx / btc[d])
    avg = sum(ratio[-WIN:]) / WIN
    d0, d1 = days[-1 - WIN], days[-1]
    b_ret = btc[d1] / btc[d0] - 1
    beat = [by[s][d1] / by[s][d0] - 1 > b_ret for s in alts if d0 in by[s] and d1 in by[s] and by[s][d0] > 0]
    breadth = sum(beat) / len(beat) if len(beat) >= 10 else None
    c = [btc[d] for d in days]
    s200 = sum(c[-200:]) / 200 if len(c) >= 200 else None
    bull = bool(s200 and c[-1] > s200 and c[-1] > c[-61]) if len(c) >= 61 and s200 else None
    on = bool(bull and breadth is not None and breadth >= BREADTH_MIN and ratio[-1] > avg)
    return {"on": on, "bull": bull, "breadth_pct": round(breadth * 100) if breadth is not None else None,
            "ratio_vs_avg_pct": round((ratio[-1] / avg - 1) * 100, 1), "alt_vs_btc_91d_pct": round((ratio[-1] / ratio[-1 - WIN] - 1) * 100, 1),
            "alts": len(beat), "day": d1}


def targets(bull: bool | None, alt_on: bool) -> dict[str, float]:
    """Shares of the whole portfolio, %."""
    t: dict[str, float] = dict(CORE)
    if alt_on:
        for s in ALT_BASKET:
            t[s] = t.get(s, 0.0) + ALT_PART / len(ALT_BASKET)
    else:
        t["BTC"] += ALT_PART
    if bull is False:
        coins = sum(t.values())
        t = {s: w * BEAR_KEEP for s, w in t.items()}
        t["USDT (Earn)"] = CASH + coins * (1 - BEAR_KEEP)
    else:
        t["USDT (Earn)"] = CASH
    t["Фьючерсы (сигналы)"] = FUTURES
    return {s: round(w, 1) for s, w in sorted(t.items(), key=lambda x: -x[1])}


def text(g: dict, t: dict, why: str) -> str:
    regime = "рост" if g["bull"] else "осторожно"
    alt = "ВКЛЮЧЁН — альт-часть в альтах" if g["on"] else "выключен — альт-часть в BTC"
    lines = [f"{NAME}", why, "",
             f"Рынок: {regime}. Датчик альтсезона: {alt}.",
             f"За 3 месяца альты {'сильнее' if g['alt_vs_btc_91d_pct'] >= 0 else 'слабее'} биткоина на {abs(g['alt_vs_btc_91d_pct']):.0f}%, "
             f"лучше биткоина выросли {g['breadth_pct']}% монет (нужно от {int(BREADTH_MIN * 100)}%).",
             "Выход из альтов: " + ("когда датчик выключится." if EXIT == "gauge" else "когда рынок выйдет из режима «рост»."),
             "", "Доли портфеля:"]
    lines += [f"• {s} — {w:g}%" for s, w in t.items()]
    lines += ["", "Ребаланс — лимитными ордерами, сначала продажи. Это не финансовый совет."]
    return "\n".join(lines)


def run(conn: sqlite3.Connection, out: str = "web", now: int | None = None, send=None, daily=None) -> dict:
    from .signal_feed import _send, signals_chat
    now = now or int(time.time() * 1000)
    conn.execute("CREATE TABLE IF NOT EXISTS alt_state(k TEXT PRIMARY KEY, v TEXT)")
    st = {k: json.loads(v) for k, v in conn.execute("SELECT k, v FROM alt_state")}
    conn.execute("CREATE TABLE IF NOT EXISTS own_closes(symbol TEXT, day INTEGER, close REAL, PRIMARY KEY(symbol, day))")
    if conn.execute("SELECT COUNT(DISTINCT day) FROM own_closes WHERE symbol='BTC'").fetchone()[0] < 2 * WIN + 1 and daily is not False:
        from .own_desk import COINS, fetch_daily      # first run: the own desk keeps closes only from its next daily step
        day = now // DAY * DAY
        for sym in COINS:
            try:
                rows = (daily or fetch_daily)(sym)
            except Exception:  # noqa: BLE001
                continue
            conn.executemany("INSERT OR REPLACE INTO own_closes VALUES(?,?,?)",
                             [(sym, int(r[0]), float(r[4])) for r in rows if int(r[0]) < day][-220:])
        conn.commit()
    g = gauge(conn)
    if not g:
        return {"error": "not enough history"}
    alt_hold = g["on"] if EXIT == "gauge" else bool(g["bull"])
    t = targets(g["bull"], alt_hold)
    doc = {"asof": now, "name": NAME, "gauge": g, "targets_pct": t, "rules_ru": (
        "Датчик альтсезона включён, когда альты в среднем сильнее биткоина своей 3-месячной средней, биткоин обгоняют от 60% "
        "монет и рынок в режиме «рост». Пока включён — альт-часть (43% портфеля) поровну в 10 альтах, иначе в BTC.")}
    with open(os.path.join(out, "altseason.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    prev = st.get("state")
    cur = {"bull": g["bull"], "on": alt_hold}
    monday = dt.datetime.fromtimestamp(now / 1000, dt.timezone.utc).weekday() == 0
    week = dt.datetime.fromtimestamp(now / 1000, dt.timezone.utc).strftime("%G-%V")
    why = None
    if prev is None:
        why = "Портфель подключён к каналу. Сейчас так:"
    elif prev != cur:
        if prev.get("on") != cur["on"]:
            why = ("Датчик альтсезона включился: переложить альт-часть из BTC в 10 альтов поровну." if cur["on"]
                   else "Датчик альтсезона выключился: альт-часть продать и переложить в BTC.")
        else:
            why = ("Рынок снова в режиме «рост»: вернуть монеты к полным долям." if cur["bull"]
                   else "Рынок перешёл в «осторожно»: монеты сократить до 35% их доли, остальное в доллары под процент.")
    elif monday and st.get("week") != week:
        why = "Понедельник — сверка долей портфеля:"
    posted = False
    if why:
        chat = signals_chat(conn)
        if chat or send is not None:
            try:
                msg = text(g, t, why)
                mid = send(msg) if send is not None else _send(msg, None, chat)
                posted = mid is not None or send is not None
            except Exception as e:  # noqa: BLE001
                doc["error"] = str(e)[:120]
        if posted:                      # no channel yet → the state is not saved, so the first post waits for the channel
            conn.execute("INSERT OR REPLACE INTO alt_state VALUES('state', ?)", (json.dumps(cur),))
            conn.execute("INSERT OR REPLACE INTO alt_state VALUES('week', ?)", (json.dumps(week),))
    conn.commit()
    doc["posted"] = posted
    return doc
