"""Public prediction scorecard (20.10.2026, owner: show the real process publicly and honestly — every forecast and signal the
system publishes is written down when it is published, checked later against real prices and scored; all of them, no
cherry-picking).

Record   data/scorecard_ledger.jsonl — one JSON line per published prediction, appended when the engine first sees it in a
         public feed. At 256 KB the file is closed unchanged into data/scorecard/ledger-<its first line number>.jsonl and a new one
         continues the same chain, so a git commit never rewrites more than 256 KB of it (the same for the outcomes). SHA-256 hash chain: a line's "h" = sha256(previous line's h + the line's text without its "h"), the first
         line starts from 64 zeros. Changing, removing, inserting or reordering any line breaks every hash after it.
         Lines taken from older ledgers (agents.sqlite, git history of the feeds) when the scorecard started carry "bf": 1.
Resolve  data/scorecard_outcomes.jsonl — one line per checked prediction, its own hash chain; every outcome names the hash of
         the prediction it checks ("ph"). Prices: the engine's own 15-minute candles (data/cnq.sqlite), and only candles that
         opened at or after the prediction's time — never data from before publication. Signals: the desk's own close
         (stop / target / time / rule exit, fees included) as published in the feed; the app's 1.x signals: app_signals.py's
         candle check (main policy, costs included).
Score    web/scorecard.json (ru + en) for web/scorecard.html: per source and horizon — count, checked, hit rate, Brier vs 0.25 (a
         coin) and vs climatology (always saying the observed base rate), calibration table; signals — win rate, average % and R
         after fees, against simply buying the same coin for the same time. Misses are listed next to hits.

Sources (key → where it is published):
  ab    A/B 24 h forecast, «A x %» (web/calibration.json open; cnq/agents/calibration.py)
  band  the 2-of-3 price band for day / week / month, BTC ETH SOL (web/outlook.json) — one per window, windows never overlap
  base  «share of periods that ended higher in this BTC regime» (web/outlook.json), same windows
  news  the LLM news analyst's direction calls with confidence and horizon (web/decision.json news.impacts)
  feed  CRYPTO NOVA signals (web/signals_feed.json — the app and the Telegram signal channel)
  app   the app's 1.x signals (Ai Golden Crypto bot via the app API; web/app_signals.json)

Verify: python3 scripts/verify_scorecard.py. Engine step (cnq/engine.py FAST, «scorecard»): stdlib only, cheap, never raises —
a missing or broken input only skips that source and is listed in "errors"; a broken chain is never appended to."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

GENESIS = "0" * 64
M15 = 900_000
HOUR = 3_600_000
LEDGER = "scorecard_ledger.jsonl"
OUTCOMES = "scorecard_outcomes.jsonl"
FEED = "scorecard.json"
SEG_DIR = "scorecard"           # data/scorecard/: closed segments of both chains
SEG_BYTES = 256_000             # the active file is closed into a segment at this size → a commit never rewrites more than this
BUCKET_MS = 4 * HOUR            # calibration.py records one A/B forecast per coin per 4-hour bucket; its id = SYMBOL-<bucket>
FEE_RT_PCT = 0.14               # «always long» baseline: 0.05 % fee + 0.02 % slippage per side, as on the desks (paper_desk FEE/SLIP)
VOID_AFTER_H = 72               # still not checkable 72 h after its horizon (no candles for the coin) → «не проверено», counted
MIN_PROB_N = 30                 # below this a verdict is «мало данных»
MIN_SIG_N = 20                  # autolab.live_check / signal_feed: judge a signal set from 20 closed trades
REWRITE_S = 900                 # scorecard.json is rewritten when something changed, else at most every 15 minutes
FRESH_S = 3 * 3600              # a feed older than this is not «being published now» — the live recorder skips it
LATE_S = 2 * 3600               # recorded more than 2 h after its own time → "late": 1 (shown; it proves less than an on-time line)
RECENT_N = 50
OUTLOOK_HZ = {"day": 24, "week": 168, "month": 720}
HZ_RU = {6: "6 ч", 24: "сутки", 48: "2 суток", 168: "неделя", 240: "10 суток", 720: "месяц"}
HZ_EN = {6: "6 h", 24: "day", 48: "2 days", 168: "week", 240: "10 days", 720: "month"}
NEWS_HZ = {"hours": 6, "days": 48, "weeks": 240}          # news.py ImpactLedger.HORIZON_MS
BAND_P = 0.6667                                            # «2 outcomes out of 3 inside the band»
PROB, SIGNAL = "prob", "signal"
SOURCES = {
    "ab": {"kind": PROB, "name_ru": "Прогноз A/B на сутки", "name_en": "A/B 24 h forecast",
           "what_ru": "вероятность, что цена сначала дойдёт до цели вверх, а не вниз (за 24 ч)",
           "what_en": "probability that price touches the upper target before the lower one (within 24 h)"},
    "band": {"kind": PROB, "name_ru": "Коридор «2 из 3»", "name_en": "2-of-3 price band",
             "what_ru": "цена в конце срока внутри коридора (заявлено: 2 случая из 3)",
             "what_en": "price at the end of the window inside the band (claimed: 2 cases out of 3)"},
    "base": {"kind": PROB, "name_ru": "Доля роста в режиме BTC", "name_en": "Up share in the BTC regime",
             "what_ru": "цена в конце срока выше, чем в момент прогноза",
             "what_en": "price at the end of the window above the price at publication"},
    "news": {"kind": PROB, "name_ru": "Оценка новостей (LLM)", "name_en": "News calls (LLM)",
             "what_ru": "цена идёт в сторону, которую назвал аналитик; вероятность = 0.5 + уверенность / 2",
             "what_en": "price moves the way the analyst said; probability = 0.5 + confidence / 2"},
    "feed": {"kind": SIGNAL, "name_ru": "Сигналы CRYPTO NOVA", "name_en": "CRYPTO NOVA signals",
             "what_ru": "сделки ленты сигналов: вход, стоп, цели, выход как у демо-стола (комиссии учтены)",
             "what_en": "signal feed trades: entry, stop, targets, exit as on the demo desk (fees included)"},
    "app": {"kind": SIGNAL, "name_ru": "Сигналы приложения 1.x", "name_en": "App 1.x signals",
            "what_ru": "сигналы внешнего бота Ai Golden Crypto в приложении 1.x; итог — наша проверка по свечам после комиссий",
            "what_en": "the external Ai Golden Crypto bot's signals in app 1.x; result = our candle check after costs"},
}


def _now() -> int:
    return int(time.time() * 1000)


def _r(x, d: int = 8):
    """a number rounded to d significant digits (keeps the ledger short); None for anything that is not a finite number"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return float(f"{v:.{d}g}")


def _coin(sym) -> str:
    s = str(sym or "").upper().strip()
    for suf in ("-USDT", "/USDT", "USDT"):
        if s.endswith(suf) and len(s) > len(suf):
            return s[: -len(suf)]
    return s


