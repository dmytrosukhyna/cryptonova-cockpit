"""One small feed for the CRYPTO NOVA 2.0 app (19.10.2026, owner: «приложение — быстрая сводка, как Apple Watch; всё должно
работать по-настоящему»).

web/app_home.json (https://cryptonova.vip/app_home.json, on the server every minute) — everything the 2.0 home, futures and
spot tabs show, already in plain numbers so the app never recomputes the engine:
  total: earned since the demo start, split by source (futures = the average CNPTS $10 000 account — cons/bal/aggr trade the
         same signals at three sizes; spot = the CRYPTO NOVA portfolio per $10 000);
  curve: both sources over time (≤ 60 points); futures: result, today, trades, worst dip, open trades with stop/entry/now/target
         and a plain «why», last closed trades; spot: allocation groups, per-coin change since purchase, last action;
  market: bitcoin price and 24 h change, fear & greed, regime (BTC above its 200-day average = «рост»).
20.10: every open trade also explains itself (see _explain): why_title, checks [{t, v, ok}] with the numbers the desk stored at
entry, history (the rule's backtest record from docs/research + its live record), risk (stop / target / R:R / leverage / $ at risk /
holding time), work (the real scan counters behind the signal), note; futures.scan = the own desk's last daily scan.
20.10 (the app became multilingual): the same data → one file per language in the same step — web/app_home.json (ru, as before)
and web/app_home.<lang>.json for en / uk / el / es (cnq/agents/i18n.py LANGS); the field names stay (status_ru, about_ru, src_ru …)
and carry that file's language; the texts come from the catalog cnq/agents/i18n.py. market.regime («рост»/«спад») and the fear &
greed label stay Russian in every file — the app translates them itself. GET /v2/home?lang=xx serves the file of that language.
web/app_btc.json — bitcoin daily closes since 2021 for the free piggy bank («копилка»): the app replays the user's weekly or
monthly demo buys at real prices. Demo only, not advice. Stdlib only."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import time

from .i18n import DEFAULT, LANGS, T, file_name, plural, t

START = 10000.0
PROFILES = ("cons", "bal", "aggr")
GROUPS = (("alloc.btc", {"BTC"}), ("alloc.eth", {"ETH"}), ("alloc.sol", {"SOL"}))
KIND_KEY = {"мем": "alloc.meme", "крупный альт": "alloc.alt", "ядро": "alloc.core"}      # app_portfolio kind_ru → catalog
# the plain «why» of a trade: by the desk's kind (the main strategy's climax candles) or by the rule (i18n why.<key>)
WHY_KINDS = {"CLIMAX_LONG", "CLIMAX_SHORT"}
# 20.10 (owner: «нету активных сигналов в системе криптонова»): the app shows the trades of EVERY source of the CRYPTO NOVA mix
# (signal_feed.SOURCES), not only the main CNPTS desk — the own daily signals were open but invisible in the app.
WHY_GRADES = {"P", "C", "LST", "K"}
SRC_KEYS = {"bal", "imp", "own", "lst"}

# 20.10 (owner: «хотелось бы видеть более серьёзное объяснение, почему мы вошли в сделку. Мы проделываем огромную работу, и люди
# должны это видеть»): every open trade explains itself — the setup in a few words, the conditions checked at entry with THIS
# trade's numbers (the desks store them: own_desk.entry_facts, paper_desk signals()["facts"], listing_desk.entry_facts), the
# rule's record on history and live, the risk, and the scale of the scan behind it. Nothing is filled in when the data is missing.
TITLES = {"P", "C", "E", "B", "F", "D", "K", "LST"}                       # i18n title.<grade>
# The rules' record on history — numbers only from the research write-ups / backtest outputs named in `src`; a number the
# research does not give is None, never estimated. The words (years_plus, text) are i18n hist.<grade>.text and `years`.
HISTORY = {
    "P": {"n": None, "win_pct": None, "avg_pct": 3.1, "period": "2023–2026", "years": "hist.every_year",
          "src": "docs/research/own_signals.md, раунды 9–10"},
    "C": {"n": 403, "win_pct": 53, "avg_pct": 6.3, "period": "2021–2026", "years": "hist.every_year",
          "src": "docs/research/orders.md §3; own_signals.md, раунды 9–10"},
    "E": {"n": 3414, "win_pct": 60, "avg_pct": 1.26, "period": "2023–2026", "years": "hist.every_year",
          "src": "docs/research/oi_flush.md (дополнение 16.10), panic_breadth.md"},
    "B": {"n": 729, "win_pct": 59, "avg_pct": 0.95, "period": "2021–2026", "years": "hist.B.years",
          "src": "docs/research/orders.md §1"},
    "F": {"n": 650, "win_pct": 56, "avg_pct": 1.53, "period": "2023–2026", "years": "hist.every_year",
          "src": "docs/research/oi_flush.md"},
    "D": {"n": None, "win_pct": 55, "avg_pct": 1.77, "period": "2023–2026", "years": "hist.every_year",
          "src": "docs/research/new_signals.md"},
    "K": {"n": None, "win_pct": None, "avg_pct": None, "period": "2025–2026", "years": "hist.K.years",
          "src": "paper_desk.PROFILES['imp'], коммит 17139d5f; scripts/lab/results/trend_wide50.json"},
    "LST": {"n": 729, "win_pct": 55, "avg_pct": 2.6, "period": "2023–2026", "years": "hist.every_year",
            "src": "cnq/agents/listing_desk.py (scripts/listings_study.py)"},
}
RULE_NAMES = {"E", "B", "F", "D", "K"}                                     # i18n rname.<grade> («work» sentence)


def _load(out: str, name: str) -> dict:
    try:
        with open(os.path.join(out, name), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _r(x, n: int = 2):
    return None if x is None else round(float(x), n)


def _avg_curve(profiles: dict) -> list[tuple[int, float]]:
    """The average CNPTS account over time: the three profiles share timestamps (same engine cycle)."""
    hist = [(p, (profiles.get(p) or {}).get("equity_history") or []) for p in PROFILES]
    hist = [(p, h) for p, h in hist if h]
    if not hist:
        return []
    base = min(hist, key=lambda x: len(x[1]))[1]
    idx = [{int(e["time"]): float(e["equity"]) for e in h} for _, h in hist]
    out = []
    for e in base:
        t = int(e["time"])
        vals = [m[t] for m in idx if t in m]
        if len(vals) == len(idx):
            out.append((t, sum(vals) / len(vals) - START))
    return out


def _sample(points: list, n: int = 60) -> list:
    if len(points) <= n:
        return points
    step = (len(points) - 1) / (n - 1)
    return [points[round(i * step)] for i in range(n)]


def _mix_keys() -> list[str]:
    try:
        from .signal_feed import SOURCES
        return [k for k, _n, _w in SOURCES]
    except Exception:  # noqa: BLE001
        return ["bal"]


def _pc(x: float, d: int = 1, sign: bool = True) -> str:
    """a fraction as a percent in the app's style: 0.0399 → «+4.0%», −0.018 → «−1.8%» (sign=False → «4.0%»)"""
    v = round(x * 100, d)
    s = f"{abs(v):.{d}f}%"
    return (("+" if v > 0 else "−" if v < 0 else "") if sign else "") + s


