"""Hedge / carry filter + relaxed-bar pool (owner 26.11.2026 18:24). RESEARCH ONLY — no real-account change.

Pre-registered (26.11 ~19:30 Athens, before any result was seen):
A. HEDGE / CARRY flags («хедж») for every trader we follow, have passing or watch, and for every relaxed-bar candidate:
   Binance / OKX lead traders (ws_trips, closed trips of the last 60 days; decision = one trader's trips in the same coin + side within 4 h):
     carry      funding received ≥ 40 % of the trader's own P&L (funding = OKX USDT-swap funding history × the trip's notional over its
                hold; price P&L = ws_trips pnl), with ≥ 10 decisions and a positive total;
     carry2     ≥ 70 % of decisions on the funding-RECEIVING side, median hold ≥ 72 h and the mean price move of his trips < 0.5 %;
     offset     ≥ 25 % of decisions opened while the same trader holds the OPPOSITE side of the same coin (two-way book);
     bigmiss    his top-quartile-notional decisions (≥ 8) moved his way after 24 h AND after 72 h in < 45 % of cases (big size ≠ view).
   Hyperliquid wallets (watched + relaxed candidates; public API): the same carry (userFunding vs closedPnl over 30 days) and bigmiss
   (position opens, HL 1h candles), plus
     offset     spot balances of a coin (incl. its U-wrapped spot token) ≥ 50 % of a perp SHORT on it, or gross perp notional ≥ 0.5 × equity
                with |net delta incl. spot| ≤ 30 % of gross (delta-neutral book);
     bigbook    gross notional ≥ 3 × equity and 30-day closed P&L < 1 % of gross (a big book with a tiny price edge);
     cluster    (reported only) pairs of candidate wallets with opposite ≥ $1M positions in one coin within 20 % of size.
   «хедж» = any flag. Backtest: the lock traders' copies (exp_variants base: +2 min, stop −16 %, 0.11 % + funding, 60 days) with vs
   without the flagged traders; the same for the relaxed pool; HL copies (next 1h open after his open / close) with vs without.
B. RELAXED BAR (paper only; the strict bar stays for real): last 30 days, his own decisions — win ≥ 60 %, PF ≥ 1.5, ≥ 15 decisions, a
   trade in the last 7 days, not «хедж». Sources: ws_trips bn / okx (all traders the whale study stores), Hyperliquid leaderboard
   (accounts ≥ $100k, month P&L > 0; fills of 30 days). Stable = both 15-day halves positive AND our copies (stop −16 %) PF ≥ 1.2.
   Pick: the 3–5 best stable ones by copy PF (bn / okx) or own PF (HL), at most 3 per source.
Usage: hedge_pool.py DB OUT.json"""
import bisect, json, math, os, sqlite3, statistics as st, sys, time, urllib.request
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_delay as D  # noqa: E402
import exp_variants as V  # noqa: E402

M1, H, DAY = D.M1, D.H, D.DAY
OKX, HL, LB = "https://www.okx.com/api/v5", "https://api.hyperliquid.xyz/info", "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")


