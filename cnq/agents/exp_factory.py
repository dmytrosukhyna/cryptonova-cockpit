"""Experiment FACTORY on paper (owner 22.11.2026 16:48: «без ограничений» — but on paper; real-money rules only with his explicit
yes). One generic runner: a strategy spec = signal source × filters × entry × stop × size × take-profit × hold × side (follow /
inverse). Every spec runs forward from its birth on the same price marks (one OKX SWAP tickers call per pass, every 5 minutes on the
server), owner-only (<run>/data/exp_factory). No spec has a copy-signal id: the real account can never open its trades.

Sources (events):
  whales   every copy the real whale desk opens (web/whales_live.json desk) — exit with the whale too unless the spec says otherwise
  own      the own-signals desk (web/paper.json profiles.own) opens;  aggr  the autopilot «aggr» profile opens
  fear     BTC when the Fear & Greed index (web/markets.json fng) is ≤ 10
  dow      BTC at Monday 00:00 UTC, Friday 00:00 UTC, the US open 13:30 UTC (calendar effects)
  brk      BTC 4h close beyond its 20-bar high / low (follow = breakout, inverse = fade)
  funding  BTC / ETH / SOL funding ≥ +0.05 % → short, ≤ −0.03 % → long (crowd contrarian; inverse = with the crowd)
  lsr      BTC long/short account ratio (OKX rubik) ≥ 2.5 → short, ≤ 0.8 → long
Filters: reg4h20 (no alt short above a rising BTC 4h EMA20, no alt long below a falling one), reg1d200 (long only above the BTC
daily EMA200, short only below), reg1h50 (same on the BTC 1h EMA50), trend (the coin's own 1h close on the trade's side of its
EMA50), major (BTC/ETH/SOL), btc (BTC only), agree2 (≥ 2 desk whales on the same coin and side), smart (smart-money veto).
Entry: now | conf1 (only after a +1 % move our way within 12 h) | retest (only when the price comes back within 0.3 % of the source's
entry within 24 h). Stop: % from our entry or «atr2» (2 × ATR(14, 1h)). Take-profit: k × R (R = stop distance). Hold: hours.
Size: fixed $1 000 | vol ($1 000 × 2 % / ATR%) | conv ($1 000 × agreeing whales). Costs 0.11 % a round trip; funding not modelled
forward (marked «без фандинга» — the backtests include it).

Honesty (owner point 3): every spec is logged with its birth time BEFORE any result; the score is forward only. A spec is a
CANDIDATE only with ≥ 30 forward trades, t ≥ 3 (multiple-testing guard for 50+ specs), positive mean in both halves of its trades,
and a PF and drawdown better than the real source. Evolution once a UTC day: specs with ≥ 40 trades and PF < 0.7 (or mean < 0
with t ≤ −2) are killed; the 3 best alive specs with ≥ 15 trades spawn one mutation each (stop / take-profit / hold / filter);
every generation is logged. Stdlib only."""
from __future__ import annotations

import json
import math
import time
import urllib.request

H, DAY = 3_600_000, 86_400_000
COST, BASE_USD = 0.11, 1000.0
MAJORS = {"BTC", "ETH", "SOL"}
OKX = "https://www.okx.com/api/v5"
MAX_LIVE = 140


def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "cnq-factory"}), timeout=15) as r:
        return json.loads(r.read())


# ── specs ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def sid(s: dict) -> str:
    f = "+".join(sorted(s.get("filt") or [])) or "-"
    return (f"{s['src']}|{s.get('side', 'follow')}|{f}|{s.get('entry', 'now')}|s{s.get('stop')}|tp{s.get('tp') or '-'}|"
            f"h{s.get('hold') or '-'}|{s.get('size', 'fixed')}" + (f"|ds{s['daystop']}" if s.get("daystop") else "")
            + ("|tpown" if s.get("tp_own") else "")
            + ("|sown" if s.get("stop_own") else "")
            + (f"|{s['sub']}" if s.get("sub") else ""))


