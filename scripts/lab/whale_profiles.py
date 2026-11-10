"""Whale profiles by day / session / BTC regime + out-of-sample checks (backlog: owner 10.11 «по каждому киту вести аналитику по дням»).
Research only: reads data/whale_trades/*.jsonl, web/whales_top.json, web/public_real.json and the 15-min candles in data/agents.sqlite.
Usage: python scripts/lab/whale_profiles.py [out.json]   → docs/research/whale-profiles-2026-11-10.md

Our copy at live sizing: risk 3% of the $2 350 futures budget at a 16% stop → notional $440.6 per copy, round-trip costs 0.11%,
a loss is capped at the stop (−$70.5 − costs). Decision = one trader's fills in one coin and side opened within 4 h of the first."""
import sys, json, glob, math, bisect, sqlite3, statistics as st, datetime as dt, collections, random

BUDGET, RISK, STOP, COST = 2350.0, 0.03, 0.16, 0.0011
NOTIONAL = BUDGET * RISK / STOP
H4, M15, DAY = 4 * 3600_000, 15 * 60_000, 86_400_000
random.seed(7)


def utc(t):
    return dt.datetime.fromtimestamp(t / 1000, dt.UTC)


def load():
    fills, names, src = {}, {}, {}
    for f in sorted(glob.glob("data/whale_trades/*.jsonl")):
        for line in open(f):
            d = json.loads(line)
            names[d["trader"]] = d.get("name") or d["trader"]
            src[d["trader"]] = d.get("src")
            if d["kind"] == "closed" and d.get("entry") and d.get("exit") and d.get("notional"):
                fills[(d["trader"], d["id"])] = d
    by = collections.defaultdict(list)
    for d in fills.values():
        by[(d["trader"], d["coin"], d["side"])].append(d)
    dec = []
    for (tr, coin, side), fs in by.items():
        fs.sort(key=lambda d: d["open_t"])
        cur = None
        for d in fs:
            if cur and d["open_t"] - cur["open_t"] <= H4:
                cur["fills"].append(d)
            else:
                cur = {"trader": tr, "coin": coin, "side": side, "open_t": d["open_t"], "fills": [d]}
                dec.append(cur)
    for x in dec:
        fs = x.pop("fills")
        n = sum(abs(f["notional"]) for f in fs)
        x["notional"] = n
        x["entry"] = sum(f["entry"] * abs(f["notional"]) for f in fs) / n
        x["exit"] = sum(f["exit"] * abs(f["notional"]) for f in fs) / n
        x["close_t"] = max(f["close_t"] for f in fs)
        x["pnl"] = sum(f["pnl"] for f in fs)
        x["lev"] = st.median([f.get("lev") or 1 for f in fs])
        sg = 1 if x["side"] == "LONG" else -1
        x["move"] = sg * (x["exit"] / x["entry"] - 1)
        x["hold_h"] = (x["close_t"] - x["open_t"]) / 3600_000
    dec.sort(key=lambda x: x["open_t"])
    return dec, names, src


def btc_series(dec, db):
    pts = []
    for x in dec:
        if x["coin"] == "BTC":
            pts += [(x["open_t"], x["entry"]), (x["close_t"], x["exit"])]
    try:
        c = sqlite3.connect(db)
        pts += [(t + M15, cl) for t, cl in c.execute("select t, c from ws_candles where coin='BTC'")]
    except Exception:
        pass
    pts.sort()
    # drop spikes: a point more than 3% away from the median of its 9 neighbours (wrong-instrument fills)
    px = [p for _, p in pts]
    keep = [pts[i] for i in range(len(pts)) if abs(px[i] / st.median(px[max(0, i - 4):i + 5]) - 1) < 0.03]
    return [t for t, _ in keep], [p for _, p in keep]


def px_at(T, ts, ps, tol=3 * 3600_000):
    i = bisect.bisect_right(ts, T) - 1          # last price known at T (no look-ahead)
    if i < 0 or T - ts[i] > tol:
        return None
    return ps[i]


def our_usd(move):
    m = max(move, -STOP)
    return NOTIONAL * (m - COST)


def pf(xs):
    g = sum(v for v in xs if v > 0); l_ = -sum(v for v in xs if v < 0)
    return round(g / l_, 2) if l_ > 0 else (99.0 if g > 0 else 0.0)


