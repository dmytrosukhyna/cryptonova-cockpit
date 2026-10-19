"""CRYPTO NOVΛ app signals — tracking, honest outcomes, strategy profile and improvement rules → web/app_signals.json.

Pipeline on the owner's side: Telegram «Ai Golden Crypto (Auto Bot)» → Cornix → orders on his BingX → TradeReader → app backend.
The app reads the signals from a PUBLIC JSON API (no auth headers; see the app repo network/…/utils/URLs.kt, dto/signal/*.kt):
  GET https://app.crypto-capital.app/api/signals/active      GET https://app.crypto-capital.app/api/signals/history
Response = CommonResponse {code (1 ok / 0 error), body, error_message, date}; body = list of signals in snake_case:
signal_id, name, entry_point (string: one price or a range «a - b»), stop_lost, actual_price, actual_precent, leverage_x, date (ms),
position_side, targets [{level, price, precent, is_completed}], icon_url, is_vip. The parser is tolerant (camelCase, numbers as
strings with spaces/commas, ranges, LONG/Long/buy, s/ms/ISO dates, body wrapped in a dict) and lists unknown shapes in notes_ru.

Each run:
 1. both lists are fetched and upserted into agents.sqlite `app_signals` (never deleted — the API may only return a recent window,
    our table becomes the full history). A signal that leaves /active is marked closed.
 2. Outcome engine, independent of the app's is_completed flags: 15m perpetual candles from the first FULL 15m candle after the
    signal time (no look into prices before the signal) — BingX swap (cnq.bingx.rest), fallback OKX swap, then Binance spot mirror.
    The path starts at the candle containing the signal time: 1m candles for the first ~3 hours, 15m after that.
    Candles live in `app_signal_candles` (shared per coin, closed candles only) and are fetched only while a signal is unfinished:
    the 1m window once, the 15m path until it is resolved plus 7 days after a stop (did the price come back to TP1?). A signal's
    15m path only uses candles up to its own verified coverage (`path_until`), so candles cached for later signals never stand in
    for missing ones. Finished signals keep their result in the row and their candles are pruned (the DB is committed to git).
      entry: within 0.6 % of the signal minute's open or close → market order (Cornix posts it at once): filled at the entry at the
      signal time; farther → limit order: one price → a candle touches it; range → a candle touches any price in it (fill at the
      candle open if it opened inside the range, else at the near edge). Not touched within 48 h, or TP1 / stop reached first →
      «не вошёл».
      after the fill: stop vs TP1 — which came first, the farthest TP before the stop, time to TP1, max favourable / adverse move.
      One candle touching both the stop and a target → the stop counts first (cautious). For a limit fill no TP on the fill candle
      unless it opened at the entry. No exit after 14 days → closed at the market («по времени»).
    The app's targets[].is_completed (real BingX orders via TradeReader) are only used as a cross-check (stats.app_flags).
    Exit policies, in % of price (net of 0.05 % fee per side + 0.02 % slippage = 0.12 % per trade) and in R (R = entry→stop):
      ladder_be  MAIN (owner 18.10.2026): equal parts at each TP, stop → entry after TP1 — summary, splits and rules use it;
      app        «как в приложении» — the farthest TP reached is the result (the app's showcase, not executable: nobody knows in
                 advance which TP will be the last);
      tp1        everything at TP1;   ladder  equal parts at each TP, stop stays;   last  everything at the last TP or the stop.
    Leverage only multiplies the % (both reported); a leveraged loss is capped at −100 % of the margin (≈ liquidation at a move of
    100 / leverage − 0.5 %).
 3. Strategy profile from candles at signal time: direction vs 4h trend (EMA50/EMA200), RSI14 1h/4h, distance from the 24 h / 7 d
    high and low, last 4 h move, ATR %, BTC 4h trend, hour / weekday, coin, leverage, TP / stop distances and R:R → entry type
    («покупка отката в восходящем тренде», «шорт на перекупленности», …) and simple splits of winners vs losers.
 4. Proposals: a handful of explainable rules evaluated on the stored history, before / after with sample sizes; anything with fewer
    than 30 resolved signals is «рано судить». No number is invented — every figure comes from the stored rows. Among them the
    walk-forward coin filter: skip a coin when ≥ 6 of its signals resolved BEFORE this one and ≥ 50 % were a stop without TP.
 5. Two demo accounts (`accounts`): «как приходят сигналы» (ladder, no filter) and «с улучшениями» (ladder_be + coin filter);
    $10 000, margin 5 % of equity at entry, the signal's leverage capped at 20×, the sim's fees, compounded in closing order;
    views `since_live` (signals from 18.10.2026 00:00 Kyiv — the forward test) and `backfill` (from the first stored signal).
Stdlib only; the API fetch and the candle fetch are injectable (tests run without network)."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import statistics
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

API_BASE = "https://app.crypto-capital.app"
API_ACTIVE = API_BASE + "/api/signals/active"
API_HISTORY = API_BASE + "/api/signals/history"
# The Android app uses Ktor's Android engine (HttpURLConnection) → the system Dalvik user agent; JSON content type like ApiClient.
APP_HEADERS = {"User-Agent": "Dalvik/2.1.0 (Linux; U; Android 14; Pixel 7 Build/UQ1A.240205.004)",
               "Accept": "application/json", "Content-Type": "application/json", "Accept-Charset": "UTF-8"}
BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Mobile Safari/537.36",
              "Accept": "application/json"}
PLAIN_UA = {"User-Agent": "Mozilla/5.0 cryptonova/1.0"}

M1, M15, H1, H4, DAY = 60_000, 900_000, 3_600_000, 14_400_000, 86_400_000
IV_MS = {"1m": M1, "15m": M15, "1h": H1, "4h": H4}
M1_WINDOW_MS = 3 * H1       # the first 3 hours after the signal are checked on 1m candles, 15m after that
BACKLOG_BIG, BACKLOG_REQUESTS, BACKLOG_SECONDS = 60, 650, 330
MARKET_TOL_PCT = 0.6        # entry within 0.6 % of the price at the signal minute = market order (filled at the signal time)
FILL_WINDOW_MS = 48 * H1
MAX_HOLD_MS = 14 * DAY
AFTER_MS = 7 * DAY
FEE_PCT, SLIP_PCT = 0.05, 0.02
COST_PCT = 2 * FEE_PCT + SLIP_PCT
MIN_SAMPLE = 30
LEV_CAP = 10
SIM_VERSION = 4             # 2: market fill at the signal minute + 1m path for the first 3 h; strict path coverage; 3: what-if variants; 4: what-if close times (reverse account)
WHATIF = {   # «что если» on the same price path: the owner asked whether flipping the signal or changing the stop would work
    "rev": "Разворот: та же сделка в обратную сторону (стоп и цели зеркально)",
    "wide15": "Стоп в 1.5 раза дальше, цели те же",
    "wide2": "Стоп в 2 раза дальше, цели те же",
    "tp3": "Цели только до TP3: выход равными частями по TP1–TP3",
}
MAKER_SAVING_PCT = 0.08      # limit (maker) orders instead of market: 0.02 % × 2 fees and no slippage vs 0.05 % × 2 + 0.02 %
MAX_FETCH_FAIL = 6

POLICIES = [   # key, name, executable — the first one is the main policy (owner 18.10.2026), the rest are for comparison
    ("ladder_be", "Равными частями на каждом TP, после TP1 стоп в точку входа", True),
    ("app", "Как в приложении: итог = самый дальний достигнутый TP", False),
    ("tp1", "Всё закрыть на TP1", True),
    ("ladder", "Равными частями на каждом TP, стоп на месте", True),
    ("last", "Всё держать до последнего TP или стопа", True),
]
MAIN_POLICY = BASELINE = "ladder_be"   # summary, splits, filter rules and the panel use it

# Walk-forward coin filter: skip a coin when ≥ 6 of its signals resolved BEFORE this one and ≥ 50 % of them were a stop without TP
COIN_FILTER_MIN, COIN_FILTER_SHARE = 6, 0.5
# Demo accounts
ACCOUNT_START, ACCOUNT_MARGIN_SHARE, ACCOUNT_LEV_CAP = 10_000.0, 0.05, 20
LIVE_START_MS = int(datetime(2026, 10, 18, tzinfo=timezone(timedelta(hours=3))).timestamp() * 1000)   # 18.10.2026 00:00 Kyiv (EEST)
ACCOUNTS = [   # key, name, rules, policy, coin filter
    ("as_is", "Как приходят сигналы", "равными частями по целям, стоп на месте", "ladder", False),
    ("improved", "С улучшениями", "равными частями по целям + стоп в безубыток после TP1 + фильтр монет", "ladder_be", True),
    # 18.10: on 315 signals the flipped trade (opposite side, stop and targets mirrored) earned +0.33 % per trade after fees,
    # positive in both halves of the month — the owner's idea; tested live on demo before anything else
    ("reverse", "Разворот (эксперимент)", "сделка в обратную сторону: стоп и цели зеркально, равными частями по целям + стоп в безубыток после TP1", "rev:ladder_be", False),
    # 18.10 (owner: «пробовать их стратегию наоборот … не во всех случаях, грамотно»): over 317 signals the flip of their LONGs
    # (we go short) was positive in BOTH halves of the month (+0.30 / +0.48 % per trade), the flip of their SHORTs turned
    # negative in the second half (+0.75 → −0.24 %). This is the version published to the app.
    # 18.10 (owner: «не бездумно разворачивать — их удачные идеи использовать, неудачные превращать в удачные»): a signal that
    # goes WITH Bitcoin's last closed 4-hour candle is taken as is, one that goes AGAINST it is flipped; both held to the last
    # target or the stop. Set in advance (sign only, no threshold); on 315 signals positive in every quarter of the month
    # (+0.64 / +0.56 / +0.27 / +0.24 % per trade), while «flip everything» fell to −0.02 % in the last quarter.
    ("smart", "Умный разворот", "по ходу последней 4-часовой свечи BTC — как есть, против неё — наоборот; всё до последней цели или стопа", "smart:last", False),
    ("reverse_long", "Разворот только их лонгов", "только их лонги — в шорт, стоп и цели зеркально, равными частями по целям + стоп в безубыток после TP1; их шорты пропускаем", "rev_long:ladder_be", False),
    # 19.10 (owner: «голден сигналс — с ними нужно хорошо поработать»). On the 322 signals of 18.09–18.10 the EXIT decides more
    # than the direction: held to the last target or the stop, the signals as they come are +0.20 % a trade (positive in 4 of 5
    # weeks) while the ladder exits lose; and the mirrored trade is +0.53 %. Both sides win because the stop is tight (≈ 1.1 %)
    # and the last target far (≈ 2.8 %), and a big move often follows a signal — the edge is in the volatility after the signal
    # more than in its direction. Two forward tests:
    ("as_last", "Как приходят · до последней цели", "сигнал как есть, всё держим до последней цели или стопа", "last", False),
    ("both", "Вилка: обе стороны", "сигнал как есть и зеркальная сделка одновременно, по половине маржи; обе до последней цели или стопа (на истории +0.37% на сигнал, 4 недели из 5 в плюсе)", "both:last", False),
]
WEEKDAYS_RU = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

KNOWN_KEYS = {"signal_id", "name", "entry_point", "stop_lost", "actual_price", "actual_precent", "leverage_x", "date",
              "position_side", "targets", "icon_url", "is_vip"}
KNOWN_TARGET_KEYS = {"level", "price", "precent", "is_completed"}


# ------------------------------------------------------------------------------------------------------------ parsing
def _snake(k: str) -> str:
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", str(k)).lower()


def _g(d: dict, *keys):
    """First non-empty value among keys, matching snake_case / camelCase / any case."""
    if not isinstance(d, dict):
        return None
    norm = {_snake(k): v for k, v in d.items()}
    for k in keys:
        v = norm.get(_snake(k))
        if v is not None and v != "":
            return v
    return None


def parse_num(x) -> float | None:
    """'65000', '65 000,5', '1,234.5', '0,1234', '1.234.567', '$0.52', '12%', 0.5 → float; None when not a number."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x) if math.isfinite(x) else None
    s = str(x).strip()
    for ch in (" ", " ", " ", " ", "'", "$", "%", "−"):
        s = s.replace(ch, "-" if ch == "−" else "")
    s = re.sub(r"(?i)usdt|usd", "", s).lstrip("+")
    if not s:
        return None
    try:
        v = float(s)
        return v if math.isfinite(v) else None
    except ValueError:
        pass
    neg = s.startswith("-")
    s = s.lstrip("-")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) > 2:
            s = "".join(parts)
        else:
            ip, fp = parts
            thousands = len(fp) == 3 and ip not in ("", "0") and ip.isdigit() and fp.isdigit()
            s = ip + fp if thousands else ip + "." + fp
    elif s.count(".") > 1:
        s = s.replace(".", "")
    m = re.match(r"^\d*\.?\d+(?:[eE][-+]?\d+)?$", s)
    if not m:
        return None
    v = float(s)
    return -v if neg else v


_RANGE_SPLIT = re.compile(r"\s*(?:[–—~/;|]|\s-\s|\bto\b|\.{2,}|,\s+)\s*|(?<=\d)-(?=\d)")


def parse_entry(x) -> tuple[float, float] | None:
    """One price → (p, p); a range «a - b», «a–b», «a / b», [a, b], {from, to} → (min, max). None when not parsable."""
    if x is None:
        return None
    if isinstance(x, (list, tuple)):
        vals = [v for v in (parse_num(i) if not isinstance(i, dict) else parse_num(_g(i, "price", "value")) for i in x) if v and v > 0]
        return (min(vals), max(vals)) if vals else None
    if isinstance(x, dict):
        vals = [v for v in (parse_num(_g(x, k)) for k in ("from", "to", "min", "max", "low", "high", "price", "value")) if v and v > 0]
        return (min(vals), max(vals)) if vals else None
    v = parse_num(x)
    if v is not None and v > 0:
        return (v, v)
    parts = [p for p in _RANGE_SPLIT.split(str(x).strip()) if p and p.strip()]
    vals = [v for v in (parse_num(p) for p in parts) if v is not None and v > 0]
    return (min(vals), max(vals)) if vals else None


def parse_side(x) -> str | None:
    s = str(x or "").strip().upper()
    if not s:
        return None
    if "SHORT" in s or s in ("SELL", "S", "ASK") or "ПРОДА" in s or "ШОРТ" in s:
        return "SHORT"
    if "LONG" in s or s in ("BUY", "L", "BID") or "ПОКУП" in s or "ЛОНГ" in s:
        return "LONG"
    return None


