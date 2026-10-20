"""«Сигналы CRYPTO NOVA» — our own daily signals with a steady flow, a $10 000 demo desk (19.10.2026, owner: «нам нужны
собственные сигналы … нужно движение, сигналы должны быть»; «почему мы не будем открывать ничего без резкого пролива?»).

Evidence (scripts/own_signals_study.py, Binance USDT-M daily candles 2022-01 → 2026-10, costs 0.14 % a trade + 0.02 % a day):
  • 42 rule variants (breakouts, pullbacks, weekly trend; both sides; four gates). No single family is in plus every year:
    the pullback lost in 2024, the trailing breakouts lost in 2025. Together they cover each other.
  • The chosen pair — P (pullback in a trend) + C (20-day breakout with a 3-ATR trailing exit), both only in the direction of
    our BTC regime — is in plus per trade in EVERY year 2022–2026 on the 40 coins it was picked on AND on 30 other coins never
    used to pick it (FET, RENDER, PEPE, WIF, …): +0.76 % and +0.75 % a trade, about 4 and 2.5 signals a day, 61 % winners,
    ≈ +0.4 % a trade better than a random entry of the same coin, side and holding time.
  • The edge is in the longs (+1.9…+2.3 % a trade); the shorts are about zero (+0.06 / −0.14 %) — they keep the flow going in a
    falling market and are sized the same, but they are the weak part.
  • Account (fixed risk per trade, at most 20 open): risk 0.5 % → 2022–2026 +31 / +28 / +1 / +18 / +36 % (40 coins) and
    +26 / +17 / +11 / +8 / +23 % (30 other coins), worst drawdown −46 % / −43 %, about +2 % a month on average.
    Risk 0.25 % → every year in plus on both sets, drawdown −26 %, about +1 % a month. Trades fall together in sharp market turns
    — that is where the drawdown comes from.

Round 5 (19.10, the live knobs, risk 0.5 % a trade): longs only and at most 4 NEW trades a day beat both sides with no cap —
    40 coins: +2.9 % a month on average, drawdown −24 % (both sides, no cap: +2.2 %, −46 %), 2023–2026 +34 / +59 / +4 / +10 %;
    30 other coins: +2.8 % a month, −18 %, +16 / +60 / +1 / +29 %. In a market that is not «рост» the desk waits in cash
    (2022: no trades). The shorts of the rules stay computed but are not traded.

Rounds 6–8 (19.10, owner: «что это за правило не больше 4 сделок в день? … главное качество, а не количество, но не упускать
    возможности»), on a DAILY mark-to-market account (open trades count in the drawdown), every signal day a candidate:
    • the cap of 4 took the first 4 coins of the list — no selection; at the same drawdown (−25 %) it earned no more than taking
      every signal or a random 4 (40 coins: +1.3 / +1.4 / +1.4 % a month) — a cap is only a smaller risk;
    • the quality mark is the VOLUME of the signal day: in dollars ≥ 1.5× its 30-day average. Taking every such signal, no
      daily cap: 40 coins +3.2 % a month, drawdown −20 %, average trade +4.5 % (was +1.6 %, −29 %, +1.4 %); 30 other coins
      +2.4 %, −16 %; all 70 +5.3 %, −28 %, 0.7 trades a day; at the same drawdown −25 %: +4.3 / +3.9 / +4.8 % a month
      (now: +1.3 / +2.4 / +1.3 %). Plus every year 2023–2026 on 40 and 70 coins. Ranking by the volume beat a random pick at
      every cap 2…6 on 40 and on 30 coins, with 20 / 30 / 50-day windows and base or quote volume alike.
    • the pullback without the volume mark is the weak part (70 coins: drawdown −45 %); the breakout with volume carries it.
    Rounds 9–10 (the honest test): EVERY Binance USDT-M perp, delisted ones included, and each day the coin set = the top N by
    30-day dollar volume ON THAT DAY. More coins is WORSE: top 40 → +2.1 % a month, drawdown −23 %; top 60 → −39 %; top 100 →
    −57 %; all → −72 %. Today's 70 (and today's 40) were flattered by survivors — about ×2…4 at the same drawdown. The honest
    winner: the top 40 by volume, the mark 1.5×, ≤ 40 open: +2.1 % a month, −23 %, average trade +3.1 %, 0.6 trades a day,
    2023–2026 +44 / +54 / +10 / +13 %; at the same drawdown −25 % +2.3 % a month vs +0.7 % for 4 random signals a day (≈ the
    old rule) and +0.8 % for every signal without the mark.
    Live since 19.10: each day the 40 BingX perps with the largest 30-day dollar volume (from a pool of the 90 largest by 24-hour
    volume + the 40 core coins), the volume mark, no daily cap, ≤ 40 open, gross ≤ 2× the account, risk 0.5 %.

Rules (checked once a day after the daily close, 00:00 UTC; entries and rule exits at the new day's open):
  P — long: close above a RISING 50-day average (vs 5 days ago) and RSI(2) < 10, BTC regime «рост» (BTC above its 200-day average
      and up over 60 days); out when the close is back above the 5-day average or after 10 days. Short: the mirror, regime not «рост».
  C — long: close above the high of the previous 20 days and above the 20-day average, regime «рост»; out when the close falls
      3 ATR(14) below the best close since entry, or after 14 days. Short: the mirror, regime not «рост».
  Stop for both: 2.5 ATR(14) from the entry, at least 3 % and at most 15 % — checked every run (≈ 30 min) against the live price.
  Volume: the signal day's volume in dollars (base volume × close) ≥ 1.5× its 30-day average, else no trade; when the room is
  short (40 open, gross 2× the account), the higher volume first.
  Size: 0.5 % of the account is lost at the stop (notional at most 25 % of the account), at most 40 open, one position per coin and
  rule. Fees 0.05 % + slippage 0.02 % per side. Writes profiles["own"] into web/paper.json. Stdlib only; fetchers injectable."""