def summ(xs):
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "win": round(sum(v > 0 for v in xs) / len(xs) * 100), "pf": pf(xs), "avg": round(sum(xs) / len(xs), 2),
            "sum": round(sum(xs), 1)}


def tstat(xs):
    if len(xs) < 3:
        return 0.0
    sd = st.pstdev(xs)
    return round(sum(xs) / len(xs) / (sd / math.sqrt(len(xs))), 2) if sd > 0 else 0.0


def maxdd(xs):
    eq = pk = dd = 0.0
    for v in xs:
        eq += v; pk = max(pk, eq); dd = min(dd, eq - pk)
    return round(dd, 1)


SESS = [(0, 8, "Азия 00–08"), (8, 14, "Европа 08–14"), (14, 21, "США 14–21"), (21, 24, "Ночь 21–24")]


def session(t):
    h = utc(t).hour
    return next(n for a, b, n in SESS if a <= h < b)


def tag(dec, ts, ps):
    for x in dec:
        p0, p1, p7 = px_at(x["open_t"], ts, ps), px_at(x["open_t"] - DAY, ts, ps), px_at(x["open_t"] - 7 * DAY, ts, ps, 12 * 3600_000)
        x["r24"] = None if not (p0 and p1) else ("вверх" if p0 > p1 else "вниз")
        x["r7"] = None if not (p0 and p7) else ("вверх" if p0 > p7 else "вниз")
        x["wd"] = utc(x["open_t"]).strftime("%a")
        x["sess"] = session(x["open_t"])
        x["usd"] = our_usd(x["move"])
        x["margin"] = NOTIONAL / max(1.0, x["lev"])
        x["mday"] = x["margin"] * max(x["hold_h"], 0.25) / 24       # $·days of margin tied up
    # flips: next decision of the same trader in the same coin, opposite side, opened ≤ 15 min after this one closed
    last = {}
    for x in sorted(dec, key=lambda x: x["open_t"]):
        k = (x["trader"], x["coin"])
        p = last.get(k)
        x["flip"] = False
        if p and p["side"] != x["side"] and x["open_t"] - p["close_t"] <= M15:
            p["flip"] = True
        last[k] = x if (not p or x["close_t"] >= p["close_t"]) else p
    for x in dec:
        x["fast"] = x["hold_h"] * 60 < 15
    return dec


def style(ds):
    hm = st.median([x["hold_h"] for x in ds])
    fr = sum(x["flip"] or x["fast"] for x in ds) / len(ds)
    if fr >= 0.2:
        return "скальпер/перевёртыш", hm, fr
    if hm >= 72:
        return "свинг", hm, fr
    return "интрадей–дни", hm, fr


def profile(tr, ds, names):
    s, hm, fr = style(ds)
    o = {"trader": tr, "name": names.get(tr, tr), "n": len(ds), "style": s, "hold_med_h": round(hm, 1), "flip_fast_pct": round(fr * 100),
         "his_avg_usd": round(sum(x["pnl"] for x in ds) / len(ds), 1), "our_avg_usd": round(sum(x["usd"] for x in ds) / len(ds), 2),
         "our_sum_usd": round(sum(x["usd"] for x in ds), 1), "our_small_pct": round(sum(abs(x["usd"]) < 3 for x in ds) / len(ds) * 100),
         "usd_per_100_mday": round(sum(x["usd"] for x in ds) / max(1e-9, sum(x["mday"] for x in ds)) * 100, 2),
         "move_med_pct": round(st.median([abs(x["move"]) for x in ds]) * 100, 2)}
    for key, vals in (("side", ["LONG", "SHORT"]), ("r24", ["вверх", "вниз"]), ("r7", ["вверх", "вниз"]), ("sess", [n for *_, n in SESS]),
                      ("wd", ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])):
        o[key] = {v: summ([x["usd"] for x in ds if x[key] == v]) for v in vals}
    o["side_r7"] = {f"{sd}/{r}": summ([x["usd"] for x in ds if x["side"] == sd and x["r7"] == r]) for sd in ("LONG", "SHORT") for r in ("вверх", "вниз")}
    return o