def _vs(lang: str, a: float, b: float) -> str:
    """«на 4.0% выше» / «на 1.2% ниже» — a against b (en «4.0% above» …; the sentence around it names b)"""
    return t(lang, "vs.above" if a >= b else "vs.below", pct=_pc(a / b - 1, sign=False))


def _num(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".")


def _checks(grade: str, f: dict, sym: str = "", lang: str = DEFAULT) -> list[dict]:
    """the conditions the rule checked at entry, with THIS trade's numbers (only those the stored facts back)"""
    out: list[dict] = []

    def add(key, v, ok):
        out.append({"t": t(lang, key), "v": v, "ok": bool(ok)})
    c = f.get("close")
    if grade in ("P", "C"):
        if grade == "P":
            s50, s5, r2, ch2 = f.get("sma50"), f.get("sma50_5"), f.get("rsi2"), f.get("chg2")
            if c and s50:
                add("chk.trend", t(lang, "chk.trend.v50", vs=_vs(lang, c, s50)), c > s50)
            if s50 and s5:
                add("chk.trend_up", t(lang, "chk.trend_up.v", pct=_pc(s50 / s5 - 1)), s50 > s5)
            if r2 is not None:
                add("chk.pullback", t(lang, "chk.pullback.v", chg=_pc(ch2), rsi=_num(r2)) if ch2 is not None
                    else t(lang, "chk.pullback.v_rsi", rsi=_num(r2)), r2 < 10)
        else:
            hh, s20 = f.get("hh20"), f.get("sma20")
            if c and hh:
                add("chk.breakout", t(lang, "chk.breakout.v", vs=_vs(lang, c, hh)), c > hh)
            if c and s20:
                add("chk.trend", t(lang, "chk.trend.v20", vs=_vs(lang, c, s20)), c > s20)
        b = f.get("btc") or {}
        if b.get("close") and b.get("sma200") and b.get("c60"):
            bull = b["close"] > b["sma200"] and b["close"] > b["c60"]
            add("chk.market", t(lang, "chk.market.v", vs=_vs(lang, b["close"], b["sma200"]), pct=_pc(b["close"] / b["c60"] - 1),
                                mode=t(lang, "chk.market.bull" if bull else "chk.market.not")), bull)
        if f.get("vol") is not None:
            add("chk.volume", t(lang, "chk.volume.v30", x=f"{f['vol']:.1f}"), f["vol"] >= 1.5)
        if f.get("rank") and f.get("of"):
            add("chk.liquidity", t(lang, "chk.liquidity.v", rank=f["rank"], of=f["of"]), f["rank"] <= f["of"])
    elif grade == "E":
        if f.get("breadth") is not None and f.get("need_breadth") is not None:
            add("chk.selloff", t(lang, "chk.selloff.v", b=_pc(f["breadth"], 0, False), need=_pc(f["need_breadth"], 0, False)),
                f["breadth"] >= f["need_breadth"])
        if f.get("drop") is not None and f.get("need_drop") is not None:
            add("chk.candle", t(lang, "chk.candle.v", drop=_pc(f["drop"]), need=_pc(f["need_drop"])), f["drop"] <= f["need_drop"])
        if f.get("vol_x") is not None and f.get("need_vol") is not None:
            add("chk.volume", t(lang, "chk.volume.vday", x=f"{f['vol_x']:.1f}", need=f"{f['need_vol']:g}"), f["vol_x"] >= f["need_vol"])
    elif grade == "B":
        if f.get("ret") is not None and f.get("sigma_x") is not None:
            add("chk.crash", t(lang, "chk.crash.v", ret=_pc(f["ret"]), x=f"{abs(f['sigma_x']):.1f}",
                               need=f"{abs(f.get('need_sigma') or 3.5):g}"), f["sigma_x"] <= (f.get("need_sigma") or -3.5))
        if f.get("wick") is not None:
            add("chk.wick", t(lang, "chk.wick.v", w=_pc(f["wick"], 0, False), need=_pc(f.get("need_wick") or 0.5, 0, False)),
                f["wick"] >= (f.get("need_wick") or 0.5))
    elif grade == "F":
        if f.get("oi4") is not None and f.get("need_oi") is not None:
            add("chk.oi", t(lang, "chk.4h.v", x=_pc(f["oi4"], 0), need=_pc(f["need_oi"], 0)), f["oi4"] <= f["need_oi"])
        if f.get("px4") is not None and f.get("need_px") is not None:
            add("chk.price", t(lang, "chk.4h.v", x=_pc(f["px4"]), need=_pc(f["need_px"], 0)), f["px4"] <= f["need_px"])
    elif grade == "D":
        if f.get("basis") is not None and f.get("need_basis") is not None:
            add("chk.discount", t(lang, "chk.discount.below" if f["basis"] < 0 else "chk.discount.above",
                                  b=_pc(abs(f["basis"]), 2, False), need=_pc(abs(f["need_basis"]), 2, False)),
                f["basis"] <= f["need_basis"])
    elif grade == "K":
        if f.get("btc_ret") is not None and f.get("need_btc") is not None:
            add("chk.btc_jump", t(lang, "chk.btc_jump.v", ret=_pc(f["btc_ret"]), need=_pc(f["need_btc"])), f["btc_ret"] >= f["need_btc"])
        sc = f.get("scan") or {}
        if f.get("rank") and sc.get("coins"):
            add("chk.liquidity", t(lang, "chk.liquidity.vK", rank=f["rank"], n=sc["coins"]), True)
    elif grade == "LST":
        if f.get("age_h") is not None:
            add("chk.listing", t(lang, "chk.listing.v", h=_num(f["age_h"])), 4 <= f["age_h"] <= 24)
        if f.get("move") is not None and f.get("skip_pump") is not None:
            add("chk.no_pump", t(lang, "chk.no_pump.v", move=_pc(f["move"], 0), skip=_pc(f["skip_pump"], 0)), f["move"] < f["skip_pump"])
        if f.get("qvol") is not None and f.get("min_qvol") is not None:
            add("chk.turnover", t(lang, "chk.turnover.v", q=f"{f['qvol'] / 1e6:.1f}", min=f"{f['min_qvol'] / 1e6:g}"),
                f["qvol"] >= f["min_qvol"])
        if out:
            try:
                from .listing_desk import not_crypto
                add("chk.token", t(lang, "chk.token.v"), not not_crypto(f"{sym}-USDT"))
            except Exception:  # noqa: BLE001
                pass
    if grade in ("E", "B", "F", "D") and out and (f.get("scan") or {}).get("coins"):
        add("chk.coin", t(lang, "chk.coin.v", n=f["scan"]["coins"]), True)
    return out