def seed_specs() -> list:
    """generation 0 — the families of the owner's 22.11 16:48 list (≈ 70 specs)"""
    S = []
    add = lambda **k: S.append({**{"side": "follow", "filt": [], "entry": "now", "stop": 16, "tp": None, "hold": None, "size": "fixed"}, **k})  # noqa: E731
    # whales: baselines, trend / regime filters, confirmation, stops grid, TP ladders, hold limits, size, inverse
    add(src="whales", family="baseline")
    for f in (["major"], ["btc"], ["agree2"], ["reg4h20"], ["reg1d200"], ["reg1h50"], ["trend"], ["smart"], ["agree2", "major"],
              ["reg4h20", "trend"], ["agree2", "reg4h20"]):
        add(src="whales", family="filter", filt=f)
    for e in ("conf1", "retest"):
        add(src="whales", family="confirm", entry=e)
        add(src="whales", family="confirm", entry=e, filt=["agree2"])
    for st in (4, 8, 12, "atr2"):
        add(src="whales", family="stop", stop=st)
    for tp in (1, 2, 3):
        add(src="whales", family="tp", tp=tp, stop=8)
    for h in (12, 24, 72):
        add(src="whales", family="hold", hold=h)
    add(src="whales", family="size", size="vol")
    add(src="whales", family="size", size="conv")
    add(src="whales", family="contrarian", side="inverse")
    add(src="whales", family="contrarian", side="inverse", stop=8, tp=1)
    # own signals and the autopilot «aggr»: as is, fixes, inverse
    for src in ("own", "aggr"):
        add(src=src, family="baseline")
        add(src=src, family="stop", stop=8)
        add(src=src, family="filter", filt=["reg1d200"])
        add(src=src, family="filter", filt=["trend"])
        add(src=src, family="contrarian", side="inverse")
        add(src=src, family="contrarian", side="inverse", stop=8)
    # calendar effects, panic, breakouts, crowd positioning — each with its mirror
    for sub, hold in (("mon", 24), ("fri", 24), ("usopen", 6)):
        for side in ("follow", "inverse"):
            add(src="dow", family="calendar", sub=sub, side=side, hold=hold, stop=4)
    for hold in (168, 720):
        for side in ("follow", "inverse"):
            add(src="fear", family="panic", side=side, hold=hold, stop=12)
    for side in ("follow", "inverse"):
        for st, tp in ((4, None), (3, 2)):
            add(src="brk", family="breakout", side=side, stop=st, tp=tp, hold=24)
    for side in ("follow", "inverse"):
        add(src="funding", family="crowd", side=side, stop=4, hold=24)
        add(src="lsr", family="crowd", side=side, stop=4, hold=24)
    out, seen = [], set()
    for s in S:
        s["id"] = sid(s)
        if s["id"] not in seen:
            seen.add(s["id"])
            out.append(s)
    return out


# ── market context ───────────────────────────────────────────────────────────────────────────────────────────────────────
def ema(xs: list, n: int) -> list:
    out, e = [], None
    for x in xs:
        e = x if e is None else e + (x - e) * 2 / (n + 1)
        out.append(e)
    return out


def candles(inst: str, bar: str, n: int, get=None) -> list:
    d = (get or _get)(f"{OKX}/market/candles?instId={inst}&bar={bar}&limit={n}").get("data") or []
    return sorted((int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4])) for k in d if len(k) < 9 or str(k[8]) == "1")


def atr_pct(c1: list, n: int = 14) -> float | None:
    if len(c1) < n + 1:
        return None
    trs = [max(c1[i][2], c1[i - 1][4]) - min(c1[i][3], c1[i - 1][4]) for i in range(1, len(c1))]
    return sum(trs[-n:]) / n / c1[-1][4] * 100


def trend_state(cs: list, n: int):
    """(close, ema, ema a bar before) of the last completed bar"""
    if len(cs) < n + 2:
        return None
    e = ema([c[4] for c in cs], n)
    return cs[-1][4], e[-1], e[-2]


def passes(spec: dict, coin: str, side: str, ctx: dict, ev: dict) -> bool:
    for f in spec.get("filt") or []:
        if f == "major" and coin not in MAJORS:
            return False
        if f == "btc" and coin != "BTC":
            return False
        if f == "agree2" and ev.get("agree", 1) < 2:
            return False
        if f == "liquid" and (ctx.get("vol24") or {}).get(coin, 0) < 5e6:       # 22.11 22:36: ≥ $5M a day on OKX
            return False
        if f == "nohedge" and str(ev.get("whale") or "").lower() in (ctx.get("hedge") or ()):   # 26.11: «хедж» traders out
            return False
        if f == "ivan" and not str(ev.get("whale") or "").startswith("ivan"):   # 22.11 17:55: Ivan's copies only
            return False
        if f == "cap2side" and ev.get("side_open", 0) >= 2:         # 22.11 post-mortem: no 3rd copy on the same side (baskets)
            return False
        if f == "sess_eu_late":                                      # 22.11 post-mortem: US / Asia entries lost (exploratory)
            h = time.gmtime(int(ev.get("t") or 0) / 1000).tm_hour
            if h < 8 or 13 <= h < 21:
                return False
        if f == "smart":
            fl = (ctx.get("smart") or {}).get(coin)
            if (side == "SHORT" and fl == "acc") or (side == "LONG" and fl == "dist"):
                return False
        if f in ("reg4h20", "reg1h50") and coin != "BTC":
            r = ctx.get(f)
            if r:
                c, e, pe = r
                if (side == "SHORT" and c > e and e > pe) or (side == "LONG" and c < e and e < pe):
                    return False
        if f == "reg1d200":
            r = ctx.get(f)
            if r and ((side == "LONG" and r[0] < r[1]) or (side == "SHORT" and r[0] > r[1])):
                return False
        if f == "trend":
            r = ev.get("trend")
            if r and ((side == "LONG" and r[0] < r[1]) or (side == "SHORT" and r[0] > r[1])):
                return False
    return True