from __future__ import annotations

import json
import os
import sqlite3
import time

from .history import thin
import urllib.request

COINS40 = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "DOT", "LTC", "BCH", "TRX", "NEAR", "ATOM", "ETC", "FIL",
           "APT", "ARB", "OP", "INJ", "SUI", "UNI", "AAVE", "XLM", "HBAR", "ICP", "RUNE", "STX", "GRT", "LDO", "SAND", "AXS", "CRV",
           "DYDX", "GALA", "TIA", "SEI", "WLD", "ENA"]
COINS30 = ["FET", "RENDER", "1000PEPE", "WIF", "JUP", "PYTH", "ONDO", "TAO", "1000BONK", "FLOKI", "ORDI", "JTO", "STRK", "PENDLE",
           "ENS", "IMX", "MANA", "EGLD", "THETA", "ALGO", "VET", "QNT", "KAVA", "ZIL", "MASK", "CHZ", "COMP", "SNX", "1INCH", "ZRO"]
COINS = COINS40                    # the core list: always fetched (the altseason gauge reads their closes); trading = TOP_N below
TOP_N, POOL_N = 40, 90             # 19.10 rounds 9–10: trade the 40 perps with the largest 30-day dollar volume (pool: 90 by 24 h)
NOT_TRADED = {"USDC", "FDUSD", "TUSD", "USDE", "DAI", "USDP", "PYUSD", "USD1", "BUSD", "XAUT", "PAXG"}
START = 10_000.0
RISK, NOTIONAL_CAP, MAX_OPEN, GROSS_CAP = 0.005, 0.25, 40, 2.0
LONG_ONLY = True                   # 19.10 round 5: the shorts of the rules ≈ 0 a trade
VOL_MIN, VOL_DAYS = 1.5, 30        # 19.10 rounds 6–8: the signal day's dollar volume ≥ 1.5× its 30-day average (see the docstring)
FEE = 0.0007                      # 0.05 % taker + 0.02 % slippage, per side
HOLD = {"P": 10, "C": 14}
DAY = 86_400_000
NAME = "Сигналы CRYPTO NOVA"
RULE_RU = {"P": "откат в тренде", "C": "пробой 20 дней"}
ABOUT = ("Наши собственные сигналы раз в день на 40 самых торгуемых монетах (список обновляется каждый день по объёму за 30 дней): "
         "покупаем, только когда рынок растёт, в остальное время ждём в долларах. "
         "Два правила: «откат в тренде» (монета в росте сделала короткий откат) и «пробой 20 дней» (цена вышла из 20-дневного "
         "диапазона, держим, пока тренд не сломается). Входим, только если в день сигнала объём торгов был в 1.5 раза выше "
         "обычного: за таким движением стоят деньги. Лимита сделок в день нет, до 40 открытых. Стоп 3–15% от входа, риск 0.5% "
         "счёта на сделку. Честная история 2023–2026 (все монеты Binance, включая снятые с торгов): в среднем около +2% в месяц, "
         "худшая просадка −23%, каждый год в плюсе. Демо $10 000.")


