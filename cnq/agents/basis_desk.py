"""Фикс-ставка на фьючерсах — a $10 000 demo desk that earns the futures–spot gap with no bet on the price (18.10.2026, owner:
«участвовать в разных источниках, в разных секторах … система сама распределит»; from the yield research: quarterly basis).

How it works (cash-and-carry): buy BTC (or ETH) on spot and short the same sum of its DATED futures on Deribit. The price no longer
matters: whatever BTC does, at expiry the futures and spot meet, and the gap that existed at entry is earned. So the rate is
FIXED when entering (unlike perpetual funding, which floats every hour). Deribit's dated futures are inverse (coin-margined): the
bought coin is the margin of a 1× short, so there is no leverage and nothing to liquidate.

Each run:
  • holding a position → equity grows linearly to the locked rate (what the trade pays when held to expiry; the day-to-day mark
    can wobble, the end can't); at expiry the trade is closed and the desk is flat again;
  • flat → takes the best of BTC / ETH (yield_desk.basis_row: furthest expiry 25–200 days, annualised, minus ≈0.3 % fees) and
    enters if the net rate is at least MIN_RATE; otherwise waits in USDT (0 %), logged.
Risks: the exchange (Deribit), fees, and an early exit (if the sum is needed before expiry the gap may be worse). Stdlib only."""
from __future__ import annotations

import json
import os
import sqlite3
import time

from .yield_desk import basis_row, fetch_futures

START = 10_000.0
MIN_RATE = 3.0            # % a year net; below that the cash waits — lending pays about as much with less hassle
YEAR_MS = 365 * 86_400_000


def step(conn: sqlite3.Connection, now: int | None = None, fetch=fetch_futures) -> dict:
    now = now or int(time.time() * 1000)
    conn.execute("CREATE TABLE IF NOT EXISTS basis_state(k TEXT PRIMARY KEY, v TEXT)")
    st = {k: json.loads(v) for k, v in conn.execute("SELECT k, v FROM basis_state")}
    if not st.get("start"):
        st = {"start": now, "cash": START, "pos": None, "history": [[now, START]], "events": []}
    events = []
    rows, err = {}, None
    for cur in ("BTC", "ETH"):
        try:
            r = basis_row(fetch(cur), cur, now)
        except Exception as e:  # noqa: BLE001
            r, err = None, f"{type(e).__name__}: {str(e)[:80]}"
        if r:
            rows[cur] = r
    pos = st.get("pos")
    if pos and now >= pos["expiry"]:                       # expiry: the locked gap is earned in full
        st["cash"] = pos["amount"] * (1 + pos["rate"] / 100 * (pos["expiry"] - pos["entered"]) / YEAR_MS)
        events.append({"t": now, "ru": f"{pos['cur']}: фьючерс истёк, заработано ${st['cash'] - pos['amount']:,.2f} по ставке {pos['rate']:.2f}% годовых."})
        st["pos"] = pos = None
    if not pos:
        best = max(rows.values(), key=lambda r: r["apy"]) if rows else None
        if best and best["apy"] >= MIN_RATE:
            st["pos"] = pos = {"cur": best["symbol"], "rate": best["apy"], "gross": best["apy_base"], "entered": now,
                               "expiry": best["expiry"], "amount": st["cash"], "name_ru": best["name_ru"]}
            events.append({"t": now, "ru": f"Вход: {best['name_ru']} — {best['apy']:.2f}% годовых после комиссий, ставка зафиксирована."})
        elif best and (st.get("waiting") != round(best["apy"], 1)):
            st["waiting"] = round(best["apy"], 1)
            events.append({"t": now, "ru": f"Ждём: лучшая ставка сейчас {best['apy']:.2f}% — ниже порога {MIN_RATE:.0f}%, деньги в USDT."})
    if pos:
        equity = pos["amount"] * (1 + pos["rate"] / 100 * (min(now, pos["expiry"]) - pos["entered"]) / YEAR_MS)
    else:
        equity = st["cash"]
    st["history"] = (st.get("history") or [])[-500:] + [[now, round(equity, 4)]]
    st["events"] = ((st.get("events") or []) + events)[-30:]
    for k, v in st.items():
        conn.execute("INSERT OR REPLACE INTO basis_state VALUES(?, ?)", (k, json.dumps(v)))
    conn.commit()
    best_now = max(rows.values(), key=lambda r: r["apy"]) if rows else None
    return {"asof": now, "name": "Фикс-ставка на фьючерсах (BTC / ETH)", "start_equity": START, "since": st["start"],
            "equity": round(equity, 2), "pnl_usd": round(equity - START, 2), "pnl_pct": round((equity / START - 1) * 100, 3),
            "position": pos, "rate_now": best_now["apy"] if best_now else None, "rates": rows, "min_rate": MIN_RATE, "error": err,
            "history": st["history"][-300:], "events": st["events"],
            "about_ru": ("Покупаем BTC или ETH на споте и одновременно продаём (шорт) на ту же сумму срочный фьючерс на Deribit. "
                         "Цена монеты нам больше не важна: к дате экспирации фьючерс и спот сходятся, и разница, которая была при входе, "
                         "становится нашим доходом — ставка фиксируется в момент входа. Без плеча: купленная монета — залог короткой "
                         f"позиции. Входим, только если ставка после комиссий не ниже {MIN_RATE:.0f}% годовых; иначе ждём в USDT. "
                         "Риски: биржа, комиссии и досрочный выход. Это демо.")}


def run(conn: sqlite3.Connection, out: str = "web", now: int | None = None, fetch=fetch_futures) -> dict:
    doc = step(conn, now, fetch)
    with open(os.path.join(out, "basis.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    return doc
