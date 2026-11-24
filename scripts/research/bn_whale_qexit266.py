"""Backlog 266 (24.11.2026): the rotation «fast exit from column A» (and, before the 3-day lock, «trader removed
from the list») closes our open copies at market. Would it be better to let them live until the trader's own exit
or our stop (as after an ordinary lock exit), or to hold at most 24 h?  Research only; real rules are not touched.

Why: 22.11 SOL short of 小富婆6688 and 23.11 four shorts of 0xRubi were closed by the rule against us at a local
extreme; the diary of 24.11 estimated the 4 shorts 16 h later at ≈ +$1.8 instead of −$19.27 (real account).
Item 165 (14.11) tested the same question on a history book (hold better 1–2 p.p. a copy, t 1.4/1.8, not passed);
this is the check on the desk's OWN journal (web/whales_live.json desk.closed), as asked by the item.

Data: desk.closed rows whose «why» starts with «быстрый выход» (fast exit, from 14.11) or «кит убран из списка»
(old removal rule, 06–08.11 — reference group). Trader exit = the close of the trader's chain (same trader, coin,
side; add-ons merged, re-open ≤ 60 min merged) from data/whale_trades/*.jsonl + ws_trips that was open at our close.
Prices: Binance USD-M 5m (archive) + OKX 5m REST for the last days.

Pre-registered (written before any result was seen):
- a = what the desk did: our entry → our recorded exit (desk prices), costs as recorded (−0.10 % round trip).
- b = same entry, exit at the first 5m bar ≥ trader exit + 2 min, or at our recorded stop (desk «stop») if touched
  after our close; if the trader still holds at the data end → marked to the last price (censored, shown).
- c = as b but exit no later than our close + 24 h.
- result per copy in % of notional and in $ (desk notional); costs 0.10 % a round trip in all three (no funding).
- pass (only as a candidate for the owner): b or c beats a in the sum of BOTH halves (split by close time) AND the
  worst single copy of b/c is not worse than the worst of a. 2 variants → noted; n is small, t shown for reference.
Usage: bn_whale_qexit266.py DB OUT.json"""
import json, math, os, statistics as st, sys, time, urllib.request
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bn_whale_stops as B
import bn_whale_flip245 as F

COST = 0.10
H = 3_600_000
MIN = 60_000
DELAY = 2 * MIN


def okx_tail(coin, t0):
    """5m OKX swap candles from t0 to now -> {ts: (o,h,l,c)}"""
    inst = f"{coin}-USDT-SWAP"
    out, after = {}, None
    for _ in range(60):
        url = f"https://www.okx.com/api/v5/market/history-candles?instId={inst}&bar=5m&limit=100"
        if after:
            url += f"&after={after}"
        try:
            d = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "cnq"}), timeout=20).read())
        except Exception as e:  # noqa: BLE001
            print(f"::warning::okx {inst}: {e}")
            break
        rows = d.get("data") or []
        if not rows:
            break
        for r in rows:
            out[int(r[0])] = (float(r[1]), float(r[2]), float(r[3]), float(r[4]))
        last = int(rows[-1][0])
        if last <= t0:
            break
        after = last
        time.sleep(0.12)
    return out


def series(coin, t0, t1):
    sym = F.ALIAS.get(coin, coin) + "USDT"
    S = B.load_sym(sym, t0, t1) or {"t": [], "k": {}, "fund": []}
    last = S["t"][-1] if S["t"] else t0
    if last < t1 - 10 * MIN:
        tail = okx_tail(coin, last)
        mult = 1000 if F.ALIAS.get(coin, coin).startswith("1000") else 1
        for ts, v in tail.items():
            if ts > last:
                S["k"][ts] = tuple(x * mult for x in v)
        S["t"] = sorted(S["k"])
    return S if S["t"] else None


def walk(S, side, entry, t_from, t_to, stop):
    """from our close t_from to t_to: exit price, stopped?, exit time"""
    s = 1 if side == "LONG" else -1
    i, j = B.bar_at(S, t_from), B.bar_at(S, t_to)
    if i is None:
        return None
    if j is None:
        j = len(S["t"]) - 1
    for q in range(i, j):
        o, h, l, c = S["k"][S["t"][q]]
        if stop and ((s > 0 and l <= stop) or (s < 0 and h >= stop)):
            px = o if ((s > 0 and o < stop) or (s < 0 and o > stop)) else stop
            return px, True, S["t"][q]
    return S["k"][S["t"][j]][0], False, S["t"][j]


def ret(side, a, b):
    s = 1 if side == "LONG" else -1
    return s * (b / a - 1) * 100 - COST