def parse_symbol(name) -> str | None:
    """'BTC/USDT', 'BTCUSDT', 'BTC-USDT', '#BTC/USDT', 'btc_usdt', 'BTC/USDT.P', 'BTCUSDT PERP', 'BTC' → 'BTC-USDT' (BingX perp)."""
    s = str(name or "").upper().strip()
    s = re.sub(r"[#$\s]", "", s)
    for _ in range(2):
        s = re.sub(r"(\.P|PERP|PERPETUAL|-SWAP|:USDT|\.PERP|SWAP)$", "", s)
    base = None
    for sep in ("/", "-", "_", ":"):
        if sep in s:
            base = s.split(sep, 1)[0]
            break
    if base is None:
        base = s
        for q in ("USDT", "USDC", "BUSD", "USD"):
            if s.endswith(q) and len(s) > len(q):
                base = s[: -len(q)]
                break
    base = re.sub(r"[^A-Z0-9]", "", base)
    return f"{base}-USDT" if base else None


def parse_time(x) -> int | None:
    """ms / s / µs epoch (number or digit string) or ISO-8601 → epoch ms."""
    if x is None or isinstance(x, bool):
        return None
    v = None
    if isinstance(x, (int, float)):
        v = float(x)
    else:
        s = str(x).strip()
        if re.fullmatch(r"\d+(\.\d+)?", s):
            v = float(s)
        else:
            try:
                d = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T", 1))
                if d.tzinfo is None:
                    d = d.replace(tzinfo=timezone.utc)
                return int(d.timestamp() * 1000)
            except ValueError:
                return None
    if v is None or v <= 0:
        return None
    if v > 1e17:
        v /= 1e6        # ns
    elif v > 1e14:
        v /= 1e3        # µs
    elif v < 1e11:
        v *= 1e3        # seconds
    return int(v)


def parse_leverage(x) -> float:
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return float(x) if x >= 1 else 1.0
    m = re.search(r"\d+(?:[.,]\d+)?", str(x or ""))
    v = float(m.group(0).replace(",", ".")) if m else 1.0
    return v if v >= 1 else 1.0


def _parse_targets(raw, problems: list) -> list[dict]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = [p for p in re.split(r"[;,|\s]+", raw) if p]
    if isinstance(raw, dict):
        raw = [dict(v, level=v.get("level", k)) if isinstance(v, dict) else {"level": k, "price": v} for k, v in raw.items()]
    out = []
    for i, t in enumerate(raw if isinstance(raw, list) else []):
        if isinstance(t, dict):
            lvl = parse_num(_g(t, "level", "lvl", "index", "number", "tp"))
            px = parse_num(_g(t, "price", "value", "target", "tp_price"))
            pct = parse_num(_g(t, "precent", "percent", "pct", "profit"))
            done = _g(t, "is_completed", "completed", "done", "hit", "reached", "is_reached")
            done = str(done).strip().lower() in ("true", "1", "yes") if not isinstance(done, bool) else done
            extra = set(_snake(k) for k in t) - KNOWN_TARGET_KEYS
            if extra:
                problems.append("target_keys:" + ",".join(sorted(extra)))
        else:
            lvl, px, pct, done = None, parse_num(t), None, False
        if px is None or px <= 0:
            problems.append("target_price")
            continue
        out.append({"level": int(lvl) if lvl is not None else i + 1, "price": px, "app_pct": pct, "done": bool(done)})
    out.sort(key=lambda r: r["level"])
    return out


def normalize(raw: dict) -> dict:
    """One API item → plain dict; `problems` lists what could not be read (never a guess)."""
    problems: list[str] = []
    if not isinstance(raw, dict):
        return {"signal_id": None, "problems": ["not_a_dict"]}
    name = _g(raw, "name", "pair", "symbol", "coin", "ticker")
    symbol = parse_symbol(name)
    entry = parse_entry(_g(raw, "entry_point", "entry", "entry_price", "entry_zone", "entries"))
    sl = parse_num(_g(raw, "stop_lost", "stop_loss", "stoploss", "sl", "stop"))
    side_raw = _g(raw, "position_side", "side", "direction", "type", "position")
    side = parse_side(side_raw)
    t = parse_time(_g(raw, "date", "created_at", "time", "timestamp", "created"))
    targets = _parse_targets(_g(raw, "targets", "take_profits", "tps", "take_profit"), problems)
    lev = parse_leverage(_g(raw, "leverage_x", "leverage", "lev"))
    sid = _g(raw, "signal_id", "id", "signalid", "uuid")
    if sid is None:
        sid = "h" + hashlib.sha1(f"{name}|{t}|{_g(raw, 'entry_point', 'entry')}".encode()).hexdigest()[:12]
        problems.append("no_id")
    for what, v in (("name", symbol), ("entry", entry), ("stop", sl), ("date", t)):
        if not v:
            problems.append(what)
    if not targets:
        problems.append("targets")
    if not side and side_raw is not None:
        problems.append(f"side:{str(side_raw)[:12]}")
    # Geometry: LONG has stop < entry < targets, SHORT the opposite. Trust the prices when the label is missing or contradicts them.
    if entry and sl and targets:
        tp1 = targets[0]["price"]
        geo = "LONG" if sl < entry[0] and tp1 > entry[1] else "SHORT" if sl > entry[1] and tp1 < entry[0] else None
        if geo is None:
            problems.append("geometry")
        elif side and side != geo:
            problems.append("side_conflict")
            side = geo
        elif not side:
            side = geo
    if not side:
        problems.append("side")
    extra = set(_snake(k) for k in raw) - KNOWN_KEYS
    vip = _g(raw, "is_vip", "vip")
    done_levels = [x["level"] for x in targets if x["done"]]
    return {"signal_id": str(sid), "name": str(name) if name is not None else None, "symbol": symbol,
            "coin": symbol.split("-")[0] if symbol else None, "side": side,
            "entry_lo": entry[0] if entry else None, "entry_hi": entry[1] if entry else None, "sl": sl, "leverage": lev,
            "t": t, "targets": targets, "is_vip": bool(vip) if isinstance(vip, bool) else str(vip).lower() in ("true", "1"),
            "app_best_tp": max(done_levels) if done_levels else 0,
            "actual_price": parse_num(_g(raw, "actual_price", "current_price")),
            "actual_pct": parse_num(_g(raw, "actual_precent", "actual_percent", "pnl")),
            "problems": problems, "extra_keys": sorted(extra)}


def extract_list(resp) -> tuple[list | None, str | None]:
    """CommonResponse {code, body, error_message} / a plain list / body wrapped in a dict → (items, error)."""
    if isinstance(resp, list):
        return resp, None
    if not isinstance(resp, dict):
        return None, f"ответ не JSON-объект ({type(resp).__name__})"
    body = None
    for k in ("body", "data", "result", "signals", "items"):
        if k in resp and resp[k] is not None:
            body = resp[k]
            break
    if isinstance(body, dict):
        for k in ("signals", "items", "list", "data", "content", "rows", "results"):
            if isinstance(body.get(k), list):
                body = body[k]
                break
    if isinstance(body, list):
        return body, None
    err = _g(resp, "error_message", "message", "msg", "error")
    return None, f"code={resp.get('code')} {str(err)[:120] if err else 'нет списка в ответе'}"


# ------------------------------------------------------------------------------------------------------------ network
def _get_api(url: str, timeout: int = 20):
    last = None
    for headers in (APP_HEADERS, BROWSER_UA):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (401, 403, 406, 429):
                raise
    raise last  # type: ignore[misc]


def _get_plain(url: str, timeout: int = 10):
    with urllib.request.urlopen(urllib.request.Request(url, headers=PLAIN_UA), timeout=timeout) as r:
        return json.loads(r.read().decode())


class KlineSource:
    """klines(symbol 'BTC-USDT', interval '15m'|'1h'|'4h', start_ms, end_ms) → [[t, o, h, l, c], …] ascending.
    BingX swap first (cnq.bingx.rest, 1 request/s), then OKX swap, then the Binance spot mirror. `used` counts answers per source."""

    def __init__(self, get_json=None, bingx=None):
        self.get_json = get_json or _get_plain
        self._bx = bingx
        self.used: dict[str, int] = {}
        self.errors: list[str] = []

    def _bingx(self, symbol, iv, start, end):
        if self._bx is None:
            from ..bingx.rest import BingXRest
            self._bx = BingXRest()
        return [[c.open_time, c.open, c.high, c.low, c.close] for c in self._bx.klines(symbol, iv, start_ms=start, end_ms=end, limit=1440)]

    @staticmethod
    def _base_scale(symbol):
        base = symbol.split("-")[0]
        if base.startswith("1000") and len(base) > 4:   # BingX 1000PEPE = 1000 × PEPE price elsewhere
            return base[4:], 1000.0
        return base, 1.0

    def _okx(self, symbol, iv, start, end):
        base, k = self._base_scale(symbol)
        bar = {"1m": "1m", "15m": "15m", "1h": "1H", "4h": "4H"}[iv]
        ms = IV_MS[iv]
        out, t, pages = {}, start, 0
        while t <= end and pages < 15:
            hi = min(end, t + 100 * ms - 1)
            d = self.get_json(f"https://www.okx.com/api/v5/market/history-candles?instId={base}-USDT-SWAP&bar={bar}"
                              f"&before={t - 1}&after={hi + 1}&limit=100")
            if str(d.get("code")) != "0":
                raise RuntimeError(f"okx code={d.get('code')} {d.get('msg')}")
            for r in d.get("data") or []:
                ts = int(r[0])
                if start <= ts <= end:
                    out[ts] = [ts, float(r[1]) * k, float(r[2]) * k, float(r[3]) * k, float(r[4]) * k]
            t, pages = hi + 1, pages + 1
            time.sleep(0.12)
        return [out[x] for x in sorted(out)]

    def _binance(self, symbol, iv, start, end):
        base, k = self._base_scale(symbol)
        out, t = {}, start
        for _ in range(3):
            if t > end:
                break
            rows = self.get_json(f"https://data-api.binance.vision/api/v3/klines?symbol={base}USDT&interval={iv}"
                                 f"&startTime={t}&endTime={end}&limit=1000")
            if not isinstance(rows, list) or not rows:
                break
            for r in rows:
                out[int(r[0])] = [int(r[0]), float(r[1]) * k, float(r[2]) * k, float(r[3]) * k, float(r[4]) * k]
            t = int(rows[-1][0]) + IV_MS[iv]
        return [out[x] for x in sorted(out)]

    def __call__(self, symbol, iv, start, end):
        errs, empty = [], False
        for name, fn in (("BingX", self._bingx), ("OKX", self._okx), ("Binance spot", self._binance)):
            try:
                rows = fn(symbol, iv, start, end)
            except Exception as e:  # noqa: BLE001 — any source may refuse; try the next one
                errs.append(f"{name}: {str(e)[:80]}")
                continue
            if rows:
                self.used[name] = self.used.get(name, 0) + 1
                return rows
            empty = True
        if errs and not empty:
            self.errors.append(f"{symbol} {iv}: " + "; ".join(errs))
            raise RuntimeError("; ".join(errs))
        return []


class _Budget:
    def __init__(self, seconds: float, requests: int):
        self.deadline = time.monotonic() + seconds
        self.left = requests
        self.used = 0

    def take(self) -> bool:
        if self.left <= 0 or time.monotonic() > self.deadline:
            return False
        self.left -= 1
        self.used += 1
        return True


# ------------------------------------------------------------------------------------------------------------ storage
SCHEMA = """
CREATE TABLE IF NOT EXISTS app_signals (
  signal_id TEXT PRIMARY KEY, first_seen INTEGER, last_seen INTEGER, status TEXT, closed_seen INTEGER,
  raw TEXT, name TEXT, symbol TEXT, side TEXT, entry_lo REAL, entry_hi REAL, sl REAL, leverage REAL, t_signal INTEGER,
  targets TEXT, is_vip INTEGER, app_best_tp INTEGER, actual_price REAL, actual_pct REAL, problems TEXT, plan TEXT,
  sim TEXT, features TEXT, sim_version INTEGER, path_until INTEGER, ctx_done INTEGER DEFAULT 0,
  resolved_at INTEGER, done INTEGER DEFAULT 0, fetch_fail INTEGER DEFAULT 0, m1_done INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS app_signal_candles (
  symbol TEXT, interval TEXT, t INTEGER, o REAL, h REAL, l REAL, c REAL, PRIMARY KEY (symbol, interval, t));
"""


MIGRATIONS = {"m1_done": "INTEGER DEFAULT 0"}     # columns added after the first server run (ALTER on the existing table)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(app_signals)")}
    for col, decl in MIGRATIONS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE app_signals ADD COLUMN {col} {decl}")
    conn.commit()


def _plan_key(s: dict) -> str:
    return json.dumps([s.get("symbol"), s.get("side"), s.get("entry_lo"), s.get("entry_hi"), s.get("sl"), s.get("t"),
                       [x["price"] for x in s.get("targets") or []]])


def upsert(conn, items_by_status: dict[str, list], now: int, active_ok: bool) -> dict:
    """items_by_status = {"active": [...raw...], "closed": [...raw...]}. Keeps every signal ever seen; returns parse stats."""
    ensure_schema(conn)
    seen_active, stats = set(), {"items": 0, "new": 0, "problems": {}, "extra_keys": set(), "examples": []}
    for status in ("active", "closed"):
        for raw in items_by_status.get(status) or []:
            s = normalize(raw)
            if not s.get("signal_id"):
                stats["problems"]["not_a_dict"] = stats["problems"].get("not_a_dict", 0) + 1
                continue
            stats["items"] += 1
            for p in s["problems"]:
                key = p.split(":")[0]
                stats["problems"][key] = stats["problems"].get(key, 0) + 1
                if key in ("entry", "stop", "targets", "date", "name", "side", "geometry") and len(stats["examples"]) < 3:
                    stats["examples"].append({k: raw.get(k) for k in list(raw)[:12]} if isinstance(raw, dict) else str(raw)[:200])
            stats["extra_keys"].update(s["extra_keys"])
            sid = s["signal_id"]
            if status == "active":
                seen_active.add(sid)
            row = conn.execute("SELECT status, plan FROM app_signals WHERE signal_id=?", (sid,)).fetchone()
            plan = _plan_key(s)
            vals = dict(raw=json.dumps(raw, ensure_ascii=False), name=s["name"], symbol=s["symbol"], side=s["side"],
                        entry_lo=s["entry_lo"], entry_hi=s["entry_hi"], sl=s["sl"], leverage=s["leverage"], t_signal=s["t"],
                        targets=json.dumps(s["targets"]), is_vip=int(s["is_vip"]), app_best_tp=s["app_best_tp"],
                        actual_price=s["actual_price"], actual_pct=s["actual_pct"], problems=json.dumps(s["problems"]), plan=plan,
                        last_seen=now, status=status)
            if row is None:
                stats["new"] += 1
                if vals["t_signal"] is None and status == "active":
                    vals["t_signal"] = now          # an active signal without a date: first seen ≈ published
                vals.update(first_seen=now, closed_seen=None)
                cols = ",".join(["signal_id"] + list(vals))
                conn.execute(f"INSERT INTO app_signals ({cols}) VALUES ({','.join('?' * (len(vals) + 1))})", [sid] + list(vals.values()))
            else:
                if vals["t_signal"] is None:
                    vals.pop("t_signal")
                if row[0] == "active" and status == "closed":
                    vals["closed_seen"] = now
                sets = ",".join(f"{k}=?" for k in vals)
                conn.execute(f"UPDATE app_signals SET {sets} WHERE signal_id=?", list(vals.values()) + [sid])
                if row[1] != plan:   # the plan changed on the server → recompute from scratch
                    conn.execute("UPDATE app_signals SET sim=NULL, done=0, path_until=NULL, ctx_done=0, resolved_at=NULL, "
                                 "features=NULL WHERE signal_id=?", (sid,))
    if active_ok:
        ids = list(seen_active)
        q = "UPDATE app_signals SET status='closed', closed_seen=COALESCE(closed_seen, ?) WHERE status='active'"
        if ids:
            q += f" AND signal_id NOT IN ({','.join('?' * len(ids))})"
        conn.execute(q, [now] + ids)
    conn.commit()
    stats["extra_keys"] = sorted(stats["extra_keys"])
    return stats