def _hold(grade: str, lang: str = DEFAULT) -> str | None:
    try:
        from . import listing_desk, own_desk, paper_desk as pd
    except Exception:  # noqa: BLE001
        return None
    h = {"E": pd.HOLD_E_H, "F": pd.HOLD_F_H, "D": pd.HOLD_D_H, "K": pd.HOLD_K_H}
    if grade in ("P", "C"):
        return t(lang, f"hold.{grade}", d=own_desk.HOLD[grade])
    if grade in h:
        return t(lang, "hold.time", h=h[grade], hours=plural(lang, h[grade], "hour"))
    if grade == "B":
        return t(lang, "hold.B", h=pd.HOLD_B_H)
    if grade == "LST":
        return t(lang, "hold.LST", d=listing_desk.HOLD_D)
    return None


def _risk(o: dict, grade: str, entry, stop, tgt, lang: str = DEFAULT) -> dict | None:
    if not entry or not stop:
        return None
    try:
        from .signal_feed import LEV
        lev = LEV.get(grade, 3)
    except Exception:  # noqa: BLE001
        lev = None
    stop_pct = abs(stop / entry - 1) * 100
    tgt_pct = abs(tgt / entry - 1) * 100 if tgt else None
    qty = o.get("qty_left") if o.get("qty_left") is not None else o.get("qty")
    risk_usd = round(float(qty) * abs(entry - stop)) if qty else None
    return {"stop_pct": _r(-stop_pct, 1), "target_pct": _r(tgt_pct, 1),
            "rr": _r(tgt_pct / stop_pct, 1) if tgt_pct and stop_pct else None, "lev": lev, "risk_usd": risk_usd,
            "risk_pct": None if risk_usd is None else _r(risk_usd / START * 100, 2), "hold": _hold(grade, lang)}


