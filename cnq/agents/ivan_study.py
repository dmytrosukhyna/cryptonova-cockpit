"""«Иван · изучение стратегий» (17.11.2026, owner 17:27): Ivan trades «от уровней» on several Bybit accounts («Иван Тест 2 / 3 / 4»).
To distil his logic later we keep, RAW, the market context of every entry / add (averaging) / reduce / exit of every connected
account, and per-strategy numbers that do not hide a held loss: the open (unrealised) result, the worst open loss, the maximum
drawdown of «closed + open», the number of averaging steps — not only the win rate of closed trades.

Pure functions (no network, no files): cnq/api/ivan_tests.py reads the positions (read-only keys) and the public candles and stores
what these return in <run>/data/ivan_tests (owner-only; nothing here is published). Nothing here can place an order. Stdlib only."""
from __future__ import annotations

import math

H1, DAY = 3_600_000, 86_400_000
EPS = 0.005                 # a size change smaller than 0.5 % of the position is not an add / a reduce
SWING_K, SWING_N = 3, 5     # a swing = a candle whose high (low) is the extreme of K candles on each side; the last N are kept
MAX_EVENTS = 400            # the latest events kept in the account's study state (the raw file keeps all)


def _f(x, d=0.0) -> float:
    try:
        v = float(x)
        return v if v == v else d
    except (TypeError, ValueError):
        return d


def pct(a, b):
    """a relative to b, % (None when b is missing)"""
    a, b = _f(a), _f(b)
    return round((a / b - 1) * 100, 3) if a and b else None