# ---------- 2. persistence of "cells" (trader × side × regime / weekday / session) between halves ----------
CELLS = {"сторона×тренд7д": lambda x: (x["side"], x["r7"]), "сторона×24ч": lambda x: (x["side"], x["r24"]), "сторона": lambda x: x["side"],
         "день недели": lambda x: x["wd"], "сессия": lambda x: x["sess"]}


def persistence(dec, split, traders, min_n=5):
    out = {}
    for fam, fn in CELLS.items():
        pairs = []
        for tr in traders:
            ds = [x for x in dec if x["trader"] == tr and fn(x) is not None and None not in (fn(x) if isinstance(fn(x), tuple) else (fn(x),))]
            a = collections.defaultdict(list); b = collections.defaultdict(list)
            for x in ds:
                (a if x["open_t"] < split else b)[fn(x)].append(x["usd"])
            allA = [v for vs in a.values() for v in vs]
            if len(allA) < 2 * min_n:
                continue
            mA_tr = sum(allA) / len(allA)
            allB = [v for vs in b.values() for v in vs]
            mB_tr = sum(allB) / len(allB) if allB else 0
            for k in a:
                if len(a[k]) >= min_n and len(b[k]) >= min_n:
                    # cell edge relative to the same trader's own average (so the test is "this cell", not "this trader")
                    pairs.append((sum(a[k]) / len(a[k]) - mA_tr, sum(b[k]) / len(b[k]) - mB_tr, len(b[k]), sum(b[k]) / len(b[k])))
        if len(pairs) < 4:
            out[fam] = {"cells": len(pairs)}
            continue
        xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
        r = cov / math.sqrt(sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys) or 1)
        # permutation p-value of r (shuffle the 2nd-half edges)
        cnt = 0
        for _ in range(2000):
            yp = ys[:]; random.shuffle(yp)
            myp = sum(yp) / len(yp)
            rp = sum((a - mx) * (b - myp) for a, b in zip(xs, yp)) / math.sqrt(sum((a - mx) ** 2 for a in xs) * sum((b - myp) ** 2 for b in yp) or 1)
            cnt += rp >= r
        good = [p for p in pairs if p[0] > 0]; bad = [p for p in pairs if p[0] <= 0]
        out[fam] = {"cells": len(pairs), "r": round(r, 3), "p_perm": round(cnt / 2000, 3), "sign_kept_pct": round(sum((a > 0) == (b > 0) for a, b in zip(xs, ys)) / len(xs) * 100),
                    "h2_edge_good_cells": round(sum(p[1] * p[2] for p in good) / max(1, sum(p[2] for p in good)), 2),
                    "h2_edge_bad_cells": round(sum(p[1] * p[2] for p in bad) / max(1, sum(p[2] for p in bad)), 2),
                    "h2_n_good": sum(p[2] for p in good), "h2_n_bad": sum(p[2] for p in bad)}
    return out


# ---------- 3. walk-forward rotation: current single A vs an A/B/C mix ----------
def qualifies(ds):
    """the live follow rule, simplified to what the archive shows: ≥ 20 decisions in 60 days, ≥ 70% winners, PF ≥ 2, 60-day P&L > 0"""
    if len(ds) < 20:
        return False
    w = sum(x["pnl"] > 0 for x in ds) / len(ds)
    return w >= 0.70 and pf([x["pnl"] for x in ds]) >= 2 and sum(x["pnl"] for x in ds) > 0