# ------------------------------------------------------------------------------------------------------------- hash chain
def _body(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(prev: str, body: str) -> str:
    return hashlib.sha256((prev + body).encode("utf-8")).hexdigest()


def make_line(prev: str, payload: dict) -> tuple[str, str]:
    """(line, hash): the payload as compact JSON with sorted keys, then ,"h":"<sha256(prev + that JSON)>" before the last brace"""
    body = _body({k: v for k, v in payload.items() if k != "h"})
    h = _hash(prev, body)
    return body[:-1] + ',"h":"' + h + '"}', h


def split_line(line: str) -> tuple[str, str]:
    """(body, h) of one ledger line — exactly the text that was hashed, recovered from the line itself"""
    if len(line) < 75 or not line.endswith('"}') or line[-72:-66] != ',"h":"':
        raise ValueError("no hash at the end of the line")
    h = line[-66:-2]
    if any(c not in "0123456789abcdef" for c in h):
        raise ValueError("the hash is not 64 hex characters")
    return line[:-72] + "}", h


def verify_text(text: str) -> dict:
    """{'ok', 'lines', 'head', 'rows', 'bad_line', 'error'} — the chain is checked line by line from the start"""
    rows: list[dict] = []
    prev = GENESIS
    parts = text.split("\n")
    if parts and parts[-1] == "":
        parts.pop()
    for i, raw in enumerate(parts, 1):
        try:
            body, h = split_line(raw)
            if _hash(prev, body) != h:
                raise ValueError("hash does not match: this line, or one before it, was changed, removed or reordered")
            d = json.loads(raw)
            if not isinstance(d, dict) or d.get("h") != h:
                raise ValueError("the line is not one JSON object with its hash last")
            if d.get("n") != i:
                raise ValueError(f"line number n={d.get('n')} where {i} was expected (a line was removed or inserted)")
        except ValueError as e:
            return {"ok": False, "lines": len(rows), "head": prev, "rows": rows, "bad_line": i, "error": str(e)[:200]}
        rows.append(d)
        prev = h
    return {"ok": True, "lines": len(rows), "head": prev, "rows": rows, "bad_line": None, "error": None}


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def read_chain(data: str, name: str) -> dict:
    """the whole chain «name» (ledger | outcomes): closed segments data/scorecard/<name>-<first n>.jsonl in order, then the active
    file data/scorecard_<name>.jsonl. Never raises: nothing yet is an empty, valid chain; an unreadable or broken one is not ok
    (and is never appended to)."""
    adir, active = os.path.join(data, SEG_DIR), os.path.join(data, f"scorecard_{name}.jsonl")
    info = {"ok": False, "lines": 0, "head": GENESIS, "rows": [], "bad_line": None, "error": None, "active_text": None,
            "active": active, "archive_dir": adir, "name": name, "segments": 0, "half_rotated": False}
    try:
        segs = sorted(f for f in os.listdir(adir) if f.startswith(name + "-") and f.endswith(".jsonl")) if os.path.isdir(adir) else []
        texts = [_read(os.path.join(adir, f)) for f in segs]
        act = _read(active) if os.path.exists(active) else ""
    except Exception as e:  # noqa: BLE001
        info["error"] = repr(e)[:200]
        return info
    if texts and act and act == texts[-1]:      # a rotation stopped between its two writes: the segment is the truth
        act, info["half_rotated"] = "", True
    if any(t and not t.endswith("\n") for t in texts):
        info["error"] = "a closed segment does not end with a newline"
        return info
    info.update(verify_text("".join(texts) + act))
    info.update({"active_text": act, "segments": len(segs)})
    return info


def _atomic_write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _rotate(info: dict) -> None:
    """a full active file becomes a closed segment (never written again), so a git commit never carries more than SEG_BYTES of
    it: first the segment is written, then the active file is emptied (a stop in between is recognised by read_chain)"""
    t = info.get("active_text") or ""
    if len(t.encode("utf-8")) < SEG_BYTES:
        return
    first = info["lines"] - t.count("\n") + 1
    _atomic_write(os.path.join(info["archive_dir"], f"{info['name']}-{first:07d}.jsonl"), t)
    _atomic_write(info["active"], "")
    info["active_text"] = ""
    info["segments"] = info.get("segments", 0) + 1


def append_chain(info: dict, payloads: list[dict], now: int) -> list[dict]:
    """append payloads (n, rec and h added) atomically — the active file is rewritten to .tmp and swapped in. Returns new rows."""
    if not info.get("ok") or info.get("active_text") is None:
        return []
    if not payloads:
        if info.get("half_rotated"):
            _atomic_write(info["active"], "")
            info["half_rotated"] = False
        return []
    prev, n = info["head"], info["lines"]
    lines, new = [], []
    for p in payloads:
        n += 1
        row = {k: v for k, v in p.items() if v is not None and k not in ("h", "n")}
        row["n"] = n
        row.setdefault("rec", now)
        line, prev = make_line(prev, row)
        lines.append(line)
        new.append({**row, "h": prev})
    text = info["active_text"]
    if text and not text.endswith("\n"):
        text += "\n"
    text += "\n".join(lines) + "\n"
    _atomic_write(info["active"], text)
    info.update({"active_text": text, "head": prev, "lines": n, "half_rotated": False})
    info["rows"] = info["rows"] + new
    _rotate(info)
    return new


# ------------------------------------------------------------------------------------------------------------- collectors
def _load(path: str) -> tuple[dict | None, str | None]:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return None, "missing"
    except Exception as e:  # noqa: BLE001
        return None, repr(e)[:120]
    return (d, None) if isinstance(d, dict) else (None, "not a JSON object")


def collect_ab(cal: dict) -> list[dict]:
    """web/calibration.json «open»: the newest unresolved A/B forecast of every coin (one per coin per 4 hours)"""
    out = []
    for coin, o in ((cal or {}).get("open") or {}).items():
        try:
            t, p, up, dn = int(o["time"]), float(o["p_up"]), float(o["up"]), float(o["dn"])
        except (KeyError, TypeError, ValueError):
            continue
        c = _coin(coin)
        if not c or not (0.0 <= p <= 1.0) or not (up > dn > 0):
            continue
        out.append({"id": f"ab:{c}-USDT-{t // BUCKET_MS}", "src": "ab", "sym": c, "t": t, "hz": 24, "p": _r(p, 4), "ev": "up_first",
                    "px": _r(o.get("price")), "up": _r(up), "dn": _r(dn)})
    return out


def collect_outlook(ol: dict, last: dict, now: int | None = None) -> list[dict]:
    """web/outlook.json: band and base rate per coin and horizon. A new one is recorded only when the previous window of the same
    coin and horizon has ended — overlapping windows would count the same price move many times. `last` is updated in place."""
    out = []
    ol = ol or {}
    try:
        asof = int(ol.get("asof") or 0)
    except (TypeError, ValueError):
        return out
    if not asof or (now is not None and now - asof > FRESH_S * 1000):
        return out
    blocks = [(ol.get("symbol") or "BTC", ol.get("price"), ol.get("horizons") or {})]
    for s, o in (ol.get("others") or {}).items():
        if isinstance(o, dict):
            blocks.append((s, o.get("price"), o.get("horizons") or {}))
    for sym, px, hs in blocks:
        sym, px = _coin(sym), _r(px)
        if not sym or not px or not isinstance(hs, dict):
            continue
        for name, h in hs.items():
            if not isinstance(h, dict):
                continue
            hz = OUTLOOK_HZ.get(name)
            if not hz:
                continue
            rng, br = h.get("range") or {}, h.get("base_rate_up") or {}
            lo, hi, pb = _r(rng.get("lo")), _r(rng.get("hi")), _r(br.get("p"), 4)
            for src, ok in (("band", bool(lo and hi and lo < hi)), ("base", pb is not None and 0 <= pb <= 1)):
                key = (src, sym, hz)
                if not ok or (last.get(key) is not None and asof < last[key] + hz * HOUR):
                    continue
                d = {"id": f"{src}:{sym}:{name}:{asof}", "src": src, "sym": sym, "t": asof, "hz": hz, "px": px}
                d.update({"p": BAND_P, "ev": "inside", "lo": lo, "hi": hi} if src == "band" else {"p": pb, "ev": "close_up"})
                out.append(d)
                last[key] = asof
    return out


def collect_news(impacts: list[dict]) -> list[dict]:
    """the LLM analyst's calls as published: direction, confidence ("conf", 0–1), horizon; MARKET means BTC (as news.py scores it);
    one call per story and coin. The confidence becomes a probability only when scored (prob())."""
    out, seen = [], set()
    for i in impacts or []:
        if not isinstance(i, dict):
            continue
        try:
            nid, t = str(i["news_id"]), int(i["created"])
        except (KeyError, TypeError, ValueError):
            continue
        a = str(i.get("asset") or "").upper()
        sym = "BTC" if a in ("", "MARKET") else _coin(a)
        key = f"news:{nid}:{sym}"
        if not sym or key in seen:
            continue
        seen.add(key)
        d = i.get("direction")
        conf = _r(i.get("confidence"), 3)
        directional = d in ("bull", "bear") and conf is not None and 0 <= conf <= 1
        out.append({"id": key, "src": "news", "sym": sym, "t": t, "hz": NEWS_HZ.get(i.get("horizon"), 48),
                    "conf": conf, "ev": "dir" if directional else "neutral", "dir": d,
                    "px": _r(i.get("price_at")), "cat": i.get("category"), "mag": i.get("magnitude"),
                    "tt": str(i.get("title") or i.get("title_ru") or "")[:60] or None})
    return out


_TP = re.compile(r"^\s*\d+\)\s*([0-9]+(?:\.[0-9]+)?(?:e-?\d+)?)\s*$")


def _tps(text: str) -> list[float]:
    out, on = [], False
    for ln in (text or "").splitlines():
        if ln.startswith("Take-Profit Targets"):
            on = True
            continue
        if on:
            m = _TP.match(ln)
            if not m:
                break
            out.append(_r(m.group(1)))
    return [x for x in out if x]


def collect_feed(feed: dict) -> list[dict]:
    """web/signals_feed.json items: every signal of the CRYPTO NOVA feed (pub = 1 when it went out as a new signal, 0 when it was
    «seeded» on the feed's first run or recorded while its source was out of the mix — both are scored, nothing is left out)"""
    out = []
    for it in (feed or {}).get("items") or []:
        try:
            sid, t, side = str(it["id"]), int(it["opened"]), str(it["side"]).upper()
            entry, stop = float(it["entry"]), float(it["stop"])
        except (KeyError, TypeError, ValueError):
            continue
        if side not in ("LONG", "SHORT") or entry <= 0 or stop <= 0:
            continue
        out.append({"id": f"feed:{sid}", "src": "feed", "sym": _coin(it.get("symbol")), "t": t, "side": side, "entry": _r(entry),
                    "stop": _r(stop), "tps": _tps(it.get("open_text") or "") or None, "rule": it.get("grade"), "desk": it.get("source"),
                    "pub": 1 if it.get("seeded") == 0 else 0})
    return out


def _app_row(r: dict) -> dict | None:
    try:
        sid, t, side = str(r["id"]), int(r["t"]), str(r["side"]).upper()
        lo, sl = float(r["entry_lo"]), float(r["sl"])
    except (KeyError, TypeError, ValueError):
        return None
    hi = _r(r.get("entry_hi"))
    tps = [_r(x.get("price")) for x in r.get("targets") or [] if isinstance(x, dict)]
    return {"id": f"app:{sid}", "src": "app", "sym": _coin(r.get("coin") or r.get("name")), "t": t, "side": side, "entry": _r(lo),
            "entry_hi": hi if hi and hi != _r(lo) else None, "stop": _r(sl), "tps": [x for x in tps if x] or None, "lev": _r(r.get("leverage"), 4)}


def collect_app(app: dict) -> list[dict]:
    rows = list((app or {}).get("active") or []) + list((app or {}).get("recent") or [])
    return [d for d in (_app_row(r) for r in rows if isinstance(r, dict)) if d]


# ------------------------------------------------------------------------------------------------------------- prices
class Prices:
    """the engine's 15-minute candles (cnq.store.db schema), read only"""

    def __init__(self, path: str | None):
        self.conn, self.error = None, None
        if not path or not os.path.exists(path):
            self.error = "candle store missing"
            return
        try:
            self.conn = sqlite3.connect(path, timeout=10)
            self.conn.execute("SELECT 1 FROM candles LIMIT 1")
        except Exception as e:  # noqa: BLE001
            self.conn, self.error = None, repr(e)[:120]

    def bars(self, sym: str, start: int, end: int) -> list[tuple]:
        """[(open_time, high, low, close)] of closed-or-not 15m candles with start ≤ open_time ≤ end"""
        if not self.conn or not sym:
            return []
        try:
            return self.conn.execute("SELECT open_time, high, low, close FROM candles WHERE symbol=? AND interval='15m' AND open_time>=? "
                                     "AND open_time<=? ORDER BY open_time", (f"{sym}-USDT", int(start), int(end))).fetchall()
        except sqlite3.Error:
            return []

    def healthy(self, now: int) -> bool:
        return bool(self.bars("BTC", now - 3 * HOUR, now))


def close_before(bars: list[tuple], t: int, max_age: int = HOUR) -> float | None:
    """the close of the last candle that had CLOSED by time t (the price known at t), if it is not older than max_age"""
    best = None
    for b in bars:
        if b[0] + M15 <= t:
            best = b
        else:
            break
    return best[3] if best and best[0] + M15 >= t - max_age else None


def resolve_prob(pred: dict, prices: Prices, now: int) -> dict | None:
    """{'y': 1/0, 't', 'px0', 'px1'} or None while it cannot be decided. Only candles that opened at or after the prediction's
    time decide it (the price before it is used only as the reference «price at publication» when the line has none)."""
    try:
        t, hz, sym, ev = int(pred["t"]), int(pred["hz"]) * HOUR, pred["sym"], pred.get("ev")
    except (KeyError, TypeError, ValueError):
        return None
    end = t + hz
    if now < end + M15 or ev not in ("up_first", "inside", "close_up", "dir", "neutral"):
        return None
    bars = prices.bars(sym, t - 2 * HOUR, end + M15)
    after = [b for b in bars if b[0] >= t and b[0] + M15 <= now]
    p0 = pred.get("px") or close_before(bars, t)
    if ev in ("dir", "neutral"):          # news: the engine's own price at publication when it has one
        p0 = close_before(bars, t) or pred.get("px")
    if not p0:
        return None
    if ev == "up_first":
        up, dn = pred.get("up"), pred.get("dn")
        if not up or not dn:
            return None
        seen = 0
        last = None
        for ot, hi, lo, cl in after:
            if ot > end:
                break
            seen += 1
            last = (ot, cl)
            hit_up, hit_dn = hi >= up, lo <= dn
            if hit_up and hit_dn:                       # both inside one candle: its close decides (as calibration.py)
                return {"y": 1 if cl >= p0 else 0, "t": ot, "px0": _r(p0), "px1": _r(cl)}
            if hit_up:
                return {"y": 1, "t": ot, "px0": _r(p0), "px1": _r(up)}
            if hit_dn:
                return {"y": 0, "t": ot, "px0": _r(p0), "px1": _r(dn)}
        # neither target in 24 h → the close decides, but only when the candles cover the whole window (no hole hides a touch)
        if last and last[0] >= end - M15 and seen >= 0.9 * (hz // M15):
            return {"y": 1 if last[1] >= p0 else 0, "t": last[0], "px0": _r(p0), "px1": _r(last[1])}
        return None
    fin = [b for b in after if b[0] + M15 <= end]
    if not fin or fin[-1][0] + M15 < end - HOUR:
        return None
    ot, _hi, _lo, c1 = fin[-1]
    if ev == "inside":
        y = 1 if pred["lo"] <= c1 <= pred["hi"] else 0
    elif ev == "close_up":
        y = 1 if c1 > p0 else 0
    elif ev == "neutral":                 # «no clear direction»: the move is kept for the record, it is not scored
        y = None
    else:
        ret = c1 / p0 - 1
        y = 1 if (ret > 0 and pred.get("dir") == "bull") or (ret < 0 and pred.get("dir") == "bear") else 0
    return {"y": y, "t": ot + M15, "px0": _r(p0), "px1": _r(c1)}


def always_long(prices: Prices, sym: str, t0: int, t1: int) -> float | None:
    """buying the same coin at the signal's time and selling at its exit, fees and slippage as on the desks (% of price)"""
    if not t1 or t1 <= t0:
        return None
    a = close_before(prices.bars(sym, t0 - 2 * HOUR, t0), t0)
    b = close_before(prices.bars(sym, t1 - 2 * HOUR, t1), t1)
    return round((b / a - 1) * 100 - FEE_RT_PCT, 3) if a and b else None


def _risk_pct(pred: dict) -> float | None:
    try:
        e, s = float(pred["entry"]), float(pred["stop"])
        return abs(e - s) / e * 100 if e > 0 and e != s else None
    except (KeyError, TypeError, ValueError):
        return None


def resolve_feed(pred: dict, item: dict | None, prices: Prices) -> dict | None:
    if not item or not item.get("closed") or item.get("pnl_pct") is None:
        return None
    try:
        pct, t1 = float(item["pnl_pct"]), int(item["closed"])
    except (TypeError, ValueError):
        return None
    risk = _risk_pct(pred)
    return {"t": t1, "pct": round(pct, 3), "r": round(pct / risk, 3) if risk else None, "exit": item.get("exit_kind"),
            "base": always_long(prices, pred.get("sym"), int(pred["t"]), t1), "by": "desk"}


def resolve_app(row: dict | None) -> dict | None:
    if not row or row.get("status") != "closed":
        return None
    pol = (row.get("policies") or {}).get("ladder_be") or {}
    if pol.get("pct") is None:
        return None
    return {"pct": _r(pol.get("pct"), 5), "r": _r(pol.get("r"), 5), "exit": row.get("state_ru") or row.get("state"), "by": "app_sim"}


def resolve_all(preds: list[dict], done: set, prices: Prices, feeds: dict, now: int) -> list[dict]:
    """outcome payloads for predictions that can be decided now (and «void» for those the candles never covered)"""
    items = {f"feed:{it.get('id')}": it for it in ((feeds.get("feed") or {}).get("items") or []) if isinstance(it, dict)}
    app_rows = {f"app:{r.get('id')}": r for r in (list((feeds.get("app") or {}).get("recent") or [])
                                                  + list((feeds.get("app") or {}).get("active") or [])) if isinstance(r, dict)}
    healthy = None
    out = []
    for p in preds:
        if p["id"] in done:
            continue
        kind = SOURCES.get(p.get("src"), {}).get("kind")
        res = None
        try:
            if kind == PROB:
                res = resolve_prob(p, prices, now)
                if res is not None:
                    res["by"] = "c15"
                elif now > int(p["t"]) + (int(p["hz"]) + VOID_AFTER_H) * HOUR:
                    if healthy is None:
                        healthy = prices.healthy(now)
                    if healthy:                     # the store is fine, only this coin / window has no candles
                        res = {"void": "no_candles", "by": "c15"}
            elif p.get("src") == "feed":
                res = resolve_feed(p, items.get(p["id"]), prices)
            elif p.get("src") == "app":
                res = resolve_app(app_rows.get(p["id"]))
        except Exception as e:  # noqa: BLE001 — one odd line never stops the others
            print("scorecard resolve:", p.get("id"), repr(e)[:120])
            res = None
        if res is not None:
            out.append({"id": p["id"], "ph": p["h"], **res})
    return out


# ------------------------------------------------------------------------------------------------------------- scoring
def prob(pred: dict) -> float | None:
    """the probability a line states for its event. News: the analyst names a direction and a confidence c (0 … 1); read as
    «the direction is right» with p = 0.5 + c/2 — c = 0 is a coin flip, c = 1 is certainty, and a weak bullish call is never
    counted as a bet on a fall. news.py's own table reads p = c; on the 168 calls checked by 20.10 that reading gave the calls a
    BETTER Brier (0.284 vs 0.301), so this choice does not flatter them."""
    if pred.get("ev") == "neutral":
        return None
    if pred.get("src") == "news":
        c = pred.get("conf")
        return None if c is None or pred.get("ev") != "dir" else round(0.5 + float(c) / 2, 4)
    p = pred.get("p")
    return None if p is None else float(p)


def prob_stats(pairs: list[tuple[float, int]]) -> dict:
    """Brier = mean (p − y)²; a coin (p = 0.5) scores 0.25; climatology = always saying the observed base rate b → b(1 − b)
    (in-sample, so it is a fair-to-generous reference); hit = (p ≥ 0.5) agrees with what happened"""
    n = len(pairs)
    if not n:
        return {"n": 0}
    brier = sum((p - y) ** 2 for p, y in pairs) / n
    base = sum(y for _, y in pairs) / n
    clim = base * (1 - base)
    hits = sum(1 for p, y in pairs if (p >= 0.5) == (y == 1))
    return {"n": n, "hits": hits, "misses": n - hits, "hit_rate": round(hits / n, 3), "brier": round(brier, 4), "brier_coin": 0.25,
            "brier_clim": round(clim, 4), "base_rate": round(base, 3), "skill_vs_coin": round(1 - brier / 0.25, 3),
            "skill_vs_clim": round(1 - brier / clim, 3) if clim > 0 else None, "p_mean": round(sum(p for p, _ in pairs) / n, 3)}


def calibration_table(pairs: list[tuple[float, int]]) -> list[dict]:
    """10 buckets of the stated probability: how often the event happened in each"""
    b: dict[int, list] = {}
    for p, y in pairs:
        b.setdefault(min(9, max(0, int(p * 10 + 1e-9))), []).append((p, y))
    rows = []
    for k in sorted(b):
        xs = b[k]
        pm, fr = sum(p for p, _ in xs) / len(xs), sum(y for _, y in xs) / len(xs)
        rows.append({"range": f"{k * 10}–{k * 10 + 10}%", "n": len(xs), "p_mean": round(pm, 3), "freq": round(fr, 3), "gap": round(fr - pm, 3)})
    return rows


def signal_stats(outs: list[dict]) -> dict:
    n = len(outs)
    if not n:
        return {"n": 0}
    pct = [float(o["pct"]) for o in outs]
    rs = [float(o["r"]) for o in outs if o.get("r") is not None]
    bl = [(float(o["pct"]), float(o["base"])) for o in outs if o.get("base") is not None]
    wins = sum(1 for x in pct if x > 0)
    d = {"n": n, "wins": wins, "losses": n - wins, "win_rate": round(wins / n, 3), "avg_pct": round(sum(pct) / n, 3),
         "sum_pct": round(sum(pct), 2), "avg_r": round(sum(rs) / len(rs), 3) if rs else None, "best_pct": round(max(pct), 2),
         "worst_pct": round(min(pct), 2), "base_n": len(bl)}
    if bl:
        d["base_avg_pct"] = round(sum(b for _, b in bl) / len(bl), 3)
        d["avg_pct_same"] = round(sum(a for a, _ in bl) / len(bl), 3)
        d["edge_pct"] = round(d["avg_pct_same"] - d["base_avg_pct"], 3)
    return d


def _pct(x) -> str:
    return f"{round(100 * x)}%"


def verdict(src: str, st: dict) -> tuple[str, str, str]:
    """(code, ru, en) — one honest line"""
    n = st.get("n") or 0
    if SOURCES[src]["kind"] == SIGNAL:
        if n < MIN_SIG_N:
            return "few", f"мало сделок: закрыто {n}, судить можно от {MIN_SIG_N}", f"too few trades: {n} closed, judge from {MIN_SIG_N}"
        avg = st["avg_pct"]
        if avg <= 0:
            return "loss", f"в минусе после комиссий: {avg:+.2f}% на сделку", f"losing after fees: {avg:+.2f}% per trade"
        if st.get("edge_pct") is not None and st["edge_pct"] <= 0:
            return "no_edge", (f"в плюсе ({avg:+.2f}% на сделку), но не лучше простой покупки той же монеты на то же время"),\
                (f"positive ({avg:+.2f}% per trade) but no better than simply buying the same coin for the same time")
        return "edge", f"в плюсе после комиссий: {avg:+.2f}% на сделку", f"profitable after fees: {avg:+.2f}% per trade"
    if n < MIN_PROB_N:
        return "few", f"мало данных: проверено {n}, выводы — от {MIN_PROB_N}", f"too little data: {n} checked, conclusions from {MIN_PROB_N}"
    if src == "band":
        cov = st["base_rate"]
        if cov < BAND_P - 0.1:
            return "narrow", f"коридор узкий: внутри {_pct(cov)} вместо заявленных 67%", f"band too narrow: {_pct(cov)} inside instead of the claimed 67%"
        if cov > BAND_P + 0.1:
            return "wide", f"коридор шире нужного: внутри {_pct(cov)}, заявлено 67%", f"band wider than claimed: {_pct(cov)} inside, claimed 67%"
        return "as_claimed", f"коридор держит заявленное: внутри {_pct(cov)} (заявлено 67%)", f"band holds its claim: {_pct(cov)} inside (claimed 67%)"
    b = st["brier"]
    if b > 0.25:
        return "worse_than_coin", f"пока хуже подбрасывания монеты (Brier {b:.4f} > 0.25)", f"so far worse than a coin flip (Brier {b:.4f} > 0.25)"
    if b >= st["brier_clim"]:
        return "no_skill", (f"не хуже монеты (Brier {b:.4f}), но не лучше простой частоты {_pct(st['base_rate'])} (Brier {st['brier_clim']:.4f})"),\
            (f"no worse than a coin (Brier {b:.4f}) but no better than the plain base rate {_pct(st['base_rate'])} (Brier {st['brier_clim']:.4f})")
    return "skill", f"лучше монеты и простой частоты (Brier {b:.4f})", f"better than a coin and the base rate (Brier {b:.4f})"


def _fmt_px(p) -> str:
    try:
        p = float(p)
    except (TypeError, ValueError):
        return "—"
    if p >= 1000:
        return f"{p:,.0f}".replace(",", " ")
    if p >= 1:
        return f"{p:.4g}" if p < 10 else f"{p:.2f}"
    return f"{p:.4g}"


def _texts(p: dict, o: dict) -> tuple[str, str, bool | None]:
    """(ru, en, ok) — one line for the «latest checked» list; ok None = could not be checked"""
    sym, src = p.get("sym", "?"), p.get("src")
    if o.get("void"):
        return f"{sym}: не удалось проверить — нет свечей", f"{sym}: could not be checked — no candles", None
    hz = p.get("hz")
    if src == "ab":
        ok = (p["p"] >= 0.5) == (o["y"] == 1)
        return (f"{sym}: A/B «сначала вверх» {_pct(p['p'])} → сначала {'вверх' if o['y'] else 'вниз'}",
                f"{sym}: A/B “up first” {_pct(p['p'])} → {'up' if o['y'] else 'down'} first", ok)
    if src == "band":
        return (f"{sym} {HZ_RU.get(hz, hz)}: коридор {_fmt_px(p['lo'])}–{_fmt_px(p['hi'])} → {_fmt_px(o.get('px1'))}, {'внутри' if o['y'] else 'вне коридора'}",
                f"{sym} {HZ_EN.get(hz, hz)}: band {_fmt_px(p['lo'])}–{_fmt_px(p['hi'])} → {_fmt_px(o.get('px1'))}, {'inside' if o['y'] else 'outside'}",
                o["y"] == 1)
    if src == "base":
        ok = (p["p"] >= 0.5) == (o["y"] == 1)
        return (f"{sym} {HZ_RU.get(hz, hz)}: рост {_pct(p['p'])} → {'вырос' if o['y'] else 'не вырос'}",
                f"{sym} {HZ_EN.get(hz, hz)}: up {_pct(p['p'])} → {'rose' if o['y'] else 'did not rise'}", ok)
    if src == "news":
        try:
            ret = (float(o["px1"]) / float(o["px0"]) - 1) * 100
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            ret = 0.0
        tt = f" «{p['tt']}»" if p.get("tt") else ""
        if prob(p) is None or o.get("y") is None:
            return (f"{sym} {HZ_RU.get(hz, hz)}:{tt} без направления → {ret:+.1f}% (не оценивается)",
                    f"{sym} {HZ_EN.get(hz, hz)}:{tt} no direction → {ret:+.1f}% (not scored)", None)
        bull = p.get("dir") == "bull"
        return (f"{sym} {HZ_RU.get(hz, hz)}:{tt} {'рост' if bull else 'падение'}, уверенность {_pct(p['conf'])} → {ret:+.1f}%",
                f"{sym} {HZ_EN.get(hz, hz)}:{tt} {'up' if bull else 'down'}, confidence {_pct(p['conf'])} → {ret:+.1f}%", o["y"] == 1)
    r = f" ({o['r']:+.2f}R)" if o.get("r") is not None else ""
    side_ru = "лонг" if p.get("side") == "LONG" else "шорт"
    side_en = "long" if p.get("side") == "LONG" else "short"
    return (f"{sym} {side_ru} от {_fmt_px(p.get('entry'))}, стоп {_fmt_px(p.get('stop'))} → {o['pct']:+.2f}%{r}",
            f"{sym} {side_en} from {_fmt_px(p.get('entry'))}, stop {_fmt_px(p.get('stop'))} → {o['pct']:+.2f}%{r}", o["pct"] > 0)


def build(preds: list[dict], outs: list[dict], led: dict, oc: dict, now: int, errors: list[str], refs: dict | None = None) -> dict:
    out_by = {o["id"]: o for o in outs}
    refs = refs or {}
    sources, worse = [], []
    for key, meta in SOURCES.items():
        ps = [p for p in preds if p.get("src") == key]
        res = [(p, out_by[p["id"]]) for p in ps if p["id"] in out_by]
        void = [x for x in res if x[1].get("void")]
        ok = [x for x in res if not x[1].get("void")]
        s = {"key": key, "kind": meta["kind"], "name_ru": meta["name_ru"], "name_en": meta["name_en"], "what_ru": meta["what_ru"],
             "what_en": meta["what_en"], "recorded": len(ps), "checked": len(ok), "void": len(void), "pending": len(ps) - len(res),
             "backfilled": sum(1 for p in ps if p.get("bf")), "late": sum(1 for p in ps if p.get("late") and not p.get("bf")),
             "since": min((p["t"] for p in ps), default=None)}
        lags = sorted((p["rec"] - p["t"]) / 60_000 for p in ps if not p.get("bf") and p.get("rec") is not None)
        s["lag_median_min"] = round(lags[len(lags) // 2], 1) if lags else None
        if meta["kind"] == PROB:
            sc = [(p, o) for p, o in ok if prob(p) is not None and o.get("y") is not None]
            s["not_directional"] = sum(1 for p, _ in ok if p.get("ev") == "neutral")
            pairs = [(prob(p), int(o["y"])) for p, o in sc]
            s["stats"] = prob_stats(pairs)
            s["live"] = prob_stats([(prob(p), int(o["y"])) for p, o in sc if not p.get("bf")])
            s["calibration"] = calibration_table(pairs)
            hzs = sorted({int(p["hz"]) for p, _ in sc})
            s["horizons"] = [{"hz": h, "label_ru": HZ_RU.get(h, f"{h} ч"), "label_en": HZ_EN.get(h, f"{h} h"),
                              **prob_stats([(prob(p), int(o["y"])) for p, o in sc if int(p["hz"]) == h])} for h in hzs]
        else:
            s["stats"] = signal_stats([o for _, o in ok])
            s["live"] = signal_stats([o for p, o in ok if not p.get("bf")])
            s["published"] = sum(1 for p in ps if p.get("pub") == 1) if key == "feed" else None
        code, s["verdict_ru"], s["verdict_en"] = verdict(key, s["stats"])
        s["verdict"] = code
        if key in refs:
            s["ref"] = refs[key]
        if code in ("worse_than_coin", "loss"):
            worse.append(key)
        sources.append(s)
    pairs_all = [(p, out_by[p["id"]]) for p in preds if p["id"] in out_by]
    recent = sorted(pairs_all, key=lambda x: (x[1].get("t") or x[1].get("rec") or 0, x[1].get("n", 0)), reverse=True)[:RECENT_N]
    rec_rows = []
    for p, o in recent:
        ru, en, good = _texts(p, o)
        rec_rows.append({"src": p["src"], "sym": p.get("sym"), "t": p.get("t"), "t_out": o.get("t") or o.get("rec"), "ok": good,
                         "bf": 1 if p.get("bf") else 0, "ru": ru, "en": en, "id": p["id"]})
    # the biggest misses, shown as prominently as the hits: the most confident wrong probabilities and the worst signals
    wrong = [(p, o) for p, o in pairs_all if not o.get("void") and SOURCES.get(p["src"], {}).get("kind") == PROB and prob(p) is not None
             and o.get("y") is not None and (prob(p) >= 0.5) != (int(o["y"]) == 1)]
    wrong.sort(key=lambda x: -abs(prob(x[0]) - int(x[1]["y"])))
    bad_sig = sorted([(p, o) for p, o in pairs_all if not o.get("void") and o.get("pct") is not None], key=lambda x: float(x[1]["pct"]))[:5]
    misses = []
    for p, o in wrong[:10] + [x for x in bad_sig if x[1]["pct"] < 0]:
        ru, en, _ = _texts(p, o)
        misses.append({"src": p["src"], "sym": p.get("sym"), "t": p.get("t"), "ru": ru, "en": en, "id": p["id"]})
    checked = sum(s["checked"] for s in sources)
    return {
        "asof": now, "version": 1,
        "headline": {"recorded": len(preds), "checked": checked, "pending": sum(s["pending"] for s in sources),
                     "void": sum(s["void"] for s in sources), "backfilled": sum(s["backfilled"] for s in sources),
                     "live_recorded": len(preds) - sum(s["backfilled"] for s in sources), "sources": len(sources),
                     "sources_losing": worse, "since": min((p["t"] for p in preds), default=None)},
        "sources": sources, "recent": rec_rows, "misses": misses,
        "chain": {
            "ledger": {"file": f"data/{LEDGER}", "segments_dir": f"data/{SEG_DIR}/ledger-*.jsonl", "segments": led.get("segments", 0),
                       "lines": led.get("lines", 0), "head": led.get("head"), "ok": bool(led.get("ok")),
                       "bad_line": led.get("bad_line"), "error": led.get("error")},
            "outcomes": {"file": f"data/{OUTCOMES}", "segments_dir": f"data/{SEG_DIR}/outcomes-*.jsonl", "segments": oc.get("segments", 0),
                         "lines": oc.get("lines", 0), "head": oc.get("head"), "ok": bool(oc.get("ok")),
                         "bad_line": oc.get("bad_line"), "error": oc.get("error")},
            "verify_cmd": "git clone https://github.com/dmytrosukhyna/cryptonova-cockpit && cd cryptonova-cockpit && python3 scripts/verify_scorecard.py",
            "how_ru": ("Каждая строка журнала содержит хеш SHA-256 от хеша предыдущей строки и своего текста. Если задним числом изменить, "
                       "удалить или вставить хоть одну строку, все хеши после неё перестанут сходиться. Проверка — одна команда, без наших "
                       "серверов: скрипт пересчитывает всю цепочку и пишет OK или BROKEN. Журнал лежит в публичном git-репозитории, поэтому "
                       "история коммитов показывает, когда каждая строка появилась."),
            "how_en": ("Every ledger line carries a SHA-256 hash of the previous line's hash plus its own text. Changing, deleting or inserting "
                       "any line afterwards breaks every hash after it. Checking takes one command and none of our servers: the script "
                       "recomputes the whole chain and prints OK or BROKEN. The ledger lives in a public git repository, so the commit "
                       "history also shows when each line appeared.")},
        "method_ru": ("Прогноз записывается в журнал, когда он появляется в нашей публичной ленте (раз в минуту на сервере). Проверяем по "
                      "15-минутным свечам сервера и только по свечам, открывшимся после момента прогноза. Brier — средний квадрат ошибки "
                      "вероятности: 0 — идеально, 0.25 — как монетка, больше 0.25 — хуже монетки. «Простая частота» — если бы мы всегда "
                      "называли среднюю долю исходов, Brier = доля × (1 − доля). Сигналы считаем после комиссий и сравниваем с простой "
                      "покупкой той же монеты на то же время. Строки с пометкой «из старых журналов» перенесены при запуске табло."),
        "method_en": ("A prediction is written to the ledger when it appears in our public feed (once a minute on the server). It is checked "
                      "on the server's 15-minute candles, using only candles that opened after the prediction. Brier is the mean squared "
                      "error of the probability: 0 is perfect, 0.25 is a coin flip, above 0.25 is worse than a coin. The plain base rate is "
                      "always stating the average share of outcomes: Brier = share × (1 − share). Signals are counted after fees and compared "
                      "with simply buying the same coin for the same time. Lines marked “from older ledgers” were carried over when the "
                      "scorecard started."),
        "not_covered_ru": ["сделки остальных демо-столов (lab, lsn, turbo, exp, brk, imp) — их счета в paper.json, в табло пока не входят",
                           "посты и графики канала с «A x %» — это тот же прогноз A/B, он уже в табло",
                           "прогнозы чужих каналов (channel_watch) — не наши, считаются отдельно",
                           "пока сервер выключен, табло не пишется: прогнозы A/B того времени попадут сюда, только если ещё видны в ленте"],
        "not_covered_en": ["trades of the other demo desks (lab, lsn, turbo, exp, brk, imp) — their accounts are in paper.json, not here yet",
                           "channel posts and charts with “A x %” — that is the same A/B forecast, already here",
                           "other channels' calls (channel_watch) — not ours, scored separately",
                           "while the server is down nothing is recorded: A/B forecasts of that time get here only if still visible in the feed"],
        "errors": errors[:20],
    }


def _refs(feeds: dict) -> dict:
    """the existing ledgers' own numbers, shown next to ours (they are not used for scoring)"""
    out = {}
    st = (feeds.get("cal") or {}).get("stats") or {}
    if st.get("n"):
        out["ab"] = {"file": "web/calibration.json", "n": st.get("n"), "hit_rate": st.get("hit_rate"), "brier": st.get("brier")}
    pol = (((feeds.get("app") or {}).get("stats") or {}).get("policies") or {}).get("ladder_be") or {}
    if pol.get("n"):
        out["app"] = {"file": "web/app_signals.json", "n": pol.get("n"), "win_rate": pol.get("winrate"), "avg_pct": pol.get("avg_pct")}
    fs = (feeds.get("feed") or {}).get("stats") or {}
    if fs.get("signals"):
        out["feed"] = {"file": "web/signals_feed.json", "n": fs.get("closed"), "avg_pct": fs.get("avg_pct"), "note": "только опубликованные"}
    return out


def _last_outlook(rows: list[dict]) -> dict:
    last: dict = {}
    for r in rows:
        if r.get("src") in ("band", "base"):
            k = (r["src"], r["sym"], int(r["hz"]))
            last[k] = max(last.get(k, 0), int(r["t"]))
    return last


def _write_feed(path: str, doc: dict, now: int) -> bool:
    """rewrite only when something changed or the file is older than REWRITE_S — the engine commits every changed file"""
    old, _ = _load(path)
    if old and now - int(old.get("asof") or 0) < REWRITE_S * 1000:
        skip = ("asof", "this_run")
        a = {k: v for k, v in old.items() if k not in skip}
        b = json.loads(json.dumps({k: v for k, v in doc.items() if k not in skip}, ensure_ascii=False))
        if a == b:
            return False
    _atomic_write(path, json.dumps(doc, ensure_ascii=False, separators=(",", ":")))
    return True


def run(out: str = "web", data: str = "data", candles_db: str | None = "data/cnq.sqlite", now: int | None = None) -> dict:
    """the engine step: record what is published now → resolve what is due → web/scorecard.json. Never raises."""
    now = now or _now()
    errors: list[str] = []
    try:
        led = read_chain(data, "ledger")
        oc = read_chain(data, "outcomes")
        for name, c in (("ledger", led), ("outcomes", oc)):
            if not c["ok"]:
                errors.append(f"{name} chain BROKEN at line {c['bad_line']}: {c['error']} — nothing is appended until it is fixed")
        feeds = {}
        for k, f in (("cal", "calibration.json"), ("outlook", "outlook.json"), ("decision", "decision.json"),
                     ("feed", "signals_feed.json"), ("app", "app_signals.json")):
            d, err = _load(os.path.join(out, f))
            feeds[k] = d or {}
            if err:
                errors.append(f"{f}: {err}")
        known = {r.get("id") for r in led["rows"]}
        cands: list[dict] = []
        for name, fn in (("ab", lambda: collect_ab(feeds["cal"])),
                         ("outlook", lambda: collect_outlook(feeds["outlook"], _last_outlook(led["rows"]), now)),
                         ("news", lambda: collect_news((feeds["decision"].get("news") or {}).get("impacts") or [])),
                         ("feed", lambda: collect_feed(feeds["feed"])), ("app", lambda: collect_app(feeds["app"]))):
            try:
                cands += fn()
            except Exception as e:  # noqa: BLE001
                errors.append(f"collect {name}: {repr(e)[:120]}")
        new, seen = [], set()
        for c in sorted(cands, key=lambda d: (d["t"], d["id"])):
            if c["id"] in known or c["id"] in seen:
                continue
            if c["t"] > now + 5 * 60_000:                    # a clock from the future is not a publication
                errors.append(f"{c['id']}: time in the future, skipped")
                continue
            seen.add(c["id"])
            if now - c["t"] > LATE_S * 1000:
                c = {**c, "late": 1}
            new.append(c)
        added = append_chain(led, new, now)
        prices = Prices(candles_db)
        if prices.error:
            errors.append(f"candles: {prices.error}")
        resolved = []
        if oc["ok"] and led["ok"]:
            due = resolve_all(led["rows"], {o.get("id") for o in oc["rows"]}, prices, feeds, now)
            resolved = append_chain(oc, due, now)
        doc = build(led["rows"], oc["rows"], led, oc, now, errors, _refs(feeds))
        doc["this_run"] = {"recorded": len(added), "resolved": len(resolved)}
        os.makedirs(out, exist_ok=True)
        doc["written"] = _write_feed(os.path.join(out, FEED), doc, now)
        return doc
    except Exception as e:  # noqa: BLE001 — the engine loop must never stop because of the scorecard
        import traceback
        traceback.print_exc()
        return {"asof": now, "error": repr(e)[:300], "errors": errors}


# ------------------------------------------------------------------------------------------------------------- backfill
def _git_versions(git_dir: str, path: str) -> list[tuple[int, str]]:
    """[(commit time s, file text)] of every committed version of a file, oldest first"""
    try:
        log = subprocess.run(["git", "-C", git_dir, "log", "--format=%H %ct", "--", path], capture_output=True, text=True, timeout=120).stdout
    except Exception:  # noqa: BLE001
        return []
    out = []
    for ln in reversed(log.strip().splitlines()):
        h, ts = ln.split()
        try:
            txt = subprocess.run(["git", "-C", git_dir, "show", f"{h}:{path}"], capture_output=True, text=True, timeout=60).stdout
        except Exception:  # noqa: BLE001
            continue
        out.append((int(ts), txt))
    return out


def backfill(out: str = "web", data: str = "data", agents_db: str = "data/agents.sqlite", git_dir: str | None = ".",
             now: int | None = None) -> dict:
    """one-time start of the ledger from what already exists, every line marked "bf": 1:
      A/B — agents.sqlite `forecasts` + every committed version of web/calibration.json (forecasts made after the database copy);
      band / base — every committed version of web/outlook.json, oldest first, non-overlapping windows;
      news — agents.sqlite `news_impact` + web/decision.json; feed — web/signals_feed.json;
      app — agents.sqlite `app_signals` (+ their outcomes from app_signals.py's candle check, also marked bf).
    Idempotent: ids already in the ledger are skipped."""
    now = now or _now()
    led = read_chain(data, "ledger")
    oc = read_chain(data, "outcomes")
    if not led["ok"] or not oc["ok"]:
        return {"error": "chain broken — fix it first", "ledger": led.get("error"), "outcomes": oc.get("error")}
    cands: list[dict] = []
    app_out: dict[str, dict] = {}
    conn = None
    if agents_db and os.path.exists(agents_db):
        conn = sqlite3.connect(f"file:{agents_db}?mode=ro", uri=True)
    if conn:
        try:
            for fid, t, sym, p, px, up, dn, hz in conn.execute("SELECT id, time, symbol, p_up, price, up_target, dn_target, horizon FROM forecasts ORDER BY time"):
                if p is None or up is None or dn is None:
                    continue
                cands.append({"id": f"ab:{fid}", "src": "ab", "sym": _coin(sym), "t": int(t), "hz": int((hz or 24 * HOUR) // HOUR), "p": _r(p, 4),
                              "ev": "up_first", "px": _r(px), "up": _r(up), "dn": _r(dn)})
        except sqlite3.Error as e:
            print("backfill forecasts:", e)
    vers = {f: _git_versions(git_dir, f"web/{f}") if git_dir else [] for f in ("calibration.json", "outlook.json")}
    for _ts, txt in vers["calibration.json"]:
        try:
            cands += collect_ab(json.loads(txt))
        except ValueError:
            continue
    cur = {k: _load(os.path.join(out, f))[0] or {} for k, f in (("cal", "calibration.json"), ("outlook", "outlook.json"),
                                                                ("decision", "decision.json"), ("feed", "signals_feed.json"))}
    cands += collect_ab(cur["cal"])
    last = _last_outlook(led["rows"])
    for _ts, txt in vers["outlook.json"] + [(0, json.dumps(cur["outlook"]))]:
        try:
            ol = json.loads(txt)
        except ValueError:
            continue
        cands += collect_outlook(ol, last, None)
    imp = []
    if conn:
        try:
            for nid, asset, cat, d, hz, mag, conf, created, px, title in conn.execute(
                    "SELECT news_id, asset, category, direction, horizon, magnitude, confidence, created, price_at, title_ru FROM news_impact ORDER BY created"):
                imp.append({"news_id": nid, "asset": asset, "category": cat, "direction": d, "horizon": hz, "magnitude": mag,
                            "confidence": conf, "created": created, "price_at": px, "title": title})
        except sqlite3.Error as e:
            print("backfill news:", e)
    imp += list((cur["decision"].get("news") or {}).get("impacts") or [])
    cands += collect_news(imp)
    cands += collect_feed(cur["feed"])
    if conn:
        try:
            for sid, sym, side, lo, hi, sl, lev, t, tg, sim in conn.execute(
                    "SELECT signal_id, symbol, side, entry_lo, entry_hi, sl, leverage, t_signal, targets, sim FROM app_signals ORDER BY t_signal"):
                try:
                    tgs = json.loads(tg or "[]")
                except ValueError:
                    tgs = []
                d = _app_row({"id": sid, "coin": sym, "side": side, "t": t, "entry_lo": lo, "entry_hi": hi, "sl": sl, "leverage": lev, "targets": tgs})
                if not d:
                    continue
                cands.append(d)
                try:
                    s = json.loads(sim or "{}")
                except ValueError:
                    s = {}
                pol = (s.get("policies") or {}).get("ladder_be") or {}
                if s.get("state") == "closed" and pol.get("closed") and pol.get("net") is not None:
                    app_out[d["id"]] = {"t": pol.get("t") or s.get("resolved_at"), "pct": _r(pol["net"], 5), "r": _r(pol.get("r"), 5),
                                        "exit": s.get("ended"), "by": "app_sim", "bf": 1}
        except sqlite3.Error as e:
            print("backfill app:", e)
    known = {r.get("id") for r in led["rows"]}
    new, seen = [], set()
    for c in sorted(cands, key=lambda d: (d["t"], d["id"])):
        if c["id"] in known or c["id"] in seen or c["t"] > now:
            continue
        seen.add(c["id"])
        new.append({**c, "bf": 1})
    added = append_chain(led, new, now)
    done = {o.get("id") for o in oc["rows"]}
    outs = [{"id": r["id"], "ph": r["h"], **app_out[r["id"]]} for r in led["rows"] if r["id"] in app_out and r["id"] not in done]
    outs.sort(key=lambda o: (o.get("t") or 0, o["id"]))
    resolved = append_chain(oc, outs, now)
    by = {}
    for r in added:
        by[r["src"]] = by.get(r["src"], 0) + 1
    return {"added": len(added), "by_source": by, "outcomes": len(resolved), "lines": led["lines"], "head": led["head"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CRYPTO NOVA public prediction scorecard")
    ap.add_argument("--backfill", action="store_true", help="start the ledger from the existing ledgers (one time; marked bf)")
    ap.add_argument("--out", default="web")
    ap.add_argument("--data", default="data")
    ap.add_argument("--agents-db", default="data/agents.sqlite")
    ap.add_argument("--candles", default=os.environ.get("CNQ_DB", "data/cnq.sqlite"))
    ap.add_argument("--git", default=".")
    a = ap.parse_args(argv)
    if a.backfill:
        print(json.dumps(backfill(a.out, a.data, a.agents_db, a.git), ensure_ascii=False))
        return 0
    d = run(a.out, a.data, a.candles)
    print(json.dumps({"this_run": d.get("this_run"), "headline": d.get("headline"), "errors": d.get("errors")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