def http(url, body=None, timeout=40):
    for k in range(3):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                         headers={"User-Agent": "Mozilla/5.0 (cnq-research)", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception:  # noqa: BLE001
            time.sleep(1 + 2 * k)
    return None


def hl(body):
    time.sleep(0.25)
    return http(HL, body)


def f(x):
    try:
        return float(x)
    except Exception:  # noqa: BLE001
        return 0.0


FUND, OK1H = {}, {}


def okx_fund(coin, t0):
    """[(t, rate)] OKX funding history of coin-USDT-SWAP back to t0 (≤ 3 months served)"""
    if coin in FUND:
        return FUND[coin]
    out, after = [], None
    for _ in range(4):
        d = http(f"{OKX}/public/funding-rate-history?instId={coin}-USDT-SWAP&limit=100" + (f"&after={after}" if after else "")) or {}
        rows = d.get("data") or []
        if not rows:
            break
        out += [(int(r["fundingTime"]), f(r.get("realizedRate") or r.get("fundingRate"))) for r in rows]
        after = min(int(r["fundingTime"]) for r in rows)
        time.sleep(0.12)
        if after <= t0:
            break
    FUND[coin] = sorted(out)
    return FUND[coin]


def okx_1h(coin, t0, t1):
    if coin in OK1H:
        return OK1H[coin]
    out, after = {}, t1 + H
    for _ in range(25):
        d = http(f"{OKX}/market/history-candles?instId={coin}-USDT-SWAP&bar=1H&limit=100&after={after}") or {}
        rows = d.get("data") or []
        if not rows:
            break
        for r in rows:
            out[int(r[0])] = f(r[1])
        after = min(int(r[0]) for r in rows)
        time.sleep(0.21)
        if after <= t0:
            break
    OK1H[coin] = (sorted(out), out)
    return OK1H[coin]


def px_at(series, t):
    T, m = series
    i = bisect.bisect_left(T, t)
    return m[T[i]] if i < len(T) and T[i] - t <= 2 * H else None


def stat(xs):
    return V.stat(xs)


def decisions(lst):
    """ws_trips rows (src, trader, coin, side, open_t, close_t, entry, exit, notional, pnl) → decisions (4 h merge per coin + side)"""
    cur, out = {}, []
    for r in sorted(lst, key=lambda r: r[4]):
        k = (r[2].upper(), r[3])
        e = cur.get(k)
        if e and r[4] - e["open_t"] <= 4 * H:
            e["g"].append(r)
            e["close_t"] = max(e["close_t"], r[5])
        else:
            e = {"coin": r[2].upper(), "side": r[3], "open_t": r[4], "close_t": r[5], "g": [r]}
            cur[k] = e
            out.append(e)
    for e in out:
        ntl = sum(abs(r[8] or 0) for r in e["g"]) or 1.0
        e["ntl"] = ntl
        e["pnl"] = sum(r[9] or 0 for r in e["g"])
        s = 1 if e["side"] == "LONG" else -1
        e["ret"] = sum(s * ((r[7] or 0) / (r[6] or 1) - 1) * 100 * abs(r[8] or 0) for r in e["g"] if r[6] and r[7]) / ntl
    return out


def flags_ws(tid, decs, trips, t_from):
    """carry / carry2 / offset / bigmiss for one bn / okx trader over his decisions (closed, in the window)"""
    ds = [e for e in decs if e["open_t"] >= t_from and e["close_t"] > 0]
    out = {"n": len(ds)}
    if len(ds) < 5:
        return out
    fund_sum, price_sum, recv, holds = 0.0, 0.0, 0, []
    for e in ds:
        s = 1 if e["side"] == "LONG" else -1
        F = okx_fund(e["coin"], t_from)
        rs = [r for t, r in F if e["open_t"] < t <= e["close_t"]]
        fu = -s * sum(rs) * e["ntl"]                                    # funding he received (+) / paid (−), $
        fund_sum += fu
        price_sum += e["pnl"]
        if rs and -s * sum(rs) > 0:
            recv += 1
        holds.append((e["close_t"] - e["open_t"]) / H)
    tot = price_sum + fund_sum
    out.update({"funding_usd": round(fund_sum, 1), "pnl_usd": round(price_sum, 1), "recv_share": round(recv / len(ds), 2),
                "hold_med_h": round(st.median(holds), 1), "mean_move": round(st.mean(e["ret"] for e in ds), 2)})
    out["fund_share"] = round(fund_sum / tot, 2) if tot > 0 else None
    fl = []
    if len(ds) >= 10 and tot > 0 and fund_sum / tot >= 0.4:
        fl.append("carry")
    if out["recv_share"] >= 0.7 and out["hold_med_h"] >= 72 and out["mean_move"] < 0.5:
        fl.append("carry2")
    opp = 0
    for e in ds:
        o = "SHORT" if e["side"] == "LONG" else "LONG"
        if any(a < e["open_t"] < (b or 1e18) for a, b in trips.get((e["coin"], o), [])):
            opp += 1
    out["offset_share"] = round(opp / len(ds), 2)
    if out["offset_share"] >= 0.25:
        fl.append("offset")
    q = sorted(e["ntl"] for e in ds)[int(len(ds) * 0.75)]
    big = [e for e in ds if e["ntl"] >= q]
    h24 = h72 = n = 0
    for e in big:
        S = okx_1h(e["coin"], t_from - DAY, int(time.time() * 1000))
        a, b, c = px_at(S, e["open_t"]), px_at(S, e["open_t"] + 24 * H), px_at(S, e["open_t"] + 72 * H)
        if a and b and c:
            s = 1 if e["side"] == "LONG" else -1
            n += 1
            h24 += s * (b - a) > 0
            h72 += s * (c - a) > 0
    out.update({"big_n": n, "big_hit24": round(h24 / n, 2) if n else None, "big_hit72": round(h72 / n, 2) if n else None})
    if n >= 8 and h24 / n < 0.45 and h72 / n < 0.45:
        fl.append("bigmiss")
    out["flags"] = fl
    return out


def own_bar(ds, t_from, t_mid):
    xs = [e["pnl"] for e in ds if e["open_t"] >= t_from]
    pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
    h1 = sum(e["pnl"] for e in ds if t_from <= e["open_t"] < t_mid)
    h2 = sum(e["pnl"] for e in ds if e["open_t"] >= t_mid)
    return {"n": len(xs), "win": round(sum(x > 0 for x in xs) / len(xs), 3) if xs else 0, "pf": round(pos / neg, 2) if neg else (99.0 if pos else 0),
            "pnl": round(sum(xs), 1), "h1": round(h1, 1), "h2": round(h2, 1)}


# ── Hyperliquid ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
HLC = {}


def hl_1h(coin, t0, t1):
    if coin not in HLC:
        c = hl({"type": "candleSnapshot", "req": {"coin": coin, "interval": "1h", "startTime": t0, "endTime": t1}}) or []
        m = {int(x["t"]): f(x["o"]) for x in c if isinstance(x, dict)}
        HLC[coin] = (sorted(m), m)
    return HLC[coin]


def hl_wallet(a, now):
    t30 = now - 30 * DAY
    fills, t = [], t30
    for _ in range(3):
        fl = hl({"type": "userFillsByTime", "user": a, "startTime": t, "aggregateByTime": True}) or []
        if not isinstance(fl, list) or not fl:
            break
        fills += fl
        if len(fl) < 2000:
            break
        t = int(fl[-1]["time"]) + 1
    fills = [x for x in fills if isinstance(x, dict) and "/" not in str(x.get("coin")) and not str(x.get("coin", "")).startswith("@")]
    fu = hl({"type": "userFunding", "user": a, "startTime": t30}) or []
    fund = sum(f((x.get("delta") or {}).get("usdc")) for x in fu if isinstance(x, dict))
    ch = hl({"type": "clearinghouseState", "user": a}) or {}
    sp = hl({"type": "spotClearinghouseState", "user": a}) or {}
    eq = f((ch.get("marginSummary") or {}).get("accountValue"))
    pos, mark = {}, {}
    for p in ch.get("assetPositions") or []:
        q = p.get("position") or {}
        sz = f(q.get("szi"))
        if sz:
            c = str(q.get("coin")).upper()
            pos[c] = (1 if sz > 0 else -1) * f(q.get("positionValue"))
            mark[c] = f(q.get("positionValue")) / abs(sz)
    spot = {}
    for b in sp.get("balances") or []:
        c = str(b.get("coin") or "").upper()
        c = c[1:] if c not in pos and c.startswith("U") and c[1:] in pos else c      # UBTC / UETH … = the wrapped spot coin
        if c in pos and f(b.get("total")):
            spot[c] = spot.get(c, 0.0) + f(b.get("total")) * mark[c]
    gross = sum(abs(v) for v in pos.values())
    net = abs(sum(pos.values()) + sum(spot.values()))
    # decisions: closing fills grouped per coin within 4 h; episodes (flat → flat) for copies and big opens
    closes, ep, cur = defaultdict(list), [], {}
    for x in sorted(fills, key=lambda x: int(x["time"])):
        c, tm, cp = str(x["coin"]).upper(), int(x["time"]), f(x.get("closedPnl")) - f(x.get("fee"))
        sp0, sz = f(x.get("startPosition")), f(x.get("sz")) * (1 if x.get("side") == "B" else -1)
        end = sp0 + sz
        if f(x.get("closedPnl")):
            g = closes[c]
            if g and tm - g[-1]["t0"] <= 4 * H:
                g[-1]["pnl"] += cp
            else:
                g.append({"t0": tm, "pnl": cp})
        if abs(sp0) < 1e-12 and abs(end) > 1e-12:
            cur[c] = {"coin": c, "side": "LONG" if end > 0 else "SHORT", "t_open": tm, "ntl": abs(sz) * f(x.get("px"))}
        elif c in cur and abs(end) > 1e-12 and abs(end) > abs(sp0):
            cur[c]["ntl"] += abs(sz) * f(x.get("px"))
        if c in cur and (abs(end) < 1e-12 or (sp0 * end < 0)):
            e = cur.pop(c)
            e["t_close"] = tm
            ep.append(e)
            if sp0 * end < 0:
                cur[c] = {"coin": c, "side": "LONG" if end > 0 else "SHORT", "t_open": tm, "ntl": abs(end) * f(x.get("px"))}
    ds = sorted([d for g in closes.values() for d in g], key=lambda d: d["t0"])
    pnl = sum(d["pnl"] for d in ds)
    pos_, neg_ = sum(d["pnl"] for d in ds if d["pnl"] > 0), -sum(d["pnl"] for d in ds if d["pnl"] < 0)
    mid = now - 15 * DAY
    last = max((int(x["time"]) for x in fills), default=0)
    o = {"addr": a, "equity": round(eq), "n": len(ds), "win": round(sum(d["pnl"] > 0 for d in ds) / len(ds), 3) if ds else 0,
         "pf": round(pos_ / neg_, 2) if neg_ else (99.0 if pos_ else 0), "pnl": round(pnl), "funding": round(fund),
         "h1": round(sum(d["pnl"] for d in ds if d["t0"] < mid)), "h2": round(sum(d["pnl"] for d in ds if d["t0"] >= mid)),
         "last_d": round((now - last) / DAY, 1) if last else None, "gross": round(gross), "net": round(net),
         "fills": len(fills), "episodes": len(ep), "pos": {k: round(v) for k, v in pos.items()}}
    tot = pnl + fund
    o["fund_share"] = round(fund / tot, 2) if tot > 0 else None
    fl = []
    if len(ds) >= 10 and tot > 0 and fund / tot >= 0.4:
        fl.append("carry")
    if any(pos[c] < 0 and spot.get(c, 0) >= 0.5 * abs(pos[c]) for c in spot) or (eq > 0 and gross >= 0.5 * eq and gross and net / gross <= 0.3):
        fl.append("offset")
    if eq > 0 and gross >= 3 * eq and abs(pnl) < 0.01 * gross:
        fl.append("bigbook")
    big = sorted(ep, key=lambda e: -e["ntl"])[:max(1, len(ep) // 4)] if ep else []
    big = [e for e in big if e["t_open"] <= now - 72 * H][:12]
    n = h24 = h72 = 0
    for e in big:
        S = hl_1h(e["coin"], now - 32 * DAY, now)
        p0, p1, p3 = px_at(S, e["t_open"] + H), px_at(S, e["t_open"] + 25 * H), px_at(S, e["t_open"] + 73 * H)
        if p0 and p1 and p3:
            s = 1 if e["side"] == "LONG" else -1
            n += 1
            h24 += s * (p1 - p0) > 0
            h72 += s * (p3 - p0) > 0
    o.update({"big_n": n, "big_hit24": round(h24 / n, 2) if n else None, "big_hit72": round(h72 / n, 2) if n else None})
    if n >= 8 and h24 / n < 0.45 and h72 / n < 0.45:
        fl.append("bigmiss")
    o["flags"] = fl
    o["_ep"] = ep
    return o


def hl_copies(o, now):
    xs = []
    for e in o.get("_ep") or []:
        if e["t_close"] - e["t_open"] < H:
            continue                                           # we copy on a 5-min pass; < 1 h round trips are not copyable
        S = hl_1h(e["coin"], now - 32 * DAY, now)
        a, b = px_at(S, e["t_open"] + H), px_at(S, e["t_close"] + H)
        if a and b:
            s = 1 if e["side"] == "LONG" else -1
            xs.append((e["t_open"], s * (b / a - 1) * 100 - 0.11))
    return xs


def main():
    db, out = sys.argv[1], sys.argv[2]
    now = int(time.time() * 1000)
    wt = json.load(open(os.path.join(ROOT, "web", "whales_top.json")))
    fol = {x.get("addr") for x in wt.get("following") or [] if isinstance(x, dict)}
    lock = V.load_lock()
    watch = [w["addr"].lower() for w in (json.load(open(os.path.join(ROOT, "config", "exp_watch_traders.json"))).get("hl") or [])]
    c = sqlite3.connect(db)
    rows = c.execute("SELECT src, trader, coin, side, open_t, close_t, entry, exit, notional, pnl FROM ws_trips "
                     "WHERE coin IS NOT NULL AND coin != '' ORDER BY open_t").fetchall()
    srcs = defaultdict(int)
    by = defaultdict(list)
    for r in rows:
        tid = r[1] if str(r[1]).startswith(r[0] + ":") else f"{r[0]}:{r[1]}"
        by[tid].append(r)
        srcs[r[0]] += 1
    t_end = max((r[5] for r in rows if r[5]), default=now)
    t30, t60, t7 = t_end - 30 * DAY, t_end - 60 * DAY, now - 7 * DAY
    print(f"::notice title=hp db::rows {len(rows)} by src {dict(srcs)}, traders {len(by)}, newest close "
          f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(t_end / 1000))} UTC, lock {len(lock)}, following {len(fol)}", flush=True)
    # ── B (bn / okx): relaxed bar on his own decisions ──
    cand, D_ = {}, {}
    for tid, lst in by.items():
        if not tid.split(":")[0] in ("bn", "okx"):
            continue
        ds = decisions(lst)
        D_[tid] = ds
        closed = [e for e in ds if e["close_t"] > 0]
        act = max((max(r[4], r[5] or 0) for r in lst), default=0) >= t7 or any(not r[5] for r in lst if r[4] >= now - 30 * DAY)
        b = own_bar(closed, t30, t_end - 15 * DAY)
        b["active7"] = bool(act)
        if b["n"] >= 15 and b["win"] >= 0.6 and b["pf"] >= 1.5 and act:
            cand[tid] = b
    relaxed_n = len(cand)
    print(f"::notice title=hp relaxed bn/okx::{relaxed_n} pass the relaxed bar (of {len(D_)} traders)", flush=True)
    # ── A (bn / okx): flags for lock + candidates ──
    need = (lock | fol | set(cand)) & set(D_)
    FL = {}
    for tid in sorted(need):
        trips = defaultdict(list)
        for r in by[tid]:
            trips[(r[2].upper(), r[3])].append((r[4], r[5] or None))
        FL[tid] = flags_ws(tid, D_[tid], trips, t60)
    flagged = {t for t, v in FL.items() if v.get("flags")}
    print(f"::notice title=hp flags bn/okx::checked {len(FL)}, flagged {len(flagged)}: " + json.dumps(
        {t: FL[t]["flags"] for t in sorted(flagged)})[:3800], flush=True)
    # copies (exp_variants base) for lock + candidates
    eps = []
    for tid in need:
        for e in D_[tid]:
            if e["open_t"] >= t60 and e["close_t"] > 0:
                eps.append({**e, "trader": tid})
    syms = defaultdict(list)
    for e in eps:
        syms[e["coin"] + "USDT"].append((e["open_t"] - H, e["close_t"]))
    data = {}
    for n, (sym, spans) in enumerate(sorted(syms.items(), key=lambda kv: -len(kv[1]))):
        S = D.load_sym(sym, spans)
        if S:
            fl_ = okx_fund(sym[:-4], t60)
            last = S["fund"][-1][0] if S["fund"] else 0
            S["fund"] = S["fund"] + [(t, r) for t, r in fl_ if t > last]
        data[sym] = S
        if n % 20 == 0:
            print(f"candles {n + 1}/{len(syms)}", flush=True)
    cp = defaultdict(list)
    for e in sorted(eps, key=lambda x: x["open_t"]):
        S = data.get(e["coin"] + "USDT")
        if not S:
            continue
        r = V.sim(S, e["side"], e["open_t"] + 2 * M1, e["close_t"] + 2 * M1)
        if r:
            cp[e["trader"]].append((e["open_t"], r[0]))
    def agg(ts, excl=()):
        xs = sorted(x for t in ts if t not in excl for x in cp.get(t, []))
        return stat([x[1] for x in xs])
    bt = {"lock_all": agg(lock), "lock_no_hedge": agg(lock, flagged), "lock_flagged_only": agg(lock & flagged),
          "relaxed_all": agg(set(cand)), "relaxed_no_hedge": agg(set(cand), flagged)}
    print("::notice title=hp backtest bn/okx::" + json.dumps(bt)[:3900], flush=True)
    # stable picks bn / okx
    picks = []
    for tid, b in cand.items():
        x30 = [x for t, x in cp.get(tid, []) if t >= t30]
        s = stat(x30)
        b["copy30"] = s
        b["flags"] = (FL.get(tid) or {}).get("flags") or []
        b["stable"] = bool(b["h1"] > 0 and b["h2"] > 0 and s.get("n", 0) >= 10 and (s.get("pf") or 0) >= 1.2 and not b["flags"])
        if b["stable"]:
            picks.append(tid)
    picks.sort(key=lambda t: -(cand[t]["copy30"].get("pf") or 0))
    print("::notice title=hp relaxed list::" + json.dumps({t: {k: cand[t].get(k) for k in ("n", "win", "pf", "pnl", "h1", "h2", "stable", "flags")}
                                                         | {"copy": {k: cand[t]["copy30"].get(k) for k in ("n", "win", "pf", "mean")}}
                                                         for t in sorted(cand, key=lambda t: -(cand[t]["copy30"].get("pf") or 0))[:25]},
                                                        ensure_ascii=False)[:3900], flush=True)
    # ── Hyperliquid: watched + leaderboard candidates ──
    lb = http(LB, None, 120) or {}
    hrows = []
    for x in lb.get("leaderboardRows") or []:
        m = {k: v for k, v in (x.get("windowPerformances") or [])}
        av, pm = f(x.get("accountValue")), f((m.get("month") or {}).get("pnl"))
        if av >= 1e5 and pm > 0 and f((m.get("month") or {}).get("vlm")) > 0:
            hrows.append(((x.get("ethAddress") or "").lower(), av, pm))
    hc = {a for a, _, _ in sorted(hrows, key=lambda r: -r[2] / r[1])[:150]} | {a for a, _, _ in sorted(hrows, key=lambda r: -r[2])[:100]}
    hc = list(dict.fromkeys(watch + sorted(hc)))
    print(f"hl candidates {len(hc)}", flush=True)
    HW = {}
    for n, a in enumerate(hc):
        try:
            HW[a] = hl_wallet(a, now)
        except Exception as e:  # noqa: BLE001
            HW[a] = {"addr": a, "error": type(e).__name__, "flags": []}
        if n % 25 == 0:
            print(f"hl {n + 1}/{len(hc)}", flush=True)
    # clusters: opposite ≥ $1M positions in one coin within 20 % of size
    clusters = []
    ws = [w for w in HW.values() if w.get("pos")]
    for i in range(len(ws)):
        for j in range(i + 1, len(ws)):
            for c_, v in ws[i]["pos"].items():
                u = ws[j]["pos"].get(c_)
                if u and v * u < 0 and min(abs(v), abs(u)) >= 1e6 and abs(abs(v) - abs(u)) <= 0.2 * max(abs(v), abs(u)):
                    clusters.append([ws[i]["addr"], ws[j]["addr"], c_, v, u])
    hflag = {a for a, w in HW.items() if w.get("flags")}
    hrel = {a: w for a, w in HW.items() if w.get("n", 0) >= 15 and w.get("win", 0) >= 0.6 and w.get("pf", 0) >= 1.5
            and w.get("last_d") is not None and w["last_d"] <= 7}
    hcp = {a: hl_copies(HW[a], now) for a in set(hrel) | set(watch) if a in HW}
    def hagg(ad):
        return stat([x for _, x in sorted(x for a in ad for x in hcp.get(a, []))])
    hbt = {"watch_all": hagg(set(watch)), "watch_no_hedge": hagg(set(watch) - hflag),
           "relaxed_all": hagg(set(hrel)), "relaxed_no_hedge": hagg(set(hrel) - hflag), "relaxed_flagged": hagg(set(hrel) & hflag)}
    hpicks = []
    for a, w in hrel.items():
        s = stat([x for _, x in hcp.get(a, [])])
        w["copy"] = s
        w["stable"] = bool(w["h1"] > 0 and w["h2"] > 0 and not w["flags"] and s.get("n", 0) >= 10 and (s.get("pf") or 0) >= 1.2)
        if w["stable"]:
            hpicks.append(a)
    hpicks.sort(key=lambda a: -(HW[a]["copy"].get("pf") or 0))
    keep = ("n", "win", "pf", "pnl", "funding", "fund_share", "h1", "h2", "last_d", "equity", "gross", "net", "big_n", "big_hit24", "big_hit72", "flags")
    print(f"::notice title=hp hl::candidates {len(HW)}, flagged {len(hflag)}, relaxed {len(hrel)}, stable {len(hpicks)}, clusters {len(clusters)}; "
          "flag counts " + json.dumps({k: sum(k in (w.get('flags') or []) for w in HW.values()) for k in ("carry", "offset", "bigbook", "bigmiss")}), flush=True)
    print("::notice title=hp hl watched::" + json.dumps({a[:10]: {k: HW[a].get(k) for k in keep} for a in watch if a in HW})[:3900], flush=True)
    print("::notice title=hp hl backtest::" + json.dumps(hbt)[:3900], flush=True)
    print("::notice title=hp hl relaxed::" + json.dumps({a: {k: HW[a].get(k) for k in ("n", "win", "pf", "pnl", "h1", "h2", "last_d", "equity", "flags")}
                                                       | {"copy": {k: HW[a]["copy"].get(k) for k in ("n", "win", "pf", "mean")}}
                                                       for a in sorted(hrel, key=lambda a: -(HW[a]["copy"].get("pf") or 0))[:12]})[:3900], flush=True)
    pick = [("ws", t) for t in picks[:3]] + [("hl", a) for a in hpicks[:3]]
    print("::notice title=hp picks::" + json.dumps(pick), flush=True)
    for w in HW.values():
        w.pop("_ep", None)
    json.dump({"t": now, "t_end": t_end, "ws_flags": FL, "ws_backtest": bt, "ws_relaxed": cand, "ws_picks": picks,
               "hl": HW, "hl_backtest": hbt, "hl_relaxed": list(hrel), "hl_picks": hpicks, "clusters": clusters[:50]},
              open(out, "w"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