def walk(dec, start, end, scheme, step=3 * DAY, look=60 * DAY, n_a=7):
    taken = []
    by_tr = collections.defaultdict(list)
    for x in dec:
        by_tr[x["trader"]].append(x)
    t = start
    while t < end:
        hist = {tr: [x for x in ds if t - look <= x["close_t"] < t] for tr, ds in by_tr.items()}   # only decisions already closed at t
        ranked = sorted([tr for tr, ds in hist.items() if qualifies(ds)], key=lambda tr: -pf([x["pnl"] for x in hist[tr]]))
        rules = {}
        A = ranked[:n_a]
        for tr in A:
            rules[tr] = None                                                     # copy everything
        if scheme == "wide_all":                                               # control: every trader with ≥ 20 decisions, no quality bar
            rules = {tr: None for tr, ds in hist.items() if len(ds) >= 20}
        if scheme in ("abc", "abc_specialists_only", "abc_b_allcells"):
            # B: any trader in the 60-day window (≥ 20 decisions, not in A) whose own copies in one side × 7-day-trend cell made
            # ≥ 8 decisions with our-$ PF ≥ 2 and positive sum → copy only that cell
            cand = [tr for tr, ds in hist.items() if tr not in A and len(ds) >= 20]
            for tr in cand:
                cells = []
                for sd in ("LONG", "SHORT"):
                    for r in ("вверх", "вниз"):
                        v = [x["usd"] for x in hist[tr] if x["side"] == sd and x["r7"] == r]
                        if len(v) >= 8 and pf(v) >= 2 and sum(v) > 0:
                            cells.append((sd, r))
                if cells:
                    rules[tr] = cells
            if scheme == "abc_b_allcells":                                     # control: the same B traders, but all their decisions
                rules = {k: None for k in rules}
            if scheme == "abc_specialists_only":
                rules = {k: v for k, v in rules.items() if v is not None}
        if scheme == "a_regime":
            # A traders, but each only in the side × trend cells where his last-60-day copies were positive
            for tr in A:
                cells = []
                for sd in ("LONG", "SHORT"):
                    for r in ("вверх", "вниз"):
                        v = [x["usd"] for x in hist[tr] if x["side"] == sd and x["r7"] == r]
                        if len(v) < 5 or sum(v) > 0:
                            cells.append((sd, r))
                rules[tr] = cells
        for tr, cells in rules.items():
            for x in by_tr[tr]:
                if t <= x["open_t"] < t + step and (cells is None or (x["side"], x["r7"]) in cells):
                    taken.append(x)
        t += step
    taken.sort(key=lambda x: x["close_t"])
    return taken


def capped(tk, cap):
    book, kept = [], []
    for x in sorted(tk, key=lambda x: x["open_t"]):
        book = [c for c in book if c > x["open_t"]]
        if len(book) < cap:
            book.append(x["close_t"]); kept.append(x)
    return kept


def peak_open(tk):
    ev = sorted([(x["open_t"], 1) for x in tk] + [(x["close_t"], -1) for x in tk])
    cur = pk = 0
    for _, d in ev:
        cur += d; pk = max(pk, cur)
    return pk


# ---------- 5. exits on the 15-min candles ----------
def candle_paths(dec, db):
    c = sqlite3.connect(db)
    bars = collections.defaultdict(list)
    for coin, t, o, h, l_, cl in c.execute("select coin, t, o, h, l, c from ws_candles order by coin, t"):
        bars[coin].append((t, o, h, l_, cl))
    idx = {k: [b[0] for b in v] for k, v in bars.items()}
    out = []
    for x in dec:
        B = bars.get(x["coin"])
        if not B:
            continue
        T = idx[x["coin"]]
        i0 = bisect.bisect_right(T, x["open_t"] + 2 * 60_000) - 1          # bar holding our entry (2 min after the trader)
        i1 = bisect.bisect_right(T, x["close_t"] + 2 * 60_000) - 1         # bar holding our exit
        if i0 < 0 or i1 >= len(B) - 1 or i1 < i0 or B[i0][0] < x["open_t"] - M15 or B[i1][0] < x["close_t"] - M15:
            continue
        # consecutive bars only (no gaps) and the same instrument as the trader (entry within 3%)
        if T[i1] - T[i0] != (i1 - i0) * M15 or abs(B[i0][4] / x["entry"] - 1) > 0.03:
            continue
        out.append((x, B[i0][4], B[i0 + 1:i1], B[i1][4]))
    return out


