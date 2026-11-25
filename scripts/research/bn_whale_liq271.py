"""Backlog 271 (25.11.2026): a copy whose trader has «passed» his own liquidation.

If the price moved against the trader's entry by more than 1/lev (Ivan 25x -> 4 %), an isolated position would be
liquidated; he keeps it on cross margin (account balance), while our copy is protected only by the -16 % stop.
Question: are such copies worse than the rest, and is «close the copy at that moment» better?
Research only; real rules are not touched.

Two books, both pre-registered before any result was seen:
 D = the desk's own journal (web/whales_live.json desk.closed, from 15.10): our entry/exit/stop as recorded,
     whale_entry and whale_lev of each copy.
 T = the trip book (data/whale_trades/*.jsonl + ws_trips, lever known): every closed trader decision of traders
     with >= 20 decisions; copy = in at the first 5m bar >= open + 2 min, out at the first bar >= close + 2 min,
     stop -16 % from our entry; sub-group «bar» = traders that pass win >= 70 % and PF >= 2 on their own book.
Flag: some 5m bar between our entry and our exit has low (long) / high (short) beyond trader_entry * (1 -/+ 1/lev)
      (thr = 100/lev %; lev <= 1 or thr >= 16 -> never flagged; the stop comes first).
Variants (costs 0.10 % a side = 0.20 % round trip in all; funding ignored):
 a = as is (D: our recorded entry -> exit; T: copy as above)
 b = close at the close of the first flagged bar; if the price is already beyond thr at our entry -> no copy (0)
 c = close half at that moment, the other half as a
Pass (candidate for the owner only): b (or c) beats a in BOTH halves (split by entry time) in D and in T-bar,
 the worst copy of b/c is not worse than a's, and b cuts <= 20 % of a's winning copies. 2 variants x 2 books noted.
Usage: bn_whale_liq271.py DB OUT.json"""
import glob, json, math, os, sqlite3, statistics as st, sys, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B
import bn_whale_flip245 as F
import bn_whale_qexit266 as Q

RT = 0.20
H = 3_600_000
MIN = 60_000
DELAY = 2 * MIN
STOP = 16.0


def lev_trips(db):
    rows = {}
    try:
        c = sqlite3.connect(db)
        for r in c.execute("SELECT src, trader, id, coin, side, open_t, close_t, entry, exit, notional, lever FROM ws_trips WHERE close_t > 0"):
            rows[(r[0], r[1], str(r[2]))] = r
    except Exception as e:  # noqa: BLE001
        print(f"::warning::ws_trips not read: {e}")
    for f in sorted(glob.glob("data/whale_trades/*.jsonl")):
        for ln in open(f):
            try:
                d = json.loads(ln)
            except Exception:  # noqa: BLE001
                continue
            if d.get("kind") != "closed" or not d.get("close_t"):
                continue
            k = (d["src"], d["trader"], str(d.get("id")))
            if k not in rows or not rows[k][10]:
                rows[k] = (d["src"], d["trader"], k[2], d["coin"], d["side"], d["open_t"], d["close_t"], d.get("entry"), d.get("exit"),
                           d.get("notional"), d.get("lev"))
    return [r for r in rows.values() if r[4] in ("LONG", "SHORT") and r[7] and r[10]]


_S = {}
SPAN = {}


def want(coin, t0, t1):
    a, b = SPAN.get(coin, (t0, t1))
    SPAN[coin] = (min(a, t0), max(b, t1))


def ser(coin, t0=None, t1=None):
    if coin not in _S:
        a, b = SPAN.get(coin, (t0, t1))
        try:
            _S[coin] = Q.series(coin, a, b)
        except Exception as e:  # noqa: BLE001
            print(f"::warning::{coin}: {e}")
            _S[coin] = None
    return _S[coin]


def scan(S, side, we, thr, i, j):
    """first bar index in [i, j) beyond trader entry by thr %"""
    s = 1 if side == "LONG" else -1
    lvl = we * (1 - s * thr / 100)
    for q in range(i, j):
        o, h, l, c = S["k"][S["t"][q]]
        if (s > 0 and l <= lvl) or (s < 0 and h >= lvl):
            return q
    return None


def r_(side, a, b):
    return (1 if side == "LONG" else -1) * (b / a - 1) * 100 - RT