def main():
    db, out = sys.argv[1], sys.argv[2]
    desk = json.load(open("web/whales_live.json"))["desk"]
    ev = [c for c in desk["closed"] if (c.get("why") or "").startswith(("быстрый выход", "кит убран из списка"))]
    rows = F.trips(db)
    by = defaultdict(list)
    for r in rows:
        tr = r[1] if ":" in str(r[1]) or str(r[1]).startswith("0x") else f"{r[0]}:{r[1]}"
        by[(tr, r[3], r[4])].append(r)
        by[(r[1], r[3], r[4])].append(r)
    now = int(time.time() * 1000)
    res, miss = [], []
    for c in ev:
        k = (c["whale"], c["coin"], c["side"])
        lst = sorted({(r[5], r[6]) for r in by.get(k, [])})
        # chain open at our close: trips overlapping, re-open ≤ 60 min merged
        w_exit, censored = None, False
        cur = None
        for o, cl in lst:
            if cl < c["opened_t"] - 6 * H:
                continue
            if cur is None:
                if o <= c["closed_t"] + 5 * MIN and cl >= c["closed_t"] - 5 * MIN:
                    cur = cl
            elif o <= cur + 60 * MIN:
                cur = max(cur, cl)
        if cur is None:
            live = [p for p in (desk.get("live") or {}).get(c["whale"], []) if isinstance(p, dict) and p.get("coin") == c["coin"]] \
                if isinstance(desk.get("live"), dict) else []
            if live:
                cur, censored = now, True
        if cur is None:
            miss.append(f"{c['whale'][-6:]} {c['coin']} {c['side']}")
            continue
        w_exit = cur
        S = series(c["coin"], c["opened_t"] - H, max(w_exit, c["closed_t"] + 24 * H) + H)
        if not S:
            miss.append(f"{c['coin']} no candles")
            continue
        stop = c.get("stop")
        bb = walk(S, c["side"], c["entry"], c["closed_t"], w_exit + DELAY, stop)
        cc = walk(S, c["side"], c["entry"], c["closed_t"], min(w_exit + DELAY, c["closed_t"] + 24 * H), stop)
        if not bb or not cc:
            miss.append(f"{c['coin']} no bars")
            continue
        n = c["notional"]
        ra, rb, rc = ret(c["side"], c["entry"], c["exit"]), ret(c["side"], c["entry"], bb[0]), ret(c["side"], c["entry"], cc[0])
        res.append({"t": c["closed_t"], "who": c["whale"][-6:], "coin": c["coin"], "side": c["side"],
                    "rule": "fast" if c["why"].startswith("быстрый") else "removed",
                    "a": round(ra, 2), "b": round(rb, 2), "c": round(rc, 2),
                    "a_usd": round(ra * n / 100, 2), "b_usd": round(rb * n / 100, 2), "c_usd": round(rc * n / 100, 2),
                    "desk_pnl": c.get("pnl"), "hold_h_b": round((bb[2] - c["closed_t"]) / H, 1), "stop_b": bb[1], "stop_c": cc[1],
                    "censored": censored and bb[2] >= S["t"][-1] - 10 * MIN})
    O = {"rows": res, "missing": miss}
    for name, f in (("fast", lambda r: r["rule"] == "fast"), ("removed", lambda r: r["rule"] == "removed"), ("all", lambda r: True)):
        rs = sorted([r for r in res if f(r)], key=lambda r: r["t"])
        if not rs:
            continue
        mid = rs[len(rs) // 2]["t"]
        q = {"n": len(rs)}
        for v in "abc":
            q[v] = {"usd": round(sum(r[f"{v}_usd"] for r in rs), 2), "pct_mean": round(st.mean(r[v] for r in rs), 2),
                    "h1": round(sum(r[f"{v}_usd"] for r in rs if r["t"] < mid), 2),
                    "h2": round(sum(r[f"{v}_usd"] for r in rs if r["t"] >= mid), 2),
                    "worst": round(min(r[v] for r in rs), 2), "stops": sum(r.get(f"stop_{v}", False) for r in rs)}
        for v in "bc":
            d = [r[v] - r["a"] for r in rs]
            t = round(st.mean(d) / (st.pstdev(d) / math.sqrt(len(d))), 2) if len(d) > 1 and st.pstdev(d) else None
            q[v]["t_vs_a"] = t
            q[v]["better_share"] = round(sum(x > 0 for x in d) / len(d), 2)
            q[v]["pass"] = q[v]["h1"] > q["a"]["h1"] and q[v]["h2"] > q["a"]["h2"] and q[v]["worst"] >= q["a"]["worst"]
        O[name] = q
        print(f"::notice title=qexit266 {name}::n {q['n']} | a ${q['a']['usd']} ({q['a']['h1']}/{q['a']['h2']}) worst {q['a']['worst']}% | "
              f"b ${q['b']['usd']} ({q['b']['h1']}/{q['b']['h2']}) worst {q['b']['worst']}% stops {q['b']['stops']} t {q['b']['t_vs_a']} better {q['b']['better_share']} pass {q['b']['pass']} | "
              f"c ${q['c']['usd']} ({q['c']['h1']}/{q['c']['h2']}) worst {q['c']['worst']}% stops {q['c']['stops']} t {q['c']['t_vs_a']} better {q['c']['better_share']} pass {q['c']['pass']}")
    for r in res:
        print(f"::notice title=qexit266 row::{time.strftime('%m-%d %H:%M', time.gmtime(r['t'] / 1000))} {r['rule']} {r['who']} {r['coin']} {r['side']} "
              f"a {r['a']}% ${r['a_usd']} | b {r['b']}% ${r['b_usd']} ({r['hold_h_b']} h{' stop' if r['stop_b'] else ''}{' cens' if r['censored'] else ''}) | c {r['c']}% ${r['c_usd']}")
    print(f"::notice title=qexit266 meta::events {len(ev)}, used {len(res)}, missing {len(miss)}: {'; '.join(miss)[:400]}")
    json.dump(O, open(out, "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
