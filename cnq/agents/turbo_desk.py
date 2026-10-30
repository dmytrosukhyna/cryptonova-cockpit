"""«Турбо» — two demo accounts that push our proven edges as far as the history allows (19.10.2026, owner: «цель 100%, промежуточная —
+70–80% в месяц; делаем всё, чтобы её добиться»). Evidence: scripts/turbo_study.py (docs/research/turbo.md).

  • lstx «Турбо · шорт листингов» — the live listing-short desk (profiles.lst) at 20 % of the account per listing instead of 5 %
    (the same trades, ×4 the size; up to 20 open = up to 4× the account in shorts). History 2023-01 → 2026-10 (447 listings after
    the live filters, compounding): about +6 % a month on average, 2025 +296 %, 2026 +123 %, but only half the months in plus and a
    worst drawdown of −56 %. Bigger sizes add little and break the account (30 % → +7 % a month, −72 %; 50 % → +7.5 %, −90 %).
    Here: a scaled mirror — equity = $10 000 + 4 × the base desk's result (no compounding: a fair, slightly modest copy).
  • btcx «Турбо · BTC ×2 в росте» — BTC with 2× leverage while our regime says «рост» (BTC above its 200-day average and up over
    60 days), cash at 4 % otherwise; funding paid on the leverage. History 2021 → 2026: +2.9 % a month on average, ×7.2 in total,
    drawdown −69 %. 2× is the best leverage on history: 3× already grows LESS (+2.5 % a month, −88 %), 5× ends at ×0.27.
Both are demo accounts measured against the owner's target (+70 % a month) — the panel shows the month so far next to it. Stdlib only."""
from __future__ import annotations

import json
import os
import sqlite3
import time

from ..risk import gate as risk_gate

START = 10_000.0
LST_K = 4.0                       # 20 % per listing ÷ the base desk's 5 %
BTC_LEV = 2.0
CASH_APY = 0.04
FUND_8H = 0.0001                  # ≈ typical BTC funding per 8 h (0.01 %) — paid by a long on the leveraged part
DAY = 86_400_000
TARGET_MONTH_PCT = 70.0

NAMES = {"lstx": "Турбо · шорт листингов", "btcx": "Турбо · BTC ×2 в росте"}
ABOUT = {
    "lstx": "Тот же шорт новых листингов, но 20% счёта на монету вместо 5% (до 20 позиций — до 4 счетов в шортах). История 2023–2026: "
            "в среднем около +6% в месяц, 2025 +296%, 2026 +123%, но в плюсе только половина месяцев, худшая просадка −56%. Больше "
            "размер почти не добавляет дохода и ломает счёт (30% → +7% в месяц и −72%). Демо $10 000: зеркало основного счёта ×4.",
    "btcx": "Биткоин с плечом 2, только пока наш режим говорит «рост» (BTC выше 200-дневной средней и за 60 дней в плюсе); иначе — "
            "доллары под 4%. История 2021–2026: в среднем +2.9% в месяц, ×7.2 за всё время, худшая просадка −69%. Плечо 2 — лучшее "
            "на истории: при 3 рост уже меньше (+2.5% в месяц, −88%), при 5 счёт заканчивается на ×0.27. Демо $10 000.",
}


