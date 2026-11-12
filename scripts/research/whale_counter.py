"""Backlog 143, OKX/HL half: never copy a whale's counter leg (12.11.2026, research only).

Pre-registered (written in backlog 143 on 11.11, before this data was looked at; 1 variant):
 A «counter leg» = a decision (episode: same coin + side, entries within 4 h) that opens while the SAME whale still has the
 OPPOSITE side of the SAME coin open (any of his sub-positions with open_t < t0 < close_t, or still open). Known at our entry.
 Rule: skip every counter leg (even if we did not copy the first leg). Base = the live book (book_sim.simulate: risk 3 % at the
 16 % stop, daily stop 3 %, 15 slots, one copy per coin), copy +2 min, exit +2 min after the whale, stop −16 %, costs 0.11 %
 + funding. Pass (per backlog): in each source with data the book total with the rule is not worse than base AND the mean
 copy result of the skipped decisions is < 0. Reported also: paired daily t per half (pass_strict = t >= 2.5 in both halves).
 Pools: top-10 by 60-day P&L of whales passing the live bar (main), all passing the bar (info).
 HL accounts are one-way (net position) — a counter leg cannot exist there; their count is reported to make that explicit.
 Binance is out of sample only from 11.11 → forward record to 10.12 (backlog 143), not here.
Data: lab-data-whales (whale_rules_data.json.gz, 04.11.2026) → whale_rules_convert.py; whale_rules_study.py (branch
research/whale-rules-0411) next to this file as study.py. Usage: whale_counter.py <conv>/whales.json.gz OUT.json
"""
import json, math, os, sys, statistics as st
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import study as S0
import daystop_sim as D
import book_sim as K

MIN, DAY = 60_000, 86_400_000
DD = S0.load_data(sys.argv[1]); now = int(DD["made"])
W = S0.WhaleSeries(os.path.join(os.path.dirname(sys.argv[1]), "mkt"), None)


def fwd2(S, t0, t1, side, stop=16.0):
    a, b = S.price(t0), S.price(t1)
    if not a or not b: return None
    s = 1 if side == "LONG" else -1
    i0, i1 = S.idx(t0 + S0.H1), S.idx(t1 + S0.H1)
    lvl = a * (1 - s * stop / 100)
    for k in range(i0 + 1, i1):
        if (s > 0 and S.l[k] <= lvl) or (s < 0 and S.h[k] >= lvl):
            b = S.o[k] if ((s > 0 and S.o[k] < lvl) or (s < 0 and S.o[k] > lvl)) else lvl
            t1 = int(S.t[k]); break
    return a, t1, s * (b / a - 1) * 100 - S0.COST - S.funding_paid(side, t0, t1)


whales = []
for w in DD.get("hl") or []:
    if len(w["fills"]) < 8: continue
    whales.append(("hl", w.get("name") or w["addr"][:10], S0.hl_trips(w, now)))
for t in DD.get("okx") or []:
    tr = S0.okx_trips(t, now)
    if len(tr) < 8: continue
    whales.append(("okx", t.get("name") or t["code"], tr))