def run_exit(p, rule):
    x, e, mid, ex = p
    sg = 1 if x["side"] == "LONG" else -1
    stop = -STOP; peak = 0.0; part = 0.0; left = 1.0
    kind, X, Y = rule
    for (_, o, h, l_, cl) in mid:
        fav = sg * ((h if sg > 0 else l_) / e - 1)
        adv = sg * ((l_ if sg > 0 else h) / e - 1)
        if adv <= stop:                                                           # stop first (conservative order inside a bar)
            return part + left * stop
        if kind == "tp3" and left == 1.0 and fav >= X:
            part, left = X / 3, 2 / 3
        peak = max(peak, fav)                                                     # the new stop acts from the next bar
        if kind == "be" and peak >= X:
            stop = max(stop, 0.0)
        if kind == "trail" and peak >= X:
            stop = max(stop, peak - Y)
    return part + left * max(sg * (ex / e - 1), -STOP)


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else None
    db = "data/agents.sqlite"
    dec, names, src = load()
    ts, ps = btc_series(dec, db)
    tag(dec, ts, ps)
    top = json.load(open("web/whales_top.json"))
    A = [x["addr"] for x in top["rotation"]["a"]]
    B = [x["addr"] for x in top["rotation"]["b"]]
    cnt = collections.Counter(x["trader"] for x in dec)
    prof_set = list(dict.fromkeys(A + B + [tr for tr, n in cnt.most_common() if n >= 30]))
    split = sorted(x["open_t"] for x in dec)[len(dec) // 2]
    res = {"decisions": len(dec), "traders": len(cnt), "from": utc(dec[0]["open_t"]).isoformat()[:10], "to": utc(dec[-1]["open_t"]).isoformat()[:10],
           "split": utc(split).isoformat()[:16], "btc_points": len(ts), "r7_known": sum(x["r7"] is not None for x in dec),
           "notional": round(NOTIONAL, 1)}
    profs = []
    for tr in prof_set:
        ds = [x for x in dec if x["trader"] == tr]
        if not ds:
            continue
        p = profile(tr, ds, names)
        p["col"] = "A" if tr in A else ("B" if tr in B else "-")
        p["src"] = src.get(tr)
        p["h1"] = summ([x["usd"] for x in ds if x["open_t"] < split]); p["h2"] = summ([x["usd"] for x in ds if x["open_t"] >= split])
        profs.append(p)
    res["profiles"] = profs
    res["styles"] = {s: summ([x["usd"] for p in profs if p["style"] == s for x in dec if x["trader"] == p["trader"]]) for s in
                     ("свинг", "интрадей–дни", "скальпер/перевёртыш")}
    # 2.
    pt = [p["trader"] for p in profs]
    res["persistence"] = persistence(dec, split, pt)
    # 3.
    start = dec[0]["open_t"] + 60 * DAY
    end = dec[-1]["open_t"]
    res["walk_from"] = utc(start).isoformat()[:10]
    wf = {}
    for sch in ("a", "a_regime", "abc", "abc_specialists_only", "abc_b_allcells", "wide_all"):
        tk = walk(dec, start, end, sch)
        v = [x["usd"] for x in tk]
        h1 = [x["usd"] for x in tk if x["open_t"] < split]; h2 = [x["usd"] for x in tk if x["open_t"] >= split]
        days = collections.defaultdict(float)
        for x in tk:
            days[utc(x["close_t"]).date()] += x["usd"]
        wf[sch] = {**summ(v), "dd": maxdd(v), "h1": summ(h1), "h2": summ(h2), "traders": len({x["trader"] for x in tk}),
                   "small_pct": round(sum(abs(u) < 3 for u in v) / max(1, len(v)) * 100), "day_t": tstat(list(days.values())),
                   "usd_per_100_mday": round(sum(v) / max(1e-9, sum(x["mday"] for x in tk)) * 100, 2),
                   "peak_open": peak_open(tk), "days_n": len(days)}
        cp = capped(tk, 15)                                                       # the live book limit: at most 15 copies open at once
        wf[sch]["cap15"] = {**summ([x["usd"] for x in cp]), "dd": maxdd([x["usd"] for x in sorted(cp, key=lambda x: x["close_t"])]),
                            "h1": summ([x["usd"] for x in cp if x["open_t"] < split]), "h2": summ([x["usd"] for x in cp if x["open_t"] >= split])}
        wf[sch]["_days"] = {str(k): round(v_, 2) for k, v_ in days.items()}
    da, dabc = wf["a"].pop("_days"), wf["abc"].pop("_days")
    for k in ("a_regime", "abc_specialists_only", "abc_b_allcells", "wide_all"):
        wf[k].pop("_days")
    alld = sorted(set(da) | set(dabc))
    diff = [dabc.get(d, 0) - da.get(d, 0) for d in alld]
    wf["abc_minus_a_day_t"] = tstat(diff)
    res["walk"] = wf
    # 4. small copies + "minimum value" filter decided on the 1st half
    allv = [x["usd"] for x in dec]
    res["small"] = {"archive_pct": round(sum(abs(v) < 3 for v in allv) / len(allv) * 100)}
    try:
        real = json.load(open("web/public_real.json"))
        rc = [c["pnl_usd"] for c in real.get("closed", []) if c.get("pnl_usd") is not None]
        res["small"]["real_n"] = len(rc); res["small"]["real_pct"] = round(sum(abs(v) < 3 for v in rc) / max(1, len(rc)) * 100)
        res["small"]["real_sum_small"] = round(sum(v for v in rc if abs(v) < 3), 2); res["small"]["real_sum"] = round(sum(rc), 2)
    except Exception:
        pass
    filt = {}
    for thr in (0.5, 1.0, 1.5, 2.0):          # skip a trader whose trailing-60-day median |move| < thr %
        for half, (a0, a1) in (("h1", (start, split)), ("h2", (split, end + 1))):
            base = [x for x in walk(dec, start, end, "a") if a0 <= x["open_t"] < a1]
            kept = []
            for x in base:
                hist = [y["move"] for y in dec if y["trader"] == x["trader"] and x["open_t"] - 60 * DAY <= y["close_t"] < x["open_t"]]
                if hist and st.median([abs(m) for m in hist]) * 100 >= thr:
                    kept.append(x)
            sk = [x["usd"] for x in base if x not in kept]
            md = lambda xs: round(sum(x["usd"] for x in xs) / max(1e-9, sum(x["mday"] for x in xs)) * 100, 2)  # noqa: E731
            filt[f"{thr}_{half}"] = {"base": summ([x["usd"] for x in base]), "kept": summ([x["usd"] for x in kept]), "skipped": summ(sk),
                                     "kept_per_100_mday": md(kept), "skipped_per_100_mday": md([x for x in base if x not in kept])}
    res["small"]["filter"] = filt
    # 5.
    paths = candle_paths(dec, db)
    res["exit_n"] = len(paths)
    if paths:
        res["exit_from"] = utc(min(p[0]["open_t"] for p in paths)).isoformat()[:10]
        res["exit_to"] = utc(max(p[0]["close_t"] for p in paths)).isoformat()[:10]
        sp = sorted(p[0]["open_t"] for p in paths)[len(paths) // 2]
        res["exit_split"] = utc(sp).isoformat()[:10]
        rules = [("base", 0, 0)] + [("be", X, 0) for X in (0.03, 0.05, 0.08)] + [("trail", X, Y) for X in (0.03, 0.05, 0.08) for Y in (0.03, 0.05, 0.08)] \
            + [("tp3", X, 0) for X in (0.03, 0.05, 0.08)]
        base = [NOTIONAL * (run_exit(p, rules[0]) - COST) for p in paths]
        ex = {}
        for r in rules:
            v = [NOTIONAL * (run_exit(p, r) - COST) for p in paths]
            d = [a - b for a, b in zip(v, base)]
            h1 = [i for i, p in enumerate(paths) if p[0]["open_t"] < sp]; h2 = [i for i, p in enumerate(paths) if p[0]["open_t"] >= sp]
            name = r[0] if r[0] == "base" else f"{r[0]} X{int(r[1]*100)}" + (f" Y{int(r[2]*100)}" if r[0] == "trail" else "")
            wins = [u for u in v if u > 0]
            ex[name] = {**summ(v), "avg_win": round(sum(wins) / max(1, len(wins)), 2), "dd": maxdd([v[i] for i in sorted(range(len(v)), key=lambda i: paths[i][0]["close_t"])]),
                        "t": tstat(d), "h1_sum": round(sum(v[i] for i in h1), 1), "h2_sum": round(sum(v[i] for i in h2), 1),
                        "h1_t": tstat([d[i] for i in h1]), "h2_t": tstat([d[i] for i in h2]),
                        "long_hold_sum": round(sum(v[i] for i, p in enumerate(paths) if p[0]["hold_h"] >= 48), 1)}
        res["exits"] = ex
        res["exit_long_hold_n"] = sum(p[0]["hold_h"] >= 48 for p in paths)
        res["exit_hold_med_h"] = round(st.median([p[0]["hold_h"] for p in paths]), 1)
    if out_path:
        json.dump(res, open(out_path, "w"), ensure_ascii=False, indent=1)
    return res


if __name__ == "__main__":
    r = main()
    print(json.dumps({k: v for k, v in r.items() if k != "profiles"}, ensure_ascii=False, indent=1)[:12000])