def _live(desk: dict, src: str, grade: str) -> dict | None:
    """the rule's live record on its demo desk so far (closed trades, winners)"""
    if not desk:
        return None
    st = desk.get("stats") or {}
    if src == "own":
        r = (desk.get("live_by_rule") or {}).get(grade) or {"n": 0, "wins": 0, "avg_pct": None}
        return {"n": r.get("n", 0), "wins": r.get("wins", 0), "avg_pct": r.get("avg_pct"), "since": desk.get("since")}
    if src == "lst":
        n = int(st.get("trades") or 0)
        return {"n": n, "wins": round(float(st.get("winrate") or 0) * n), "avg_pct": st.get("avg_trade_pct"), "since": desk.get("since")}
    g = (st.get("by_grade") or {}).get(grade) or {}
    n = int(g.get("n") or 0)
    return {"n": n, "wins": round(float(g.get("winrate") or 0) * n), "avg_pct": None, "since": desk.get("since")}


def _scan_text(sc: dict, now: int | None, lang: str = DEFAULT) -> str | None:
    """the own desk's daily scan in one sentence: «Сегодня система проверила 40 монет по 8 условиям: …»"""
    if not sc.get("coins") or not sc.get("checks"):
        return None
    day = int(sc.get("day") or 0)
    today = (now or 0) // 86_400_000 * 86_400_000
    n, s = int(sc["coins"]), int(sc.get("signals") or 0)
    head = t(lang, "scan.head.today" if day == today else "scan.head.day", n=n, coins=plural(lang, n, "coin_acc"), k=sc["checks"],
             day=dt.datetime.fromtimestamp(day / 1000, dt.timezone.utc).strftime("%d.%m"))
    if sc.get("bull") is False:
        return head + t(lang, "scan.bear")
    return head + t(lang, "scan.signals", s=s, signals=plural(lang, s, "signal"), p=int(sc.get("passed") or 0))


def _scan(sc: dict | None, now: int | None, lang: str = DEFAULT) -> dict | None:
    """futures.scan — today's (the last) daily scan of «Сигналы CRYPTO NOVA», real counters only"""
    if not isinstance(sc, dict) or not sc.get("coins"):
        return None
    return {"coins": sc.get("coins"), "checks": sc.get("checks"), "passed": sc.get("passed"), "signals": sc.get("signals"),
            "opened": sc.get("opened"), "asof": sc.get("asof"), "day": sc.get("day"), "src": "own", "text": _scan_text(sc, now, lang)}


def _work(grade: str, f: dict, now: int | None, lang: str = DEFAULT) -> str | None:
    """one sentence with the real counters of the scan behind the signal; None when the counters were not recorded"""
    sc = f.get("scan") or {}
    if grade in ("P", "C"):
        return _scan_text(sc, now, lang)
    if grade == "K" and sc.get("coins"):
        n = sc["coins"]
        slots = f.get("slots")
        return t(lang, "work.K.slots" if slots else "work.K", n=n, alts=plural(lang, n, "altcoin_acc"), slots=slots)
    if grade in RULE_NAMES and sc.get("coins") and sc.get("rules"):
        n, r, k = sc["coins"], sc["rules"], int(sc.get("fired") or 0)
        return t(lang, "work.rule", n=n, coins=plural(lang, n, "coin_acc"), r=r, rules=plural(lang, r, "rule_by"),
                 rule=t(lang, f"rname.{grade}"), k=k)
    if grade == "LST" and f.get("watching"):
        w = int(f["watching"])
        return t(lang, "work.LST", w=w, futures=plural(lang, w, "futures_by"))
    return None


def _explain(o: dict, src: str, grade: str, entry, stop, tgt, desk: dict | None, now: int | None, lang: str = DEFAULT) -> dict:
    f = o.get("facts") if isinstance(o.get("facts"), dict) else {}
    checks = _checks(grade, f, str(o.get("symbol") or ""), lang) if f else []
    hist = HISTORY.get(grade)
    note = None
    if src == "own" and grade in ("P", "C") and o.get("vol") is None and o.get("opened"):
        # the volume mark and the daily top 40 came on 19.10 — a trade without a stored volume was opened before them
        d = dt.datetime.fromtimestamp(o["opened"] / 1000, _kyiv())
        note = t(lang, "note.v1", d=f"{d:%d.%m}")
    history = None
    if hist:
        history = {k: v for k, v in hist.items() if k != "years"}
        history.update(years_plus=t(lang, hist["years"]), text=t(lang, f"hist.{grade}.text"), live=_live(desk or {}, src, grade))
    return {"why_title": t(lang, f"title.{grade}") if grade in TITLES else None, "checks": checks or None, "history": history,
            "risk": _risk(o, grade, entry, stop, tgt, lang), "work": _work(grade, f, now, lang) if f else None, "note": note}