trades, status, cnt = [], {}, {}
for src, name, trips in whales:
    trips = sorted(trips, key=lambda x: x["open_t"])
    eps = S0.episodes(trips)
    done = []
    for e in eps:
        t0 = e["open_t"]
        opp = "SHORT" if e["side"] == "LONG" else "LONG"
        counter = any(x["coin"] == e["coin"] and x["side"] == opp and x["open_t"] < t0 and (x.get("open") or x["close_t"] > t0)
                      for x in trips)
        c = cnt.setdefault(src, {"decisions": 0, "counter": 0}); c["decisions"] += 1; c["counter"] += int(counter)
        pri = [d for d in done if d[1] < t0 and d[0] >= t0 - 60 * DAY]
        bar = False
        if len(pri) >= 20:
            wins = sum(1 for d in pri if d[2] > 0); gp = sum(d[2] for d in pri if d[2] > 0); gl = -sum(d[2] for d in pri if d[2] < 0)
            bar = wins / len(pri) >= 0.70 and (gp / gl if gl > 0 else 99) >= 2 and gp - gl > 0
        done.append((t0, e["close_t"], e["pnl"]))
        status.setdefault(name, []).append((t0, bar, sum(d[2] for d in pri)))
        if not bar or e.get("open") or e["close_t"] - t0 <= 2 * MIN: continue
        Sx = W.get(e["coin"])
        if Sx is None: continue
        v = fwd2(Sx, t0 + 2 * MIN, e["close_t"] + 2 * MIN, e["side"])
        if v is None or not math.isfinite(v[2]): continue
        trades.append({"src": src, "w": name, "coin": e["coin"], "side": e["side"], "t_in": t0 + 2 * MIN, "t_out": v[1], "a": v[0],
                       "ret": float(v[2]), "counter": counter})


def mark(coin, t):
    S = W.get(coin)
    return S.price(t) if S is not None else None


def tstat(xs):
    if len(xs) < 2: return None
    sd = st.pstdev(xs)
    return round(st.mean(xs) / (sd / math.sqrt(len(xs))), 2) if sd > 0 else None


def look(tr, tag):
    d0 = min(t["t_in"] for t in tr) // DAY; d1 = max(t["t_out"] for t in tr) // DAY; mid = (d0 + d1) // 2
    parts = (("all", (d0, d1)), ("h1", (d0, mid)), ("h2", (mid + 1, d1)))
    base = K.simulate(tr, mark, S0.COST)
    rule = K.simulate([t for t in tr if not t["counter"]], mark, S0.COST)
    sk = [t["ret"] for t in tr if t["counter"]]; rest = [t["ret"] for t in tr if not t["counter"]]
    R = {"n": len(tr), "counter_n": len(sk), "counter_share": round(len(sk) / len(tr), 3),
         "counter_mean": round(st.mean(sk), 3) if sk else None, "counter_t": tstat(sk),
         "counter_mean_h": [round(st.mean(x), 3) if x else None for x in
                            ([t["ret"] for t in tr if t["counter"] and t["t_in"] // DAY <= mid],
                             [t["ret"] for t in tr if t["counter"] and t["t_in"] // DAY > mid])],
         "rest_mean": round(st.mean(rest), 3) if rest else None}
    for k, S in (("base", base), ("skip_counter", rule)):
        R[k] = {p: D.curve_stats(S["days"], a, b) for p, (a, b) in parts}
        R[k]["taken"] = S["taken"]; R[k]["taken_mean"] = S["taken_mean"]
    R["diff"] = {p: D.paired(rule["days"], base["days"], a, b) for p, (a, b) in parts}
    R["pass"] = bool(sk and R["skip_counter"]["all"]["total"] >= R["base"]["all"]["total"] and st.mean(sk) < 0)
    R["pass_strict"] = bool(R["pass"] and all((R["diff"][h]["t"] or 0) >= 2.5 for h in ("h1", "h2")))
    print(f"::notice title=counter {tag}::n {R['n']} counter {R['counter_n']} ({R['counter_share']}) mean {R['counter_mean']}% "
          f"t {R['counter_t']} halves {R['counter_mean_h']} rest {R['rest_mean']}% | base {R['base']['all']} | rule {R['skip_counter']['all']} "
          f"| diff {R['diff']} | pass {R['pass']} strict {R['pass_strict']}")
    return R


out = {"made": now, "decisions": cnt, "whales": len({t["w"] for t in trades})}
print("decisions", cnt)
top = D.top_n(trades, status, 10)
for src in ("okx", "hl"):
    for pool, tr in (("top10", top), ("all_bar", trades)):
        x = [t for t in tr if t["src"] == src]
        if x: out[f"{src}_{pool}"] = look(x, f"{src} {pool}")
out["both_top10"] = look(top, "okx+hl top10")
json.dump(out, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