def one(S, side, entry, t_in, t_out, we, lev, stop_px=None):
    """-> dict(a, b, c, flag, at_entry) in % of notional"""
    i, j = B.bar_at(S, t_in), B.bar_at(S, t_out)
    if i is None or j is None or j <= i:
        return None
    s = 1 if side == "LONG" else -1
    if entry is None:                                   # T-book: our entry is the bar open
        entry = S["k"][S["t"][i]][0]
    if stop_px is None:
        stop_px = entry * (1 - s * STOP / 100)
    # a: exit at t_out bar open, or the stop
    exit_px, jj = S["k"][S["t"][j]][0], j
    for q in range(i, j):
        o, h, l, c = S["k"][S["t"][q]]
        if (s > 0 and l <= stop_px) or (s < 0 and h >= stop_px):
            exit_px = o if q > i and ((s > 0 and o < stop_px) or (s < 0 and o > stop_px)) else stop_px
            jj = q
            break
    a = r_(side, entry, exit_px)
    thr = 100.0 / lev if lev and lev > 1 else None
    if not thr or thr >= STOP:
        return {"a": a, "b": a, "c": a, "flag": False, "at_entry": False, "thr": thr}
    beyond0 = s * (entry / we - 1) * 100 <= -thr
    if beyond0:
        return {"a": a, "b": 0.0, "c": a / 2, "flag": True, "at_entry": True, "thr": thr}
    q = scan(S, side, we, thr, i, jj + 1 if jj < j else j)
    if q is None:
        return {"a": a, "b": a, "c": a, "flag": False, "at_entry": False, "thr": thr}
    b = r_(side, entry, S["k"][S["t"][q]][3])
    return {"a": a, "b": b, "c": (a + b) / 2, "flag": True, "at_entry": False, "thr": thr}