def session(t: int) -> str:
    """the trading session of a UTC time: asia 00–07, europe 07–13, us 13–21, late 21–24"""
    h = (int(t) // H1) % 24
    return "asia" if h < 7 else "europe" if h < 13 else "us" if h < 21 else "late"


def sma(closes: list, n: int):
    return sum(closes[-n:]) / n if len(closes) >= n else None


def atr(cs: list, n: int = 14):
    """average true range of the last n candles ([t, o, h, l, c], oldest first)"""
    if len(cs) < n + 1:
        return None
    tr = [max(cs[i][2] - cs[i][3], abs(cs[i][2] - cs[i - 1][4]), abs(cs[i][3] - cs[i - 1][4])) for i in range(len(cs) - n, len(cs))]
    return sum(tr) / n


def swings(cs: list, k: int = SWING_K, n: int = SWING_N) -> dict:
    """the last n swing highs and lows (fractals of k candles on each side), newest first: {"highs": [[t, px]], "lows": [[t, px]]}"""
    hi, lo = [], []
    for i in range(k, len(cs) - k):
        w = cs[i - k:i + k + 1]
        if cs[i][2] == max(x[2] for x in w):
            hi.append([cs[i][0], cs[i][2]])
        if cs[i][3] == min(x[3] for x in w):
            lo.append([cs[i][0], cs[i][3]])
    return {"highs": hi[-n:][::-1], "lows": lo[-n:][::-1]}


def round_levels(px: float) -> dict | None:
    """the nearest round numbers around a price: the step is the price's order of magnitude (10 000 for 97 300, 0.1 for 0.47) and half
    of it; distances in % of the price (below ≤ 0 ≤ above)"""
    px = _f(px)
    if px <= 0:
        return None
    big = 10 ** math.floor(math.log10(px))
    out = {}
    for name, step in (("big", big), ("half", big / 2), ("tenth", big / 10)):
        lo, hi = math.floor(px / step) * step, math.ceil(px / step) * step
        near = lo if px - lo <= hi - px else hi
        out[name] = {"step": step, "below": lo, "above": hi, "below_pct": pct(lo, px), "above_pct": pct(hi, px),
                     "nearest": near, "nearest_pct": pct(near, px)}
    return out


def trend(cs: list, px: float) -> dict:
    """the price against SMA 20 / 50 / 200 of the candles' closes (% above +, below −) and the SMA 20's slope over 5 candles"""
    c = [x[4] for x in cs]
    out = {f"sma{n}_pct": pct(px, sma(c, n)) for n in (20, 50, 200)}
    s_now, s_then = sma(c, 20), sma(c[:-5], 20) if len(c) >= 25 else None
    out["sma20_slope_pct"] = pct(s_now, s_then)
    return out


def ranges(h1: list, px: float, now: int) -> dict:
    """distance (% of the price) to the high / low of the current UTC day, the previous day and the last 7 days (H1 candles)"""
    day0 = now - now % DAY
    def hl(a, b):
        w = [x for x in h1 if a <= x[0] < b]
        return (max(x[2] for x in w), min(x[3] for x in w)) if w else (None, None)
    out = {}
    for name, (a, b) in (("day", (day0, now + 1)), ("prev_day", (day0 - DAY, day0)), ("week", (now - 7 * DAY, now + 1))):
        h, lo = hl(a, b)
        out[name] = {"high": h, "low": lo, "to_high_pct": pct(h, px), "to_low_pct": pct(lo, px),
                     "pos": round((px - lo) / (h - lo), 3) if h and lo and h > lo else None}      # 0 = at the low, 1 = at the high
    return out


def btc_regime(h1: list, d1: list) -> dict | None:
    """BTC at the moment: against SMA 200 D1 and SMA 50 / 200 H1, 24 h and 7 d change, a plain label"""
    if not h1:
        return None
    px = h1[-1][4]
    c1 = [x[4] for x in h1]
    out = {"px": px, "d1_sma200_pct": pct(px, sma([x[4] for x in d1], 200)), "h1_sma50_pct": pct(px, sma(c1, 50)),
           "h1_sma200_pct": pct(px, sma(c1, 200)), "chg_24h_pct": pct(px, c1[-25]) if len(c1) >= 25 else None,
           "chg_7d_pct": pct(px, c1[-169]) if len(c1) >= 169 else None}
    a, b = out["d1_sma200_pct"], out["h1_sma200_pct"]
    out["label"] = None if a is None or b is None else "bull" if a > 0 and b > 0 else "bear" if a < 0 and b < 0 else "mixed"
    return out


def context(px: float, now: int, h1: list, d1: list, btc_h1: list | None = None, btc_d1: list | None = None) -> dict:
    """the raw market context of one event at price px: levels (day / week range, prior swings, round numbers), volatility, trend on
    H1 and D1, session, BTC regime. Candles: [t, o, h, l, c] oldest first, only candles that opened before `now`."""
    h1 = [x for x in h1 or [] if x[0] <= now]
    d1 = [x for x in d1 or [] if x[0] <= now]
    a1, ad = atr(h1), atr(d1)
    sw = swings(h1)
    for k in ("highs", "lows"):
        sw[k] = [{"t": t, "px": p, "dist_pct": pct(p, px)} for t, p in sw[k]]
    swd = swings(d1)
    for k in ("highs", "lows"):
        swd[k] = [{"t": t, "px": p, "dist_pct": pct(p, px)} for t, p in swd[k]]
    return {"px": px, "session": session(now), "utc_hour": (now // H1) % 24, "weekday": (now // DAY + 3) % 7,       # 0 = Monday
            "range": ranges(h1, px, now), "swings_h1": sw, "swings_d1": swd, "round": round_levels(px),
            "atr_h1_pct": round(a1 / px * 100, 3) if a1 and px else None, "atr_d1_pct": round(ad / px * 100, 3) if ad and px else None,
            "trend_h1": trend(h1, px), "trend_d1": trend(d1, px), "btc": btc_regime(btc_h1 or [], btc_d1 or []),
            "candles": {"h1": len(h1), "d1": len(d1)}}


def diff(open_: dict, positions: list, now: int) -> list[dict]:
    """what changed since the previous look. open_ = {"SYM|SIDE": ladder} is updated in place; positions = [{symbol, side, size, entry,
    mark, upnl, leverage, stop, tp, value}]. Events: entry (a new position), add (the size grew — an averaging step when the new
    fill is against the position), reduce, exit. Each carries the ladder: step number, size ratio to the previous size, the price
    step from the previous fill, minutes since the previous add."""
    ev = []
    cur = {f"{p['symbol']}|{p['side']}": p for p in positions if _f(p.get("size")) > 0}
    for k, p in cur.items():
        size, entry, px = _f(p["size"]), _f(p.get("entry")), _f(p.get("mark")) or _f(p.get("entry"))
        s = 1 if p["side"] == "LONG" else -1
        base = {"t": now, "symbol": p["symbol"], "side": p["side"], "px": px, "size": size, "avg_entry": entry, "upnl": _f(p.get("upnl")),
                "lev": _f(p.get("leverage")) or None, "stop": p.get("stop"), "tp": p.get("tp"), "value": _f(p.get("value")) or None}
        L = open_.get(k)
        if L is None:
            open_[k] = {"t0": now, "first_px": entry or px, "size": size, "avg": entry, "steps": 1, "adds": 0, "avg_downs": 0,
                        "last_t": now, "last_px": entry or px, "max_size": size, "worst_upnl": min(0.0, base["upnl"])}
            ev.append({**base, "kind": "entry", "step": 1})
            continue
        L["worst_upnl"] = min(_f(L.get("worst_upnl")), base["upnl"])
        prev = _f(L.get("size"))
        if prev and size > prev * (1 + EPS):
            # the fill price of the added part from the change of the average entry (exact when nothing was closed in between)
            add_sz = size - prev
            fill = (entry * size - _f(L.get("avg")) * prev) / add_sz if entry and L.get("avg") else px
            if not fill or fill <= 0 or abs(fill / px - 1) > 0.2:
                fill = px
            against = s * (fill - _f(L.get("last_px"))) < 0            # bought lower (long) / sold higher (short) than the last fill
            L["steps"] += 1
            L["adds"] += 1
            L["avg_downs"] += 1 if against else 0
            ev.append({**base, "kind": "add", "step": L["steps"], "averaging": against, "add_size": add_sz, "fill_px": fill,
                       "size_ratio": round(add_sz / prev, 4), "step_from_last_pct": pct(fill, L.get("last_px")),
                       "from_first_pct": pct(fill, L.get("first_px")), "min_since_last": round((now - int(L.get("last_t") or now)) / 60_000, 1),
                       "avg_before": L.get("avg")})
            L.update(last_t=now, last_px=fill, max_size=max(_f(L.get("max_size")), size))
        elif prev and size < prev * (1 - EPS):
            ev.append({**base, "kind": "reduce", "step": L["steps"], "cut_ratio": round(1 - size / prev, 4),
                       "from_avg_pct": pct(px, entry), "min_held": round((now - int(L["t0"])) / 60_000, 1)})
        L.update(size=size, avg=entry)
    for k in [k for k in open_ if k not in cur]:
        L = open_.pop(k)
        sym, side = k.split("|", 1)
        ev.append({"t": now, "symbol": sym, "side": side, "kind": "exit", "px": None, "size": 0.0, "avg_entry": L.get("avg"),
                   "steps": L.get("steps"), "adds": L.get("adds"), "avg_downs": L.get("avg_downs"), "max_size": L.get("max_size"),
                   "worst_upnl": L.get("worst_upnl"), "min_held": round((now - int(L["t0"])) / 60_000, 1)})
    return ev


def track(st: dict, out: dict, now: int) -> dict:
    """per-strategy numbers that do not hide a held loss. st (kept between looks) follows the curve «closed result + open result»;
    out = the account's reading (ivan_watch.collect). → the card's block."""
    closed = _f((out.get("stats") or {}).get("pnl_sum"))
    upnl = _f(out.get("upnl"))
    tot = closed + upnl
    st.setdefault("since", now)
    st["peak"] = max(_f(st.get("peak"), tot), tot)
    st["max_dd_usd"] = round(max(_f(st.get("max_dd_usd")), st["peak"] - tot), 2)
    st["worst_open_usd"] = round(min(_f(st.get("worst_open_usd")), upnl), 2)
    eq = _f(out.get("equity"))
    if eq:                                                           # drawdown of the account's equity itself, % of its peak
        st["eq_peak"] = max(_f(st.get("eq_peak")), eq)
        st["max_dd_pct"] = round(max(_f(st.get("max_dd_pct")), (st["eq_peak"] - eq) / st["eq_peak"] * 100 if st["eq_peak"] else 0.0), 2)
    lad = st.get("open") or {}
    c = st.setdefault("counts", {})
    return {"since": st["since"], "closed_pnl_usd": round(closed, 2), "open_pnl_usd": round(upnl, 2), "total_pnl_usd": round(tot, 2),
            "worst_open_usd": st["worst_open_usd"], "max_dd_usd": st["max_dd_usd"], "max_dd_pct": st.get("max_dd_pct"),
            "max_dd_account_pct": round(_f(out.get("max_dd")) * 100, 2),
            "open_n": len(out.get("positions") or []), "open_averaged_n": sum(1 for L in lad.values() if _f(L.get("adds")) > 0),
            "open_max_steps": max((int(_f(L.get("steps"))) for L in lad.values()), default=0),
            "entries": int(_f(c.get("entry"))), "adds": int(_f(c.get("add"))), "averagings": int(_f(c.get("averaging"))),
            "reduces": int(_f(c.get("reduce"))), "exits": int(_f(c.get("exit"))),
            "winrate": (out.get("stats") or {}).get("winrate"), "closed_n": (out.get("stats") or {}).get("n"),
            "note_ru": "Винрейт считает только закрытые сделки. «Открытый результат» и «просадка» показывают то, что винрейт не видит: "
                       "убыточные позиции, которые не закрыты, а усреднены."}


def count(st: dict, events: list) -> None:
    c = st.setdefault("counts", {})
    for e in events:
        c[e["kind"]] = int(_f(c.get(e["kind"]))) + 1
        if e.get("averaging"):
            c["averaging"] = int(_f(c.get("averaging"))) + 1
    st["events"] = ((st.get("events") or []) + [{k: v for k, v in e.items() if k != "ctx"} for e in events])[-MAX_EVENTS:]