# ── events from the sources ──────────────────────────────────────────────────────────────────────────────────────────────
def desk_events(desk: dict, src: str) -> list:
    opens = [o for o in desk.get("open") or [] if isinstance(o, dict) and (o.get("coin") or o.get("symbol")) and (o.get("entry") or src in ("tgc", "tgf"))]
    out = []
    for o in opens:
        coin = str(o.get("coin") or o.get("symbol")).upper().replace("-USDT-SWAP", "").replace("USDT", "")
        side = str(o.get("side") or "").upper()
        agree = sum(1 for x in opens if str(x.get("coin") or x.get("symbol")).upper().replace("USDT", "") == coin
                    and str(x.get("side")).upper() == side)
        side_open = sum(1 for x in opens if x is not o and str(x.get("side")).upper() == side
                        and int(x.get("opened_t") or x.get("opened") or 0) <= int(o.get("opened_t") or o.get("opened") or 0))
        t = int(o.get("opened_t") or o.get("opened") or 0)
        out.append({"key": f"{src}|{o.get('whale') or o.get('id')}|{coin}|{t}", "src": src, "coin": coin, "side": side, "t": t,
                    "entry": float(o.get("entry") or 0) or None, "agree": agree, "side_open": side_open, "whale": str(o.get("whale") or ""),
                    "targets_x": o.get("targets_x") or [], "targets_px": o.get("targets_px") or [],
                    **({"sub": str(o.get("whale"))} if src in ("hlw", "wsw", "tgf") else {}),
                    **({"stop_px": o.get("stop_px"), "zone": o.get("zone")} if src == "tgf" else {})})
    return out


def closed_keys(desk: dict, src: str) -> dict:
    out = {}
    for c in desk.get("closed") or []:
        if not isinstance(c, dict):
            continue
        coin = str(c.get("coin") or c.get("symbol") or "").upper().replace("-USDT-SWAP", "").replace("USDT", "")
        t = int(c.get("opened_t") or c.get("opened") or 0)
        out[f"{src}|{c.get('whale') or c.get('id')}|{coin}|{t}"] = float(c.get("exit") or 0) or None
    return out


def market_events(ctx: dict, now: int, st: dict) -> list:
    """BTC / majors events the factory itself generates (each fires once per its own window)"""
    ev = []
    g = time.gmtime(now / 1000)
    mins = g.tm_hour * 60 + g.tm_min
    day = now // DAY
    if g.tm_wday == 0 and mins < 10:
        ev.append({"key": f"dow|mon|{day}", "src": "dow", "sub": "mon", "coin": "BTC", "side": "LONG", "t": now})
    if g.tm_wday == 4 and mins < 10:
        ev.append({"key": f"dow|fri|{day}", "src": "dow", "sub": "fri", "coin": "BTC", "side": "LONG", "t": now})
    if g.tm_wday < 5 and 13 * 60 + 30 <= mins < 13 * 60 + 40:
        ev.append({"key": f"dow|usopen|{day}", "src": "dow", "sub": "usopen", "coin": "BTC", "side": "LONG", "t": now})
    fg = ctx.get("fng")
    if fg is not None and fg <= 10:
        ev.append({"key": f"fear|{day // 7}", "src": "fear", "coin": "BTC", "side": "LONG", "t": now})
    c4 = ctx.get("c4") or []
    if len(c4) >= 22:
        hi, lo, last = max(c[2] for c in c4[-21:-1]), min(c[3] for c in c4[-21:-1]), c4[-1]
        if last[4] > hi:
            ev.append({"key": f"brk|up|{last[0]}", "src": "brk", "coin": "BTC", "side": "LONG", "t": now})
        elif last[4] < lo:
            ev.append({"key": f"brk|dn|{last[0]}", "src": "brk", "coin": "BTC", "side": "SHORT", "t": now})
    for coin, fr in (ctx.get("funding") or {}).items():
        if fr >= 0.0005:
            ev.append({"key": f"funding|{coin}|{now // (8 * H)}", "src": "funding", "coin": coin, "side": "SHORT", "t": now})
        elif fr <= -0.0003:
            ev.append({"key": f"funding|{coin}|{now // (8 * H)}", "src": "funding", "coin": coin, "side": "LONG", "t": now})
    r = ctx.get("lsr")
    if r is not None and r >= 2.5:
        ev.append({"key": f"lsr|{now // (12 * H)}", "src": "lsr", "coin": "BTC", "side": "SHORT", "t": now})
    elif r is not None and r <= 0.8:
        ev.append({"key": f"lsr|{now // (12 * H)}", "src": "lsr", "coin": "BTC", "side": "LONG", "t": now})
    return ev


# ── the runner ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def _ret(side, a, b):
    return (1 if side == "LONG" else -1) * (b / a - 1) * 100