def _get(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 cryptonova/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fetch_daily(sym: str) -> list:
    """[[open ms, o, h, l, c, base volume], …] ascending, the last row is today's forming candle. BingX perp first, Binance spot
    mirror next (not for the 1000-unit perps — the spot price is on another scale)."""
    try:
        from ..bingx.rest import BingXRest
        rows = [[c.open_time, c.open, c.high, c.low, c.close, c.volume] for c in BingXRest().klines(f"{sym}-USDT", "1d", limit=400)]
        if len(rows) >= 120:
            return rows
    except Exception:  # noqa: BLE001
        pass
    if sym.startswith("1000"):
        return []
    d = _get(f"https://data-api.binance.vision/api/v3/klines?symbol={sym}USDT&interval=1d&limit=400")
    return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in d]


def fetch_volumes() -> dict:
    """{SYM: 24-hour volume in dollars} for the BingX USDT perps (one request) — the pool for the daily top 40."""
    d = _get(f"https://open-api.bingx.com/openApi/swap/v2/quote/ticker?timestamp={int(time.time() * 1000)}")
    out = {}
    for x in d.get("data") or []:
        s = str(x.get("symbol") or "")
        try:
            qv = float(x.get("quoteVolume") or 0) or float(x.get("volume") or 0) * float(x.get("lastPrice") or 0)
        except (TypeError, ValueError):
            continue
        if s.endswith("-USDT") and qv > 0:
            out[s[:-5]] = qv
    return out


def tradable(sym: str) -> bool:
    from .listing_desk import not_crypto
    return sym not in NOT_TRADED and not not_crypto(f"{sym}-USDT")


def dollar_vol30(K: list) -> float | None:
    """The 30-day average dollar volume (base volume × close) of the closed candles K."""
    if len(K) < VOL_DAYS or any(len(k) < 6 for k in K[-VOL_DAYS:]):
        return None
    return sum(k[5] * k[4] for k in K[-VOL_DAYS:]) / VOL_DAYS


def fetch_prices() -> dict:
    """{SYM: last price} for the BingX perps (one request)."""
    d = _get(f"https://open-api.bingx.com/openApi/swap/v2/quote/ticker?timestamp={int(time.time() * 1000)}")
    out = {}
    for x in d.get("data") or []:
        s = str(x.get("symbol") or "")
        if s.endswith("-USDT") and x.get("lastPrice") not in (None, "", "0"):
            out[s[:-5]] = float(x["lastPrice"])
    return out


# ------------------------------------------------------------------------------------------------ indicators (closed candles)
def _sma(xs: list, n: int, end: int) -> float | None:
    return sum(xs[end - n + 1:end + 1]) / n if end - n + 1 >= 0 else None


def _atr(K: list, end: int, n: int = 14) -> float | None:
    if end < n:
        return None
    trs = [max(K[i][2] - K[i][3], abs(K[i][2] - K[i - 1][4]), abs(K[i][3] - K[i - 1][4])) for i in range(end - n + 1, end + 1)]
    return sum(trs) / n


def _rsi2(c: list, end: int) -> float | None:
    if end < 2:
        return None
    d = [c[i] - c[i - 1] for i in (end - 1, end)]
    up = sum(x for x in d if x > 0) / 2
    dn = sum(-x for x in d if x < 0) / 2
    return 100.0 if dn == 0 else 100 - 100 / (1 + up / dn)


def regime(btc: list) -> bool | None:
    """BTC above its 200-day average and up over 60 days, on closed candles."""
    c = [k[4] for k in btc]
    e = len(c) - 1
    s200 = _sma(c, 200, e)
    if s200 is None or e < 60:
        return None
    return c[e] > s200 and c[e] > c[e - 60]


def vol_surge(K: list) -> float | None:
    """The last closed day's volume in dollars (base volume × close) ÷ its average over the last VOL_DAYS days (that day
    included). None without volume data."""
    if len(K) < VOL_DAYS or any(len(k) < 6 for k in K[-VOL_DAYS:]):
        return None
    dv = [k[5] * k[4] for k in K[-VOL_DAYS:]]
    avg = sum(dv) / VOL_DAYS
    return dv[-1] / avg if avg > 0 else None