def _open_row(o: dict, src: str, desk: dict | None = None, now: int | None = None, lang: str = DEFAULT) -> dict:
    """one open trade of a mix desk in the app's words; the target: the desk's last take-profit or, for the rules that exit by
    rule / time, the same +1R/+2R/+3R ladder the Telegram message shows (its 3rd step)"""
    entry, price, stop = o.get("entry"), o.get("price"), o.get("stop_now") or o.get("stop")
    side = str(o.get("side") or "").upper()
    tps = o.get("tps") or []
    tgt = tps[-1] if tps else None
    grade = str(o.get("grade") or "")
    try:
        from .signal_feed import TIME_EXIT
    except Exception:  # noqa: BLE001
        TIME_EXIT = {"P", "C", "LST"}
    if (tgt is None or grade in TIME_EXIT) and entry and stop:
        try:
            from .signal_feed import targets
            tgt = targets({"entry": entry, "stop": stop, "side": side, "grade": grade, "tps": tps})[-1]
        except Exception:  # noqa: BLE001
            pass
    pct = None
    if entry and price:
        pct = (price / entry - 1) * 100 * (1 if side == "LONG" else -1)
    kind = str(o.get("kind") or "")
    why = t(lang, f"why.{kind}") if kind in WHY_KINDS else t(lang, f"why.{grade}") if grade in WHY_GRADES else t(lang, "why.default")
    if o.get("vol"):
        why += t(lang, "why.vol", vol=f"{float(o['vol']):.1f}")
    try:
        from .signal_feed import RULE_RU
    except Exception:  # noqa: BLE001
        RULE_RU = {}
    rule = (t(lang, f"rule.{grade}") if f"rule.{grade}" in T else RULE_RU[grade]) if RULE_RU.get(grade) else None
    return {"sym": o.get("symbol"), "side": side, "entry": entry, "price": price, "stop": stop, "target": tgt,
            "pnl_usd": _r(float(o.get("open_pnl_usd") or 0)), "pct": _r(pct, 1), "opened": o.get("opened"),
            "expires": o.get("expires"), "why": why, "src": src,
            "src_ru": rule or t(lang, f"src.{src}" if src in SRC_KEYS else "src.default"),
            **_explain(o, src, grade, entry, stop, tgt, desk, now, lang)}


def _kyiv():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Kyiv")
    except Exception:  # noqa: BLE001
        return dt.timezone(dt.timedelta(hours=3))


def _status(bal_open: int, closed: list, opened: list, lang: str = DEFAULT) -> str | None:
    """what the main strategy is doing, in one calm line (shown when it has nothing open)"""
    if bal_open:
        return None
    last = closed[0] if closed else None
    tail = ""
    if last and last.get("pnl_usd") is not None:
        d = dt.datetime.fromtimestamp((last.get("closed") or 0) / 1000, _kyiv())
        tail = t(lang, "status.last", sym=last["sym"], pnl=f"{'+' if last['pnl_usd'] >= 0 else '−'}${abs(last['pnl_usd']):.0f}",
                 d=f"{d:%d.%m}")
    other = len(opened)
    return t(lang, "status.main") + tail + (t(lang, "status.other", n=other, trades=plural(lang, other, "trade")) if other else "")