def summ(rows, key="usd"):
    rows = sorted(rows, key=lambda r: r["t"])
    if not rows:
        return {"n": 0}
    mid = rows[len(rows) // 2]["t"]
    O = {"n": len(rows), "flagged": sum(r["flag"] for r in rows), "at_entry": sum(r["at_entry"] for r in rows)}
    for v in "abc":
        val = lambda r: r[v] * r.get("w", 1) if key == "usd" else r[v]  # noqa: E731
        O[v] = {"sum": round(sum(val(r) for r in rows), 2), "h1": round(sum(val(r) for r in rows if r["t"] < mid), 2),
                "h2": round(sum(val(r) for r in rows if r["t"] >= mid), 2), "worst": round(min(r[v] for r in rows), 2),
                "mean": round(st.mean(r[v] for r in rows), 3)}
    fl = [r for r in rows if r["flag"]]
    un = [r for r in rows if not r["flag"]]
    O["flag_a_mean"] = round(st.mean(r["a"] for r in fl), 3) if fl else None
    O["flag_a_win"] = round(sum(r["a"] > 0 for r in fl) / len(fl), 3) if fl else None
    O["unflag_a_mean"] = round(st.mean(r["a"] for r in un), 3) if un else None
    O["flag_stop_share"] = round(sum(r["a"] <= -STOP + 0.5 for r in fl) / len(fl), 3) if fl else None
    plus = [r for r in rows if r["a"] > 0]
    O["plus_cut"] = round(sum(r["flag"] for r in plus) / len(plus), 3) if plus else None
    d = [r["b"] - r["a"] for r in fl]
    O["t_b"] = round(st.mean(d) / (st.pstdev(d) / math.sqrt(len(d))), 2) if len(d) > 1 and st.pstdev(d) else None
    for v in "bc":
        O[f"pass_{v}"] = (O[v]["h1"] > O["a"]["h1"] and O[v]["h2"] > O["a"]["h2"] and O[v]["worst"] >= O["a"]["worst"]
                          and (O["plus_cut"] or 0) <= 0.20)
    return O


def note(name, O):
    if not O.get("n"):
        print(f"::notice title=liq271 {name}::empty")
        return
    print(f"::notice title=liq271 {name}::n {O['n']} flagged {O['flagged']} (at entry {O['at_entry']}) | flagged a mean {O['flag_a_mean']}% win {O['flag_a_win']} "
          f"stop-share {O['flag_stop_share']} vs unflagged {O['unflag_a_mean']}% | a {O['a']['sum']} ({O['a']['h1']}/{O['a']['h2']}) worst {O['a']['worst']} | "
          f"b {O['b']['sum']} ({O['b']['h1']}/{O['b']['h2']}) worst {O['b']['worst']} t {O['t_b']} | c {O['c']['sum']} ({O['c']['h1']}/{O['c']['h2']}) | "
          f"plus cut {O['plus_cut']} | pass b {O['pass_b']} c {O['pass_c']}")


def main():
    db, out = sys.argv[1], sys.argv[2]
    OUT = {}
    # ---- D: the desk's own journal
    desk = json.load(open("web/whales_live.json"))["desk"]
    D, miss = [], []
    rows = lev_trips(db)
    now = time.time() * 1000
    for c in desk["closed"]:
        if c.get("opened_t") and c.get("closed_t"):
            want(c["coin"], c["opened_t"] - H, c["closed_t"] + H)
    for r in rows:
        if r[6] <= now - 10 * MIN:
            want(r[3], r[5] - H, r[6] + H)
    print(f"coins to load: {len(SPAN)}", flush=True)
    for c in desk["closed"]:
        if not (c.get("whale_lev") and c.get("whale_entry") and c.get("entry") and c.get("exit")):
            miss.append("nolev")
            continue
        S = ser(c["coin"], c["opened_t"] - H, c["closed_t"] + H)
        if not S:
            miss.append(c["coin"])
            continue
        x = one(S, c["side"], c["entry"], c["opened_t"], c["closed_t"], c["whale_entry"], c["whale_lev"], c.get("stop"))
        if not x:
            miss.append(c["coin"] + " bars")
            continue
        # a from the journal itself (recorded exit), not the bar
        x["a"] = r_(c["side"], c["entry"], c["exit"])
        if not x["flag"]:
            x["b"] = x["c"] = x["a"]
        elif not x["at_entry"]:
            x["c"] = (x["a"] + x["b"]) / 2
        else:
            x["c"] = x["a"] / 2
        x.update(t=c["opened_t"], w=c["notional"] / 100, who=c["whale"], coin=c["coin"], side=c["side"], lev=c["whale_lev"],
                 why=(c.get("why") or "")[:30], desk_pnl=c.get("pnl"))
        D.append(x)
    OUT["D"] = summ(D)
    OUT["D_ivan"] = summ([r for r in D if str(r["who"]).startswith("ivan")])
    OUT["D_rest"] = summ([r for r in D if not str(r["who"]).startswith("ivan")])
    for k in ("D", "D_ivan", "D_rest"):
        note(k + " $", OUT[k])
    for r in D:
        if r["flag"]:
            print(f"::notice title=liq271 D row::{time.strftime('%m-%d %H:%M', time.gmtime(r['t'] / 1000))} {str(r['who'])[-10:]} {r['coin']} {r['side']} "
                  f"lev {r['lev']} thr {round(r['thr'], 2)}% | a {round(r['a'], 2)}% b {round(r['b'], 2)}% {'entry' if r['at_entry'] else ''} {r['why']}")
    print(f"::notice title=liq271 D meta::closed {len(desk['closed'])}, used {len(D)}, missing {len(miss)}: {', '.join(sorted(set(miss)))[:300]}")
    # ---- T: the trip book
    by = defaultdict(list)
    for r in rows:
        by[r[1]].append(r)
    T, Tbar = [], []
    for tr, lst in by.items():
        if len(lst) < 20:
            continue
        xs = [(1 if r[4] == "LONG" else -1) * (r[8] / r[7] - 1) * 100 - RT for r in lst if r[8]]
        pos, neg = sum(x for x in xs if x > 0), -sum(x for x in xs if x < 0)
        bar = bool(xs) and sum(x > 0 for x in xs) / len(xs) >= 0.70 and (neg == 0 or pos / neg >= 2)
        for r in lst:
            if r[6] > now - 10 * MIN:
                continue
            S = ser(r[3])
            if not S:
                continue
            x = one(S, r[4], None, r[5] + DELAY, r[6] + DELAY, r[7], float(r[10]))
            if not x:
                continue
            x.update(t=r[5], w=1, who=tr, coin=r[3], side=r[4], lev=r[10])
            T.append(x)
            if bar:
                Tbar.append(x)
    for name, rs in (("T", T), ("T_bar", Tbar), ("T_bar_lev10+", [r for r in Tbar if float(r["lev"]) >= 10]),
                     ("T_bar_lev<10", [r for r in Tbar if float(r["lev"]) < 10])):
        OUT[name] = summ(rs, key="pct")
        note(name + " %", OUT[name])
    print(f"::notice title=liq271 T meta::trips with lever {len(rows)}, traders {len(by)}, copies {len(T)}, bar copies {len(Tbar)}, coins {len(_S)} "
          f"(no candles {sum(1 for v in _S.values() if not v)})")
    passed = OUT["D"].get("pass_b") and OUT["T_bar"].get("pass_b")
    passed_c = OUT["D"].get("pass_c") and OUT["T_bar"].get("pass_c")
    print(f"::notice title=liq271 verdict::b passes both books: {bool(passed)}; c passes both books: {bool(passed_c)}")
    OUT["rows_D"] = D
    json.dump(OUT, open(out, "w"), ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