def signals(K: list, bull: bool) -> list[tuple[str, int]]:
    """Signals at the last CLOSED candle of K → [(rule, side)], side 1 long / −1 short."""
    c = [k[4] for k in K]
    e = len(c) - 1
    out = []
    s50, s50_5, s20 = _sma(c, 50, e), _sma(c, 50, e - 5), _sma(c, 20, e)
    r2 = _rsi2(c, e)
    if s50 is not None and s50_5 is not None and r2 is not None:
        if bull and c[e] > s50 and s50 > s50_5 and r2 < 10:
            out.append(("P", 1))
        if not bull and c[e] < s50 and s50 < s50_5 and r2 > 90:
            out.append(("P", -1))
    if s20 is not None and e >= 21:
        hh = max(k[2] for k in K[e - 20:e])
        ll = min(k[3] for k in K[e - 20:e])
        if bull and c[e] > hh and c[e] > s20:
            out.append(("C", 1))
        if not bull and c[e] < ll and c[e] < s20:
            out.append(("C", -1))
    return out


def rule_exit(t: dict, K: list) -> bool:
    """The rule's own exit at the last closed candle (the stop is checked separately, every run)."""
    c = [k[4] for k in K]
    e = len(c) - 1
    sg = 1 if t["side"] == "LONG" else -1
    days = (K[e][0] - t["opened_day"]) // DAY + 1
    if t["rule"] == "P":
        s5 = _sma(c, 5, e)
        if s5 is not None and ((sg == 1 and c[e] > s5) or (sg == -1 and c[e] < s5)):
            return True
    else:
        a = _atr(K, e)
        best = t["best"]
        if a is not None and ((sg == 1 and c[e] < best - 3 * a) or (sg == -1 and c[e] > best + 3 * a)):
            return True
    return days >= HOLD[t["rule"]]