def _futures(paper: dict, now: int, lang: str = DEFAULT) -> dict:
    prof = paper.get("profiles") or {}
    curve = _avg_curve(prof)
    eq = [(prof.get(p) or {}) for p in PROFILES if prof.get(p)]
    usd = sum(float(p.get("equity", START)) + float(p.get("open_pnl_usd") or 0) - START for p in eq) / max(1, len(eq))
    day0 = int(dt.datetime.fromtimestamp(now / 1000, dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
               .timestamp() * 1000)
    before = [v for t_, v in curve if t_ < day0]
    today = usd - (before[-1] if before else 0.0)
    peak, dd = START, 0.0
    for _, v in curve:
        peak = max(peak, START + v)
        dd = min(dd, (START + v) / peak - 1)
    bal = prof.get("bal") or paper
    opened = []
    for src in _mix_keys():
        desk = bal if src == "bal" else prof.get(src) or {}
        for o in desk.get("open") or []:
            opened.append(_open_row(o, src, desk, now, lang))
    opened.sort(key=lambda o: -(o.get("opened") or 0))
    bal_open = sum(1 for o in opened if o["src"] == "bal")
    closed = []
    for c in sorted(bal.get("closed") or [], key=lambda c: c.get("closed") or 0, reverse=True)[:8]:
        closed.append({"sym": c.get("symbol"), "side": str(c.get("side") or "").upper(), "pnl_usd": _r(float(c.get("pnl_usd") or 0)),
                       "closed": c.get("closed")})
    st = bal.get("stats") or {}
    n_closed = int(st.get("trades") or len(bal.get("closed") or []))
    return {"usd": _r(usd), "pct": _r(usd / START * 100), "today_usd": _r(today), "trades": n_closed + bal_open,
            "status_ru": _status(bal_open, closed, opened, lang), "scan": _scan((prof.get("own") or {}).get("scan"), now, lang),
            "wins": round(float(st.get("winrate") or 0) * n_closed), "closed_n": n_closed, "worst_dd_pct": _r(dd * 100, 1),
            "open": opened, "closed": closed, "curve": [[t_, _r(v)] for t_, v in _sample(curve)],
            "about_ru": t(lang, "futures.about")}


_TRIM_PLAN = re.compile(r"^(?:Ядро|План): ([A-Z0-9]+) ([\d.]+)% счёта — больше (?:плана|плановых) ([\d.]+)%")
_ADD_CORE = re.compile(r"^Ядро: ([A-Z0-9]+) ([\d.]+)% счёта — доливаем до ([\d.]+)%")
_BUY_TABLE = re.compile(r"^Из таблицы «можно брать».*?([\d.]+)% счёта")


def _action_text(a: dict, lang: str) -> str | None:
    """the spot portfolio's last action: Russian = the portfolio's own sentence; other languages = a sentence of the catalog with
    the numbers read from it (the reasons are written by many rules in Russian), else the plain meaning of the action"""
    ru = a.get("why_ru")
    if lang == DEFAULT:
        return ru
    sym, act, txt = str(a.get("symbol") or ""), str(a.get("action") or "").upper(), str(ru or "")
    m = _TRIM_PLAN.match(txt)
    if m and act == "TRIM":
        return t(lang, "act.trim_plan", sym=m.group(1), w=m.group(2), p=m.group(3))
    m = _ADD_CORE.match(txt)
    if m and act == "ADD":
        return t(lang, "act.add_core", sym=m.group(1), w=m.group(2), p=m.group(3))
    m = _BUY_TABLE.match(txt)
    if m and act == "BUY":
        return t(lang, "act.buy_table", sym=sym, w=m.group(1))
    if not sym:
        return None
    return t(lang, f"act.{act}" if act in ("BUY", "ADD", "TRIM", "SELL") else "act.other", sym=sym)


def _money(x) -> str:
    x = float(x or 0)
    return f"${x / 1e9:.1f} млрд" if x >= 1e9 else f"${x / 1e6:.1f} млн" if x >= 1e6 else f"${x / 1e3:.0f} тыс." if x >= 1e3 else f"${x:.0f}"


def _coins(pf: dict, ex: dict, lang: str) -> list[dict]:
    """21.10 (owner's app spec §8 «Почему этот актив в портфеле?»): one entry per coin the portfolio holds — numbers in every
    language; the system's own explanations (written in Russian by the engine) only in the Russian feed, so other languages
    never show Russian text (the app falls back to its translated generic rule there)."""
    ideas = {p.get("symbol"): p for p in ex.get("ideas") or []}
    tok = ex.get("tok") or {}
    tokens, unlocks, profs = tok.get("tokens") or {}, tok.get("unlocks_next") or {}, ex.get("profiles") or {}
    out = []
    for p in pf.get("positions") or []:
        sym = p.get("symbol")
        if not sym:
            continue
        q, prof = ideas.get(sym) or {}, profs.get(sym) or {}
        entry, price = p.get("entry"), p.get("price")
        c = {"sym": sym, "name": p.get("name"), "weight": _r(float(p.get("weight") or 0) * 100, 1),
             "entry": entry and _r(entry, 6 if entry < 1 else 4 if entry < 100 else 2), "price": price,
             "pct": _r((price / entry - 1) * 100, 1) if entry and price else None,
             "stop": (q.get("levels") or {}).get("stop"), "image": p.get("image")}
        if lang == DEFAULT:
            reco = q.get("reco") or {}
            mark = " — ".join(x for x in (q.get("table_status_ru"), q.get("table_why_ru")) if x)
            hold = tokens.get(sym) or {}
            tparts = [prof.get("tokenomics_ru")] if prof.get("tokenomics_ru") else []
            if hold.get("holders_rev_30d_usd"):
                tparts.append(f"Держатели получают от протокола {_money(hold['holders_rev_30d_usd'])} за 30 дней (по DefiLlama).")
            un = unlocks.get(sym) or {}
            unlock = (f"Ближайший разлок {un['date']}: {un.get('pct_circ')}% монет в обращении"
                      + (f" (≈${un['usd_m']} млн)" if un.get("usd_m") else "") + ".") if un.get("date") else "Крупных разлоков в ближайшие 60 дней нет."
            c.update({"kind_ru": p.get("kind_ru"), "horizon_ru": p.get("horizon_ru"), "why_ru": q.get("reason"), "mark_ru": mark or None,
                      "reco_ru": (f"{reco.get('t')}: {reco.get('why')}" if reco.get("t") else None),
                      "token_ru": " ".join(tparts) or None, "unlock_ru": unlock, "risk_ru": prof.get("risks_ru"),
                      "about_ru": prof.get("what_ru"), "ahead_ru": prof.get("catalysts_ru")})
        out.append(c)
    return out


def _spot(pf: dict, lang: str = DEFAULT, ex: dict | None = None) -> dict:
    hist = pf.get("history") or []
    idx = pf.get("index")
    usd = None if idx is None else (float(idx) - 100.0) * START / 100.0
    pos = pf.get("positions") or []
    groups = []
    used = set()
    for key, syms in GROUPS:
        w = sum(float(p.get("weight") or 0) for p in pos if p.get("symbol") in syms)
        used |= syms
        groups.append({"name": t(lang, key), "weight": _r(w * 100, 1)})
    rest: dict[str, float] = {}
    for p in pos:
        if p.get("symbol") in used:
            continue
        g = KIND_KEY.get(str(p.get("kind_ru") or ""), "alloc.other")
        rest[g] = rest.get(g, 0.0) + float(p.get("weight") or 0)
    for g, w in sorted(rest.items(), key=lambda x: -x[1]):
        groups.append({"name": t(lang, g), "weight": _r(w * 100, 1)})
    groups.append({"name": t(lang, "alloc.cash"), "weight": _r(float(pf.get("cash_pct") or 0), 1)})
    movers = []
    for p in pos:
        if p.get("entry") and p.get("price"):
            movers.append({"sym": p["symbol"], "pct": _r((p["price"] / p["entry"] - 1) * 100, 1)})
    movers.sort(key=lambda m: -m["pct"])
    acts = pf.get("recent_actions") or []
    last = max(acts, key=lambda a: a.get("time") or 0) if acts else None
    return {"usd": _r(usd), "pct": _r(None if idx is None else float(idx) - 100.0), "index": idx, "n_coins": len(pos),
            "cash_pct": pf.get("cash_pct"), "alloc": groups, "movers": movers,
            "curve": [[h["t"], _r((float(h["v"]) - 100.0) * START / 100.0)] for h in _sample(hist) if h.get("v") is not None],
            "last_action": None if not last else {"t": last.get("time"), "text": _action_text(last, lang), "sym": last.get("symbol"),
                                                  "action": last.get("action")},
            "started": pf.get("started"), "coins": _coins(pf, ex or {}, lang)}


_FNG_RU = {"крайний страх": "Extreme Fear", "страх": "Fear", "нейтрально": "Neutral", "жадность": "Greed", "крайняя жадность": "Extreme Greed"}


def _fng_label(fng: dict, lang: str) -> str | None:
    """markets.json keeps the Russian word for today and the English one in the history — either way, the app's language"""
    raw = str(fng.get("label") or "")
    key = _FNG_RU.get(raw.lower(), raw)
    if not key:
        hist = fng.get("history") or []
        key = (hist[-1] or {}).get("label") if hist else None
    return t(lang, "fng." + key) if key and ("fng." + key) in T else (raw or None)


def _market(mk: dict, lang: str = DEFAULT) -> dict:
    top = {str(r.get("symbol") or "").upper(): r for r in mk.get("top") or []}
    btc = top.get("BTC") or {}
    fng = mk.get("fng") or {}
    return {"btc": {"price": btc.get("price"), "chg24": _r(btc.get("chg24"), 2)},
            "eth": {"price": (top.get("ETH") or {}).get("price"), "chg24": _r((top.get("ETH") or {}).get("chg24"), 2)},
            # «label» / «regime» keep the Russian words the released app compares against («рост» → green);
            # «label_text» / «regime_text» are the same words in the feed's language for the new app to show
            "fng": {"value": fng.get("value"), "label": fng.get("label"), "label_text": _fng_label(fng, lang)},
            "regime": None if mk.get("btc_bull") is None else ("рост" if mk.get("btc_bull") else "спад"),
            "regime_text": None if mk.get("btc_bull") is None else t(lang, "regime.up" if mk.get("btc_bull") else "regime.down")}


def _engine_facts(out: str) -> dict:
    """Real numbers for the welcome screen (never invented): the nightly lab and the signals checked by price."""
    al, sig, ac = _load(out, "autolab.json"), _load(out, "app_signals.json"), _load(out, "academy.json")
    return {"lab_tried": al.get("tried"), "lab_tested": al.get("tested"), "lab_passed": al.get("passed"),
            "lab_reliable": al.get("reliable"), "lab_markets": len(al.get("coverage") or {}) or None,
            "signals_checked": (sig.get("counts") or {}).get("resolved"),
            "lessons": sum(len(lv.get("lessons") or []) for lv in ac.get("levels") or [] if lv.get("level") != "L0") or None,   # what Learning shows (the app-intro level is reached from the tour)
            "cycle_sec": 60}


def _config(out: str) -> dict:
    """web/app_config.json (in git): what the app may change without a new App Store version — the exchange partner link"""
    c = _load(out, "app_config.json")
    ref = c.get("exchange_ref")
    # the public portfolio's caption per language {"ru": {"title", "sub", "note"}, …}; empty = the app's own honest default
    # (fill it only when it is true — e.g. the day the owner's real money is connected, with that day's date)
    pf = c.get("portfolio") if isinstance(c.get("portfolio"), dict) else {}
    pf = {lang: {k: str(v) for k, v in t.items() if k in ("title", "sub", "note") and v}
          for lang, t in pf.items() if isinstance(lang, str) and isinstance(t, dict)}
    return {"exchange_name": c.get("exchange_name") or "BingX",
            "exchange_ref": ref if isinstance(ref, str) and ref.startswith("https://") else None,
            "exchange_trade_url": c.get("exchange_trade_url"),
            "portfolio": {k: v for k, v in pf.items() if v}}


def btc_daily(conn: sqlite3.Connection | None, out: str) -> dict:
    from . import outlook
    closes = outlook.merge(outlook.seed_closes(), outlook.local_closes(), outlook.db_closes(conn),
                           outlook.terminal_closes(_load(out, "terminal.json") or None)).get("BTC") or {}
    start = (dt.date(2021, 1, 1) - dt.date(1970, 1, 1)).days
    days = sorted(d for d in closes if d >= start)
    if not days:
        return {}
    first, last = days[0], days[-1]
    return {"start_day": first, "closes": [_r(closes.get(d), 2) for d in range(first, last + 1)],
            "about_ru": "Дневные закрытия биткоина (UTC). day = номер дня от 1970-01-01; closes[i] — закрытие дня start_day + i."}


def _public_portfolio(out: str) -> dict | None:
    """The public portfolio of the app (owner 20.10: «нашу лучшую версию… полностью распределить $10 000, как на практике»):
    «CRYPTO NOVA Портфолио», its main «balanced» model (web/capital.json) — one $10 000 demo account spread over 7 sources
    by the regime allocator. curve = $ earned since the start. None while capital.json is missing → the app falls back to
    the two $10 000 demo accounts (futures + spot)."""
    c = _load(out, "capital.json")
    start, value, started = c.get("start_usd"), c.get("value_usd"), c.get("started")
    if not isinstance(start, (int, float)) or not isinstance(value, (int, float)) or not started or start <= 0:
        return None
    hist = [h for h in c.get("history") or [] if isinstance(h, list) and len(h) >= 2 and isinstance(h[1], (int, float))]
    step = max(1, len(hist) // 240)
    pts = hist[::step]
    if hist and pts[-1] is not hist[-1]:
        pts.append(hist[-1])                                   # always end on the latest value
    curve = [[int(h[0]), round(float(h[1]) - start, 2)] for h in pts]
    sleeves = [{"key": x.get("key"), "share": _r(x.get("share_now") if x.get("share_now") is not None else x.get("weight"), 4),
                "usd": _r(x.get("value_usd"), 2)} for x in c.get("sleeves") or [] if isinstance(x, dict) and x.get("key")]
    return {"model": "balanced", "start_usd": round(float(start), 2), "since": int(started), "value_usd": round(float(value), 2),
            "pnl_usd": round(float(value) - start, 2), "pnl_pct": _r(c.get("pnl_pct"), 2), "curve": curve, "sleeves": sleeves,
            "regime": (c.get("regime") or {}).get("key")}


def build(paper: dict, pf: dict, mk: dict, out: str, now: int, lang: str = DEFAULT, ex: dict | None = None) -> dict:
    """app_home in one language — the same numbers in every language, only the words differ"""
    fut, spot = _futures(paper, now, lang), _spot(pf, lang, ex)
    pub = _public_portfolio(out)
    total = pub["pnl_usd"] if pub else round((fut["usd"] or 0) + (spot["usd"] or 0), 2)
    return {"v": 1, "asof": now, "mode": "demo", "lang": lang, "since": paper.get("since") or "2026-10-17",
            "total": {"usd": total, "futures_usd": fut["usd"], "spot_usd": spot["usd"]}, "portfolio": pub,
            "sources": [{"key": "futures", "usd": fut["usd"]}, {"key": "spot", "usd": spot["usd"]},
                        {"key": "piggy", "usd": None}, {"key": "soon", "label": t(lang, "sources.soon")}],
            "market": _market(mk, lang), "futures": fut, "spot": spot, "engine": _engine_facts(out), "config": _config(out),
            "note_ru": t(lang, "home.note")}


def _write(out: str, name: str, doc: dict) -> None:
    """write next to the old file and rename: the API never reads a half-written feed"""
    path = os.path.join(out, name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def run(conn: sqlite3.Connection | None = None, out: str = "web", now: int | None = None) -> dict:
    """web/app_home.json (ru) + web/app_home.<lang>.json for the other languages; returns the Russian one"""
    now = now or int(time.time() * 1000)
    paper, pf, mk = _load(out, "paper.json"), _load(out, "app_portfolio.json"), _load(out, "markets.json")
    ex = {"ideas": ((_load(out, "portfolio.json").get("portfolios") or {}).get("ideas") or {}).get("positions") or [],
          "tok": _load(out, "tokenomics.json"), "profiles": _load(out, "coin_profiles.json").get("coins") or {}}
    docs = {lang: build(paper, pf, mk, out, now, lang, ex) for lang in LANGS}
    for lang in [x for x in LANGS if x != DEFAULT] + [DEFAULT]:          # Russian last: its mtime says «all languages written»
        _write(out, file_name("app_home", lang), docs[lang])
    if int(now / 60000) % 60 == 0 or not os.path.exists(os.path.join(out, "app_btc.json")):
        b = btc_daily(conn, out)
        if b:
            with open(os.path.join(out, "app_btc.json"), "w", encoding="utf-8") as f:
                json.dump(b, f, ensure_ascii=False, separators=(",", ":"))
    return docs[DEFAULT]