def _open(st, spec, ev, side, px, now, atrp):
    stop = 2 * atrp if spec["stop"] == "atr2" and atrp else (16.0 if spec["stop"] == "atr2" else float(spec["stop"]))
    if spec.get("stop_own") and ev.get("stop_px"):                  # 26.11: the channel's own stop (its distance from our fill)
        d_ = (float(ev["stop_px"]) / px - 1) * 100 * (-1 if side == "LONG" else 1)
        if 0.2 <= d_ <= 30:
            stop = d_
    size = BASE_USD
    if spec.get("size") == "vol" and atrp:
        size = BASE_USD * min(3.0, max(0.25, 2.0 / atrp))
    elif spec.get("size") == "conv":
        size = BASE_USD * min(3, max(1, ev.get("agree", 1)))
    elif spec.get("size") == "macrobias":                         # 23.11: before / after CPI-PCE-FOMC with a BTC 4h flip down
        mb = (st.get("macro_bias") or {})
        k = 1.0
        if mb.get("active"):
            k = 1.5 if ev.get("side") == "SHORT" or side == "SHORT" else 0.5
        size = BASE_USD * k
    elif spec.get("size") == "ladder":                             # 22.11 18:07: the real risk ladder 1 → 1.5 → 2 % on this spec's own closes
        from cnq.execution import risk_ladder as RL
        S = st.setdefault("ladders", {}).setdefault(spec["id"], {})
        RL.decide(S, [(c["closed_t"], c["usd"]) for c in st["closed"] if c["spec"] == spec["id"]], 0.0, float(st.get("fut_budget") or 1000.0), now)
        size = RL.level(S) * float(st.get("fut_budget") or 1000.0) / (stop / 100)
    elif str(spec.get("size", "")).startswith("risk"):            # 22.11 18:05: the real sizing — risk % of the futures budget / stop
        size = float(spec["size"][4:]) / 100 * float(st.get("fut_budget") or 1000.0) / (stop / 100)
    tpx = None
    if spec.get("tp_own") and (ev.get("targets_x") or ev.get("targets_px")):        # 22.11 22:36: his own first target
        if side == "SHORT":                                         # 26.11: futures channels call shorts too
            c_ = [x for x in ev.get("targets_px") or [] if x < px * 0.998]
            tpx = max(c_) if c_ else None
        else:
            c_ = [px * x for x in ev.get("targets_x") or []] + [x for x in ev.get("targets_px") or []
                                                                 if x > px * (1.002 if spec.get("src") == "tgf" else 1.02)]
            tpx = min(c_) if c_ else None
    st["open"].append({"spec": spec["id"], "key": ev["key"], "coin": ev["coin"], "side": side, "entry": px, "t": now, "best": px,
                       "stop_pct": round(stop, 3), "usd": round(size, 2), "src_entry": ev.get("entry"), "tp_px": tpx})