def _store_candles(conn, symbol, iv, rows) -> None:
    conn.executemany("INSERT OR REPLACE INTO app_signal_candles (symbol, interval, t, o, h, l, c) VALUES (?,?,?,?,?,?,?)",
                     [(symbol, iv, int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in rows])


def _candles(conn, symbol, iv, start, end) -> list[list]:
    return [list(r) for r in conn.execute("SELECT t, o, h, l, c FROM app_signal_candles WHERE symbol=? AND interval=? AND t>=? AND t<=? "
                                          "ORDER BY t", (symbol, iv, start, end))]


def _have(conn, symbol, iv, start, end, share=0.9) -> bool:
    n = conn.execute("SELECT COUNT(*) FROM app_signal_candles WHERE symbol=? AND interval=? AND t>=? AND t<?",
                     (symbol, iv, start, end)).fetchone()[0]
    return n >= share * max(1, (end - start) // IV_MS[iv])


def _first_missing(conn, symbol, iv, start, end) -> int | None:
    """First candle open time in [start, end] (aligned to the interval) that is not in the cache; None when all are there."""
    ms = IV_MS[iv]
    t = (start + ms - 1) // ms * ms
    for (ct,) in conn.execute("SELECT t FROM app_signal_candles WHERE symbol=? AND interval=? AND t>=? AND t<=? ORDER BY t",
                              (symbol, iv, t, end)):
        if ct != t:
            return t
        t += ms
    return t if t <= end else None


def _m1_end(t0: int) -> int:
    """End of the 1m window: the first 15m boundary at least 3 hours after the signal minute."""
    sm = t0 // M1 * M1
    return -(-(sm + M1_WINDOW_MS) // M15) * M15


def build_path(t0: int, m1: list, c15: list, m1_final: bool, now: int, path_until: int | None = None) -> tuple[list | None, str]:
    """Candles from the one containing the signal time: 1m for the first ~3 h, then 15m. → (path, source) or (None, why).
    m1 / c15 are cached rows [[t, o, h, l, c], …]; m1_final = the 1m window was fetched for good (or is unavailable);
    path_until = this signal's own verified 15m coverage (the shared cache may hold other signals' candles after a hole)."""
    sm, S = t0 // M1 * M1, _m1_end(t0)
    q = [list(r[:5]) + [M15] for r in c15 if path_until is not None and r[0] <= path_until]
    a = [list(r[:5]) + [M1] for r in m1 if sm <= r[0] < S]
    if a and a[0][0] <= sm + 5 * M1 and (m1_final or now < S + M1):
        return a + [r for r in q if r[0] >= S], "1m"
    if not m1_final:
        return None, "wait_1m"
    q0 = t0 // M15 * M15                                  # no 1m data anywhere: 15m from the candle containing the signal
    q = [r for r in q if r[0] >= q0]
    return (q, "15m") if q and q[0][0] == q0 else (None, "no_start")


def _fetch_range(conn, klines, budget, symbol, iv, start, end, now: int | None = None) -> tuple[bool, int | None]:
    """Fetch [start, end] in 1440-candle windows; only CLOSED candles are stored (a forming one would be cached as final).
    → (complete, open time of the last candle stored)."""
    ms = IV_MS[iv]
    now = now if now is not None else int(time.time() * 1000)
    step = ms * 1440
    t, last = start, None
    while t <= end:
        if not budget.take():
            return False, last
        rows = klines(symbol, iv, t, min(t + step - 1, end))
        rows = [r for r in rows or [] if t <= int(r[0]) <= end]
        if rows:
            last_t = int(rows[-1][0])
            rows = [r for r in rows if int(r[0]) + ms <= now]
            if rows:
                _store_candles(conn, symbol, iv, rows)
                last = int(rows[-1][0])
            t = last_t + ms
        else:
            t += step
    return True, last


# ------------------------------------------------------------------------------------------------------------ simulation
def _dur(c) -> int:
    return c[5] if len(c) > 5 else M15


def simulate(sig: dict, candles: list, now: int) -> dict:
    """Path of one signal over candles [[t, o, h, l, c, duration_ms], …] that start with the candle containing the signal time
    (build_path: 1m for the first ~3 h, 15m after). sig: side, entry_lo, entry_hi, sl, tps (prices by level), t, leverage.
    Entry within MARKET_TOL_PCT of the signal candle's open or close → market order, filled at the entry at the signal time;
    farther → limit order that needs a touch within 48 h."""
    s = 1 if sig["side"] == "LONG" else -1
    lo, hi, sl, tps, t0 = sig["entry_lo"], sig["entry_hi"], sig["sl"], list(sig["tps"]), sig["t"]
    lev = max(1.0, float(sig.get("leverage") or 1.0))
    n = len(tps)
    res = {"v": SIM_VERSION, "state": "pending", "n_tps": n, "best_tp": 0, "first": None, "ended": None, "resolved_at": None,
           "policies": {}, "ambiguous": 0}
    cs = [c for c in candles if c[0] + _dur(c) > t0]
    if not cs:
        res["reason"] = "no_candles"
        return res
    c0 = cs[0]
    if c0[0] > t0:                                   # the cache does not reach back to the signal → no verdict yet
        res["reason"] = "no_start"
        return res
    res["ref_price"] = c0[1]
    res["last_price"], res["last_t"] = cs[-1][4], cs[-1][0]
    res["path_src"] = "1m" if _dur(c0) == M1 else "15m"
    mid = (lo + hi) / 2
    if abs(c0[1] / mid - 1) > 0.5:
        res.update(state="bad_data", reason="price_mismatch")
        return res

    def dist(px):
        return abs(min(max(px, lo), hi) - px) / px * 100

    d0 = min(dist(c0[1]), dist(c0[4]))
    res["entry_dist_pct"] = round(d0, 3)
    tp1 = tps[0]
    fill_i = fill = None
    at_open = False
    if d0 <= MARKET_TOL_PCT:
        res["entry_mode"] = "market"
        fill, fill_i, at_open, fill_t = (lo if lo == hi else min(max(c0[1], lo), hi)), 0, True, t0
    else:
        res["entry_mode"] = "limit"
        for i, c in enumerate(cs):
            if c[0] >= t0 + FILL_WINDOW_MS:
                break
            o, h, l = c[1], c[2], c[3]
            if l <= hi and h >= lo:
                if lo <= o <= hi:
                    fill, at_open = o, True
                else:
                    fill = hi if o > hi else lo
                fill_i = i
                break
            fav, adv = (h, l) if s > 0 else (l, h)
            if s * (fav - tp1) >= 0:
                res.update(state="not_filled", reason="tp1_before_entry", resolved_at=max(c[0], t0))
                return res
            if s * (adv - sl) <= 0:
                res.update(state="not_filled", reason="sl_before_entry", resolved_at=max(c[0], t0))
                return res
        if fill_i is None:
            if cs[-1][0] + _dur(cs[-1]) >= t0 + FILL_WINDOW_MS:
                res.update(state="not_filled", reason="no_touch", resolved_at=t0 + FILL_WINDOW_MS)
            return res
        fill_t = max(cs[fill_i][0], t0)

    F = fill
    risk_pct = s * (F - sl) / F * 100
    if risk_pct <= 0:
        res.update(state="bad_data", reason="stop_inside_entry")
        return res

    def P(px):
        return s * (px - F) / F * 100

    liq_move = max(0.1, 100.0 / lev - 0.5) if lev > 1 else None
    res.update(state="open", fill=F, fill_t=fill_t, fill_delay_h=round((fill_t - t0) / H1, 2), risk_pct=round(risk_pct, 4),
               liq_move_pct=round(liq_move, 3) if liq_move else None, liq_risk=bool(liq_move and risk_pct >= liq_move))
    best, tp_t = 0, [None] * n
    be_t, be_best, sl_t, liq_t, end_t, ended, exit_px, end_i = None, 0, None, None, None, None, None, None
    mfe = mae = 0.0
    for j in range(fill_i, len(cs)):
        tc0, o, h, l, cl = cs[j][:5]
        tc = max(tc0, t0)                                # event time (the signal candle starts before the signal)
        if tc0 >= fill_t + MAX_HOLD_MS:
            ended, end_t, exit_px, end_i = "timeout", tc0, cs[j - 1][4], j - 1
            break
        first = j == fill_i
        fav, adv = (h, l) if s > 0 else (l, h)
        adv_pct = max(0.0, -P(adv))
        fav_pct = max(0.0, P(fav))
        sl_touch = s * (adv - sl) <= 0
        if best >= 1 and be_t is None and tp_t[0] < tc and s * (adv - F) <= 0:
            be_t, be_best = tc, best                    # stop moved to entry: entry touched on a later candle (cautious)
        if liq_move and liq_t is None and adv_pct >= liq_move and (not sl_touch or liq_move < risk_pct):
            liq_t = tc
        if sl_touch:
            if any(s * (fav - p) >= 0 for p in tps[best:]) and (not first or at_open):
                res["ambiguous"] += 1
            mae = max(mae, risk_pct)
            sl_t, ended, end_t, end_i = tc, "sl", tc, j
            break
        mae = max(mae, adv_pct)
        if not first or at_open:
            mfe = max(mfe, fav_pct)
            reached = [k for k in range(n) if s * (fav - tps[k]) >= 0]
            if reached and max(reached) + 1 > best:
                top = max(reached) + 1
                for k in range(best, top):
                    tp_t[k] = tc
                best = top
        if best == n:
            ended, end_t, end_i = "last_tp", tc, j
            break
    res.update(best_tp=best, tp_t=tp_t, sl_t=sl_t, be_t=be_t, be_best=be_best, liq_t=liq_t, liquidated=liq_t is not None,
               mfe_pct=round(mfe, 4), mae_pct=round(mae, 4), mfe_r=round(mfe / risk_pct, 3), mae_r=round(mae / risk_pct, 3),
               first="tp1" if tp_t[0] is not None else ("sl" if ended == "sl" else None),
               time_to_tp1_h=round((tp_t[0] - t0) / H1, 2) if tp_t[0] is not None else None)
    if ended:
        res.update(state="closed", ended=ended, end_t=end_t, resolved_at=end_t,
                   exit_px=exit_px if ended == "timeout" else (sl if ended == "sl" else tps[-1]))
    if ended == "sl":   # did the price come back to TP1 within 7 days after the stop? (stop too tight?)
        after = [c for c in cs[end_i + 1:] if c[0] <= sl_t + AFTER_MS]
        hit = any(s * ((c[2] if s > 0 else c[3]) - tp1) >= 0 for c in after)
        covered = cs[-1][0] + _dur(cs[-1]) >= sl_t + AFTER_MS
        res["after_tp1"] = True if hit else (False if covered else None)
    mtm = exit_px if ended == "timeout" else None
    pol = {}

    def put(key, gross, closed_at):
        net = gross - COST_PCT
        pol[key] = {"closed": True, "pct": round(gross, 4), "net": round(net, 4), "r": round(net / risk_pct, 4),
                    "lev": round(max(net * lev, -100.0), 3), "t": closed_at}

    # app — farthest TP reached is the result
    if ended:
        put("app", P(tps[best - 1]) if best else (P(sl) if ended == "sl" else P(mtm)), tp_t[best - 1] if best else end_t)
    # tp1
    if best >= 1:
        put("tp1", P(tps[0]), tp_t[0])
    elif ended:
        put("tp1", P(sl) if ended == "sl" else P(mtm), end_t)
    # last
    if ended:
        put("last", P(tps[-1]) if ended == "last_tp" else (P(sl) if ended == "sl" else P(mtm)), end_t)
    # ladder — stop stays
    if ended:
        rest_px = sl if ended == "sl" else (mtm if ended == "timeout" else None)
        g = sum(P(tps[k]) for k in range(best)) / n + ((n - best) / n * P(rest_px) if rest_px is not None else 0.0)
        put("ladder", g, end_t)
    # ladder_be — stop to entry after TP1
    if be_t is not None:
        put("ladder_be", sum(P(tps[k]) for k in range(be_best)) / n, be_t)
    elif ended == "last_tp":
        put("ladder_be", sum(P(p) for p in tps) / n, end_t)
    elif ended == "sl":
        put("ladder_be", P(sl), end_t)
    elif ended == "timeout":
        put("ladder_be", sum(P(tps[k]) for k in range(best)) / n + (n - best) / n * P(mtm), end_t)
    # liquidation: leveraged result of the part still open at liq_t = −100 % of its margin
    if liq_t is not None:
        def gone(tt):
            return tt is None or tt >= liq_t
        for key, p in pol.items():
            if key == "ladder":
                parts = [k for k in range(n) if tp_t[k] is not None and tp_t[k] < liq_t]
                lv = sum((P(tps[k]) - COST_PCT) * lev for k in parts) / n - 100.0 * (n - len(parts)) / n
                p["lev"] = round(max(lv, -100.0), 3)
            elif key == "ladder_be":
                if be_t is None or be_t > liq_t:
                    parts = [k for k in range(n) if tp_t[k] is not None and tp_t[k] < liq_t]
                    lv = sum((P(tps[k]) - COST_PCT) * lev for k in parts) / n - 100.0 * (n - len(parts)) / n
                    p["lev"] = round(max(lv, -100.0), 3)
            elif key == "tp1":
                if gone(tp_t[0]):
                    p["lev"] = -100.0
            elif key == "app":
                if gone(tp_t[best - 1] if best else None):
                    p["lev"] = -100.0
            elif key == "last" and liq_t <= (end_t or liq_t):
                p["lev"] = -100.0
    res["policies"] = pol
    res["done_at"] = (sl_t + AFTER_MS) if ended == "sl" else end_t
    return res


# ------------------------------------------------------------------------------------------------------------ features
def ema(vals: list[float], n: int) -> float | None:
    if len(vals) < n:
        return None
    k = 2 / (n + 1)
    e = sum(vals[:n]) / n
    for v in vals[n:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes: list[float], n: int = 14) -> float | None:
    if len(closes) <= n:
        return None
    gains = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    losses = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        ag, al = (ag * (n - 1) + g) / n, (al * (n - 1) + l) / n
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    return 100 - 100 / (1 + ag / al)


def atr(rows: list[list], n: int = 14) -> float | None:
    if len(rows) <= n:
        return None
    trs = [max(r[2] - r[3], abs(r[2] - p[4]), abs(r[3] - p[4])) for p, r in zip(rows, rows[1:])]
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def _r(x, nd=2):
    return None if x is None else round(x, nd)


def plan_features(sig: dict) -> dict:
    s = 1 if sig["side"] == "LONG" else -1
    mid = (sig["entry_lo"] + sig["entry_hi"]) / 2
    sl_pct = abs(mid - sig["sl"]) / mid * 100
    tp_pct = [s * (p - mid) / mid * 100 for p in sig["tps"]]
    d = datetime.fromtimestamp(sig["t"] / 1000, timezone.utc)
    return {"side": sig["side"], "coin": sig["symbol"].split("-")[0], "leverage": sig.get("leverage") or 1.0,
            "hour_utc": d.hour, "weekday": d.weekday(), "n_tps": len(sig["tps"]), "range_entry": sig["entry_hi"] > sig["entry_lo"],
            "entry_width_pct": _r((sig["entry_hi"] - sig["entry_lo"]) / mid * 100, 3),
            "sl_pct": _r(sl_pct, 3), "tp_pct": [_r(x, 3) for x in tp_pct],
            "rr": [_r(x / sl_pct, 2) if sl_pct > 0 else None for x in tp_pct]}


def _trend(rows4h):
    closes = [r[4] for r in rows4h]
    e50, e200 = ema(closes, 50), ema(closes, 200)
    if e50 is None or e200 is None:
        return None, e50, e200
    return ("up" if e50 > e200 else "down"), e50, e200


def compute_features(sig: dict, c1h: list, c4h: list, btc4h: list) -> dict:
    """Context at signal time from CLOSED bars only (bar open + length ≤ signal time)."""
    t = sig["t"]
    f = plan_features(sig)
    h1 = [r for r in c1h if r[0] + H1 <= t]
    h4 = [r for r in c4h if r[0] + H4 <= t]
    b4 = [r for r in btc4h if r[0] + H4 <= t]
    mid = (sig["entry_lo"] + sig["entry_hi"]) / 2
    if len(h1) >= 30:
        closes = [r[4] for r in h1]
        px = closes[-1]
        d24, d7 = h1[-24:], h1[-168:]
        f.update(price_at_signal=px, rsi1h=_r(rsi(closes), 1),
                 dist_hi24_pct=_r((px / max(r[2] for r in d24) - 1) * 100), dist_lo24_pct=_r((px / min(r[3] for r in d24) - 1) * 100),
                 dist_hi7d_pct=_r((px / max(r[2] for r in d7) - 1) * 100), dist_lo7d_pct=_r((px / min(r[3] for r in d7) - 1) * 100),
                 move4h_pct=_r((px / closes[-5] - 1) * 100) if len(closes) >= 5 else None,
                 move24h_pct=_r((px / closes[-25] - 1) * 100) if len(closes) >= 25 else None,
                 atr1h_pct=_r(atr(h1) / px * 100, 3) if atr(h1) else None,
                 entry_vs_market_pct=_r((mid / px - 1) * 100, 3), h7d_bars=len(d7))
    if len(h4) >= 20:
        closes4 = [r[4] for r in h4]
        tr, e50, e200 = _trend(h4)
        f.update(rsi4h=_r(rsi(closes4), 1), trend4h=tr,
                 ema50_vs_200_pct=_r((e50 / e200 - 1) * 100) if e50 and e200 else None)
    if len(b4) >= 200:
        tr, _, _ = _trend(b4)
        f.update(btc_trend4h=tr, btc_move4h_pct=_r((b4[-1][4] / b4[-2][4] - 1) * 100))
    side = sig["side"]
    if f.get("trend4h"):
        f["with_trend"] = (side == "LONG") == (f["trend4h"] == "up")
    if f.get("btc_trend4h"):
        f["with_btc"] = (side == "LONG") == (f["btc_trend4h"] == "up")
    if f.get("rsi1h") is not None:
        f["rsi_wrong"] = (side == "LONG" and f["rsi1h"] >= 70) or (side == "SHORT" and f["rsi1h"] <= 30)
    f["archetype"] = archetype(f)
    return f


def archetype(f: dict) -> str:
    side, tr, r1 = f.get("side"), f.get("trend4h"), f.get("rsi1h")
    hi24, lo24 = f.get("dist_hi24_pct"), f.get("dist_lo24_pct")
    if hi24 is None and tr is None:
        return "Покупка (нет данных о рынке)" if side == "LONG" else "Шорт (нет данных о рынке)"
    if side == "LONG":
        if tr == "up" and hi24 is not None and hi24 <= -3:
            return "Покупка отката в восходящем тренде"
        if tr == "up" and hi24 is not None and hi24 > -1:
            return "Покупка у максимума / на пробое в восходящем тренде"
        if tr == "up":
            return "Покупка в восходящем тренде"
        if (r1 is not None and r1 <= 35) or (lo24 is not None and lo24 <= 1.5):
            return "Ловля дна: покупка на перепроданности против тренда"
        if tr == "down":
            return "Покупка против нисходящего тренда"
        return "Покупка (тренд 4h не определён)"
    if tr == "down" and lo24 is not None and lo24 >= 3:
        return "Шорт отскока в нисходящем тренде"
    if tr == "down" and lo24 is not None and lo24 < 1:
        return "Шорт у минимума / на пробое вниз"
    if tr == "down":
        return "Шорт в нисходящем тренде"
    if (r1 is not None and r1 >= 65) or (hi24 is not None and hi24 >= -1.5):
        return "Шорт на перекупленности против тренда"
    if tr == "up":
        return "Шорт против восходящего тренда"
    return "Шорт (тренд 4h не определён)"


# ------------------------------------------------------------------------------------------------------------ run loop
def _row_sig(r: sqlite3.Row) -> dict:
    tg = json.loads(r["targets"] or "[]")
    return {"id": r["signal_id"], "symbol": r["symbol"], "side": r["side"], "entry_lo": r["entry_lo"], "entry_hi": r["entry_hi"],
            "sl": r["sl"], "t": r["t_signal"], "leverage": r["leverage"], "tps": [x["price"] for x in tg], "targets": tg}


def _sim_ready(sig: dict) -> bool:
    return bool(sig["symbol"] and sig["side"] and sig["entry_lo"] and sig["sl"] and sig["t"] and sig["tps"])


def _sim_from_cache(conn, sig: dict, m1_final: bool, now: int, path_until: int | None) -> dict:
    t0, sym = sig["t"], sig["symbol"]
    path, why = build_path(t0, _candles(conn, sym, "1m", t0 // M1 * M1, _m1_end(t0)),
                           _candles(conn, sym, "15m", t0 // M15 * M15, now + M15), m1_final, now, path_until)
    if path is None:
        return {"v": SIM_VERSION, "state": "pending", "reason": why, "policies": {}, "n_tps": len(sig["tps"]), "best_tp": 0}
    res = simulate(sig, path, now)
    if res.get("state") in ("closed", "open"):
        res["whatif"] = whatif(sig, path, now)
    return res


def _variant(sig: dict, key: str) -> dict:
    v = dict(sig)
    mid = (sig["entry_lo"] + sig["entry_hi"]) / 2
    if key == "rev":
        v["side"] = "SHORT" if sig["side"] == "LONG" else "LONG"
        v["sl"] = 2 * mid - sig["sl"]
        v["tps"] = [2 * mid - p for p in sig["tps"]]
    elif key in ("wide15", "wide2"):
        k = 1.5 if key == "wide15" else 2.0
        v["sl"] = mid - (mid - sig["sl"]) * k
    elif key == "tp3":
        v["tps"] = list(sig["tps"][:3])
    return v


def whatif(sig: dict, path: list, now: int) -> dict:
    """Outcome of each WHATIF variant on the same candles (compact: state, first event, best TP, net % under two exits)."""
    out = {}
    for key in WHATIF:
        try:
            r = simulate(_variant(sig, key), path, now)
        except Exception:  # noqa: BLE001
            continue
        pol = r.get("policies") or {}
        out[key] = {"state": r.get("state"), "first": r.get("first"), "best_tp": r.get("best_tp"),
                    "be": (pol.get("ladder_be") or {}).get("net"), "last": (pol.get("last") or {}).get("net"),
                    "t_be": (pol.get("ladder_be") or {}).get("t"), "t_last": (pol.get("last") or {}).get("t"),
                    "risk_pct": r.get("risk_pct"), "fill": r.get("fill"), "fill_t": r.get("fill_t")}
    return out


def process(conn, now: int, klines, budget: _Budget, notes: list) -> dict:
    """Fetch what each unfinished signal still needs (within the budget) and recompute its outcome from the cache.
    Order per signal: the 1m window after the signal (decides market vs limit fill and the first hours), the 15m path, the
    context at signal time. Candles already in the shared per-coin cache are not fetched again."""
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    if conn.execute("SELECT COUNT(*) FROM app_signals WHERE sim_version IS NOT NULL AND sim_version<>?", (SIM_VERSION,)).fetchone()[0]:
        conn.execute("UPDATE app_signals SET done=0, sim=NULL, path_until=NULL, m1_done=0 WHERE sim_version IS NOT NULL AND sim_version<>?",
                     (SIM_VERSION,))
        # version 1 also cached the candle still forming at fetch time; the newest 15m candle per coin may be partial → refetch it
        conn.execute("DELETE FROM app_signal_candles WHERE interval='15m' AND (symbol, t) IN "
                     "(SELECT symbol, MAX(t) FROM app_signal_candles WHERE interval='15m' GROUP BY symbol)")
    rows = cur.execute("SELECT * FROM app_signals WHERE done=0 ORDER BY (status='active') DESC, t_signal DESC").fetchall()
    st = {"unfinished": len(rows), "fetch_errors": 0, "skipped_budget": 0}

    def note(msg):
        if len(notes) < 40:
            notes.append(msg)

    for r in rows:
        sig = _row_sig(r)
        sid = sig["id"]
        if not _sim_ready(sig):
            conn.execute("UPDATE app_signals SET sim=?, done=1, sim_version=? WHERE signal_id=?",
                         (json.dumps({"v": SIM_VERSION, "state": "bad_data", "reason": "parse"}), SIM_VERSION, sid))
            continue
        sym, t0 = sig["symbol"], sig["t"]
        fail0 = r["fetch_fail"] or 0          # runs in a row with every source failing
        ok_any, path_complete, failed, short = False, True, False, False
        # 1. 1m window [signal minute, ~3 h]
        m1_final = bool(r["m1_done"])
        if not m1_final:
            sm, S = t0 // M1 * M1, _m1_end(t0)
            w_end = min(S - M1, now // M1 * M1 - M1)
            try:
                miss = _first_missing(conn, sym, "1m", sm, w_end) if w_end >= sm else None
                complete = True
                if miss is not None:
                    complete, _ = _fetch_range(conn, klines, budget, sym, "1m", miss, w_end, now)
                    if complete:
                        ok_any = True
                if complete and now >= S + M1:
                    m1_final = True
                elif not complete:
                    short = True
            except Exception as e:  # noqa: BLE001
                failed = True
                st["fetch_errors"] += 1
                note(f"свечи 1m {sym}: {str(e)[:120]}")
                m1_final = fail0 + 1 >= MAX_FETCH_FAIL
            if m1_final:
                conn.execute("UPDATE app_signals SET m1_done=1 WHERE signal_id=?", (sid,))
        # 2. 15m path from the candle containing the signal, window by window; stop once the outcome (+7 days after a stop) is covered
        path_until = r["path_until"]
        prev = json.loads(r["sim"]) if r["sim"] else {}
        stop_at = prev.get("done_at") if prev.get("state") == "closed" else None
        q0 = t0 // M15 * M15
        last_closed = now // M15 * M15 - M15
        for _ in range(8):
            start = path_until + M15 if path_until is not None else q0
            end = last_closed if stop_at is None else min(last_closed, stop_at + M15)
            if start > end:
                break
            w_end = min(end, start + 1439 * M15)
            miss = _first_missing(conn, sym, "15m", start, w_end)
            if miss is not None:
                try:
                    complete, _ = _fetch_range(conn, klines, budget, sym, "15m", miss, w_end, now)
                except Exception as e:  # noqa: BLE001
                    failed = True
                    st["fetch_errors"] += 1
                    path_complete = False
                    note(f"свечи {sym}: {str(e)[:120]}")
                    break
                if not complete:
                    short = True
                    path_complete = False
                    break
                ok_any = True
            path_until = w_end                     # [start, w_end] is now cached or fetched (holes left by the source stay holes)
            conn.execute("UPDATE app_signals SET path_until=? WHERE signal_id=?", (path_until, sid))
            sim = _sim_from_cache(conn, sig, m1_final, now, path_until)
            if sim["state"] == "closed":
                stop_at = sim["done_at"]
            elif sim["state"] in ("not_filled", "bad_data"):
                break
            if w_end >= end:
                break
        # 3. context at signal time (once): coin 1h + 4h, BTC 4h
        ctx_done = bool(r["ctx_done"])
        if not ctx_done:
            got_all = True
            for s2, iv, st_ in ((sym, "1h", t0 - 200 * H1), (sym, "4h", t0 - 250 * H4), ("BTC-USDT", "4h", t0 - 250 * H4)):
                if _have(conn, s2, iv, st_ + IV_MS[iv] * 5, t0 - IV_MS[iv]):
                    continue
                try:
                    complete, _ = _fetch_range(conn, klines, budget, s2, iv, st_, t0, now)
                    got_all = got_all and complete
                    short = short or not complete
                except Exception as e:  # noqa: BLE001
                    got_all = False
                    failed = True
                    note(f"контекст {s2} {iv}: {str(e)[:100]}")
            if got_all or (failed and fail0 + 1 >= MAX_FETCH_FAIL):
                feat = compute_features(sig, _candles(conn, sym, "1h", t0 - 200 * H1, t0), _candles(conn, sym, "4h", t0 - 250 * H4, t0),
                                        _candles(conn, "BTC-USDT", "4h", t0 - 250 * H4, t0))
                conn.execute("UPDATE app_signals SET features=?, ctx_done=1 WHERE signal_id=?", (json.dumps(feat), sid))
                ctx_done = True
        st["skipped_budget"] += int(short)
        # 4. outcome from the cache
        sim = _sim_from_cache(conn, sig, m1_final, now, path_until)
        has_any = conn.execute("SELECT 1 FROM app_signal_candles WHERE symbol=? AND interval IN ('1m','15m') AND t>=? LIMIT 1",
                               (sym, t0 // M15 * M15)).fetchone()
        if not has_any and ((failed and fail0 + 1 >= MAX_FETCH_FAIL) or (path_complete and m1_final and now > t0 + FILL_WINDOW_MS + DAY)):
            sim = {"v": SIM_VERSION, "state": "no_data", "reason": "candles", "policies": {}}
        state = sim["state"]
        if state in ("bad_data", "no_data", "not_filled"):
            done = 1
        elif state == "closed":
            done = int(sim.get("ended") != "sl" or sim.get("after_tp1") is not None or now >= sim["done_at"] + DAY)
        else:
            done = 0
        if done and not ctx_done:
            if state in ("bad_data", "no_data") or (failed and fail0 + 1 >= MAX_FETCH_FAIL):
                pf = plan_features(sig)
                conn.execute("UPDATE app_signals SET features=?, ctx_done=1 WHERE signal_id=?", (json.dumps(dict(pf, archetype=archetype(pf))), sid))
            else:
                done = 0          # keep it until the context at signal time is in
        conn.execute("UPDATE app_signals SET sim=?, sim_version=?, resolved_at=?, done=?, fetch_fail=? WHERE signal_id=?",
                     (json.dumps(sim), SIM_VERSION, sim.get("resolved_at"), done, 0 if ok_any else fail0 + int(failed), sid))
        conn.commit()
    return st


def prune(conn, now: int) -> int:
    """Finished signals keep their result; drop candles no unfinished signal needs (the DB is committed to git)."""
    need: dict[str, int] = {}
    m1_need: dict[str, list] = {}
    for sym, t in conn.execute("SELECT symbol, t_signal FROM app_signals WHERE done=0 AND symbol IS NOT NULL AND t_signal IS NOT NULL"):
        need[sym] = min(need.get(sym, t), t)
        m1_need.setdefault(sym, []).append((t // M1 * M1, _m1_end(t)))
    before = conn.total_changes
    for (sym,) in conn.execute("SELECT DISTINCT symbol FROM app_signal_candles WHERE interval='15m'").fetchall():
        if sym not in need:
            conn.execute("DELETE FROM app_signal_candles WHERE symbol=? AND interval='15m'", (sym,))
        else:
            conn.execute("DELETE FROM app_signal_candles WHERE symbol=? AND interval='15m' AND t<?", (sym, need[sym] - DAY))
    for (sym,) in conn.execute("SELECT DISTINCT symbol FROM app_signal_candles WHERE interval='1m'").fetchall():
        wins = m1_need.get(sym)
        if not wins:
            conn.execute("DELETE FROM app_signal_candles WHERE symbol=? AND interval='1m'", (sym,))
            continue
        keep = " OR ".join("(t>=? AND t<?)" for _ in wins)
        conn.execute(f"DELETE FROM app_signal_candles WHERE symbol=? AND interval='1m' AND NOT ({keep})",
                     [sym] + [x for w in wins for x in w])
    oldest_ctx = conn.execute("SELECT MIN(t_signal) FROM app_signals WHERE ctx_done=0 AND done=0").fetchone()[0]
    cutoff = min(oldest_ctx or now, now) - 60 * DAY
    conn.execute("DELETE FROM app_signal_candles WHERE interval IN ('1h','4h') AND t<?", (cutoff,))
    conn.commit()
    return conn.total_changes - before


# ------------------------------------------------------------------------------------------------------------ aggregation
def _load(conn) -> list[dict]:
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    out = []
    for r in cur.execute("SELECT * FROM app_signals ORDER BY t_signal DESC"):
        out.append({"id": r["signal_id"], "name": r["name"], "symbol": r["symbol"], "coin": (r["symbol"] or "").split("-")[0] or None,
                    "side": r["side"], "entry_lo": r["entry_lo"], "entry_hi": r["entry_hi"], "sl": r["sl"],
                    "leverage": r["leverage"] or 1.0, "t": r["t_signal"], "targets": json.loads(r["targets"] or "[]"),
                    "status": r["status"], "first_seen": r["first_seen"], "last_seen": r["last_seen"], "closed_seen": r["closed_seen"],
                    "app_best_tp": r["app_best_tp"] or 0, "is_vip": bool(r["is_vip"]), "actual_price": r["actual_price"],
                    "actual_pct": r["actual_pct"], "problems": json.loads(r["problems"] or "[]"),
                    "sim": json.loads(r["sim"]) if r["sim"] else {}, "feat": json.loads(r["features"]) if r["features"] else {}})
    return out


def _resolved(rows):
    return [r for r in rows if r["sim"].get("state") == "closed"]


def _pol(r, key):
    p = r["sim"].get("policies", {}).get(key)
    return p if p and p.get("closed") else None


def pstats(rows, key: str, lev_cap: float | None = None) -> dict:
    xs = [p for p in (_pol(r, key) for r in rows) if p]
    if not xs:
        return {"n": 0, "winrate": None, "avg_pct": None, "avg_pct_lev": None, "avg_r": None, "sum_pct": None, "best": None, "worst": None}
    nets = [p["net"] for p in xs]
    levs = [p["lev"] for p in xs]
    return {"n": len(xs), "winrate": round(sum(1 for v in nets if v > 0) / len(xs), 3), "avg_pct": round(statistics.fmean(nets), 3),
            "avg_pct_lev": round(statistics.fmean(levs), 2), "avg_r": round(statistics.fmean(p["r"] for p in xs), 3),
            "sum_pct": round(sum(nets), 2), "sum_pct_lev": round(sum(levs), 1), "best": round(max(nets), 3), "worst": round(min(nets), 3),
            "worst_lev": round(min(levs), 1)}


def grp(rows, key: str = BASELINE) -> dict:
    """Small group summary: n resolved, TP1-before-stop share, win share and average R under the baseline policy."""
    res = _resolved(rows)
    ps = pstats(res, key)
    t1 = [r for r in res if r["sim"].get("first") in ("tp1", "sl")]
    return {"n": len(res), "tp1_rate": round(sum(1 for r in t1 if r["sim"]["first"] == "tp1") / len(t1), 3) if t1 else None,
            "winrate": ps["winrate"], "avg_r": ps["avg_r"], "avg_pct": ps["avg_pct"]}


def _median(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 3) if xs else None


def _bucket_hour(h):
    return f"{(h // 6) * 6:02d}–{(h // 6) * 6 + 6:02d} UTC"


def _small(n):
    return n < MIN_SAMPLE


def build_stats(rows) -> dict:
    res = _resolved(rows)
    st = {"policies": {k: pstats(res, k) for k, _, _ in POLICIES}}
    n = len(res)
    reach = {}
    maxn = max([r["sim"].get("n_tps", 0) for r in res] or [0])
    for k in range(1, maxn + 1):
        have = [r for r in res if r["sim"].get("n_tps", 0) >= k]
        reach[f"tp{k}"] = round(sum(1 for r in have if r["sim"].get("best_tp", 0) >= k) / len(have), 3) if have else None
    st["tp_reach"] = reach
    st["sl_share"] = round(sum(1 for r in res if r["sim"].get("first") == "sl") / n, 3) if n else None
    st["timeout_share"] = round(sum(1 for r in res if r["sim"].get("ended") == "timeout") / n, 3) if n else None
    st["median_time_to_tp1_h"] = _median([r["sim"].get("time_to_tp1_h") for r in res])
    st["median_fill_delay_h"] = _median([r["sim"].get("fill_delay_h") for r in res])
    st["median_mfe_r"] = _median([r["sim"].get("mfe_r") for r in res])
    st["median_mae_r"] = _median([r["sim"].get("mae_r") for r in res])
    st["ambiguous_candles"] = sum(r["sim"].get("ambiguous", 0) for r in res)
    st["by_side"] = {sd: grp([r for r in rows if r["side"] == sd]) for sd in ("LONG", "SHORT")}
    coins = {}
    for r in rows:
        if r["coin"]:
            coins.setdefault(r["coin"], []).append(r)
    top = sorted(coins.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:10]
    st["by_coin"] = [{"coin": c, "signals": len(v), **grp(v)} for c, v in top]
    # entry type: market (entry within 0.6 % of the price at the signal minute) vs limit (needs a touch within 48 h)
    modes = {}
    for r in rows:
        m = r["sim"].get("entry_mode")
        if m:
            modes.setdefault(m, []).append(r)
    st["entry_modes"] = {m: {"signals": len(v), "not_filled": sum(1 for r in v if r["sim"].get("state") == "not_filled"), **grp(v)}
                         for m, v in sorted(modes.items())}
    st["path_src"] = {k: sum(1 for r in rows if r["sim"].get("path_src") == k and r["sim"].get("state") not in (None, "pending"))
                      for k in ("1m", "15m")}
    st["median_entry_dist_pct"] = _median([r["sim"].get("entry_dist_pct") for r in rows])
    st["app_flags"] = app_flags_check(rows)
    st["whatif"] = whatif_stats(res)
    return st


def whatif_stats(res) -> dict:
    """Per variant and exit: n, average net % per trade, share of winners, and the two halves of history (stability check)."""
    if not res:
        return {}
    srt = sorted(res, key=lambda r: r["t"] or 0)
    half = srt[len(srt) // 2]["t"] or 0

    def summ(xs):
        xs = [x for x in xs if x is not None]
        return {"n": len(xs), "avg_pct": round(statistics.fmean(xs), 3) if xs else None,
                "winrate": round(sum(1 for x in xs if x > 0) / len(xs), 3) if xs else None}

    out = {"variants": [], "half_t": half}
    base = {"key": "base", "name_ru": "Как есть (для сравнения)"}
    for ex, pk in (("be", "ladder_be"), ("last", "last")):
        vals = [(r["t"] or 0, (_pol(r, pk) or {}).get("net")) for r in res]
        base[ex] = {**summ([v for _, v in vals]), "h1": summ([v for t, v in vals if t < half])["avg_pct"],
                    "h2": summ([v for t, v in vals if t >= half])["avg_pct"]}
    out["variants"].append(base)
    mk = {"key": "maker", "name_ru": f"Лимитные ордера вместо рыночных (комиссии на {MAKER_SAVING_PCT:.2f}% меньше)"}
    for ex in ("be", "last"):
        b = base[ex]
        mk[ex] = {"n": b["n"], "avg_pct": round(b["avg_pct"] + MAKER_SAVING_PCT, 3) if b["avg_pct"] is not None else None, "winrate": None,
                  "h1": round(b["h1"] + MAKER_SAVING_PCT, 3) if b["h1"] is not None else None,
                  "h2": round(b["h2"] + MAKER_SAVING_PCT, 3) if b["h2"] is not None else None}
    for key, name in WHATIF.items():
        rows_ = [r for r in res if (r["sim"].get("whatif") or {}).get(key)]
        if not rows_:
            continue
        v = {"key": key, "name_ru": name}
        for ex in ("be", "last"):
            vals = [(r["t"] or 0, r["sim"]["whatif"][key].get(ex)) for r in rows_]
            v[ex] = {**summ([x for _, x in vals]), "h1": summ([x for t, x in vals if t < half])["avg_pct"],
                     "h2": summ([x for t, x in vals if t >= half])["avg_pct"]}
        out["variants"].append(v)
    out["variants"].insert(1, mk)
    return out


def app_flags_check(rows) -> dict:
    """The app's targets[].is_completed come from real BingX orders (TradeReader). Compare with our price check on signals the
    app has closed and we have a verdict for (not filled counts as 0 TPs on our side)."""
    cmp_ = [r for r in rows if r["status"] == "closed" and r["targets"] and r["sim"].get("state") in ("closed", "not_filled")]
    ours = lambda r: r["sim"].get("best_tp", 0) if r["sim"].get("state") == "closed" else 0   # noqa: E731
    dis = [r for r in cmp_ if ours(r) != r["app_best_tp"]]
    by_mode = {}
    for r in cmp_:
        m = r["sim"].get("entry_mode") or "—"
        b = by_mode.setdefault(m, {"n": 0, "agree": 0})
        b["n"] += 1
        b["agree"] += int(ours(r) == r["app_best_tp"])
    return {"n": len(cmp_), "agree": len(cmp_) - len(dis), "agree_share": round((len(cmp_) - len(dis)) / len(cmp_), 3) if cmp_ else None,
            "app_more": sum(1 for r in dis if r["app_best_tp"] > ours(r)), "app_less": sum(1 for r in dis if r["app_best_tp"] < ours(r)),
            "within_one_tp": sum(1 for r in cmp_ if abs(ours(r) - r["app_best_tp"]) <= 1), "by_entry_mode": by_mode,
            "disagreements": [{"id": r["id"], "coin": r["coin"], "ours": ours(r), "app": r["app_best_tp"], "state": r["sim"].get("state"),
                               "reason": r["sim"].get("reason"), "entry_mode": r["sim"].get("entry_mode")} for r in dis[:10]]}


def _split(label_ru, rows, fn, labels: dict) -> dict:
    groups = {}
    for r in rows:
        k = fn(r)
        if k is None:
            continue
        groups.setdefault(k, []).append(r)
    out = [{"label": labels.get(k, str(k)), **grp(v)} for k, v in sorted(groups.items(), key=lambda kv: str(kv[0]))]
    n = sum(g["n"] for g in out)
    return {"feature_ru": label_ru, "groups": out, "n": n,
            "note_ru": f"рано судить: закрытых сигналов {n} < {MIN_SAMPLE}" if _small(n) else ""}


def build_profile(rows) -> dict:
    fr = [r for r in rows if r["feat"]]
    res = _resolved(rows)
    n = len(fr)
    prof = {"n_signals": n, "n_resolved": len(res), "sample_ok": not _small(len(res))}
    if not n:
        prof["summary_ru"] = "Пока нет сигналов с разобранными данными."
        return prof
    sides = {sd: sum(1 for r in fr if r["feat"].get("side") == sd) for sd in ("LONG", "SHORT")}
    arch = {}
    for r in fr:
        arch.setdefault(r["feat"].get("archetype") or "—", []).append(r)
    archetypes = sorted(({"label": k, "n": len(v), "share": round(len(v) / n, 3), **{f"res_{a}": b for a, b in grp(v).items()}}
                         for k, v in arch.items()), key=lambda x: -x["n"])
    F = [r["feat"] for r in fr]
    plan = {"median_sl_pct": _median([f.get("sl_pct") for f in F]),
            "median_tp1_pct": _median([(f.get("tp_pct") or [None])[0] for f in F]),
            "median_last_tp_pct": _median([(f.get("tp_pct") or [None])[-1] for f in F]),
            "median_rr_tp1": _median([(f.get("rr") or [None])[0] for f in F]),
            "median_rr_last": _median([(f.get("rr") or [None])[-1] for f in F]),
            "typical_n_tps": statistics.mode([f.get("n_tps") for f in F]) if F else None,
            "median_leverage": _median([f.get("leverage") for f in F]),
            "leverage_dist": {f"{k:g}": sum(1 for f in F if f.get("leverage") == k) for k in sorted({f.get("leverage") for f in F if f.get("leverage")})},
            "range_entry_share": round(sum(1 for f in F if f.get("range_entry")) / n, 3),
            "median_entry_vs_market_pct": _median([f.get("entry_vs_market_pct") for f in F]),
            "median_atr1h_pct": _median([f.get("atr1h_pct") for f in F])}
    ctx_known = [f for f in F if f.get("with_trend") is not None]
    btc_known = [f for f in F if f.get("with_btc") is not None]
    rsi_known = [f for f in F if f.get("rsi_wrong") is not None]
    context = {"with_trend_share": round(sum(1 for f in ctx_known if f["with_trend"]) / len(ctx_known), 3) if ctx_known else None,
               "with_trend_n": len(ctx_known),
               "with_btc_share": round(sum(1 for f in btc_known if f["with_btc"]) / len(btc_known), 3) if btc_known else None,
               "with_btc_n": len(btc_known),
               "rsi_wrong_share": round(sum(1 for f in rsi_known if f["rsi_wrong"]) / len(rsi_known), 3) if rsi_known else None,
               "median_rsi1h_long": _median([f.get("rsi1h") for f in F if f.get("side") == "LONG"]),
               "median_rsi1h_short": _median([f.get("rsi1h") for f in F if f.get("side") == "SHORT"]),
               "median_dist_hi24_long": _median([f.get("dist_hi24_pct") for f in F if f.get("side") == "LONG"]),
               "median_dist_lo24_short": _median([f.get("dist_lo24_pct") for f in F if f.get("side") == "SHORT"]),
               "median_move4h_long": _median([f.get("move4h_pct") for f in F if f.get("side") == "LONG"]),
               "median_move4h_short": _median([f.get("move4h_pct") for f in F if f.get("side") == "SHORT"])}
    hours = {}
    for f in F:
        b = _bucket_hour(f["hour_utc"])
        hours[b] = hours.get(b, 0) + 1
    coins = {}
    for f in F:
        coins[f["coin"]] = coins.get(f["coin"], 0) + 1
    sl_res = [r for r in res if r["sim"].get("ended") == "sl" and r["sim"].get("after_tp1") is not None]
    hunts = {"n": len(sl_res), "tp1_after_sl": sum(1 for r in sl_res if r["sim"]["after_tp1"])}
    rr = [r for r in fr]
    med_atr = plan["median_atr1h_pct"]
    splits = [
        _split("Лонг / шорт", rr, lambda r: r["side"], {"LONG": "лонг", "SHORT": "шорт"}),
        _split("По тренду монеты 4h (EMA50/EMA200)", rr, lambda r: r["feat"].get("with_trend"), {True: "по тренду", False: "против тренда"}),
        _split("По тренду BTC 4h", rr, lambda r: r["feat"].get("with_btc"), {True: "по тренду BTC", False: "против тренда BTC"}),
        _split("RSI 1h в «неправильную» сторону (лонг при ≥70, шорт при ≤30)", rr, lambda r: r["feat"].get("rsi_wrong"),
               {True: "RSI против сделки", False: "RSI нормальный"}),
        _split("Плечо", rr, lambda r: None if not r["feat"].get("leverage") else (r["feat"]["leverage"] > LEV_CAP),
               {False: f"до {LEV_CAP}x", True: f"больше {LEV_CAP}x"}),
        _split("Время сигнала (UTC)", rr, lambda r: _bucket_hour(r["feat"]["hour_utc"]) if r["feat"].get("hour_utc") is not None else None, {}),
        _split("Волатильность (ATR 1h к медиане)", rr,
               lambda r: None if med_atr is None or r["feat"].get("atr1h_pct") is None else r["feat"]["atr1h_pct"] > med_atr,
               {False: "спокойнее медианы", True: "волатильнее медианы"}),
        _split("R:R первой цели", rr, lambda r: None if not r["feat"].get("rr") or r["feat"]["rr"][0] is None else r["feat"]["rr"][0] >= 1,
               {False: "TP1 ближе стопа (R:R < 1)", True: "TP1 не ближе стопа (R:R ≥ 1)"}),
        _split("Тип входа", rr, lambda r: r["feat"].get("archetype"), {}),
    ]
    prof.update(sides=sides, archetypes=archetypes, plan=plan, context=context, by_hour=hours,
                by_weekday={WEEKDAYS_RU[k]: sum(1 for f in F if f.get("weekday") == k) for k in range(7)},
                coins_top=sorted(({"coin": c, "n": v} for c, v in coins.items()), key=lambda x: (-x["n"], x["coin"]))[:10],
                stop_then_tp1=hunts, splits=splits)
    # plain-language summary built only from the numbers above
    parts = []
    top = archetypes[0]
    parts.append(f"Чаще всего это «{top['label']}» — {top['n']} из {n} {_pl(n, 'сигнала', 'сигналов', 'сигналов')}; "
                 f"лонгов {sides['LONG']}, шортов {sides['SHORT']}.")
    if plan["median_sl_pct"] is not None and plan["median_tp1_pct"] is not None:
        txt = (f"Типичный план: стоп {plan['median_sl_pct']:.2f}% от входа, первая цель {plan['median_tp1_pct']:.2f}%"
               f" (R:R {plan['median_rr_tp1']}), последняя {plan['median_last_tp_pct']:.2f}% (R:R {plan['median_rr_last']}),"
               f" целей обычно {plan['typical_n_tps']}, плечо медиана {plan['median_leverage']:g}x.")
        parts.append(txt)
    if plan["median_entry_vs_market_pct"] is not None:
        e = plan["median_entry_vs_market_pct"]
        parts.append(f"Вход в среднем {'почти по рынку' if abs(e) < 0.3 else ('ниже рынка' if e < 0 else 'выше рынка')}"
                     f" ({e:+.2f}% от цены в момент сигнала).")
    if context["with_btc_share"] is not None:
        parts.append(f"По тренду BTC 4h — {context['with_btc_share'] * 100:.0f}% сигналов (из {context['with_btc_n']} с данными).")
    if hunts["n"]:
        parts.append(f"После стопа цена в течение 7 дней всё же дошла до TP1 в {hunts['tp1_after_sl']} из {hunts['n']} случаев.")
    if _small(len(res)):
        parts.append(f"Закрытых сигналов пока {len(res)} — меньше {MIN_SAMPLE}, разбиения по признакам ниже только для ориентира.")
    prof["summary_ru"] = " ".join(parts)
    return prof


def _ev(rows, key=BASELINE):
    p = pstats(rows, key)
    return {"n": p["n"], "winrate": p["winrate"], "avg_r": p["avg_r"], "avg_pct": p["avg_pct"], "sum_pct": p["sum_pct"]}


def _verdict_filter(n, before, after, skipped):
    if _small(n):
        return f"рано судить: закрытых сигналов {n} < {MIN_SAMPLE}"
    if skipped["n"] < 5:
        return f"почти нечего отсеивать ({skipped['n']} сигн.) — правило картину не меняет"
    if after["avg_r"] is None or before["avg_r"] is None:
        return "нет данных"
    d = after["avg_r"] - before["avg_r"]
    if d > 0.05 and (skipped["avg_r"] or 0) < 0:
        return (f"помогает: средний итог {before['avg_r']:+.2f}R → {after['avg_r']:+.2f}R; отсеянные {skipped['n']} сигн. "
                f"в среднем {skipped['avg_r']:+.2f}R")
    if d < -0.05:
        return f"вредит: {before['avg_r']:+.2f}R → {after['avg_r']:+.2f}R — отсеиваются в среднем прибыльные сигналы"
    return f"без заметной разницы ({before['avg_r']:+.2f}R → {after['avg_r']:+.2f}R)"


def build_proposals(rows, flags: dict | None = None) -> list[dict]:
    flags = flags if flags is not None else coin_filter_flags(rows)
    res = _resolved(rows)
    n = len(res)
    out = []
    names = {k: nm for k, nm, _ in POLICIES}
    # 1. best executable exit policy (each policy on the signals where every compared policy is closed)
    execs = [k for k, _, ex in POLICIES if ex]
    common = [r for r in res if all(_pol(r, k) for k in execs)]
    ranked = sorted(((k, _ev(common, k)) for k in execs), key=lambda kv: -(kv[1]["avg_r"] if kv[1]["avg_r"] is not None else -9))
    if ranked and ranked[0][1]["n"]:
        bk, bst = ranked[0]
        base = _ev(common, BASELINE)
        app = _ev(common, "app")
        v = (f"рано судить: закрытых сигналов {len(common)} < {MIN_SAMPLE}" if _small(len(common)) else
             (f"лучший исполнимый выход сейчас — «{names[bk]}»: {bst['avg_r']:+.2f}R на сделку против {base['avg_r']:+.2f}R"
              if bk != BASELINE else f"основной способ («{names[BASELINE]}») и так лучший из исполнимых: {bst['avg_r']:+.2f}R"))
        if app["avg_r"] is not None and bst["avg_r"] is not None:
            v += f". Витрина приложения показывает {app['avg_r']:+.2f}R — это потолок, не реальный результат"
        out.append({"id": "exit_policy", "rule_ru": "Выбрать способ выхода по целям", "n": len(common), "before": base, "after": bst,
                    "after_policy": bk, "ranking": [{"policy": k, "name_ru": names[k], **s_} for k, s_ in ranked], "verdict_ru": v})
    else:
        out.append({"id": "exit_policy", "rule_ru": "Выбрать способ выхода по целям", "n": 0, "before": None, "after": None,
                    "verdict_ru": f"рано судить: закрытых сигналов 0 < {MIN_SAMPLE}"})

    def filt(pid, rule_ru, cond, extra_note=""):
        known = [r for r in res if cond(r) is not None]
        skip = [r for r in res if cond(r) is True]
        keep = [r for r in res if cond(r) is not True]
        before, after, skipped = _ev(res), _ev(keep), _ev(skip)
        v = _verdict_filter(n, before, after, skipped)
        if extra_note and not _small(n):
            v += ". " + extra_note
        out.append({"id": pid, "rule_ru": rule_ru, "n": n, "known": len(known), "skipped": len(skip), "before": before,
                    "after": after, "skipped_stats": skipped, "verdict_ru": v})

    filt("skip_vs_btc", "Не брать сигналы против тренда BTC 4h (EMA50/EMA200)",
         lambda r: None if r["feat"].get("with_btc") is None else not r["feat"]["with_btc"])
    filt("skip_vs_trend", "Не брать сигналы против тренда самой монеты 4h",
         lambda r: None if r["feat"].get("with_trend") is None else not r["feat"]["with_trend"])
    filt("skip_rsi_wrong", "Не брать лонг при RSI 1h ≥ 70 и шорт при RSI 1h ≤ 30",
         lambda r: r["feat"].get("rsi_wrong"))
    filt("coin_filter", f"Фильтр монет: не брать монету, если у неё ≥{COIN_FILTER_MIN} прошлых закрытых сигналов и "
                        f"≥{COIN_FILTER_SHARE * 100:.0f}% из них — стоп без единого TP (считается только по прошлому)",
         lambda r: (flags.get(r["id"]) or {}).get("skip"),
         "правило экспериментальное: соседние пороги (например ≥8 сигналов) на флагах приложения не подтвердились")
    # 4. stop to entry after TP1
    both = [r for r in res if _pol(r, "ladder") and _pol(r, "ladder_be")]
    b, a = _ev(both, "ladder"), _ev(both, "ladder_be")
    if _small(len(both)):
        v = f"рано судить: закрытых сигналов {len(both)} < {MIN_SAMPLE}"
    elif a["avg_r"] is not None and b["avg_r"] is not None:
        d = a["avg_r"] - b["avg_r"]
        v = (f"{'помогает' if d > 0.03 else 'вредит' if d < -0.03 else 'без заметной разницы'}: "
             f"{b['avg_r']:+.2f}R → {a['avg_r']:+.2f}R, прибыльных сделок {b['winrate'] * 100:.0f}% → {a['winrate'] * 100:.0f}%")
    else:
        v = "нет данных"
    out.append({"id": "be_after_tp1", "rule_ru": "После TP1 переносить стоп в точку входа (выход равными частями)", "n": len(both),
                "before": b, "after": a, "verdict_ru": v})
    # 5. leverage cap
    lev_rows = [r for r in res if _pol(r, BASELINE)]
    if lev_rows:
        def lev_of(r, cap=None):
            p = _pol(r, BASELINE)
            L = r["leverage"] if cap is None else min(r["leverage"], cap)
            liq = r["sim"].get("liquidated") and (cap is None or L == r["leverage"])
            if cap is not None and L < r["leverage"]:   # recheck liquidation with the lower leverage
                lm = 100.0 / L - 0.5 if L > 1 else None
                liq = bool(lm and r["sim"].get("mae_pct", 0) >= lm)
            if liq and cap is not None and L < r["leverage"]:
                return -100.0
            return p["lev"] if cap is None else max(p["net"] * L, -100.0)
        bl = [lev_of(r) for r in lev_rows]
        al = [lev_of(r, LEV_CAP) for r in lev_rows]
        over = sum(1 for r in lev_rows if r["leverage"] > LEV_CAP)
        liq_n = sum(1 for r in lev_rows if r["sim"].get("liquidated"))
        liq_risk = sum(1 for r in lev_rows if r["sim"].get("liq_risk"))
        bst = {"n": len(bl), "avg_pct_lev": round(statistics.fmean(bl), 2), "worst_lev": round(min(bl), 1), "liquidations": liq_n,
               "stop_beyond_liquidation": liq_risk}
        ast = {"n": len(al), "avg_pct_lev": round(statistics.fmean(al), 2), "worst_lev": round(min(al), 1),
               "liquidations": sum(1 for x in al if x <= -100.0)}
        if _small(len(lev_rows)):
            v = f"рано судить: закрытых сигналов {len(lev_rows)} < {MIN_SAMPLE}"
        elif not over:
            v = f"плечо и так не выше {LEV_CAP}x — ограничение ничего не меняет"
        else:
            v = (f"плечо не меняет знак результата, только размах: средний итог на маржу {bst['avg_pct_lev']:+.1f}% → "
                 f"{ast['avg_pct_lev']:+.1f}%, худшая сделка {bst['worst_lev']:+.0f}% → {ast['worst_lev']:+.0f}%")
        if liq_risk:
            v += f". У {liq_risk} сигн. стоп дальше цены ликвидации при заявленном плече — позицию закроет ликвидация, а не стоп"
        out.append({"id": "lev_cap", "rule_ru": f"Ограничить плечо до {LEV_CAP}x", "n": len(lev_rows), "over_cap": over,
                    "before": bst, "after": ast, "verdict_ru": v})
    else:
        out.append({"id": "lev_cap", "rule_ru": f"Ограничить плечо до {LEV_CAP}x", "n": 0, "before": None, "after": None,
                    "verdict_ru": f"рано судить: закрытых сигналов 0 < {MIN_SAMPLE}"})
    # 6. time of day: the worst 6-hour UTC window (chosen on the same history → needs checking on new signals)
    buckets = {}
    for r in res:
        h = r["feat"].get("hour_utc")
        if h is not None:
            buckets.setdefault(_bucket_hour(h), []).append(r)
    cand = [(b_, _ev(v_)) for b_, v_ in buckets.items() if len(v_) >= 5]
    cand = [c for c in cand if c[1]["avg_r"] is not None]
    if cand:
        worst = min(cand, key=lambda c: c[1]["avg_r"])[0]
        filt("time_filter", f"Не брать сигналы в окне {worst}", lambda r: None if r["feat"].get("hour_utc") is None
             else _bucket_hour(r["feat"]["hour_utc"]) == worst,
             "окно выбрано по этой же истории — проверять на новых сигналах, прежде чем верить")
    else:
        out.append({"id": "time_filter", "rule_ru": "Фильтр по времени суток (UTC, окна по 6 часов)", "n": n, "before": None,
                    "after": None, "verdict_ru": f"рано судить: закрытых сигналов {n} < {MIN_SAMPLE}" if _small(n)
                    else "ни в одном окне нет 5+ закрытых сигналов"})
    return out


def _stop_no_tp(r) -> bool:
    return r["sim"].get("ended") == "sl" and r["sim"].get("best_tp", 0) == 0


def _coin_history(rows) -> dict[str, list]:
    out: dict[str, list] = {}
    for r in sorted(_resolved(rows), key=lambda x: x["sim"].get("resolved_at") or 0):
        if r["coin"] and r["sim"].get("resolved_at"):
            out.setdefault(r["coin"], []).append((r["sim"]["resolved_at"], _stop_no_tp(r)))
    return out


def coin_filter_flags(rows) -> dict[str, dict]:
    """Walk-forward: at each signal's time only that coin's signals RESOLVED BEFORE it count (no look into the future)."""
    hist = _coin_history(rows)
    out = {}
    for r in rows:
        if not r.get("t") or not r.get("coin"):
            continue
        past = [x for ra, x in hist.get(r["coin"], []) if ra < r["t"]]
        n, k = len(past), sum(past)
        out[r["id"]] = {"past": n, "stops": k, "skip": n >= COIN_FILTER_MIN and k / n >= COIN_FILTER_SHARE}
    return out


def coin_blacklist(rows, now: int) -> list[dict]:
    """Coins the filter would skip right now."""
    out = []
    for coin, past in _coin_history(rows).items():
        past = [x for ra, x in past if ra <= now]
        n, k = len(past), sum(past)
        if n >= COIN_FILTER_MIN and k / n >= COIN_FILTER_SHARE:
            out.append({"coin": coin, "n": n, "stops_no_tp": k, "share": round(k / n, 3)})
    return sorted(out, key=lambda x: (-x["share"], -x["n"], x["coin"]))


def _margin_return(r: dict, net: float, lev: float) -> float:
    """% of margin for a closed trade at leverage `lev`: net % × leverage, −100 % at most; a stop beyond the liquidation price
    (≈ 100 / leverage − 0.5 % against the position) loses the whole margin."""
    sim = r["sim"]
    if lev > 1 and sim.get("first") == "sl" and (sim.get("risk_pct") or 0) >= 100.0 / lev - 0.5:
        return -100.0
    return max(net * lev, -100.0)


def run_account(rows, key: str, name_ru: str, rules_ru: str, policy: str, use_filter: bool, start_t: int, now: int,
                flags: dict) -> dict:
    """Demo account: $10 000, margin 5 % of the (realized) equity at entry, leverage = the signal's capped at 20×, the sim's fees;
    trades opened at the fill and closed when the policy closes, compounded in time order."""
    trades, skipped = [], 0
    for r in rows:
        sim = r["sim"]
        if not r["t"] or r["t"] < start_t or sim.get("fill") is None or sim.get("state") not in ("open", "closed"):
            continue
        if use_filter and (flags.get(r["id"]) or {}).get("skip"):
            skipped += 1
            continue
        if policy.startswith("smart:"):
            mv = (r.get("feat") or {}).get("btc_move4h_pct")
            if mv is None or not mv:
                skipped += 1
                continue
            if (r["side"] == "LONG") == (mv > 0):                 # with BTC's last 4 h candle → take the signal as it is
                p = _pol(r, "last")
                trades.append({"r": r, "open_t": sim["fill_t"], "close_t": p["t"] if p else None, "net": p["net"] if p else None,
                               "lev": min(float(r["leverage"] or 1.0), ACCOUNT_LEV_CAP)})
                continue
            wi = (sim.get("whatif") or {}).get("rev")                # against it → flipped, held to the last (mirrored) target or stop
            if not wi or wi.get("fill_t") is None:
                continue
            closed_ = wi.get("t_last") if wi.get("state") == "closed" else None
            rr = {**r, "side": "SHORT" if r["side"] == "LONG" else "LONG", "sim": {**sim, "first": wi.get("first"), "risk_pct": wi.get("risk_pct")}}
            trades.append({"r": rr, "open_t": wi["fill_t"], "close_t": closed_, "net": wi.get("last") if closed_ else None,
                           "lev": min(float(r["leverage"] or 1.0), ACCOUNT_LEV_CAP)})
            continue
        if policy.startswith("both:"):                            # as is AND mirrored, half the margin each
            p = _pol(r, "last")
            wi = (sim.get("whatif") or {}).get("rev")
            lev = min(float(r["leverage"] or 1.0), ACCOUNT_LEV_CAP)
            if p:
                trades.append({"r": r, "open_t": sim["fill_t"], "close_t": p["t"], "net": p["net"], "lev": lev, "w": 0.5})
            if wi and wi.get("fill_t") is not None:
                closed_ = wi.get("t_last") if wi.get("state") == "closed" else None
                rr = {**r, "side": "SHORT" if r["side"] == "LONG" else "LONG", "sim": {**sim, "first": wi.get("first"), "risk_pct": wi.get("risk_pct")}}
                trades.append({"r": rr, "open_t": wi["fill_t"], "close_t": closed_, "net": wi.get("last") if closed_ else None, "lev": lev, "w": 0.5})
            continue
        if policy.startswith("rev_long:") and r["side"] != "LONG":
            skipped += 1
            continue
        if policy.startswith("rev:") or policy.startswith("rev_long:"):
            wi = (sim.get("whatif") or {}).get("rev")
            if not wi or wi.get("fill_t") is None:
                continue
            closed_ = wi.get("t_be") if wi.get("state") == "closed" else None
            rr = {**r, "side": "SHORT" if r["side"] == "LONG" else "LONG", "sim": {**sim, "first": wi.get("first"), "risk_pct": wi.get("risk_pct")}}
            trades.append({"r": rr, "open_t": wi["fill_t"], "close_t": closed_, "net": wi.get("be") if closed_ else None,
                           "lev": min(float(r["leverage"] or 1.0), ACCOUNT_LEV_CAP)})
            continue
        p = _pol(r, policy)
        trades.append({"r": r, "open_t": sim["fill_t"], "close_t": p["t"] if p else None, "net": p["net"] if p else None,
                       "lev": min(float(r["leverage"] or 1.0), ACCOUNT_LEV_CAP)})
    events = [(tr["open_t"], 0, i) for i, tr in enumerate(trades)] + [(tr["close_t"], 1, i) for i, tr in enumerate(trades) if tr["close_t"]]
    equity = peak = ACCOUNT_START
    max_dd, curve, closed = 0.0, [[start_t, ACCOUNT_START]], []
    for t, kind, i in sorted(events, key=lambda e: (e[0], e[1])):
        tr = trades[i]
        if kind == 0:
            tr["margin"] = equity * ACCOUNT_MARGIN_SHARE * tr.get("w", 1.0)
            continue
        m = _margin_return(tr["r"], tr["net"], tr["lev"])
        pnl = tr["margin"] * m / 100.0
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100 if peak else 0.0)
        curve.append([t, round(equity, 2)])
        r = tr["r"]
        closed.append({"id": r["id"], "coin": r["coin"], "side": r["side"], "opened": tr["open_t"], "closed": t,
                       "leverage": tr["lev"], "margin": round(tr["margin"], 2), "pnl_usd": round(pnl, 2), "pnl_pct": round(m, 2),
                       "outcome_ru": _state_ru(r["sim"], r["status"])})
    opened = []
    for tr in trades:
        if tr["close_t"]:
            continue
        r, sim = tr["r"], tr["r"]["sim"]
        s_ = 1 if r["side"] == "LONG" else -1
        px = sim.get("last_price")
        m = max(s_ * (px - sim["fill"]) / sim["fill"] * 100 * tr["lev"], -100.0) if px else None
        opened.append({"id": r["id"], "coin": r["coin"], "side": r["side"], "opened": tr["open_t"], "leverage": tr["lev"],
                       "margin": round(tr.get("margin") or 0.0, 2), "best_tp": sim.get("best_tp", 0), "price": px,
                       "unrealized_pct": round(m, 2) if m is not None else None,
                       "unrealized_usd": round((tr.get("margin") or 0.0) * m / 100, 2) if m is not None else None})
    wins = sum(1 for c in closed if c["pnl_usd"] > 0)
    unreal = sum(o["unrealized_usd"] or 0.0 for o in opened)
    return {"key": key, "name_ru": name_ru, "rules_ru": rules_ru, "policy": policy, "coin_filter": use_filter,
            "start": ACCOUNT_START, "start_t": start_t, "equity": round(equity, 2), "equity_with_open": round(equity + unreal, 2),
            "pnl_usd": round(equity - ACCOUNT_START, 2), "pnl_pct": round((equity / ACCOUNT_START - 1) * 100, 2),
            "trades": len(closed), "wins": wins, "winrate": round(wins / len(closed), 3) if closed else None,
            "max_dd_pct": round(max_dd, 2), "skipped_by_filter": skipped, "open": opened,
            "last": sorted(closed, key=lambda c: -c["closed"])[:10], "curve": curve}


def build_accounts(rows, now: int, flags: dict) -> dict:
    ts = [r["t"] for r in rows if r["t"]]
    first = min(ts) if ts else now
    first_ru = datetime.fromtimestamp(first / 1000, timezone(timedelta(hours=3))).strftime("%d.%m.%Y")
    views = {"since_live": (LIVE_START_MS, "Живой тест: сигналы с 18.10.2026 00:00 по Киеву"),
             "backfill": (first, f"Если бы начали {first_ru} — на истории (не живой результат)")}
    out = {"params": {"start": ACCOUNT_START, "margin_share": ACCOUNT_MARGIN_SHARE, "lev_cap": ACCOUNT_LEV_CAP, "cost_pct": COST_PCT,
                      "note_ru": f"Старт $10 000, маржа на сделку 5% от капитала на момент входа, плечо как в сигнале, но не больше "
                                 f"{ACCOUNT_LEV_CAP}x, комиссии {COST_PCT:.2f}% от цены на сделку, убыток на сделку не больше маржи; "
                                 f"результаты складываются по времени закрытия."}}
    for view, (start_t, label) in views.items():
        out[view] = {"label_ru": label, "start_t": start_t,
                     **{k: run_account(rows, k, nm, rules, pol, filt, start_t, now, flags) for k, nm, rules, pol, filt in ACCOUNTS}}
    return out


def _f(x, nd=6):
    return None if x is None else round(float(x), nd)


def _pl(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def _state_ru(sim: dict, status: str) -> str:
    st = sim.get("state")
    if st == "pending":
        return "ждём свечи для проверки" if sim.get("reason") in ("wait_1m", "no_start", "no_candles") else "ждёт входа"
    if st == "not_filled":
        return {"tp1_before_entry": "не вошёл — цена ушла к TP1 без входа", "sl_before_entry": "не вошёл — стоп раньше входа"}.get(
            sim.get("reason"), "не вошёл за 48 ч")
    if st == "open":
        b = sim.get("best_tp", 0)
        return f"в позиции, достигнут TP{b}" if b else "в позиции"
    if st == "closed":
        b, n, e = sim.get("best_tp", 0), sim.get("n_tps", 0), sim.get("ended")
        if e == "last_tp":
            return f"все цели (TP{n})"
        if e == "sl":
            return f"TP{b} → стоп" if b else "стоп"
        return f"по времени (14 дн.), достигнут TP{b}" if b else "по времени (14 дн.)"
    if st in ("bad_data", "no_data"):
        return "нет данных для проверки"
    return st or "—"


def _brief(r: dict) -> dict:
    sim = r["sim"]
    return {"id": r["id"], "name": r["name"], "coin": r["coin"], "side": r["side"], "t": r["t"], "leverage": r["leverage"],
            "entry_lo": r["entry_lo"], "entry_hi": r["entry_hi"], "sl": r["sl"],
            "targets": [{"level": x["level"], "price": x["price"], "app_done": x["done"]} for x in r["targets"]],
            "state": sim.get("state"), "state_ru": _state_ru(sim, r["status"]), "best_tp": sim.get("best_tp", 0),
            "n_tps": sim.get("n_tps"), "first": sim.get("first"), "fill": _f(sim.get("fill")),
            "time_to_tp1_h": sim.get("time_to_tp1_h"), "mfe_pct": sim.get("mfe_pct"), "mae_pct": sim.get("mae_pct"),
            "app_best_tp": r["app_best_tp"], "archetype": r["feat"].get("archetype"), "with_btc": r["feat"].get("with_btc"),
            "rsi1h": r["feat"].get("rsi1h"), "status": r["status"]}


def _active_item(r: dict) -> dict:
    d = _brief(r)
    sim = r["sim"]
    s = 1 if r["side"] == "LONG" else -1
    px = sim.get("last_price")
    best = sim.get("best_tp", 0)
    d["targets"] = [dict(x, hit=i < best) for i, x in enumerate(d["targets"])]
    d.update(price=_f(px), price_t=sim.get("last_t"), age_h=None, app_actual_price=r["actual_price"], app_actual_pct=r["actual_pct"])
    if px:
        tps = [x["price"] for x in r["targets"]]
        if best < len(tps):
            nxt = tps[best]
            d["next_tp"] = {"level": r["targets"][best]["level"], "price": nxt, "to_pct": round(s * (nxt - px) / px * 100, 3)}
        d["to_sl_pct"] = round(s * (px - r["sl"]) / px * 100, 3) if r["sl"] else None
        if sim.get("fill"):
            g = s * (px - sim["fill"]) / sim["fill"] * 100
            d.update(pnl_pct=round(g, 3), pnl_lev_pct=round(max(g * r["leverage"], -100.0), 2))
        elif r["entry_lo"]:
            lo, hi = r["entry_lo"], r["entry_hi"]
            near = hi if px > hi else lo if px < lo else px
            d["to_entry_pct"] = round((near - px) / px * 100, 3)
    return d


def build_doc(conn, now: int, notes: list, api: dict, sources: dict) -> dict:
    rows = _load(conn)
    sims = [r["sim"].get("state") for r in rows]
    counts = {"total": len(rows), "active": sum(1 for r in rows if r["status"] == "active"),
              "closed": sum(1 for r in rows if r["status"] == "closed"), "resolved": sims.count("closed"),
              "not_filled": sims.count("not_filled"), "open": sims.count("open"), "pending": sims.count("pending") + sims.count(None),
              "no_data": sims.count("bad_data") + sims.count("no_data"),
              "entry_market": sum(1 for r in rows if r["sim"].get("entry_mode") == "market"),
              "entry_limit": sum(1 for r in rows if r["sim"].get("entry_mode") == "limit"),
              "waiting_candles": sum(1 for r in rows if r["sim"].get("state") in ("pending", None) and r["sim"].get("reason") in
                                     (None, "wait_1m", "no_start", "no_candles"))}
    stats = build_stats(rows)
    active = []
    for r in rows:
        if r["status"] == "active":
            a = _active_item(r)
            a["age_h"] = round((now - r["t"]) / H1, 1) if r["t"] else None
            active.append(a)
    recent = []
    for r in rows:
        if r["sim"].get("state") not in ("closed", "not_filled") and r["status"] != "closed":
            continue
        b = _brief(r)
        b["policies"] = {k: ({"pct": p["net"], "lev": p["lev"], "r": p["r"]} if p else None)
                         for k, p in ((k, _pol(r, k)) for k, _, _ in POLICIES)}
        recent.append(b)
        if len(recent) >= 30:
            break
    profile = build_profile(rows)
    flags = coin_filter_flags(rows)
    for a in active:
        a["coin_filter_skip"] = bool((flags.get(a["id"]) or {}).get("skip"))
    proposals = build_proposals(rows, flags)
    accounts = build_accounts(rows, now, flags)
    blacklist = coin_blacklist(rows, now)
    # summary from numbers only
    P = stats["policies"]
    parts = [f"Отслежено {counts['total']} {_pl(counts['total'], 'сигнал', 'сигнала', 'сигналов')} приложения: активных сейчас "
             f"{counts['active']}, закрыто по нашей проверке ценами {counts['resolved']}, не вошли {counts['not_filled']} "
             f"(рыночных входов {counts['entry_market']}, лимитных {counts['entry_limit']})."]
    if counts["resolved"]:
        m, a, b = P[MAIN_POLICY], P["app"], P["tp1"]
        parts.append(f"Основной способ — равными частями по целям, после TP1 стоп в точку входа: {m['avg_pct']:+.2f}% на сделку без плеча "
                     f"после комиссий ({m['avg_r']:+.2f}R, в плюс {m['winrate'] * 100:.0f}% сделок); для сравнения витрина приложения "
                     f"(самый дальний TP) {a['avg_pct']:+.2f}%, всё на TP1 {b['avg_pct']:+.2f}%.")
        if stats["tp_reach"].get("tp1") is not None and stats["sl_share"] is not None:
            parts.append(f"TP1 достигнут в {stats['tp_reach']['tp1'] * 100:.0f}% закрытых сигналов, стоп раньше TP1 — в {stats['sl_share'] * 100:.0f}%.")
    bf = accounts["backfill"]
    if bf["improved"]["trades"]:
        parts.append(f"Демо-счёт «{bf['improved']['name_ru']}» на истории: $10 000 → ${bf['improved']['equity']:,.0f} "
                     f"({bf['improved']['pnl_pct']:+.1f}%), «{bf['as_is']['name_ru']}» — ${bf['as_is']['equity']:,.0f} "
                     f"({bf['as_is']['pnl_pct']:+.1f}%).".replace(",", " "))
    rv = bf.get("reverse") or {}
    if rv.get("trades"):
        parts.append(f"Если делать наоборот (разворот сигнала, эксперимент): $10 000 → ${rv['equity']:,.0f} ({rv['pnl_pct']:+.1f}%) на той же истории.".replace(",", " "))
    if counts["resolved"] < MIN_SAMPLE:
        parts = parts[:3] + [f"Закрытых сигналов {counts['resolved']} — меньше {MIN_SAMPLE}, выводы предварительные."]
    method = [
        "Итог считаем сами по свечам 15m бессрочных фьючерсов (BingX, запасные OKX и Binance), флаги «достигнут» из приложения не используем — только сравниваем.",
        f"Отсчёт с минуты сигнала: первые 3 часа по 1-минутным свечам, дальше по 15-минутным. Вход в пределах {MARKET_TOL_PCT}% от цены в минуту сигнала — рыночный (Cornix ставит ордер сразу): считаем исполненным по цене входа в момент сигнала.",
        "Вход дальше от рынка — лимитный ордер: нужно касание цены входа (или любой цены диапазона) в течение 48 ч. Нет касания, или раньше дошли до TP1 / стопа — «не вошёл».",
        "Если одна свеча задела и стоп, и цель — считаем, что первым был стоп (осторожно). Для лимитного входа на свече касания цели не засчитываем, если вход был не по её открытию.",
        f"Комиссия 0.05% на вход и 0.05% на выход + 0.02% проскальзывание = {COST_PCT:.2f}% от цены на сделку. R — расстояние от входа до стопа. Плечо только умножает % (убыток на маржу не больше −100%).",
        "Позиция без выхода 14 дней закрывается по рынку («по времени»). После стопа ещё 7 дней смотрим, вернулась ли цена к TP1.",
        f"Всё, где закрытых сигналов меньше {MIN_SAMPLE}, помечено «рано судить».",
    ]
    bad = {}
    for r in rows:
        if r["sim"].get("state") in ("bad_data", "no_data"):
            k = f"{r['sim'].get('reason') or r['sim'].get('state')}"
            bad.setdefault(k, []).append(r["coin"] or r["name"] or r["id"])
    af = stats["app_flags"]
    if af["n"]:
        notes = notes + [f"Флаги приложения (реальные ордера BingX через TradeReader) против нашей проверки ценами: совпал самый дальний TP "
                         f"в {af['agree']} из {af['n']} закрытых сигналов, расхождение не больше одной цели — в {af['within_one_tp']}."
                         + (" Расхождения (id: наш TP / TP приложения): " + ", ".join(
                             f"{d['id']} {d['coin']}: {d['ours']}/{d['app']}" + (" (не вошёл)" if d["state"] == "not_filled" else "")
                             for d in af["disagreements"]) if af["disagreements"] else "")]
    if bad:
        notes = notes + ["Без проверки ценой: " + "; ".join(f"{k} ×{len(v)} ({', '.join(sorted({str(x) for x in v})[:6])})"
                                                          for k, v in sorted(bad.items()))]
    return {"asof": now, "source": {"api": [API_ACTIVE, API_HISTORY], "api_ok": api, "candles": sources},
            "policies": [{"key": k, "name_ru": nm, "executable": ex, "main": k == MAIN_POLICY} for k, nm, ex in POLICIES],
            "main_policy": MAIN_POLICY, "baseline": BASELINE,
            "counts": counts, "stats": stats, "active": active, "recent": recent, "profile": profile, "proposals": proposals,
            "accounts": accounts,
            "coin_filter": {"rule_ru": f"Не брать монету, если у неё ≥{COIN_FILTER_MIN} закрытых сигналов в прошлом и ≥"
                                       f"{COIN_FILTER_SHARE * 100:.0f}% из них — стоп без единого TP",
                            "min_signals": COIN_FILTER_MIN, "stop_share": COIN_FILTER_SHARE, "blacklist": blacklist},
            "summary_ru": " ".join(parts[:5]), "method_ru": method, "notes_ru": notes}


# ------------------------------------------------------------------------------------------------------------ entry points
def run(conn, now: int | None = None, fetch_json=None, klines=None, budget_s: float = 240, max_requests: int = 220) -> dict:
    now = int(now if now is not None else time.time() * 1000)
    ensure_schema(conn)
    notes: list[str] = []
    fetch_json = fetch_json or _get_api
    lists, api = {}, {}
    for kind, status, url in (("active", "active", API_ACTIVE), ("history", "closed", API_HISTORY)):
        try:
            items, err = extract_list(fetch_json(url))
        except Exception as e:  # noqa: BLE001
            items, err = None, f"{type(e).__name__}: {str(e)[:120]}"
        api[kind] = items is not None
        if items is None:
            notes.append(f"API {kind}: не удалось получить список ({err})")
        else:
            lists[status] = items
            api[f"{kind}_n"] = len(items)
    # 18.10: does the app's backend keep more than a month? (the owner wants the flip idea checked over ~2 years)
    # Probe a few common paging parameters once a day; any answer longer / older than the plain list is merged in.
    if fetch_json is _get_api and lists.get("closed") is not None:
        try:
            last = conn.execute("SELECT v FROM app_meta WHERE k='probe_t'").fetchone() if conn.execute(
                "SELECT name FROM sqlite_master WHERE name='app_meta'").fetchone() else None
        except sqlite3.Error:
            last = None
        if not last or now - int(last[0]) > DAY:
            conn.execute("CREATE TABLE IF NOT EXISTS app_meta (k TEXT PRIMARY KEY, v TEXT)")
            base_n = len(lists["closed"])
            base_min = min((int(x.get("date") or 0) for x in lists["closed"] if isinstance(x, dict) and x.get("date")), default=0)
            probe = {}
            for q in ("limit=5000", "size=5000", "page=2", "offset=300", "skip=300", "days=730", f"from={now - 730 * DAY}"):
                try:
                    items, _ = extract_list(fetch_json(API_HISTORY + "?" + q))
                except Exception as e:  # noqa: BLE001
                    probe[q] = f"ошибка {type(e).__name__}"
                    continue
                if items is None:
                    probe[q] = "нет списка"
                    continue
                mn = min((int(x.get("date") or 0) for x in items if isinstance(x, dict) and x.get("date")), default=0)
                probe[q] = {"n": len(items), "oldest": mn}
                if len(items) > base_n or (mn and base_min and mn < base_min):
                    known = {x.get("signal_id") for x in lists["closed"] if isinstance(x, dict)}
                    lists["closed"] += [x for x in items if isinstance(x, dict) and x.get("signal_id") not in known]
            api["history_probe"] = probe
            conn.execute("INSERT OR REPLACE INTO app_meta VALUES ('probe_t', ?)", (str(now),))
            conn.execute("INSERT OR REPLACE INTO app_meta VALUES ('probe', ?)", (json.dumps(probe),))
            conn.commit()
    pstats_ = upsert(conn, lists, now, active_ok=api.get("active", False))
    if pstats_["extra_keys"]:
        notes.append("Новые поля в ответе API (пока не используются): " + ", ".join(pstats_["extra_keys"][:20]))
    probs = {k: v for k, v in pstats_["problems"].items() if k not in ("target_keys",)}
    if probs:
        notes.append("Не распознано в сигналах: " + ", ".join(f"{k} ×{v}" for k, v in sorted(probs.items())))
        for ex in pstats_["examples"][:2]:
            notes.append("Пример непонятного сигнала: " + json.dumps(ex, ensure_ascii=False)[:300])
    src = klines or KlineSource()
    try:                            # a big backlog (first backfill, a simulator upgrade) gets a bigger budget so it clears in 1–2 runs
        backlog = conn.execute("SELECT COUNT(*) FROM app_signals WHERE done=0").fetchone()[0]
    except sqlite3.Error:
        backlog = 0
    if backlog > BACKLOG_BIG and klines is None:
        max_requests, budget_s = max(max_requests, BACKLOG_REQUESTS), max(budget_s, BACKLOG_SECONDS)
    budget = _Budget(budget_s, max_requests)
    st = process(conn, now, src, budget, notes)
    pruned = prune(conn, now)
    sources = dict(getattr(src, "used", {}) or {})
    sources.update(requests=budget.used, unfinished=st["unfinished"], fetch_errors=st["fetch_errors"],
                   left_for_next_run=st["skipped_budget"], pruned_rows=pruned)
    for e in (getattr(src, "errors", None) or [])[:5]:
        if len(notes) < 50:
            notes.append("Свечи: " + e)
    return build_doc(conn, now, notes, api, sources)


def _clean(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


def write(out_dir: str = "web", conn=None, **kw) -> dict:
    if conn is None:
        os.makedirs("data", exist_ok=True)
        conn = sqlite3.connect("data/agents.sqlite")
    d = _clean(run(conn, **kw))
    os.makedirs(out_dir, exist_ok=True)
    tmp = os.path.join(out_dir, "app_signals.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(out_dir, "app_signals.json"))
    return d