# ------------------------------------------------------------------------------------------------ storage
def ensure(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS own_state(k TEXT PRIMARY KEY, v TEXT)")
    conn.execute("""CREATE TABLE IF NOT EXISTS own_trades(id TEXT PRIMARY KEY, symbol TEXT, rule TEXT, side TEXT, opened INTEGER,
                    opened_day INTEGER, entry REAL, stop REAL, qty REAL, best REAL, closed INTEGER, exit REAL, exit_kind TEXT,
                    pnl_usd REAL)""")
    try:
        conn.execute("ALTER TABLE own_trades ADD COLUMN vol REAL")      # 19.10: the signal day's volume ÷ its 30-day average
    except sqlite3.OperationalError:
        pass
    conn.execute("CREATE TABLE IF NOT EXISTS own_equity(time INTEGER PRIMARY KEY, equity REAL)")
    conn.execute("CREATE TABLE IF NOT EXISTS own_closes(symbol TEXT, day INTEGER, close REAL, PRIMARY KEY(symbol, day))")
    conn.commit()


def _state(conn, k, d=None):
    r = conn.execute("SELECT v FROM own_state WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else d


def _set(conn, k, v):
    conn.execute("INSERT OR REPLACE INTO own_state VALUES(?, ?)", (k, json.dumps(v)))


def _close(conn, t: dict, px: float, kind: str, now: int) -> float:
    sg = 1 if t["side"] == "LONG" else -1
    pnl = sg * (px - t["entry"]) * t["qty"] - (t["entry"] + px) * t["qty"] * FEE
    conn.execute("UPDATE own_trades SET closed=?, exit=?, exit_kind=?, pnl_usd=? WHERE id=?", (now, px, kind, pnl, t["id"]))
    return pnl


def _open_rows(conn) -> list[dict]:
    keys = ("id", "symbol", "rule", "side", "opened", "opened_day", "entry", "stop", "qty", "best", "vol")
    return [dict(zip(keys, r)) for r in conn.execute(f"SELECT {', '.join(keys)} FROM own_trades WHERE closed IS NULL ORDER BY opened")]


def equity_closed(conn) -> float:
    return START + (conn.execute("SELECT COALESCE(SUM(pnl_usd), 0) FROM own_trades WHERE closed IS NOT NULL").fetchone()[0] or 0.0)


def _pool_candles(conn, day: int, daily, volumes) -> dict:
    """Daily candles (today's forming one last) of the core coins + the POOL_N largest BingX perps by 24-hour volume + the open
    trades' coins; only series that reach today and have ≥ 120 days."""
    try:
        vols = volumes() if volumes else {}
    except Exception:  # noqa: BLE001
        vols = {}
    ranked = [x for x, _v in sorted(vols.items(), key=lambda kv: -kv[1]) if tradable(x)][:POOL_N]
    pool = list(dict.fromkeys(COINS + ranked + [t["symbol"] for t in _open_rows(conn)]))
    K = {}
    for s in pool:
        try:
            rows = daily(s)
        except Exception:  # noqa: BLE001
            continue
        if rows and rows[-1][0] == day and len(rows) >= 120:
            K[s] = rows
    return K


def pool_features(K: dict) -> dict:
    """19.10 round 14: per pool coin (closed candles) — 30- and 180-day dollar volume, the 3-month change against BTC and the
    number of daily candles we have (≤ 400). spot_quality.py ranks the alts with it."""
    b = K.get("BTC")
    if not b or len(b) < 93:
        return {}
    bc = [r[4] for r in b[:-1]]
    out = {}
    for s, k in K.items():
        c = k[:-1]
        if not tradable(s) or len(c) < 181 or any(len(r) < 6 for r in c[-180:]):
            continue
        dv = [r[5] * r[4] for r in c]
        m3 = (c[-1][4] / c[-92][4]) / (bc[-1] / bc[-92]) - 1 if c[-92][4] > 0 and bc[-92] > 0 else None
        out[s] = {"dv30": round(sum(dv[-30:]) / 30), "dv180": round(sum(dv[-180:]) / 180), "m3": None if m3 is None else round(m3, 4),
                  "n": len(c)}
    return out


def pick_basket(K: dict) -> list:
    """19.10 round 12: the alt basket of the «ядро + альтсезон» model — the 10 alts with the largest 180-day dollar volume among
    coins with ≥ 1 year of candles (alt_portfolio.py reads it; the honest test picked it this way, not by hand)."""
    dv180 = {s: sum(r[5] * r[4] for r in k[:-1][-180:]) / 180 for s, k in K.items()
             if tradable(s) and s not in ("BTC", "ETH") and len(k) > 366 and all(len(r) >= 6 for r in k[-181:-1])}
    return sorted((s for s in dv180 if dv180[s] > 0), key=lambda s: -dv180[s])[:10]


def step(conn: sqlite3.Connection, now: int | None = None, daily=fetch_daily, prices=fetch_prices, volumes=None) -> dict:
    now = now or int(time.time() * 1000)
    ensure(conn)
    events = []
    day = now // DAY * DAY
    # 1. stops against the live price, every run
    try:
        px = prices() or {}
    except Exception as e:  # noqa: BLE001
        px, events = {}, [f"цены: {str(e)[:80]}"]
    for t in _open_rows(conn):
        p = px.get(t["symbol"])
        if p and ((t["side"] == "LONG" and p <= t["stop"]) or (t["side"] == "SHORT" and p >= t["stop"])):
            fill = min(p, t["stop"]) if t["side"] == "LONG" else max(p, t["stop"])        # a gap through the stop fills worse
            pnl = _close(conn, t, fill, "стоп", now)
            events.append(f"{t['symbol']} {RULE_RU[t['rule']]}: стоп, {pnl:+.0f}$")
    # 2. once a day after the daily close: rule exits, then new signals at the new day's open
    def _px(k, sym):
        """The day's open right after the close; later in the day (a missed run, a late deploy) the live price instead —
        a fill at an open hours old would be a free look-back."""
        return px[sym] if now - day > 3_600_000 and px.get(sym) else k[-1][1]
    daily_due = _state(conn, "day") != day and now - day >= 5 * 60_000
    first_pool = volumes and ((_state(conn, "alt_basket") is None and _state(conn, "basket_try") != day)
                              or (_state(conn, "pool_feats") is None and _state(conn, "feats_try") != day))
    if daily_due or first_pool:                       # the first basket / features must not wait for tomorrow (one try a day)
        _set(conn, "basket_try", day)
        _set(conn, "feats_try", day)
        K = _pool_candles(conn, day, daily, volumes)
        basket = pick_basket(K)
        if len(basket) == 10:
            _set(conn, "alt_basket", basket)          # the fallback basket; spot_quality.py replaces it with the quality pick
        feats = pool_features(K)
        if feats:
            _set(conn, "pool_feats", feats)
        conn.commit()
    if daily_due:
        # the day's trading set: the TOP_N by 30-day dollar volume among the fetched (closed candles; no volume → not ranked)
        dv = {s: dollar_vol30(k[:-1]) for s, k in K.items() if tradable(s)}
        universe = set(sorted((s for s in dv if dv[s]), key=lambda s: -dv[s])[:TOP_N])
        # closed daily closes for the altseason gauge (alt_portfolio.py) — kept ~220 days per coin
        conn.executemany("INSERT OR REPLACE INTO own_closes VALUES(?,?,?)", [(s, int(r[0]), float(r[4])) for s, k in K.items() for r in k[:-1][-220:]])
        conn.execute("DELETE FROM own_closes WHERE day < ?", (day - 240 * DAY,))
        if "BTC" in K:
            bull = regime(K["BTC"][:-1])
            for t in _open_rows(conn):
                k = K.get(t["symbol"])
                if not k:
                    continue
                closed = k[:-1]
                c = closed[-1][4]
                best = max(t["best"], c) if t["side"] == "LONG" else min(t["best"], c)
                conn.execute("UPDATE own_trades SET best=? WHERE id=?", (best, t["id"]))
                t["best"] = best
                if rule_exit(t, closed):
                    pnl = _close(conn, t, _px(k, t["symbol"]), "правило", now)
                    events.append(f"{t['symbol']} {RULE_RU[t['rule']]}: выход по правилу, {pnl:+.0f}$")
            if bull is not None:
                eq = equity_closed(conn)
                open_now = _open_rows(conn)
                have = {(t["symbol"], t["rule"]) for t in open_now}
                n_open = len(open_now)
                gross = sum(t["qty"] * t["entry"] for t in open_now)
                cands = []
                for s, k in K.items():
                    if s not in universe:
                        continue
                    closed = k[:-1]
                    for rule, sg in signals(closed, bull):
                        if (LONG_ONLY and sg != 1) or (s, rule) in have:
                            continue
                        v = vol_surge(closed)
                        if v is None or v < VOL_MIN:            # no volume behind the signal → no trade (rounds 6–8)
                            continue
                        cands.append((v, s, rule, sg))
                for v, s, rule, sg in sorted(cands, key=lambda x: -x[0]):   # the room is short → the higher volume first
                    if n_open >= MAX_OPEN:
                        break
                    k = K[s]
                    closed = k[:-1]
                    e = _px(k, s)
                    a = _atr(closed, len(closed) - 1)
                    if not a or e <= 0:
                        continue
                    dist = min(max(2.5 * a, 0.03 * e), 0.15 * e)
                    qty = min(eq * RISK / dist, eq * NOTIONAL_CAP / e)
                    if gross + qty * e > GROSS_CAP * eq:
                        continue
                    tid = f"OWN-{rule}-{s}-{day // DAY}"
                    conn.execute("INSERT OR IGNORE INTO own_trades(id, symbol, rule, side, opened, opened_day, entry, stop, qty, best, vol) "
                                 "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                 (tid, s, rule, "LONG" if sg == 1 else "SHORT", now, day, e, e - sg * dist, qty, closed[-1][4], round(v, 2)))
                    have.add((s, rule))
                    n_open += 1
                    gross += qty * e
                    events.append(f"{s} {RULE_RU[rule]}: {'покупка' if sg == 1 else 'шорт'} по {e:g}, объём ×{v:.1f}")
            _set(conn, "regime", bull)
            _set(conn, "day", day)
            _set(conn, "coins_seen", len(K))
            _set(conn, "universe", sorted(universe, key=lambda s: -(dv.get(s) or 0)))
    conn.commit()
    return {"events": events, "prices": px}


def doc(conn: sqlite3.Connection, now: int, px: dict, events: list) -> dict:
    eqc = equity_closed(conn)
    out_open, open_pnl = [], 0.0
    for t in _open_rows(conn):
        p = px.get(t["symbol"]) or t["entry"]
        sg = 1 if t["side"] == "LONG" else -1
        u = sg * (p - t["entry"]) * t["qty"] - t["entry"] * t["qty"] * FEE
        open_pnl += u
        out_open.append({"id": t["id"], "symbol": t["symbol"], "side": t["side"], "grade": t["rule"], "kind": "OWN_" + t["rule"],
                         "entry": t["entry"], "stop": t["stop"], "qty": t["qty"], "qty_left": t["qty"], "opened": t["opened"], "pnl_usd": 0.0,
                         "price": p, "open_pnl_usd": round(u, 2), "vol": t.get("vol"),
                         "reason": f"{RULE_RU[t['rule']]}" + (f", объём ×{t['vol']:.1f} к среднему" if t.get("vol") else "")
                                   + f": стоп {abs(t['stop'] / t['entry'] - 1) * 100:.1f}%, выход по правилу или через {HOLD[t['rule']]} дней"})
    eq = eqc + open_pnl
    conn.execute("INSERT OR REPLACE INTO own_equity VALUES(?, ?)", (now, round(eq, 2)))
    conn.commit()
    eqh = [{"time": a, "equity": b} for a, b in conn.execute("SELECT time, equity FROM own_equity ORDER BY time")]
    peak, dd = START, 0.0
    for e in eqh:
        peak = max(peak, e["equity"])
        dd = max(dd, (peak - e["equity"]) / peak)
    keys = ("id", "symbol", "rule", "side", "opened", "entry", "closed", "exit", "exit_kind", "pnl_usd", "qty")
    closed = [dict(zip(keys, r)) for r in conn.execute(f"SELECT {', '.join(keys)} FROM own_trades WHERE closed IS NOT NULL ORDER BY closed DESC LIMIT 60")]
    for t in closed:
        t.update({"grade": t.pop("rule"), "net_usd": round(t["pnl_usd"], 2)})
    n = conn.execute("SELECT COUNT(*) FROM own_trades WHERE closed IS NOT NULL").fetchone()[0]
    wins = conn.execute("SELECT COUNT(*) FROM own_trades WHERE closed IS NOT NULL AND pnl_usd > 0").fetchone()[0]
    avg = conn.execute("SELECT AVG(pnl_usd / (qty * entry)) FROM own_trades WHERE closed IS NOT NULL AND qty > 0").fetchone()[0]
    first = conn.execute("SELECT MIN(time) FROM own_equity").fetchone()[0] or now
    bull = _state(conn, "regime")
    return {"asof": now, "profile": "own", "name": NAME, "about": ABOUT, "start_equity": START, "since": first, "reset": None,
            "equity": round(eqc, 2), "equity_mtm": round(eq, 2), "pnl_usd": round(eq - START, 2), "pnl_pct": round((eq / START - 1) * 100, 2),
            "open_pnl_usd": round(open_pnl, 2), "open": out_open, "pending": [], "closed": closed,
            "stats": {"trades": n, "winrate": round(wins / n, 3) if n else None, "avg_trade_pct": round(avg * 100, 2) if avg is not None else None,
                      "max_dd_pct": round(dd * 100, 2), "best": 0, "worst": 0},
            "equity_history": thin(eqh), "events": events[:20], "error": None,
            "regime_ru": None if bull is None else ("рост: покупаем" if bull else "не рост: ждём в долларах"),
            "rules": "откат в тренде и пробой 20 дней на 40 самых торгуемых монетах, только покупки в режиме «рост» и только при объёме дня от 1.5× к среднему за 30 дней; стоп 2.5 ATR (3–15%), риск 0.5% счёта, до 40 открытых"}


def run(conn: sqlite3.Connection, out: str = "web", now: int | None = None, daily=fetch_daily, prices=fetch_prices, volumes=None) -> dict:
    now = now or int(time.time() * 1000)
    if volumes is None and daily is fetch_daily:      # live: the BingX 24-hour volumes; tests inject candles → the core list only
        volumes = fetch_volumes
    r = step(conn, now, daily, prices, volumes)
    d = doc(conn, now, r["prices"], r["events"])
    p = os.path.join(out, "paper.json")
    try:
        paper = json.load(open(p, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return d
    paper.setdefault("profiles", {})["own"] = d
    pl = [x for x in paper.get("profile_list") or [] if x.get("key") != "own"]
    pl.append({"key": "own", "name": NAME, "about": ABOUT, "risk": 2, "classes": ["P", "C"], "max_open": MAX_OPEN})
    paper["profile_list"] = pl
    with open(p, "w", encoding="utf-8") as f:
        json.dump(paper, f, ensure_ascii=False)
    return d
