"""The CRYPTO NOVA portfolio for the mobile app (19.10.2026, owner: «в разделе Портфель пользователь будет видеть то, что сейчас
видит у нас, вводит свою сумму, и наша система делает ему балансировку … демо-портфель, что у него конкретно на балансе»).

One small public file — web/app_portfolio.json (https://cryptonova.vip/app_portfolio.json) — from the panel's «CRYPTO NOVA
портфель» (portfolio.json → portfolios.ideas):
  positions: coin, name, share of the portfolio, entry price, today's price, icons (the app's own icon URL + CoinGecko's);
  cash: the share in dollars; index: the portfolio's value with 100 at its start; history: the index over time (kept here in
  app_pf_hist so the app can draw the whole curve, not only the last 60 points); recent actions (buy / trim / sell) with reasons.
The app keeps the user's amount and start date ON THE PHONE (MVP, no account changes): holdings = amount × share ÷ price at
the start; today = amount × index(now) ÷ index(start). Demo only, not advice. Stdlib only."""
from __future__ import annotations

import json
import os
import sqlite3
import time

ICON = "https://www.crypto-capital.app/api/images/icons/{base}.png"


def _load(out: str, name: str) -> dict:
    try:
        with open(os.path.join(out, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def run(conn: sqlite3.Connection, out: str = "web", now: int | None = None) -> dict:
    now = now or int(time.time() * 1000)
    pf = (_load(out, "portfolio.json").get("portfolios") or {}).get("ideas") or {}
    mk = {str(r.get("symbol") or "").upper(): r for r in (_load(out, "markets.json").get("top") or [])}
    conn.execute("CREATE TABLE IF NOT EXISTS app_pf_hist(time INTEGER PRIMARY KEY, v REAL)")
    for h in pf.get("history") or []:
        if h.get("time") and h.get("v") is not None:
            conn.execute("INSERT OR IGNORE INTO app_pf_hist VALUES(?, ?)", (int(h["time"]), float(h["v"])))
    if pf.get("index") is not None:
        conn.execute("INSERT OR REPLACE INTO app_pf_hist VALUES(?, ?)", (now, float(pf["index"])))
    conn.commit()
    hist = [{"t": t, "v": round(v, 3)} for t, v in conn.execute("SELECT time, v FROM app_pf_hist ORDER BY time")]
    if len(hist) > 2000:                                    # keep the file small: every n-th point + the last one
        step = len(hist) // 1500 + 1
        hist = hist[::step] + ([hist[-1]] if hist[-1] is not hist[::step][-1] else [])
    pos = []
    for p in pf.get("positions") or []:
        if p.get("status") != "OPEN":
            continue
        s = str(p.get("symbol") or "").upper()
        m = mk.get(s) or {}
        pos.append({"symbol": s, "name": p.get("name") or m.get("name") or s, "weight": round(float(p.get("weight") or 0), 4),
                    "entry": p.get("entry"), "price": m.get("price"), "kind_ru": p.get("kind_ru"), "horizon_ru": p.get("horizon_ru"),
                    "icon_url": ICON.format(base=s.lower()), "image": m.get("image")})
    pos.sort(key=lambda x: -x["weight"])
    acts = [{"time": a.get("time"), "action": a.get("action"), "symbol": a.get("symbol"), "price": a.get("price"),
             "why_ru": (a.get("why_ru") or a.get("reason") or "")[:200]} for a in (pf.get("recent_actions") or [])[:15]]
    doc = {"asof": now, "available": bool(pos), "name": pf.get("name") or "CRYPTO NOVA портфель", "started": pf.get("started"),
           "index": pf.get("index"), "cash_pct": pf.get("cash_pct"), "positions": pos, "history": hist, "recent_actions": acts,
           "about_ru": (pf.get("about") or "")[:600],
           "how_ru": ("Введите сумму — приложение покажет, сколько каждой монеты было бы куплено на неё по долям портфеля CRYPTO NOVA, "
                      "и как эта сумма меняется вместе с портфелем с момента, когда вы её ввели. Портфель сам докупает, фиксирует и "
                      "перекладывает деньги — это демо-расчёт, без реальных покупок."),
           "disclaimer_ru": "Демо-расчёт на основе портфеля CRYPTO NOVA. Это не финансовый совет и не обещание дохода."}
    with open(os.path.join(out, "app_portfolio.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    return doc