def _load(out: str, name: str) -> dict:
    try:
        return json.load(open(os.path.join(out, name), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def fresh_btc(candles_db: str | None, after: int) -> float | None:
    """The latest BTC 15m close from the engine's candle store (updated every minute), if newer than `after` (ms).
    decision.json is refreshed only every 15–30 min, so without this the ×2 account was marked to a stale price."""
    if not candles_db or not os.path.exists(candles_db):
        return None
    try:
        c = sqlite3.connect(f"file:{candles_db}?mode=ro", uri=True, timeout=5)
        try:
            r = c.execute("SELECT close, close_time, open_time FROM candles WHERE symbol='BTC-USDT' AND interval='15m' "
                          "ORDER BY open_time DESC LIMIT 1").fetchone()
        finally:
            c.close()
    except Exception:  # noqa: BLE001
        return None
    if not r or not r[0]:
        return None
    t = min(int(r[1] or 0), int(time.time() * 1000)) if r[1] else int(r[2] or 0)
    return float(r[0]) if t > after else None


def btc_state(out: str, candles_db: str | None = None) -> tuple[float | None, bool | None]:
    dec = _load(out, "decision.json")
    b = next((c for c in dec.get("coins") or [] if str(c.get("symbol", "")).startswith("BTC")), None) or {}
    rg = b.get("regime") or {}
    px, sma, r60 = b.get("price"), rg.get("sma200"), rg.get("r60")
    fx = fresh_btc(candles_db, int(dec.get("time") or dec.get("asof") or 0))
    if fx and px and r60 is not None:                       # r60 moves with the price; rescale it to the fresher close
        r60 = (1 + r60) * fx / float(px) - 1
    px = fx or px
    if px and sma and r60 is not None:
        return float(px), bool(px > sma and r60 > 0)
    for r in _load(out, "markets.json").get("top") or []:
        if r.get("symbol") == "BTC" and r.get("price"):
            return float(r["price"]), None
    return None, None


def _month_start(now: int) -> int:
    import datetime as dt
    d = dt.datetime.fromtimestamp(now / 1000, dt.timezone(dt.timedelta(hours=3)))
    return int(d.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)


def _month_pct(hist: list, now: int) -> float | None:
    ms = _month_start(now)
    before = [h for h in hist if h[0] < ms]
    base = before[-1][1] if before else START          # started this month → from the $10 000 start
    return round((hist[-1][1] / base - 1) * 100, 2) if hist and base else None


def _doc(key: str, equity: float, hist: list, now: int, extra: dict) -> dict:
    peak, dd = START, 0.0
    for _, e in hist:
        peak = max(peak, e)
        dd = max(dd, (peak - e) / peak if peak else 0)
    return {"asof": now, "profile": key, "name": NAMES[key], "about": ABOUT[key], "start_equity": START,
            "since": hist[0][0] if hist else now, "equity": round(equity, 2), "pnl_usd": round(equity - START, 2),
            "pnl_pct": round((equity / START - 1) * 100, 2), "open_pnl_usd": 0.0, "open": [], "pending": [], "closed": [],
            "stats": {"trades": 0, "winrate": None, "max_dd_pct": round(dd * 100, 2), "best": 0, "worst": 0},
            "equity_history": [{"time": t, "equity": round(e, 2)} for t, e in hist[-500:]], "events": [], "error": None,
            "month_pct": _month_pct(hist, now), "target_month_pct": TARGET_MONTH_PCT, **extra}


def step(conn: sqlite3.Connection, out: str = "web", now: int | None = None, candles_db: str | None = None) -> dict:
    now = now or int(time.time() * 1000)
    conn.execute("CREATE TABLE IF NOT EXISTS turbo_state(k TEXT PRIMARY KEY, v TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS turbo_hist(desk TEXT, time INTEGER, equity REAL, PRIMARY KEY(desk, time))")
    st = {k: json.loads(v) for k, v in conn.execute("SELECT k, v FROM turbo_state")}
    docs = {}
    # ---- lstx: the listing desk ×4 ----
    base = ((_load(out, "paper.json").get("profiles") or {}).get("lst")) or {}
    if base.get("equity") is not None:
        b0 = float(base.get("start_equity") or START)
        mtm = float(base["equity_mtm"] if base.get("equity_mtm") is not None else base["equity"])
        eq = START + LST_K * (mtm - b0)                                     # history and month: closed + open
        eq_closed = START + LST_K * (float(base["equity"]) - b0)            # `equity` = closed only (the paper-desk convention)
        conn.execute("INSERT OR REPLACE INTO turbo_hist VALUES(?,?,?)", ("lstx", now, eq))
        hist = [[t, e] for t, e in conn.execute("SELECT time, equity FROM turbo_hist WHERE desk='lstx' ORDER BY time")]
        opn = [{**o, "qty": (o.get("qty") or 0) * LST_K, "qty_left": (o.get("qty") or 0) * LST_K, "pnl_usd": 0.0,
                "open_pnl_usd": round((o.get("open_pnl_usd") or 0) * LST_K, 2)} for o in base.get("open") or []]
        d = _doc("lstx", eq, hist, now, {"open": opn, "open_pnl_usd": round(sum(o["open_pnl_usd"] for o in opn), 2), "equity_mtm": round(eq, 2),
                                         "rules": "как «Шорт листингов», но 20% счёта на монету (×4), стоп +30%, выход через 14 дней"})
        bs = base.get("stats") or {}
        d["stats"].update({"trades": bs.get("trades", 0), "winrate": bs.get("winrate"), "avg_trade_pct": bs.get("avg_trade_pct")})
        d["closed"] = [{**t, "net_usd": round((t.get("net_usd") or 0) * LST_K, 2)} for t in base.get("closed") or []]
        d["equity"] = round(eq_closed, 2)
        if base.get("watch"):
            d["watch"] = base["watch"]      # 22.10: the same listings feed the ×4 mirror — the same «ждёт листинг» status
        docs["lstx"] = d
    # ---- btcx: BTC ×2 while the regime says «рост» ----
    px, bull = btc_state(out, candles_db)
    fresh = "btcx" not in st
    b = st.get("btcx") or {"equity": START, "px": None, "on": False, "t": now}
    if "day" not in b:           # 30.10: older state — keep the current position until the next daily close
        b["day"], b["want"] = now // DAY, (None if fresh else b["on"])
    if px:
        dt_ms = max(0, now - b["t"])
        if b["on"] and b["px"]:
            r = px / b["px"] - 1
            b["equity"] *= max(0.0, 1 + BTC_LEV * r - BTC_LEV * FUND_8H * dt_ms / (8 * 3_600_000))
        else:
            b["equity"] *= 1 + CASH_APY * dt_ms / (365 * DAY)
        events = []
        # 30.10: the regime is decided ONCE per UTC day, on the first run after 00:00 (≈ the daily close), as in the tested history
        # (scripts/turbo_study.py: daily closes). Deciding every minute on the live price flipped the desk in and out whenever BTC sat
        # on the 60-day threshold (29–30.10: out at 00:20, back in at 05:10, 0.1 % fee per flip) — behaviour the history never had.
        if bull is not None and (b["day"] != now // DAY or b.get("want") is None):
            b["day"], b["want"] = now // DAY, bull
        want = b.get("want")
        go = want is not None and want != b["on"]
        if go and want:          # 20.10: going into BTC ×2 is a new entry → the risk layer; refused → stays in dollars, tried again next run
            ok, why = risk_gate.allow_entry("btcx", "BTC", "LONG", BTC_LEV * b["equity"], now)
            if not ok:
                go = False
                if risk_gate.record_skip(conn, "btcx", f"BTCX-{now // DAY}", "BTC", "LONG", BTC_LEV * b["equity"], why, now):
                    events.append(f"Рынок «рост», но вход отложен — риск-слой: {why}")
        if go:                   # leaving to dollars (an exit) is never held back
            events.append("Рынок «рост» — покупаем BTC с плечом 2." if want else "Рынок «осторожно» — выходим в доллары.")
            b["equity"] *= 1 - 0.0005 * BTC_LEV                # taker fee on the switch
            b["on"] = want
        b["px"], b["t"] = px, now
        conn.execute("INSERT OR REPLACE INTO turbo_state VALUES(?, ?)", ("btcx", json.dumps(b)))
        conn.execute("INSERT OR REPLACE INTO turbo_hist VALUES(?,?,?)", ("btcx", now, b["equity"]))
        hist = [[t, e] for t, e in conn.execute("SELECT time, equity FROM turbo_hist WHERE desk='btcx' ORDER BY time")]
        d = _doc("btcx", b["equity"], hist, now, {"in_market": b["on"], "btc_price": px,
                                                  "rules": "BTC ×2, пока BTC выше 200-дневной средней и за 60 дней в плюсе (решение раз в день, по дневному закрытию); иначе доллары под 4%"})
        d["events"] = events
        d["position_ru"] = "в BTC с плечом 2" if b["on"] else "в долларах под 4%"      # marked to market in `equity`; no open rows
        docs["btcx"] = d
    conn.commit()
    return docs


def run(conn: sqlite3.Connection, out: str = "web", now: int | None = None, candles_db: str | None = None) -> dict:
    docs = step(conn, out, now, candles_db)
    p = os.path.join(out, "paper.json")
    try:
        paper = json.load(open(p, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return docs
    paper.setdefault("profiles", {}).update(docs)
    pl = [x for x in paper.get("profile_list") or [] if x.get("key") not in docs]
    for k in docs:
        pl.append({"key": k, "name": NAMES[k], "about": ABOUT[k], "risk": 0, "classes": ["TURBO"], "max_open": 20})
    paper["profile_list"] = pl
    with open(p, "w", encoding="utf-8") as f:
        json.dump(paper, f, ensure_ascii=False)
    return docs