def step(st: dict, now: int, ctx: dict, sources: dict, marks: dict, trend_of=None, atr_of=None) -> dict:
    """one pass: new events → pending / open positions per spec; exits on the marks. sources = {src: desk dict}"""
    t0 = time.time()
    st.setdefault("born", now)
    if not st.get("specs"):
        st["specs"] = [{**s, "gen": 0, "born": now, "alive": True} for s in seed_specs()]
        st.setdefault("generations", []).append({"t": now, "gen": 0, "spawned": [s["id"] for s in st["specs"]], "killed": []})
    st.setdefault("open", [])
    st.setdefault("pending", [])
    st.setdefault("closed", [])
    seen = st.setdefault("seen", {})
    specs = [s for s in st["specs"] if s.get("alive")]
    evs = []
    for src, desk in (sources or {}).items():
        evs += desk_events(desk or {}, src)
    evs += market_events(ctx, now, st)
    exits_src = {}
    for src, desk in (sources or {}).items():
        exits_src.update(closed_keys(desk or {}, src))
    live_keys = {e["key"] for e in evs if e["src"] in (sources or {})}
    new = 0
    for ev in evs:
        if ev["key"] in seen:
            continue
        seen[ev["key"]] = now
        if ev["src"] in (sources or {}) and ev["t"] < int(st["born"]) - 10 * 60_000:
            continue                                                   # opened before the factory existed
        px = marks.get(ev["coin"])
        if not px:
            continue
        if any("trend" in (s.get("filt") or []) for s in specs if s["src"] == ev["src"]) and trend_of:
            try:
                ev["trend"] = trend_of(ev["coin"])
            except Exception:  # noqa: BLE001
                ev["trend"] = None
        atrp = None
        for s in specs:
            if s["src"] != ev["src"] or (s.get("sub") and s["sub"] != ev.get("sub")):
                continue
            side = ev["side"] if s.get("side", "follow") == "follow" else ("SHORT" if ev["side"] == "LONG" else "LONG")
            if not passes(s, ev["coin"], side, ctx, ev):
                continue
            if any(p["spec"] == s["id"] and p["coin"] == ev["coin"] for p in st["open"]):
                continue
            if s.get("daystop"):                                   # the real day stop: today's closed $ of this spec ≤ −x % of budget
                d0 = now - now % DAY
                today = sum(c["usd"] for c in st["closed"] if c["spec"] == s["id"] and c["closed_t"] >= d0)
                if s["daystop"] == "ladder":
                    from cnq.execution import risk_ladder as RL
                    lim = RL.day_loss(RL.level((st.get("ladders") or {}).get(s["id"])))
                else:
                    lim = float(s["daystop"])
                if today <= -lim * float(st.get("fut_budget") or 1000.0):
                    continue
            if atrp is None and (s["stop"] == "atr2" or s.get("size") == "vol") and atr_of:
                try:
                    atrp = atr_of(ev["coin"])
                except Exception:  # noqa: BLE001
                    atrp = None
            if s.get("entry", "now") == "now":
                _open(st, s, ev, side, px, now, atrp)
                new += 1
            else:
                st["pending"].append({"spec": s["id"], "ev": ev, "side": side, "px0": px, "t": now, "atrp": atrp})
    # pending confirmations
    keep = []
    for q in st["pending"]:
        s = next((x for x in specs if x["id"] == q["spec"]), None)
        px = marks.get(q["ev"]["coin"])
        if not s or not px:
            continue
        sgn = 1 if q["side"] == "LONG" else -1
        if s["entry"] == "conf1":
            if sgn * (px / q["px0"] - 1) * 100 >= 1.0:
                _open(st, s, q["ev"], q["side"], px, now, q.get("atrp")); new += 1; continue
            if now - q["t"] < 12 * H:
                keep.append(q)
        elif s["entry"] == "zone":                                # 26.11: a futures call's entry zone, ≤ 48 h (no zone = market)
            z = q["ev"].get("zone")
            if not z or (z[0] * 0.998 <= px <= z[1] * 1.002) or (sgn > 0 and px < z[0] and px >= z[0] * 0.99) \
                    or (sgn < 0 and px > z[1] and px <= z[1] * 1.01):
                _open(st, s, q["ev"], q["side"], px, now, q.get("atrp")); new += 1; continue
            if now - q["t"] < 48 * H:
                keep.append(q)
        elif s["entry"] == "retest":
            ref = q["ev"].get("entry") or q["px0"]
            if abs(px / ref - 1) <= 0.003:
                _open(st, s, q["ev"], q["side"], px, now, q.get("atrp")); new += 1; continue
            if now - q["t"] < 24 * H:
                keep.append(q)
    st["pending"] = keep
    # exits
    spec_by = {s["id"]: s for s in st["specs"]}
    still, closed_n = [], 0
    for p in st["open"]:
        s = spec_by.get(p["spec"]) or {}
        px = marks.get(p["coin"])
        if not px:
            still.append(p); continue
        sgn = 1 if p["side"] == "LONG" else -1
        p["best"] = max(p["best"], px) if sgn > 0 else min(p["best"], px)
        r = _ret(p["side"], p["entry"], px)
        why = None
        if r <= -p["stop_pct"]:
            why, r = "стоп", -p["stop_pct"]
        elif p.get("tp_px") and ((sgn > 0 and px >= p["tp_px"]) or (sgn < 0 and px <= p["tp_px"])):
            why, r = "его цель", _ret(p["side"], p["entry"], p["tp_px"])
        elif s.get("tp") and r >= s["tp"] * p["stop_pct"]:
            why, r = f"цель {s['tp']}R", s["tp"] * p["stop_pct"]
        elif s.get("hold") and now - p["t"] >= s["hold"] * H:
            why = f"{s['hold']} ч"
        elif p["key"] in exits_src and s.get("src") in (sources or {}):
            why = "источник вышел"
        elif s.get("src") in (sources or {}) and p["key"] not in live_keys and p["key"] not in exits_src and now - p["t"] > 30 * 60_000:
            why = "источник исчез"
        elif not s.get("hold") and s.get("src") not in (sources or {}) and now - p["t"] >= 72 * H:
            why = "72 ч"
        if why:
            net = r - COST
            st["closed"].append({"spec": p["spec"], "coin": p["coin"], "side": p["side"], "t": p["t"], "closed_t": now, "pct": round(net, 3),
                                 "usd": round(net / 100 * p["usd"], 2), "why": why})
            closed_n += 1
        else:
            still.append(p)
    st["open"] = still
    st["closed"] = st["closed"][-20000:]
    if len(seen) > 20000:
        for k in sorted(seen, key=seen.get)[:-10000]:
            seen.pop(k, None)
    evolve(st, now)
    st["cycle_ms"] = round((time.time() - t0) * 1000, 1)
    st["last_t"] = now
    return {"events": len(evs), "opened": new, "closed": closed_n, "live_specs": sum(1 for s in st["specs"] if s.get("alive")),
            "cycle_ms": st["cycle_ms"]}


# ── scores and evolution ─────────────────────────────────────────────────────────────────────────────────────────────────
def score(xs: list) -> dict:
    if not xs:
        return {"n": 0}
    pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    cum = peak = dd = 0.0
    for x in xs:
        cum += x
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    m = sum(xs) / len(xs)
    sd = (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5 if len(xs) > 1 else 0.0
    h = len(xs) // 2
    return {"n": len(xs), "win": round(sum(x > 0 for x in xs) / len(xs) * 100, 1), "pf": round(pos / neg, 2) if neg else None,
            "mean": round(m, 3), "sum": round(sum(xs), 2), "max_dd": round(dd, 2), "t": round(m / (sd / math.sqrt(len(xs))), 2) if sd else None,
            "h1": round(sum(xs[:h]) / h, 3) if h else None, "h2": round(sum(xs[h:]) / (len(xs) - h), 3) if len(xs) - h else None}


def per_spec(st: dict) -> dict:
    by: dict = {}
    for c in sorted(st.get("closed") or [], key=lambda c: c["closed_t"]):
        by.setdefault(c["spec"], []).append(c["pct"])
    return {s["id"]: score(by.get(s["id"], [])) for s in st.get("specs") or []}


def candidate(sc: dict, src: dict | None) -> bool:
    src = src or {}
    return bool(sc.get("n", 0) >= 30 and (sc.get("t") or 0) >= 3 and (sc.get("h1") or 0) > 0 and (sc.get("h2") or 0) > 0
                and (sc.get("pf") or 0) > float(src.get("pf") or 0) and sc.get("max_dd", 1e9) < float(src.get("max_dd") or 1e9))


def mutate(s: dict, k: int) -> dict:
    m = {key: (list(v) if isinstance(v, list) else v) for key, v in s.items() if key not in ("id", "gen", "born", "alive", "parent", "died")}
    opt = k % 4
    if opt == 0:
        cur = s["stop"] if isinstance(s["stop"], (int, float)) else 16
        m["stop"] = max(2, cur - 4) if k % 8 < 4 else cur + 4
    elif opt == 1:
        m["tp"] = None if s.get("tp") else 2
    elif opt == 2:
        m["hold"] = {None: 48, 12: 24, 24: 72, 48: 24, 72: None}.get(s.get("hold"), 24)
    else:
        extra = ["reg4h20", "trend", "agree2", "major", "reg1d200"][k % 5]
        m["filt"] = sorted(set(m.get("filt") or []) ^ {extra})
    m["family"] = s.get("family", "?") + "·мут"
    return m


def evolve(st: dict, now: int) -> None:
    day = now // DAY
    if st.get("evolved_day") == day:
        return
    st["evolved_day"] = day
    sc = per_spec(st)
    killed, spawned = [], []
    for s in st["specs"]:
        x = sc.get(s["id"]) or {}
        if s.get("alive") and x.get("n", 0) >= 40 and ((x.get("pf") or 0) < 0.7 or (x.get("mean", 0) < 0 and (x.get("t") or 0) <= -2)):
            s["alive"], s["died"] = False, now
            killed.append(s["id"])
    alive = [s for s in st["specs"] if s.get("alive")]
    ids = {s["id"] for s in st["specs"]}
    best = sorted([s for s in alive if (sc.get(s["id"]) or {}).get("n", 0) >= 15], key=lambda s: -((sc[s["id"]].get("pf") or 0)))[:3]
    gen = max((s.get("gen", 0) for s in st["specs"]), default=0) + 1
    for i, s in enumerate(best):
        if len(alive) + len(spawned) >= MAX_LIVE:
            break
        for k in range(i, i + 8):
            m = mutate(s, k + day)
            m["id"] = sid(m)
            if m["id"] not in ids:
                st["specs"].append({**m, "gen": gen, "born": now, "alive": True, "parent": s["id"]})
                ids.add(m["id"])
                spawned.append(m["id"])
                break
    if killed or spawned:
        st.setdefault("generations", []).append({"t": now, "gen": gen, "spawned": spawned, "killed": killed})
        st["generations"] = st["generations"][-200:]


def summary(st: dict, src: dict | None = None) -> dict:
    sc = per_spec(st)
    rows = []
    for s in st.get("specs") or []:
        x = sc.get(s["id"]) or {"n": 0}
        rows.append({"id": s["id"], "family": s.get("family"), "src": s["src"], "gen": s.get("gen", 0), "alive": s.get("alive", True),
                     "born": s.get("born"), "open": sum(1 for p in st.get("open") or [] if p["spec"] == s["id"]), **x,
                     "candidate": candidate(x, src)})
    rows.sort(key=lambda r: (-(r.get("n", 0) >= 10), -(r.get("pf") or 0), -(r.get("sum") or 0)))
    return {"born": st.get("born"), "live": sum(1 for s in st.get("specs") or [] if s.get("alive")), "specs": len(st.get("specs") or []),
            "cycle_ms": st.get("cycle_ms"), "last_t": st.get("last_t"), "open": len(st.get("open") or []), "pending": len(st.get("pending") or []),
            "closed": len(st.get("closed") or []), "rows": rows, "generations": (st.get("generations") or [])[-10:],
            "rule_ru": "Кандидат: ≥ 30 сделок вперёд, t ≥ 3 (поправка на десятки спецификаций), плюс в обеих половинах, PF и просадка лучше "
                       "текущего источника. На реальный счёт — только после «да» владельца."}


# ── 22.11 17:19 (owner): watched traders found outside our exchanges — Hyperliquid wallets, PAPER only ─────────────────────────
def hl_book(prev: dict | None, wallets: list, positions_of, now: int, marks: dict) -> tuple[dict, dict]:
    """positions_of(addr) → [{coin, side, entry}]; returns (new book {addr: {coin: {side, entry, t}}}, desk-like {open, closed})"""
    book, opens, closed = {}, [], []
    prev = prev or {}
    for a in wallets:
        try:
            cur = {p["coin"]: p for p in positions_of(a) or []}
        except Exception:  # noqa: BLE001 — a failed read keeps the last book of that wallet
            book[a] = prev.get(a) or {}
            for coin, p in book[a].items():
                opens.append({"whale": a, "coin": coin, "side": p["side"], "entry": p["entry"], "opened_t": p["t"]})
            continue
        old = prev.get(a) or {}
        book[a] = {}
        for coin, p in cur.items():
            o = old.get(coin)
            # a position already open when we first read the wallet is not a new event (t = 0 → before the factory's birth)
            t = o["t"] if o and o["side"] == p["side"] else (now if a in prev else 0)
            book[a][coin] = {"side": p["side"], "entry": float(p["entry"]), "t": t}
            opens.append({"whale": a, "coin": coin, "side": p["side"], "entry": float(p["entry"]), "opened_t": t})
        for coin, o in old.items():
            if coin not in cur or cur[coin]["side"] != o["side"]:
                closed.append({"whale": a, "coin": coin, "side": o["side"], "entry": o["entry"], "opened_t": o["t"], "exit": marks.get(coin)})
    return book, {"open": opens, "closed": closed}


def ensure_watch_specs(st: dict, wallets: list, now: int, src: str = "hlw", family: str = "global") -> list:
    """one follow spec per watched wallet + the group specs (≥ 2 watched wallets on the same coin and side); returns the new ids"""
    have = {s["id"] for s in st.get("specs") or []}
    new = []
    base = {"side": "follow", "filt": [], "entry": "now", "stop": 16, "tp": None, "hold": None, "size": "fixed"}
    for a in wallets:
        s = {**base, "src": src, "family": family, "sub": a}
        s["id"] = sid(s)
        if s["id"] not in have:
            new.append(s)
    for g in ({"filt": ["agree2"]}, {"filt": ["agree2"], "entry": "retest"}, {"filt": ["agree2", "major"]}):
        s = {**base, **g, "src": src, "family": f"{family}·группа"}
        s["id"] = sid(s)
        if s["id"] not in have:
            new.append(s)
    for s in new:
        st.setdefault("specs", []).append({**s, "gen": 0, "born": now, "alive": True})
    if new:
        st.setdefault("generations", []).append({"t": now, "gen": "watch", "spawned": [s["id"] for s in new], "killed": []})
    return [s["id"] for s in new]


# ── 22.11 17:19 (owner): the post-mortem's fixes as specs (added to a running factory once) ─────────────────────────────────
POSTMORTEM_SPECS = [
    {"src": "whales", "family": "постмортем", "filt": ["cap2side"]},
    {"src": "whales", "family": "постмортем", "filt": ["cap2side", "reg4h20"]},
    {"src": "whales", "family": "постмортем", "filt": ["reg4h20"], "entry": "retest"},
    {"src": "whales", "family": "постмортем", "filt": ["sess_eu_late"]},
    {"src": "whales", "family": "постмортем", "filt": ["cap2side", "reg4h20", "agree2"]},
    # 22.11 17:55 (owner): Ivan on probation — as is vs + BTC regime vs + no 3rd copy on a side
    {"src": "whales", "family": "иван", "filt": ["ivan"]},
    {"src": "whales", "family": "иван", "filt": ["ivan", "reg4h20"]},
    {"src": "whales", "family": "иван", "filt": ["ivan", "cap2side"]},
    # 22.11 18:05 (owner): the real rules at 2 % risk + 3 % day stop vs 1 % + 2 % (the $ and the drawdown differ, the % per trade not)
    {"src": "whales", "family": "реальные правила", "size": "risk2", "daystop": 0.03},
    {"src": "whales", "family": "реальные правила", "size": "risk1", "daystop": 0.02},
    {"src": "whales", "family": "реальные правила", "size": "ladder", "daystop": "ladder"},
    # 23.11 11:44 (owner): around inflation / Fed data with a BTC 4h flip down — shorts ×1.5, longs ×0.5 (vs the baseline ×1)
    {"src": "whales", "family": "макро-перекос", "size": "macrobias"},
]


def macro_bias(events: list, c4: list, now: int) -> dict:
    """{"active", "event"}: active from the first 4h flip down (close < EMA20(4h) and a lower high) inside the 24 h before a CPI / PCE /
    FOMC release until 24 h after it. events = [(t, type)]; c4 = completed BTC 4h candles [(t, o, h, l, c)] oldest first"""
    ev = next(((t, ty) for t, ty in sorted(events) if t - 24 * H <= now <= t + 24 * H), None)
    if not ev or len(c4) < 22:
        return {"active": False, "event": ev}
    e, E = None, {}
    for k in c4:
        e = k[4] if e is None else e + (k[4] - e) * 2 / 21
        E[k[0]] = e
    flip = any(c4[i][4] < E[c4[i][0]] and c4[i][2] < c4[i - 1][2] for i in range(1, len(c4))
               if ev[0] - 24 * H <= c4[i][0] + 4 * H <= min(ev[0], now))
    return {"active": bool(flip), "event": ev}


def ensure_specs(st: dict, specs: list, now: int, tag: str) -> list:
    have = {s["id"] for s in st.get("specs") or []}
    base = {"side": "follow", "filt": [], "entry": "now", "stop": 16, "tp": None, "hold": None, "size": "fixed"}
    new = []
    for x in specs:
        s = {**base, **x}
        s["id"] = sid(s)
        if s["id"] not in have:
            have.add(s["id"])
            new.append(s)
            st.setdefault("specs", []).append({**s, "gen": 0, "born": now, "alive": True})
    if new:
        st.setdefault("generations", []).append({"t": now, "gen": tag, "spawned": [s["id"] for s in new], "killed": []})
    return [s["id"] for s in new]


# ── 22.11 22:36 (owner): a trader's public Telegram calls (cnq/agents/influencer.py) — PAPER spot buys, owner-only ──────────────
def tg_events(posts: list, coins: set, key: str, born: int) -> dict:
    """the channel's NEW calls since the factory's birth as a desk-like {open: […]} (recaps are not calls)"""
    from . import influencer as I
    opens = []
    for p in posts or []:
        if not p.get("t") or p["t"] < born:
            continue
        for c in I.classify(p, coins):
            if c["kind"] == "call" and c["side"] == "LONG":
                opens.append({"whale": key, "coin": c["coin"], "side": "LONG", "entry": c.get("entry") or 0.0, "opened_t": p["t"],
                              "targets_x": c["targets_x"], "targets_px": c["targets_px"], "id": p["id"]})
    return {"open": opens, "closed": []}


def manual_events(lines: list, key: str, born: int) -> list:
    """22.11 (owner): calls he forwards from the trader's Instagram — jsonl rows {t (ms or ISO), coin, entry?, targets_x?, targets_px?,
    source}; only those after the factory's birth, as desk-like opens of the same source"""
    from cnq.execution.owner_overrides import _ms
    out = []
    for r in lines or []:
        try:
            t = _ms(r.get("t"))
            coin = str(r.get("coin") or "").upper()
        except Exception:  # noqa: BLE001
            continue
        if t and t >= born and coin.isalnum():
            out.append({"whale": key, "coin": coin, "side": "LONG", "entry": float(r.get("entry") or 0), "opened_t": t,
                        "targets_x": [float(x) for x in r.get("targets_x") or []], "targets_px": [float(x) for x in r.get("targets_px") or []],
                        "id": f"m{t}{coin}", "source": str(r.get("source") or "manual")[:20]})
    return out


TG_SPECS = [
    {"src": "tgc", "family": "телеграм·основной (спот-лонги)", "stop": 30, "tp_own": True, "hold": 2160},   # 22.11: the main paper spec
    {"src": "tgc", "family": "телеграм", "stop": 99, "tp_own": True, "hold": 2160},
    {"src": "tgc", "family": "телеграм", "stop": 30, "tp_own": True, "hold": 2160, "filt": ["liquid", "reg1d200"]},
    {"src": "tgc", "family": "телеграм", "stop": 30, "hold": 720},
]


# ── 26.11 18:24 (owner): the hedge / carry filter and the widened pool (scripts/research/hedge_pool.py) — PAPER ─────────────────
HEDGE_SPECS = [
    {"src": "whales", "family": "без хеджей", "filt": ["nohedge"]},                         # the real copy stream without «хедж» traders
    {"src": "whales", "family": "без хеджей", "filt": ["nohedge"], "size": "risk1", "daystop": 2},
    {"src": "hlw", "family": "global·группа без хеджей", "filt": ["agree2", "nohedge"]},
]


def fut_events(posts: list, coins: set, key: str, born: int) -> dict:
    """26.11: a public futures-signal channel's NEW calls (cnq/agents/fut_calls) as desk-like opens; both sides, own stop / targets"""
    from . import fut_calls as FC
    opens = []
    for c in FC.calls([p for p in posts or [] if p.get("t") and p["t"] >= born], coins):
        z = [c["entry_lo"], c["entry_hi"]] if c.get("entry_lo") else None
        opens.append({"whale": key, "coin": c["coin"], "side": c["side"], "entry": (sum(z) / 2) if z else 0.0, "opened_t": c["t"],
                      "zone": z, "stop_px": c["stop"], "targets_px": c["targets"], "id": c["id"]})
    return {"open": opens, "closed": []}


def fut_specs(keys: list) -> list:
    """one paper desk per futures channel (its zone, its stop, its first target, ≤ 7 days) + all channels together, size = risk 1 %"""
    out = []
    for k in keys:
        out.append({"src": "tgf", "sub": k, "family": "телеграм·фьючерсы", "entry": "zone", "stop": 16, "stop_own": True, "tp_own": True,
                    "hold": 168, "size": "risk1"})
    if len(keys) >= 2:
        out.append({"src": "tgf", "family": "телеграм·фьючерсы·все", "entry": "zone", "stop": 16, "stop_own": True, "tp_own": True,
                    "hold": 168, "size": "risk1"})
    return out
